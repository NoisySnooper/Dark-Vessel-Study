"""Static ocean context layers: depth, distance to coast, ports, EEZ lines and AIS shipping density.

Sources (all read anonymously; licences recorded in SOURCES and checked by scripts/22_static_layers.py):
  GEBCO_2026 Grid, 15 arc-second, netCDF held at CEDA/BODC. The file is one contiguous int16 array (43200 x 86400),
      so the AOI rows are a single byte range of the file: fetched once, sliced, then deleted.
  Natural Earth 10 m land (coastline) and 10 m ports, public domain.
  NGA World Port Index (Pub 150) through the MSI publications API (CSV).
  Marine Regions Maritime Boundaries v12 (VLIZ) EEZ polygons and boundary lines through the VLIZ WFS, as published.
  World Bank / IMF Global Shipping Traffic Density: six vessel-type layers (all, commercial, fishing, oil and gas,
      passenger, leisure) of 0.005 degree cells, Jan 2015 to Feb 2021, as zipped GeoTIFFs read through GDAL /vsizip/.
      The readme calls the values AIS position counts, but in five of the six layers a large share of the values cannot
      be counts (SHIP_DENSITY_WARNING), so only presence (value > 0) is used downstream.

Pure grid helpers (bin_to_grid, sphere_distance_km, depth_contours, label_raster, model_cell_table) take transforms
and arrays, so tests run on small synthetic grids without network.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
from shapely.geometry import LineString, box
from skimage.measure import find_contours

from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL
from darkvessel.coverage import R_EARTH_KM
from darkvessel.ocean.grid import OCEAN_CACHE

ACCESS_DATE = "2026-10-08"

# GEBCO_2026 at CEDA: 15 arc-second cells, pixel-centre registered, lat ascending from -90, lon from -180.
GEBCO_URL = "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc"
GEBCO_CELLS_PER_DEG = 240
GEBCO_SHAPE = (43200, 86400)
GEBCO_ROW_BYTES = GEBCO_SHAPE[1] * 2

WPI_URL = "https://msi.nga.mil/api/publications/world-port-index?output=csv"
NE_PORTS_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_ports.geojson"
VLIZ_WFS = "https://geo.vliz.be/geoserver/MarineRegions/wfs"
MR_VERSION = "Maritime Boundaries v12 (2023-10-25), doi:10.14284/632"
WB_BASE = "https://datacatalogfiles.worldbank.org/ddh-published/0037580/5"
WB_FILES = {"all": f"{WB_BASE}/DR0045406/shipdensity_global.zip",
            "fishing": f"{WB_BASE}/DR0045403/ShipDensity_Fishing.zip",
            "commercial": f"{WB_BASE}/DR0045405/shipdensity_commercial_.zip",
            "oilgas": f"{WB_BASE}/DR0045402/ShipDensity_OilGas.zip",
            "passenger": f"{WB_BASE}/DR0045404/ShipDensity_Passenger.zip",
            "leisure": f"{WB_BASE}/DR0045401/ShipDensity_Leisure.zip"}
SHIP_DENSITY_UNIT = ("values as published by the World Bank/IMF (the readme: 'total number of AIS positions' per 0.005 "
                     "degree cell, January 2015 to February 2021), summed over the four source cells whose centres fall in "
                     "the 0.01 degree cell; in five of the six layers many values cannot be counts (see the shipping "
                     "warning), so read only value > 0 (presence), not the magnitude")
# Hours from 2015-01-01 to 2021-03-01 (2,251 days): at one position per ship per hour ('hourly AIS positions' in the
# readme), the most one ship can add to a cell over the whole period.
WB_PERIOD_HOURS = 2251 * 24
SHIP_DENSITY_WARNING = (
    "Values UNVERIFIED as counts. The World Bank readme says each cell holds 'the total number of AIS positions' from "
    "hourly AIS, January 2015 to February 2021, but in five of the six layers a large share of the values cannot be "
    "counts: (1) in the raw leisure GeoTIFF, window 103.5 to 104.5E, 1.0 to 1.5N (Singapore Strait), all 1,857 nonzero "
    "0.005 degree cells lie between 151,669 and 430,408, every value is distinct and none is below 100,000; the file's "
    "own histogram (.aux.xml, 256 buckets of 11,706 from 0 to 2,996,838) has two flat plateaus, about 19,000 cells per "
    "bucket up to 0.69 million and about 10,100 per bucket up to 2.29 million, then almost none, where counts would "
    "thin out as the value rises; (2) over AOI sea 0.01 degree cells the nonzero values form two modes with a gap "
    "between them: values above 10,000 are 50 % of the nonzero cells in 'all' and 'commercial' and 100 % in 'oilgas' "
    "and 'leisure'; 'all' has 155 of 1,976,693 nonzero cells between 10^2.5 and 10^4; 'fishing' has 158 of 22,317 "
    "nonzero cells above 10^5.5 and none between 10^3 and 10^5.5; only 'passenger' has a continuous tail (1 to "
    "133,847); (3) along the row 11.99 to 12.00N, 111.0 to 112.5E of the 'all' layer, 97 of 150 cells hold 1 to 10, "
    "while 15 separate cells hold near-identical values from 10,709,621 to 10,711,838 and one holds 21,423,646, exactly "
    "2 x 10,711,823; (4) 46 % of the nonzero 'all' cells more than 100 km from a major port exceed 54,024, the number of "
    "hours in the period, which at one position per ship per hour would need more than one ship in that 1 km cell for "
    "every hour of six years. Magnitude, rank and log scale are therefore not usable. Presence (value > 0) is the only "
    "reading used here, for every layer, and even that assumes a zero means no AIS record. The encoding is UNVERIFIED "
    "until the World Bank data team confirms it.")
SHIP_PRESENCE_RULE = ("ship_presence_share_<type> = share of the model cell's AOI sea 0.01 degree cells whose World Bank "
                      "value is above 0 (any AIS record of that vessel type in the four 0.005 degree source cells, "
                      "2015 to 2021); the magnitude is not used")
# Vessel types per layer, as listed in the catalogue readme (DR0045407/readme_ddh.txt); 'leisure' is 'Pleasure' there.
WB_TYPES = {"all": "all ship types combined",
            "commercial": "cargo, tanker, container, bulk, tug, supply, offshore, research, patrol and other working ships",
            "fishing": "FISHING VESSEL, TRAWLER", "oilgas": "PLATFORM, FLOATING STORAGE/PRODUCTION, DRILLING JACK UP, "
            "DRILLING RIG, WELL STIMULATION VESSEL", "passenger": "PASSENGER SHIP, RO-RO/PASSENGER SHIP",
            "leisure": "YACHT, SAILING VESSEL"}
WB_README = "https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0084213/readme.txt"
WB_TYPES_README = "https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0045407/readme_ddh.txt"

# WPI harbour size codes as labelled in the MSI World Port Index query form (msi.nga.mil/Publications/WPI).
WPI_HARBOUR_SIZE = {"L": "Large", "M": "Medium", "S": "Small", "V": "Very Small"}
MAJOR_PORT_RULE = ("major = WPI harbour size L (Large) or M (Medium), or any Natural Earth 10 m port; "
                   "WPI S (Small) and V (Very Small) are kept in the layer but not used for dist_port_km")

# Key, name, URL, version, licence as published. 'resolved' is filled at run time by check_sources().
SOURCES = [
    {"key": "gebco", "name": "GEBCO_2026 Grid (15 arc-second global terrain model), netCDF at CEDA/BODC", "url": GEBCO_URL,
     "version": "GEBCO_2026, date_created 2026-04-17, doi:10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa",
     "licence": "GEBCO terms of use: 'The GEBCO Grid is placed in the public domain and may be used free of charge'; "
                "users may copy, adapt and commercially exploit it and must acknowledge the source; not for navigation",
     "licence_url": "https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use",
     "citation": "GEBCO Bathymetric Compilation Group 2026 (2026). The GEBCO_2026 Grid - a continuous terrain model for "
                 "oceans and land at 15 arc-second intervals. doi:10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa"},
    {"key": "ne_land", "name": "Natural Earth 10 m land polygons (coastline)",
     "url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson",
     "version": "natural-earth-vector master, VERSION file 5.2.0-pre", "licence": "Public domain",
     "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/"},
    {"key": "ne_ports", "name": "Natural Earth 10 m ports", "url": NE_PORTS_URL,
     "version": "natural-earth-vector master, VERSION file 5.2.0-pre", "licence": "Public domain",
     "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/"},
    {"key": "wpi", "name": "NGA World Port Index (Pub 150), Maritime Safety Information publications API", "url": WPI_URL,
     "version": "as served by the MSI API on the access date; the WPI is maintained continuously and the API output "
                "carries no edition number",
     "licence": "msi.nga.mil Commercial Use Warning: 'NGA claims no copyright or other intellectual property right in "
                "the nautical products posted on this website for public use'; the NGA name, seal or initials may not "
                "be used to imply endorsement (10 U.S.C. 425). Legal disclaimer: provided 'as is', no warranty.",
     "licence_url": "https://msi.nga.mil/api/pageContent/getByTitle?pageTitle=Commercial%20Use%20Warning"},
    {"key": "marineregions",
     "name": "Marine Regions Maritime Boundaries v12: EEZ polygons and boundary lines (Flanders Marine Institute, VLIZ)",
     "url": f"{VLIZ_WFS}?service=WFS&version=2.0.0&request=GetCapabilities",
     "version": "Maritime Boundaries v12, record date 2023-10-25 (WFS layers MarineRegions:eez 'Exclusive Economic Zones "
                "(200 NM) (v12, world, 2023)' and MarineRegions:eez_boundaries 'Maritime Boundaries (v12, world, 2023)'), "
                "doi:10.14284/632",
     "licence": "CC BY 4.0 (dataset record: 'This dataset is licensed under a Creative Commons Attribution 4.0 "
                "International License'). Terms of use on marineregions.org/disclaimer.php: not meant for legal, "
                "economical or navigational purposes; 'VLIZ expresses no opinion about the legal state neither of any "
                "country, territory or area nor concerning its delimitation'; users are kindly asked not to make the "
                "products available for download elsewhere; where these terms differ from the CC licence, the CC licence "
                "prevails",
     "licence_url": "https://www.marineregions.org/disclaimer.php",
     "citation": "Flanders Marine Institute (2023). Maritime Boundaries Geodatabase: Maritime Boundaries and Exclusive "
                 "Economic Zones (200NM), version 12. Available online at https://www.marineregions.org/. "
                 "https://doi.org/10.14284/632"},
    {"key": "worldbank",
     "name": "World Bank / IMF Global Shipping Traffic Density (AIS-derived layers Jan 2015 to Feb 2021, 0.005 degree; "
             "values not usable as counts, presence only)",
     "url": "https://datacatalog.worldbank.org/search/dataset/0037580",
     "version": "catalogue version 5, metadata last updated 2023-01-18; zips listed as last updated 2021-05-03 on the "
                "catalogue page, HTTP Last-Modified 2025-02-27 on the file server",
     "licence": "Creative Commons Attribution 4.0 (catalogue page: 'This dataset is licensed under Creative Commons "
                "Attribution 4.0')",
     "licence_url": "https://datacatalog.worldbank.org/public-licenses?fragment=cc",
     "citation": "World Bank Group, with IMF (Cerdeiro, Komaromi, Liu and Saeed, 2020), Global Shipping Traffic "
                 "Density, https://datacatalog.worldbank.org/search/dataset/0037580"},
]


# ---------------------------------------------------------------- downloads

def download(url: str, path: Path, timeout: int = 120, retries: int = 5, headers: dict | None = None) -> Path:
    """Stream `url` to `path` (resumable with Range on a .part file). Returns path; skips when it exists."""
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(retries):
        try:
            have = part.stat().st_size if part.exists() else 0
            h = dict(headers or {})
            if have:
                h["Range"] = f"bytes={have}-"
            with requests.get(url, stream=True, timeout=timeout, headers=h) as r:
                if have and r.status_code == 200:  # server ignored the range: start over
                    have = 0
                r.raise_for_status()
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(8 << 20):
                        f.write(chunk)
            part.rename(path)
            return path
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):  # noqa: PERF203
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def fetch_range(url: str, start: int, end: int, path: Path) -> Path:
    """Byte range [start, end] of `url` to `path`, resumable."""
    path = Path(path)
    if path.exists():
        return path
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(5):
        have = part.stat().st_size if part.exists() else 0
        if have >= end - start + 1:
            break
        try:
            with requests.get(url, stream=True, timeout=120, headers={"Range": f"bytes={start + have}-{end}"}) as r:
                r.raise_for_status()
                if r.status_code != 206:
                    raise RuntimeError(f"{url}: server did not honour the Range header")
                with open(part, "ab") as f:
                    for chunk in r.iter_content(8 << 20):
                        f.write(chunk)
        except (requests.ConnectionError, requests.Timeout):  # noqa: PERF203
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    if part.stat().st_size != end - start + 1:
        raise RuntimeError(f"{path}: got {part.stat().st_size} bytes, expected {end - start + 1}")
    part.rename(path)
    return path


# naturalearthdata.com answers 406 to the default python-requests user agent, journals.ametsoc.org 403 to a bare one
CHECK_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) darkvessel-source-check/1.0", "Accept": "*/*"}


def check_sources(sources: list[dict] = SOURCES, access_date: str = ACCESS_DATE) -> list[dict]:
    """Resolve every source URL and licence URL now (status < 400 counts as resolved), with the time of the check."""
    out = []
    for s in sources:
        rec = dict(s)
        rec.setdefault("access_date", access_date)
        rec["checked_utc"] = utc_now()
        for key, flag in (("url", "resolved"), ("licence_url", "licence_url_resolved")):
            if not rec.get(key):
                continue
            try:
                for h in ({"Range": "bytes=0-0"}, {}):  # some servers refuse a range on a page; retry without it
                    r = requests.get(rec[key], timeout=60, stream=True, headers={**CHECK_HEADERS, **h})
                    r.close()
                    if r.status_code < 400:
                        break
                rec[flag] = r.status_code < 400
                rec[f"{flag}_status"] = r.status_code
            except requests.RequestException as e:  # noqa: BLE001
                rec[flag] = False
                rec[f"{flag}_status"] = repr(e)[:120]
        out.append(rec)
    return out


# ---------------------------------------------------------------- GEBCO depth

def gebco_window(bounds, pad: float = 0.5) -> tuple[int, int, int, int]:
    """(row0, row1, col0, col1) of GEBCO cells covering lon/lat `bounds` grown by `pad` degrees (rows from the south)."""
    west, south, east, north = bounds
    n = GEBCO_CELLS_PER_DEG
    r0, r1 = int(np.floor((south - pad + 90) * n)), int(np.ceil((north + pad + 90) * n))
    c0, c1 = int(np.floor((west - pad + 180) * n)), int(np.ceil((east + pad + 180) * n))
    return max(r0, 0), min(r1, GEBCO_SHAPE[0]), max(c0, 0), min(c1, GEBCO_SHAPE[1])


def gebco_window_transform(r1: int, c0: int):
    """North-up transform of the window whose top row is GEBCO row r1 - 1 and first column c0."""
    res = 1.0 / GEBCO_CELLS_PER_DEG
    return from_origin(-180.0 + c0 * res, -90.0 + r1 * res, res, res)


def gebco_header(url: str = GEBCO_URL) -> tuple[int, dict]:
    """(byte offset of the contiguous elevation array, global attributes) read from the remote HDF5 header."""
    import h5py

    from darkvessel.viirs.access import RangeFile

    with RangeFile(url, block=256 * 1024) as f, h5py.File(f, "r") as h:
        e = h["elevation"]
        if e.chunks is not None or e.dtype != np.int16 or e.shape != GEBCO_SHAPE:
            raise RuntimeError("GEBCO file layout changed: expected a contiguous int16 43200 x 86400 array")
        attrs = {k: (v.decode() if isinstance(v, bytes) else (v.tolist() if hasattr(v, "tolist") else v))
                 for k, v in h.attrs.items()}
        return int(e.id.get_offset()), attrs


def gebco_subset(bounds, cache: Path = OCEAN_CACHE / "gebco", pad: float = 0.5, url: str = GEBCO_URL):
    """(elevation int16 north-up, transform, attrs) for the AOI window, cached as one .npz; the raw rows are deleted."""
    r0, r1, c0, c1 = gebco_window(bounds, pad)
    cache = Path(cache)
    sub = cache / f"gebco_2026_r{r0}_{r1}_c{c0}_{c1}.npz"
    if sub.exists():
        z = np.load(sub, allow_pickle=False)
        return z["elevation"], gebco_window_transform(r1, c0), json.loads(str(z["attrs"]))
    cache.mkdir(parents=True, exist_ok=True)
    offset, attrs = gebco_header(url)
    raw = cache / f"gebco_2026_rows{r0}_{r1}.int16"
    fetch_range(url, offset + r0 * GEBCO_ROW_BYTES, offset + r1 * GEBCO_ROW_BYTES - 1, raw)
    rows = np.memmap(raw, dtype="<i2", mode="r", shape=(r1 - r0, GEBCO_SHAPE[1]))
    elev = np.ascontiguousarray(rows[:, c0:c1][::-1])  # GEBCO rows run south to north; products are north-up
    del rows
    np.savez_compressed(sub, elevation=elev, attrs=json.dumps(attrs))
    raw.unlink()
    return elev, gebco_window_transform(r1, c0), attrs


# ---------------------------------------------------------------- grid algebra

def bin_to_grid(src: np.ndarray, src_transform, dst_transform, dst_shape, valid: np.ndarray | None = None,
                chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Sum and count of source cells, by the destination cell that contains each source cell centre.

    Both grids are north-up lon/lat. Source cells outside the destination grid or with valid False are skipped.
    Works in row chunks so a 40-million-cell source never needs a full index array.
    """
    h, w = src.shape
    H, W = dst_shape
    lon_c = src_transform.c + (np.arange(w) + 0.5) * src_transform.a
    lat_c = src_transform.f + (np.arange(h) + 0.5) * src_transform.e
    col = np.floor((lon_c - dst_transform.c) / dst_transform.a).astype(np.int64)
    row = np.floor((lat_c - dst_transform.f) / dst_transform.e).astype(np.int64)
    okc, okr = (col >= 0) & (col < W), (row >= 0) & (row < H)
    sums, cnt = np.zeros(H * W), np.zeros(H * W)
    for i0 in range(0, h, chunk):
        i1 = min(h, i0 + chunk)
        rr = okr[i0:i1]
        if not rr.any():
            continue
        blk = np.asarray(src[i0:i1][rr][:, okc], dtype=np.float64)
        v = np.isfinite(blk) if valid is None else (valid[i0:i1][rr][:, okc] & np.isfinite(blk))
        flat = (row[i0:i1][rr][:, None] * W + col[okc][None, :])[v]
        sums += np.bincount(flat, weights=blk[v], minlength=H * W)
        cnt += np.bincount(flat, minlength=H * W)
    return sums.reshape(dst_shape), cnt.reshape(dst_shape)


