#!/usr/bin/env python3
"""Original Katana PIC32-only installer. Default is offline --inspect.
No BIN-only input, no arbitrary address/opcode, no other components, no auto retry.
Linux adapter derived from hardware-tested research; portable refactor offline-tested only.
"""

from pathlib import Path
import argparse, contextlib, datetime, fcntl, hashlib, json, os, re, sys, time

ROOT = Path(__file__).resolve().parent.parent
HOME = None
STATE = None
SERIAL = None
VERSION = b"2.0.171220.130\0"
STOCK_SHA = "a119076916e5f90c39681cb26d2776d8cf2ea16f406af599a207cc48a525a91f"
STOCK_HEX = None
BASE = 0x1D004180
END = 0x1D03FB7C
NORMAL_DESC = bytes.fromhex(
    "0600ff0901a10119012940150026ff00750895408100190129409100c0"
)
MAINT_DESC = bytes.fromhex(
    "050c0901a10109e909ea1500250175019502810209b609b509cd09e209b3950581060900950181010900150026ff007508950f81020900150026ff007508954091020900150026ff0075089540b102c0"
)
VERSION_OFFSET = 0x3B6E0
STOCK_VERSION = VERSION.decode().rstrip("\0")


def version_bytes(value):
    need(
        isinstance(value, str)
        and re.fullmatch(r"2\.0\.171220\.[0-9]{3}", value) is not None,
        "Only fixed-width matching-stock ASCII versions accepted",
    )
    return value.encode("ascii") + b"\0"


def need(ok, msg):
    if not ok:
        raise RuntimeError(msg)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def parse_hex(blob):
    need(len(blob) < 2000000, "HEX too large")
    records = []
    mapping = {}
    upper = None
    eof = False
    for line in blob.decode("ascii").splitlines():
        need(not eof, "Records/blank lines after EOF")
        need(re.fullmatch(r":[0-9A-Fa-f]+", line) is not None, "Invalid HEX syntax")
        r = bytes.fromhex(line[1:])
        need(5 <= len(r) <= 61, "HEX record does not fit HID report")
        n, kind = r[0], r[3]
        addr = int.from_bytes(r[1:3], "big")
        need(n + 5 == len(r) and sum(r) % 256 == 0, "HEX length/checksum")
        if kind == 4:
            need(n == 2 and addr == 0, "Malformed ELA")
            upper = int.from_bytes(r[4:6], "big") << 16
            need(
                upper in (0, 0x1D000000, 0x1D010000, 0x1D020000, 0x1D030000),
                "ELA outside original app",
            )
        elif kind == 0:
            need(upper is not None and n > 0, "Data without ELA/zero length")
            for i, v in enumerate(r[4:-1]):
                a = upper + addr + i
                need(
                    BASE <= a < END and a not in mapping,
                    "Out-of-app or overlapping byte",
                )
                mapping[a] = v
        elif kind == 1:
            need(n == 0 and addr == 0, "Malformed EOF")
            eof = True
        else:
            raise RuntimeError("Only stock types 00/01/04 permitted")
        records.append(r)
    need(eof and mapping, "Missing EOF/data")
    binary = bytes(mapping.get(a, 255) for a in range(BASE, END))
    return records, mapping, binary


