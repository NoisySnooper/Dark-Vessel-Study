"""Daily ocean layers over the South China Sea AOI: fetch once, cache per day, load on demand.

Sources (all anonymous HTTPS, no key; licence text recorded by scripts/23_daily_ocean.py):
  SST, 1 km      MUR v4.1 analysed_sst, NOAA CoastWatch ERDDAP dataset jplMURSST41 (daily 09:00 UTC, 0.01 degree,
                 axis values are cell centres at whole hundredths). The project's fine grid (grid.py) has centres at
                 half hundredths, so every fine cell is the exact mean of the 2 x 2 MUR cells it straddles. Fallback for
                 a day ERDDAP lacks: NOAA OISST v2.1 (0.25 degree) from the AWS bucket, sampled to the fine grid.
  Chlorophyll    NOAA CoastWatch VIIRS chlorophyll-a from the same ERDDAP: DINEOF gap-filled 2 km science-quality
                 (VIIRS S-NPP + NOAA-20 + Sentinel-3A OLCI) first, then the 9 km gap-filled science-quality and
                 near-real-time products for the latest days. The non-filled daily S-NPP product (science quality,
                 then near-real-time) gives the share of days with a direct retrieval.
  Currents, SSH, mixed layer
                 NOAA RTOFS global nowcast diagnostics, bucket noaa-nws-rtofs-pds: rtofs.YYYYMMDD/rtofs_glo_2ds_nHHH_diag.nc
                 holds the nowcast valid at HHH UTC of the previous day (checked against the file's MT/Date on
                 2026-10-08). Only the AOI rows and columns are read (h5py over HTTP ranges, gzip chunks of 825 x 1125).
  Waves          NOAA GFS-Wave global 0.25 degree, bucket noaa-gfs-bdp-pds: significant wave height (HTSGW) fetched by
                 byte range from the .idx, like the wind in darkvessel/weather.py, decoded with ecCodes (JPEG2000
                 packing, which this GDAL build cannot read).
  Wind           darkvessel.weather.gfs_wind (GFS 0.25 degree 10 m wind).

Caches live under data/cache/ocean/<source>/ as compressed .npz, .nc or .grib2; a rerun skips finished days.
Loaders return (array, rasterio transform) on a lon/lat grid, north up, NaN where there is no value.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

import h5py
import netCDF4
import numpy as np
import requests
from rasterio.transform import from_origin

from darkvessel.config import DATA_DIR
from darkvessel.ocean.grid import OCEAN_CACHE, aoi_geom, bin_mean, centres, fine_grid, sample

ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
OISST = "https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/data/v2.1/avhrr"
RTOFS = "https://noaa-nws-rtofs-pds.s3.amazonaws.com"
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
WEATHER_CACHE = DATA_DIR / "cache" / "weather"

MUR_ID = "jplMURSST41"
# Chlorophyll datasets in order of preference (id, label). Gap-filled first; the time axis decides which one has the day.
CHL_GAPFILLED = (("noaacwNPPN20S3ASCIDINEOF2kmDaily", "DINEOF gap-filled, 2 km, science quality, VIIRS S-NPP + NOAA-20 + S-3A OLCI"),
                 ("nesdisNPPN20S3ASCIDINEOFDaily", "DINEOF gap-filled, 9 km, science quality, VIIRS S-NPP + NOAA-20 + S-3A OLCI"),
                 ("nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily", "DINEOF gap-filled, 9 km, near-real-time, VIIRS S-NPP + NOAA-20"))
CHL_OBSERVED = (("nesdisVHNSQchlaDaily", "daily, 4 km, science quality, VIIRS S-NPP, not gap-filled"),
                ("nesdisVHNchlaDaily", "daily, 4 km, near-real-time, VIIRS S-NPP, not gap-filled"))
RTOFS_VARS = ("ssh", "u_barotropic_velocity", "v_barotropic_velocity", "mixed_layer_thickness", "surface_boundary_layer_thickness")
RTOFS_FILL = 1e20  # values above this are the 1.2676506e30 fill
RETRY_WAITS_S = (2, 4, 8)
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _get(url: str, retry_404: bool = True, **kw) -> requests.Response:
    """GET with retries on connection errors, 5xx and (through the proxy) spurious 404s.

    On 2026-10-08 one in about eight range requests to the NOAA buckets came back 404 through the session's proxy and
    succeeded on retry, so a 404 is retried a few times before it counts as missing.
    """
    for wait in (*RETRY_WAITS_S, None):
        try:
            r = _session().get(url, timeout=300, **kw)
            if r.status_code < 400 or wait is None or not (r.status_code >= 500 or (retry_404 and r.status_code == 404)):
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout):
            if wait is None:
                raise
        time.sleep(wait)
    raise RuntimeError("unreachable")


def night_time(night: str | dt.date, hour: int = 18) -> dt.datetime:
    """UTC time that stands for a local night (the VIIRS passes fall near 18 UTC, 01:00 in Vietnam)."""
    d = dt.date.fromisoformat(night) if isinstance(night, str) else night
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=dt.timezone.utc)


def _date(d) -> dt.date:
    return dt.date.fromisoformat(d) if isinstance(d, str) else d


# ----------------------------------------------------------------------------------------------------------------------
# ERDDAP grids

def erddap_times(dataset: str, start: str, cache: Path | None = None) -> list[str]:
    """Time steps of an ERDDAP griddap dataset from `start` (ISO date) to its last value, as 'YYYY-MM-DD' strings."""
    cache = cache or OCEAN_CACHE / "erddap" / f"{dataset}_times_{start}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
        return json.loads(cache.read_text())
    r = _get(f"{ERDDAP}/{dataset}.json?time%5B({start}T00:00:00Z):last%5D", retry_404=False)
    days = [row[0][:10] for row in r.json()["table"]["rows"]]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(days))
    return days


def erddap_subset(dataset: str, variables: list[str], day: str, lat: tuple[float, float], lon: tuple[float, float],
                  path: Path, hour: str = "12:00:00", extra_dim: str = "") -> Path:
    """Download one day of `variables` over a lat/lon box as NetCDF to `path` (skipped if it exists).

    `lat` is given in the dataset's own axis order (south, north) for MUR, (north, south) for the CoastWatch
    chlorophyll grids. `extra_dim` is the altitude index string for datasets with an altitude axis.
    """
    if path.exists():
        return path
    sel = f"%5B({day}T{hour}Z)%5D{extra_dim}%5B({lat[0]}):({lat[1]})%5D%5B({lon[0]}):({lon[1]})%5D"
    url = f"{ERDDAP}/{dataset}.nc?" + ",".join(f"{v}{sel}" for v in variables)
    r = _get(url, retry_404=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.rename(path)
    return path


def axis_transform(lat: np.ndarray, lon: np.ndarray):
    """(transform, flip) for 1-D axis values at cell centres: the north-up transform and whether rows need flipping."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    dlat = float(np.median(np.abs(np.diff(lat)))) if len(lat) > 1 else 0.0
    dlon = float(np.median(np.diff(lon)))
    flip = len(lat) > 1 and lat[1] > lat[0]
    north = (lat[-1] if flip else lat[0]) + dlat / 2
    return from_origin(lon[0] - dlon / 2, north, dlon, dlat), flip


