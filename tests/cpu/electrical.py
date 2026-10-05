#!/usr/bin/env python3
"""Execute stock bit-banged display I/O; Count is a synthetic instruction clock.
This measures interrupt admission, NOT wall-clock audio deadlines.
"""

from events import Boundary
from fixture_data import OUT
from machine import Machine, expected, p32, packet
from probe import install, lease, segments
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def io_machine():
    # Real display GPIO code executes; only USB replies are intercepted.
    m = install(Machine(intercept_display=False))
    m.u.mem_map(0x1F800000, 0x10000)
    m.intercepts = set()
    m.u.mem_write(0x89F0, b"\1")
    m.u.mem_write(0x50, p32(0))
    return m


class ClockBoundary(Boundary):
    def __init__(self, m):
        self.samples = []
        super().__init__(m)

    def hook(self, u, a, size, data):
        self.cp[9, 0] = (self.cp.get((9, 0), 0) + 1) & 0xFFFFFFFF
        # Unicorn executes native MFC0 in JR's delay slot after the code-hook
        # register substitution, overwriting V0. Model only the two Count helpers
        # at their entry; GPIO/bit loops and all display control remain real.
        if a == 0x9D03FB4C:
            u.reg_write(UC_MIPS_REG_V0, self.cp[9, 0])
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
            return
        if a == 0x9D03FB54:
            self.cp[9, 0] = u.reg_read(UC_MIPS_REG_A0)
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
            return
        super().hook(u, a, size, data)
        # Relocated ownership machinery is not stock GPIO code. GPIO stores
        # themselves remain independently checked by attach_stats (all addresses).
        if 0x9D036AF8 <= a < 0x9D0373BC and not any(
            lo <= a < lo + len(blob) for lo, blob in segments[2:]
        ):
            self.samples.append((a, self.cp[12, 0]))


def electrical():
    m = io_machine()
    b = ClockBoundary(m)
    # Snapshot the complete request to include custom grant and renderer gate.
    u = m.u
    u.mem_write(0x50A0, packet())
    persistent = bytes(u.mem_read(0x84C, 0xF00))
    dirty = bytes(u.mem_read(0x1E1, 1))
    m.call(0x9D02B79C, [1, 0xA00050A0])
    assert (
        bytes(u.mem_read(0x84C, 0xF00)) == persistent
        and bytes(u.mem_read(0x1E1, 1)) == dirty
    )
    allowed = [(0xF000, 0xFF00), (0xE000, 0xE040), (0xFF20, 0xFFE8), (0x50, 0x54)]
    gpio = {0x1F886134, 0x1F886138, 0x1F886334, 0x1F886338}
    assert all(
        any(lo <= a and a + n <= hi for lo, hi in allowed) or (a in gpio and n == 4)
        for a, n, v in m.writes
    )
    assert m.replies[0][7] == 0
    assert m.frames == [expected(b"HELLO   ")] and lease(m) == 3
    assert any(a == 0x9D036B68 for a, s in b.samples)
    assert any(a == 0x9D036BA4 for a, s in b.samples)
    # Each sampled instruction has IE=1, EXL=ERL=0, IPL=0: any maskable
    # priority 1..7 would be admitted. This is not execution of an audio ISR.
    blocked = [
        (hex(a), hex(s))
        for a, s in b.samples
        if not (s & 1) or s & 6 or ((s >> 10) & 7)
    ]
    assert not blocked
    return dict(
        completed=True,
        display_instructions=len(b.samples),
        blocked_admission_samples=blocked,
        priorities_admissible=list(range(1, 8)),
        cp_status_writes=[r for r in b.log if r[1] != 9],
        clock_model="one Count tick per executed instruction, not physical cycles",
        audio_deadline_qualified=False,
        frames=[f.hex() for f in m.frames],
        intercepts=m.intercepted,
    )


if __name__ == "__main__":
    r = electrical()
    (OUT / "electrical.json").write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps(r, indent=2))
