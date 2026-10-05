#!/usr/bin/env python3
"""Real populated placement, independent encodings, and bounded RAM audit."""

import hashlib
import json
import struct
from pathlib import Path

# fixture_data puts firmware/ and tools/ on sys.path; import it first.
from fixture_data import OUT, S, build, katana_flash, stock_path
import build as firmware_build  # firmware/build.py, the deterministic builder
from machine import SP
from probe import assembly_units, image, labels, machine, md, patches, segments
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def hx(x):
    return hex(x)


def sha(b):
    return hashlib.sha256(b).hexdigest()


ins = {i.address: i for i in md.disasm(S, build.BASE)}
replaced = [
    (0x9D02B868, 0x9D02BB94),
    (0x9D036E14, 0x9D037344),
    (0x9D00E35C, 0x9D00E3EC),
]


def containing(v):
    if (v >> 29) not in (0, 4, 5):
        return None
    return next(
        (
            (a, z)
            for a, z in replaced
            if (a & 0x1FFFFFFF) <= (v & 0x1FFFFFFF) < (z & 0x1FFFFFFF)
        ),
        None,
    )


def inside(pc):
    return any(a <= pc < z for a, z in replaced)


hexpaths = [stock_path]
pop = katana_flash.parse_hex(hexpaths[0].read_bytes())[1]
assert bytes(pop.get(build.PHYS + i, 255) for i in range(len(S))) == S
assert sha(S) == "a119076916e5f90c39681cb26d2776d8cf2ea16f406af599a207cc48a525a91f"
assert all((a & 0x1FFFFFFF) + i in pop for a, z in replaced for i in range(z - a))
direct = []
literal = []
construct = []
for pc, i in ins.items():
    if i.mnemonic.startswith(("b", "j")):
        try:
            target = int(i.op_str.split(",")[-1].strip(), 16)
        except ValueError:
            continue
        if containing(target) and not inside(pc):
            direct.append(
                dict(
                    pc=hx(pc),
                    target=hx(target),
                    instruction=i.mnemonic + " " + i.op_str,
                )
            )
# Byte-aligned pointer literals, including physical and uncached aliases.
for off in range(len(S) - 3):
    value = struct.unpack_from("<I", S, off)[0]
    if containing(value):
        literal.append(
            dict(
                pc=hx(build.BASE + off),
                target=hx(value),
                source_inside_replaced=inside(build.BASE + off),
                alignment=off % 4,
            )
        )
# Conservative 32-instruction LUI-to-immediate pairing, NOT constant propagation:
# intervening writes are deliberately not trusted to discard potential references.
for off in range(0, len(S) - 3, 4):
    w = struct.unpack_from("<I", S, off)[0]
    if w >> 26 != 15:
        continue
    reg = (w >> 16) & 31
    upper = (w & 65535) << 16
    for delta in range(4, min(132, len(S) - off - 3), 4):
        v = struct.unpack_from("<I", S, off + delta)[0]
        if v >> 26 not in (9, 13) or ((v >> 21) & 31) != reg:
            continue
        low = v & 65535
        target = (
            upper + (low - 65536 if v >> 26 == 9 and low & 32768 else low)
        ) & 0xFFFFFFFF
        if containing(target):
            construct.append(
                dict(
                    pc=hx(build.BASE + off),
                    low_pc=hx(build.BASE + off + delta),
                    target=hx(target),
                    source_inside_replaced=inside(build.BASE + off),
                    window_instructions=delta // 4,
                )
            )
assert not [
    r for r in direct if int(r["target"], 16) not in (0x9D02B868, 0x9D00E35C)
], direct
# Unaligned values cannot be normal MIPS32 entry pointers; retain them in
# evidence rather than silently treating every byte coincidence as a pointer.
assert not [
    r
    for r in literal
    if not r["source_inside_replaced"] and int(r["target"], 16) % 4 == 0
], literal
for row in construct:
    pc = int(row["pc"], 16)
    low_pc = int(row["low_pc"], 16)
    reg = (struct.unpack_from("<I", S, pc - build.BASE)[0] >> 16) & 31
    middle = [
        (a, struct.unpack_from("<I", S, a - build.BASE)[0])
        for a in range(pc + 4, low_pc, 4)
    ]
    branch_free = all(not ins[a].mnemonic.startswith(("b", "j")) for a, _ in middle)
    kills = [hex(a) for a, w in middle if w >> 26 == 15 and ((w >> 16) & 31) == reg]
    row["intervening_lui_overwrites"] = kills if branch_free else []
