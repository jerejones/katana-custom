#!/usr/bin/env python3
"""Deterministic offline firmware patch; no device or updater execution.
Tiny exact-subset MIPS32LE encoder v1 (explicit instruction+delay slots).
"""

from pathlib import Path
import hashlib, re, struct

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
BASE = 0x9D004180
PHYS = BASE & 0x1FFFFFFF
START, END = 0x9D02B868, 0x9D02BB94
STOCK_SHA = "a119076916e5f90c39681cb26d2776d8cf2ea16f406af599a207cc48a525a91f"

REG = {
    n: i
    for i, n in enumerate(
        "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split()
    )
}


def sha(x):
    return hashlib.sha256(x).hexdigest()


def word(x):
    return struct.pack("<I", x & 0xFFFFFFFF)


def jump(addr, link=False):
    assert addr & 3 == 0 and addr >> 28 == 9
    return word((0x0C000000 if link else 0x08000000) | ((addr >> 2) & 0x03FFFFFF))


def assemble(text, sparse):
    lines = []
    labels = {}
    pc = START
    for raw in text.replace(
        "SPARSE_TABLE", "\n".join(".word " + str(x) for x in sparse + [0])
    ).splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        if line.endswith(":"):
            labels[line[:-1]] = pc
            continue
        if line == ".align 4":
            assert pc % 4 == 0
            continue
        lines.append((pc, line))
        pc += 4
    values = {"HI_TABLE": labels["table"] >> 16, "LO_TABLE": labels["table"] & 65535}

    def val(x):
        return values[x] if x in values else labels[x] if x in labels else int(x, 0)

    def r(x):
        return REG[x]

    out = bytearray()
    for pc, line in lines:
        op, *tail = line.split(None, 1)
        a = re.split(r"[,\s]+", tail[0]) if tail else []
        if op == ".word":
            w = val(a[0])
        elif op == "nop":
            w = 0
        elif op == "move":
            w = r(a[1]) << 21 | r(a[0]) << 11 | 0x21
        elif op in ("j", "jal"):
            out += jump(val(a[0]), op == "jal")
            continue
        elif op == "jr":
            w = r(a[0]) << 21 | 8
        elif op in ("addu", "or", "srlv"):
            d, s, t = map(r, a)
            if op == "srlv":
                s, t = t, s
            w = s << 21 | t << 16 | d << 11 | {"addu": 0x21, "or": 0x25, "srlv": 6}[op]
        elif op in ("srl", "sll"):
            w = (
                r(a[1]) << 16
                | r(a[0]) << 11
                | val(a[2]) << 6
                | ({"srl": 2, "sll": 0}[op])
            )
        elif op in ("beq", "bne", "b"):
            if op == "b":
                a = ["zero", "zero", a[0]]
            off = (val(a[2]) - pc - 4) // 4
            assert -32768 <= off <= 32767
            w = (
                (5 if op == "bne" else 4) << 26
                | r(a[0]) << 21
                | r(a[1]) << 16
                | (off & 65535)
            )
        elif op == "lui":
            w = 15 << 26 | r(a[0]) << 16 | val(a[1])
        elif op in ("addiu", "ori", "andi", "sltiu"):
            imm = val(a[2])
            assert -32768 <= imm <= 65535
            w = (
                {"addiu": 9, "ori": 13, "andi": 12, "sltiu": 11}[op] << 26
                | r(a[1]) << 21
                | r(a[0]) << 16
                | (imm & 65535)
            )
        elif op in ("lw", "sw", "lbu", "lhu", "sb"):
            match = re.fullmatch(r"(-?(?:0x[0-9a-f]+|\d+))\((\w+)\)", a[1])
            assert match, a
            imm = int(match[1], 0)
            assert -32768 <= imm <= 32767
            w = (
                {"lw": 35, "sw": 43, "lbu": 36, "lhu": 37, "sb": 40}[op] << 26
                | r(match[2]) << 21
                | r(a[0]) << 16
                | (imm & 65535)
            )
        else:
            raise ValueError(line)
        out += word(w)
    assert len(out) <= END - START, (len(out), END - START)
    return bytes(out), labels, lines
