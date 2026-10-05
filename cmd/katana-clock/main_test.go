//go:build linux && (amd64 || arm64)

package main

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/jerejones/katana-custom/katana"
)

// fakeDisplay stands in for *katana.Client. It records every call and can
// fail a SET on demand, mimicking the library's poisoned connection.
type fakeDisplay struct {
	text    string
	sets    []string
	clears  int
	closed  bool
	onSet   func(string)
	failSet bool
}

func (f *fakeDisplay) Set(text string) (string, error) {
	if f.onSet != nil {
		f.onSet(text)
	}
	if f.failSet {
		return "", errors.New("ambiguous exchange")
	}
	f.sets = append(f.sets, text)
	f.text = text
	return text, nil
}
func (f *fakeDisplay) Clear() error { f.clears++; f.text = "        "; return nil }
func (f *fakeDisplay) Close() error { f.closed = true; return nil }

type fakeClock struct {
	now   time.Time
	waits []time.Duration
}

func (f *fakeClock) Now() time.Time { return f.now }
func (f *fakeClock) Wait(ctx context.Context, d time.Duration) bool {
	f.waits = append(f.waits, d)
	f.now = f.now.Add(d)
	return ctx.Err() == nil
}

func opener(f *fakeDisplay, opened *[]katana.Options) func(katana.Options) (display, error) {
	return func(o katana.Options) (display, error) {
		if opened != nil {
			*opened = append(*opened, o)
		}
		return f, nil
	}
}

func TestClockAnchoredSkipAndFreshTime(t *testing.T) {
	f := &fakeDisplay{}
	c := &fakeClock{now: time.Date(2026, 1, 1, 12, 34, 56, 200000000, time.Local)}
	f.onSet = func(string) {
		if len(f.sets) == 0 {
			c.now = c.now.Add(2500 * time.Millisecond) // first exchange is slow
		}
	}
	n, e := runClock(context.Background(), f, 3, c)
	if e != nil || n != 3 {
		t.Fatal(n, e)
	}
	if fmt.Sprint(f.sets) != "[12-34-56 12-34-59 12-35-00]" {
		t.Fatal(f.sets)
	}
	if len(c.waits) != 2 || c.waits[0] != 300*time.Millisecond || c.waits[1] != time.Second {
		t.Fatal(c.waits)
	}
	if f.clears != 1 {
		t.Fatal("healthy finish must CLEAR once")
	}
}

func TestHealthyStopAndErrorNoClear(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(fmt.Sprint(fail), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			f := &fakeDisplay{}
			f.onSet = func(string) { cancel(); f.failSet = fail }
			n, e := runClock(ctx, f, 0, &fakeClock{now: time.Now()})
			if fail {
				if e == nil || n != 0 || f.clears != 0 {
					t.Fatal("failed SET must not be followed by CLEAR", n, e, f.clears)
				}
			} else if e != nil || n != 1 || f.clears != 1 {
				t.Fatal(n, e, f.clears)
			}
		})
	}
}

func TestNoSetMeansNoClear(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	f := &fakeDisplay{}
	n, e := runClock(ctx, f, 0, &fakeClock{now: time.Now()})
	if e != nil || n != 0 || f.clears != 0 {
		t.Fatal("must not CLEAR someone else's background", n, e, f.clears)
	}
}

func TestCLIPassesOptionsAndReports(t *testing.T) {
	f := &fakeDisplay{}
	var opened []katana.Options
	var out, errOut bytes.Buffer
	rc := run(context.Background(), []string{"--serial", "TEST-SERIAL", "--state-dir", "/tmp/example-state", "--ticks", "3"}, &out, &errOut, deps{open: opener(f, &opened), clock: &fakeClock{now: time.Now()}})
	if rc != 0 || !f.closed || f.clears != 1 || len(f.sets) != 3 || !bytes.Contains(out.Bytes(), []byte("Accepted 3")) {
		t.Fatal(rc, out.String(), errOut.String())
	}
	if len(opened) != 1 || opened[0].Serial != "TEST-SERIAL" || opened[0].StateDir != "/tmp/example-state" || opened[0].Warn == nil {
		t.Fatal(opened)
	}
	opened[0].Warn("stale")
	if !bytes.Contains(errOut.Bytes(), []byte("WARNING: stale")) {
		t.Fatal(errOut.String())
	}
}

func TestCLIRefusesBeforeOpen(t *testing.T) {
	for _, args := range [][]string{{"--help"}, {"--ticks", "0"}, {"--ticks", "-1"}, {"--unknown-flag"}, {"extra"}} {
		calls := 0
		var errOut bytes.Buffer
		rc := run(context.Background(), args, &bytes.Buffer{}, &errOut, deps{open: func(katana.Options) (display, error) { calls++; return nil, errors.New("unexpected") }})
		if calls != 0 {
			t.Fatal("opened device for", args)
		}
		if args[0] == "--help" && (rc != 0 || !bytes.Contains(errOut.Bytes(), []byte("Usage:"))) {
			t.Fatal(rc, errOut.String())
		}
		if args[0] != "--help" && rc != 2 {
			t.Fatal(args, rc)
		}
	}
}

func TestOpenFailureIsReported(t *testing.T) {
	var out, errOut bytes.Buffer
	rc := run(context.Background(), nil, &out, &errOut, deps{open: func(katana.Options) (display, error) { return nil, errors.New("USB serial mismatch") }, clock: realClock{}})
	if rc != 2 || !strings.Contains(errOut.String(), "USB serial mismatch") || out.Len() != 0 {
		t.Fatal(rc, out.String(), errOut.String())
	}
}

// TestSignalProcessHelper is the child side of TestRealSignalShutdown; it is a
// no-op unless the environment variable is set.
func TestSignalProcessHelper(t *testing.T) {
	if os.Getenv("KATANA_TEST_SIGNAL") != "1" {
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	f := &fakeDisplay{}
	f.onSet = func(string) {
		if len(f.sets) == 0 {
			fmt.Println("READY")
		}
	}
	rc := run(ctx, nil, os.Stdout, os.Stderr, deps{open: opener(f, nil), clock: realClock{}})
	if rc != 0 || !f.closed || f.clears != 1 {
		os.Exit(4)
	}
	os.Exit(0)
}

func TestRealSignalShutdown(t *testing.T) {
	for _, sig := range []syscall.Signal{syscall.SIGINT, syscall.SIGTERM} {
		t.Run(sig.String(), func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestSignalProcessHelper$")
			cmd.Env = append(os.Environ(), "KATANA_TEST_SIGNAL=1")
			pipe, e := cmd.StdoutPipe()
			if e != nil {
				t.Fatal(e)
			}
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			if e = cmd.Start(); e != nil {
				t.Fatal(e)
			}
			scanner := bufio.NewScanner(pipe)
			if !scanner.Scan() || scanner.Text() != "READY" {
				t.Fatal("not ready", scanner.Err())
			}
			if e = cmd.Process.Signal(sig); e != nil {
				t.Fatal(e)
			}
			var lines []string
			for scanner.Scan() {
				lines = append(lines, scanner.Text())
			}
			if e = cmd.Wait(); e != nil {
				t.Fatal(e, stderr.String())
			}
			if !strings.Contains(strings.Join(lines, "\n"), "CLEAR acknowledged and STATUS checked") {
				t.Fatal(lines)
			}
		})
	}
}
