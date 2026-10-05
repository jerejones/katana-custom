# Agent guide

This file applies to the entire repository. Keep this project reproducible,
understandable to newcomers, and safe by default. Repository work is not permission
to access hardware, flash a device, change host permissions, or publish a release.

## Read first

- `README.md`: supported hardware, quick start, and current release boundaries.
- `CONTRIBUTING.md`: development and source-release requirements.
- `docs/SAFETY.md`: permanent-brick risk and unproven recovery paths.
- For firmware changes: `docs/BUILD.md`, `docs/ARCHITECTURE.md`, and
  `docs/FLASHING.md`.
- For host changes: `docs/PROTOCOL.md`, `docs/GO-LIBRARY.md`, and
  `docs/USB-ACCESS.md`.
- `docs/VERIFICATION.md`: distinguish historical hardware evidence from tests of
  the portable repository implementation.

## Project map

- `katana/`: importable Go package, the reusable native Linux display client.
  Module path is `github.com/jerejones/katana-custom`.
- `cmd/katana-clock/`: local 24-hour clock CLI.
- `firmware/`: patch assembly, placement, deterministic builder, reference hashes.
- `tools/`: offline extraction, guarded flashing, source-only staging checks.
- `tests/`: host tests and optional actual-MIPS CPU qualification.
- `docs/`: user and technical documentation.
- `build/`: ignored generated artifacts; some contain proprietary vendor code.

Support is intentionally narrow: original Sound BlasterX Katana MF8245, exact
stock base `2.0.171220.130`, and the reviewed API-v2 patch. Do not extend identity
checks to other models or firmware versions without new evidence. The stock
version string is unchanged by the patch; an API-v2 reply proves capability, not
cryptographic identity of the installed image. Go transport targets Linux
amd64/arm64; a cross-build is not runtime qualification.

## Non-negotiable behavior

- Use RAM-only display operations. Never fall back to persistent profile writes.
- Keep stock volume/source feedback in control. Background SET remembers the
  latest text and resumes only when eligible; explicit display-off/standby wins.
- Retain the single global background slot semantics. Do not claim multi-client
  ownership isolation. All cooperating local writers must share the state lock.
- Healthy clock shutdown CLEARs with ACK/STATUS verification. Ambiguous I/O stops
  without retry or blind CLEAR; a crash can leave frozen text.
- Keep strict framing, device identity, descriptor, version, and capability
  checks. Do not invent sequence/nonce guarantees the protocol does not have.
- ACK/STATUS proves remembered text, not visible pixels. Request physical
  confirmation when visual behavior is an acceptance criterion.
- Default builds/tests must not enumerate or open real USB devices. Never add
  automatic driver detachment, USB reset, permission changes, or broad discovery.
- The clock uses `HH-MM-SS`: the stock renderer has no supported colon glyph.

## Firmware and flashing boundaries

Offline development is the default. Live checks, loader entry, transfers, and
recovery experiments require separate explicit operator authorization. Follow
`docs/FLASHING.md` rather than improvising commands from this summary.

- Preserve hash-pinned stock input, exact populated placement, stock HEX layout,
  assembly crosschecks, and all fail-closed audits.
- Do not update expected hashes merely to make a failing test pass. A changed
  image requires fresh qualification and independent review; historical hardware
  success does not qualify it.
- Never patch bootloader/configuration regions or infer erased space is available.
- Normal control uses HID interface 4; maintenance entry uses interface 3. Do not
  conflate them or detach the whole audio device.
- Preserve separate qualification, entry, loader capture, one-shot transfer, and
  return verification. Keep exact image, serial, approval, and evidence lineage.
- Preserve exclusive durable pre-mutation gates and per-record journaling. Never
  delete/rearm consumed gates, create another attempt to evade them, retry erase
  or transfer automatically, or resume at an invented record index.
- Final-reset disconnect is ambiguous. Inspect evidence and separately verify
  return; it is neither proof of failure nor permission to reflash.
- No guaranteed nonboot recovery, bootloader backup, exact MCU identification, or
  whole-device stack/audio-deadline proof is claimed.

## Verification commands

Run from the repository root. Start with offline checks:

```sh
go test -race -count=1 ./...
go vet ./...
go build -o build/katana-clock ./cmd/katana-clock
python3 -m unittest discover -s tests -v
```

For complete firmware qualification, follow `docs/BUILD.md` to obtain the exact
local stock input and install the test dependencies in `.venv`. Set
`KATANA_STOCK_HEX` to that private input path, then run:

```sh
python3 firmware/build.py --stock-hex "$KATANA_STOCK_HEX"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/run_cpu.py
.venv/bin/python tools/check_staging.py --cpu
```

Never run Python with `-O`: assertions are part of the qualification gates.
Report skipped stock-dependent tests explicitly; they are not a full pass.
Use source-only staging to detect accidental dependencies on an external research
checkout. Keep build outputs under ignored paths. For Go changes, format changed
Go files with `gofmt`; test error paths, locking, shutdown, and production adapter
wiring as well as the happy path. For documentation changes, verify commands,
relative links, and required variables on both normal and failure paths.

## Public-source hygiene

- Do not commit vendor firmware, patched images, proprietary disassembly dumps,
  updater executables, raw USB captures, private journals, approvals, or consumed
  gate files. Ignored files are not automatically safe to distribute.
- Do not include personal names, home paths, actual serials, credentials, or local
  environment details. Use synthetic identifiers such as `TEST-SERIAL` in tests.
- Keep user-supplied firmware and live attempt evidence outside the source tree.
- Review staged contents and run `git diff --check` and
  `git diff --cached --check` before handoff. Do not push or publish without a
  separate request.
- Read `LICENSE.md` and `NOTICE.md`. Original work is MIT licensed; the license
  grants nothing over vendor code. Do not relicense, add license headers that
  claim vendor material, or grant rights to vendor code on the owner's behalf.

## Delivery expectations

Make focused changes and add regression coverage for behavioral fixes. Update
user documentation when commands, supported behavior, or safety boundaries change.
Preserve existing work; do not modify outside research trees to make this project
pass. End with changed files, commands actually executed, results/skips, and any
remaining limitations. Distinguish source-complete, offline-tested, cross-built,
and hardware-verified states. Never present a simulated result as a device result.