def inspect(blob, pre_version=STOCK_VERSION, post_version=STOCK_VERSION):
    version_bytes(pre_version)
    post = version_bytes(post_version)
    records, m, binary = parse_hex(blob)
    sr, sm, sb = parse_hex(STOCK_HEX.read_bytes())
    need(sha(sb) == STOCK_SHA, "Untouched stock source mismatch")
    need(
        m.keys() == sm.keys(),
        "Candidate coverage must equal original source; no gap/boot additions",
    )
    # Preserve record order/layout too; PIC32 streaming parser is stateful.
    need(
        len(records) == len(sr) and all(r[:4] == s[:4] for r, s in zip(records, sr)),
        "Preserve stock HEX record layout/order",
    )
    changes = []
    start = None
    for i, (a, b) in enumerate(zip(sb, binary)):
        if a != b and start is None:
            start = i
        if a == b and start is not None:
            changes.append(
                dict(
                    offset=start, before=sb[start:i].hex(), after=binary[start:i].hex()
                )
            )
            start = None
    if start is not None:
        changes.append(
            dict(offset=start, before=sb[start:].hex(), after=binary[start:].hex())
        )
    need(
        binary[VERSION_OFFSET : VERSION_OFFSET + len(post)] == post,
        "Post-version does not match candidate ASCII literal",
    )
    scope = (
        "version-only"
        if changes
        and all(
            VERSION_OFFSET <= c["offset"]
            and c["offset"] + len(bytes.fromhex(c["after"]))
            <= VERSION_OFFSET + len(post)
            for c in changes
        )
        else "application"
    )
    review = dict(
        schema=2,
        serial=SERIAL,
        pre_version=pre_version,
        post_version=post_version,
        scope=scope,
        stock_bin_sha256=STOCK_SHA,
        hex_sha256=sha(blob),
        bin_sha256=sha(binary),
        base=BASE,
        end=END,
        records=len(records),
        changes=changes,
    )
    return records, review


def approved(blob, approval_blob, pinned_sha):
    need(
        re.fullmatch("[0-9a-f]{64}", pinned_sha or "") is not None,
        "Explicit lowercase approval SHA256 required",
    )
    need(sha(approval_blob) == pinned_sha, "Approval document hash mismatch")
    document = json.loads(approval_blob)
    need(
        document.get("schema") == 2,
        "Obsolete approval schema: new independent pre/post review required",
    )
    records, review = inspect(blob, document["pre_version"], document["post_version"])
    need(document == review, "Approval must exactly match current inspect JSON")
    need(review["changes"], "Live path rejects unchanged stock fixture")
    return records, review


def frame(op, data=b""):
    need(op in (0x80, 0x81, 0x82, 0x8E, 0x8F), "Non-PIC32 opcode forbidden")
    if op == 0x82:
        need(
            5 <= len(data) <= 61 and len(data) == data[0] + 5 and sum(data) % 256 == 0,
            "Invalid record frame",
        )
        return (bytes([63, op, len(data)]) + data).ljust(64, b"\0")
    need(not data, "Unexpected command payload")
    return bytes([63, op]).ljust(64, b"\0")


def descriptor_profile(raw):
    """Parse unnumbered HID sizes; loader shape is observed, not invented.
    Only the reviewed 16/64-byte input, 64-byte output profiles are supported.
    Binding additionally pins normal descriptors or consumer top-level usage.
    """
    i = 0
    size = count = page = 0
    usage = None
    bits = {8: 0, 9: 0, 11: 0}
    collections = 0
    tops = []
    while i < len(raw):
        prefix = raw[i]
        i += 1
        need(prefix != 254, "Long HID item unsupported")
        n = (0, 1, 2, 4)[prefix & 3]
        need(i + n <= len(raw), "Truncated HID descriptor")
        value = int.from_bytes(raw[i : i + n], "little")
        i += n
        typ = (prefix >> 2) & 3
        tag = prefix >> 4
        if typ == 1:
            need(
                tag not in (8, 10, 11),
                "Report IDs/global stack unsupported: stop before erase",
            )
            if tag == 0:
                page = value
            if tag == 7:
                size = value
            if tag == 9:
                count = value
        elif typ == 2 and tag == 0:
            usage = value
        elif typ == 0:
            if tag == 10:
                if collections == 0:
                    tops.append((page, usage, value))
                collections += 1
            if tag == 12:
                collections -= 1
                need(collections >= 0, "Bad HID collection")
            if tag in bits:
                need(
                    collections > 0 and 0 < size <= 32 and 0 < count <= 512,
                    "Invalid report field",
                )
                bits[tag] += size * count
            usage = None
    need(
        collections == 0 and len(tops) == 1 and tops[0][2] == 1,
        "Need one application collection",
    )
    need(
        bits[8] in (128, 512) and bits[9] == 512 and bits[11] in (0, 512),
        "Unsupported actual HID report sizes",
    )
    return dict(
        input_bytes=bits[8] // 8,
        output_bytes=64,
        feature_bytes=bits[11] // 8,
        report_id=None,
        usage_page=tops[0][0],
        usage=tops[0][1],
    )


