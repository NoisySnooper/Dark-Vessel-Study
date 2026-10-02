"""Write the project AOI to data/aoi.gpkg (EPSG:4326 and the AOI's UTM zone).

Default AOI: South China Sea with the Gulf of Tonkin and Gulf of Thailand, from Natural
Earth 10 m marine areas (public domain). Sub-areas (Ca Mau, Gulf of Tonkin boxes) are kept
in layer aoi_candidates_* for detail work.
Usage: python scripts/01_make_aoi.py [--aoi south_china_sea|ca_mau|gulf_of_tonkin]
"""

import argparse

import geopandas as gpd
import pandas as pd

from darkvessel.aoi import aoi_gdf, aoi_utm
from darkvessel.config import AOIS, CRS_UTM_REGIONAL, DATA_DIR, DEFAULT_AOI
from darkvessel.io import write_dual_crs

ap = argparse.ArgumentParser()
ap.add_argument("--aoi", default=DEFAULT_AOI, choices=sorted(AOIS))
args = ap.parse_args()

out = DATA_DIR / "aoi.gpkg"
if out.exists():
    out.unlink()
layers = write_dual_crs(aoi_gdf(args.aoi), out, "aoi", utm_crs=aoi_utm(args.aoi))
alts = gpd.GeoDataFrame(pd.concat([aoi_gdf(k) for k in sorted(AOIS)], ignore_index=True), crs="EPSG:4326")
layers += write_dual_crs(alts, out, "aoi_candidates", utm_crs=CRS_UTM_REGIONAL)
print(f"wrote {out} layers={layers}")
print(alts.drop(columns="geometry").to_string(index=False))