def depth_on_grid(elev: np.ndarray, elev_transform, transform, shape) -> np.ndarray:
    """Mean depth (m, positive down) of the below-sea-level source cells in each cell; NaN where none."""
    s, n = bin_to_grid(-elev.astype(np.float32), elev_transform, transform, shape, valid=elev < 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan).astype(np.float32)


def unit_vectors(lon, lat) -> np.ndarray:
    lo, la = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def sphere_distance_km(lon, lat, pts_lon, pts_lat) -> np.ndarray:
    """Great-circle distance (km) from each (lon, lat) to the nearest of the points: 3-D KD-tree on unit vectors.

    Longitude convergence is handled by construction; no projection is needed over the whole box.
    """
    lon, lat = np.asarray(lon, float).ravel(), np.asarray(lat, float).ravel()
    out = np.full(lon.shape, np.nan)
    if len(lon) == 0 or len(np.asarray(pts_lon)) == 0:
        return out
    chord, _ = cKDTree(unit_vectors(pts_lon, pts_lat)).query(unit_vectors(lon, lat), workers=-1)
    return 2.0 * R_EARTH_KM * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))


def coast_points(land: gpd.GeoDataFrame, bounds, step_deg: float = 0.002) -> np.ndarray:
    """Coastline vertices (lon, lat), densified to `step_deg`, inside `bounds`.

    The boundary is taken before clipping, so the clip box adds no false coast segments.
    """
    lines = gpd.GeoSeries(land.geometry.boundary, crs=land.crs).clip(box(*bounds))
    lines = lines[~lines.is_empty]
    if not len(lines):
        return np.zeros((0, 2))
    return shapely.get_coordinates(shapely.segmentize(lines.union_all(), step_deg))


