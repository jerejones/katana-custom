#!/usr/bin/env python3
"""Bounded correction evidence. No device access, firmware files or deployment."""

from electrical import io_machine, ClockBoundary, electrical
import hashlib

from events import Boundary, event
from fixture_data import OUT, ROOT, S, build
from machine import MEMOPS, SP, STOP, expected, p32, packet
from pathlib import Path
from probe import CPU, code, image, labels, lease, machine, request, segments, state
from unicorn import UC_HOOK_CODE
from usb_path import initialized, proof, transaction
import json
import struct
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def attach_stats(m, b):
    stats = dict(
        critical_lengths=[],
        gpio_status=[],
        transactions=[],
        minimum_observed_sp=0xFFFFFFFF,
    )
    current = []
    data_bit = 0
    active = False
    masked = 0

    def hook(u, a, size, unused):
        nonlocal current, data_bit, active, masked
        stats["minimum_observed_sp"] = min(
            stats["minimum_observed_sp"], u.reg_read(UC_MIPS_REG_SP)
        )
        if (
            any(lo <= a < lo + len(blob) for lo, blob in segments)
            and not b.cp[12, 0] & 1
        ):
            masked += 1
        elif masked:
            stats["critical_lengths"].append(masked)
            masked = 0
        mem = MEMOPS.get(a)
        if not mem or mem[0] != 43:
            return
        op, rs, rt, imm = mem
        address = (u.reg_read(CPU[rs]) + imm) & 0x1FFFFFFF
        v = u.reg_read(CPU[rt])
        if address not in (0x1F886134, 0x1F886138, 0x1F886334, 0x1F886338):
            return
        stats["gpio_status"].append(b.cp[12, 0])
        if address in (0x1F886134, 0x1F886138) and v & 0x8000:
            data_bit = int(address == 0x1F886138)
        if address == 0x1F886334 and v & 32:
            assert not active, "overlapping electrical transaction"
            active = True
            current = []
        if address == 0x1F886338 and v & 512 and active:
            current.append(data_bit)
        if address == 0x1F886338 and v & 32 and active:
            assert len(current) % 8 == 0, len(current)
            stats["transactions"].append(
                bytes(
                    sum(current[i + j] << j for j in range(8))
                    for i in range(0, len(current), 8)
                ).hex()
            )
            active = False

    m.u.hook_add(UC_HOOK_CODE, hook)
    return stats


def io_initialized():
    m = io_machine()
    m.run(0x9D005070, 0x9D005118)
    b = ClockBoundary(m)
    b.cp[12, 0] = 0
    m.call(0x9D03FB5C)
    m.call(0x9D01AC30)
    m.call(0x9D01B4B8)
    m.call(0x9D01C76C)
    assert b.cp[12, 0] == 1
    transaction(m, b, True)
    u = m.u
    u.mem_write(0x1B8, p32(0xA000E000))
    u.mem_write(0x50, p32(0))
    u.mem_write(0x89F0, b"\1")
    u.mem_write(0x89CE, b"\1")
    u.mem_write(0x1E1, b"\xff")
    return m, b


def gpio_irq(bit_index):
    m, b = io_initialized()
    stats = attach_stats(m, b)
    u = m.u
    count = 0
    u.mem_write(0x50A0, packet())
    u.reg_write(UC_MIPS_REG_A0, 1)
    u.reg_write(UC_MIPS_REG_A1, 0xA00050A0)
    u.reg_write(UC_MIPS_REG_RA, STOP)

    def stop(u, a, size, d):
        nonlocal count
        if a == 0x9D036B68 and m.frames:
            count += 1
            if count == bit_index:
                u.emu_stop()

    h = u.hook_add(UC_HOOK_CODE, stop)
    u.emu_start(0x9D02B79C, STOP, count=100000)
    pc = u.reg_read(UC_MIPS_REG_PC)
    assert pc == 0x9D036B68 and count == bit_index
    u.hook_del(h)
    before = len(stats["transactions"])
    frames_before = list(m.frames)
    persistent = bytes(u.mem_read(0x84C, 0xF00))
    dirty = bytes(u.mem_read(0x1E1, 1))
    transaction(m, b, False)
    assert (
        len(stats["transactions"]) == before and m.frames == frames_before
    )  # actual IRQ queues, no interleaved GPIO
    assert int.from_bytes(u.mem_read(0xFF30, 4), "little") == 1
    queued = bytes(u.mem_read(0xFF40, 16))
    m.run(pc)
    assert m.frames == [expected(b"HELLO   "), queued]
    want = [
        "40",
        "c0" + expected(b"HELLO   ").hex(),
        "8f",
        "40",
        "c0" + queued.hex(),
        "8f",
    ]
    assert stats["transactions"] == want, stats["transactions"]
    assert all(x & 1 and not x & 6 and (x >> 10) & 7 == 0 for x in stats["gpio_status"])
    assert bytes(u.mem_read(0xFF20, 4)) == bytes(4) and bytes(
        u.mem_read(0xFF30, 4)
    ) == bytes(4)
    assert persistent == bytes(u.mem_read(0x84C, 0xF00)) and dirty == bytes(
        u.mem_read(0x1E1, 1)
    )
    return dict(
        bit_index=bit_index,
        passed=True,
        frames=[x.hex() for x in m.frames],
        transactions=stats["transactions"],
        gpio_samples=len(stats["gpio_status"]),
        blocked_gpio_samples=0,
        critical_lengths=stats["critical_lengths"],
        lease=lease(m),
        irq_preserved_registers=True,
        minimum_observed_sp=hex(stats["minimum_observed_sp"]),
        observed_descent_from_fixture_sp=SP - stats["minimum_observed_sp"],
        stack_bound_proven=False,
    )