class Journal:
    def __init__(self, name):
        self.path = HOME / name
        self.f = self.path.open("x", encoding="utf8")

    def event(self, kind, **kw):
        self.f.write(
            json.dumps(
                dict(
                    event=kind,
                    utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    **kw
                ),
                sort_keys=True,
            )
            + "\n"
        )
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self):
        self.f.close()


@contextlib.contextmanager
def clock_lock():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(STATE / "clock.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(
            not os.path.lexists(STATE / "pending.json"), "Clock pending restore exists"
        )
        yield
    finally:
        os.close(fd)


class USB:
    """No configure/reset/detach. SET_OUTPUT_REPORT and GET_INPUT_REPORT via EP0.
    HID Windows leading zero is not a wire Report ID. Descriptors must prove no ID.
    """

    def __init__(self, journal):
        import usb.core, usb.util

        self.core, self.util = usb.core, usb.util
        self.j = journal
        self.dev = None
        self.iface = None
        self.claimed = False

    def candidates(self, pid):
        return list(self.core.find(find_all=True, idVendor=0x041E, idProduct=pid))

    def bind(self, d, role="loader", normal=False):
        if normal:
            role = "normal"
        need(
            role in ("normal", "maintenance", "loader") and not self.claimed,
            "Invalid role or already bound",
        )
        need(
            d.idVendor == 0x041E
            and d.idProduct == (0x30CF if role == "loader" else 0x3247),
            "Role/device mismatch",
        )
        self.dev = d
        cfg = d.get_active_configuration()
        choices = []
        for intf in cfg:
            if intf.bInterfaceClass != 3 or intf.bAlternateSetting != 0:
                continue
            idx = intf.bInterfaceNumber
            if role == "normal" and idx != 4:
                continue
            if role == "maintenance" and idx != 3:
                continue
            raw = bytes(d.ctrl_transfer(0x81, 6, 0x2200, idx, 4096, timeout=2000))
            self.j.event(
                "descriptor_observed", role=role, interface=idx, descriptor=raw.hex()
            )
            try:
                profile = descriptor_profile(raw)
            except RuntimeError:
                continue
            if role == "normal" and raw != NORMAL_DESC:
                continue
            if role == "maintenance" and raw != MAINT_DESC:
                continue
            if role == "loader" and (profile["usage_page"], profile["usage"]) != (
                12,
                1,
            ):
                continue
            choices.append((idx, raw, list(intf), profile))
        need(
            len(choices) == 1,
            "Exactly one compatible HID report interface required; retain capture log for review",
        )
        idx, raw, eps, profile = choices[0]
        self.iface = idx
        self.role = role
        self.profile = profile
        need(
            not d.is_kernel_driver_active(idx),
            "HID kernel driver bound; no automatic detach. Operator must review targeted access solution",
        )
        self.util.claim_interface(d, idx)
        self.claimed = True
        self.identity = dict(
            bus=d.bus,
            ports=list(d.port_numbers or ()),
            pid=d.idProduct,
            interface=idx,
            configuration=cfg.bConfigurationValue,
            descriptor=raw.hex(),
            descriptor_sha256=sha(raw),
            profile=profile,
            endpoints=[
                dict(
                    address=e.bEndpointAddress,
                    attributes=e.bmAttributes,
                    max_packet=e.wMaxPacketSize,
                )
                for e in eps
            ],
        )
        need(self.identity["ports"], "Stable physical port chain unavailable")
        self.j.event("bound", role=role, identity=self.identity)
        return self.identity

    def send(self, p):
        need(self.claimed and len(p) == 64, "Unbound or wrong length")
        if self.role == "normal":
            need(
                self.iface == 4 and p == bytes.fromhex("5a070102").ljust(64, b"\0"),
                "Normal interface accepts only read-only 5a version query",
            )
        else:
            need(
                self.role in ("maintenance", "loader") and p[0] == 0x3F,
                "Maintenance/loader cannot send normal 5a",
            )
            allowed = (
                (0x80, 0x8E, 0x8F)
                if self.role == "maintenance"
                else (0x80, 0x81, 0x82, 0x8E, 0x8F)
            )
            need(p[1] in allowed, "Opcode not allowed for transport role")
            if self.role == "maintenance":
                need(self.iface == 3, "Maintenance must use interface 3")
        self.j.event("intent", role=self.role, interface=self.iface, report=p.hex())
        # Stock erase goes through WriteFile, unlike the other commands' EP0
        # SetOutputReport. Match interrupt OUT when the runtime descriptor has it.
        outs = [
            e["address"]
            for e in getattr(self, "identity", {}).get("endpoints", [])
            if not e["address"] & 128 and e["attributes"] & 3 == 3
        ]
        need(len(outs) <= 1, "Ambiguous interrupt OUT endpoints")
        if p[1] == 0x81 and outs:
            n = self.dev.write(outs[0], p, timeout=2000)
            # The Windows wrapper waits up to 500 ms for an interrupt response,
            # but InitL ignores its return and parses no erase status. Do not
            # invent a device ACK. The transfer state machine then waits 3.5 s.
            time.sleep(0.5)
        else:
            n = self.dev.ctrl_transfer(0x21, 9, 0x0200, self.iface, p, timeout=2000)
        need(n == 64, "Short output report: ambiguous, never retry")
        self.j.event("usb_output_complete")

    def version(self):
        need(self.role == "loader", "Loader version requires loader binding")
        return self.maintenance_version(1)

    def maintenance_version(self, major=2):
        need(
            self.role == ("maintenance" if major == 2 else "loader")
            and major in (1, 2),
            "Version/role mismatch",
        )
        self.send(frame(0x80))
        n = self.profile["input_bytes"]
        r = bytes(self.dev.ctrl_transfer(0xA1, 1, 0x0100, self.iface, n, timeout=2000))
        self.j.event(
            "maintenance_version_response",
            role=self.role,
            requested=n,
            received=len(r),
            report=r.hex(),
        )
        # Matching app GET_REPORT stages FOUR meaningful bytes even though
        # HID caps advertise input16. Accept that source-proven short reply.
        need(
            len(r) in (4, n) and r[:2] == b"\x3f\x80" and r[2] == major,
            "Unexpected maintenance reply/major; no erase",
        )
        if major == 2:
            need(r[3] == 0, "Expected matching application maintenance 2.0")
        return r[2:4].hex()

    def normal_version(self, expected=STOCK_VERSION):
        need(
            self.role == "normal" and self.iface == 4,
            "Normal version requires interface4",
        )
        expected = version_bytes(expected)
        eps = [
            e["address"]
            for e in self.identity["endpoints"]
            if e["address"] & 128 and e["attributes"] & 3 == 3
        ]
        need(eps == [0x84], "Normal interrupt endpoint mismatch")
        for _ in range(64):
            try:
                self.dev.read(0x84, 64, timeout=20)
            except self.core.USBTimeoutError:
                break
        else:
            raise RuntimeError("Normal interrupt queue not quiet")
        self.send(bytes.fromhex("5a070102").ljust(64, b"\0"))
        deadline = time.monotonic() + 3
        for _ in range(64):
            if time.monotonic() >= deadline:
                break
            try:
                r = bytes(self.dev.read(0x84, 64, timeout=100))
            except self.core.USBTimeoutError:
                continue
            need(
                len(r) == 64 and r[0] == 90 and r[2] <= 61, "Malformed normal response"
            )
            if r[1] != 7:
                continue
            need(
                r[2] == len(expected) and r[3 : 3 + r[2]] == expected,
                "Normal version differs from approved expectation",
            )
            self.j.event(
                "normal_version_verified", version=expected.decode().rstrip("\0")
            )
            return
        raise RuntimeError("No normal version response")

    def close(self):
        try:
            if self.claimed:
                self.util.release_interface(self.dev, self.iface)
        finally:
            self.claimed = False
            if self.dev is not None:
                self.util.dispose_resources(self.dev)
            self.dev = None