def depth_contours(depth: np.ndarray, transform, levels=(50, 200, 1000), sigma: float = 1.0,
                   simplify_deg: float = 0.003, min_len_deg: float = 0.1) -> gpd.GeoDataFrame:
    """Depth contour lines from a north-up depth grid (NaN = land, treated as 0 m), lightly smoothed and simplified.

    Rings and lines shorter than `min_len_deg` (degrees of lon/lat path length) are dropped.
    """
    z = gaussian_filter(np.where(np.isfinite(depth), depth, 0.0).astype(np.float64), sigma) if sigma else \
        np.where(np.isfinite(depth), depth, 0.0)
    rows = []
    for lev in levels:
        for c in find_contours(z, float(lev)):
            if len(c) < 2:
                continue
            lon = transform.c + (c[:, 1] + 0.5) * transform.a
            lat = transform.f + (c[:, 0] + 0.5) * transform.e
            line = LineString(np.c_[lon, lat]).simplify(simplify_deg, preserve_topology=False)
            if line.length >= min_len_deg and not line.is_empty:
                rows.append({"depth_m": int(lev), "geometry": line})
    gdf = gpd.GeoDataFrame(rows, columns=["depth_m", "geometry"], geometry="geometry", crs=CRS_GEO)
    gdf["length_km"] = geodesic_length_km(gdf.geometry.values) if len(gdf) else pd.Series(dtype=float)
    return gdf


