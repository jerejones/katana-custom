//go:build linux && (amd64 || arm64)

package katana

import (
	"bytes"
	"errors"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

type timeoutTransport struct {
	reads, writes int
	now           *time.Time
}

func (f *timeoutTransport) Read(d time.Duration) ([]byte, error) {
	f.reads++
	*f.now = f.now.Add(d)
	return nil, errTimeout
}
func (f *timeoutTransport) Write([]byte) (int, error) { f.writes++; return 64, nil }
func (*timeoutTransport) Close() error                { return nil }
func TestReplyTimeoutBound(t *testing.T) {
	now := time.Now()
	f := &timeoutTransport{now: &now}
	d := &device{t: f, healthy: true, now: func() time.Time { return now }}
	if _, e := d.exchange(versionQuery()); e == nil || d.healthy {
		t.Fatal("timeout accepted")
	}
	if f.writes != 1 || f.reads > 22 {
		t.Fatal(f.writes, f.reads)
	}
	if _, e := d.exchange(versionQuery()); e == nil || f.writes != 1 {
		t.Fatal("retried poisoned request")
	}
}
func TestConcreteUSBTransportWithProtocol(t *testing.T) {
	var pending []byte
	var urb *usbURB
	cancelled := false
	closed := false
	text := ""
	writes := 0
	u := &usbTransport{serial: serial, fd: 100, claimed: true, closeFD: func(int) error { closed = true; return nil }}
	u.ioctl = func(_ int, op uintptr, p unsafe.Pointer) (int, error) {
		switch op {
		case controlIO:
			c := (*ctrlTransfer)(p)
			if c.RequestType != 0x21 || c.Request != 9 || c.Value != 0x0200 || c.Index != 4 || c.Length != 64 || c.Timeout != 1000 {
				t.Fatal("wrong SET_REPORT")
			}
			packet := unsafe.Slice((*byte)(c.Data), 64)
			writes++
			pending = make([]byte, 64)
			if bytes.Equal(packet, versionQuery()) {
				copy(pending, []byte{0x5a, 7, 15})
				copy(pending[3:], baseVersion)
			} else {
				if packet[3] == 1 {
					text = string(packet[8:16])
				}
				if packet[3] == 2 {
					text = "        "
				}
				copy(pending, replyHeader)
				copy(pending[8:16], text)
			}
			return 64, nil
		case submitIO:
			urb = (*usbURB)(p)
			cancelled = false
			return 0, nil
		case discardIO:
			cancelled = true
			return 0, nil
		case reapIO:
			if !cancelled && pending == nil {
				return 0, syscall.EAGAIN
			}
			*(*uintptr)(p) = uintptr(unsafe.Pointer(urb))
			if cancelled {
				urb.Status = -int32(syscall.ENOENT)
			} else {
				urb.ActualLength = int32(len(pending))
				copy(unsafe.Slice((*byte)(unsafe.Add(unsafe.Pointer(urb), 128)), 64), pending)
				pending = nil
			}
			return 0, nil
		case releaseIO:
			return 0, nil
		}
		return 0, errors.New("unexpected ioctl")
	}
	d, e := newDevice(u, nil)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = d.request(1, "12-34-56"); e != nil {
		t.Fatal(e)
	}
	if _, e = d.request(2, ""); e != nil {
		t.Fatal(e)
	}
	if e = u.Close(); e != nil || !closed || writes != 6 || text != "        " {
		t.Fatal(e, closed, writes, text)
	}
}
