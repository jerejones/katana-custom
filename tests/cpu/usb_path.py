from events import Boundary
from fixture_data import OUT
from machine import SP, STOP, p32, packet
from probe import labels, lease, machine, request, state, stubs, turn
import json
from unicorn.mips_const import *  # UC_MIPS_REG_* constants


def initialized():
    m = machine()
    m.intercepts = set()
    m.run(0x9D005070, 0x9D005118)
    b = Boundary(m)
    b.cp[12, 0] = 0
    # Actual EI (including jr delay slot); stock USB initialization/attach.
    m.call(0x9D03FB5C)
    assert b.cp[12, 0] == 1
    m.call(0x9D01AC30)
    m.call(0x9D01B4B8)
    m.call(0x9D01C76C)
    assert int.from_bytes(m.u.mem_read(0x1F881070, 4), "little") & 4
    assert (int.from_bytes(m.u.mem_read(0x1F881100, 4), "little") >> 10) & 7 == 5
    assert int.from_bytes(m.u.mem_read(0x1F885210, 4), "little") & 8
    return m, b


def transaction(m, b, setup=False):
    u = m.u
    if setup:
        u.mem_write(0x474, bytes.fromhex("2109000303004000"))
        assert bytes(u.mem_read(0x5004, 4)) == p32(0x474)
        u.mem_write(0x5000, bytes([0x34, 0, 8, 0]))
        ustat = 0
    else:
        assert u.mem_read(0x499, 1) == b"\1"
        assert bytes(u.mem_read(0x500C, 4)) == p32(0x50A0)
        u.mem_write(0x50A0, bytes([0x27, 0x2A, 1]) + bytes(61))
        u.mem_write(0x5008, bytes([0x04, 0, 64, 0]))
        ustat = 4
    u.mem_write(0x1F885200, p32(8))
    u.mem_write(0x1F885240, p32(ustat))
    u.mem_write(0x1F881040, p32(4))
    begin = len(m.visited)
    b.irq(0x9D01AE14, 5)
    return [hex(a) for a in m.visited[begin:]]


def proof(window=None):
    m, b = initialized()
    setup = transaction(m, b, True)
    u = m.u
    u.mem_write(0x1B8, p32(0xA000E000))
    u.mem_write(0x50, p32(0))
    u.mem_write(0x89F0, b"\1")
    u.mem_write(0x89CE, b"\1")
    u.mem_write(0x1E1, b"\xff")
    if window in ("grant", "render_io"):
        u.mem_write(0x50A0, packet())
        u.reg_write(UC_MIPS_REG_A0, 1)
        u.reg_write(UC_MIPS_REG_A1, 0xA00050A0)
        u.reg_write(UC_MIPS_REG_RA, STOP)
        pc = labels["reply"] if window == "grant" else labels["writer_output"]
        m.break_at = pc
        u.emu_start(0x9D02B79C, STOP, count=10000)
        assert u.reg_read(UC_MIPS_REG_PC) == pc
        m.break_at = None
    elif window in ("blank", "blank_committed"):
        request(m)
        m.intercepts = stubs
        turn(m)
        turn(m)
        pc = labels["service"] if window == "blank" else labels["countdown"]
        # Keep unrelated scheduler stubs until the ownership window. Removing
        # them here enters stock I2C polling before service without completions.
        u.mem_write(0x3F6, b"\1")
        u.mem_write((SP - 128 + 0x10) & 0x1FFFFFFF, p32(99))
        u.reg_write(UC_MIPS_REG_SP, SP - 128)
        u.reg_write(UC_MIPS_REG_FP, SP - 128)
        m.break_at = pc
        u.emu_start(0x9D00D3E8, STOP, count=100000)
        assert u.reg_read(UC_MIPS_REG_PC) == pc
        m.break_at = None
    if window == "blank_committed":
        # An IRQ becoming pending at the old vulnerable decrement cannot be
        # admitted in this short transaction. Deliver at the FIRST unlock.
        assert b.cp[12, 0] & 1 == 0
        unlock = labels["expiry_output"] + 8
        m.break_at = unlock
        u.emu_start(pc, STOP, count=1000)
        assert u.reg_read(UC_MIPS_REG_PC) == unlock
        m.break_at = None
        pc = unlock
        assert b.cp[12, 0] & 1
    m.intercepts = set()  # Execute the real IRQ path, not scheduler task stubs.
    trace = transaction(m, b, False)
    queued = int.from_bytes(u.mem_read(0xFF30, 4), "little")
    frame = bytes(u.mem_read(0xFF40, 16)) if queued else m.frames[-1]
    assert frame != bytes(16)
    after = lease(m)
    stockstate = state(m)
    status = b.cp[12, 0]
    if window in ("grant", "render_io"):
        m.run(pc)
        resume = lease(m)
        frame_after_resume = m.frames[-1].hex()
        m.intercepts = stubs
        turns = [turn(m) for _ in range(3)]
    elif window in ("blank", "blank_committed"):
        m.intercepts = stubs
        # Preserve the interrupted registers and frame; do not reset SP/FP.
        m.break_at = 0x9D00D3CC
        u.emu_start(pc, STOP, count=100000)
        assert u.reg_read(UC_MIPS_REG_PC) == 0x9D00D3CC
        m.break_at = None
        resume = lease(m)
        frame_after_resume = m.frames[-1].hex()
        turns = [(*state(m), lease(m))]
    else:
        resume = lease(m)
        frame_after_resume = m.frames[-1].hex()
        turns = []
    return dict(
        frame_after_resume=frame_after_resume,
        frames=[f.hex() for f in m.frames],
        window=window,
        status_at_admission=hex(status),
        setup_trace=setup,
        irq_trace=trace,
        cp=b.log,
        stock_frame=frame.hex(),
        state_after_irq=stockstate,
        lease_after_irq=after,
        lease_after_resume=resume,
        turns=turns,
        final_frame=m.frames[-1].hex(),
        preserved=m.frames[-1] == frame,
        intercepts=list(m.intercepted),
        steps=len(m.visited),
    )


if __name__ == "__main__":
    rows = []
    for window in (None, "grant", "blank", "render_io"):
        try:
            rows.append(proof(window))
        except Exception as e:
            rows.append(dict(window=window, harness_error=repr(e)))
            (OUT / "usb-path.json").write_text(json.dumps(rows, indent=2) + "\n")
            raise
        (OUT / "usb-path.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(
        json.dumps(
            [
                {
                    k: v
                    for k, v in r.items()
                    if k not in ("setup_trace", "irq_trace", "cp", "intercepts")
                }
                for r in rows
            ],
            indent=2,
        )
    )
