"""Stock instruction entry/GET_REPORT proof. Peripheral/flash helpers INTERCEPTED.
This tests software routing only, not physical recovery or real persistent writes.
"""

import struct, json
from unicorn.mips_const import *
from fixture_data import OUT, S
from unicorn import (
    UC_ARCH_MIPS,
    UC_HOOK_CODE,
    UC_MODE_LITTLE_ENDIAN,
    UC_MODE_MIPS32,
    Uc,
)

B = S
STOP = 0x9D004000


def mips(iface, cmd, shift=False, run_task=True):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(0x1D000000, 0x40000)
    u.mem_write(0x1D004180, B)
    u.mem_map(0, 0x10000)
    u.mem_map(0x1F800000, 0x100000)
    u.reg_write(UC_MIPS_REG_GP, 0xA0008020)

    def call(a):
        u.reg_write(UC_MIPS_REG_SP, 0xA000F000)
        u.reg_write(UC_MIPS_REG_RA, STOP)
        u.emu_start(a, STOP, count=500000)
        assert u.reg_read(UC_MIPS_REG_PC) == STOP, hex(u.reg_read(UC_MIPS_REG_PC))

    # Actual USB setup packet staging (bmRequestType,bRequest,wValue,wIndex,wLength).
    u.mem_write(0x474, struct.pack("<BBHHH", 0x21, 9, 0x0200, iface, 64))
    report = (b"\0" if shift else b"") + bytes([0x3F, cmd])
    u.mem_write(0x50A0, report.ljust(64, b"\0"))
    ops = []
    trace = []
    ticks = 0

    def hook(uc, a, size, _):
        nonlocal ticks
        args = [
            uc.reg_read(r) for r in [UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2]
        ]
        if a in [0x9D019D28, 0x9D019E44, 0x9D02FC34, 0x9D02FC68]:
            trace.append(hex(a))
        if a == 0x9D039368:
            ops.append(["flash_read", hex(args[0])])
        elif a == 0x9D039474:
            uc.mem_write(args[0] & 0x1FFFFFFF, b"\xff" * args[1])
        elif a == 0x9D0393FC:
            pass
        elif a == 0x9D038FB8:
            ops.append(["flash_write", hex(args[0]), args[1]])
        elif a == 0x9D039580:
            raise AssertionError("Unexpected erase in empty flash fixture")
        elif a in [
            0x9D01C76C,
            0x9D0374A4,
            0x9D02F1BC,
            0x9D0213F4,
            0x9D02530C,
            0x9D03D46C,
        ]:
            ops.append(["intercepted_helper", hex(a)])
            uc.reg_write(UC_MIPS_REG_V0, 1)
        elif a == 0x9D03FB54:
            ticks = 0
        elif a == 0x9D03FB4C:
            ticks += 40000000
            uc.reg_write(UC_MIPS_REG_V0, ticks)
        elif a == 0x9D0069D8:
            ops.append(["reset_requested", args[0]])
        else:
            return
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    u.hook_add(UC_HOOK_CODE, hook)
    call(0x9D0192F4)
    state = u.reg_read(UC_MIPS_REG_V0)
    queued = int.from_bytes(u.mem_read(0x496, 2), "little")
    if run_task:
        call(0x9D00CBAC)
    return dict(
        interface=iface,
        opcode=hex(cmd),
        shifted=shift,
        setup_return=state,
        queued=hex(queued),
        flag=u.mem_read(0x230, 1)[0],
        trace=trace,
        ops=ops,
    )


