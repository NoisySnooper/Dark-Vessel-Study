"""Find Sentinel-1C/1D GRD scenes over an AOI for the last N days.

Writes data/s1_footprints<sfx>.gpkg (EPSG:4326 + the AOI's UTM zone), data/s1_scenes<sfx>.csv
and data/s1_search_summary<sfx>.json, where <sfx> is empty for the default AOI (South China
Sea) and "_<aoi>" otherwise. Prints count, dates, orbit direction and polarisation.

Source: --source aws (works now), or stac_pc / stac_cdse once the network allows.
Usage: python scripts/02_search_scenes.py [--aoi south_china_sea|ca_mau|gulf_of_tonkin] [--days 90]
"""

import argparse
import datetime as dt
import json

from darkvessel.aoi import aoi_gdf, aoi_utm
from darkvessel.config import AOIS, DATA_DIR, DEFAULT_AOI, aoi_suffix
from darkvessel.io import write_dual_crs
from darkvessel.s1 import footprints as fp

ap = argparse.ArgumentParser()
ap.add_argument("--aoi", default=DEFAULT_AOI, choices=sorted(AOIS))
ap.add_argument("--days", type=int, default=90)
ap.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today UTC)")
ap.add_argument("--source", default="aws", choices=["aws", "stac_pc", "stac_cdse"])
ap.add_argument("--missions", default="S1C,S1D")
args = ap.parse_args()

end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(dt.timezone.utc).date()
start = end - dt.timedelta(days=args.days)
a = aoi_gdf(args.aoi)
aoi = a.geometry.iloc[0]
sfx = aoi_suffix(args.aoi)

if args.source == "aws":
    from darkvessel.s1.aws import search_aws, utc_windows_for

    windows = utc_windows_for(a.west.iloc[0], a.east.iloc[0])
    print("UTC pre-filter windows:", [(w[0].isoformat("minutes"), w[1].isoformat("minutes")) for w in windows])
    records = search_aws(aoi.simplify(0.02), start, end, missions=tuple(args.missions.split(",")),
                         utc_windows=windows, max_workers=24)
else:
    from darkvessel.s1.stac import search_stac

    endpoint = {"stac_pc": "planetary_computer", "stac_cdse": "cdse"}[args.source]
    records = fp.stac_items_to_records(search_stac(tuple(aoi.bounds), start, end, endpoint), endpoint)

gdf = fp.add_aoi_overlap(fp.records_to_gdf(records), aoi)
gdf["search_start"] = start.isoformat()
gdf["search_end"] = end.isoformat()
write_dual_crs(gdf, DATA_DIR / f"s1_footprints{sfx}.gpkg", "s1_footprints", utm_crs=aoi_utm(args.aoi))
gdf.drop(columns="geometry").to_csv(DATA_DIR / f"s1_scenes{sfx}.csv", index=False)

summary = fp.summarize(gdf)
summary.update({"aoi": args.aoi, "window": f"{start} to {end}", "source": args.source,
                "aoi_area_km2": float(a.area_km2.iloc[0])})
(DATA_DIR / f"s1_search_summary{sfx}.json").write_text(json.dumps(summary, indent=2, default=str))
print(json.dumps({k: v for k, v in summary.items() if k != "unique_dates"}, indent=2, default=str))
print("dates:", ", ".join(summary.get("unique_dates", [])))
