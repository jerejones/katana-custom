from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *
from fixture_data import OUT, S, build
from machine import CPU_REG, SP, STOP, p32
from probe import block, lease, machine, request, state, stubs, turn
import json

CPU = CPU_REG


class Boundary:
    def __new__(cls, m):
        if hasattr(m, "boundary"):
            return m.boundary
        return super().__new__(cls)

    def __init__(self, m):
        if hasattr(m, "boundary"):
            return
        m.boundary = self
        self.m = m
        self.cp = {(12, 0): 1, (12, 2): 0, (14, 0): STOP}
        self.log = []
        self.pending = []
        self.return_pc = None
        m.u.hook_add(UC_HOOK_CODE, self.hook)

    def hook(self, u, a, size, data):
        for addr, value in self.pending:
            u.mem_write(addr, p32(value))
        self.pending = []
        w = int.from_bytes(u.mem_read(a & 0x1FFFFFFF, 4), "little")
        rs = (w >> 21) & 31
        rt = (w >> 16) & 31
        rd = (w >> 11) & 31
        sel = w & 7
        nextpc = a + 4
        prev = int.from_bytes(u.mem_read((a - 4) & 0x1FFFFFFF, 4), "little")
        if prev & 0xFC1FFFFF == 8:
            nextpc = u.reg_read(CPU[(prev >> 21) & 31])
        if w == 0x42000018:
            self.cp[12, 0] &= ~2
            u.reg_write(UC_MIPS_REG_PC, self.cp[14, 0])
            return
        if w in (0x415DE800, 0x41DDE800):
            # rdpgpr/wrpgpr sp,sp: same bank fixture CSS=PSS=0.
            u.reg_write(UC_MIPS_REG_PC, nextpc)
            return
        if w >> 26 == 16 and rs in (0, 4):
            if rs == 0:
                u.reg_write(CPU[rt], self.cp.get((rd, sel), 0))
            else:
                self.cp[rd, sel] = u.reg_read(CPU[rt])
                self.log.append([hex(a), rd, sel, hex(self.cp[rd, sel])])
            u.reg_write(UC_MIPS_REG_PC, nextpc)
            return
        if w & 0xFFE0FFFF in (0x41606000, 0x41606020):
            old = self.cp[12, 0]
            u.reg_write(CPU[rt], old)
            self.cp[12, 0] = (old | 1) if w & 32 else old & ~1
            self.log.append([hex(a), "ei" if w & 32 else "di", hex(self.cp[12, 0])])
            u.reg_write(UC_MIPS_REG_PC, nextpc)
            return
        if w >> 26 == 43:
            imm = w & 65535
            imm -= 65536 if imm & 32768 else 0
            addr = (u.reg_read(CPU[rs]) + imm) & 0x1FFFFFFF
            value = u.reg_read(CPU[rt])
            if 0x1F881000 <= addr < 0x1F881200 or 0x1F805100 <= addr < 0x1F805160:
                low = addr & 15
                base = addr - low
                if low in (4, 8, 12):
                    old = int.from_bytes(u.mem_read(base, 4), "little")
                    self.pending.append(
                        (
                            base,
                            (
                                old & ~value
                                if low == 4
                                else old | value if low == 8 else old ^ value
                            ),
                        )
                    )
            if addr == 0x1F885200:
                old = int.from_bytes(u.mem_read(addr, 4), "little")
                self.pending.append((addr, old & ~value))

    def irq(self, entry, priority):
        u = self.m.u
        s = self.cp[12, 0]
        assert s & 1 and not s & 6 and priority > ((s >> 10) & 7), (hex(s), priority)
        regs = [
            u.reg_read(r)
            for r in CPU[1:26]
            + [UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_FP, UC_MIPS_REG_RA]
        ]
        self.cp[14, 0] = STOP
        self.cp[12, 0] = s | 2
        self.m.run(entry)
        assert self.cp[12, 0] == s
        assert regs == [
            u.reg_read(r)
            for r in CPU[1:26]
            + [UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_FP, UC_MIPS_REG_RA]
        ]


