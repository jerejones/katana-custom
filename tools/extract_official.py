#!/usr/bin/env python3
"""Extract only MCU_RCDATA from the exact locally supplied official updater.
No updater execution, network, USB, PE loading, or generic firmware fallback.
The fixed slice is valid ONLY after whole-file identity/size verification.
"""

import argparse
import hashlib
from pathlib import Path

INSTALLER_SHA = "22abeb87dc943d79f57b2ee2fbf33f2773a61b61e71697f68a8dfd5d3ea102c2"
RESOURCE_SHA = "aac90c80f60fb061d3fc368e0c4e76f18a19ce6901188f3cbff11bf955437ab8"
OFFSET = 0x3FC40
SIZE = 680978


def extract(blob):
    if len(blob) != 36451312 or hashlib.sha256(blob).hexdigest() != INSTALLER_SHA:
        raise ValueError(
            "Not the exact official BXKT_PCFW_EN_2_0_171220_130.exe; no fallback"
        )
    resource = blob[OFFSET : OFFSET + SIZE]
    if hashlib.sha256(resource).hexdigest() != RESOURCE_SHA:
        raise ValueError("MCU resource hash mismatch")
    return resource


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("installer", type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    resource = extract(a.installer.read_bytes())
    # Exclusive creation: do not overwrite a stock input or prior extraction.
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("xb") as f:
        f.write(resource)
    print("Extracted MCU_RCDATA:", RESOURCE_SHA)


if __name__ == "__main__":
    main()
