//go:build linux && (amd64 || arm64)

package katana

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

// acquireLock takes the shared non-blocking flock in the state directory that
// every cooperating local writer, including the flasher, must agree on. It
// refuses to proceed while an old profile-clock recovery marker is present.
func acquireLock(state string) (*os.File, error) {
	if err := os.MkdirAll(state, 0700); err != nil {
		return nil, err
	}
	fd, e := syscall.Open(filepath.Join(state, "clock.lock"), syscall.O_RDWR|syscall.O_CREAT|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0600)
	if e != nil {
		return nil, e
	}
	f := os.NewFile(uintptr(fd), "clock.lock")
	fail := func(err error) (*os.File, error) { f.Close(); return nil, err }
	if e = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		return fail(fmt.Errorf("another clock/recovery instance may hold the lock: %w", e))
	}
	if _, e = os.Lstat(filepath.Join(state, "pending.json")); e == nil {
		return fail(errors.New("old profile restore pending; separate recovery review required; no USB opened"))
	} else if !os.IsNotExist(e) {
		return fail(e)
	}
	return f, nil
}