def read_erddap_grid(path: Path, var: str):
    """(array north-up float32 with NaN, transform) of a 2-D (or 1 x 1 x H x W) ERDDAP NetCDF subset."""
    with netCDF4.Dataset(path) as ds:
        a = np.ma.filled(ds[var][:].astype(np.float32), np.nan).reshape(ds[var].shape[-2:])
        valid_min = getattr(ds[var], "valid_min", None)
        if valid_min is not None:
            a = np.where(a >= float(valid_min), a, np.nan)
        tr, flip = axis_transform(ds["latitude"][:], ds["longitude"][:])
    return (a[::-1] if flip else a), tr


def regrid(arr: np.ndarray, transform, dst_transform, dst_shape) -> np.ndarray:
    """Move a lon/lat raster onto another lon/lat grid: cell means where the source is finer, else the containing cell."""
    if abs(transform.a) < abs(dst_transform.a):
        lon, lat = centres(transform, arr.shape)
        return bin_mean(lon.ravel(), lat.ravel(), arr.ravel(), dst_transform, dst_shape)
    lon, lat = centres(dst_transform, dst_shape)
    return sample(arr, transform, lon, lat)


# ----------------------------------------------------------------------------------------------------------------------
# SST: MUR on the fine grid, OISST as fallback

def mur_to_fine(sst: np.ndarray, mask: np.ndarray):
    """Fine-grid (SST, sea, ice) from MUR arrays whose centres sit on the fine grid's cell corners.

    `sst` and `mask` are (H + 1, W + 1) north-up; cell (i, j) of the fine grid is the mean of MUR cells (i:i+2, j:j+2).
    The MUR mask is a bit field (ERDDAP flag_masks 1, 2, 4, 8, 16 = open_sea, land, open_lake, open_sea_with_ice_in_the_grid,
    open_lake_with_ice_in_the_grid). sea is True where all four MUR cells are open sea (bit 1 without the land bit 2 or
    the lake bit 4); ice where any has bit 8 or 16.
    """
    s = np.asarray(sst, np.float32)
    m = np.asarray(mask).astype(np.int16)
    q = lambda a: (a[:-1, :-1], a[1:, :-1], a[:-1, 1:], a[1:, 1:])  # noqa: E731
    stack = np.stack(q(s))
    n = np.isfinite(stack).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        fine = (np.nansum(stack, axis=0) / n).astype(np.float32)
    fine[n == 0] = np.nan
    sea = np.all([((x & 1) == 1) & ((x & 6) == 0) for x in q(m)], axis=0)
    ice = np.any([(x & 24) > 0 for x in q(m)], axis=0)
    fine[~sea] = np.nan
    return fine, sea, ice


