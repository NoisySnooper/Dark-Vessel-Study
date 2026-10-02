"""Spatial output helpers. Every vector product is written twice: EPSG:4326 and a UTM zone."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from darkvessel.config import CRS_GEO, CRS_UTM


def utm_suffix(crs: str = CRS_UTM) -> str:
    """Layer suffix for a UTM CRS, e.g. EPSG:32648 -> 'utm48n'."""
    code = int(str(crs).split(":")[-1])
    if 32601 <= code <= 32660:
        return f"utm{code - 32600}n"
    if 32701 <= code <= 32760:
        return f"utm{code - 32700}s"
    return f"epsg{code}"


def write_dual_crs(gdf: gpd.GeoDataFrame, path: str | Path, layer: str, utm_crs: str = CRS_UTM,
                   spatial_index: bool = True) -> list[str]:
    """Write `gdf` to a GeoPackage as two layers: <layer>_4326 and <layer>_utmNNx.

    Overwrites only the two named layers, so several products can share one file.
    spatial_index=False skips the R-tree (about a fifth of the file for point layers);
    ArcGIS Pro can add one with the Add Spatial Index tool. Returns the layer names written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS; refusing to guess.")
    names = []
    for crs, suffix in ((CRS_GEO, "4326"), (utm_crs, utm_suffix(utm_crs))):
        name = f"{layer}_{suffix}"
        gdf.to_crs(crs).to_file(path, layer=name, driver="GPKG", engine="pyogrio",
                                layer_options=None if spatial_index else {"SPATIAL_INDEX": "NO"})
        names.append(name)
    return names
