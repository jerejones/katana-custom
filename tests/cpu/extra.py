#!/usr/bin/env python3
"""Additional race, stock differential and foreground protocol checks."""

from events import Boundary
from fixture_data import S, build
from machine import ALLOW, STOP, expected, p32, packet
from probe import labels, lease, machine, request, state, turn
from test_single_writer import save, suspend_request
from usb_path import initialized, proof, transaction
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def before_grant():
    m, b = initialized()
    transaction(m, b, True)
    m.u.mem_write(0x1B8, p32(0xA000E000))
    m.u.mem_write(0x50, p32(0))
    m.u.mem_write(0x89F0, b"\1")
    suspend_request(m, labels["admission"])
    transaction(m, b, False)
    stock = m.frames[-1]
    m.run(labels["admission"])
    assert m.frames == [stock] and lease(m) == 0 and m.replies[-1][7] == 1
    return dict(
        passed=True, status=m.replies[-1][7], frames=[f.hex() for f in m.frames]
    )


def stock_differential():
    rows = []
    for text in (
        b"ABCDEFGH",
        b"12345678",
        b"VOL 49  ",
        b"HELLO   ",
        b"ABCDEFGHIJKLMNOP",
    ):
        outcomes = []
        for stock in (False, True):
            m = machine()
            Boundary(m)
            m.intercepts = set()
            if stock:
                m.u.mem_write(build.BASE & 0x1FFFFFFF, S)
            m.u.mem_write(0xD000, text)
            m.call(0x9D0374A4, [0xA000D000, len(text), 0xA000D000])
            if len(text) > 8:
                for _ in range(4):
                    m.call(0x9D0375C4)
            outcomes.append(
                dict(
                    frames=[f.hex() for f in m.frames],
                    state=state(m),
                    scroller=bytes(m.u.mem_read(0x2BF8, 96)).hex(),
                )
            )
        assert outcomes[0] == outcomes[1], outcomes
        rows.append(dict(text=text.decode(), passed=True, **outcomes[0]))
    return rows


def nested_stock(m, text):
    u = m.u
    context = u.context_save()
    u.mem_write(0xD000, text)
    u.reg_write(UC_MIPS_REG_SP, 0xA000E800)
    u.reg_write(UC_MIPS_REG_RA, STOP)
    u.reg_write(UC_MIPS_REG_A0, 0xA000D000)
    u.reg_write(UC_MIPS_REG_A1, 8)
    m.run(0x9D036DF8)
    u.mem_write(0xD000, b"XXXXXXXX")
    u.mem_write(0xE400, b"\xdd" * 0x400)
    u.context_restore(context)


def refill_during_drain():
    m = machine()
    Boundary(m)
    m.intercepts = set()
    suspend_request(m, labels["writer_output"])
    for i in range(1, 5):
        nested_stock(m, str(i).encode() * 8)
    m.break_at = labels["writer_complete"]
    m.u.emu_start(labels["writer_output"], STOP, count=20000)
    assert m.u.reg_read(UC_MIPS_REG_PC) == m.break_at
    m.break_at = labels["writer_output"]
    m.u.emu_start(labels["writer_complete"], STOP, count=20000)
    assert m.u.reg_read(UC_MIPS_REG_PC) == m.break_at
    m.break_at = None
    nested_stock(m, b"55555555")
    m.run(labels["writer_output"])
    want = [expected(b"HELLO   ")] + [
        expected(str(i).encode() * 8) for i in range(1, 6)
    ]
    assert m.frames == want and m.u.mem_read(0xFF3C, 4) == bytes(4)
    return dict(passed=True, frames=[f.hex() for f in m.frames], overflows=0)


def pending_priority():
    rows = []
    for addr in (0x234, 0x240, 0x2A8, 0x2B4, 0xFF20):
        m = machine()
        Boundary(m)
        m.u.mem_write(addr, p32(1))
        r = request(m)
        assert r[0][7] == 1 and not m.frames and not lease(m)
        rows.append(dict(pending=hex(addr), passed=True))
    return rows


def protocol():
    cases = 0
    for pos in range(8):
        for c in range(256):
            m = machine()
            Boundary(m)
            text = bytearray(b"ABCDEFGH")
            text[pos] = c
            r = request(m, packet(1, bytes(text)))
            assert bool(r) == (c in ALLOW)
            if r:
                assert m.frames == [expected(text)]
            else:
                assert not m.frames and not lease(m)
            cases += 1
    for op in (1, 2):
        m = machine(latch=1)
        Boundary(m)
        for _ in range(3):
            assert request(m, packet(op))[0][7] == 0
            original = list(m.frames)
            m.u.mem_write(0x50A0, b"\xdd" * 64)
            for left in (3, 2, 1):
                for _ in range(3):
                    assert (
                        request(m, packet(op))[0][7] == 1
                        and lease(m) == left
                        and m.frames == original
                    )
                    cases += 1
                turn(m)
            assert lease(m) == 0
    return dict(cases=cases, passed=True)


def run():
    r = dict(
        early_admission_race=before_grant(),
        expiry_irq_pending_in_critical=proof("blank_committed"),
        stock_differential=stock_differential(),
        fifo_refill=refill_during_drain(),
        pending_priority=pending_priority(),
        protocol=protocol(),
    )
    assert r["expiry_irq_pending_in_critical"]["preserved"]
    save("extra.json", r)
    print(
        json.dumps(
            {
                k: (
                    v
                    if k not in ("expiry_irq_pending_in_critical", "stock_differential")
                    else "passed"
                )
                for k, v in r.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    run()
