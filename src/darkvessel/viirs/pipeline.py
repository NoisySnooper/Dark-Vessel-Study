"""Night-pass VIIRS DNB light detection over the AOI: granule search, sea mask, detection, cloud screening.

Quality classes of a detected light:
  clear        the VIIRS cloud mask says clear or probably clear at the light
  under_cloud  probably cloudy or cloudy, kept only when the light is strong and isolated
               (spike >= 5 nW cm-2 sr-1 and isolation >= 8): lights shine through thin cloud,
               while moonlit cloud texture makes weak, poorly isolated spikes
Lights at sea are not all vessels: platforms, gas flares and island lights recur night after night
and are separated by persistence across nights (see scripts/15_viirs_lights.py).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

from darkvessel.viirs import access
from darkvessel.viirs.detect import spike_detect

R_KM = 6371.0088


class SeaGrid:
    """Open sea inside the AOI, beyond `buffer_deg` of Natural Earth land, on a fine lon/lat grid."""

    def __init__(self, aoi_geom, land_geoms, res: float = 0.01, buffer_deg: float = 0.02):
        w, s, e, n = aoi_geom.bounds
        self.res = res
        self.west, self.north = np.floor(w / res) * res, np.ceil(n / res) * res
        width = int(np.ceil((e - self.west) / res)) + 1
        height = int(np.ceil((self.north - s) / res)) + 1
        tr = from_origin(self.west, self.north, res, res)
        inner = aoi_geom.difference(shapely.union_all(list(land_geoms)).buffer(buffer_deg))
        self.mask = features.rasterize([(inner, 1)], out_shape=(height, width), transform=tr, dtype="uint8").astype(bool)

    def lookup(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        col = np.floor((lon - self.west) / self.res).astype(np.int64)
        row = np.floor((self.north - lat) / self.res).astype(np.int64)
        ok = (row >= 0) & (row < self.mask.shape[0]) & (col >= 0) & (col < self.mask.shape[1]) & (lat > -90)
        out = np.zeros(lon.shape, bool)
        out[ok] = self.mask[row[ok], col[ok]]
        return out


def find_aoi_granules(sat: str, day, aoi_geom, start_utc="16:00", end_utc="21:00", workers: int = 6) -> list[str]:
    """Descending (night) DNB granules of one UTC day whose outline touches the AOI.

    Reads every third outline first (a pass over the AOI spans about 6 granules), then the
    neighbours of every hit, so a night costs about 70 small metadata reads per satellite.
    """
    keys = access.geo_keys(sat, day, start_utc, end_utc)
    if not keys:
        return []
    rings = {}

    def ring(i):
        if i not in rings:
            try:
                rings[i] = access.gring(sat, keys[i])
            except Exception:  # noqa: BLE001 (one unreadable outline should not stop the night)
                rings[i] = None
        return rings[i]

    def hits(idx):
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(ring, idx))
        out = []
        for i in idx:
            r = rings.get(i)
            if r is None or r[2] != 1:
                continue
            poly = Polygon(zip(r[1], r[0]))
            if poly.is_valid and poly.intersects(aoi_geom):
                out.append(i)
        return out

    coarse = hits(range(0, len(keys), 3))
    near = sorted({j for i in coarse for j in range(i - 2, i + 3) if 0 <= j < len(keys)})
    return [keys[i] for i in hits(near)]


def detect_granule(gran: access.Granule, sea: SeaGrid) -> pd.DataFrame:
    """Lights at sea in one granule, cloud-screened, with position, time and conditions."""
    sea_px = sea.lookup(gran.lon, gran.lat)
    if not sea_px.any():
        return pd.DataFrame()
    det = spike_detect(gran.rad, sea_px)
    if det.empty:
        return det
    det["lat"] = gran.lat[det.row, det.col]
    det["lon"] = gran.lon[det.row, det.col]
    det["time_utc"] = gran.start + pd.to_timedelta(det.row / 768 * 85.0, unit="s")
    det["satellite"] = gran.sat
    det["granule"] = gran.geo_key.rsplit("/", 1)[-1][:40]
    det["moon_illum_pct"] = round(gran.moon_illum, 1)
    det["lunar_zenith_deg"] = round(gran.lunar_zenith_median, 1)
    if gran.cloud is not None:
        ok = gran.cloud_lat > -90
        lat0 = np.radians(float(np.median(det.lat)))
        xy = lambda lo, la: np.c_[np.radians(lo) * R_KM * np.cos(lat0), np.radians(la) * R_KM]  # noqa: E731
        d, i = cKDTree(xy(gran.cloud_lon[ok], gran.cloud_lat[ok])).query(xy(det.lon.to_numpy(), det.lat.to_numpy()))
        det["cloud_mask"] = np.where(d < 2.0, gran.cloud[ok][i], -1).astype(int)
    else:
        det["cloud_mask"] = -1
    clear = det.cloud_mask.isin([0, 1])
    strong = det.cloud_mask.isin([2, 3, -1]) & (det.isolation >= 8) & (det.spike_nw >= 5)
    det = det[clear | strong].copy()
    det["quality"] = np.where(det.cloud_mask.isin([0, 1]), "clear", "under_cloud")
    return det.reset_index(drop=True)


def light_sites(lights: pd.DataFrame, link_m: float = 500.0) -> pd.DataFrame:
    """Group recurring lights into sites: lights linked within `link_m` form one site (single linkage).

    Returns one row per site: median position, number of lights, distinct nights, median and maximum
    radiance, and the smallest Satlas distance among its lights (if the column is present).
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    if lights.empty:
        return pd.DataFrame(columns=["site", "lat", "lon", "n_lights", "nights", "radiance_med_nw", "radiance_max_nw"])
    lat0 = np.radians(float(lights.lat.median()))
    xy = np.c_[np.radians(lights.lon.to_numpy()) * R_KM * 1000 * np.cos(lat0), np.radians(lights.lat.to_numpy()) * R_KM * 1000]
    pairs = cKDTree(xy).query_pairs(link_m, output_type="ndarray")
    n = len(lights)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n))
    _, label = connected_components(graph, directed=False)
    g = lights.assign(site=label).groupby("site")
    out = g.agg(lat=("lat", "median"), lon=("lon", "median"), n_lights=("lat", "size"), nights=("night", "nunique"),
                radiance_med_nw=("radiance_nw", "median"), radiance_max_nw=("radiance_nw", "max"))
    if "satlas_infra_m" in lights:
        out["satlas_infra_m"] = g.satlas_infra_m.min()
    return out.reset_index()
