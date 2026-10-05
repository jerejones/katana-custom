# Contributing

The original work here is under the MIT License; see [LICENSE.md](LICENSE.md).
Contributions are accepted under the same terms. The license does not extend to
Creative's firmware, and nobody should distribute vendor-derived build products.

## Default checks

These run offline and need no device, no stock firmware and no Python packages
beyond the standard library:

```sh
go test -race -count=1 ./...
go vet ./...
go build -o build/katana-clock ./cmd/katana-clock
python3 -m unittest discover -s tests -v
```

Keep build output under the ignored `build/` directory. No default test imports
PyUSB or enumerates hardware. Tests that need the stock image are skipped when
`KATANA_STOCK_HEX` is unset; a run with skips is not a full qualification, so say
so when reporting results. [docs/BUILD.md](docs/BUILD.md) covers the complete
local-stock, Unicorn and LLVM tests and the source-only staging reproduction.

Python is formatted with `black` and checked with `pyflakes`, both pinned in
`requirements-dev.txt`. Format Go with `gofmt`. Formatting the firmware builder
is safe to confirm: the build rejects any image that differs from the reference.

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/black firmware tools tests
.venv/bin/python -m pyflakes firmware tools tests
```

The Go tests use fake protocol responses, fake ioctl adapters, interrupt URB
cancellation, descriptor rejection, process locks, real signal subprocesses and
public API concurrency. None of them opens a real USB device.

## Safety rules for changes

The safety tests are fail-closed on purpose. A change must not add:

- retries after ambiguous I/O, or a hidden CLEAR after a failed exchange;
- profile or settings writes, or any persistent fallback;
- automatic permission changes, driver detachment of the whole audio device,
  USB resets, or broad device discovery;
- arbitrary packets on the runtime client, or maintenance packets on interface 4;
- flexibility in image addresses, record layout, or expected hashes.

Do not change expected image hashes to make a failing test pass. A firmware
change needs new bounded qualification, independent review and separately
authorized hardware validation. Earlier hardware results do not qualify a new
image. Editing `tools/katana_flash.py` changes its pinned hash and invalidates
evidence from any in-flight flash attempt; see
[docs/FLASHING.md](docs/FLASHING.md).

## Public-source hygiene

Use synthetic identifiers such as `TEST-SERIAL` in tests. Never commit personal
names, home paths, actual serials, consumed gate markers, private journals, vendor
firmware or raw USB captures. Only ignored paths may hold generated vendor
disassembly or images. Run `git diff --check` and review staged contents before
pushing. `tools/check_staging.py` proves that a source-only copy of the tree
builds the identical image without any external research checkout.
