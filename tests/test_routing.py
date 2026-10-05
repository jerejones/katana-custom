"""Independent adapter + production orchestration regressions, synthetic USB only."""

import contextlib, io, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fixture import k, STOCK_AVAILABLE
import os
from test_flash import Journal

POST = k.STOCK_VERSION
CANDIDATE = (
    Path(
        os.environ.get(
            "KATANA_BUILD_DIR",
            str(Path(__file__).resolve().parents[1] / "build/firmware"),
        )
    )
    / "candidate.hex"
)
CANDIDATE_SHA = "88f9fb6430f183b85aa23f745b2bc39f7229a4ba4166479de7f45a17d26beaa2"
REAL_USB = k.USB


class Timeout(Exception):
    pass


class Interface(list):
    def __init__(self, index, endpoint, size):
        super().__init__(
            [NS(bEndpointAddress=endpoint, bmAttributes=3, wMaxPacketSize=size)]
        )
        self.bInterfaceClass = 3
        self.bAlternateSetting = 0
        self.bInterfaceNumber = index


class Config(list):
    bConfigurationValue = 1


class Device:
    idVendor = 0x041E
    bcdDevice = 0x0200
    iSerialNumber = 1
    bus = 5
    port_numbers = (1,)

    def __init__(self, pid=0x3247):
        self.idProduct = pid
        self.calls = []
        self.sent = []
        self.pending = False
        self.version = k.STOCK_VERSION
        self.bound = set()
        self.reply = None
        self.loader_descriptor = k.MAINT_DESC
        self.on_send = lambda p: None

    def get_active_configuration(self):
        return Config(
            [Interface(3, 0x83, 16), Interface(4, 0x84, 64)]
            if self.idProduct == 0x3247
            else [Interface(0, 0x81, 16)]
        )

    def is_kernel_driver_active(self, index):
        return index in self.bound

    def ctrl_transfer(self, request_type, request, value, index, data, **kw):
        self.calls.append((request_type, request, value, index, data))
        if request_type == 0x81:
            return (
                (k.MAINT_DESC if index == 3 else k.NORMAL_DESC)
                if self.idProduct == 0x3247
                else self.loader_descriptor
            )
        if request_type == 0x21:
            self.sent.append((index, bytes(data)))
            self.pending = data[0] == 0x5A
            self.on_send(data)
            return len(data)
        assert (request_type, request, value) == (0xA1, 1, 0x0100)
        return (
            self.reply
            if self.reply is not None
            else (
                b"\x3f\x80\x02\0" if self.idProduct == 0x3247 else b"\x3f\x80\x01\0"
            ).ljust(data, b"\0")
        )

    def read(self, endpoint, length, **kw):
        if not self.pending:
            raise Timeout()
        self.pending = False
        v = k.version_bytes(self.version)
        return (b"\x5a\x07" + bytes([len(v)]) + v).ljust(64, b"\0")


class Utility:
    @staticmethod
    def claim_interface(d, i):
        pass

    @staticmethod
    def release_interface(d, i):
        pass

    @staticmethod
    def dispose_resources(d):
        pass

    @staticmethod
    def get_string(d, i):
        return k.SERIAL


def adapter(j=None):
    t = REAL_USB.__new__(REAL_USB)
    t.j = j or Journal()
    t.claimed = False
    t.dev = None
    t.iface = None
    t.util = Utility
    t.core = NS(USBTimeoutError=Timeout)
    return t


