#!/usr/bin/env python3
"""Exhaustive defined 0..8 glyph-count differential against executed stock."""

from events import Boundary
import random

from fixture_data import OUT, S, build
from probe import CPU, machine
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def run():
    candidate = machine()
    stock = machine()
    Boundary(candidate)
    Boundary(stock)
    stock.u.mem_write(build.BASE & 0x1FFFFFFF, S)
    for m in (candidate, stock):
        m.intercepts = set()
    cases = 0
    sample = []
    saved = [UC_MIPS_REG_SP, UC_MIPS_REG_FP, UC_MIPS_REG_GP] + [
        CPU[i] for i in range(16, 24)
    ]

    def check(text, n):
        nonlocal cases
        got = []
        for m in (candidate, stock):
            m.frames.clear()
            m.u.mem_write(0xD000, text + b"\xaa" * 16)
            before = [m.u.reg_read(r) for r in saved]
            m.call(0x9D036DF8, [0xA000D000, n])
            assert [m.u.reg_read(r) for r in saved] == before
            got.append(list(m.frames))
        assert got[0] == got[1], (text.hex(), n, got)
        cases += 1
        if text[:1] in (b"d", b"\xff", b"\x00"):
            sample.append(
                dict(text=text.hex(), count=n, segments=[x.hex() for x in got[0]])
            )

    check(b"", 0)
    for n in range(1, 9):
        for pos in range(n):
            for c in range(256):
                t = bytearray(b"Ab3*#-+ "[0:n])
                t[pos] = c
                check(bytes(t), n)
    rng = random.Random(1729)
    for _ in range(256):
        n = rng.randrange(9)
        check(bytes(rng.randrange(256) for _ in range(n)), n)
    result = dict(
        passed=True,
        cases=cases,
        all_byte_values_at_every_position_for_counts_1_to_8=True,
        zero_count=True,
        random_mixed_cases=256,
        callee_saved_preserved=True,
        samples=sample,
        limits=[
            "Counts above 8 exceed original segment-buffer capacity and are not qualified.",
            "Equivalence is output bytes and ABI, not identical volatile scratch registers or instruction timing.",
        ],
    )
    (OUT / "glyph-differential.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    run()
