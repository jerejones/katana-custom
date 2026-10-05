# Provenance and attribution

This project distills an original-device reverse-engineering effort and its
hardware-tested RAM-background implementation. It is unaffiliated with Creative.
Creative, Sound Blaster and Katana marks identify compatible equipment only.

Source lineage (names are historical research units, not runtime dependencies):

- `firmware/endpoint.S`, `glyph-small.S`, `idle-provenance.S`, `placement.py`:
  final **ram-background-provenance** source, based on **ram-background-fit** and
  **display-single-writer-fit**. Frozen compact assembly is the source input;
  the prototype string-rewrite compactor is deliberately not needed at build time.
- `firmware/encoder.py`: exact-subset MIPS32LE encoder from **ram-display-wake**.
  The production build also assembles every placed unit with LLVM and verifies
  exact equality. This is a source build, not an opaque binary delta.
- Go protocol, usbfs transport, scheduler and tests: **go-clock**, refactored into
  a reusable package, portable device selection and XDG state configuration.
- Flasher/parser: **flash-tool**; separate maintenance qualification, entry,
  loader capture, one-shot transfer and verify-return gates preserved.
- CPU fixtures: **ram-display-wake**, **ram-background-provenance**,
  **loader-entry-debug** and **flash-tool/prove_entry**. The host-emulation
  and immutable-research-path dependencies have been removed. Peripheral and
  timing interception limits remain explicit.

The stock MCU image comes only from the exact official Creative updater supplied
by the operator; the project does not claim authorship of vendor routines.
Functional constants and compact dispatch/glyph logic were derived by inspecting
that image. No vendor binary, complete disassembly or captured USB session is
part of the source distribution.

External dependencies are installed separately, not vendored: Go, Python, LLVM,
Unicorn Engine, Capstone and PyUSB. Their upstream licenses govern those tools.
This project's own source is under the MIT License; see LICENSE.md.
