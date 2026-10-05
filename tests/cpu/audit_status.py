#!/usr/bin/env python3
"""Placed-code Status restoration and reset-state checks."""

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE

from events import Boundary
from machine import SP
from test_latched import accepted, setup
from test_single_writer import save
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def run():
    rows = []
    for prior in (0, 1, 0x401, 0x10000001, 0x10000400):
        m = setup()
        b = Boundary(m)
        b.cp[12, 0] = prior
        observed = []
        writes = []

        def codehook(u, a, size, data):
            if a in (0x9D036D34, 0x9D036C18):
                observed.append(b.cp[12, 0])
                assert b.cp[12, 0] == prior, (hex(a), hex(b.cp[12, 0]), hex(prior))

        def memhook(u, access, a, size, value, data):
            writes.append(a)
            assert not 0x1D000000 <= (a & 0x1FFFFFFF) < 0x1D080000

        m.u.hook_add(UC_HOOK_CODE, codehook)
        m.u.hook_add(UC_HOOK_MEM_WRITE, memhook)
        accepted(m)
        assert b.cp[12, 0] == prior
        accepted(m, 2)
        assert b.cp[12, 0] == prior
        assert observed
        rows.append(
            dict(
                prior_status=hex(prior),
                io_calls=len(observed),
                restored=True,
                flash_writes=0,
            )
        )
    m = setup()
    accepted(m)
    m.run(0x9D005030, 0x9D005038)
    assert m.u.reg_read(UC_MIPS_REG_SP) == SP and bytes(
        m.u.mem_read(0xFF98, 16)
    ) == bytes(16)
    m.run(0x9D005070, 0x9D005118)
    assert bytes(m.u.mem_read(0xFF98, 16)) == bytes(16)
    result = dict(
        status=rows,
        actual_initializer_and_crt_reset_background=True,
        new_stack_carve_bytes=0,
    )
    save("placed-extra-audit.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    run()
