#!/usr/bin/env python3
"""Bounded idle-path differential, register/IRQ checks, and stock transitions."""

from itertools import product

from fixture_data import S, build
from machine import expected, p32
from probe import CPU, block, labels, state
from test_latched import (
    accepted,
    clear_during_stock,
    generation_races,
    latched_gpio_irq,
    service_only,
    setup,
    stock,
    volume_actual,
)
from test_single_writer import save
from unicorn import UC_HOOK_CODE
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def idle(m):
    block(m, 0x9D00E35C, 0x9D00E3EC)


def differential():
    rows = 0
    changed = 0
    cases = list(
        product((0, 1, 2, 255), (0, 1, 2), (0, 1, 2, 3), (0, 1), (0, 1), (0, 1))
    )
    cases += [(i, 0, 1, 0, 0, 1) for i in range(256)]
    for countdown, mode, power, flag, inhibit, latch in cases:
        states = []
        suppress = (
            countdown == 1
            and mode == 1
            and power == 1
            and flag == 0
            and inhibit == 0
            and latch == 1
        )
        for original in (True, False):
            m = setup()
            u = m.u
            if original:
                u.mem_write(
                    0x1D00E35C, S[0x9D00E35C - build.BASE : 0x9D00E3EC - build.BASE]
                )
            for a, v in ((0x241, countdown), (0x89F0, power), (0x8A36, flag)):
                u.mem_write(a, bytes([v]))
            for a, v in ((0xFFA0, mode), (0x288, inhibit), (0x50, latch)):
                u.mem_write(a, p32(v))
            persistent = bytes(u.mem_read(0x84C, 0xF00))
            dirty = bytes(u.mem_read(0x1E1, 1))
            saved = [
                UC_MIPS_REG_S0,
                UC_MIPS_REG_S1,
                UC_MIPS_REG_S2,
                UC_MIPS_REG_S3,
                UC_MIPS_REG_S4,
                UC_MIPS_REG_S5,
                UC_MIPS_REG_S6,
                UC_MIPS_REG_S7,
                UC_MIPS_REG_GP,
            ]
            regs = [u.reg_read(r) for r in saved]
            writes = []
            notifications = []
            u.hook_add(
                UC_HOOK_CODE,
                lambda u, a, n, d: (
                    notifications.append(u.reg_read(UC_MIPS_REG_A0))
                    if a == 0x9D02AC5C
                    else None
                ),
            )

            def stores(u, a, n, d):
                # UC_HOOK_MEM_WRITE perturbs this Unicorn build's stock memcpy loop.
                # Observe stores from actual fetched instructions instead (not stale MEMOPS).
                w = int.from_bytes(u.mem_read(a & 0x1FFFFFFF, 4), "little")
                op = w >> 26
                if op in (40, 41, 42, 43, 46, 56):
                    imm = w & 65535
                    imm -= 65536 if imm & 32768 else 0
                    writes.append(
                        ((u.reg_read(CPU[(w >> 21) & 31]) + imm) & 0x1FFFFFFF, op)
                    )

            u.hook_add(UC_HOOK_CODE, stores)
            try:
                idle(m)
            except Exception:
                print(
                    "case",
                    countdown,
                    mode,
                    power,
                    flag,
                    inhibit,
                    latch,
                    "original",
                    original,
                    "pc",
                    hex(u.reg_read(UC_MIPS_REG_PC)),
                    "trace",
                    [hex(x) for x in m.visited[-16:]],
                    flush=True,
                )
                raise
            assert regs == [u.reg_read(r) for r in saved]
            assert persistent == bytes(u.mem_read(0x84C, 0xF00)) and dirty == bytes(
                u.mem_read(0x1E1, 1)
            )
            assert not any(0x1D000000 <= a < 0x1E000000 for a, n in writes)
            states.append((bytes(u.mem_read(0, 0xE000)), m.frames, notifications))
        if not suppress:
            assert states[0] == states[1], (
                countdown,
                mode,
                power,
                flag,
                inhibit,
                latch,
            )
        else:
            assert states[0][1] == [expected(b" " * 8)] and states[1][1] == []
            assert states[0][2] == [50] and states[1][2] == []
            changed += 1
        rows += 1
    return dict(
        cases=rows,
        intended_suppressions=changed,
        all_other_cases_stock_identical=True,
        all_byte_countdowns=True,
        no_persistent_or_flash_writes=True,
        callee_saved_preserved=True,
    )


