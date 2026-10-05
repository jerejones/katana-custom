# Katana custom RAM display

**Experimental firmware for the original Sound BlasterX Katana (MF8245), not
Katana V2 or V2X. Flashing can permanently brick the device. Recovery is not
guaranteed.** This is an independent project, not a Creative release.

## What this is

The original Katana soundbar has a small eight-character segment display on its
front. Stock firmware only uses it for feedback such as the volume level or the
active input, and blanks it after a timeout. This project adds a small patch to
that firmware so a computer can put its own eight characters on the display,
for example a clock reading `12-34-56`, while the stock feedback keeps working
exactly as before.

The patched firmware behaves like this:

- The host sends a short USB message with up to eight characters. The firmware
  keeps that text in RAM and shows it whenever the display is on and nothing
  else is using it.
- If the display went dark from the stock idle timeout, a SET turns it back on.
  If you switched the display off yourself, the text is stored but stays
  invisible until you turn it on again; the patch never overrides an explicit
  off. Once on, your text keeps the display from idling off again.
- Stock feedback still wins. When you change the volume, the display shows the
  volume as usual, then returns to your text.
- The text survives until the host clears it or the device resets. Nothing is
  written to flash and no saved profile or setting is modified.
- A Linux command-line clock, `katana-clock`, updates the text once a second.
  A Go package lets other programs do the same.

The repository contains three independent parts. You only need the first one if
your device already runs the patched firmware.

| Part | What it is |
|---|---|
| Go client (`katana/`, `cmd/katana-clock/`) | Talks to a patched device over USB. Never flashes anything. |
| Firmware build (`firmware/`) | Reproducibly builds the patched image from the official stock firmware that you supply. |
| Flasher (`tools/katana_flash.py`) | A deliberately slow, multi-stage tool for installing the image. Every stage requires explicit operator action. |

### Terms used in these docs

- **Stock firmware**: Creative's own MCU firmware, version `2.0.171220.130`. The
  patch keeps that version string unchanged.
- **Background text**: the eight characters the host asked the display to show
  when nothing else needs it.
- **API v2**: the small USB request format this patch adds. See
  [docs/PROTOCOL.md](docs/PROTOCOL.md). An API v2 reply proves the patch is
  present; it does not prove which exact image is installed.
- **SET / STATUS / CLEAR**: the three API v2 operations. SET stores text, STATUS
  reads back the stored text, CLEAR releases the display.
- **ACK**: the device's reply to a request. It confirms the text was remembered,
  not that it is physically visible on the screen.
- **`HH-MM-SS`**: the clock format. The stock glyph table has no colon, so the
  separator is a hyphen.

## Already running the patched firmware?

Linux amd64/arm64, Go 1.23 or later; the Go client has no external dependencies.
Help and tests do not touch USB. Running the clock does.

```sh
go test -race ./...
go vet ./...
go build -o build/katana-clock ./cmd/katana-clock
./build/katana-clock --help
# After reviewing permissions/interface 4 access in docs/USB-ACCESS.md:
./build/katana-clock --serial YOUR_DEVICE_SERIAL --ticks 8
./build/katana-clock --serial YOUR_DEVICE_SERIAL
```

Omit `--serial` only if exactly one matching original Katana is connected. The
selected sysfs serial is rechecked against the live USB string descriptor; wrong
revision/descriptors/base version/API are refused. No automatic detach, reset,
permission changes or profile-write fallback occurs. State defaults to
`$XDG_STATE_HOME/katana-custom` (otherwise `~/.local/state/katana-custom`);
`--state-dir /absolute/private/path` overrides it. All local writers must use the
same directory. A `pending.json` in the selected state directory from an old
profile-clock recovery blocks USB. When migrating an older installation, stop its
clock first and explicitly select its existing state directory until any pending
recovery is reviewed; the new XDG default does not discover or migrate old state.

The first update is sent immediately; later updates align to the host's wall-clock
second. A slow USB exchange skips seconds rather than replaying them.

Ctrl-C/SIGTERM on a healthy connection sends CLEAR and verifies ACK plus STATUS.
A second Ctrl-C does not abort that shutdown; the exchange is bounded by its own
timeouts. A crash leaves frozen time. Ambiguous I/O stops without retry or blind
CLEAR. ACK/STATUS confirms remembered text, **not physical screen visibility**.

## New to custom firmware?

1. Check the original model and read [risk/limitations](docs/SAFETY.md).
2. Supply the exact official stock updater locally and follow
   [extract → source build → offline qualification](docs/BUILD.md).
3. Independently review image hashes, changes and risks. Then, only if you choose
   to accept the risk, follow the [separate gated flashing workflow](docs/FLASHING.md).
   The build is not permission to flash. There is no one-command installer.
4. Arrange [narrow USB access](docs/USB-ACCESS.md), verify API v2, then use the clock.

## Repository map

| Path | Purpose |
|---|---|
| `katana/` | importable Go package: `katana.Open`, `Set`, `Status`, `Clear`, `Close` |
| `cmd/katana-clock` | the clock command; a worked example of using the package |
| `firmware/` | readable patch assembly, placement, LLVM-checked deterministic build |
| `tools/` | offline official-resource extraction; guarded original-device flasher |
| `tests/` | synthetic host/flash tests; optional actual-MIPS CPU qualification |
| `docs/` | wire protocol, architecture, build, safety, access, flashing, verification |

[Go library](docs/GO-LIBRARY.md) · [protocol](docs/PROTOCOL.md) ·
[architecture and research conclusions](docs/ARCHITECTURE.md) ·
[verified matrix / troubleshooting](docs/VERIFICATION.md) · [contributing](CONTRIBUTING.md)

## Scope and release status

The stock version string remains `2.0.171220.130`; **API v2 distinguishes
capability**, not a cryptographic identity of installed flash. Only the exact
image hashes in `firmware/reference-image.json` are reproducible here. The current
image adds wake-on-idle-blank to the image the research trials ran on, passed the
offline CPU qualification suite, and was then flashed once through the gated
workflow: the clock ran, and a display darkened by the idle timeout woke on its
own. That is one bounded trial on one device, not certification; see
[docs/VERIFICATION.md](docs/VERIFICATION.md) for exactly what was observed.

Native transport support is Linux 64-bit amd64/arm64, not Windows/macOS. The
firmware builder is Python plus LLVM with MIPS target support. Normal audio and
controls worked in the research trials; this is not whole-device certification.

## Authorship

All of the research, firmware, code and documentation in this repository were
produced by two AI models, GPT-6 Astra and Claude Fable 5.1, working under the
direction of the repository owner, who reviewed the results, authorized each
hardware step and watched the display.

## License

The original work here is released under the [MIT License](LICENSE.md). That
grants nothing over Creative's firmware or updater, which are not distributed; see
[NOTICE.md](NOTICE.md). No vendor binaries, personal serials, consumed gates, raw
captures or private journals belong in a source release.
