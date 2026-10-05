# Stock input → source build → offline qualification

Nothing here executes an updater or opens USB. Never run Python with `-O`:
placement and CPU audit assertions are safety gates. Use paths outside the source
tree for proprietary downloads, approvals, journals and supplied stock files.

## Prerequisites

- Python 3.10+ (verified with **3.14.7**), standard library for extraction/build/host tests.
- LLVM `llvm-mc` with MIPS32r2 little-endian support (verified **22.1.8**).
  On Fedora the package is `llvm`; on Debian/Ubuntu, `llvm`. No proprietary MCU
  compiler or exact PIC32 part identification is required or claimed.
- Go 1.23+ (verified **1.26.0 linux/amd64**); no Go modules downloaded.
- Optional actual-MIPS tests: Unicorn **2.1.4**, Capstone **5.0.7** from
  `requirements-test.txt`. These are test tools, not hardware emulators of the
  complete SoC. Install in a virtual environment.
- Only for future live flashing: PyUSB **1.3.1**, system libusb 1.0, Linux. These
  are not needed for the Go clock or default tests.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
# Optional, only for the separately reviewed flasher:
.venv/bin/python -m pip install -r requirements-flash.txt
```

## Official local input

Obtain **BXKT_PCFW_EN_2_0_171220_130.exe**, release 28 December 2017, driver ID
100203, from Creative. Do **not** substitute 130B, 2.1, V2 or V2X. Historical
vendor sources recorded by the research (availability has not been rechecked):

- [Creative details](https://support.creative.com/downloads/DriverDetails.aspx?driverID=100203)
- [Official updater URL](https://files.creative.com/manualdn/Firmwares/KATANA_FIRMWARE/Windows/100203/0x15655E57/BXKT_PCFW_EN_2_0_171220_130.exe)

The repository does not fetch it automatically. If unavailable, stop and obtain
a legally held exact copy; do not bypass hashes or use an arbitrary mirror.
Recorded SHA256s establish reproducibility, not a newly performed vendor checksum
or Authenticode trust-chain verification.

| Input | Bytes | SHA256 |
|---|---:|---|
| official EXE | 36451312 | `22abeb87dc943d79f57b2ee2fbf33f2773a61b61e71697f68a8dfd5d3ea102c2` |
| MCU_RCDATA (Intel HEX) | 680978 | `aac90c80f60fb061d3fc368e0c4e76f18a19ce6901188f3cbff11bf955437ab8` |
| normalized MCU BIN | 244220 | `a119076916e5f90c39681cb26d2776d8cf2ea16f406af599a207cc48a525a91f` |

```sh
# Choose your own existing download path and new private output path.
python3 tools/extract_official.py /absolute/path/BXKT_PCFW_EN_2_0_171220_130.exe \
  --output /absolute/private/katana-input/MCU_RCDATA
export KATANA_STOCK_HEX=/absolute/private/katana-input/MCU_RCDATA
```

The extractor validates the whole EXE before extracting the recorded resource
slice (offset `0x3fc40`, length `680978`) and then independently verifies its
SHA256. It refuses an existing destination. This avoids needing PE tooling.
Alternatively `7z x -o/absolute/private/extracted /absolute/path/updater.exe`
extracts `.rsrc/RCDATA/MCU_RCDATA`; the builder still checks exact stock identity.
Neither path runs the EXE. There is no device backup implied by these files.

## Build and verify

```sh
python3 firmware/build.py --stock-hex "$KATANA_STOCK_HEX"
go test -race ./...
go vet ./...
go build -o build/katana-clock ./cmd/katana-clock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/run_cpu.py
```

`build/firmware/manifest.json` records all source hashes, placements, LLVM checks,
coverage and output hashes. The builder validates checksum/length/order/coverage,
only patches populated stock spans, checks nonoverlap and inverse reconstruction,
keeps non-data records and line endings unchanged, and rejects anything differing
from the pinned reference BIN **or HEX** hash. It assembles real instructions using
LLVM, compares each placed unit with the independent subset encoder, and rejects
unresolved ELF relocations. No opaque prebuilt payload is used.

Expected outputs:

- BIN: `e653aff4b0bf75a92729b0ff60703f5d77f65b1db82d965a0ff0c792b0ed4a69`
- HEX: `88f9fb6430f183b85aa23f745b2bc39f7229a4ba4166479de7f45a17d26beaa2`
- 15326 HEX records, 240376 populated bytes, 244220-byte mapped BIN,
  2316 patch-covered bytes (covered is not the count of changed bytes).

This image adds the wake-on-idle-blank behavior described in ARCHITECTURE.md.
It passed offline CPU qualification and one bounded hardware trial, recorded in
`firmware/reference-image.json` under `hardware_trial`. The previous image is
recorded there under `hardware_tested_predecessor`.

Outputs contain vendor firmware and are gitignored. Never publish them by
accident. Regeneration refuses an existing artifact with different content.
A future firmware change needs a new independently reviewed reference and hardware
qualification; do not simply replace the hash to make a failing build pass.

`tests/run_cpu.py` writes reports/logs beneath `build/firmware/cpu-tests`; it checks
11880 inherited cases plus v2/idle suites, placement/RAM/reference audit and stock
maintenance entry/GET_REPORT cases. Host tests use synthetic USB, not hardware.
Without `KATANA_STOCK_HEX`, proprietary-input tests are explicitly skipped; that
is useful for contributors but **not a full firmware qualification**.

To prove no research-tree dependency, run `python3 tools/check_staging.py` with
`KATANA_STOCK_HEX` set. It copies only source files into a fresh scratch directory,
builds Go and firmware there and compares BIN/HEX/manifest bytes. It never copies
vendor input into that source tree. See VERIFICATION.md for actual executed gates.
