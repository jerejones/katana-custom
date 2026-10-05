//go:build linux && (amd64 || arm64)

package katana

// Native Linux usbfs. ABI from linux/usbdevice_fs.h (64-bit Linux only).
// An interrupt URB is submitted/reaped directly, never via USBDEVFS_BULK.
import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

const (
	controlIO   = 0xc0185500
	getDriverIO = 0x41045508
	submitIO    = 0x8038550a
	discardIO   = 0x550b
	reapIO      = 0x4008550d
	claimIO     = 0x8004550f
	releaseIO   = 0x80045510
)

type ctrlTransfer struct {
	RequestType, Request byte
	Value, Index, Length uint16
	Timeout              uint32
	Data                 unsafe.Pointer
}
type usbURB struct {
	Type, Endpoint                                              byte
	Status                                                      int32
	Flags                                                       uint32
	Buffer                                                      uintptr
	BufferLength, ActualLength, StartFrame, Packets, ErrorCount int32
	Signr                                                       uint32
	UserContext                                                 uintptr
}
type driverInfo struct {
	Interface uint32
	Driver    [256]byte
}
type ioctlFunc func(int, uintptr, unsafe.Pointer) (int, error)

func realIOCTL(fd int, op uintptr, p unsafe.Pointer) (int, error) {
	r, _, e := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), op, uintptr(p))
	if e != 0 {
		return 0, e
	}
	return int(r), nil
}

type usbTransport struct {
	serial  string
	fd      int
	claimed bool
	ioctl   ioctlFunc
	closeFD func(int) error
}

