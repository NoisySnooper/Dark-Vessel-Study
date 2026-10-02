"""Area of interest definitions."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from darkvessel.config import AOIS, CRS_GEO, CRS_UTM, DEFAULT_AOI


def aoi_gdf(name: str = DEFAULT_AOI) -> gpd.GeoDataFrame:
    """Return the named AOI as a one-row GeoDataFrame in EPSG:4326."""
    spec = AOIS[name]
    west, south, east, north = spec["bbox"]
    gdf = gpd.GeoDataFrame(
        {
            "aoi_id": [name],
            "label": [spec["label"]],
            "west": [west],
            "south": [south],
            "east": [east],
            "north": [north],
        },
        geometry=[box(west, south, east, north)],
        crs=CRS_GEO,
    )
    gdf["area_km2"] = gdf.to_crs(CRS_UTM).area / 1e6
    return gdf


def aoi_geometry(name: str = DEFAULT_AOI):
    """Shapely polygon of the AOI in EPSG:4326."""
    return aoi_gdf(name).geometry.iloc[0]
