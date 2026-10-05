//go:build linux && (amd64 || arm64)

package katana

import (
	"bytes"
	"encoding/binary"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
	"unicode/utf16"
	"unsafe"
)

const serial = "TEST-SERIAL"

func configFixture() []byte {
	return []byte{9, 2, 25, 0, 1, 1, 0, 0x80, 50, 9, 4, 4, 0, 1, 3, 0, 0, 0, 7, 5, 0x84, 3, 64, 0, 1}
}
func TestUSBConfigValidation(t *testing.T) {
	if e := validateConfig(configFixture()); e != nil {
		t.Fatal(e)
	}
	for _, i := range []int{0, 1, 2, 5, 9, 11, 12, 13, 14, 18, 20, 21, 22} {
		b := configFixture()
		b[i] ^= 255
		if e := validateConfig(b); e == nil {
			t.Fatalf("accepted corrupt byte %d", i)
		}
	}
}
func TestUSBABI(t *testing.T) {
	if unsafe.Sizeof(ctrlTransfer{}) != 24 || unsafe.Offsetof(ctrlTransfer{}.Data) != 16 || unsafe.Sizeof(usbURB{}) != 56 || unsafe.Offsetof(usbURB{}.Buffer) != 16 || unsafe.Offsetof(usbURB{}.ActualLength) != 28 || unsafe.Sizeof(driverInfo{}) != 260 {
		t.Fatal("Linux ABI mismatch")
	}
}
func TestDiscovery(t *testing.T) {
	root := t.TempDir()
	makeDev := func(name string) {
		p := filepath.Join(root, name)
		os.Mkdir(p, 0700)
		for k, v := range map[string]string{"idVendor": "041e", "idProduct": "3247", "serial": serial, "busnum": "7", "devnum": "19"} {
			os.WriteFile(filepath.Join(p, k), []byte(v+"\n"), 0600)
		}
	}
	if _, e := selectUSB(root, "/fakeusb", ""); e == nil {
		t.Fatal("missing accepted")
	}
	makeDev("7-1")
	got, e := selectUSB(root, "/fakeusb", "")
	if e != nil || got.path != "/fakeusb/007/019" || got.serial != serial {
		t.Fatal(got, e)
	}
	makeDev("7-2")
	if _, e = selectUSB(root, "/fakeusb", ""); e == nil {
		t.Fatal("duplicates accepted")
	}
}
func usbString(s string) []byte {
	u := utf16.Encode([]rune(s))
	b := make([]byte, 2+2*len(u))
	b[0] = byte(len(b))
	b[1] = 3
	for i, v := range u {
		binary.LittleEndian.PutUint16(b[2+2*i:], v)
	}
	return b
}
func TestUSBPreflightAndRelease(t *testing.T) {
	for _, kind := range []string{"good", "vid", "revision", "serial", "config", "endpoint", "driver", "driver-query-error", "claim-error", "alt", "hid", "short"} {
		t.Run(kind, func(t *testing.T) {
			claimed, released, closed := 0, 0, 0
			u := &usbTransport{serial: serial, fd: 100, closeFD: func(int) error { closed++; return nil }}
			u.ioctl = func(_ int, op uintptr, p unsafe.Pointer) (int, error) {
				switch op {
				case getDriverIO:
					if kind == "driver" {
						return 0, nil
					}
					if kind == "driver-query-error" {
						return 0, syscall.EIO
					}
					return 0, syscall.ENODATA
				case claimIO:
					if kind == "claim-error" {
						return 0, syscall.EBUSY
					}
					claimed++
					return 0, nil
				case releaseIO:
					released++
					return 0, nil
				case controlIO:
					c := (*ctrlTransfer)(p)
					var b []byte
					switch {
					case c.Request == 6 && c.Value == 0x0100:
						b = make([]byte, 18)
						b[0] = 18
						b[1] = 1
						binary.LittleEndian.PutUint16(b[8:], 0x041e)
						binary.LittleEndian.PutUint16(b[10:], 0x3247)
						binary.LittleEndian.PutUint16(b[12:], 0x0200)
						b[16] = 3
						b[17] = 1
						if kind == "vid" {
							b[8] = 0
						}
						if kind == "revision" {
							b[12] = 1
						}
					case c.Request == 6 && c.Value == 0x0300:
						b = []byte{4, 3, 9, 4}
					case c.Request == 6 && c.Value == 0x0303:
						b = usbString(serial)
						if kind == "serial" {
							b = usbString("wrong")
						}
					case c.Request == 8:
						b = []byte{1}
						if kind == "config" {
							b[0] = 2
						}
					case c.Request == 6 && c.Value == 0x0200:
						b = configFixture()
						if kind == "endpoint" {
							b[20] = 0x85
						}
						if c.Length == 9 {
							b = b[:9]
						}
					case c.Request == 10:
						b = []byte{0}
						if kind == "alt" {
							b[0] = 1
						}
					case c.Request == 6 && c.Value == 0x2200:
						b = bytes.Clone(hidDescriptor)
						if kind == "hid" {
							b[0] = 0
						}
					default:
						t.Fatalf("unexpected control %+v", c)
					}
					if kind == "short" {
						b = b[:len(b)-1]
					}
					copy(unsafe.Slice((*byte)(c.Data), int(c.Length)), b)
					return len(b), nil
				default:
					t.Fatalf("unexpected ioctl %x", op)
				}
				return 0, nil
			}
			e := u.preflight()
			if (e == nil) != (kind == "good") {
				t.Fatal(kind, e)
			}
			u.Close()
			u.Close()
			if closed != 1 || released != claimed {
				t.Fatal(claimed, released, closed)
			}
		})
	}
}
func TestInterruptURBLifecycle(t *testing.T) {
	for _, kind := range []string{"success", "timeout", "disconnect", "submit-error", "reap-error", "short", "bad-completion", "discard-error"} {
		t.Run(kind, func(t *testing.T) {
			closed, release, cancels := 0, 0, 0
			var urb *usbURB
			u := &usbTransport{serial: serial, fd: 100, claimed: true, closeFD: func(int) error { closed++; return nil }}
			u.ioctl = func(_ int, op uintptr, p unsafe.Pointer) (int, error) {
				switch op {
				case submitIO:
					if kind == "submit-error" {
						return 0, syscall.EIO
					}
					urb = (*usbURB)(p)
					if urb.Type != 1 || urb.Endpoint != 0x84 || urb.BufferLength != 64 {
						t.Fatal("not interrupt IN 64")
					}
					return 0, nil
				case reapIO:
					if kind == "reap-error" {
						return 0, syscall.EIO
					}
					if kind == "disconnect" {
						return 0, syscall.ENODEV
					}
					if kind == "timeout" || kind == "discard-error" {
						if cancels == 0 {
							return 0, syscall.EAGAIN
						}
						urb.Status = -int32(syscall.ENOENT)
					}
					if kind == "bad-completion" {
						*(*uintptr)(p) = 1
						return 0, nil
					}
					*(*uintptr)(p) = uintptr(unsafe.Pointer(urb))
					urb.ActualLength = 64
					if kind == "short" {
						urb.ActualLength = 16
					}
					return 0, nil
				case discardIO:
					cancels++
					if kind == "discard-error" {
						return 0, syscall.EIO
					}
					return 0, nil
				case releaseIO:
					release++
					return 0, nil
				}
				t.Fatalf("unexpected ioctl %x", op)
				return 0, nil
			}
			b, e := u.Read(time.Millisecond)
			switch kind {
			case "success":
				if e != nil || len(b) != 64 {
					t.Fatal(len(b), e)
				}
			case "short":
				if e != nil || len(b) != 16 {
					t.Fatal(len(b), e)
				}
			case "timeout":
				if !errors.Is(e, errTimeout) || cancels != 1 {
					t.Fatal(e, cancels)
				}
			default:
				if e == nil {
					t.Fatal("expected failure")
				}
			}
			if kind == "disconnect" || kind == "reap-error" || kind == "bad-completion" || kind == "discard-error" {
				if closed != 1 {
					t.Fatal("pending URB not closed before unmap")
				}
			}
			u.Close()
			if closed != 1 || release != 1 {
				t.Fatal(closed, release)
			}
		})
	}
}
