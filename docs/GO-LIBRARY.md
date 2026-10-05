# Reusable Go API

The module is `github.com/jerejones/katana-custom`. The importable package lives
in the `katana/` directory, so the import path ends in the package name:

```go
import "github.com/jerejones/katana-custom/katana"
```

From another module, `go get github.com/jerejones/katana-custom/katana` adds the
requirement. The clock command installs with
`go install github.com/jerejones/katana-custom/cmd/katana-clock@latest`.

Linux amd64/arm64 only. No CGo, libusb or Python needed for the Go adapter. Requests
use native usbfs interrupt URBs, not a bulk-transfer approximation. Public clients
serialize complete transactions and hold the shared process lock until Close.

```go
package main

import (
    "log"
    "github.com/jerejones/katana-custom/katana"
)

func main() {
    // This is an explicitly LIVE example: Open enumerates/claims USB.
    c, err := katana.Open(katana.Options{Serial: "YOUR_DEVICE_SERIAL"})
    if err != nil { log.Fatal(err) }
    defer c.Close() // Close releases resources; it DOES NOT send CLEAR.
    text, err := c.Set("HELLO")
    if err != nil { log.Fatal(err) } // no retry or blind cleanup command
    log.Printf("Remembered %q; visibility not established", text)
    if err := c.Clear(); err != nil { log.Fatal(err) }
}
```

`cmd/katana-clock/main.go` is a complete worked example: it opens the device,
calls `Set` once a second, calls `Clear` on a healthy stop, and never issues a
command after a failed exchange.

`Options.Serial` empty means exactly one matching device, not first match.
`Options.StateDir` overrides XDG defaults; every local writer must agree on it.
`Options.Warn` receives bounded stale-report drain notices. `Status()` returns
exactly eight remembered bytes, including NUL bytes after reset, not a trimmed
screen string. `Set()` permits eight ASCII glyphs, maps lowercase to uppercase,
pads with spaces and follows ACK with STATUS. `Clear()` also verifies STATUS.
`Close()` is idempotent, releases the lock, and does not guess display ownership.
All methods are serialized; do not reenter a Client from its warning callback.

A failed exchange poisons the connection. Invalid locally rejected text does not
send USB. There is no low-level public arbitrary report writer, firmware entry,
profile mutation, driver detach or fallback. Timeouts are bounded; no request is
retransmitted. The clock's signal cancellation waits for the in-progress bounded
exchange; it does not abort halfway and then pretend CLEAR is safe.
