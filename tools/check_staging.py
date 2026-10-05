#!/usr/bin/env python3
"""Build a fresh source-only staging copy. Offline; stock remains external.
Scratch goes beneath TMPDIR when set, otherwise ~/.cache/katana-custom.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {".git", ".venv", "build", "vendor", "work", "state", "__pycache__"}
SUFFIXES = {".go", ".py", ".S", ".md", ".json", ".txt"}


def source_files(root=ROOT):
    for p in sorted(root.rglob("*")):
        relative = p.relative_to(root)
        if any(x in EXCLUDE for x in relative.parts) or not p.is_file():
            continue
        if p.suffix in SUFFIXES or p.name in (
            "go.mod",
            "go.sum",
            "Makefile",
            ".gitignore",
        ):
            yield p


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    stock = os.environ.get("KATANA_STOCK_HEX")
    if not stock or not Path(stock).is_file():
        raise RuntimeError("Set KATANA_STOCK_HEX to external official MCU_RCDATA")
    stock = str(Path(stock).resolve())
    scratch = Path(os.environ.get("TMPDIR", str(Path.home() / ".cache/katana-custom")))
    scratch.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="katana-source-", dir=scratch))
    count = 0
    for p in source_files():
        dest = stage / p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        count += 1
    env = {
        **os.environ,
        "KATANA_STOCK_HEX": stock,
        "KATANA_BUILD_DIR": str(stage / "build/firmware"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    commands = [
        ["go", "test", "-race", "-count=1", "./..."],
        ["go", "vet", "./..."],
        ["go", "build", "-o", "build/katana-clock", "./cmd/katana-clock"],
        [sys.executable, "firmware/build.py", "--stock-hex", stock],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    ]
    if args.cpu:
        commands.append([sys.executable, "tests/run_cpu.py"])
    runs = []
    for index, command in enumerate(commands):
        p = subprocess.run(
            command, cwd=stage, env=env, capture_output=True, text=True, timeout=900
        )
        (stage / f"check-{index}.log").write_text(p.stdout + p.stderr)
        runs.append(dict(command=command, exit_code=p.returncode))
        print("PASS" if p.returncode == 0 else "FAIL", command[1:3], flush=True)
        if p.returncode:
            print(p.stdout + p.stderr)
            raise RuntimeError("Staging failed: " + str(stage))
    matches = {}
    for name in ("candidate.bin", "candidate.hex", "manifest.json"):
        a = (ROOT / "build/firmware" / name).read_bytes()
        b = (stage / "build/firmware" / name).read_bytes()
        if a != b:
            raise RuntimeError("Staging differs: " + name)
        matches[name] = hashlib.sha256(b).hexdigest()
    out = ROOT / "build/staging-verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            dict(
                all_passed=True,
                hardware_accessed=False,
                staging_directory=str(stage),
                source_files=count,
                commands=runs,
                identical_artifact_sha256=matches,
            ),
            indent=2,
        )
        + "\n"
    )
    print("Identical source-only staging build:", stage)


if __name__ == "__main__":
    main()
