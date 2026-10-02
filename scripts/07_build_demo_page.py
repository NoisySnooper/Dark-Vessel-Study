"""Build the self-contained demo web page from the pipeline outputs.

Usage: python scripts/07_build_demo_page.py --out /path/to/demo.html
Needs: outputs of 02_search_scenes.py and 03_run_baseline.py (06_apply_verifier.py optional).
The Natural Earth 10 m land layer (public domain) is fetched once from GitHub.
"""

import argparse
from pathlib import Path

import requests

from darkvessel.config import RAW_DIR
from darkvessel.viz.demo import build_demo

NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson"

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True, type=Path)
ap.add_argument("--max-chips", type=int, default=1500)
args = ap.parse_args()

land = RAW_DIR / "natural_earth" / "ne_10m_land.geojson"
if not land.exists():
    land.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(NE_URL, timeout=300)
    r.raise_for_status()
    land.write_bytes(r.content)

out = build_demo(args.out, land, max_chips=args.max_chips)
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
