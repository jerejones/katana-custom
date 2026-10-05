"""Locally supplied, hash-pinned stock and built image. Never opens USB."""

import os, sys, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "firmware"), str(ROOT / "tools")]
import encoder as build
import katana_flash

stock_path = Path(os.environ["KATANA_STOCK_HEX"])
_, _, S = katana_flash.parse_hex(stock_path.read_bytes())
assert hashlib.sha256(S).hexdigest() == build.STOCK_SHA
BUILD_DIR = Path(os.environ.get("KATANA_BUILD_DIR", str(ROOT / "build/firmware")))
B = (BUILD_DIR / "candidate.bin").read_bytes()
assert (
    hashlib.sha256(B).hexdigest()
    == "e653aff4b0bf75a92729b0ff60703f5d77f65b1db82d965a0ff0c792b0ed4a69"
)
OUT = BUILD_DIR / "cpu-tests"
OUT.mkdir(parents=True, exist_ok=True)
