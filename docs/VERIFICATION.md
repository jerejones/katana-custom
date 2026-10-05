# Verification record and troubleshooting

## Image status

The image this repository builds today adds wake-on-idle-blank (ARCHITECTURE.md,
design step 7). It passed the complete offline CPU qualification below and was
then flashed once on 2026-10-05 through the FLASHING.md workflow. The change
touches the idle block, the background grant path, and the writer's custom-path
checks; the inherited 11880 cases, the 640-case idle differential and six new
wake cases all pass. The predecessor image's hashes are recorded under
`hardware_tested_predecessor` in `firmware/reference-image.json`.

## Hardware trial of the current image (2026-10-05)

| Step | Observed |
|---|---|
| Read-only qualification | normal version `2.0.171220.130`; maintenance `3f 80 02 00`; same bus and port |
| Loader entry and capture | device re-enumerated as `041e:30cf` "SB EVO Upgrader", loader version `0100`, 16/64 report shape; capture ran as the documented resume after the entry disconnect |
| Transfer | erase, 15326 records journaled, finalize, reset; `USBError 19` at reset as in earlier trials |
| Verify-return | same serial, revision 0200, same port, pinned version over interface 4 |
| Clock trial | 30/30 ticks accepted, CLEAR acknowledged with STATUS readback |
| Visual | display was dark after a stock "COMPUTER" message idled out; it turned on by itself when the clock started, showed `HH-MM-SS`, and blanked on CLEAR |
| Stock priority | volume feedback displayed over the running clock, then the clock returned |
| Explicit off | stock display-off stayed off while the clock kept sending; display-on resumed the clock |

Not yet observed on this image: long-duration operation.
Evidence files live in the private attempt directory, not in this repository.

## Offline verification of this repository

Environment: Linux amd64, Go **1.26.0**, Python **3.14.7**, LLVM **22.1.8**,
Unicorn **2.1.4**, Capstone **5.0.7**, PyUSB **1.3.1** installed separately.
Everything in this table ran offline. No device was opened or flashed.

| Gate | Observed result |
|---|---|
| `go test -race -count=1 ./...` | PASS; package and CLI compile, race detector enabled |
| `go vet ./...` | PASS |
| native CLI `go build -o build/katana-clock ./cmd/katana-clock` | PASS |
| `GOOS=linux GOARCH=arm64 go build -o build/katana-clock-arm64 ./cmd/katana-clock` | PASS cross-build; **not arm64 execution** |
| default Python unittest discovery, no stock env | 36 discovered, 13 passed, 23 explicitly skipped |
| Python unittest discovery with local stock + built image | 36 passed, no skips |
| `tests/run_cpu.py` | all nine scripts passed; 11880 inherited cases plus v2/idle/entry/audit suites and six wake cases |
| v2 protocol sub-suite | 2055 cases, invalid inputs preserved state |
| idle differential sub-suite | 640 cases; one intended suppression, all others stock-identical |
| stock maintenance entry/GET_REPORT | nine bounded CPU cases passed |
| source-build vs reference image | exact BIN and HEX SHA256 match (current, unflashed image) |
| independent official-EXE extraction → build | same exact BIN/HEX; resource hash verified |
| placement audit | 2316 covered / 2105 actually changed bytes, inverse stock reconstruction |
| source-only staging copy `tools/check_staging.py --cpu` | Go race/vet/build, Python tests, all CPU scripts passed; BIN/HEX/manifest byte-identical |
| CLI `--help`, flasher `--inspect` with TEST-SERIAL | offline success; 15326 records, application scope, exact reference hashes |

The nine CPU scripts are enumerated in `tests/run_cpu.py`, not a single aggregate
claim that all hardware conditions were covered. Reports live under ignored
`build/firmware/cpu-tests/`. `build/firmware/manifest.json` binds firmware build
sources and output identity. `build/staging-verification.json` records the fresh
source-only copy and executed commands. Generated evidence may contain local
paths or vendor-derived data: do not publish it without separate sanitization.

Independent verification from the repository root:

```sh
export KATANA_STOCK_HEX=/absolute/private/katana-input/MCU_RCDATA
python3 firmware/build.py --stock-hex "$KATANA_STOCK_HEX"
go test -race -count=1 ./...
go vet ./...
go build -o build/katana-clock ./cmd/katana-clock
GOOS=linux GOARCH=arm64 go build -o build/katana-clock-arm64 ./cmd/katana-clock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/run_cpu.py
.venv/bin/python tools/check_staging.py --cpu
python3 tools/katana_flash.py build/firmware/candidate.hex --stock-hex "$KATANA_STOCK_HEX" --serial TEST-SERIAL --inspect
```

These commands do not open USB. The inspection output is not an approval. See
BUILD.md for dependency setup and extraction. Firmware reference identity is in
`firmware/reference-image.json`; each fresh build includes source hashes in its
own manifest. The CLI adapter refactor has not been hardware-run in this repo.

## Earlier hardware evidence (predecessor image, from the research)

| Trial | Outcome and limit |
|---|---|
| Original stock profile-name HELLO | Visually worked, but setter persists; retired, not shipped |
| Timed RAM v1 | Visually worked, refresh/idle clock gaps; not recommended for clocks |
| Final source-built v2 background | HELLO remained 20 seconds without refresh; CLEAR visibly removed it |
| Stock feedback interleaving | Volume → COMPUTER → latest clock visually confirmed |
| Python v2 clock | 30/30 accepted ticks in prior hardware trial |
| Native Go research clock | 8/8 accepted ticks, ACK/STATUS/CLEAR, user-visible |
| Firmware installation lineage | Four research releases transferred all 15326 records; final reset disappearance required separate verify-return |
| Audio/control | Normal use preserved in trials; not every feature/audio mode certified |

These are prior observations, not fresh execution results of the portable
refactor. Exact image reproduction ties the source build to that final firmware;
it does not establish the image installed on another device. The unchanged
version string cannot provide that identity. API-v2 STATUS proves capability,
not a full flash readback.

## Troubleshooting without unsafe shortcuts

- **No device / multiple devices:** specify an exact serial; never pick first USB
  match. Only original model VID/PID/revision is supported.
- **Permission denied / driver bound:** read USB-ACCESS.md. Grant narrowly scoped
  access and consciously unbind only the required interface. No sudo fallback.
- **Base version passes, v2 STATUS fails:** stock or older custom firmware may be
  installed. Do not fall back to v1/profile setters. Review image provenance.
- **ACK succeeds but display stays dark:** ACK is remembered text, not visibility.
  Stock off/standby/inhibitors win. Normal stock activity can wake; no forced wake
  command exists in this client.
- **Clock replaced by volume/source:** expected stock priority. Latest SET should
  resume after feedback; competing writers or unsupported modes need investigation.
- **Frozen time after process crash:** expected last RAM value. Stop competing
  writers, then deliberately use a new healthy API-v2 connection to CLEAR. Do not
  make a failed connection silently retry/clear an unknown transaction.
- **Lock busy:** find and stop the actual cooperating owner. Do not delete the
  lock file while another process holds its inode. Use the same state directory.
- **`pending.json` present:** old profile restoration requires separate review;
  no tool here erases or repairs that recovery state.
- **Wrong stock hash / HEX formatting mismatch / LLVM mismatch:** stop. Correct
  the input/toolchain; do not weaken checks, normalize the HEX or patch reference
  hashes to force acceptance.
- **Entry/transfer/reset error:** preserve evidence and gates. Resolve access and
  use capture-loader or verify-return when appropriate; never automatically repeat
  entry or erase. See FLASHING.md for the explicit stage boundaries.
