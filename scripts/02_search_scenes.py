"""Find Sentinel-1C/1D GRD scenes over the AOI for the last N days.

Writes data/s1_footprints.gpkg (EPSG:4326 + UTM 48N) and data/s1_scenes.csv, and
prints count, dates, orbit direction and polarisation.

Source order: --source aws (works now), or stac_pc / stac_cdse once the network allows.
Usage: python scripts/02_search_scenes.py [--days 90] [--aoi ca_mau] [--source aws]
"""

import argparse
import datetime as dt
import json

from darkvessel.aoi import aoi_geometry
from darkvessel.config import AOIS, DATA_DIR, DEFAULT_AOI
from darkvessel.io import write_dual_crs
from darkvessel.s1 import footprints as fp

ap = argparse.ArgumentParser()
ap.add_argument("--aoi", default=DEFAULT_AOI, choices=sorted(AOIS))
ap.add_argument("--days", type=int, default=90)
ap.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today UTC)")
ap.add_argument("--source", default="aws", choices=["aws", "stac_pc", "stac_cdse"])
args = ap.parse_args()

end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(dt.timezone.utc).date()
start = end - dt.timedelta(days=args.days)
aoi = aoi_geometry(args.aoi)

if args.source == "aws":
    from darkvessel.s1.aws import search_aws

    records = search_aws(aoi, start, end)
else:
    from darkvessel.s1.stac import search_stac

    endpoint = {"stac_pc": "planetary_computer", "stac_cdse": "cdse"}[args.source]
    records = fp.stac_items_to_records(search_stac(AOIS[args.aoi]["bbox"], start, end, endpoint), endpoint)

gdf = fp.add_aoi_overlap(fp.records_to_gdf(records), aoi)
gdf["search_start"] = start.isoformat()
gdf["search_end"] = end.isoformat()
write_dual_crs(gdf, DATA_DIR / "s1_footprints.gpkg", "s1_footprints")
gdf.drop(columns="geometry").to_csv(DATA_DIR / "s1_scenes.csv", index=False)

summary = fp.summarize(gdf)
summary.update({"aoi": args.aoi, "window": f"{start} to {end}", "source": args.source})
(DATA_DIR / "s1_search_summary.json").write_text(json.dumps(summary, indent=2, default=str))
print(json.dumps({k: v for k, v in summary.items() if k != "unique_dates"}, indent=2, default=str))
print("dates:", ", ".join(summary.get("unique_dates", [])))