def geodesic_length_km(geoms) -> np.ndarray:
    """Length of lon/lat lines on the WGS 84 ellipsoid (km, 0.1 km precision): no projection scale error."""
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    return np.round([geod.geometry_length(g) / 1000.0 for g in geoms], 1)


def slope_m_per_km(depth: np.ndarray, transform) -> np.ndarray:
    """Magnitude of the depth gradient (m per km) on a lon/lat grid; NaN where any neighbour is NaN."""
    h, w = depth.shape
    lat = transform.f + (np.arange(h) + 0.5) * transform.e
    dy_km = abs(transform.e) * 111.32
    dx_km = abs(transform.a) * 111.32 * np.cos(np.radians(lat))[:, None]
    gy, gx = np.gradient(depth.astype(np.float64))
    return np.hypot(gx / dx_km, gy / dy_km).astype(np.float32)


# ---------------------------------------------------------------- ports

_DMS = re.compile(r"^\s*(\d+)°\s*(\d+)'\s*(\d+(?:\.\d+)?)\"\s*([NSEW])\s*$")


def dms_to_deg(s) -> float:
    """WPI coordinate string like 30°20'00\"N to decimal degrees (NaN if unparseable)."""
    m = _DMS.match(str(s)) if isinstance(s, str) else None
    if not m:
        return np.nan
    v = int(m[1]) + int(m[2]) / 60.0 + float(m[3]) / 3600.0
    return -v if m[4] in "SW" else v


