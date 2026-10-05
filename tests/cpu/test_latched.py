#!/usr/bin/env python3
"""Latched-background regressions executing assembled MIPS. No hardware."""

import events

from events import Boundary
from extra import nested_stock
from machine import ALLOW, SP, STOP, expected, p32, packet
from probe import block, labels, lease, machine, request, turn
from test_single_writer import save
import json
import struct
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def bg(op=1, text=b"12-34-56"):
    p = bytearray(packet(op, text))
    p[6] = 2
    return bytes(p)


def setup():
    m = machine(latch=1)
    Boundary(m)
    return m


def accepted(m, op=1, text=b"12-34-56"):
    r = request(m, bg(op, text))
    assert r and r[0][7] == 0
    assert r[0][3] == 2 and r[0][6] == 2
    assert r[0][8:16] == (text if op == 1 else b" " * 8)
    return r


def stock(m, text=b"VOL 49  "):
    m.u.mem_write(0xD000, text)
    m.call(0x9D036DF8, [0xA000D000, 8])


def continuous():
    m = setup()
    for i in range(120):
        text = ("%02d-%02d-%02d" % (12, i // 60, i % 60)).encode()
        accepted(m, text=text)
        assert m.frames[-1] == expected(text)
        turn(m)
        turn(m)
        assert lease(m) == 0
    assert len(m.frames) == 120 and expected(b" " * 8) not in m.frames
    for _ in range(1000):
        turn(m)
    assert lease(m) == 0 and m.frames[-1] == expected(text) and len(m.frames) == 120
    accepted(m, 2)
    assert m.frames[-1] == expected(b" " * 8)
    for _ in range(20):
        turn(m)
    assert len(m.frames) == 121
    return dict(
        updates=120,
        no_request_service_opportunities=1000,
        no_blank_between_updates=True,
        clear_removes=True,
    )


def service_only(m):
    # Isolate endpoint service without advancing the STOCK feedback countdown.
    block(m, labels["service"], labels["resume"])


def priority():
    rows = []
    for pending in (0x234, 0x240, 0x2A8, 0x2B4, 0xFF20):
        m = setup()
        accepted(m)
        stock(m)
        m.u.mem_write(pending, p32(1))
        before = list(m.frames)
        for text in (b"12-34-57", b"12-34-58", b"12-34-59"):
            accepted(m, text=text)
            service_only(m)
            assert m.frames == before
            assert m.u.mem_read(pending, 1) == b"\x01"
        m.u.mem_write(pending, p32(0))
        turn(m)
        assert m.frames == before + [expected(b"12-34-59")]
        rows.append(hex(pending))
    return dict(pending_fields=rows, latest_only=True)


def clear_during_stock():
    for pending in (0x234, 0x240, 0x2A8, 0x2B4, 0xFF20):
        m = setup()
        accepted(m)
        stock(m)
        m.u.mem_write(pending, p32(1))
        before = list(m.frames)
        accepted(m, 2)
        for _ in range(5):
            service_only(m)
        assert m.frames == before
        m.u.mem_write(pending, p32(0))
        for _ in range(5):
            service_only(m)
        assert m.frames == before and m.u.mem_read(0xFFA0, 4) == bytes(4)
    return dict(cases=5, stock_untouched=True, no_resume=True)


def off_and_standby():
    for addr, val in ((0x50, 0), (0x89F0, 0), (0x8A36, 1), (0x2B0, 1), (0x288, 1)):
        m = setup()
        accepted(m)
        stock(m, b"        ")
        old = bytes(m.u.mem_read(addr, 4))
        m.u.mem_write(addr, p32(val))
        before = list(m.frames)
        accepted(m, text=b"01-02-03")
        for _ in range(10):
            service_only(m)
        assert m.frames == before, (hex(addr), m.frames)
        assert m.u.mem_read(addr, 4) == p32(val)
        m.u.mem_write(addr, old)
        service_only(m)
        assert m.frames[-1] == expected(b"01-02-03")
    return dict(cases=5, no_forced_wake=True)


def suspend(m, p, pc):
    u = m.u
    u.mem_write(0x50A0, p)
    u.mem_write(0x448, struct.pack("<H", 64))
    u.reg_write(UC_MIPS_REG_A0, 1)
    u.reg_write(UC_MIPS_REG_A1, 0xA00050A0)
    u.reg_write(UC_MIPS_REG_RA, STOP)
    m.break_at = pc
    u.emu_start(0x9D02B79C, STOP, count=100000)
    assert u.reg_read(UC_MIPS_REG_PC) == pc
    m.break_at = None


def generation_races():
    # Deliberate nested calls, not claimed physical EP0 reentrancy. Prove stale
    # pre-gate snapshots cannot publish after a newer CLEAR or stock takeover.
    rows = []
    for where in ("render_gate", "writer"):
        for action in ("clear", "set", "stock"):
            m = setup()
            suspend(m, bg(), labels[where])
            u = m.u
            context = u.context_save()
            u.reg_write(UC_MIPS_REG_SP, 0xA000E800)
            if action == "stock":
                nested_stock(m, b"VOL 50  ")
            else:
                accepted(m, 2 if action == "clear" else 1, b"12-34-59")
            before = list(m.frames)
            u.context_restore(context)
            m.run(labels[where])
            assert m.frames == before, (where, action, m.frames, before)
            rows.append([where, action])
    return dict(cases=rows, stale_output_suppressed=True)


def expiry_races():
    rows = []
    for action in ("set", "clear", "stock"):
        m = setup()
        request(m)
        turn(m)
        turn(m)
        m.u.mem_write(0x496, bytes(2))
        m.u.mem_write(0x3F6, b"\1")
        m.u.mem_write((SP - 128 + 16) & 0x1FFFFFFF, p32(99))
        u = m.u
        u.reg_write(UC_MIPS_REG_SP, SP - 128)
        u.reg_write(UC_MIPS_REG_FP, SP - 128)
        m.break_at = labels["render_gate"]
        u.emu_start(0x9D00D3E8, STOP, count=200000)
        assert u.reg_read(UC_MIPS_REG_PC) == m.break_at
        m.break_at = None
        context = u.context_save()
        u.reg_write(UC_MIPS_REG_SP, 0xA000E800)
        if action == "stock":
            nested_stock(m, b"VOL 50  ")
        else:
            accepted(m, 1 if action == "set" else 2, b"12-34-59")
        before = list(m.frames)
        u.context_restore(context)
        m.break_at = 0x9D00D3CC
        u.emu_start(labels["render_gate"], STOP, count=200000)
        assert u.reg_read(UC_MIPS_REG_PC) == m.break_at
        m.break_at = None
        assert m.frames == before, (action, m.frames, before)
        rows.append(action)
    return dict(cases=rows, old_expiry_suppressed=True)


def volume_actual():
    old = events.request
    captured = []

    def set_background(m, *args, **kw):
        captured.append(m)
        m.u.mem_write(0x50, p32(1))
        return old(m, bg())

    events.request = set_background
    try:
        r = events.event(2, True, True)
    finally:
        events.request = old
    # Actual stock event, deferred I2C feedback, common render and timeout path.
    assert r["consumer_state"]["lease"] == 0
    assert r["frames"][0] == expected(b"12-34-56").hex()
    assert any(x != r["frames"][0] for x in r["frames"][1:])
    m = captured[0]
    turn(m)
    assert m.frames[-1] == expected(b"12-34-56"), [x.hex() for x in m.frames]
    r["resumed_frame"] = m.frames[-1].hex()
    r["resume_opportunities_after_stock_timeout"] = 1
    return {k: v for k, v in r.items() if k not in ("visited", "cp0", "completions")}


def latched_gpio_irq():
    import test_single_writer as inherited

    old_packet = inherited.packet
    old_init = inherited.io_initialized

    def init():
        m, b = old_init()
        m.u.mem_write(0x50, p32(1))
        return m, b

    inherited.packet = lambda: bg(1, b"HELLO   ")
    inherited.io_initialized = init
    try:
        rows = [inherited.gpio_irq(i) for i in (1, 9, 65, 130)]
    finally:
        inherited.packet = old_packet
        inherited.io_initialized = old_init
    return rows


def protocol_v2():
    cases = 0
    for pos in range(8):
        for c in range(256):
            m = setup()
            text = bytearray(b"ABCDEFGH")
            text[pos] = c
            r = request(m, bg(1, bytes(text)))
            assert bool(r) == (c in ALLOW)
            if r:
                assert m.frames == [expected(text)]
            else:
                assert not m.frames and m.u.mem_read(0xFFA0, 4) == bytes(4)
            cases += 1
    for kind in ("length", "magic", "glyph", "opcode", "short", "source", "pointer"):
        m = setup()
        accepted(m)
        p = bytearray(bg())
        kw = {}
        if kind == "length":
            p[2] = 12
        if kind == "magic":
            p[4] ^= 1
        if kind == "glyph":
            p[15] = 255
        if kind == "opcode":
            p[3] = 3
        if kind == "short":
            kw["count"] = 63
        if kind == "source":
            kw["transport"] = 2
        if kind == "pointer":
            kw["ptr"] = 0xA0005200
        before = bytes(m.u.mem_read(0xFF20, 200)), list(m.frames)
        assert not request(m, bytes(p), **kw)
        assert before == (bytes(m.u.mem_read(0xFF20, 200)), list(m.frames))
        cases += 1
    return dict(cases=cases, invalid_no_mutation=True)


def main():
    r = dict(
        continuous=continuous(),
        priority=priority(),
        clear_during_stock=clear_during_stock(),
        off_and_standby=off_and_standby(),
        generation_races=generation_races(),
        expiry_races=expiry_races(),
        actual_volume=volume_actual(),
        actual_gpio_irq=latched_gpio_irq(),
        protocol=protocol_v2(),
    )
    save("latched-results.json", r)
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
