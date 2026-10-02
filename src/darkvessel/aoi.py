"""Area of interest definitions."""

from __future__ import annotations

import geopandas as gpd
import requests
from shapely import make_valid
from shapely.geometry import box
from shapely.ops import unary_union

from darkvessel.config import AOIS, CRS_EQUAL_AREA, CRS_GEO, DEFAULT_AOI, NE_LAND_URL, NE_MARINE_URL, RAW_DIR


def _cached(url: str, name: str):
    path = RAW_DIR / "natural_earth" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def natural_earth_marine() -> gpd.GeoDataFrame:
    """Natural Earth 10 m marine areas (public domain), cached under data/raw/."""
    return gpd.read_file(_cached(NE_MARINE_URL, "ne_10m_geography_marine_polys.geojson"))


def natural_earth_land(bbox=None) -> gpd.GeoDataFrame:
    """Natural Earth 10 m land polygons (public domain), cached under data/raw/."""
    return gpd.read_file(_cached(NE_LAND_URL, "ne_10m_land.geojson"), bbox=bbox)


def aoi_gdf(name: str = DEFAULT_AOI) -> gpd.GeoDataFrame:
    """Return the named AOI as a one-row GeoDataFrame in EPSG:4326."""
    spec = AOIS[name]
    if "natural_earth" in spec:
        marine = natural_earth_marine()
        parts = marine[marine["name"].isin(spec["natural_earth"])]
        missing = set(spec["natural_earth"]) - set(parts["name"])
        if missing:
            raise ValueError(f"Natural Earth marine areas not found: {sorted(missing)}")
        geom = make_valid(unary_union([make_valid(g) for g in parts.geometry]))
        source = "Natural Earth 10 m marine areas (public domain): " + ", ".join(spec["natural_earth"])
    else:
        geom = box(*spec["bbox"])
        source = "lon/lat box"
    west, south, east, north = geom.bounds
    gdf = gpd.GeoDataFrame(
        {"aoi_id": [name], "label": [spec["label"]], "source": [source],
         "west": [west], "south": [south], "east": [east], "north": [north]},
        geometry=[geom],
        crs=CRS_GEO,
    )
    gdf["area_km2"] = (gdf.to_crs(CRS_EQUAL_AREA).area / 1e6).round(0)
    return gdf


def aoi_geometry(name: str = DEFAULT_AOI):
    """Shapely (multi)polygon of the AOI in EPSG:4326."""
    return aoi_gdf(name).geometry.iloc[0]


def aoi_utm(name: str = DEFAULT_AOI) -> str:
    return AOIS[name]["utm"]