def wpi_ports(csv_path: Path, bounds) -> gpd.GeoDataFrame:
    """NGA World Port Index ports inside `bounds`, with the fields this project keeps."""
    d = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    d["lat"], d["lon"] = d.latitude.map(dms_to_deg), d.longitude.map(dms_to_deg)
    west, south, east, north = bounds
    d = d[(d.lon >= west) & (d.lon <= east) & (d.lat >= south) & (d.lat <= north)].copy()
    out = pd.DataFrame({
        "name": d.portName.values, "country": d.countryName.values, "harbour_size": d.harborSize.values,
        "harbour_size_label": d.harborSize.map(WPI_HARBOUR_SIZE).values, "harbour_type": d.harborType.values,
        "wpi_port_number": pd.to_numeric(d.portNumber, errors="coerce").astype("Int64").values,
        "unlocode": d.unloCode.values if "unloCode" in d else None, "ne_scalerank": pd.array([pd.NA] * len(d), dtype="Int64"),
        "source": "NGA World Port Index (Pub 150), msi.nga.mil", "licence": "US Government work; NGA claims no copyright",
        "lon": d.lon.values, "lat": d.lat.values})
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(out.lon, out.lat), crs=CRS_GEO)


def ne_ports(path: Path, bounds) -> gpd.GeoDataFrame:
    """Natural Earth 10 m ports inside `bounds` (no country field is published, so country is empty)."""
    g = gpd.read_file(path, bbox=tuple(bounds))
    out = pd.DataFrame({
        "name": g["name"].values, "country": None, "harbour_size": None, "harbour_size_label": None, "harbour_type": None,
        "wpi_port_number": pd.array([pd.NA] * len(g), dtype="Int64"), "unlocode": None,
        "ne_scalerank": pd.to_numeric(g["scalerank"], errors="coerce").astype("Int64").values,
        "source": "Natural Earth 10 m ports", "licence": "Public domain",
        "lon": g.geometry.x.values, "lat": g.geometry.y.values})
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(out.lon, out.lat), crs=CRS_GEO)


