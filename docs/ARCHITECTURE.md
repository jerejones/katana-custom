# Design and research conclusions

## Stock firmware background

The Katana's front-panel controller is a PIC32-family microcontroller running
MIPS32 little-endian code. The official updater is a Windows executable whose
resources include the MCU application as an Intel HEX file; the application
occupies flash from physical `0x1d004180`, which the CPU sees at the cached
alias `0x9d004180`. The same updater also carries separate DSP and Bluetooth
images that this project never touches.

Over USB the device exposes several HID interfaces. Interface 4 carries the
normal vendor command family: 64-byte reports beginning `5a`, dispatched through
a 196-entry opcode table. Most of that table points at a default handler, which
is where this patch finds room. The stock table also contains raw memory read
and write opcodes; the Go client's packet allowlist exists so nothing but the
version query and the three API v2 operations can ever be sent. Interface 3 is
the consumer-control interface the official updater uses to enter the loader.

The display path inside the firmware is a queue of pending text, a wrapper that
copies it into a scratch buffer, a glyph renderer that maps bytes to segment
patterns through a 56-entry table, and a bit-banged GPIO routine that clocks the
segment bytes out to the display driver. Stock callers of that path include the
volume and source feedback, the scroller, and the idle auto-blank countdown.

This project targets stock version `2.0.171220.130` because that was the version
installed on the research device. Creative also published 2.1 and 2.6. Function
and variable addresses move between those releases, sometimes by a few bytes
and sometimes further, so every address in this repository is specific to 2.0.
A different installed version needs a fresh analysis, not an offset adjustment.

## Why not a profile-name clock?

The first visible HELLO used a stock profile-name setter. It worked visually but
mutated persistent profile state; restoration is not a justification for writing
settings once a second. That approach is retired. The project has no such setter.

Timed RAM API v1 avoided profiles but display leases expired, producing clock gaps
and races with stock feedback. A single-writer correction prevents IRQ/foreground
GPIO interleaving. The final API v2 instead remembers one background indefinitely
until CLEAR/reset, while stock feedback retains priority. Host ticking only
changes the remembered time; it is not a keepalive required for persistence.

## How the design evolved

The research went through several designs before the one shipped here. Each
step was built as real MIPS code, executed under Unicorn against the stock
image, and in several cases flashed and observed on hardware.

1. **Profile-name HELLO.** Reused the stock profile rename command. Visible on
   the glass, but it writes profile metadata to flash. Retired.
2. **Timed RAM API v1.** A new opcode showed text for a short stock-controlled
   duration. Review found that borrowing the stock duration byte could strand it
   after an idle blank, wedging later requests until stock activity repaired it.
3. **Wake-on-SET and private lease.** Fixed the already-off case and moved the
   countdown into private RAM, but the display still blanked between ticks.
4. **Single writer.** Interrupt-driven stock feedback could interleave GPIO
   writes with a custom frame. The fix serializes all display output through
   one writer with a bounded copied-frame FIFO and immutable generation tokens.
5. **Persistent background API v2.** The text is remembered until CLEAR or reset
   and re-shown whenever stock work drains; no host refresh is needed.
6. **Idle provenance.** The stock auto-blank countdown cleared the same latch as
   an explicit display-off. Rewriting the auto-blank decision in place lets a
   present background survive idle expiry without ever waking a display the user
   turned off. This is the image the research trials ran on hardware.
7. **Wake after idle blank.** In practice the display had usually idled off
   before the clock started, so the text stayed invisible until a volume press.
   The rewritten idle block now records when it is the one darkening the
   display, and SET wakes the display only while that record stands. Any pass
   that finds the display on erases the record, so an explicit off still wins.
   The wake performs the stock display-on writes: latch word and idle countdown
   re-armed from the configured timeout. This is the image built by this
   repository. It passed offline qualification and was flashed once; the wake
   was observed on the display.

## Source layout and executable placement