assert not [
    r
    for r in construct
    if not r["source_inside_replaced"] and not r["intervening_lui_overwrites"]
], construct
# Keep all seven original callsites and their argument setup visible for review.
callers = [
    a for a, i in ins.items() if i.mnemonic in ("jal", "j") and i.op_str == "0x9d036df8"
]
assert len(callers) == 7
assert all(
    struct.unpack_from("<I", S, a - build.BASE - 4)[0] == 0x24050008 for a in callers
), "All known stock callers must pass count 8"
(OUT / "glyph-callers.asm").write_text(
    "\n\n".join(
        "\n".join(
            f"{i.address:08x} {i.mnemonic} {i.op_str}"
            for i in md.disasm(S[a - build.BASE - 48 : a - build.BASE + 32], a - 48)
        )
        for a in callers
    )
    + "\n"
)
# Independent LLVM validation of each placed compilation unit, using the same
# crosscheck the builder runs. It raises on any encoding mismatch.
encoding = []
for index, ((a, blob), (lines, labels_unit)) in enumerate(
    zip(segments, assembly_units)
):
    llvm = firmware_build.llvm_unit(a, blob, lines, labels_unit, OUT, index)
    assert llvm == blob
    encoding.append(
        dict(
            start=hx(a),
            end=hx(a + len(blob)),
            bytes=len(blob),
            sha256=sha(blob),
            llvm_equal=True,
        )
    )
# Actual initializers, not guessed free SRAM, define the static floor.
crt = []
p = 0x9D0396C8 - build.BASE
while True:
    dst = struct.unpack_from("<I", S, p)[0]
    p += 4
    if not dst:
        break
    size, kind = struct.unpack_from("<II", S, p)
    p += 8
    crt.append(dict(start=hx(dst), end=hx(dst + size), bytes=size, copy=bool(kind)))
    assert not (dst < 0xA000FFF8 and dst + size > SP)
    if kind:
        p += size
    p = (p + 3) & ~3
m = machine()
m.u.mem_write(0xFF00, b"\x55" * 248)
m.run(0x9D005030, 0x9D005038)
assert (
    m.u.reg_read(UC_MIPS_REG_SP) == SP
    and bytes(m.u.mem_read(0xFF00, 32)) == b"\x55" * 32
)
want = bytearray(200)
want[112:120] = b" " * 8
assert bytes(m.u.mem_read(0xFF20, 200)) == want
m.run(0x9D005070, 0x9D005118)
assert bytes(m.u.mem_read(0xFF20, 200)) == want
# Enumerate stack setup patterns and IRQ entry frames without claiming a bound.
stack_ops = [
    dict(pc=hx(a), instruction=i.mnemonic + " " + i.op_str)
    for a, i in ins.items()
    if "$sp" in i.op_str
    and (i.mnemonic in ("lui", "move", "addiu", "addu", "subu", "lw", "ori"))
    and i.op_str.startswith("$sp,")
]
roots = sorted(
    set(
        int(ins[a].op_str, 16)
        for a in range(0x9D004200, 0x9D005000, 32)
        if ins[a].mnemonic == "j"
    )
)
irq_entries = []
for root in roots:
    frame = []
    for a in range(root, root + 160, 4):
        if a in ins and ("$sp" in ins[a].op_str or ins[a].mnemonic == ".byte"):
            frame.append(
                dict(
                    pc=hx(a),
                    bytes=ins[a].bytes.hex(),
                    instruction=ins[a].mnemonic + " " + ins[a].op_str,
                )
            )
    irq_entries.append(dict(root=hx(root), entry_stack_instructions=frame))
