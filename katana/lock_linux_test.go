//go:build linux && (amd64 || arm64)

package katana

import (
	"os"
	"path/filepath"
	"testing"
)

func stateFixture(t *testing.T) string {
	t.Helper()
	s := t.TempDir()
	if e := os.WriteFile(filepath.Join(s, "clock.lock"), []byte("preserve"), 0600); e != nil {
		t.Fatal(e)
	}
	return s
}
func TestSharedLockGuards(t *testing.T) {
	s := stateFixture(t)
	f, e := acquireLock(s)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = acquireLock(s); e == nil {
		t.Fatal("double lock")
	}
	f.Close()
	os.Symlink("absent", filepath.Join(s, "pending.json"))
	if _, e = acquireLock(s); e == nil {
		t.Fatal("dangling pending accepted")
	}
	os.Remove(filepath.Join(s, "pending.json"))
	os.Remove(filepath.Join(s, "clock.lock"))
	os.Symlink("missing", filepath.Join(s, "clock.lock"))
	if _, e = acquireLock(s); e == nil {
		t.Fatal("symlink lock accepted")
	}
	b, _ := os.ReadFile(filepath.Join(stateFixture(t), "clock.lock"))
	if string(b) != "preserve" {
		t.Fatal("fixture lock content changed")
	}
}
