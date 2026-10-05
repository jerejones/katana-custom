//go:build linux && (amd64 || arm64)

// Command katana-clock shows the local 24-hour time as HH-MM-SS on a patched
// original Katana. It is the reference user of the katana package: open the
// device, Set once a second, Clear on a healthy exit, Close.
//
// Help and invalid arguments never touch USB. Any ambiguous USB exchange stops
// the clock without retrying and without a blind CLEAR, because the library has
// poisoned the connection; a crash can therefore leave frozen time on the display.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jerejones/katana-custom/katana"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	os.Exit(run(ctx, os.Args[1:], os.Stdout, os.Stderr, deps{open: openClient, clock: realClock{}}))
}

// display is the part of *katana.Client the clock uses; tests substitute a fake.
type display interface {
	Set(text string) (string, error)
	Clear() error
	Close() error
}

type deps struct {
	open  func(katana.Options) (display, error)
	clock clockTime
}

func openClient(o katana.Options) (display, error) {
	c, err := katana.Open(o)
	if err != nil {
		return nil, err
	}
	return c, nil
}

type clockTime interface {
	Now() time.Time
	Wait(context.Context, time.Duration) bool
}
type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }
func (realClock) Wait(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// runClock sends the time immediately, then once per wall-clock second until
// ctx is cancelled or ticks updates have been accepted. Slow exchanges skip
// seconds rather than replaying them. After at least one accepted SET, a
// healthy stop CLEARs; a failed exchange returns without CLEAR.
func runClock(ctx context.Context, d display, ticks int, clock clockTime) (int, error) {
	start := clock.Now()
	phase := start.Add(time.Second - time.Duration(start.Nanosecond()))
	next := start
	count := 0
	for ctx.Err() == nil && (ticks == 0 || count < ticks) {
		now := clock.Now()
		if now.Before(next) {
			if !clock.Wait(ctx, next.Sub(now)) {
				break
			}
			continue
		}
		if _, err := d.Set(clock.Now().Local().Format("15-04-05")); err != nil {
			return count, err
		}
		count++
		completed := clock.Now()
		next = phase
		if !completed.Before(phase) {
			next = phase.Add((completed.Sub(phase)/time.Second + 1) * time.Second)
		}
	}
	if count > 0 {
		if err := d.Clear(); err != nil {
			return count, err
		}
	}
	return count, nil
}

func run(ctx context.Context, args []string, out, errOut io.Writer, deps deps) int {
	fs := flag.NewFlagSet("katana-clock", flag.ContinueOnError)
	fs.SetOutput(errOut)
	serial := fs.String("serial", "", "exact USB serial; empty requires exactly one matching device")
	state := fs.String("state-dir", "", "shared lock directory (default: $XDG_STATE_HOME/katana-custom or ~/.local/state/katana-custom)")
	ticks := fs.Int("ticks", 0, "stop after N accepted updates, then CLEAR (default: run until Ctrl-C)")
	fs.Usage = func() {
		fmt.Fprintln(errOut, "Usage: katana-clock [--serial SERIAL] [--ticks N] [--state-dir DIR]\nShow the local 24-hour time as HH-MM-SS on a patched original Katana. Ctrl-C/SIGTERM CLEARs on a healthy connection.")
		fs.PrintDefaults()
	}
	if e := fs.Parse(args); e != nil {
		if errors.Is(e, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	explicitTicks := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "ticks" {
			explicitTicks = true
		}
	})
	if (explicitTicks && *ticks < 1) || fs.NArg() != 0 {
		fmt.Fprintln(errOut, "ERROR: --ticks must be positive if supplied; no positional arguments")
		return 2
	}
	if ctx.Err() != nil {
		return 0
	}
	warn := func(s string) { fmt.Fprintln(errOut, "WARNING:", s) }
	d, e := deps.open(katana.Options{Serial: *serial, StateDir: *state, Warn: warn})
	if e != nil {
		fmt.Fprintln(errOut, "ERROR:", e, "; no retry or forced CLEAR")
		return 2
	}
	fmt.Fprintln(errOut, "RAM-only HH-MM-SS; ACK/STATUS confirms remembered text, not physical visibility. Crash leaves frozen time.")
	n, e := runClock(ctx, d, *ticks, deps.clock)
	e = errors.Join(e, d.Close())
	if e != nil {
		fmt.Fprintln(errOut, "ERROR:", e, "; stopped without retry or forced CLEAR")
		return 2
	}
	fmt.Fprintf(out, "Accepted %d clock updates; physical screen not verified.", n)
	if n > 0 {
		fmt.Fprintln(out, " Healthy exit CLEAR acknowledged and STATUS checked.")
	} else {
		fmt.Fprintln(out, " No clock SET sent.")
	}
	return 0
}
