#!/usr/bin/env python3
"""Offline CPU qualification; requires local stock and an exact source-built image."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = (
    Path(os.environ.get("KATANA_BUILD_DIR", str(ROOT / "build/firmware"))) / "cpu-tests"
)
SCRIPTS = (
    "test_provenance.py",
    "test_provenance_extended.py",
    "test_single_writer.py",
    "extra.py",
    "glyph_differential.py",
    "test_latched.py",
    "audit_status.py",
    "prove_entry.py",
    "fit_audit.py",
)


def main():
    if sys.flags.optimize:
        raise RuntimeError("CPU tests require assertions; do not use -O")
    if not os.environ.get("KATANA_STOCK_HEX"):
        raise RuntimeError("Set KATANA_STOCK_HEX to local official MCU_RCDATA")
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = (
        list((ROOT / "tests/cpu").glob("*.py"))
        + list((ROOT / "firmware").glob("*.S"))
        + [Path(os.environ["KATANA_STOCK_HEX"])]
    )
    (OUT / "preserved-before.json").write_text(
        json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    )
    runs = []
    for name in SCRIPTS:
        p = subprocess.run(
            [sys.executable, str(ROOT / "tests/cpu" / name)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=600,
            cwd=ROOT,
        )
        (OUT / (name + ".log")).write_text(p.stdout + p.stderr)
        runs.append(dict(script=name, exit_code=p.returncode))
        print(name, "PASS" if p.returncode == 0 else "FAIL", flush=True)
        if p.returncode:
            print(p.stdout + p.stderr)
            return p.returncode
    read = lambda name: json.loads((OUT / name).read_text())
    core, extra, glyph = (
        read("results.json"),
        read("extra.json"),
        read("glyph-differential.json"),
    )
    count = (
        core["enumerated_cases"]
        + 3
        + len(extra["stock_differential"])
        + len(extra["pending_priority"])
        + extra["protocol"]["cases"]
        + glyph["cases"]
    )
    assert count == 11880, count
    result = dict(
        hardware_accessed=False,
        all_passed=True,
        inherited_cases=count,
        runs=runs,
        latched=read("latched-results.json"),
        idle=read("provenance-targeted.json"),
        idle_extended=read("provenance-extended.json"),
        source_sha256={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "tests/cpu").glob("*.py"))
        },
        limitations=[
            "Not whole-SoC proof",
            "Synthetic instruction-count clock, not audio deadlines",
            "Peripheral/task intercepts and controlled fixtures",
            "No whole-device worst-case stack bound",
        ],
    )
    (OUT / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "CPU qualification PASS; inherited cases:", count, "; v2/idle suites additional"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