class Routing(unittest.TestCase):
    @unittest.skipUnless(STOCK_AVAILABLE, "local stock required")
    def test_stock_descriptors_pinned_to_matching_binary(self):
        b = k.parse_hex(k.STOCK_HEX.read_bytes())[2]
        self.assertEqual(k.sha(b), k.STOCK_SHA)
        self.assertEqual(
            b[0x9D03E0B4 - 0x9D004180 : 0x9D03E104 - 0x9D004180], k.MAINT_DESC
        )
        p = k.descriptor_profile(k.MAINT_DESC)
        self.assertEqual(
            p,
            dict(
                input_bytes=16,
                output_bytes=64,
                feature_bytes=64,
                report_id=None,
                usage_page=12,
                usage=1,
            ),
        )

    def test_wrong_family_never_reaches_device(self):
        for role, index, packets in [
            ("normal", 4, [k.frame(o) for o in (0x80, 0x8E, 0x8F)]),
            (
                "maintenance",
                3,
                [bytes.fromhex("5a070102").ljust(64, b"\0"), k.frame(0x81)],
            ),
        ]:
            d = Device()
            t = adapter()
            t.bind(d, role=role)
            for packet in packets:
                with self.assertRaises(RuntimeError):
                    t.send(packet)
            self.assertEqual(d.sent, [])

    def test_qualify_uses_distinct_interfaces_no_persistent_commands(self):
        d = Device()
        t = adapter()
        t.candidates = lambda pid: [d]
        q = k.qualify(t, k.STOCK_VERSION)
        self.assertEqual(q["maintenance_version"], "0200")
        self.assertEqual(
            [(i, p[:2].hex(), len(p)) for i, p in d.sent],
            [(4, "5a07", 64), (3, "3f80", 64)],
        )
        self.assertIn((0xA1, 1, 0x0100, 3, 16), d.calls)

    def test_maintenance_short_reply_is_source_proven_four_not_arbitrary(self):
        for n in (0, 3, 4, 5, 15, 16, 64):
            d = Device()
            d.reply = b"\x3f\x80\x02\0"[:n].ljust(n, b"\0")
            t = adapter()
            t.bind(d, role="maintenance")
            if n in (4, 16):
                self.assertEqual(t.maintenance_version(), "0200")
            else:
                with self.assertRaises(RuntimeError):
                    t.maintenance_version()
        for reply in (b"\x3f\x80\x01\0", b"\x3f\x80\x02\1", b"\0\x3f\x80\2"):
            d.reply = reply
            with self.assertRaises(RuntimeError):
                t.maintenance_version()

    def test_loader_input_size_from_observed_caps_and_consumer_only(self):
        for desc, size in [
            (k.MAINT_DESC, 16),
            (k.MAINT_DESC.replace(b"\x95\x0f\x81", b"\x95\x3f\x81"), 64),
        ]:
            d = Device(0x30CF)
            d.loader_descriptor = desc
            t = adapter()
            t.bind(d, role="loader")
            self.assertEqual(t.version(), "0100")
            self.assertIn((0xA1, 1, 0x0100, 0, size), d.calls)
        for desc in (
            k.NORMAL_DESC,
            b"\x85\1" + k.MAINT_DESC,
            k.MAINT_DESC.replace(b"\x95\x0f", b"\x95\x0e"),
        ):
            d.loader_descriptor = desc
            t = adapter()
            with self.assertRaises(RuntimeError):
                t.bind(d, role="loader")
            self.assertEqual(t.claimed, False)

    def test_bound_maintenance_honest_block_no_fallback(self):
        d = Device()
        d.bound.add(3)
        t = adapter()
        with self.assertRaisesRegex(RuntimeError, "kernel driver bound"):
            t.bind(d, role="maintenance")
        self.assertEqual(d.sent, [])

    def test_normal_before_after_independent(self):
        d = Device()
        t = adapter()
        t.bind(d, role="normal")
        t.normal_version(k.STOCK_VERSION)
        with self.assertRaises(RuntimeError):
            t.normal_version("2.0.171220.131")
        d.version = "2.0.171220.131"
        t.normal_version("2.0.171220.131")
        with self.assertRaises(RuntimeError):
            t.normal_version(k.STOCK_VERSION)

    def test_malformed_descriptor_profile(self):
        for d in (
            k.MAINT_DESC + b"\xc0",
            b"\xa4" + k.MAINT_DESC,
            k.MAINT_DESC + k.MAINT_DESC,
            k.MAINT_DESC[:-1],
        ):
            with self.assertRaises(RuntimeError):
                k.descriptor_profile(d)


