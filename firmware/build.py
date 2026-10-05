#!/usr/bin/env python3
"""Offline source build. Vendor input is supplied locally, never downloaded/executed.
Every placed unit is assembled by LLVM and compared to the exact-subset encoder.
The emitted image must match the pinned reference image byte for byte.
"""

import argparse
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import katana_flash as hexfile
import encoder
from placement import assemble

BIN_SHA = "e653aff4b0bf75a92729b0ff60703f5d77f65b1db82d965a0ff0c792b0ed4a69"
HEX_SHA = "88f9fb6430f183b85aa23f745b2bc39f7229a4ba4166479de7f45a17d26beaa2"
sha = hexfile.sha
need = hexfile.need


def llvm_unit(base, blob, lines, labels, output, index):
    source = [".set noreorder", ".set noat", ".text"]
    aliases = {
        ".word 0x00431024": "and v0,v0,v1",
        ".word 0xa5420000": "sh v0,0(t2)",
        ".word 0x00431023": "subu v0,v0,v1",
        ".word 0xa5020002": "sh v0,2(t0)",
        ".word 0x41796000": "di t9",
        ".word 0x40996000": "mtc0 t9,$12",
        ".word 0x000000c0": "ehb",
    }
    for pc, line in lines:
        source.extend(k + ":" for k, v in labels.items() if v == pc)
        if line.startswith(("j ", "jal ")):
            op, dst = line.split()
            dest = labels[dst] if dst in labels else int(dst, 0)
            source.append(op + " " + hex(dest))
            continue
        if line.split()[0] in ("beq", "bne", "b"):
            op, rest = line.split(None, 1)
            args = rest.split(",")
            dst = args[-1]
            if dst.startswith("0x"):
                args[-1] = str(int(dst, 0) - pc - 4)
                line = op + " " + ",".join(args)
        line = (
            aliases.get(line, line)
            .replace("HI_TABLE", str(labels["table"] >> 16))
            .replace("LO_TABLE", str(labels["table"] & 65535))
        )
        if line.startswith("move "):
            x, y = line[5:].split(",")
            line = f"addu {x},{y},zero"
        source.append(re.sub(r"\b(" + "|".join(encoder.REG) + r")\b", r"$\1", line))
    source.extend(k + ":" for k, v in labels.items() if v == base + len(blob))
    asm = output / f"unit-{index}.s"
    obj = output / f"unit-{index}.o"
    asm.write_text("\n".join(source) + "\n")
    subprocess.run(
        [
            "llvm-mc",
            "--triple=mipsel",
            "--mcpu=mips32r2",
            "--filetype=obj",
            str(asm),
            "-o",
            str(obj),
        ],
        check=True,
    )
    elf = obj.read_bytes()
    shoff = struct.unpack_from("<I", elf, 32)[0]
    size, num, sidx = struct.unpack_from("<HHH", elf, 46)
    sections = [struct.unpack_from("<10I", elf, shoff + i * size) for i in range(num)]
    st = elf[sections[sidx][4] : sections[sidx][4] + sections[sidx][5]]
    sec = [s for s in sections if st[s[0] :].split(b"\0", 1)[0] == b".text"]
    need(len(sec) == 1, "LLVM .text missing")
    llvm = elf[sec[0][4] : sec[0][4] + sec[0][5]]
    need(
        llvm[: len(blob)] == blob and not any(llvm[len(blob) :]),
        "LLVM encoding mismatch",
    )
    # Local branches must be resolved; this pipeline does not run a linker.
    need(
        not any(s[1] in (4, 9) and s[5] for s in sections),
        "Unexpected assembler relocations",
    )
    return llvm[: len(blob)]


def package(raw, mapping):
    """Keep original record headers/order/line endings/non-data bytes exactly."""
    records = []
    high = 0
    for line in raw.splitlines(keepends=True):
        text = line.rstrip(b"\r\n")
        ending = line[len(text) :]
        r = bytearray.fromhex(text[1:].decode())
        if r[3] == 4:
            high = int.from_bytes(r[4:6], "big") << 16
        if r[3] == 0:
            address = high + int.from_bytes(r[1:3], "big")
            data = bytes(mapping[address + i] for i in range(r[0]))
            if data != r[4:-1]:
                r[4:-1] = data
                r[-1] = (-sum(r[:-1])) & 255
                line = b":" + r.hex().upper().encode() + ending
        records.append(line)
    return b"".join(records)


