//go:build linux && (amd64 || arm64)

package katana

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestPortableSelection(t *testing.T) {
	root := t.TempDir()
	makeDev := func(name, serial, num string) {
		p := filepath.Join(root, name)
		os.Mkdir(p, 0700)
		for k, v := range map[string]string{"idVendor": "041e", "idProduct": "3247", "serial": serial, "busnum": "3", "devnum": num} {
			os.WriteFile(filepath.Join(p, k), []byte(v), 0600)
		}
	}
	makeDev("3-1", "ALPHA", "4")
	got, e := selectUSB(root, "/fakeusb", "")
	if e != nil || got.serial != "ALPHA" || got.path != "/fakeusb/003/004" {
		t.Fatal(got, e)
	}
	makeDev("3-2", "BETA", "5")
	if _, e = selectUSB(root, "/fakeusb", ""); e == nil {
		t.Fatal("ambiguous device accepted")
	}
	got, e = selectUSB(root, "/fakeusb", "BETA")
	if e != nil || got.path != "/fakeusb/003/005" {
		t.Fatal(got, e)
	}
	if _, e = selectUSB(root, "/fakeusb", "MISSING"); e == nil {
		t.Fatal("wrong serial accepted")
	}
	makeDev("3-3", "BETA", "6")
	if _, e = selectUSB(root, "/fakeusb", "BETA"); e == nil {
		t.Fatal("duplicate serial accepted")
	}
}
func TestXDGAndNewLock(t *testing.T) {
	base := t.TempDir()
	t.Setenv("XDG_STATE_HOME", base)
	state, e := DefaultStateDir()
	if e != nil || state != filepath.Join(base, "katana-custom") {
		t.Fatal(state, e)
	}
	f, e := acquireLock(state)
	if e != nil {
		t.Fatal(e)
	}
	if g, e := acquireLock(state); e == nil {
		g.Close()
		t.Fatal("duplicate lock")
	}
	f.Close()
	st, e := os.Stat(filepath.Join(state, "clock.lock"))
	if e != nil || st.Mode().Perm() != 0600 {
		t.Fatal(st, e)
	}
	t.Setenv("XDG_STATE_HOME", "relative")
	if _, e := DefaultStateDir(); e == nil {
		t.Fatal("relative XDG accepted")
	}
}
func TestRelativeStateDirRefused(t *testing.T) {
	if _, e := Open(Options{StateDir: "relative/state"}); e == nil || !strings.Contains(e.Error(), "absolute") {
		t.Fatal("relative StateDir accepted", e)
	}
}
func TestPublicClientTransactions(t *testing.T) {
	f := &fakeTransport{}
	d, e := newDevice(f, nil)
	if e != nil {
		t.Fatal(e)
	}
	lock, e := acquireLock(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	c := &Client{d: d, lock: lock}
	text, e := c.Set("hello")
	if e != nil || text != "HELLO   " {
		t.Fatal(text, e)
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := c.Status(); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	if e := c.Clear(); e != nil {
		t.Fatal(e)
	}
	before := len(f.writes)
	if e := c.Close(); e != nil {
		t.Fatal(e)
	}
	if e := c.Close(); e != nil {
		t.Fatal(e)
	}
	if _, e := c.Set("NO"); e == nil {
		t.Fatal("closed client allowed traffic")
	}
	if len(f.writes) != before {
		t.Fatal("Close emitted hidden traffic")
	}
}
