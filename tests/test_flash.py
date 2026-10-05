"""Offline tests only: stock fixtures, synthetic I/O. No USB import/enumeration."""

import unittest, json, sys
from fixture import k, STOCK_AVAILABLE


class Journal:
    def __init__(self):
        self.events = []

    def event(self, kind, **kw):
        self.events.append((kind, kw))


class Transport:
    def __init__(self, fail=None, interrupt=False):
        self.sent = []
        self.closed = False
        self.fail = fail
        self.interrupt = interrupt
        self.versions = 0

    def version(self):
        self.versions += 1
        if self.fail == 0:
            raise RuntimeError("synthetic version failure")

    def send(self, p):
        self.sent.append(p)
        if self.fail == len(self.sent):
            if self.interrupt:
                raise KeyboardInterrupt()
            raise RuntimeError("synthetic ambiguous completion")

    def close(self):
        self.closed = True


def line(r):
    return ":" + r.hex()


def rec(addr, kind, data=b""):
    b = bytes([len(data)]) + addr.to_bytes(2, "big") + bytes([kind]) + data
    return b + bytes([-sum(b) % 256])


@unittest.skipUnless(
    STOCK_AVAILABLE, "set KATANA_STOCK_HEX for proprietary stock-dependent tests"
)
class Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = k.STOCK_HEX.read_bytes()
        cls.records, cls.review = k.inspect(cls.blob)

    def test_stock(self):
        self.assertEqual(self.review["records"], 15326)
        self.assertEqual(self.review["bin_sha256"], k.STOCK_SHA)
        self.assertEqual(self.review["changes"], [])

    def test_no_usb_import(self):
        self.assertNotIn("usb.core", sys.modules)

    def test_bad_hex(self):
        for blob in (
            b"",
            b":00000001FE\n",
            self.blob + b":00000001ff\n",
            b"x" + self.blob,
            self.blob.replace(b":", b";", 1),
            self.blob[:100],
            b":020000041FC01B\n:00000001ff\n",
        ):
            with self.subTest(blob=blob[:20]), self.assertRaises(
                (RuntimeError, ValueError)
            ):
                k.inspect(blob)

    def test_protected_addresses(self):
        for upper, addr in ((0x1FC0, 0), (0x1D00, 0), (0x1D03, 0xFFFF), (0, 0)):
            blob = (
                "\n".join(
                    map(
                        line,
                        [
                            rec(0, 4, upper.to_bytes(2, "big")),
                            rec(addr, 0, b"1234"),
                            rec(0, 1),
                        ],
                    )
                )
                + "\n"
            ).encode()
            with self.subTest(upper=upper, addr=addr), self.assertRaises(RuntimeError):
                k.parse_hex(blob)

    def test_overlap_and_type(self):
        for records in (
            [
                rec(0, 4, b"\x1d\x00"),
                rec(0x4180, 0, b"1234"),
                rec(0x4180, 0, b"1234"),
                rec(0, 1),
            ],
            [rec(0, 5, b"1234"), rec(0, 1)],
        ):
            with self.assertRaises(RuntimeError):
                k.parse_hex(("\n".join(map(line, records)) + "\n").encode())

    def test_coverage_and_layout(self):
        records = self.records.copy()
        records[2], records[3] = records[3], records[2]
        with self.assertRaises(RuntimeError):
            k.inspect(("\n".join(map(line, records)) + "\n").encode())

    def test_manifest(self):
        # Mutate only in RAM; NOT a custom image artifact or deployment fixture.
        rs = self.records.copy()
        r = bytearray(rs[2])
        r[4] ^= 1
        r[-1] = -sum(r[:-1]) % 256
        rs[2] = bytes(r)
        blob = ("\n".join(map(line, rs)) + "\n").encode()
        _, review = k.inspect(blob)
        approval = json.dumps(review).encode()
        out, _ = k.approved(blob, approval, k.sha(approval))
        self.assertEqual(out, rs)
        self.assertTrue(review["changes"])
        for badhash in (None, "0" * 64, k.sha(approval).upper()):
            with self.assertRaises(RuntimeError):
                k.approved(blob, approval, badhash)
        review["changes"] = []
        bad = json.dumps(review).encode()
        with self.assertRaises(RuntimeError):
            k.approved(blob, bad, k.sha(bad))

    def test_stock_cannot_arm(self):
        approval = json.dumps(self.review).encode()
        with self.assertRaises(RuntimeError):
            k.approved(self.blob, approval, k.sha(approval))

    def test_frame(self):
        for r in self.records:
            f = k.frame(0x82, r)
            self.assertEqual(len(f), 64)
            self.assertEqual(f[:3], bytes([63, 130, len(r)]))
            self.assertEqual(f[3 : 3 + len(r)], r)
        for op in (0x90, 0x91, 0x92, 0x93, 0x99, 0xC0):
            with self.assertRaises(RuntimeError):
                k.frame(op)

    def test_descriptor(self):
        self.assertEqual(k.descriptor_profile(k.NORMAL_DESC)["report_id"], None)
        for raw in (
            b"",
            k.NORMAL_DESC[:-1],
            b"\x85\x01" + k.NORMAL_DESC,
            k.NORMAL_DESC.replace(b"\x95\x40", b"\x95\x3f"),
            b"\xfe" + k.NORMAL_DESC,
        ):
            with self.assertRaises(RuntimeError):
                k.descriptor_profile(raw)

    def test_whole_stock_state_machine(self):
        t = Transport()
        j = Journal()
        wait = []
        k.transfer(t, self.records, j, sleep=wait.append, clock=lambda: 0)
        self.assertEqual(len(t.sent), len(self.records) + 3)
        self.assertEqual(
            [p[1] for p in t.sent], [0x81] + [0x82] * len(self.records) + [0x8E, 0x8F]
        )
        self.assertEqual(wait, [3.5])
        self.assertTrue(t.closed)
        self.assertEqual(j.events[-1][0], "TRANSFER_SENT_NOT_IMAGE_READBACK")

    def test_failures_and_interrupt_do_not_retry_or_reset(self):
        rs = self.records[:3]
        for fail in range(7):
            for interrupt in (False, True):
                t = Transport(fail, interrupt)
                j = Journal()
                with self.subTest(fail=fail, interrupt=interrupt), self.assertRaises(
                    (RuntimeError, KeyboardInterrupt)
                ):
                    k.transfer(t, rs, j, sleep=lambda _: None, clock=lambda: 0)
                self.assertEqual(len(t.sent), fail)
                self.assertTrue(t.closed)
                self.assertEqual(j.events[-1][0], "aborted")

    def test_deadline(self):
        times = iter([0, 1201])
        t = Transport()
        j = Journal()
        with self.assertRaises(RuntimeError):
            k.transfer(
                t, self.records, j, sleep=lambda _: None, clock=lambda: next(times)
            )
        self.assertEqual([p[1] for p in t.sent], [0x81])
        self.assertTrue(t.closed)

    def test_usb_output_adapter(self):
        class D:
            def ctrl_transfer(self, *args, **kw):
                self.call = (args, kw)
                return 64

        t = k.USB.__new__(k.USB)
        t.j = Journal()
        t.claimed = True
        t.iface = 0
        t.role = "loader"
        t.profile = {"input_bytes": 64}
        t.dev = D()
        f = k.frame(0x82, self.records[2])
        t.send(f)
        self.assertEqual(t.dev.call, ((0x21, 9, 0x0200, 0, f), dict(timeout=2000)))
        t.dev.ctrl_transfer = lambda *a, **kw: 63
        with self.assertRaises(RuntimeError):
            t.send(f)

    def test_usb_erase_interrupt_out(self):
        from unittest.mock import patch

        class D:
            def write(self, *args, **kw):
                self.call = (args, kw)
                return 64

        t = k.USB.__new__(k.USB)
        t.j = Journal()
        t.claimed = True
        t.iface = 0
        t.role = "loader"
        t.dev = D()
        t.identity = {"endpoints": [dict(address=1, attributes=3, max_packet=64)]}
        with patch("katana_flash.time.sleep") as wait:
            t.send(k.frame(0x81))
            wait.assert_called_once_with(0.5)
        self.assertEqual(t.dev.call, ((1, k.frame(0x81)), dict(timeout=2000)))

    def test_usb_version_adapter(self):
        class D:
            def ctrl_transfer(self, *args, **kw):
                if args[0] == 0x21:
                    return 64
                self.call = (args, kw)
                return b"\x3f\x80\x01\x00" + b"\0" * 60

        t = k.USB.__new__(k.USB)
        t.j = Journal()
        t.claimed = True
        t.iface = 0
        t.role = "loader"
        t.profile = {"input_bytes": 64}
        t.dev = D()
        self.assertEqual(t.version(), "0100")
        self.assertEqual(t.dev.call, ((0xA1, 1, 0x0100, 0, 64), dict(timeout=2000)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
