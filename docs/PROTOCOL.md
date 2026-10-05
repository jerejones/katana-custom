# RAM-background API v2 wire specification

This describes bytes actually used by `protocol.go` and `firmware/endpoint.S`.
It is not a generic HID or Creative firmware API.

## Normal transport

USB VID/PID `041e:3247`, bcdDevice `0200`, active configuration 1, HID interface
**4**, alt 0; exactly one interrupt IN endpoint `0x84`, max packet 64. HID report
descriptor must equal:

```
0600ff0901a10119012940150026ff00750895408100190129409100c0
```

There is **no Report ID**. Send exactly 64 bytes via control SET_REPORT:
`bmRequestType=0x21, bRequest=9, wValue=0x0200, wIndex=4`. Receive a full 64-byte
interrupt report. Do not prepend the Windows API's synthetic leading zero.
The stock maintenance interface **3** is a different transport and opcode family.

Base ASCII-version request: `5a 07 01 02`, then 60 zero bytes. Reply begins
`5a 07 0f` plus `2.0.171220.130\0`. This unchanged string is not patch identity.
A successful strict API-v2 STATUS must precede SET.

## Requests

| Offset | Size | Meaning |
|---|---:|---|
| 0 | 1 | `5a` |
| 1 | 1 | command `7e` |
| 2 | 1 | logical payload length `0d` |
| 3 | 1 | operation: `00` STATUS, `01` SET, `02` CLEAR |
| 4..5 | 2 | ASCII `RD` (`52 44`) |
| 6..7 | 2 | little-endian API version `02 00` |
| 8..15 | 8 | SET text padded with ASCII spaces; STATUS/CLEAR send eight spaces |
| 16..63 | 48 | zero request padding |

Allowed endpoint glyph bytes: uppercase `A-Z`, `0-9`, space, `- + * #`.
No colon glyph: clock uses `HH-MM-SS`. The stock glyph implementation also maps
lowercase, but endpoint input validation is deliberately uppercase; the public
Go Set helper normalizes lowercase ASCII before encoding. O resembles 0, S
resembles 5, and `#` lights all segments. A dot/colon cannot be assumed usable.

## Replies and correlation

A successful v2 reply starts `5a 7e 0d 02 52 44 02 00`, followed by eight bytes of
**last accepted text**. Offset 3 is API version, not the request operation. Offset
7 must be zero. Initial reset STATUS can contain eight NULs; CLEAR returns eight
spaces. Bytes 16..63 are stock scratch and not API fields; do not interpret them.
The host requires full 64-byte USB reports, exact header/version and valid text.
It never falls back to API v1 or stock error/default replies.

Before each exchange the host drains at most 64 queued reports until a quiet
20-ms read timeout. A report flood stops before sending. It sends once, then
waits up to 2 seconds with bounded reads. Short writes, malformed reports,
uncorrelated headers, mismatching text or missing replies poison the connection.
SET/CLEAR requires both the immediate ACK and a separate matching STATUS.
There are no sequence numbers/nonces, so these checks cannot defeat a competing
writer or carefully timed stale replay. The process lock is local cooperation.

## Semantics

SET stores latest text even while stock feedback or off/standby inhibits rendering.
If the stock idle timeout darkened the display, SET also turns it back on; an
explicit stock display-off is never overridden. Only one global slot exists;
ACK does not mean visible. It persists without host
refresh until CLEAR/reset. Stock feedback temporarily supersedes it. CLEAR does
not erase stock feedback. Sending a new SET during feedback changes what resumes.

Legacy API v1 timed requests are still present for compatibility in firmware and
covered by tests, but new clients must use v2. Mixing v1 and v2 writers is outside
the single-owner contract. Profile-name setters are explicitly not an alternative.

Maintenance/flashing uses `3f` frames and interface 3 or the separately identified
loader, never normal interface 4. See FLASHING.md; never add maintenance packets
to the runtime client's allowlist.