def is_major_port(ports: pd.DataFrame) -> np.ndarray:
    """MAJOR_PORT_RULE as a boolean array."""
    wpi = ports.source.str.startswith("NGA").values
    size = ports.harbour_size.fillna("").values
    return np.where(wpi, np.isin(size, ["L", "M"]), True)


# ---------------------------------------------------------------- EEZ

def eez_layer(geojson_path: Path, bounds, simplify_m: float = 0.0, version: str = MR_VERSION,
              licence: str = "CC BY 4.0") -> tuple[gpd.GeoDataFrame, dict]:
    """Marine Regions features clipped to `bounds`, every published attribute kept, plus source labels.

    The geometry is the published geometry cut to the box. With `simplify_m` > 0 each feature is also simplified in
    UTM 49N with that tolerance (topology preserved, made valid). Returns (layer, stats) where stats holds the vertex
    counts before and after simplification and the largest relative area change.
    """
    import pyogrio

    pyogrio.set_gdal_config_options({"OGR_GEOJSON_MAX_OBJ_SIZE": "0"})
    g = gpd.read_file(geojson_path)
    g = g.rename(columns={"id": "wfs_id"})
    g = g.clip(box(*bounds), keep_geom_type=True)
    g = g[~g.geometry.is_empty & g.geometry.notna()].copy()
    stats = {"features": int(len(g)), "vertices_published_clipped": int(len(shapely.get_coordinates(g.geometry.values))),
             "simplify_m": float(simplify_m)}
    if simplify_m:
        utm = g.to_crs(CRS_UTM_REGIONAL).geometry
        simp = gpd.GeoSeries(shapely.make_valid(utm.simplify(simplify_m, preserve_topology=True).values), index=g.index,
                             crs=CRS_UTM_REGIONAL)
        if g.geom_type.str.contains("Polygon").all():
            simp = simp.apply(lambda x: shapely.union_all([p for p in getattr(x, "geoms", [x]) if p.geom_type in
                                                           ("Polygon", "MultiPolygon")]) if x.geom_type == "GeometryCollection" else x)
            with np.errstate(invalid="ignore", divide="ignore"):
                rel = np.abs(simp.area.values / utm.area.values - 1.0)
            stats["max_relative_area_change"] = float(np.nanmax(rel)) if len(rel) else 0.0
        g = g.set_geometry(simp.to_crs(CRS_GEO))
    stats["vertices_written"] = int(len(shapely.get_coordinates(g.geometry.values)))
    g["source"] = "Marine Regions Maritime Boundaries Geodatabase (Flanders Marine Institute, VLIZ), WFS geo.vliz.be"
    g["version"] = version
    g["licence"] = licence
    g["access_date"] = ACCESS_DATE
    return g.reset_index(drop=True), stats


