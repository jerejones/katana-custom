# Deliberate, separately gated original-Katana flashing

**Read SAFETY.md first. Permanent brick risk; no guaranteed nonboot recovery.**
This page is a manual operator workflow, not an instruction to run all stages
unattended. The repository ships no approval file and no consumed attempt
marker; you create those yourself, one reviewed attempt at a time.

The tool is `tools/katana_flash.py`. `--help` and default/`--inspect` are offline.
All other modes access hardware, including read-only qualification/capture/return
verification. Every live stage requires an exact serial, supplied stock HEX,
independently reviewed approval hash and stable-power/cable acknowledgement.
It refuses root and performs no permission changes or driver detaches.

## 1. Build, audit and inspect offline

Finish BUILD.md, including CPU and host tests. In Bash, define actual local paths:

```sh
PY=.venv/bin/python
STOCK="$KATANA_STOCK_HEX"
IMAGE="$PWD/build/firmware/candidate.hex"
SERIAL=YOUR_DEVICE_SERIAL
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/katana-custom"
# Pick an absolute private durable directory for THIS reviewed attempt.
# Do not reuse, delete, reset, or transplant an earlier attempt's markers.
ATTEMPT=/absolute/private/katana-attempt
mkdir -m 700 "$ATTEMPT"
COMMON=("$IMAGE" --stock-hex "$STOCK" --serial "$SERIAL" --state-dir "$STATE" --attempt-dir "$ATTEMPT")
"$PY" tools/katana_flash.py "${COMMON[@]}" --inspect > "$ATTEMPT/inspection.json"
```

Review the exact image SHA256s, all changed spans, coverage, base/end, record
layout, pre/post versions and target serial. Compare to `firmware/reference-image.json`.
The inspection is **not approval**. An independent reviewer/operator must decide
whether to adopt those exact bytes as approval. Preserve the reviewed JSON
unchanged as `approval.json` and calculate its SHA256, e.g. `sha256sum`.
Do not edit fields to bypass a failed check. Both versions stay `2.0.171220.130`.

```sh
APPROVAL="$ATTEMPT/approval.json"
APPROVAL_SHA=REVIEWED_LOWERCASE_SHA256
LIVE=("${COMMON[@]}" --approval "$APPROVAL" --approval-sha256 "$APPROVAL_SHA" --physical-ready short-direct-cable-stable-power)
```

The schema binds image bytes, serial, stock identity, versions and complete
changes. It is not a digital signature or proof that somebody actually reviewed
it. Live evidence additionally binds the exact flasher script hash.

## 2. Read-only maintenance qualification (live)

Stop clock/other writers, arrange USB access and separate HID3/HID4 opt-ins.

```sh
"$PY" tools/katana_flash.py "${LIVE[@]}" --qualify-maintenance
```

This checks normal HID4/version, then maintenance HID3 descriptor/version (`0200`)
and matching physical port identity. No entry/erase command is sent. Record the
returned `maintenance-qualified-*.json` path and SHA256 after reviewing them.

## 3. Enter loader once (persistent)

```sh
QUALIFICATION=/absolute/path/from-qualification-output.json
QUALIFICATION_SHA=REVIEWED_LOWERCASE_SHA256
"$PY" tools/katana_flash.py "${LIVE[@]}" --enter-loader --accept-permanent-brick \
  --qualification "$QUALIFICATION" --qualification-sha256 "$QUALIFICATION_SHA"
```

Fresh qualification must equal the pinned evidence. `ENTRY-PENDING.json` is
exclusively created and fsynced **before** sending maintenance `3f 8e`, then
`3f 8f` via HID **3**. The normal HID4 `5a` path is not interchangeable. No Windows
leading Report-ID zero is sent. Enter-loader then attempts loader capture.
A USB disappearance/access failure can interrupt that capture after entry already
happened. **Do not rerun entry.** The saved entry intent contains the physical
bus/port chain and its SHA256 is printed before mutation.

Before proceeding, define the entry evidence for **both** outcomes: successful
loader capture and interrupted capture. Use the entry-intent SHA256 printed by
stage 3, not the separate loader-capture SHA256:

