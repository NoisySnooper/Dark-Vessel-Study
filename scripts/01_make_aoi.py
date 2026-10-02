"""Write the project AOI to data/aoi.gpkg (EPSG:4326 and UTM 48N layers).

Usage: python scripts/01_make_aoi.py [--aoi ca_mau|gulf_of_tonkin]
"""

import argparse

import geopandas as gpd
import pandas as pd

from darkvessel.aoi import aoi_gdf
from darkvessel.config import AOIS, DATA_DIR, DEFAULT_AOI
from darkvessel.io import write_dual_crs

ap = argparse.ArgumentParser()
ap.add_argument("--aoi", default=DEFAULT_AOI, choices=sorted(AOIS))
args = ap.parse_args()

out = DATA_DIR / "aoi.gpkg"
layers = write_dual_crs(aoi_gdf(args.aoi), out, "aoi")
# Keep the alternative AOIs in the same file for quick comparison in ArcGIS Pro.
alts = gpd.GeoDataFrame(pd.concat([aoi_gdf(k) for k in sorted(AOIS)], ignore_index=True), crs="EPSG:4326")
layers += write_dual_crs(alts, out, "aoi_candidates")
print(f"wrote {out} layers={layers}")
print(aoi_gdf(args.aoi).drop(columns="geometry").to_string(index=False))