def standby_resume():
    rows = []
    for a, value in ((0x89F0, 3), (0x8A36, 1), (0x288, 1), (0x2B0, 1)):
        m = setup()
        accepted(m)
        stock(m, b" " * 8)
        old = bytes(m.u.mem_read(a, 4))
        m.u.mem_write(a, p32(value))
        m.u.mem_write(0x241, b"\x01")
        before = list(m.frames)
        accepted(m, text=b"12-34-59")
        for _ in range(20):
            service_only(m)
            # Outer main-loop gate skips idle region when a00002b0 is set.
            if not int.from_bytes(m.u.mem_read(0x2B0, 4), "little"):
                idle(m)
        assert m.frames == before
        m.u.mem_write(a, old)
        # Actual stock resume latch writer; state guards are controlled fixtures,
        # not a claim to emulate the entire power-supply/standby state machine.
        block(m, 0x9D009AC4, 0x9D009ACC)
        for _ in range(20):
            service_only(m)
            idle(m)
        assert m.frames[-1] == expected(b"12-34-59"), (
            hex(a),
            state(m),
            bytes(m.u.mem_read(0x234, 128)).hex(),
            [f.hex() for f in m.frames],
        )
        rows.append(hex(a))
    return dict(
        controlled_guards=rows,
        actual_stock_latch_resume=True,
        full_standby_transition_emulated=False,
    )


def latch_writers():
    rows = []
    for a, z, initial, want in (
        (0x9D007AF0, 0x9D007AF8, 0, 1),
        (0x9D009AC4, 0x9D009ACC, 0, 1),
        (0x9D00C79C, 0x9D00C7C0, 0, 1),
        (0x9D00C79C, 0x9D00C7C0, 1, 0),
    ):
        m = setup()
        m.u.mem_write(0x50, p32(initial))
        block(m, a, z)
        assert m.u.mem_read(0x50, 4) == p32(want)
        rows.append(hex(a))
    m = setup()
    m.u.mem_write(0x50, p32(0xDEADBEEF))
    m.run(0x9D005070, 0x9D005118)
    assert m.u.mem_read(0x50, 4) == p32(1)
    m = setup()
    m.u.mem_write(0x241, b"\x01")
    idle(m)
    assert m.u.mem_read(0x50, 4) == p32(0)
    return dict(
        runtime_paths=rows,
        crt_initializes_one=True,
        no_background_autoblank_clears=True,
    )


def decision_irq():
    from test_single_writer import io_initialized, attach_stats, transaction

    m, b = io_initialized()
    u = m.u
    u.mem_write(0x50, p32(1))
    accepted(m)
    u.mem_write(0x241, b"\x01")
    block(m, 0x9D00E35C, labels["idle_decision"])
    generation = int.from_bytes(u.mem_read(0xFF24, 4), "little")
    stats = attach_stats(m, b)
    transaction(m, b, False)
    after = list(m.frames)
    new_generation = int.from_bytes(u.mem_read(0xFF24, 4), "little")
    assert new_generation != generation and u.mem_read(0xFF2C, 4) == p32(0)
    m.run(labels["idle_decision"], 0x9D00E3EC)
    assert (
        m.frames == after
        and u.mem_read(0xFF24, 4) == p32(new_generation)
        and u.mem_read(0xFF2C, 4) == p32(0)
    )
    assert all(x & 1 and not x & 6 for x in stats["gpio_status"])
    return dict(
        real_usb_isr=True,
        irq_pc="0x9d01ae14",
        decision_pc=hex(labels["idle_decision"]),
        stock_frame_not_overwritten=True,
        no_stale_regrant=True,
        masked_gpio=0,
    )


if __name__ == "__main__":
    r = dict(
        differential=differential(),
        standby_resume=standby_resume(),
        latch_writers=latch_writers(),
        decision_irq=decision_irq(),
        clear_during_stock=clear_during_stock(),
        volume_expiry=volume_actual(),
        generation=generation_races(),
        data_output_irqs=latched_gpio_irq(),
    )
    save("provenance-extended.json", r)
    print(json.dumps(r, indent=2))