def query_get_report(iface, cmd, shift=False, run_task=True, initial_flag=0):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(0x1D000000, 0x40000)
    u.mem_write(0x1D004180, B)
    u.mem_map(0, 0x10000)
    u.mem_map(0x1F800000, 0x100000)
    u.reg_write(UC_MIPS_REG_GP, 0xA0008020)

    def call(a):
        u.reg_write(UC_MIPS_REG_SP, 0xA000F000)
        u.reg_write(UC_MIPS_REG_RA, STOP)
        u.emu_start(a, STOP, count=500000)
        assert u.reg_read(UC_MIPS_REG_PC) == STOP, hex(u.reg_read(UC_MIPS_REG_PC))

    # Actual USB setup packet staging (bmRequestType,bRequest,wValue,wIndex,wLength).
    u.mem_write(0x474, struct.pack("<BBHHH", 0x21, 9, 0x0200, iface, 64))
    report = (b"\0" if shift else b"") + bytes([0x3F, cmd])
    u.mem_write(0x50A0, report.ljust(64, b"\0"))
    u.mem_write(0x230, bytes([initial_flag]))
    ops = []
    trace = []
    ticks = 0

    def hook(uc, a, size, _):
        nonlocal ticks
        args = [
            uc.reg_read(r) for r in [UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2]
        ]
        if a in [0x9D019D28, 0x9D019E44, 0x9D02FC34, 0x9D02FC68]:
            trace.append(hex(a))
        if a == 0x9D039368:
            ops.append(["flash_read", hex(args[0])])
        elif a == 0x9D039474:
            uc.mem_write(args[0] & 0x1FFFFFFF, b"\xff" * args[1])
        elif a == 0x9D0393FC:
            pass
        elif a == 0x9D038FB8:
            ops.append(["flash_write", hex(args[0]), args[1]])
        elif a == 0x9D039580:
            raise AssertionError("Unexpected erase in empty flash fixture")
        elif a in [
            0x9D01C76C,
            0x9D0374A4,
            0x9D02F1BC,
            0x9D0213F4,
            0x9D02530C,
            0x9D03D46C,
        ]:
            ops.append(["intercepted_helper", hex(a)])
            uc.reg_write(UC_MIPS_REG_V0, 1)
        elif a == 0x9D03FB54:
            ticks = 0
        elif a == 0x9D03FB4C:
            ticks += 40000000
            uc.reg_write(UC_MIPS_REG_V0, ticks)
        elif a == 0x9D0069D8:
            ops.append(["reset_requested", args[0]])
        else:
            return
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    u.hook_add(UC_HOOK_CODE, hook)
    call(0x9D0192F4)
    state = u.reg_read(UC_MIPS_REG_V0)
    queued = int.from_bytes(u.mem_read(0x496, 2), "little")
    if run_task:
        call(0x9D00CBAC)
    response = bytes(u.mem_read(0x5578, 4))
    u.mem_write(0x474, struct.pack("<BBHHH", 0xA1, 1, 0x0100, iface, 16))
    call(0x9D0192F4)
    return dict(
        flag=u.mem_read(0x230, 1)[0],
        response=response.hex(),
        get_setup_return=u.reg_read(UC_MIPS_REG_V0),
        get_length=int.from_bytes(u.mem_read(0x8020 - 0x7BC8, 2), "little"),
        get_pointer=hex(int.from_bytes(u.mem_read(0x8020 - 0x7B9C, 4), "little")),
        ops=ops,
    )


results = []
for iface in (4, 3):
    for cmd in (0x80, 0x8E, 0x8F):
        x = mips(iface, cmd)
        assert x["setup_return"] == (5 if iface == 4 else 7)
        assert x["queued"] == ("0x0" if iface == 4 else hex(0x200 | (cmd & 15)))
        assert x["flag"] == int(iface == 3 and cmd == 0x8E)
        assert any(o[0] == "reset_requested" for o in x["ops"]) == (
            iface == 3 and cmd == 0x8F
        )
        results.append(x)
x = mips(3, 0x8E, shift=True)
assert x["queued"] == "0x0" and x["flag"] == 0
results.append(x)
for flag in (0, 1):
    x = query_get_report(3, 0x80, initial_flag=flag)
    assert (
        x["response"] == "3f800200"
        and x["get_length"] == 4
        and x["get_pointer"] == "0xa0005578"
        and x["get_setup_return"] == 2
    )
    assert x["flag"] == flag and all(
        o[0] == "intercepted_helper" and o[1] == "0x9d01c76c" for o in x["ops"]
    )
    results.append(x)
(OUT / "entry-proof.json").write_text(
    json.dumps(
        dict(
            all_passed=True,
            cases=len(results),
            hardware_accessed=False,
            results=results,
        ),
        indent=2,
    )
    + "\n"
)
print(len(results), "stock MIPS entry/GET_REPORT cases passed; not hardware proof")
