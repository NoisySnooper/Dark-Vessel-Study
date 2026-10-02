"""Build the self-contained demo web page from the pipeline outputs.

Usage: python scripts/07_build_demo_page.py --out /path/to/demo.html
Needs: 02_search_scenes.py and 08_coverage.py (South China Sea), 09_run_regional.py (+ --merge),
and 03_run_baseline.py (Ca Mau detail). 06_apply_verifier.py is optional (adds CNN scores).
"""

import argparse
from pathlib import Path

from darkvessel.viz.demo import build_demo

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True, type=Path)
ap.add_argument("--chips-regional", type=int, default=500)
ap.add_argument("--chips-detail", type=int, default=600)
args = ap.parse_args()

out = build_demo(args.out, args.chips_regional, args.chips_detail)
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
