#!/usr/bin/env python3
"""Real stock idle-expiry and explicit-toggle acceptance, no device access."""

from machine import SP, expected, p32, packet
from probe import block, request, state, turn
from test_latched import accepted, bg, service_only, setup
from test_single_writer import save
import json


def auto_blank():
    m = setup()
    accepted(m)
    m.u.mem_write(0x241, b"\x01")
    turn(m)
    assert state(m) == (0, 0, 1), ("auto-blank killed enabled latch", state(m))
    assert m.frames[-1] == expected(b"12-34-56")
    for _ in range(200):
        turn(m)
    r = request(m, bg(0))
    assert r[0][8:16] == b"12-34-56"
    accepted(m, text=b"12-34-59")
    for _ in range(200):
        turn(m)
    assert m.frames[-1] == expected(b"12-34-59") and expected(b" " * 8) not in m.frames
    accepted(m, 2)
    assert m.frames[-1] == expected(b" " * 8)
    return dict(
        service_opportunities=400,
        real_idle_expiry=True,
        status_then_set=True,
        blank_frames_before_clear=0,
    )


def explicit_toggle():
    m = setup()
    accepted(m)
    block(m, 0x9D00C79C, 0x9D00C8A8)
    assert 0x9D00C7AC in m.visited and state(m) == (0, 0, 0)
    frames = list(m.frames)
    for text in (b"12-34-57", b"12-34-58", b"12-34-59"):
        accepted(m, text=text)
        for _ in range(50):
            turn(m)
    assert frames == m.frames and m.frames[-1] == expected(b" " * 8)
    # Execute the actual stock on arm, not a test write to the latch.
    m.u.mem_write((SP - 128 + 32) & 0x1FFFFFFF, p32(0x40000000))
    block(m, 0x9D00C79C, 0x9D00C8A8)
    assert 0x9D00C7BC in m.visited
    # Default fixture has zero configured timeout; service after stock UI.
    turn(m)
    assert m.frames[-1] == expected(b"12-34-59")
    return dict(
        actual_off_and_on=True,
        updates_while_off=3,
        dark_opportunities=150,
        latest_resumes=True,
    )


FLAG = 0xFFA8  # idle-blank provenance record written by idle-provenance.S
TIMEOUT = 0x89F5  # stock configured idle timeout byte, copied to 0x241 on display-on


def idle(m):
    block(m, 0x9D00E35C, 0x9D00E3EC)


def flag(m):
    return int.from_bytes(m.u.mem_read(FLAG, 4), "little")


def idle_blank_no_background(m):
    """Run the real idle expiry with no background: display goes dark."""
    m.u.mem_write(0x241, b"\x01")
    idle(m)
    assert state(m)[2] == 0 and m.frames[-1] == expected(b" " * 8)


def wake_after_idle_blank():
    # The complaint this fixes: display idled off before the clock started.
    m = setup()
    m.u.mem_write(TIMEOUT, b"\x07")
    idle_blank_no_background(m)
    assert flag(m) == 1
    accepted(m, text=b"12-34-56")
    # Stock display-on writes mirrored: latch 1, idle countdown re-armed.
    assert state(m) == (0, 7, 1), state(m)
    stock_writes = sorted({(a, n) for a, n, v in m.writes if a < 0xE000})
    assert stock_writes == [(0x50, 4), (0x241, 1)], stock_writes
    assert m.frames[-1] == expected(b"12-34-56")
    # The next main-loop pass sees the display on and retires the record.
    idle(m)
    assert flag(m) == 0 and state(m)[2] == 1
    # Countdown now expires with a background present: suppressed as before.
    for _ in range(10):
        idle(m)
    assert state(m)[2] == 1 and m.frames[-1] == expected(b"12-34-56")
    accepted(m, 2)
    assert m.frames[-1] == expected(b" " * 8)
    return dict(woke=True, idle_rearmed_from_config=True, record_retired=True)


def explicit_off_never_wakes():
    # Stock display-off goes through c79c..c8a8, never through the idle block.
    m = setup()
    block(m, 0x9D00C79C, 0x9D00C8A8)
    assert state(m)[2] == 0 and flag(m) == 0
    before = list(m.frames)
    for text in (b"01-02-03", b"01-02-04"):
        accepted(m, text=text)
        for _ in range(5):
            service_only(m)
            idle(m)
    assert m.frames == before and state(m)[2] == 0
    return dict(cases=2, stayed_dark=True)


def stale_record_cannot_outlive_stock_on():
    # idle blank -> user wakes via stock -> user turns off explicitly -> SET must stay dark.
    m = setup()
    idle_blank_no_background(m)
    assert flag(m) == 1
    block(m, 0x9D009AC4, 0x9D009ACC)  # stock resume latch writer: display on
    assert state(m)[2] == 1
    idle(m)  # one ordinary pass retires the record
    assert flag(m) == 0
    block(m, 0x9D00C79C, 0x9D00C8A8)  # explicit off
    assert state(m)[2] == 0
    before = list(m.frames)
    accepted(m, text=b"12-34-56")
    for _ in range(5):
        service_only(m)
    assert m.frames == before and state(m)[2] == 0
    return dict(stayed_dark=True)


def clear_never_wakes():
    # Synthetic: ownership held, display dark with the record set. CLEAR must not wake.
    m = setup()
    accepted(m)
    m.u.mem_write(0x50, p32(0))
    m.u.mem_write(FLAG, p32(1))
    before = list(m.frames)
    accepted(m, 2)
    for _ in range(3):
        service_only(m)
    assert state(m)[2] == 0 and m.frames == before
    return dict(stayed_dark=True)


def inhibited_wake_waits_for_service():
    # Record set but standby-like power state: SET stores, no wake. Once the
    # state returns to normal, the service path performs the same wake.
    m = setup()
    idle_blank_no_background(m)
    m.u.mem_write(0x89F0, b"\x03")
    before = list(m.frames)
    accepted(m, text=b"12-34-56")
    for _ in range(3):
        service_only(m)
    assert m.frames == before and state(m)[2] == 0 and flag(m) == 1
    m.u.mem_write(0x89F0, b"\x01")
    service_only(m)
    assert state(m)[2] == 1 and m.frames[-1] == expected(b"12-34-56")
    return dict(deferred_wake=True)


def v1_unchanged_by_record():
    # Legacy API v1 keeps its inherited foreground wake semantics and must
    # behave identically whether or not the idle-blank record is set.
    outcomes = []
    for record in (0, 1):
        m = setup()
        idle_blank_no_background(m)
        m.u.mem_write(FLAG, p32(record))
        r = request(m, packet(1, b"HELLO"))
        outcomes.append((r[0][7], state(m), [f.hex() for f in m.frames]))
    assert outcomes[0] == outcomes[1], outcomes
    return dict(v1_identical_with_and_without_record=True, v1_status=outcomes[0][0])


if __name__ == "__main__":
    r = dict(
        auto_blank=auto_blank(),
        explicit_toggle=explicit_toggle(),
        wake_after_idle_blank=wake_after_idle_blank(),
        explicit_off_never_wakes=explicit_off_never_wakes(),
        stale_record_cannot_outlive_stock_on=stale_record_cannot_outlive_stock_on(),
        clear_never_wakes=clear_never_wakes(),
        inhibited_wake_waits_for_service=inhibited_wake_waits_for_service(),
        v1_unchanged_by_record=v1_unchanged_by_record(),
    )
    save("provenance-targeted.json", r)
    print(json.dumps(r, indent=2))
