package katana

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
	"time"
)

const baseVersion = "2.0.171220.130\x00"

// glyphs is the complete set of bytes the firmware endpoint accepts in SET text.
// The stock renderer has no colon glyph, so the clock uses HH-MM-SS.
const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -+*#"

var hidDescriptor = []byte{6, 0, 255, 9, 1, 161, 1, 25, 1, 41, 64, 21, 0, 38, 255, 0, 117, 8, 149, 64, 129, 0, 25, 1, 41, 64, 145, 0, 192}
var replyHeader = []byte{0x5a, 0x7e, 0x0d, 2, 'R', 'D', 2, 0}
var errTimeout = errors.New("USB read timed out")

func encode(op byte, text string) ([]byte, error) {
	if op > 2 || (op != 1 && text != "") || len(text) > 8 {
		return nil, errors.New("invalid background command")
	}
	for _, c := range []byte(text) {
		if strings.IndexByte(glyphs, c) < 0 {
			return nil, errors.New("unsupported glyph; use HH-MM-SS, not colons")
		}
	}
	p := make([]byte, 64)
	copy(p, []byte{0x5a, 0x7e, 0x0d, op, 'R', 'D', 2, 0})
	copy(p[8:16], []byte("        "))
	copy(p[8:16], text)
	return p, nil
}
func decode(p []byte) (string, error) {
	if len(p) != 64 || !bytes.Equal(p[:8], replyHeader) {
		return "", errors.New("not a full API-v2 success report; no fallback")
	}
	text := p[8:16]
	if !bytes.Equal(text, make([]byte, 8)) {
		for _, c := range text {
			if strings.IndexByte(glyphs, c) < 0 {
				return "", errors.New("invalid remembered text")
			}
		}
	}
	return string(text), nil
}
func versionQuery() []byte { p := make([]byte, 64); copy(p, []byte{0x5a, 7, 1, 2}); return p }
func allowedPacket(p []byte) bool {
	if bytes.Equal(p, versionQuery()) {
		return true
	}
	if len(p) != 64 {
		return false
	}
	text := ""
	if p[3] == 1 {
		text = string(p[8:16])
	}
	want, err := encode(p[3], text)
	return err == nil && bytes.Equal(p, want)
}

type transport interface {
	Read(time.Duration) ([]byte, error)
	Write([]byte) (int, error)
	Close() error
}
type device struct {
	t                transport
	healthy, capable bool
	now              func() time.Time
	warn             func(string)
}

func newDevice(t transport, warn func(string)) (*device, error) {
	d := &device{t: t, healthy: true, now: time.Now, warn: warn}
	p, err := d.exchange(versionQuery())
	if err == nil && (!bytes.Equal(p[:3], []byte{0x5a, 7, 15}) || !bytes.Equal(p[3:3+len(baseVersion)], []byte(baseVersion))) {
		err = errors.New("installed base version mismatch")
	}
	if err == nil {
		_, err = d.request(0, "")
	}
	if err != nil {
		d.healthy = false
		return nil, errors.Join(err, t.Close())
	}
	d.capable = true
	return d, nil
}
func (d *device) exchange(p []byte) (out []byte, err error) {
	if !allowedPacket(p) {
		return nil, errors.New("disallowed packet")
	}
	if !d.healthy {
		return nil, errors.New("transport poisoned; refusing traffic")
	}
	defer func() {
		if err != nil {
			d.healthy = false
		}
	}()
	drained := 0
	for ; drained < 64; drained++ {
		_, e := d.t.Read(20 * time.Millisecond)
		if errors.Is(e, errTimeout) {
			break
		}
		if e != nil {
			return nil, e
		}
	}
	if drained == 64 {
		return nil, errors.New("interrupt queue did not become quiet; nothing sent")
	}
	if drained > 0 && d.warn != nil {
		d.warn(fmt.Sprintf("discarded %d queued report(s) before request; no sequence/nonce exists", drained))
	}
	n, e := d.t.Write(p)
	if e != nil {
		return nil, e
	}
	if n != 64 {
		return nil, errors.New("short SET_REPORT; outcome unknown")
	}
	deadline := d.now().Add(2 * time.Second)
	for i := 0; i < 64; i++ {
		left := deadline.Sub(d.now())
		if left <= 0 {
			break
		}
		if left > 100*time.Millisecond {
			left = 100 * time.Millisecond
		}
		r, e := d.t.Read(left)
		if errors.Is(e, errTimeout) {
			continue
		}
		if e != nil {
			return nil, e
		}
		if len(r) != 64 {
			return nil, errors.New("malformed interrupt report length")
		}
		if !bytes.Equal(r[:2], p[:2]) {
			return nil, errors.New("uncorrelated reply; stop competing writers")
		}
		return r, nil
	}
	return nil, errors.New("no correlated reply; outcome unknown, no retry")
}
func (d *device) request(op byte, text string) (out string, err error) {
	p, err := encode(op, text)
	if err != nil {
		return "", err
	}
	defer func() {
		if err != nil {
			d.healthy = false
		}
	}()
	r, err := d.exchange(p)
	if err != nil {
		return "", err
	}
	out, err = decode(r)
	if err != nil {
		return "", err
	}
	if op == 1 || op == 2 {
		want := "        "
		if op == 1 {
			want = string(p[8:16])
		}
		if out != want {
			return "", errors.New("ACK text mismatch; outcome unknown")
		}
		status, _ := encode(0, "")
		r, err = d.exchange(status)
		if err != nil {
			return "", err
		}
		check, e := decode(r)
		if e != nil {
			return "", e
		}
		if check != want {
			return "", errors.New("STATUS readback mismatch; outcome unknown")
		}
	}
	return out, nil
}