def mur_path(day) -> Path:
    return OCEAN_CACHE / "mur" / f"mur_{_date(day):%Y%m%d}.npz"


def fetch_sst(day, mur_days: list[str] | None = None) -> Path:
    """Cache the fine-grid SST of `day`: MUR from ERDDAP, else OISST from AWS. Returns the .npz path."""
    day = _date(day)
    out = mur_path(day)
    if out.exists():
        return out
    tr, shape = fine_grid()
    west, north = tr.c, tr.f
    south, east = north + shape[0] * tr.e, west + shape[1] * tr.a
    out.parent.mkdir(parents=True, exist_ok=True)
    if mur_days is None or day.isoformat() in mur_days:
        try:
            nc = erddap_subset(MUR_ID, ["analysed_sst", "mask"], day.isoformat(), (round(south, 2), round(north, 2)),
                               (round(west, 2), round(east, 2)), out.with_suffix(".nc"), hour="09:00:00")
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
            nc = None
        if nc is not None:
            with netCDF4.Dataset(nc) as ds:
                lat, lon = ds["latitude"][:], ds["longitude"][:]
                if not (abs(lat[0] - south) < 1e-3 and abs(lon[0] - west) < 1e-3 and len(lat) == shape[0] + 1 and len(lon) == shape[1] + 1):
                    raise ValueError(f"MUR axis does not sit on the fine grid corners: lat {lat[0]}..{lat[-1]} n={len(lat)}, lon {lon[0]}.. n={len(lon)}")
                sst = np.ma.filled(ds["analysed_sst"][0].astype(np.float32), np.nan)[::-1]
                mask = np.ma.filled(ds["mask"][0], 2)[::-1]
            fine, sea, ice = mur_to_fine(sst, mask)
            np.savez_compressed(out, sst=fine, sea=sea, ice=ice, source="mur", dataset=MUR_ID,
                                valid_time=f"{day.isoformat()}T09:00:00Z")
            nc.unlink()
            return out
    # Fallback: OISST, sampled to the fine grid (0.25 degree cells, so fronts from it are coarse)
    sst25, tr25, name = fetch_oisst(day)
    lon, lat = centres(tr, shape)
    fine = sample(sst25, tr25, lon, lat).astype(np.float32)
    sea = np.isfinite(fine)
    np.savez_compressed(out, sst=fine, sea=sea, ice=np.zeros(shape, bool), source="oisst", dataset=name,
                        valid_time=f"{day.isoformat()}T12:00:00Z")
    return out


