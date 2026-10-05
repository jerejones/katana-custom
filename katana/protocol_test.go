package katana

import (
	"bytes"
	"errors"
	"testing"
	"time"
)

type fakeTransport struct {
	pending  []byte
	text     string
	writes   [][]byte
	closed   bool
	change   func([]byte) []byte
	writeErr error
	short    bool
	flood    bool
	onWrite  func([]byte)
}

func (f *fakeTransport) Read(time.Duration) ([]byte, error) {
	if f.flood {
		return make([]byte, 64), nil
	}
	if f.pending == nil {
		return nil, errTimeout
	}
	p := f.pending
	f.pending = nil
	if f.change != nil {
		p = f.change(p)
	}
	return p, nil
}
func (f *fakeTransport) Write(p []byte) (int, error) {
	f.writes = append(f.writes, bytes.Clone(p))
	if f.onWrite != nil {
		f.onWrite(p)
	}
	if f.writeErr != nil {
		return 0, f.writeErr
	}
	if f.short {
		return 63, nil
	}
	r := make([]byte, 64)
	if bytes.Equal(p, versionQuery()) {
		copy(r, []byte{0x5a, 7, 15})
		copy(r[3:], baseVersion)
	} else {
		if p[3] == 1 {
			f.text = string(p[8:16])
		}
		if p[3] == 2 {
			f.text = "        "
		}
		copy(r, replyHeader)
		copy(r[8:16], f.text)
	}
	f.pending = r
	return 64, nil
}
func (f *fakeTransport) Close() error { f.closed = true; return nil }
func opened(t *testing.T) (*device, *fakeTransport) {
	t.Helper()
	f := &fakeTransport{}
	d, e := newDevice(f, nil)
	if e != nil {
		t.Fatal(e)
	}
	return d, f
}
func TestCodec(t *testing.T) {
	p, e := encode(1, "12-34-56")
	if e != nil || len(p) != 64 || !bytes.Equal(p[:16], []byte{0x5a, 0x7e, 13, 1, 'R', 'D', 2, 0, '1', '2', '-', '3', '4', '-', '5', '6'}) {
		t.Fatal(p, e)
	}
	for _, x := range []struct {
		op   byte
		text string
	}{{3, ""}, {0, "A"}, {2, "A"}, {1, "12:34:56"}, {1, "abcdefgh"}, {1, "123456789"}, {1, "é"}} {
		if _, e := encode(x.op, x.text); e == nil {
			t.Fatal(x)
		}
	}
	r := append(bytes.Clone(replyHeader), []byte("12-34-56")...)
	r = append(r, make([]byte, 48)...)
	for _, i := range []int{0, 1, 2, 3, 4, 5, 6, 7, 8} {
		b := bytes.Clone(r)
		b[i] = 255
		if _, e := decode(b); e == nil {
			t.Fatalf("accepted corrupt byte %d", i)
		}
	}
	for _, n := range []int{0, 8, 16, 63} {
		if _, e := decode(r[:n]); e == nil {
			t.Fatal(n)
		}
	}
	r[63] = 255
	if _, e := decode(r); e != nil {
		t.Fatal("stock scratch must be ignored", e)
	}
	for i := range p {
		bad := bytes.Clone(p)
		bad[i] ^= 0xff
		if allowedPacket(bad) {
			t.Fatalf("allowed corrupt packet byte %d", i)
		}
	}
}
func TestPreflightStrictVersionAndV2(t *testing.T) {
	for _, kind := range []string{"version", "v1", "status-error", "length"} {
		t.Run(kind, func(t *testing.T) {
			f := &fakeTransport{change: func(p []byte) []byte {
				switch kind {
				case "version":
					if p[1] == 7 {
						p[3] = '9'
					}
				case "v1":
					if p[1] == 0x7e {
						p[3] = 1
					}
				case "status-error":
					if p[1] == 0x7e {
						p[7] = 1
					}
				case "length":
					p = p[:16]
				}
				return p
			}}
			if _, e := newDevice(f, nil); e == nil || !f.closed {
				t.Fatal(e, f.closed)
			}
			for _, p := range f.writes {
				if p[1] == 0x7e && p[3] != 0 {
					t.Fatal("mutated during preflight")
				}
			}
		})
	}
}
func TestRequestsAndPoisoning(t *testing.T) {
	for _, kind := range []string{"good", "short", "write-error", "v1", "wrong-command", "ack", "status", "timeout", "flood"} {
		t.Run(kind, func(t *testing.T) {
			d, f := opened(t)
			n := len(f.writes)
			switch kind {
			case "short":
				f.short = true
			case "write-error":
				f.writeErr = errors.New("ambiguous")
			case "flood":
				f.flood = true
			default:
				f.change = func(p []byte) []byte {
					switch kind {
					case "v1":
						p[3] = 1
					case "wrong-command":
						p[1] = 7
					case "ack":
						p[8] = 'X'
					case "status":
						if len(f.writes) == n+2 {
							p[8] = 'X'
						}
					case "timeout":
						return nil
					}
					return p
				}
			}
			_, e := d.request(1, "12-34-56")
			if kind == "good" {
				if e != nil {
					t.Fatal(e)
				}
				if _, e = d.request(2, ""); e != nil {
					t.Fatal(e)
				}
				if f.text != "        " {
					t.Fatal(f.text)
				}
				return
			}
			if e == nil || d.healthy {
				t.Fatal("failure not poisoned", e)
			}
			before := len(f.writes)
			if _, e = d.request(2, ""); e == nil || len(f.writes) != before {
				t.Fatal("poisoned context sent CLEAR")
			}
			if kind == "flood" && before != n {
				t.Fatal("sent into busy queue")
			}
		})
	}
}
func TestQuietDrainAndDeadline(t *testing.T) {
	d, f := opened(t)
	f.pending = make([]byte, 64)
	warn := 0
	d.warn = func(string) { warn++ }
	if _, e := d.request(1, "A"); e != nil || warn != 1 {
		t.Fatal(e, warn)
	}
	f.change = func([]byte) []byte { return nil }
	if _, e := d.request(1, "A"); e == nil {
		t.Fatal("nil report accepted")
	}
}