def label_raster(gdf: gpd.GeoDataFrame, transform, shape) -> np.ndarray:
    """int32 grid of the row number of the feature that contains each cell centre (-1 for none; later rows win)."""
    from rasterio import features

    if not len(gdf):
        return np.full(shape, -1, np.int32)
    return features.rasterize(((geom, i) for i, geom in enumerate(gdf.geometry.values)), out_shape=shape,
                              transform=transform, fill=-1, dtype="int32")


# ---------------------------------------------------------------- shipping density

def shipping_window(zip_path: Path, bounds, pad: float = 0.02):
    """(values int32 as published, transform, nodata) of the World Bank density GeoTIFF inside a zip, over `bounds` + pad."""
    import rasterio
    from rasterio.windows import from_bounds

    tifs = [i.filename for i in zipfile.ZipFile(zip_path).infolist() if i.filename.lower().endswith(".tif")]
    if len(tifs) != 1:
        raise RuntimeError(f"{zip_path}: expected one .tif, found {tifs}")
    west, south, east, north = bounds
    with rasterio.open(f"/vsizip/{zip_path}/{tifs[0]}") as ds:
        win = from_bounds(west - pad, south - pad, east + pad, north + pad, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win)
        return arr, ds.window_transform(win), ds.nodata


def density_on_grid(counts: np.ndarray, counts_transform, nodata, transform, shape) -> np.ndarray:
    """Sum of the source values as published per destination cell (NaN where no valid source cell); see
    SHIP_DENSITY_WARNING: the sum keeps presence (> 0) exact, its magnitude is not a count."""
    valid = (counts != nodata) if nodata is not None else np.ones(counts.shape, bool)
    s, n = bin_to_grid(counts, counts_transform, transform, shape, valid=valid)
    return np.where(n > 0, s, np.nan).astype(np.float32)


# ---------------------------------------------------------------- model-grid table