def fetch_oisst(day):
    """(sst north-up (720, 1440) float32 NaN over land, transform with lon 0..360, file name) of OISST v2.1 for `day`."""
    day = _date(day)
    cache = OCEAN_CACHE / "oisst"
    cache.mkdir(parents=True, exist_ok=True)
    have = list(cache.glob(f"oisst-avhrr-v02r01.{day:%Y%m%d}*.nc"))
    if have:
        path = have[0]
    else:
        path = None
        for suffix in ("", "_preliminary"):
            name = f"oisst-avhrr-v02r01.{day:%Y%m%d}{suffix}.nc"
            try:
                r = _get(f"{OISST}/{day:%Y%m}/{name}", retry_404=False)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                raise
            path = cache / name
            path.write_bytes(r.content)
            break
        if path is None:
            raise FileNotFoundError(f"no OISST file for {day}")
    with netCDF4.Dataset(path) as ds:
        sst = np.ma.filled(ds["sst"][0, 0].astype(np.float32), np.nan)[::-1]
        lat, lon = ds["lat"][:], ds["lon"][:]
    return sst, from_origin(float(lon[0]) - 0.125, float(lat[-1]) + 0.125, 0.25, 0.25), path.name


def load_sst(day):
    """(SST degrees C on the fine grid, transform): NaN where not open sea. Needs fetch_sst(day) first."""
    z = np.load(mur_path(day))
    return z["sst"], fine_grid()[0]


def load_sst_meta(day) -> dict:
    """Sea and ice masks and provenance of the cached SST day: keys sst, sea, ice, source, dataset, valid_time."""
    z = np.load(mur_path(day))
    return {k: (z[k] if z[k].ndim else str(z[k])) for k in z.files}


# ----------------------------------------------------------------------------------------------------------------------
# Chlorophyll-a

def _chl_box():
    w, s, e, n = aoi_geom().bounds
    return (round(n + 0.05, 2), round(s - 0.05, 2)), (round(w - 0.05, 2), round(e + 0.05, 2))


def chl_path(dataset: str, day) -> Path:
    return OCEAN_CACHE / "chl" / f"{dataset}_{_date(day):%Y%m%d}.nc"


def fetch_chl(day, candidates=CHL_GAPFILLED, start: str = "2026-09-01") -> tuple[Path, str] | tuple[None, None]:
    """Cache the first candidate dataset that has `day`. Returns (path, dataset id), or (None, None) if none has it."""
    day = _date(day)
    for dataset, _ in candidates:
        path = chl_path(dataset, day)
        if path.exists():
            return path, dataset
        if day.isoformat() not in erddap_times(dataset, start):
            continue
        lat, lon = _chl_box()
        return erddap_subset(dataset, ["chlor_a"], day.isoformat(), lat, lon, path, extra_dim="%5B(0.0)%5D"), dataset
    return None, None


def load_chl(day, candidates=CHL_GAPFILLED):
    """(log10 chlorophyll-a mg m-3 on the dataset's own grid, transform, dataset id) of the cached day, else None."""
    for dataset, _ in candidates:
        path = chl_path(dataset, day)
        if path.exists():
            chl, tr = read_erddap_grid(path, "chlor_a")
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(chl > 0, np.log10(chl), np.nan).astype(np.float32), tr, dataset
    return None


# ----------------------------------------------------------------------------------------------------------------------
# RTOFS nowcast: sea surface height, barotropic currents, mixed layer

def rtofs_url(t: dt.datetime) -> str:
    """Nowcast diagnostic file valid at the whole hour of t: folder of the next day, record nHHH."""
    t = t.astimezone(dt.timezone.utc)
    folder = (t + dt.timedelta(days=1)).date()
    return f"{RTOFS}/rtofs.{folder:%Y%m%d}/rtofs_glo_2ds_n{t.hour:03d}_diag.nc"


def rtofs_path(t: dt.datetime) -> Path:
    return OCEAN_CACHE / "rtofs" / f"rtofs_{t.astimezone(dt.timezone.utc):%Y%m%dT%H}.npz"


def _range_file(url: str, block: int = 1024 * 1024):
    from darkvessel.viirs.access import RangeFile

    for wait in (*RETRY_WAITS_S, None):
        try:
            return RangeFile(url, block=block)
        except requests.HTTPError as e:
            if wait is None or e.response is None or e.response.status_code != 404:
                raise
            time.sleep(wait)
    raise RuntimeError("unreachable")


