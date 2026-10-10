"""Dark-lead scoring: the ranked leads queue of SCS Vessel Watch, open and research builds.

Purpose: turn verified radar contacts and VIIRS lights into Lead objects (app/CONTRACT.md section 3.5) with their
evidence, a factor-by-factor review priority (0 to 100, not a risk score), lawful explanations, change indicators, the
next planned radar look and the product caveat. "Dark" means only "no AIS match"; it never means illegal.

Method (darkvessel.leads; the output's 'about' layer repeats every rule and weight)
- L1, unmatched radar contact in AIS reach: ais_status unmatched, class high or medium, cnn_score >= 0.5, both channels
  where the polarisation is known, no clutter-zone or near-fixed flag, and no known weather failure, gated per part:
  out when wind is known and >= 12 m/s or deep convection is known true; a missing part keeps the lead with the factor
  'weather unknown' at 0 points (board D4.5). Open build: live passes (data/live/). Research build: the September run
  with GFW identity (data/research/regional_identity.parquet).
- L7, lit activity where radar does not look: clear-sky VIIRS lit-vessel candidates with no Sentinel-1 pass in the
  90-day window, more than 1 km from Satlas infrastructure, one lead per 0.25 degree cell, the 20 brightest lights as
  evidence. A coverage lead for tasking, not a vessel lead. Both builds.
- Priority factors: evidence quality 0 to 30, corroboration 0 to 25 (light or AIS behaviour event within 2 km and 3 h),
  AIS reach quality 0 to 20, persistence 0 to 15 (unmatched again within 2 km on another pass within 72 h), area
  weight 0 to 10 (default 0). No AIS match adds 0. L7 is scored within the same meanings (evidence on half the scale,
  no corroboration, no AIS claim, persistence for other nights) and stays at 30 or below without an area weight.
  Bands: low 0 to 33, medium 34 to 66, high 67 to 100. Model id lead_priority_v0_20261009, calibrated false until the
  owner's labels exist. next_look_utc is the first planned pass after both the lead time and the plan's generation.
- The open build never opens data/research/ (guard in code, tested). Research rows add the research line of the
  caveat and research_only true; the about layer and the parquet metadata carry the GFW licence, attribution and
  terms stamps copied from regional_identity.parquet.
- Checkpoint-free; both builds finish in well under 3 minutes. Reruns with unchanged inputs write byte-identical tables:
  the about layer's generated_utc (also the GeoPackage timestamp) changes only when an input's mtime or size changes.
  Every file is written under a temporary name and moved into place, so the app never reads a half-written file.

Inputs (read-only): data/live/live_contacts.gpkg (open L1), data/research/regional_identity.parquet and
  data/research/gfw_events_{loitering,encounters}*.parquet (research L1), data/viirs_lights_all.gpkg (every VIIRS night;
  L7 and corroboration; data/viirs_lights.gpkg is the fallback and the source of the recurring light sites),
  data/weather_context.parquet, data/ocean_static_cells.parquet, data/outputs/small/s1_passes_4326.tif,
  data/outputs/small/ais_reach_share_4326.tif, data/s1_next_passes.json and data/ais_live.gpkg (next look).
Output: data/leads_open.gpkg (leads_4326, leads_utm49n EPSG:32649, lead_evidence, about), data/leads_open_summary.json;
  data/research/leads_research.gpkg, data/research/leads_research.parquet, data/research/leads_research_summary.json;
  docs/figures/leads_priority.png.
Usage: nice -n 10 python scripts/33_leads.py --build both
       python scripts/33_leads.py --build open --dry-run
       python scripts/33_leads.py --build research --since 2026-09-20 --until 2026-10-02
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import darkvessel  # noqa: F401,E402  (PROJ_DATA before rasterio and pyogrio)
from darkvessel.leads import BUILDS  # noqa: E402
from darkvessel.leads import build as B  # noqa: E402


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default="both", choices=["open", "research", "both"])
    ap.add_argument("--since", default=None, help="keep contacts and lights at or after this UTC time (ISO)")
    ap.add_argument("--until", default=None, help="keep contacts and lights at or before this UTC time (ISO)")
    ap.add_argument("--area-weights", default=None, help="JSON file {reporting box: points 0..10}; default 0 everywhere")
    ap.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    ap.add_argument("--no-figure", action="store_true")
    a = ap.parse_args()
    builds = list(BUILDS) if a.build == "both" else [a.build]
    weights = json.loads(Path(a.area_weights).read_text()) if a.area_weights else None
    t0 = time.time()
    results = {}
    for b in builds:
        res = B.assemble(b, since=a.since, until=a.until, area_weights=weights, log=log)
        results[b] = res
        c = res["counts"]
        log(f"[{b}] {c['leads']} leads: by type {c['by_type']}, by band {c['by_band']}; evidence rows {c['evidence_rows']}; "
            f"L1 gate {c['L1'].get('gate', {}).get('pass_all', 0)} pass, weather unknown {c['L1'].get('weather_unknown', 0)}; "
            f"corroboration window: {c['corroboration_window'].get('passes_within_3h_absolute', 'n/a')} of "
            f"{c['corroboration_window'].get('radar_passes', 'n/a')} passes within 3 h of a VIIRS overpass")
        if a.dry_run:
            continue
        B.write_outputs(res, log=log)
    if not a.dry_run and not a.no_figure:
        B.figure(results, log=log)
    log(f"done in {time.time() - t0:.1f} s" + (" (dry run, nothing written)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
