"""Always-offline checks using synthetic records; no proprietary input needed."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fixture import k, ROOT

sys.path.insert(0, str(ROOT / "firmware"))
import build
import encoder
import placement
import extract_official


def record(address, kind, data=b""):
    b = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return b":" + (b + bytes([-sum(b) & 255])).hex().upper().encode() + b"\r\n"


class Offline(unittest.TestCase):
    def test_extractor_rejects_unknown_input(self):
        with self.assertRaises(ValueError):
            extract_official.extract(b"not an updater")

    def test_synthetic_hex_roundtrip(self):
        blob = record(0, 4, b"\x1d\x00") + record(0x4180, 0, b"abcd") + record(0, 1)
        records, m, b = k.parse_hex(blob)
        self.assertEqual(b[:4], b"abcd")
        self.assertEqual(build.package(blob, m), blob)
        m[k.BASE] = ord("z")
        result = build.package(blob, m)
        rr, mm, bb = k.parse_hex(result)
        self.assertEqual(mm, m)
        self.assertEqual(bb[:4], b"zbcd")
        self.assertEqual([r[:4] for r in records], [r[:4] for r in rr])
        self.assertEqual(
            result.splitlines(keepends=True)[-1], blob.splitlines(keepends=True)[-1]
        )
        for bad in (
            blob[:-1] + b"x",
            blob + record(0, 1),
            blob.replace(b":", b";", 1),
            blob.replace(b"6162", b"6262"),
        ):
            with self.assertRaises((ValueError, RuntimeError)):
                k.parse_hex(bad)

    def test_source_placement_budgets(self):
        segments, labels, _ = placement.assemble(encoder)
        self.assertEqual(
            [(hex(a), len(b)) for a, b in segments],
            [
                ("0x9d02b868", 800),
                ("0x9d036e14", 152),
                ("0x9d036eac", 1176),
                ("0x9d00e35c", 144),
            ],
        )
        self.assertIn("background_request", labels)

    def test_no_usb_import(self):
        self.assertNotIn("usb.core", sys.modules)

    def test_durable_exclusive_evidence(self):
        with tempfile.TemporaryDirectory() as td, patch.object(k, "HOME", Path(td)):
            p, h = k.save_evidence("ENTRY-PENDING.json", {"kind": "synthetic"})
            self.assertEqual(k.sha(p.read_bytes()), h)
            with self.assertRaises(FileExistsError):
                k.save_evidence("ENTRY-PENDING.json", {})

    def test_evidence_hash_lineage_and_script_binding(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "evidence.json"
            payload = dict(
                schema=2,
                kind="entry-intent",
                serial=k.SERIAL,
                approval_sha256="a" * 64,
                script_sha256=k.sha(Path(k.__file__).read_bytes()),
            )
            p.write_text(json.dumps(payload))
            h = k.sha(p.read_bytes())
            self.assertEqual(k.pinned_evidence(p, h, "entry-intent", "a" * 64), payload)
            for digest, kind, approval in [
                ("b" * 64, "entry-intent", "a" * 64),
                (h, "loader-capture", "a" * 64),
                (h, "entry-intent", "c" * 64),
            ]:
                with self.assertRaises(RuntimeError):
                    k.pinned_evidence(p, digest, kind, approval)
            payload["script_sha256"] = "0" * 64
            p.write_text(json.dumps(payload))
            with self.assertRaises(RuntimeError):
                k.pinned_evidence(p, k.sha(p.read_bytes()), "entry-intent", "a" * 64)


if __name__ == "__main__":
    unittest.main()