def rtofs_window(url: str, pad_deg: float = 0.1) -> dict:
    """Rows and columns of the RTOFS grid that cover the AOI box, with the 2-D lon/lat of that window (cached once).

    South of about 47 N the RTOFS grid is a regular Mercator grid, so one longitude row and one latitude column locate
    the window; the window's own lon/lat arrays are kept so nothing depends on that regularity.
    """
    cache = OCEAN_CACHE / "rtofs" / "rtofs_grid.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: (int(z[k]) if z[k].ndim == 0 else z[k]) for k in z.files}
    w, s, e, n = aoi_geom().bounds
    with _range_file(url) as f, h5py.File(f, "r") as h:
        lon0 = h["Longitude"][0, :]
        cols = np.nonzero((lon0 >= w - pad_deg) & (lon0 <= e + pad_deg))[0]
        c0, c1 = int(cols.min()), int(cols.max()) + 1
        lat0 = h["Latitude"][:, (c0 + c1) // 2]
        rows = np.nonzero((lat0 >= s - pad_deg) & (lat0 <= n + pad_deg))[0]
        r0, r1 = int(rows.min()), int(rows.max()) + 1
        win = {"r0": r0, "r1": r1, "c0": c0, "c1": c1, "lon": h["Longitude"][r0:r1, c0:c1], "lat": h["Latitude"][r0:r1, c0:c1]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **win)
    return win


def fetch_rtofs(t: dt.datetime) -> Path:
    """Cache the AOI window of the RTOFS nowcast valid at t (whole hour): ssh, u, v, mld, sbl, with the valid time."""
    out = rtofs_path(t)
    if out.exists():
        return out
    url = rtofs_url(t)
    win = rtofs_window(url)
    sl = (0, slice(win["r0"], win["r1"]), slice(win["c0"], win["c1"]))
    for wait in (*RETRY_WAITS_S, None):  # a spurious 404 can also hit a block read inside the file
        try:
            with _range_file(url) as f, h5py.File(f, "r") as h:
                date = float(h["Date"][0])  # day as %Y%m%d.%f
                arrays = {v: h[v][sl] for v in RTOFS_VARS}
            break
        except requests.HTTPError as e:
            if wait is None or e.response is None or e.response.status_code != 404:
                raise
            time.sleep(wait)
    day, frac = divmod(date, 1)
    valid = dt.datetime.strptime(f"{int(day)}", "%Y%m%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=round(frac * 24))
    if abs((valid - t.astimezone(dt.timezone.utc)).total_seconds()) > 1800:
        raise ValueError(f"{url} is valid at {valid}, wanted {t}")
    arrays = {k: np.where(a < RTOFS_FILL, a, np.nan).astype(np.float32) for k, a in arrays.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, valid_time=valid.isoformat(), url=url, **arrays)
    return out


def load_rtofs(t: dt.datetime) -> dict:
    """Native-grid arrays of the cached nowcast: ssh (m), u, v (m/s, depth averaged), mld, sbl (m), speed (m/s),
    lon, lat (2-D), valid_time, url."""
    z = np.load(rtofs_path(t))
    win = rtofs_window(rtofs_url(t))
    d = {k: z[k] for k in RTOFS_VARS}
    d["speed"] = np.hypot(d["u_barotropic_velocity"], d["v_barotropic_velocity"]).astype(np.float32)
    d.update(lon=win["lon"], lat=win["lat"], valid_time=str(z["valid_time"]), url=str(z["url"]))
    return d


def metric_gradient(field: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Gradient magnitude per km of a field on a 2-D lon/lat grid (rows change latitude, columns longitude)."""
    km = 111.32
    dy = np.gradient(lat, axis=0) * km
    dx = np.gradient(lon, axis=1) * km * np.cos(np.radians(lat))
    gy, gx = np.gradient(field, axis=0) / dy, np.gradient(field, axis=1) / dx
    return np.hypot(gx, gy).astype(np.float32)


def rtofs_on_grid(t: dt.datetime, transform, shape) -> dict:
    """RTOFS fields binned onto a lon/lat grid: ssh_m, current_speed_ms, mld_m, sbl_m, ssh_grad (m per km)."""
    d = load_rtofs(t)
    lon, lat = d["lon"].ravel(), d["lat"].ravel()
    fields = {"ssh_m": d["ssh"], "current_speed_ms": d["speed"], "mld_m": d["mixed_layer_thickness"],
              "sbl_m": d["surface_boundary_layer_thickness"], "ssh_grad": metric_gradient(d["ssh"], d["lon"], d["lat"])}
    if abs(transform.a) <= 0.08:  # grid finer than RTOFS (1/12 degree): take the nearest native cell centre
        glon, glat = centres(transform, shape)
        from scipy.spatial import cKDTree

        ok = np.isfinite(lon) & np.isfinite(lat)
        _, idx = cKDTree(np.c_[lon[ok], lat[ok]]).query(np.c_[glon.ravel(), glat.ravel()])
        return {k: v.ravel()[ok][idx].reshape(shape) for k, v in fields.items()}
    return {k: bin_mean(lon, lat, v.ravel(), transform, shape) for k, v in fields.items()}


# ----------------------------------------------------------------------------------------------------------------------
# GFS-Wave significant wave height

def wave_key(t: dt.datetime) -> tuple[str, Path]:
    """(object key without extension, cache path) of the GFS-Wave global 0.25 degree file nearest to t."""
    from darkvessel.weather import _nearest_gfs

    cycle, fh = _nearest_gfs(t)
    key = f"gfs.{cycle:%Y%m%d}/{cycle:%H}/wave/gridded/gfswave.t{cycle:%H}z.global.0p25.f{fh:03d}.grib2"
    return key, OCEAN_CACHE / "gfswave" / f"gfswave_{cycle:%Y%m%d%H}_f{fh:03d}_htsgw.grib2"


def fetch_wave_hs(t: dt.datetime) -> Path:
    """Cache the HTSGW record (significant height of combined wind waves and swell) nearest to t, by byte range."""
    key, path = wave_key(t)
    if path.exists():
        return path
    idx = _get(f"{GFS}/{key}.idx").text.splitlines()
    starts = [int(line.split(":")[1]) for line in idx]
    hit = [i for i, line in enumerate(idx) if re.search(r":HTSGW:surface:", line)]
    if len(hit) != 1:
        raise RuntimeError(f"HTSGW record not found in {key}.idx")
    i = hit[0]
    end = starts[i + 1] - 1 if i + 1 < len(starts) else ""
    data = _get(f"{GFS}/{key}", headers={"Range": f"bytes={starts[i]}-{end}"}).content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def read_grib_field(path: Path):
    """(array north-up float32 with NaN, transform, valid time) of a single-record GRIB2 file on a regular lon/lat grid."""
    import eccodes

    with open(path, "rb") as f:
        gid = eccodes.codes_grib_new_from_file(f)
        try:
            ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
            lat1, lon1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"), eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
            di, dj = eccodes.codes_get(gid, "iDirectionIncrementInDegrees"), eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
            j_up = bool(eccodes.codes_get(gid, "jScansPositively"))
            missing = eccodes.codes_get(gid, "missingValue")
            vals = eccodes.codes_get_array(gid, "values").reshape(nj, ni).astype(np.float32)
            vdate, vtime = eccodes.codes_get(gid, "validityDate"), eccodes.codes_get(gid, "validityTime")
        finally:
            eccodes.codes_release(gid)
    vals[vals == missing] = np.nan
    if j_up:
        vals, lat1 = vals[::-1], lat1 + (nj - 1) * dj
    valid = dt.datetime.strptime(f"{vdate}{int(vtime):04d}", "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
    return vals, from_origin(lon1 - di / 2, lat1 + dj / 2, di, dj), valid


def load_wave_hs(t: dt.datetime):
    """(significant wave height m, global 0.25 degree, transform, valid time) nearest to t. Needs fetch_wave_hs(t) first.
    Sample with darkvessel.weather.sample_grid (longitudes wrap to the grid's 0..360 range)."""
    return read_grib_field(wave_key(t)[1])


def load_wind(t: dt.datetime):
    """(10 m wind speed m/s, transform) of the GFS analysis or short forecast nearest to t (darkvessel.weather)."""
    from darkvessel.weather import gfs_wind

    return gfs_wind(t, cache_dir=WEATHER_CACHE)
