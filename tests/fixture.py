import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import katana_flash as k

k.SERIAL = "TEST-SERIAL"
k.STOCK_HEX = (
    Path(os.environ["KATANA_STOCK_HEX"]) if os.environ.get("KATANA_STOCK_HEX") else None
)
STOCK_AVAILABLE = k.STOCK_HEX is not None
