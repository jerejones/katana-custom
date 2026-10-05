"""Exact placement in two proven populated stock spans, never FF caves."""

from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
MAIN = 0x9D02B868
GLYPH = 0x9D036E14


def scan(text, base):
    labels = {}
    pc = base
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or line == ".align 4":
            continue
        if line.endswith(":"):
            labels[line[:-1]] = pc
        else:
            pc += 4
    return labels, pc


def assemble(build):
    s = (OUT / "endpoint.S").read_text()
    front = s[: s.index("initialize:")]
    init = s[s.index("initialize:") : s.index("event_hook:")]
    middle = s[s.index("event_hook:") : s.index("qualify:")]
    qualify = s[s.index("qualify:") : s.index("background_request:")]
    bg = s[s.index("background_request:") : s.index(".align 4\ntable:")]
    table = s[s.index(".align 4\ntable:") :]
    glyph = (OUT / "glyph-small.S").read_text()
    _, core_start = scan(glyph, GLYPH)
    units = [
        (MAIN, 0x9D02BB94, front + init + qualify + table),
        (GLYPH, core_start, glyph),
        (core_start, 0x9D037344, middle + bg + "\ntable:\n"),
    ]
    units.append((0x9D00E35C, 0x9D00E3EC, (OUT / "idle-provenance.S").read_text()))
    all_labels = {}
    for a, z, text in units:
        ll, end = scan(text, a)
        assert end <= z, (hex(a), end - a, z - a)
        all_labels.update({k: v for k, v in ll.items() if k != "table"})
    segments = []
    assembly = []
    for a, z, text in units:
        local, _ = scan(text, a)
        for k, v in all_labels.items():
            if k not in local:
                text = re.sub(r"\b" + re.escape(k) + r"\b", hex(v), text)
        build.START, build.END = a, z
        text = text.replace("LO_TABLE(v1)", str(local["table"] & 65535) + "(v1)")
        blob, ll, lines = build.assemble(text, [])
        segments.append((a, blob))
        assembly.append((lines, ll))
    all_labels["table"] = assembly[0][1]["table"]
    build.START, build.END = MAIN, 0x9D02BB94
    return segments, all_labels, assembly