def model_cell_table(fine: dict[str, np.ndarray], sea: np.ndarray, fine_transform, model_transform, model_shape,
                     in_aoi: np.ndarray | None = None, cell_area_km2: np.ndarray | None = None,
                     aoi_centre: np.ndarray | None = None, labels: dict | None = None,
                     shares: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
    """One row per model cell of the AOI: AOI and sea shares, depth statistics, distances, densities, labels.

    `fine` holds fine-grid float arrays (depth_m, slope_m_per_km, dist_coast_km, dist_port_km, ship_density_*), summarised
    over the cells of `sea` (sea inside the AOI); a ship_density_<type> layer gives only ship_presence_share_<type>
    (share of finite sea cells above 0; SHIP_DENSITY_WARNING). `shares` maps a column name to a fine-grid 0/1 (NaN
    unknown) array whose mean over the sea cells becomes that column. `in_aoi` is the fine-grid AOI mask (default `sea`); `aoi_centre` is the
    model-grid mask of cells whose centre lies in the AOI. A model cell gets a row when it holds an AOI fine cell or its
    centre is in the AOI, so cells that are all land still appear (n_sea 0, sea fields NaN).
    `labels` maps a name to (code, lookup): `code` is a fine-grid int array (-1 none), `lookup` a DataFrame indexed by
    code; the table gets <name>_<column> of the code that covers most sea fine cells, <name>_share (its share of them)
    and <name>_n (distinct codes among them). Fine cells must nest in model cells.
    """
    H, W = model_shape
    fh, fw = sea.shape
    in_aoi = sea if in_aoi is None else in_aoi
    lon_c = fine_transform.c + (np.arange(fw) + 0.5) * fine_transform.a
    lat_c = fine_transform.f + (np.arange(fh) + 0.5) * fine_transform.e
    col = np.floor((lon_c - model_transform.c) / model_transform.a).astype(np.int64)
    row = np.floor((lat_c - model_transform.f) / model_transform.e).astype(np.int64)
    per_cell = int(round((model_transform.a / fine_transform.a) * (abs(model_transform.e) / abs(fine_transform.e))))
    okr, okc = (row >= 0) & (row < H), (col >= 0) & (col < W)
    cell_grid = np.where(okr[:, None] & okc[None, :], row[:, None] * W + col[None, :], -1)

    def count(mask):
        c = cell_grid[mask & (cell_grid >= 0)]
        return np.bincount(c, minlength=H * W)

    n_aoi, n_sea = count(in_aoi), count(sea & in_aoi)
    keep = n_aoi > 0
    if aoi_centre is not None:
        keep |= np.asarray(aoi_centre, bool).ravel()
    idx = np.nonzero(keep)[0]
    out = pd.DataFrame(index=pd.Index(idx, name="cell"))
    out["row"], out["col"] = idx // W, idx % W
    out["lon"] = model_transform.c + (out.col + 0.5) * model_transform.a
    out["lat"] = model_transform.f + (out.row + 0.5) * model_transform.e
    if aoi_centre is not None:
        out["aoi_centre"] = np.asarray(aoi_centre, bool).ravel()[idx]
    out["aoi_share"] = n_aoi[idx] / per_cell
    out["n_sea"] = n_sea[idx]
    out["sea_share"] = out.n_sea / per_cell

    m = sea & in_aoi & (cell_grid >= 0)
    rr, cc = np.nonzero(m)
    cell = cell_grid[rr, cc]
    df = pd.DataFrame({"cell": cell})
    for k, a in fine.items():
        df[k] = a[rr, cc]
    g = df.groupby("cell")
    agg = pd.DataFrame(index=out.index)
    if cell_area_km2 is not None:
        agg["sea_area_km2"] = pd.Series(cell_area_km2[rr, cc]).groupby(cell).sum()
    if "depth_m" in df:
        d = g.depth_m
        agg["depth_mean_m"], agg["depth_median_m"] = d.mean(), d.median()
        agg["depth_min_m"], agg["depth_max_m"], agg["depth_std_m"] = d.min(), d.max(), d.std()
        agg["share_shallower_50m"] = (df.depth_m < 50).groupby(df.cell).mean()
        agg["share_shallower_200m"] = (df.depth_m < 200).groupby(df.cell).mean()
        agg["share_shelf_break_150_250m"] = ((df.depth_m >= 150) & (df.depth_m < 250)).groupby(df.cell).mean()
    if "slope_m_per_km" in df:
        agg["slope_mean_m_per_km"] = g.slope_m_per_km.mean()
    for k in ("dist_coast_km", "dist_port_km"):
        if k in df:
            agg[k], agg[k.replace("_km", "_min_km")] = g[k].mean(), g[k].min()
    for k in [c for c in df.columns if c.startswith("ship_density_")]:  # presence only: many magnitudes cannot be counts
        agg["ship_presence_share_" + k[len("ship_density_"):]] = (df[k] > 0).where(df[k].notna()).groupby(df.cell).mean()
    for k, a in (shares or {}).items():
        v = np.asarray(a, float)[rr, cc]
        agg[k] = pd.Series(v).groupby(cell).mean()
    for name, (code, lookup) in (labels or {}).items():
        lab = pd.DataFrame({"cell": cell, "code": np.asarray(code)[rr, cc]})
        lab = lab[lab.code >= 0]
        n = lab.groupby(["cell", "code"]).size().rename("n").reset_index()
        top = n.sort_values(["cell", "n", "code"], ascending=[True, False, True]).drop_duplicates("cell").set_index("cell")
        for c in lookup.columns:
            agg[f"{name}_{c}"] = top.code.map(lookup[c])
        agg[f"{name}_share"] = top.n / pd.Series(n_sea, name="n_sea").reindex(top.index)
        agg[f"{name}_n"] = n.groupby("cell").size()
    out = out.join(agg)
    if "sea_area_km2" in out:
        out["sea_area_km2"] = out.sea_area_km2.fillna(0.0)
    for name in labels or {}:
        out[f"{name}_n"] = out[f"{name}_n"].fillna(0).astype(int)
    return out.reset_index(drop=True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