@unittest.skipUnless(
    STOCK_AVAILABLE and CANDIDATE.exists(), "local stock and built image required"
)
class Orchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".routing-test-", dir=k.HOME)
        self.home = Path(self.tmp.name)

        class MemoryJournal(Journal):
            def __init__(journal, name):
                super().__init__()
                journal.path = self.home / name

            def close(journal):
                pass

        self.patchers = [
            patch.object(k, "HOME", self.home),
            patch.object(k, "Journal", MemoryJournal),
            patch.object(k, "clock_lock", contextlib.nullcontext),
            patch.object(k.os, "geteuid", lambda: 1000),
            patch.object(k.time, "sleep", lambda _: None),
        ]
        for p in self.patchers:
            p.start()
        self.normal = Device()
        self.loader = Device(0x30CF)
        self.mode = "normal"
        self.block_loader = False
        self.block_return = False

        def normal_send(p):
            if p[:2] == b"\x3f\x8f":
                self.mode = "loader"

        def loader_send(p):
            if p[:2] == b"\x3f\x8f":
                self.mode = "normal"
                self.normal.version = POST
                if self.block_return:
                    self.normal.bound.add(4)

        self.normal.on_send = normal_send
        self.loader.on_send = loader_send

        def factory(j):
            t = adapter(j)
            t.candidates = lambda pid: (
                [self.normal]
                if self.mode == "normal" and pid == 0x3247
                else [self.loader] if self.mode == "loader" and pid == 0x30CF else []
            )
            original = t.bind

            def bind(d, **kw):
                if d is self.loader and self.block_loader:
                    raise PermissionError("synthetic reenumeration ACL missing")
                return original(d, **kw)

            t.bind = bind
            return t

        self.usbpatch = patch.object(k, "USB", factory)
        self.usbpatch.start()
        self.records, self.review = k.inspect(
            CANDIDATE.read_bytes(), k.STOCK_VERSION, POST
        )
        self.args = NS(
            qualify_maintenance=False,
            enter_loader=False,
            capture_loader=False,
            live_loader=False,
            verify_return=False,
            physical_ready="short-direct-cable-stable-power",
            accept_permanent_brick=True,
            approval_sha256="a" * 64,
            qualification=None,
            qualification_sha256=None,
            entry_evidence=None,
            entry_evidence_sha256=None,
            loader_evidence=None,
            loader_evidence_sha256=None,
            same_physical_device=True,
        )

    def tearDown(self):
        self.usbpatch.stop()
        for p in reversed(self.patchers):
            p.stop()
        self.tmp.cleanup()

    def run_mode(self, mode):
        for key in (
            "qualify_maintenance",
            "enter_loader",
            "capture_loader",
            "live_loader",
            "verify_return",
        ):
            setattr(self.args, key, key == mode)
        with contextlib.redirect_stdout(io.StringIO()):
            k.live(self.args, self.records, self.review)

    def qualify(self):
        self.run_mode("qualify_maintenance")
        p = next(self.home.glob("maintenance-qualified-*.json"))
        self.args.qualification = str(p)
        self.args.qualification_sha256 = k.sha(p.read_bytes())

    def entry(self):
        self.run_mode("enter_loader")
        self.set_entry()

    def set_entry(self):
        p = self.home / "ENTRY-PENDING.json"
        self.args.entry_evidence = str(p)
        self.args.entry_evidence_sha256 = k.sha(p.read_bytes())

    def set_loader(self):
        p = next(self.home.glob("loader-evidence-*.json"))
        self.args.loader_evidence = str(p)
        self.args.loader_evidence_sha256 = k.sha(p.read_bytes())

    def test_entry_requires_parent_pinned_qualification(self):
        with self.assertRaises(RuntimeError):
            self.run_mode("enter_loader")
        self.assertEqual(self.normal.sent, [])

    def test_readonly_mode_no_entry_marker(self):
        self.qualify()
        self.assertFalse((self.home / "ENTRY-PENDING.json").exists())
        self.assertEqual([p[:2] for _, p in self.normal.sent], [b"\x5a\7", b"\x3f\x80"])

    def test_access_failure_capture_resume_never_resends_entry(self):
        self.qualify()
        self.block_loader = True
        with self.assertRaises(PermissionError):
            self.entry()
        self.set_entry()
        before = list(self.normal.sent)
        self.block_loader = False
        self.run_mode("capture_loader")
        self.assertEqual(self.normal.sent, before)
        self.assertEqual([p[:2] for _, p in self.loader.sent], [b"\x3f\x80"])
        with self.assertRaises(RuntimeError):
            self.run_mode("enter_loader")
        self.assertFalse((self.home / "ATTEMPT-CONSUMED.json").exists())

    def test_full_transfer_real_records_post_version_no_second_erase(self):
        self.qualify()
        self.entry()
        self.set_loader()
        # Inject delay only; production transfer and real candidate records exercised.
        original = k.transfer
        with patch.object(
            k, "transfer", lambda t, rs, j: original(t, rs, j, sleep=lambda _: None)
        ):
            self.run_mode("live_loader")
        sent = [p for _, p in self.loader.sent]
        self.assertEqual(sum(p[1] == 0x81 for p in sent), 1)
        self.assertEqual(sum(p[1] == 0x82 for p in sent), len(self.records))
        self.assertEqual(sent[-3][3:8], bytes.fromhex("00000001ff"))
        self.assertEqual([p[1] for p in sent[-2:]], [0x8E, 0x8F])
        self.assertEqual(self.normal.version, POST)
        with self.assertRaises(RuntimeError):
            self.run_mode("live_loader")
        self.assertEqual(self.loader.sent, [(0, p) for p in sent])
        self.run_mode("verify_return")
        self.assertEqual(len(self.loader.sent), len(sent))

    def test_return_permission_failure_can_verify_without_reflash(self):
        self.qualify()
        self.entry()
        self.set_loader()
        self.block_return = True
        original = k.transfer
        with patch.object(
            k, "transfer", lambda t, rs, j: original(t, rs, j, sleep=lambda _: None)
        ):
            with self.assertRaisesRegex(RuntimeError, "kernel driver bound"):
                self.run_mode("live_loader")
        self.assertTrue((self.home / "ATTEMPT-CONSUMED.json").exists())
        before = list(self.loader.sent)
        self.normal.bound.clear()
        self.run_mode("verify_return")
        self.assertEqual(self.loader.sent, before)

    def test_evidence_approval_and_physical_scope(self):
        self.qualify()
        self.entry()
        self.set_loader()
        self.args.same_physical_device = False
        with self.assertRaises(RuntimeError):
            self.run_mode("capture_loader")
        self.args.same_physical_device = True
        self.args.approval_sha256 = "b" * 64
        with self.assertRaises(RuntimeError):
            self.run_mode("live_loader")
        self.assertFalse((self.home / "ATTEMPT-CONSUMED.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