`endpoint.S` contains sparse original opcode dispatch, strict source/length/magic/
glyph validation, v1 compatibility, v2 state, generation tokens, renderer gates
and one serialized physical display writer. `glyph-small.S` replaces duplicate
stock glyph classification with compact equivalent lookup. `idle-provenance.S`
rewrites only the stock auto-idle countdown's specific blanking decision.
`placement.py` splits readable source into exact populated spans:

| Virtual address | Used bytes | Role |
|---|---:|---|
| `0x9d02b868` | 800 | dispatch/front/initialization/qualification/table/jump stub |
| `0x9d036e14` | 152 | compact glyph classifier |
| `0x9d036eac` | 1176 | ownership/event/writer/background logic |
| `0x9d00e35c` | 144 | provenance-specific idle blank replacement |

The first three fit the 2140-byte combined existing dispatch/glyph budget
(2128 used). The idle block uses a separately populated 144-byte span. Startup,
service, renderer, segment bridge, stack epilogue and stock event hooks add the
remaining tiny patches. All code is placed inside spans the stock image already
populates; no erased flash region or extension area is used. The builder verifies the populated-byte domain and exact inverse.
CPU placement audit checks bounded external branches, literal aliases and LUI
pairs; arbitrary computed indirect targets are not exhaustively excluded.

## Ownership, races and electrical serialization

Stock events invalidate custom ownership even if a timer/latch happens to equal
its previous value. Immutable generation tokens travel through render snapshots
so an older SET or expiry cannot publish after a newer SET/CLEAR/stock takeover.
Only short metadata transactions mask interrupts; the original display GPIO
loops execute with restored prior Status. Copying complete frames into a bounded
FIFO prevents reentrant callers from sharing mutable scratch or interleaving
bit-banged frames. Four queued stock frames are bounded; overflow coalesces under
an explicit policy rather than claiming unlimited losslessness.

The service hook waits until the stock pending/scroller/feedback machinery yields
before rendering the latest background. CLEAR forgets ownership; if stock owns
the display, CLEAR must not overwrite its frame with blanks. If already off or in
standby/inhibited modes, SET stores without wake. The idle patch observes the
**cause** of a would-be blank (the actual stock idle path) rather than inferring
it from an ambiguous off snapshot. It preserves only an already-enabled, normal,
uninhibited V2 background and preserves every other stock countdown exclusion.

## RAM and code proof boundaries

Startup lowers the stack top to `0xa000ff00`, reserving the top region through
`0xa000fff8`; private state initialization and stock CRT preservation are tested.
The logical state occupies 152 bytes within the carve, with argument-home and
other reserved space. Static initializer audits and sampled stack descent are
not proofs of whole-device worst-case stack depth. No exact silicon-part identity,
complete shadow-register-set model, long-duration high-water run or all-audio-mode
deadline proof is claimed.

The CPU harness executes actual stock+patch MIPS instructions. It intercepts
specified USB/display/peripheral/task boundaries. Electrical tests run stock GPIO
loops with synthetic Count helpers and inspect frame transactions/interrupt
admission. Unicorn memory-write hooks were found to perturb some MIPS delay-slot
execution, so key store audits decode executed instructions in code hooks. Never
turn these fixture results into claims of wall-clock hardware timing.

## Packaging and host boundaries

The exact stock HEX record type/length/address/order/coverage/line endings are
retained, not merely a normalized address map. Changed data checksums are rebuilt;
non-data records remain byte-identical. Vendor input and output hashes are pinned.
LLVM independently assembles all units; the small encoder is a crosscheck/placement
helper, not the only source of instruction bytes.

The runtime client can send only base-version query and v2 STATUS/SET/CLEAR. The
flasher is a separate program with serial/image/script/physical-lineage evidence
and durable one-attempt markers. HID4 runtime traffic and HID3 maintenance traffic
cannot be swapped. Neither the clock nor its library imports the flasher.
