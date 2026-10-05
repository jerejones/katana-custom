# Safety contract and limitations

**You can permanently brick the Katana.** No guaranteed recovery for a device
that no longer boots is known here. There is no complete bootloader/configuration/
calibration backup. The official updater's app payload is not such a backup.
Stable mains/USB power and a short direct cable reduce risks; they do not remove
them. Do not begin if you cannot accept loss of the device.

## Supported boundary

Original Sound BlasterX Katana **MF8245**, normal USB `041e:3247`, revision
`0200`, stock MCU `2.0.171220.130` and exact supplied image hashes only. V2, V2X,
130B, other app layouts and other MCU variants are outside scope. MIPS32LE code
was executed in bounded tests; the precise PIC32 model has not been established.
No claim of compatibility follows merely from a similar product name.

The flasher writes the MCU application from stock-layout Intel HEX. It does not
write DSP, ATS, Bluetooth, boot flash or arbitrary addresses. It has no automatic
retry, resume-at-record, fallback firmware, forced reset or generic memory writer.
A normal firmware version reply is not a complete installed-image readback.

## Why recovery is unproven

The research established these facts about the update path, all by executing
stock code offline and reading the official updater, none by experiment on a
bricked device:

- The updater enters the loader by sending maintenance commands `3f 8e` then
  `3f 8f` on HID interface 3, then waits for a different USB identity,
  `041e:30cf`. Sending those bytes on interface 4 does nothing; the stock
  firmware ignores them there. An early research attempt made exactly that
  mistake, which is why the flasher pins the interface.
- Entering update mode is not read-only. The `3f 8e` handler appends a byte to a
  log in external serial flash and can erase a sector of it when the log fills.
- The loader's erase command carries no address range. Nothing in the
  application image says what the loader erases or whether the loader protects
  itself.
- No official package contains the bootloader, boot configuration or
  calibration data. There is no file to restore them from.
- The hidden service menu reached by pressing both volume buttons together offers a
  Bluetooth update request, a destructive factory reset and three diagnostic
  screens. It has no MCU recovery entry. Do not use it as a probe.

## Runtime policy

- API v2 owns one **global** RAM background slot. Not per process, client or host.
- SET remembers the latest text while stock feedback wins; it resumes afterward.
- CLEAR stops background ownership without destroying current stock feedback.
- Explicit stock off/standby wins. SET while the user has switched the display
  off stores text and does not wake; normal stock activity (e.g. volume) can wake
  and subsequently show it.
- SET wakes the display only when the stock idle timeout darkened it. The idle
  block records that fact when it blanks, and every pass that sees the display
  on erases the record, so an explicit off can never be mistaken for idle. The
  wake performs the same two writes as the stock display-on paths and nothing
  else. CLEAR and legacy v1 requests do not consult the record.
- The auto-idle-blank suppression only preserves an already-on, eligible display
  with a V2 background.
- No profiles or persistent names are modified by the runtime library/clock.
- A healthy clock exit clears; process crash/power loss can leave frozen time
  until CLEAR/reset. A stopped clock is not a reliable time source.
- Queue draining and ACK/STATUS checking are not authentication or replay
  protection. There is no transaction nonce. Stop all competing writers.
- Shared local `flock` works only for cooperating processes using the same state
  directory/user-visible inode. Other hosts, applications and alternative state
  directories are outside that lock. Do not run multiple writers.

## Known evidence limits

Research trials visually confirmed the final background and preserved normal
audio/control use, but not every audio mode, DSP function, peripheral or lifetime
condition. CPU tests intercept peripherals and unrelated tasks; synthetic Count
increments do not prove audio deadlines. IRQ admission checks do not prove every
nested IRQ schedule. Whole-device worst-case stack depth, long-term SRAM safety
and computed indirect entry exclusion are not exhaustive. Static headroom is not
a proven available stack budget. The copied-frame queue has bounded capacity and
explicit coalescing on overflow, not lossless unlimited delivery.

The host code and the current image have had one bounded live trial on one
device (VERIFICATION.md). That is not permission for unattended deployment on
another unit. Follow independent review and one-attempt gates.

## On failure

Do not reflash automatically. Preserve the durable journal, approval hashes,
entry/capture evidence, consumed markers and uninterrupted physical-device custody.
An error at reset can mean the device already reset successfully. `USBError 19`
(device disappeared) at final `8f` was observed in successful research trials;
it remains ambiguous, not blanket success. Use the separate **verify-return**
step after access/reenumeration review, not another transfer. If evidence does not
establish what happened, stop. Keep power stable and seek informed review.