def suspend_request(m, pc):
    u = m.u
    u.mem_write(0x50A0, packet())
    u.reg_write(UC_MIPS_REG_A0, 1)
    u.reg_write(UC_MIPS_REG_A1, 0xA00050A0)
    u.reg_write(UC_MIPS_REG_RA, STOP)
    m.break_at = pc
    u.emu_start(0x9D02B79C, STOP, count=100000)
    assert u.reg_read(UC_MIPS_REG_PC) == pc
    m.break_at = None


def queue_case(nframes):
    # Deliberate synchronous nested-call schedule, not claimed to be a real ISR.
    # Actual stock glyph and copied-frame writer execute; caller stack is distinct.
    m = machine()
    b = Boundary(m)
    m.intercepts = set()
    suspend_request(m, labels["writer_output"])
    u = m.u
    context = u.context_save()
    expected_stock = []
    for i in range(nframes):
        text = (str((i + 1) % 10) * 8).encode()
        u.mem_write(0xD000, text)
        u.reg_write(UC_MIPS_REG_SP, 0xA000E800)
        u.reg_write(UC_MIPS_REG_RA, STOP)
        u.reg_write(UC_MIPS_REG_A0, 0xA000D000)
        u.reg_write(UC_MIPS_REG_A1, 8)
        m.run(0x9D036DF8)
        expected_stock.append(expected(text))
        u.mem_write(0xD000, b"XXXXXXXX")
    # Overwrite returned nested call stacks and sources: queue must contain bytes.
    u.mem_write(0xE400, b"\xdd" * 0x400)
    count = int.from_bytes(u.mem_read(0xFF30, 4), "little")
    overflows = int.from_bytes(u.mem_read(0xFF3C, 4), "little")
    assert count == min(nframes, 4) and overflows == max(0, nframes - 4)
    u.context_restore(context)
    m.run(labels["writer_output"])
    retained = (
        expected_stock if nframes <= 4 else expected_stock[:3] + expected_stock[-1:]
    )
    assert m.frames == [expected(b"HELLO   ")] + retained
    assert (
        not lease(m)
        and u.mem_read(0xFF20, 4) == bytes(4)
        and u.mem_read(0xFF30, 4) == bytes(4)
    )
    return dict(
        submissions=nframes,
        frames=[x.hex() for x in m.frames],
        overflow_count=overflows,
        passed=True,
    )


def status_cases():
    rows = []
    for status in (0, 1, 0x400, 0x401, 0x10000001, 0x10000000):
        m = machine()
        b = Boundary(m)
        b.cp[12, 0] = status
        stats = attach_stats(m, b)
        request(m)
        assert b.cp[12, 0] == status
        assert all(x == status for x in stats["gpio_status"])
        rows.append(
            dict(
                prior=hex(status),
                restored=hex(b.cp[12, 0]),
                critical_lengths=stats["critical_lengths"],
            )
        )
    return rows


def malformed():
    rows = []
    for active in (False, True):
        for kind in (
            "length",
            "magic",
            "glyph",
            "opcode",
            "short",
            "source",
            "pointer",
        ):
            m = machine()
            Boundary(m)
            if active:
                request(m)
            p = bytearray(packet())
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
            before = (bytes(m.u.mem_read(0xFF20, 200)), state(m), list(m.frames))
            assert not request(m, bytes(p), **kw)
            assert before == (
                bytes(m.u.mem_read(0xFF20, 200)),
                state(m),
                list(m.frames),
            )
            rows.append(dict(active=active, kind=kind, passed=True))
    return rows