func (u *usbTransport) control(rt, r byte, value, index uint16, b []byte) (int, error) {
	c := ctrlTransfer{RequestType: rt, Request: r, Value: value, Index: index, Length: uint16(len(b)), Timeout: 1000, Data: unsafe.Pointer(&b[0])}
	n, e := u.ioctl(u.fd, controlIO, unsafe.Pointer(&c))
	runtime.KeepAlive(b)
	return n, e
}
func (u *usbTransport) Write(p []byte) (int, error) {
	if u.fd < 0 {
		return 0, errors.New("USB closed")
	}
	if !allowedPacket(p) {
		return 0, errors.New("disallowed USB packet")
	}
	return u.control(0x21, 9, 0x0200, 4, p)
}
func (u *usbTransport) Close() error {
	if u.fd < 0 {
		return nil
	}
	var releaseErr error
	if u.claimed {
		i := uint32(4)
		_, releaseErr = u.ioctl(u.fd, releaseIO, unsafe.Pointer(&i))
		u.claimed = false
	}
	e := u.closeFD(u.fd)
	u.fd = -1
	return errors.Join(releaseErr, e)
}
func (u *usbTransport) Read(timeout time.Duration) ([]byte, error) {
	if u.fd < 0 {
		return nil, errors.New("USB closed")
	}
	// The kernel holds this address asynchronously. mmap is outside the Go heap:
	// it cannot move/be collected and is unmapped only after reap or fd close.
	mem, e := syscall.Mmap(-1, 0, 4096, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_PRIVATE|syscall.MAP_ANON)
	if e != nil {
		return nil, e
	}
	defer syscall.Munmap(mem)
	urb := (*usbURB)(unsafe.Pointer(&mem[0]))
	urb.Type = 1
	urb.Endpoint = 0x84
	urb.Buffer = uintptr(unsafe.Pointer(&mem[128]))
	urb.BufferLength = 64
	if _, e = u.ioctl(u.fd, submitIO, unsafe.Pointer(urb)); e != nil {
		return nil, e
	}
	end := time.Now().Add(timeout)
	cancelled := false
	for {
		var completed uintptr
		_, e = u.ioctl(u.fd, reapIO, unsafe.Pointer(&completed))
		if e == nil {
			if completed != uintptr(unsafe.Pointer(urb)) {
				ce := u.Close()
				return nil, errors.Join(errors.New("unexpected URB completion; USB closed"), ce)
			}
			if cancelled && (urb.Status == -int32(syscall.ENOENT) || urb.Status == -int32(syscall.ECONNRESET)) {
				return nil, errTimeout
			}
			if urb.Status != 0 {
				return nil, syscall.Errno(-urb.Status)
			}
			if urb.ActualLength < 0 || urb.ActualLength > 64 {
				return nil, errors.New("invalid URB length")
			}
			return bytes.Clone(mem[128 : 128+int(urb.ActualLength)]), nil
		}
		if e != syscall.EAGAIN && e != syscall.EINTR {
			ce := u.Close()
			return nil, errors.Join(e, ce)
		}
		if !time.Now().Before(end) {
			if cancelled {
				ce := u.Close()
				return nil, errors.Join(errors.New("cancelled URB could not be reaped; USB closed"), ce)
			}
			_, e = u.ioctl(u.fd, discardIO, unsafe.Pointer(urb))
			if e != nil && e != syscall.EINVAL {
				ce := u.Close()
				return nil, errors.Join(e, ce)
			}
			cancelled = true
			end = time.Now().Add(time.Second)
		}
		time.Sleep(time.Millisecond)
	}
}
func (u *usbTransport) exactControl(rt, r byte, value, index uint16, length int) ([]byte, error) {
	b := make([]byte, length)
	n, e := u.control(rt, r, value, index, b)
	if e != nil {
		return nil, e
	}
	if n != length {
		return nil, fmt.Errorf("short descriptor/control read: %d, want %d", n, length)
	}
	return b, nil
}
func (u *usbTransport) stringDescriptor(index, lang uint16) ([]byte, error) {
	b := make([]byte, 255)
	n, e := u.control(0x80, 6, 0x0300|index, lang, b)
	if e != nil {
		return nil, e
	}
	if n < 2 || int(b[0]) != n || b[1] != 3 || n%2 != 0 {
		return nil, errors.New("malformed USB string descriptor")
	}
	return b[:n], nil
}
func validateConfig(b []byte) error {
	if len(b) < 9 || b[0] != 9 || b[1] != 2 || int(binary.LittleEndian.Uint16(b[2:4])) != len(b) || b[5] != 1 {
		return errors.New("unexpected configuration")
	}
	found, active, eps := false, false, 0
	for off := 0; off < len(b); {
		if off+2 > len(b) || b[off] < 2 || off+int(b[off]) > len(b) {
			return errors.New("malformed configuration descriptors")
		}
		d := b[off : off+int(b[off])]
		if d[1] == 4 {
			if active && eps != 1 {
				return errors.New("unexpected endpoint count")
			}
			active = false
			if len(d) < 9 {
				return errors.New("short interface")
			}
			if d[2] == 4 && d[3] == 0 {
				if found || d[4] != 1 || d[5] != 3 {
					return errors.New("unexpected interface 4")
				}
				found = true
				active = true
				eps = 0
			}
		} else if d[1] == 5 && active {
			eps++
			if len(d) < 7 || d[2] != 0x84 || d[3] != 3 || binary.LittleEndian.Uint16(d[4:6]) != 64 {
				return errors.New("unexpected interrupt endpoint")
			}
		}
		off += len(d)
	}
	if !found || (active && eps != 1) {
		return errors.New("missing interface/endpoint")
	}
	return nil
}
func (u *usbTransport) preflight() error {
	serial := u.serial
	if serial == "" {
		return errors.New("empty selected serial")
	}
	b, e := u.exactControl(0x80, 6, 0x0100, 0, 18)
	if e != nil {
		return e
	}
	if b[0] != 18 || b[1] != 1 || binary.LittleEndian.Uint16(b[8:10]) != 0x041e || binary.LittleEndian.Uint16(b[10:12]) != 0x3247 || binary.LittleEndian.Uint16(b[12:14]) != 0x0200 || b[16] == 0 {
		return errors.New("USB identity/revision mismatch")
	}
	langs, e := u.stringDescriptor(0, 0)
	if e != nil {
		return e
	}
	if len(langs) < 4 {
		return errors.New("no USB language")
	}
	s, e := u.stringDescriptor(uint16(b[16]), binary.LittleEndian.Uint16(langs[2:4]))
	if e != nil {
		return e
	}
	units := make([]uint16, (len(s)-2)/2)
	for i := range units {
		units[i] = binary.LittleEndian.Uint16(s[2+2*i:])
	}
	if string(utf16.Decode(units)) != serial {
		return errors.New("USB serial mismatch")
	}
	cfg, e := u.exactControl(0x80, 8, 0, 0, 1)
	if e != nil {
		return e
	}
	if cfg[0] != 1 {
		return errors.New("active configuration is not 1")
	}
	var desc []byte
	for i := 0; i < int(b[17]); i++ {
		h, e := u.exactControl(0x80, 6, 0x0200|uint16(i), 0, 9)
		if e != nil {
			return e
		}
		size := int(binary.LittleEndian.Uint16(h[2:4]))
		if size < 9 || size > 4096 {
			return errors.New("invalid configuration size")
		}
		if h[5] == 1 {
			desc, e = u.exactControl(0x80, 6, 0x0200|uint16(i), 0, size)
			if e != nil {
				return e
			}
			break
		}
	}
	if e = validateConfig(desc); e != nil {
		return e
	}
	info := driverInfo{Interface: 4}
	_, e = u.ioctl(u.fd, getDriverIO, unsafe.Pointer(&info))
	if e == nil {
		return errors.New("interface 4 has a kernel driver; refusing detach")
	}
	if e != syscall.ENODATA {
		return fmt.Errorf("kernel driver query: %w", e)
	}
	i := uint32(4)
	if _, e = u.ioctl(u.fd, claimIO, unsafe.Pointer(&i)); e != nil {
		return e
	}
	u.claimed = true
	alt, e := u.exactControl(0x81, 10, 0, 4, 1)
	if e != nil {
		return e
	}
	if alt[0] != 0 {
		return errors.New("active interface alternate is not 0")
	}
	hid, e := u.exactControl(0x81, 6, 0x2200, 4, 29)
	if e != nil {
		return e
	}
	if !bytes.Equal(hid, hidDescriptor) {
		return errors.New("HID report descriptor mismatch")
	}
	return nil
}

