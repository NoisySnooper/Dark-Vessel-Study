"""Scene footprints: one schema for AWS-bucket records and STAC items, AOI overlap, summaries."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from darkvessel.config import CRS_GEO, CRS_UTM

COLUMNS = [
    "product_id", "platform", "mission", "mode", "polarization", "pols", "start_utc", "stop_utc",
    "orbit_abs", "orbit_rel", "pass_dir", "source", "path",
]


def records_to_gdf(records: list[dict]) -> gpd.GeoDataFrame:
    """Records from `s1.aws.search_aws` or `stac_items_to_records` -> GeoDataFrame (EPSG:4326)."""
    rows = []
    for r in records:
        rows.append(
            {
                "product_id": r["product_id"],
                "platform": r.get("platform") or "SENTINEL-1" + r["mission"][-1],
                "mission": r["mission"],
                "mode": r.get("mode"),
                "polarization": r.get("pol"),
                "pols": "+".join(r.get("pols") or []),
                "start_utc": pd.Timestamp(r["start"]),
                "stop_utc": pd.Timestamp(r["stop"]),
                "orbit_abs": r.get("orbit"),
                "orbit_rel": r.get("orbit_rel"),
                "pass_dir": (r.get("pass_dir") or "").upper() or None,
                "source": r.get("source"),
                "path": r.get("path"),
                "geometry": r["geometry"],
            }
        )
    if not rows:
        return gpd.GeoDataFrame(columns=COLUMNS + ["geometry"], geometry="geometry", crs=CRS_GEO)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS_GEO)


def stac_items_to_records(items: list[dict], source: str) -> list[dict]:
    """Map STAC item dicts (Planetary Computer or CDSE sentinel-1-grd) to the record schema.

    Uses only the standard `sat:` and `sar:` STAC extension fields, normalising case,
    because the two catalogues differ in capitalisation (e.g. 'SENTINEL-1C' vs 'sentinel-1c').
    """
    from darkvessel.s1.aws import parse_product_id

    out = []
    for it in items:
        p = it["properties"]
        pid = it["id"].removesuffix(".SAFE")
        try:
            rec = parse_product_id(pid)
        except ValueError:
            rec = {"product_id": pid, "mission": "S1" + str(p.get("platform", "?"))[-1].upper()}
            rec["start"] = p.get("start_datetime") or p.get("datetime")
            rec["stop"] = p.get("end_datetime") or p.get("datetime")
            rec["mode"] = p.get("sar:instrument_mode")
            rec["orbit"] = p.get("sat:absolute_orbit")
        rec.update(
            {
                "platform": str(p.get("platform", "")).upper() or None,
                "pass_dir": str(p.get("sat:orbit_state", "")).upper() or None,
                "orbit_rel": p.get("sat:relative_orbit"),
                "pols": [s.upper() for s in p.get("sar:polarizations", [])],
                "geometry": shape(it["geometry"]),
                "source": source,
                "path": it.get("links", [{}])[0].get("href") if it.get("links") else None,
            }
        )
        out.append(rec)
    return out


def add_aoi_overlap(gdf: gpd.GeoDataFrame, aoi_geom, utm_crs: str = CRS_UTM) -> gpd.GeoDataFrame:
    """Add aoi_overlap_km2 and aoi_coverage (fraction of the AOI inside each footprint)."""
    out = gdf.copy()
    aoi = gpd.GeoSeries([aoi_geom], crs=CRS_GEO).to_crs(utm_crs).iloc[0]
    fp = out.geometry.to_crs(utm_crs)
    inter = fp.intersection(aoi)
    out["aoi_overlap_km2"] = (inter.area / 1e6).round(1)
    out["aoi_coverage"] = (inter.area / aoi.area).round(3)
    return out


def summarize(gdf: gpd.GeoDataFrame) -> dict:
    """Counts by mission, pass direction and polarisation, plus acquisition dates."""
    if gdf.empty:
        return {"count": 0}
    dates = sorted(gdf["start_utc"].dt.strftime("%Y-%m-%d").unique())
    gaps = pd.Series(pd.to_datetime(dates)).diff().dt.days.dropna()
    return {
        "count": int(len(gdf)),
        "by_mission": gdf["mission"].value_counts().to_dict(),
        "by_pass": gdf["pass_dir"].value_counts().to_dict(),
        "by_pols": gdf["pols"].value_counts().to_dict(),
        "by_relative_orbit": {int(k): int(v) for k, v in gdf["orbit_rel"].value_counts().items()},
        "unique_dates": dates,
        "first": dates[0],
        "last": dates[-1],
        "median_gap_days": float(gaps.median()) if len(gaps) else None,
        "max_gap_days": float(gaps.max()) if len(gaps) else None,
    }
