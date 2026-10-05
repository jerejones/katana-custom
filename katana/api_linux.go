//go:build linux && (amd64 || arm64)

// Package katana controls the experimental original-Katana RAM background API v2.
// It never flashes firmware, detaches drivers, changes permissions or writes profiles.
package katana

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Options selects an exact serial, or a unique matching device if Serial is empty.
// All local clients (including the flasher) must share StateDir. A different
// directory defeats mutual exclusion. Warn receives stale-queue warnings.
type Options struct {
	Serial, StateDir string
	Warn             func(string)
}

// DefaultStateDir returns $XDG_STATE_HOME/katana-custom or ~/.local/state/katana-custom.
func DefaultStateDir() (string, error) {
	base := os.Getenv("XDG_STATE_HOME")
	if base != "" && !filepath.IsAbs(base) {
		return "", errors.New("XDG_STATE_HOME must be absolute")
	}
	if base == "" {
		home, e := os.UserHomeDir()
		if e != nil {
			return "", e
		}
		base = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(base, "katana-custom"), nil
}

// Client serializes complete request/ACK/STATUS transactions and holds a local
// process lock until Close. The wire has no sequence IDs or multi-owner protocol.
// An ambiguous exchange poisons the connection; no operation is retried.
type Client struct {
	mu     sync.Mutex
	d      *device
	lock   *os.File
	closed bool
}

// Open validates USB descriptors, base version and API-v2 STATUS before returning.
// It requires existing USB permissions and an already-unbound HID interface 4.
func Open(o Options) (*Client, error) {
	state := o.StateDir
	if state != "" && !filepath.IsAbs(state) {
		return nil, errors.New("StateDir must be absolute so every local writer shares one lock")
	}
	if state == "" {
		var e error
		state, e = DefaultStateDir()
		if e != nil {
			return nil, e
		}
	}
	lock, e := acquireLock(state)
	if e != nil {
		return nil, e
	}
	t, e := openUSB(o.Serial)
	if e != nil {
		lock.Close()
		return nil, e
	}
	d, e := newDevice(t, o.Warn)
	if e != nil {
		lock.Close()
		return nil, e
	}
	return &Client{d: d, lock: lock}, nil
}
func (c *Client) request(op byte, text string) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return "", errors.New("client closed")
	}
	return c.d.request(op, text)
}

// Set remembers at most eight ASCII glyphs. Lowercase ASCII is mapped to uppercase.
// Success confirms remembered text, NOT that the physical display is visible.
func (c *Client) Set(text string) (string, error) {
	for _, b := range []byte(text) {
		if b >= 128 {
			return "", errors.New("only ASCII glyphs are supported")
		}
	}
	return c.request(1, strings.ToUpper(text))
}

// Status returns exactly eight remembered bytes, possibly all NULs after reset.
func (c *Client) Status() (string, error) { return c.request(0, "") }

// Clear relinquishes background display ownership without clearing stock feedback.
func (c *Client) Clear() error { _, e := c.request(2, ""); return e }

// Close releases USB and the process lock. It deliberately does not issue CLEAR:
// a client can persist text after exit, or be leaving an ambiguous transaction.
func (c *Client) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	return errors.Join(c.d.t.Close(), c.lock.Close())
}