type selection struct{ path, serial string }

func selectUSB(sysRoot, devRoot, selector string) (selection, error) {
	entries, e := os.ReadDir(sysRoot)
	if e != nil {
		return selection{}, e
	}
	var matches []selection
	read := func(p, n string) string {
		b, _ := os.ReadFile(filepath.Join(p, n))
		return strings.TrimSpace(string(b))
	}
	for _, entry := range entries {
		p := filepath.Join(sysRoot, entry.Name())
		if read(p, "idVendor") != "041e" || read(p, "idProduct") != "3247" {
			continue
		}
		serial := read(p, "serial")
		if selector != "" && serial != selector {
			continue
		}
		if serial == "" {
			return selection{}, errors.New("matching device has no readable stable serial")
		}
		bus, e1 := strconv.Atoi(read(p, "busnum"))
		num, e2 := strconv.Atoi(read(p, "devnum"))
		if e1 != nil || e2 != nil || bus < 1 || bus > 999 || num < 1 || num > 127 {
			return selection{}, errors.New("invalid dynamic USB address")
		}
		matches = append(matches, selection{path: filepath.Join(devRoot, fmt.Sprintf("%03d/%03d", bus, num)), serial: serial})
	}
	if len(matches) != 1 {
		return selection{}, fmt.Errorf("expected exactly one USB 041e:3247 matching serial selector %q; found %d", selector, len(matches))
	}
	return matches[0], nil
}
func openUSB(serial string) (transport, error) {
	selected, e := selectUSB("/sys/bus/usb/devices", "/dev/bus/usb", serial)
	if e != nil {
		return nil, e
	}
	path := selected.path
	fd, e := syscall.Open(path, syscall.O_RDWR|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if e != nil {
		return nil, fmt.Errorf("open %s (requires existing read/write permission; no permission changes): %w", path, e)
	}
	u := &usbTransport{serial: selected.serial, fd: fd, ioctl: realIOCTL, closeFD: syscall.Close}
	if e = u.preflight(); e != nil {
		return nil, errors.Join(e, u.Close())
	}
	return u, nil
}