(OUT / "exception-startup.asm").write_text(
    "\n\n".join(
        "\n".join(
            f"{i.address:08x} {i.bytes.hex()} {i.mnemonic} {i.op_str}"
            for i in md.disasm(S[a - build.BASE : z - build.BASE], a)
        )
        for a, z in [(0x9D005030, 0x9D0051F4), (0x9D03F0F4, 0x9D03F330)]
        + [(r, r + 160) for r in roots]
    )
    + "\n"
)
ram = dict(
    carve_start=hx(SP),
    carve_end="0xa000fff8",
    carve_bytes=0xA000FFF8 - SP,
    additional_vs_prior_bytes=0xA000FFB8 - SP,
    additional_vs_installed_single_writer_bytes=0,
    logical_state_bytes=152,
    argument_home_bytes=32,
    other_reserved_bytes=64,
    crt=crt,
    highest_static_end=hx(max(int(x["end"], 16) for x in crt)),
    distance_static_to_stack_top=SP - max(int(x["end"], 16) for x in crt),
    startup_and_crt_preserve_state=True,
    glyph_frame_bytes=64,
    writer_frame_bytes=80,
    stack_ops=stack_ops,
    vector_entries=irq_entries,
    whole_device_stack_bound_proven=False,
    limitations=[
        "Static gap is NOT available stack headroom: deepest foreground calls, nested IRQs, exceptions and dynamic accesses have not been bounded.",
        "RDPGPR/WRPGPR shadow-register operations are not completely modeled by Unicorn fixtures.",
        "No hardware high-water measurement performed.",
    ],
)
# Identity and exact allowed-byte audit, retaining original format and unused gaps.
spans = []
allowed = set()
for a, b in patches:
    assert all((a & 0x1FFFFFFF) + i in pop for i in range(len(b)))
    offsets = set(range(a - build.BASE, a - build.BASE + len(b)))
    assert not (offsets & allowed)
    allowed |= offsets
    spans.append(
        dict(
            va=hx(a),
            physical=hx(a & 0x1FFFFFFF),
            end=hx(a + len(b)),
            bytes=len(b),
            original=S[a - build.BASE : a - build.BASE + len(b)].hex(),
            replacement=b.hex(),
        )
    )
changed = [i for i, (x, y) in enumerate(zip(S, image)) if x != y]
assert set(changed) <= allowed
inverse = bytearray(image)
for a, b in patches:
    inverse[a - build.BASE : a - build.BASE + len(b)] = S[
        a - build.BASE : a - build.BASE + len(b)
    ]
assert inverse == S
# If rerunning after packaging, verify existing artifacts rather than silently
# accepting unrelated firmware. Packaging itself remains in package.py.
for p in OUT.glob("*.bin"):
    assert p.name == "candidate.bin" and p.read_bytes() == bytes(image)
for p in OUT.glob("*.hex"):
    assert p.name == "candidate.hex"
    emitted = katana_flash.parse_hex(p.read_bytes())[1]
    assert emitted.keys() == pop.keys()
    assert all(
        emitted[k]
        == (image[k - build.PHYS] if build.PHYS <= k < build.PHYS + len(image) else v)
        for k, v in pop.items()
    )
before = json.loads((OUT / "preserved-before.json").read_text())
modified = [p for p, h in before.items() if sha(Path(p).read_bytes()) != h]
assert not modified
report = dict(
    real_populated_placement=True,
    firmware_emitted=False,
    deployment_qualified=False,
    stock_sha256=sha(S),
    stock_hex_sha256=sha(hexpaths[0].read_bytes()),
    memory_only_image_sha256=sha(image),
    image_bytes=len(image),
    patch_span_bytes=len(allowed),
    actually_changed_bytes=len(changed),
    encoding=encoding,
    spans=spans,
    labels={k: hx(v) for k, v in labels.items()},
    replaced_regions=[dict(start=hx(a), end=hx(z), bytes=z - a) for a, z in replaced],
    references=dict(
        external_direct=direct,
        literals=literal,
        conservative_lui_pairs=construct,
        scanned_bytes=len(S),
        literal_alignments="all byte offsets",
        aliases="physical, cached, uncached",
        lui_window_instructions=32,
        glyph_callers=[hx(a) for a in callers],
        arbitrary_computed_indirect_entry_exclusion=False,
    ),
    ram=ram,
    inputs_verified_unchanged=len(before),
    placement_blockers=[],
    residual_uncertainties=[
        "Whole-device worst-case stack/RAM safety is not proven; not treated as a new packaging gate.",
        "Computed indirect entries into replaced stock regions are not exhaustively excluded; bounded reference audit only.",
    ],
    limits=[
        "No device or hardware timing qualification.",
        "Compression tested for stock-defined glyph counts 0..8; invalid overlength direct calls not qualified.",
    ],
)
(OUT / "fit-audit.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {
            k: v
            for k, v in report.items()
            if k not in ("ram", "spans", "labels", "references")
        },
        indent=2,
    )
)
