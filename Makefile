PYTHON ?= python3
.PHONY: test firmware cpu-test build check
build:
	mkdir -p build
	go build -o build/katana-clock ./cmd/katana-clock

test:
	go test -race -count=1 ./...
	go vet ./...
	$(PYTHON) -m unittest discover -s tests -v

firmware:
	test -n "$(KATANA_STOCK_HEX)"
	$(PYTHON) firmware/build.py --stock-hex "$(KATANA_STOCK_HEX)"

cpu-test:
	$(PYTHON) tests/run_cpu.py

check: test build