def build(stock_hex, output):
    need(
        sys.flags.optimize == 0,
        "Do not run with Python -O: source placement uses assertions",
    )
    raw = stock_hex.read_bytes()
    records, pop, stock = hexfile.parse_hex(raw)
    need(
        sha(stock) == hexfile.STOCK_SHA,
        "Wrong stock MCU image; only original 2.0.171220.130 supported",
    )
    output.mkdir(parents=True, exist_ok=True)
    segments, labels, units = assemble(encoder)
    segments = [
        (a, llvm_unit(a, b, lines, ll, output, i))
        for i, ((a, b), (lines, ll)) in enumerate(zip(segments, units))
    ]
    patches = segments + [
        (0x9D005030, encoder.jump(labels["initialize"]) + bytes(4)),
        (0x9D00DEA0, encoder.jump(labels["service"]) + bytes(4)),
        (0x9D036DF8, encoder.jump(labels["render_gate"]) + bytes(4)),
        (0x9D037344, encoder.jump(labels["segment_bridge"]) + bytes(4)),
        (0x9D0373B0, encoder.word(0x27BD0040)),
        (0x9D00B5D8, encoder.jump(labels["event_hook"]) + bytes(4)),
    ]
    image = bytearray(stock)
    desired = dict(pop)
    allowed = set()
    spans = []
    for a, b in patches:
        offset = a - encoder.BASE
        keys = {(a & 0x1FFFFFFF) + i for i in range(len(b))}
        need(
            keys <= pop.keys() and not keys & allowed,
            "Patch outside populated stock or overlapping",
        )
        allowed.update(keys)
        image[offset : offset + len(b)] = b
        for i, v in enumerate(b):
            desired[(a & 0x1FFFFFFF) + i] = v
        spans.append(dict(address=hex(a), bytes=len(b), sha256=sha(b)))
    need(
        sha(image) == BIN_SHA,
        "Image differs from the pinned reference candidate: DO NOT FLASH",
    )
    emitted = package(raw, desired)
    need(
        sha(emitted) == HEX_SHA,
        "HEX source formatting differs from reference; exact official resource required",
    )
    rr, mm, bb = hexfile.parse_hex(emitted)
    need(
        mm == desired and bb == image and mm.keys() == pop.keys(),
        "Sparse round-trip mismatch",
    )
    need(
        len(rr) == len(records) == 15326
        and all(a[:4] == b[:4] for a, b in zip(records, rr)),
        "Record layout mismatch",
    )
    need(
        all(desired[k] == v for k, v in pop.items() if k not in allowed),
        "Unapproved byte change",
    )
    inverse = bytearray(image)
    for a, b in patches:
        inverse[a - encoder.BASE : a - encoder.BASE + len(b)] = stock[
            a - encoder.BASE : a - encoder.BASE + len(b)
        ]
    need(inverse == stock, "Inverse reconstruction mismatch")
    for name, data in [("candidate.bin", image), ("candidate.hex", emitted)]:
        p = output / name
        if p.exists():
            need(p.read_bytes() == data, "Refusing to overwrite differing artifact")
        else:
            p.write_bytes(data)
    root = Path(__file__).resolve().parent
    manifest = dict(
        schema=1,
        experimental=True,
        hardware_accessed=False,
        stock_bin_sha256=sha(stock),
        stock_hex_sha256=sha(raw),
        bin_sha256=sha(image),
        hex_sha256=sha(emitted),
        records=len(records),
        populated_bytes=len(pop),
        image_bytes=len(image),
        patch_covered_bytes=len(allowed),
        spans=spans,
        llvm_crosscheck=True,
        inverse_stock_reconstruction=True,
        exact_sparse_coverage=True,
        stock_record_layout_preserved=True,
        source_sha256={
            str(p.relative_to(root.parent)): sha(p.read_bytes())
            for p in sorted(
                list(root.glob("*.py"))
                + list(root.glob("*.S"))
                + [root.parent / "tools/katana_flash.py"]
            )
        },
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stock-hex", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("build/firmware"))
    a = p.parse_args()
    print(json.dumps(build(a.stock_hex, a.output), indent=2))