def event(event_word=0x40, feedback=False, near_expiry=False, endpoint=True):
    m = machine()
    if not endpoint:
        m.u.mem_write(build.BASE & 0x1FFFFFFF, S)
    m.run(0x9D005070, 0x9D005118)
    m.u.mem_write(0x50, p32(0))
    m.u.mem_write(0x1B8, p32(0xA000E000))
    m.u.mem_write(0x89F0, b"\1")
    m.u.mem_write(0x89CE, b"\1")
    m.u.mem_write(0x1E1, b"\xff")
    if feedback:
        m.u.mem_write(0x2B8, p32(1))  # stock volume-feedback enable read at 7d84
    if endpoint:
        request(m)
    else:
        m.u.mem_write(0x50, p32(1))
    if near_expiry:
        turn(m)
        turn(m)
    m.u.mem_write(0x89F5, b"\0")
    m.u.mem_write(0x2A8, p32(event_word))
    m.u.mem_write(0x28C, b"\1")
    tail_state = None
    consumer_state = None
    block(m, 0x9D00B0C8, 0x9D00B120)
    m.intercepts = set()  # no main-task bypasses
    b = Boundary(m)
    m.break_at = 0x9D038B00
    m.u.reg_write(UC_MIPS_REG_RA, STOP)
    m.u.reg_write(UC_MIPS_REG_SP, SP)
    start = len(m.visited)
    pc = 0x9D00B5AC
    completions = []
    for outer in range(20):
        try:
            m.u.emu_start(pc, STOP, count=100000)
        except Exception:
            print("last", [hex(x) for x in m.visited[-100:]], "cp", b.log[-10:])
            raise
        pc = m.u.reg_read(UC_MIPS_REG_PC)
        if pc == STOP:
            if consumer_state is None:
                consumer_state = dict(
                    state=state(m),
                    lease=lease(m),
                    pending=hex(int.from_bytes(m.u.mem_read(0x234, 4), "little")),
                    work=hex(int.from_bytes(m.u.mem_read(0x2B4, 4), "little")),
                )
            if feedback:
                # Consume the actual volume/control work queued by the event consumer.
                if near_expiry:
                    # Main loop calls feedback at d42c BEFORE event consumer at d938;
                    # same-pass expiry is later at dea0. Do not run next-pass feedback first.
                    m.intercepts = stubs
                    m.u.mem_write((SP - 128 + 0x10) & 0x1FFFFFFF, p32(100))
                    block(m, 0x9D00D940, 0x9D00D3CC)
                    tail_state = dict(
                        state=state(m),
                        lease=lease(m),
                        frame=m.frames[-1].hex() if m.frames else None,
                    )
                    m.intercepts = set()
                    m.break_at = 0x9D038B00
                feedback = False
                pc = 0x9D007544
                m.u.reg_write(UC_MIPS_REG_RA, STOP)
                m.u.reg_write(UC_MIPS_REG_SP, SP)
                continue
            break
        assert pc == 0x9D038B00, hex(pc)
        m.break_at = None
        for k in range(100):
            if m.u.mem_read(0x1F1, 1) == m.u.mem_read(0x1F2, 1):
                break
            # One hardware I2C master completion: ACK received; no collision;
            # input data=0. Let the actual ISR own RAM state/queue advancement.
            m.u.mem_write(0x1F881040, p32(1 << 29))
            m.u.mem_write(0x1F805110, p32(0))
            m.u.mem_write(0x1F805160, p32(0))
            before = list(m.u.mem_read(0x1F0, 5)) + list(m.u.mem_read(0x3DC, 1))
            b.irq(0x9D037DF0, 3)
            after = list(m.u.mem_read(0x1F0, 5)) + list(m.u.mem_read(0x3DC, 1))
            completions.append([before, after])
        else:
            raise AssertionError("I2C completion bound")
        # Execute original wait condition, not a forced branch/result.
        m.break_at = None
        m.u.emu_start(pc, 0x9D038B14, count=20)
        assert m.u.reg_read(UC_MIPS_REG_PC) == 0x9D038B14
        pc = 0x9D038B14
        m.break_at = 0x9D038B00
    else:
        raise AssertionError("event completion bound")
    m.break_at = None
    result = dict(
        consumer_state=consumer_state,
        tail_state=tail_state,
        completed=pc == STOP,
        state=state(m),
        lease=lease(m),
        frames=[x.hex() for x in m.frames],
        event_word=int.from_bytes(m.u.mem_read(0x2A8, 4), "little"),
        instructions=len(m.visited) - start,
        intercepts=list(m.intercepted),
        completions=completions,
        cp0=b.log,
        visited=[hex(x) for x in m.visited[start:]],
    )
    m.intercepts = stubs
    result["turns"] = [turn(m) for _ in range(3)]
    result["final_frame"] = m.frames[-1].hex()
    return result


if __name__ == "__main__":
    try:
        r = event()
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise
    (OUT / "event.json").write_text(json.dumps(r, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in r.items() if k not in ("visited", "cp0", "completions")},
            indent=2,
        )
    )
    print("I2C interrupts", len(r["completions"]))