def transfer(t, records, j, sleep=time.sleep, clock=time.monotonic):
    """No fictional ACK, retry, MD5, or readback. Bound total to 20 minutes.
    Stock PIC32 writer checks USB output completion only, then final 8e and end8f.
    """
    state = "VERSION"
    deadline = clock() + 1200
    try:
        t.version()
        state = "ERASE_INTENT"
        j.event("state", state=state)
        t.send(frame(0x81))
        sleep(3.5)
        state = "WRITING"
        for i, r in enumerate(records):
            need(clock() < deadline, "Transfer deadline exceeded")
            j.event("record", index=i)
            t.send(frame(0x82, r))
        state = "FINALIZE_INTENT"
        j.event("state", state=state)
        t.send(frame(0x8E))
        state = "RESET_INTENT"
        j.event("state", state=state)
        t.send(frame(0x8F))
        j.event("TRANSFER_SENT_NOT_IMAGE_READBACK", records=len(records))
    except BaseException as exc:
        j.event(
            "aborted",
            state=state,
            error=repr(exc),
            action="No automatic retry/reset/recovery. Keep power; operator inspect logs.",
        )
        raise
    finally:
        t.close()


def wait_device(t, pid, bus, ports, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [
            d
            for d in t.candidates(pid)
            if d.bus == bus and list(d.port_numbers or ()) == ports
        ]
        need(len(matches) <= 1, "Ambiguous physical port identity")
        if matches:
            return matches[0]
        time.sleep(1)
    raise RuntimeError("Enumeration timeout; keep power, no automatic retry")


def save_evidence(name, payload):
    out = HOME / name
    with out.open("x") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(HOME, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return out, sha(out.read_bytes())


def pinned_evidence(path, digest, kind, approval):
    need(
        path and re.fullmatch("[0-9a-f]{64}", digest or ""), "Pinned evidence required"
    )
    blob = Path(path).read_bytes()
    need(sha(blob) == digest, "Evidence SHA mismatch")
    e = json.loads(blob)
    need(
        e.get("schema") == 2
        and e.get("kind") == kind
        and e.get("serial") == SERIAL
        and e.get("approval_sha256") == approval,
        "Evidence scope/serial/approval mismatch",
    )
    need(
        e.get("script_sha256") == sha(Path(__file__).read_bytes()),
        "Evidence from a different flasher; review again",
    )
    return e


def normal_preflight(t, expected):
    matches = t.candidates(0x3247)
    need(len(matches) == 1, "Exactly one original normal Katana required")
    d = matches[0]
    need(t.util.get_string(d, d.iSerialNumber) == SERIAL, "Serial mismatch")
    need(d.bcdDevice == 0x0200, "Normal bcdDevice mismatch")
    ident = t.bind(d, role="normal")
    t.normal_version(expected)
    t.close()
    return d, ident


def qualify(t, expected):
    d, ident = normal_preflight(t, expected)
    maintenance = t.bind(d, role="maintenance")
    need(
        (maintenance["bus"], maintenance["ports"]) == (ident["bus"], ident["ports"]),
        "Device changed between normal and maintenance",
    )
    response = t.maintenance_version()
    return dict(
        normal_identity=ident,
        maintenance_identity=maintenance,
        maintenance_version=response,
        pre_version=expected,
    )


def live(args, records, review):
    need(os.geteuid() != 0, "Refuse root/sudo")
    need(
        args.physical_ready == "short-direct-cable-stable-power",
        "Physical readiness confirmation required",
    )
    if args.enter_loader or args.live_loader:
        need(
            args.accept_permanent_brick, "Permanent brick risk acknowledgement required"
        )
    scope = dict(
        schema=2,
        serial=SERIAL,
        approval_sha256=args.approval_sha256,
        script_sha256=sha(Path(__file__).read_bytes()),
    )
    qualification = evidence = intent = None
    # All approvals checked before USB construction.
    if args.enter_loader:
        qualification = pinned_evidence(
            args.qualification,
            args.qualification_sha256,
            "maintenance-qualified",
            args.approval_sha256,
        )
        need(
            qualification["pre_version"] == review["pre_version"]
            and qualification["maintenance_version"] == "0200",
            "Qualification version mismatch",
        )
        need(
            not (HOME / "ENTRY-PENDING.json").exists(),
            "Entry already attempted; capture loader only, never resend 8e/8f",
        )
    if args.capture_loader or args.live_loader or args.verify_return:
        intent = pinned_evidence(
            args.entry_evidence,
            args.entry_evidence_sha256,
            "entry-intent",
            args.approval_sha256,
        )
        need(
            args.same_physical_device,
            "Operator must confirm uninterrupted same-device physical-port custody",
        )
    if args.live_loader:
        evidence = pinned_evidence(
            args.loader_evidence,
            args.loader_evidence_sha256,
            "loader-capture",
            args.approval_sha256,
        )
        need(
            evidence["entry_sha256"] == args.entry_evidence_sha256,
            "Loader evidence entry lineage mismatch",
        )
        profile = descriptor_profile(bytes.fromhex(evidence["identity"]["descriptor"]))
        need(
            (profile["usage_page"], profile["usage"]) == (12, 1)
            and evidence["loader_version"].startswith("01"),
            "Loader caps/major mismatch",
        )
        need(
            not (HOME / "ATTEMPT-CONSUMED.json").exists(),
            "An erase was already attempted; no automatic retry, including with another approval",
        )
    with clock_lock():
        j = Journal("live-" + str(time.time_ns()) + ".jsonl")
        t = None
        try:
            j.event("approved", review=review, **scope)
            t = USB(j)
            if args.qualify_maintenance or args.enter_loader:
                q = qualify(t, review["pre_version"])
                if args.qualify_maintenance:
                    out, digest = save_evidence(
                        "maintenance-qualified-" + str(time.time_ns()) + ".json",
                        dict(kind="maintenance-qualified", **scope, **q),
                    )
                    print(
                        json.dumps(
                            dict(
                                status="READ_ONLY_QUALIFIED_NO_PERSISTENT_COMMAND",
                                evidence=str(out),
                                sha256=digest,
                            )
                        )
                    )
                    return
                need(
                    all(q[key] == qualification[key] for key in q),
                    "Qualification identity/version changed",
                )
                intent = dict(
                    kind="entry-intent",
                    **scope,
                    **q,
                    qualification_sha256=args.qualification_sha256,
                    journal=str(j.path)
                )
                out, digest = save_evidence("ENTRY-PENDING.json", intent)
                args.entry_evidence_sha256 = digest
                # Printed/durable BEFORE persistent entry, survives access/re-enumeration failures.
                print(
                    json.dumps(
                        dict(
                            status="ENTRY_INTENT_SAVED",
                            evidence=str(out),
                            sha256=digest,
                        )
                    ),
                    flush=True,
                )
                t.send(frame(0x8E))
                t.send(frame(0x8F))
                time.sleep(0.1)
                t.close()
            if args.enter_loader or args.capture_loader:
                ident = intent["normal_identity"]
                d = wait_device(t, 0x30CF, ident["bus"], ident["ports"], 61)
                time.sleep(20)
                identity = t.bind(d, role="loader")
                version = t.version()
                out, digest = save_evidence(
                    "loader-evidence-" + str(time.time_ns()) + ".json",
                    dict(
                        kind="loader-capture",
                        **scope,
                        identity=identity,
                        loader_version=version,
                        entry_sha256=args.entry_evidence_sha256
                    ),
                )
                print(
                    json.dumps(
                        dict(
                            status="LOADER_CAPTURED_NO_APP_ERASE_REVIEW_REQUIRED",
                            evidence=str(out),
                            sha256=digest,
                        )
                    )
                )
                return
            if args.live_loader:
                ident = evidence["identity"]
                origin = intent["normal_identity"]
                need(
                    (ident["bus"], ident["ports"]) == (origin["bus"], origin["ports"]),
                    "Loader/entry physical identity mismatch",
                )
                d = wait_device(t, 0x30CF, ident["bus"], ident["ports"], 3)
                need(
                    t.bind(d, role="loader") == ident,
                    "Loader descriptor/port/configuration changed",
                )
                # Cross-approval one-shot marker: failed erase must never be replayed.
                save_evidence(
                    "ATTEMPT-CONSUMED.json",
                    dict(
                        kind="transfer-intent",
                        **scope,
                        journal=str(j.path),
                        entry_sha256=args.entry_evidence_sha256
                    ),
                )
                transfer(t, records, j)
            if args.live_loader or args.verify_return:
                ident = intent["normal_identity"]
                d = wait_device(t, 0x3247, ident["bus"], ident["ports"], 90)
                need(
                    t.util.get_string(d, d.iSerialNumber) == SERIAL
                    and d.bcdDevice == 0x0200,
                    "Post-reset identity mismatch",
                )
                # New USB node ACL/interface4 kernel binding can block this; no detach/root fallback.
                t.bind(d, role="normal")
                t.normal_version(review["post_version"])
                j.event(
                    "NORMAL_APP_RETURNED_NOT_IMAGE_READBACK",
                    scope=review["scope"],
                    post_version=review["post_version"],
                )
                print(
                    "Pinned application post-version returned; no image readback or audio/settings proof. "
                    + str(j.path)
                )
        except BaseException as exc:
            j.event("aborted_outer", error=repr(exc))
            raise
        finally:
            if t is not None:
                t.close()
            j.close()


def main(argv=None):
    global SERIAL, STOCK_HEX, HOME, STATE
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("hex", type=Path)
    mode = p.add_mutually_exclusive_group()
    for name in (
        "inspect",
        "qualify-maintenance",
        "enter-loader",
        "capture-loader",
        "live-loader",
        "verify-return",
    ):
        mode.add_argument("--" + name, action="store_true")
    p.add_argument("--pre-version", default=STOCK_VERSION)
    p.add_argument("--post-version", default=STOCK_VERSION)
    p.add_argument("--approval", type=Path)
    p.add_argument("--approval-sha256")
    p.add_argument("--qualification")
    p.add_argument("--qualification-sha256")
    p.add_argument("--entry-evidence")
    p.add_argument("--entry-evidence-sha256")
    p.add_argument("--same-physical-device", action="store_true")
    p.add_argument("--loader-evidence")
    p.add_argument("--loader-evidence-sha256")
    p.add_argument("--accept-permanent-brick", action="store_true")
    p.add_argument("--physical-ready")
    p.add_argument(
        "--serial",
        required=True,
        help="Exact target serial, bound into approval/evidence",
    )
    p.add_argument(
        "--stock-hex",
        required=True,
        type=Path,
        help="Locally supplied official MCU_RCDATA",
    )
    p.add_argument(
        "--attempt-dir",
        type=Path,
        help="Private durable directory for this independently reviewed attempt; never delete/reuse gates",
    )
    p.add_argument(
        "--state-dir",
        type=Path,
        default=Path(
            (os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state"))
        )
        / "katana-custom",
    )
    args = p.parse_args(argv)
    SERIAL = args.serial
    STOCK_HEX = args.stock_hex
    HOME = args.attempt_dir
    STATE = args.state_dir
    try:
        need(
            bool(SERIAL.strip()) and SERIAL == SERIAL.strip(),
            "Nonempty exact serial required",
        )
        need(STATE.is_absolute(), "State directory must be absolute")
        blob = args.hex.read_bytes()
        if not any(
            (
                args.qualify_maintenance,
                args.enter_loader,
                args.capture_loader,
                args.live_loader,
                args.verify_return,
            )
        ):
            _, review = inspect(blob, args.pre_version, args.post_version)
            print(json.dumps(review, indent=2))
            return 0
        need(
            HOME is not None and HOME.is_absolute(),
            "Absolute private --attempt-dir required for hardware modes",
        )
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        need(args.approval is not None, "Independently reviewed approval file required")
        need(
            args.pre_version == STOCK_VERSION and args.post_version == STOCK_VERSION,
            "Version CLI options are inspect-only; live expectations come from pinned approval",
        )
        records, review = approved(
            blob, args.approval.read_bytes(), args.approval_sha256
        )
        live(args, records, review)
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