```sh
ENTRY="$ATTEMPT/ENTRY-PENDING.json"
ENTRY_SHA=RECORDED_ENTRY_INTENT_LOWERCASE_SHA256
```

Keep these variables for transfer and return verification. Do not repeat entry.

## 4. Capture/review the observed loader, no erase

If stage 3 did not capture successfully, fix only the separately reviewed new
node access and run only this capture command using the existing entry evidence:

```sh
"$PY" tools/katana_flash.py "${LIVE[@]}" --capture-loader \
  --entry-evidence "$ENTRY" --entry-evidence-sha256 "$ENTRY_SHA" --same-physical-device
```

If enter-loader already captured it, use that evidence; do not needlessly repeat
capture. Review `041e:30cf`, physical port custody, observed HID descriptors,
report sizes and loader version (`0100` observed). The loader lacks the normal
serial assurance; physical lineage and uninterrupted custody are essential.
The tool does not assume a fixed report size; it checks the observed 16/64-byte
consumer HID shape and pins exact descriptor/configuration/endpoint evidence.

## 5. One transfer, no retry

```sh
LOADER=/absolute/path/from-loader-capture-output.json
LOADER_SHA=REVIEWED_LOWERCASE_SHA256
"$PY" tools/katana_flash.py "${LIVE[@]}" --live-loader --accept-permanent-brick \
  --entry-evidence "$ENTRY" --entry-evidence-sha256 "$ENTRY_SHA" \
  --loader-evidence "$LOADER" --loader-evidence-sha256 "$LOADER_SHA" --same-physical-device
```

The exact current loader identity must equal the capture. `ATTEMPT-CONSUMED.json`
is fsynced before the transfer, even if it subsequently fails. The transfer checks
loader version, erases once (`81`), sends all **15326** original-layout HEX records
(`82`), finalizes (`8e`), then resets (`8f`). Durable per-record journaling can make
this take about five minutes; the total deadline is 20 minutes. Do not optimize
away fsync or interrupt a slow but progressing transfer.

USB output completion is the only confirmation the loader gives. There is no
firmware ACK, checksum readback or whole-image readback. No retries, resumed record index, second erase or recovery reset occur.
Stock erase uses interrupt OUT when the observed loader offers one; other
commands use SET_OUTPUT_REPORT. That distinction is intentional.

## 6. Separate verify-return, then API/visual checks

A final-reset `USBError 19` was observed after successful research transfers. It
is an **ambiguous disconnect**, not automatic failure or proof of success. Preserve
all logs/gates, review the last state, and verify return instead of reflashing:

```sh
"$PY" tools/katana_flash.py "${LIVE[@]}" --verify-return \
  --entry-evidence "$ENTRY" --entry-evidence-sha256 "$ENTRY_SHA" --same-physical-device
```

Arrange new normal-node access/HID4 binding first if needed. This checks the same
normal serial/revision/physical port and pinned app version. It cannot establish
installed code identity because the base version is unchanged. Then use a bounded
clock trial (README) which requires API-v2 STATUS before SET; visually confirm the
clock, stock volume → source feedback → latest clock, and healthy CLEAR. For the
wake behavior, also confirm: let the display idle off with no clock running, then
start the clock and check the display turns on by itself; switch the display off
with the stock control while the clock runs and check it stays off; turn it back
on and check the clock resumes. Verify
normal audio/control behavior yourself. Do not treat a remembered-text reply as
an image readback or complete regression test.

New attempts after a failure require human investigation and an explicitly new,
independently reviewed plan. Creating another directory to evade a consumed gate
is not recovery. Never delete markers as a routine troubleshooting step. Keep
approval/entry/loader evidence and journals together and private; they identify
your device and contain vendor image records. Do not commit or upload them.

Every evidence file also records the SHA256 of `tools/katana_flash.py` itself, and
later stages refuse evidence produced by a different script. Any edit to the
flasher, even reformatting, therefore invalidates evidence from an in-flight
attempt. Finish or abandon an attempt before changing the tool.