def stock_routes():
    old = struct.unpack_from("<196I", S, 0x9D02B884 - build.BASE)
    dests = set(old)
    for op in range(256):
        if op == 126:
            continue
        m = machine()
        u = m.u
        u.mem_write(0x50A0, bytes([0x5A, op, 13]) + bytes(61))
        u.reg_write(UC_MIPS_REG_A0, 1)
        u.reg_write(UC_MIPS_REG_A1, 0xA00050A0)
        u.reg_write(UC_MIPS_REG_RA, STOP)
        hits = []

        def stop(u, a, n, d):
            if a in dests:
                hits.append(a)
                u.emu_stop()

        u.hook_add(UC_HOOK_CODE, stop)
        u.emu_start(0x9D02B79C, STOP, count=10000)
        assert hits == [old[op] if op < 196 else 0x9D02DFA0], (op, hits)
    return dict(cases=255, passed=True)


def run():
    tests = []
    # Four known defects first, before broader loops, with original-stock IRQ control.
    control_m, control_b = initialized()
    control_m.u.mem_write(build.BASE & 0x1FFFFFFF, S)
    transaction(control_m, control_b, True)
    transaction(control_m, control_b, False)
    assert control_m.frames and control_m.frames[-1] != bytes(16)
    save(
        "original-stock-irq-control.json",
        dict(frames=[f.hex() for f in control_m.frames], register_preservation=True),
    )
    irq = []
    for window in (None, "grant", "blank", "render_io"):
        r = proof(window)
        assert r["preserved"] and r["stock_frame"] == control_m.frames[-1].hex()
        irq.append(r)
    save("irq-green.json", irq)
    tests.append(dict(test="actual stock IRQ four windows", cases=4, passed=True))
    for name, word, near in [
        ("volume-near-expiry", 2, True),
        ("volume-normal", 2, False),
        ("same-value-complete", 0x40, True),
    ]:
        r = event(word, True, near)
        c = event(word, True, near, False)
        assert (
            r["state"] == c["state"]
            and r["turns"] == c["turns"]
            and r["final_frame"] == c["final_frame"]
        )
        if near:
            assert r["tail_state"]["state"] == c["tail_state"]["state"]
        if word == 2:
            assert r["frames"][1:] == c["frames"] and r["consumer_state"]["lease"] == 0
        save(name + "-green.json", dict(candidate=r, stock_control=c))
        tests.append(dict(test=name, passed=True))
    gpio = [gpio_irq(i) for i in (1, 9, 65, 130)]
    save("gpio-irq.json", gpio)
    tests.append(
        dict(test="actual IRQ during real GPIO bit loops", cases=len(gpio), passed=True)
    )
    queues = [queue_case(i) for i in (1, 2, 4, 5, 10)]
    save("queues.json", queues)
    tests.append(
        dict(
            test="copied FIFO and explicit overflow coalescing",
            cases=len(queues),
            passed=True,
        )
    )
    save("status.json", status_cases())
    tests.append(
        dict(
            test="prior status preserved including IE disabled and IPL",
            cases=6,
            passed=True,
        )
    )
    save("malformed.json", malformed())
    tests.append(dict(test="malformed no mutation", cases=14, passed=True))
    tests.append(dict(test="all stock opcode routes", **stock_routes()))
    save("electrical.json", electrical())
    tests.append(dict(test="standalone GPIO admission", passed=True))
    before = json.loads((OUT / "preserved-before.json").read_text())
    changed = [
        p
        for p, h in before.items()
        if hashlib.sha256(Path(p).read_bytes()).hexdigest() != h
    ]
    assert not changed
    save("preservation.json", dict(files_checked=len(before), changed=changed))
    r = dict(
        offline_correction_passed=True,
        deployment_qualified=False,
        firmware_emitted=False,
        tests=tests,
        enumerated_cases=sum(t.get("cases", 1) for t in tests),
        code_bytes=sum(len(b) for a, b in segments if a != 0x9D00E35C),
        idle_replacement_bytes=144,
        existing_owned_budget=812,
        new_superseded_glyph_budget=1328,
        available_regions_bytes=812 + 1328,
        unwritten_region_bytes=812
        + 1328
        - sum(len(b) for a, b in segments if a != 0x9D00E35C),
        reserved_ram_bytes=0xFFF8 - 0xFF00,
        additional_reserved_ram_vs_early_v1=0xFFB8 - 0xFF00,
        logical_state_bytes=152,
        payload_sha256=hashlib.sha256(code).hexdigest(),
        memory_only_image_sha256=hashlib.sha256(image).hexdigest(),
        source_sha256=hashlib.sha256(
            (ROOT / "firmware/endpoint.S").read_bytes()
        ).hexdigest(),
        stock_sha256=hashlib.sha256(S).hexdigest(),
        max_sampled_critical_instructions=max(
            v for row in gpio for v in row["critical_lengths"]
        ),
        input_files_unchanged=len(before),
    )
    save("results.json", r)
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    run()
