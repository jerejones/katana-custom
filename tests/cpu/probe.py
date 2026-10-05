"""Real stock-span MIPS placement; explicitly stubbed peripherals, not whole-SoC proof."""

import struct
from placement import assemble
from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs
from fixture_data import B, S, build
from machine import CPU_REG, Machine, SP, STOP, p32, packet
from unicorn.mips_const import *  # UC_MIPS_REG_* constants

segments, labels, assembly_units = assemble(build)
code = b"".join(b for a, b in segments)
patches = segments + [
    (0x9D005030, build.jump(labels["initialize"]) + bytes(4)),
    (0x9D00DEA0, build.jump(labels["service"]) + bytes(4)),
    (0x9D036DF8, build.jump(labels["render_gate"]) + bytes(4)),
    (0x9D037344, build.jump(labels["segment_bridge"]) + bytes(4)),
    (0x9D0373B0, p32(0x27BD0040)),
    (0x9D00B5D8, build.jump(labels["event_hook"]) + bytes(4)),
]
image = bytearray(B)
rebuilt = bytearray(S)
for a, b in patches:
    rebuilt[a - build.BASE : a - build.BASE + len(b)] = b
assert rebuilt == image
CPU = CPU_REG
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
md.skipdata = True
loop = list(md.disasm(S[0x9D00D3E8 - build.BASE : 0x9D00E6C8 - build.BASE], 0x9D00D3E8))
stubs = {int(i.op_str, 16) for i in loop if i.mnemonic == "jal"} - {
    0x9D00CBAC,
    0x9D0374A4,
    0x9D026EA0,
    0x9D03E840,
    0x9D0375C4,
}
stubs.add(0x9D01C76C)


def state(m):
    return tuple(
        int.from_bytes(m.u.mem_read(a, n), "little")
        for a, n in [(0x240, 1), (0x241, 1), (0x50, 4)]
    )


def lease(m):
    return m.u.mem_read(0xFFE0, 1)[0]


def install(m):
    # Code is already installed in the real stock image; no extension map.
    m.u.mem_write(0xFF90, b" " * 8)
    return m


def machine(idle=0, latch=0):
    m = install(Machine())
    m.intercepts = stubs
    m.u.mem_map(0x1F800000, 0x10000)
    m.u.mem_write(0x89F0, b"\1")
    m.u.mem_write(0x241, bytes([idle]))
    m.u.mem_write(0x50, p32(latch))
    return m


def block(m, a, z):
    m.u.reg_write(UC_MIPS_REG_SP, SP - 128)
    m.u.reg_write(UC_MIPS_REG_FP, SP - 128)
    m.break_at = z
    m.u.emu_start(a, STOP, count=2000000)
    assert m.u.reg_read(UC_MIPS_REG_PC) == z, hex(m.u.reg_read(UC_MIPS_REG_PC))
    m.break_at = None


def turn(m, eligible=True):
    m.u.mem_write(0x496, bytes(2))
    m.u.mem_write(0x3F6, b"\1")
    m.u.mem_write((SP - 128 + 16) & 0x1FFFFFFF, p32(99 if eligible else 98))
    block(m, 0x9D00D3E8, 0x9D00D3CC)
    return (*state(m), lease(m))


def request(m, p=None, count=64, transport=1, ptr=0xA00050A0):
    from events import Boundary

    if not hasattr(m, "boundary"):
        m.boundary = Boundary(m)
    p = packet() if p is None else p
    m.u.mem_write(ptr & 0x1FFFFFFF, p)
    m.u.mem_write(0x448, struct.pack("<H", count))
    m.writes.clear()
    m.replies.clear()
    m.reads.clear()
    saved = [
        UC_MIPS_REG_S0,
        UC_MIPS_REG_S1,
        UC_MIPS_REG_S2,
        UC_MIPS_REG_S3,
        UC_MIPS_REG_S4,
        UC_MIPS_REG_S5,
        UC_MIPS_REG_S6,
        UC_MIPS_REG_S7,
        UC_MIPS_REG_FP,
        UC_MIPS_REG_GP,
    ]
    regs = [m.u.reg_read(r) for r in saved]
    persistent = bytes(m.u.mem_read(0x84C, 0xF00))
    dirty = bytes(m.u.mem_read(0x1E1, 1))
    m.call(0x9D02B79C, [transport, ptr])
    assert regs == [m.u.reg_read(r) for r in saved]
    assert persistent == bytes(m.u.mem_read(0x84C, 0xF00)) and dirty == bytes(
        m.u.mem_read(0x1E1, 1)
    )
    # Endpoint write set: private state, USB reply buffer, the display latch
    # word, and the idle countdown byte that a SET wake re-arms exactly as the
    # stock display-on paths do. Nothing else in stock RAM may be written.
    allowed = [
        (0xF000, 0xFF00),
        (0xFF20, 0xFFE8),
        (0xE000, 0xE040),
        (0x50, 0x54),
        (0x241, 0x242),
    ]
    gpio = {0x1F886134, 0x1F886138, 0x1F886334, 0x1F886338}
    assert all(
        any(lo <= a and a + n <= hi for lo, hi in allowed) or a in gpio and n == 4
        for a, n, v in m.writes
    ), m.writes
    return m.replies


def dis(a, b):
    blob = image
    base = build.BASE
    return "\n".join(
        f"{i.address:08x} {i.bytes.hex()} {i.mnemonic} {i.op_str}"
        for i in md.disasm(blob[a - base : b - base], a)
    )
