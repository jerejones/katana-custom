#!/usr/bin/env python3
"""Actual MIPS CPU tests. USB/display electrical boundaries are intercepted.
No stock verification module is imported: those have write-on-import behavior.
"""

import struct
from unicorn import (
    Uc,
    UC_ARCH_MIPS,
    UC_MODE_MIPS32,
    UC_MODE_LITTLE_ENDIAN,
    UC_HOOK_CODE,
)
from unicorn.mips_const import *
from fixture_data import B, S, build

STOP = 0x9D004000
GP = 0xA0008020
RX = 0x50A0
TEXT = 0xFFD8
SP = 0xA000FF00
ALLOW = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -+*#"
# Observe actual loads/stores through the code hook, not Unicorn's MIPS memory
# hooks: UC_HOOK_MEM_WRITE was reproduced to corrupt delay-slot execution even
# inside stock memcpy. This decoder uses real executed instruction operands.
MEMOPS = {}
for off in range(0, len(B) - 3, 4):
    w = struct.unpack_from("<I", B, off)[0]
    op = w >> 26
    if op in (32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43, 46, 48, 56):
        imm = w & 65535
        imm = imm - 65536 if imm & 32768 else imm
        MEMOPS[build.BASE + off] = (op, (w >> 21) & 31, (w >> 16) & 31, imm)
CPU_REG = [
    getattr(__import__("unicorn.mips_const", fromlist=["x"]), "UC_MIPS_REG_" + str(i))
    for i in range(32)
]
RESULT = []


def record(name, **extra):
    RESULT.append(dict(test=name, passed=True, **extra))


def p32(n):
    return struct.pack("<I", n)


def packet(op=1, text=b"HELLO"):
    assert len(text) <= 8
    return (
        b"\x5a\x7e\x0d" + bytes([op]) + b"RD\x01\x00" + text.ljust(8, b" ") + bytes(48)
    )


def expected(text):
    table = S[0x9D03F6A0 - build.BASE : 0x9D03F6A0 - build.BASE + 112]

    def index(c):
        if 65 <= c <= 90:
            return c - 65
        if 48 <= c <= 57:
            return c - 22
        return {32: 55, 0: 55, 45: 52, 43: 54, 42: 36, 35: 37}[c]

    return b"".join(table[index(c) * 2 : index(c) * 2 + 2] for c in text)


class Machine:
    """Stock or candidate image under Unicorn with the USB reply and display
    output boundaries intercepted. With intercept_display=False the stock
    bit-banged GPIO display code runs for real; frames are still recorded.
    """

    def __init__(self, stock=False, intercept_display=True):
        self.u = u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        u.ctl_set_cpu_model(UC_CPU_MIPS32_24KC)
        u.mem_map(0x1D000000, 0x40000)
        u.mem_write(0x1D004180, S if stock else B)
        u.mem_map(0, 0x10000)  # actual startup RAM extent, not fictitious 256 KiB heap
        u.mem_map(0x1F880000, 0x10000)  # USB peripheral register fixture only
        u.reg_write(UC_MIPS_REG_GP, GP)
        u.reg_write(UC_MIPS_REG_SP, SP)
        u.mem_write(0x1B8, p32(0xA000E000))
        u.mem_write(0x448, struct.pack("<H", 64))
        u.mem_write(0x50, p32(1))
        u.mem_write(0x84C, bytes((i * 37 + 11) % 256 for i in range(0xF00)))
        u.mem_write(0x1E1, b"\xff")  # dirty already set: endpoint must not flush
        u.mem_write(0x234, bytes(12))
        u.mem_write(0x89CE, b"\x01")  # stock source selector used by timeout
        self.frames = []
        self.replies = []
        self.visited = []
        self.writes = []
        self.reads = []
        self.intercepts = set()
        self.intercepted = []
        self.break_at = None

        def hook(uc, a, size, data):
            self.visited.append(a)
            if a == self.break_at:
                uc.emu_stop()
                return
            if a in self.intercepts or a == 0x9D02AC5C:
                self.intercepted.append(hex(a))
                uc.reg_write(UC_MIPS_REG_V0, 0)
                uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
                return
            assert a not in (
                0x9D011FA4,
                0x9D0123C8,
                0x9D011C68,
                0x9D02F42C,
                0x9D006AEC,
                0x9D02F9E0,
            ), hex(a)
            assert not 0x9D038FB8 <= a < 0x9D0396C8, hex(a)
            if a == 0x9D02B17C:
                ptr = uc.reg_read(UC_MIPS_REG_A1) & 0x1FFFFFFF
                n = uc.mem_read(ptr + 2, 1)[0] + 3
                self.replies.append(bytes(uc.mem_read(ptr, n)))
                uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
            elif a == 0x9D036C18:
                ptr = uc.reg_read(UC_MIPS_REG_A1) & 0x1FFFFFFF
                n = uc.reg_read(UC_MIPS_REG_A2)
                self.frames.append(bytes(uc.mem_read(ptr, n)))
                if intercept_display:
                    uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
            elif a == 0x9D036D34 and intercept_display:
                uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
            elif a == 0x9D037380 and intercept_display:
                uc.reg_write(UC_MIPS_REG_PC, 0x9D0373A4)
                return
            mem = MEMOPS.get(a)
            if mem:
                op, rs, rt, imm = mem
                address = (uc.reg_read(CPU_REG[rs]) + imm) & 0x1FFFFFFF
                width = 1 if op in (32, 36, 40) else 2 if op in (33, 37, 41) else 4
                if op in (40, 41, 42, 43, 46, 56):
                    self.writes.append((address, width, uc.reg_read(CPU_REG[rt])))
                else:
                    self.reads.append((address, width))

        u.hook_add(UC_HOOK_CODE, hook)

    def run(self, start, end=STOP):
        try:
            self.u.emu_start(start, end, count=2000000)
        except Exception:
            print(
                "CPU failure",
                hex(self.u.reg_read(UC_MIPS_REG_PC)),
                "recent",
                [hex(x) for x in self.visited[-16:]],
            )
            raise
        assert self.u.reg_read(UC_MIPS_REG_PC) == end, hex(
            self.u.reg_read(UC_MIPS_REG_PC)
        )

    def call(self, address, args=()):
        u = self.u
        u.reg_write(UC_MIPS_REG_RA, STOP)
        u.reg_write(UC_MIPS_REG_SP, SP)
        for r, x in zip(
            [UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3], args
        ):
            u.reg_write(r, x)
        self.run(address)
        assert u.reg_read(UC_MIPS_REG_SP) == SP
