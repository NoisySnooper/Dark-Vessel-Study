"""AI2 Skylight Sentinel-1 vessel labels (metadata.sqlite3) decoded to lon/lat.

Source: allenai/vessel-detection-sentinels, data/metadata.sqlite3 (Apache-2.0). Tables used:
  images   one Sentinel-1 GRD product each (name = product id + '.SAFE'), extent in pixel
           coordinates on a Web Mercator tiling (tile 512 px, zoom 13)
  windows  dataset 1 ('vessels', point task): labelled crops, mostly 1024 x 1024 px, split names
           like 'jun-2020-point-train' / '...-val'
           dataset 2 ('vessel_attrs'): 128 x 128 px crops centred on one vessel, one label each
  labels   dataset 1: point (column, row); dataset 2: properties JSON with Length, Width, ...

Pixel coordinate system (README section 'images', notes): shift Web Mercator metres so the
minimum is 0, flip y, and divide the world into 2^13 tiles of 512 px per axis. So

    x_m = col * S - H,   y_m = H - row * S,   S = 2 H / (2^13 * 512),   H = 20037508.342789244

and lon = x_m / H * 180, lat = atan(sinh(y_m / R)) with R = 6378137 m. Checked in this
project against the stored image bounds of all 4,315 S1 images: residuals < 1e-4 degree.

Attribute join rule: a dataset-2 window's centre (column + width / 2, row + height / 2) lands
exactly (0 px) on one dataset-1 label of the same image for all 17,582 attribute windows
(checked here), so attributes are joined on (image_id, column, row) of that centre.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

AI2_DB = Path("/home/user/allenai/vessel-detection-sentinels/data/metadata.sqlite3")
AI2_LICENSE = "Apache-2.0 (allenai/vessel-detection-sentinels)"

HALF_WORLD_M = 20037508.342789244
EARTH_R = 6378137.0
ZOOM = 13
TILE = 512
WORLD_PX = 2**ZOOM * TILE
M_PER_PX = 2 * HALF_WORLD_M / WORLD_PX  # 9.5546 m at the equator

# Study region for the flagship transfer letter: Southeast Asia (west, south, east, north).
SEA_BBOX = (95.0, -10.0, 125.0, 25.0)


def webmerc_pixel_to_lonlat(col, row):
    """Web Mercator tiling pixel (zoom 13, tile 512) -> (lon, lat) in degrees. Vectorised."""
    col = np.asarray(col, dtype=np.float64)
    row = np.asarray(row, dtype=np.float64)
    x = col * M_PER_PX - HALF_WORLD_M
    y = HALF_WORLD_M - row * M_PER_PX
    lon = x / HALF_WORLD_M * 180.0
    lat = np.degrees(np.arctan(np.sinh(y / EARTH_R)))
    return lon, lat


def lonlat_to_webmerc_pixel(lon, lat):
    """Inverse of webmerc_pixel_to_lonlat (fractional pixels)."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    x = lon / 180.0 * HALF_WORLD_M
    y = EARTH_R * np.arcsinh(np.tan(np.radians(lat)))
    return (x + HALF_WORLD_M) / M_PER_PX, (HALF_WORLD_M - y) / M_PER_PX


def ground_m_per_px(lat):
    """Ground size of one tiling pixel at latitude `lat` (Mercator scale factor)."""
    return M_PER_PX * np.cos(np.radians(np.asarray(lat, dtype=np.float64)))


def product_path(product_id: str) -> str:
    """AWS bucket key for a product id (month and day without zero padding)."""
    pid = product_id.replace(".SAFE", "")
    date = pid.split("_")[4]
    y, m, d = int(date[0:4]), int(date[4:6]), int(date[6:8])
    mode = pid.split("_")[1]
    pol = pid.split("_")[3][-2:]  # 1SDV -> DV, 1SSV -> SV
    return f"GRD/{y}/{m}/{d}/{mode}/{pol}/{pid}"


def in_bbox(west_lon, south_lat, east_lon, north_lat, bbox=SEA_BBOX) -> np.ndarray:
    """True where a (lon/lat) box intersects `bbox`."""
    w, s, e, n = bbox
    return (np.asarray(east_lon) >= w) & (np.asarray(west_lon) <= e) & (np.asarray(north_lat) >= s) & (np.asarray(south_lat) <= n)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)


def load_images(db_path: str | Path = AI2_DB) -> pd.DataFrame:
    """Sentinel-1 images with product id, acquisition time and lon/lat bounds."""
    with _connect(db_path) as con:
        df = pd.read_sql_query(
            "select id as image_id, name, time, bounds, column as col0, row as row0, width, height, zoom "
            "from images where name like 'S1%'", con)
    assert (df.zoom == ZOOM).all(), "unexpected zoom level in images table"
    df["product_id"] = df.name.str.replace(".SAFE", "", regex=False)
    df["mission"] = df.product_id.str[:3]
    # the time column mixes '2021-11-04T17:07:49+00:00' and '2022-05-15 19:41:52+00:00'
    df["scene_time_utc"] = pd.to_datetime(df.time, utc=True, format="ISO8601")
    b = df.bounds.map(json.loads)
    df["west"] = b.map(lambda x: x["Min"]["Lon"])
    df["east"] = b.map(lambda x: x["Max"]["Lon"])
    df["south"] = b.map(lambda x: x["Min"]["Lat"])
    df["north"] = b.map(lambda x: x["Max"]["Lat"])
    df["region"] = np.where(in_bbox(df.west, df.south, df.east, df.north), "sea_asia", "other")
    df["aws_path"] = df.product_id.map(product_path)
    return df.drop(columns=["bounds", "time", "zoom"])


def load_windows(db_path: str | Path = AI2_DB, dataset_id: int = 1) -> pd.DataFrame:
    with _connect(db_path) as con:
        df = pd.read_sql_query(
            "select w.id as window_id, w.image_id, w.column as col0, w.row as row0, w.width, w.height, w.split, "
            "w.hidden, count(l.id) as n_labels from windows w left join labels l on l.window_id = w.id "
            f"where w.dataset_id = {int(dataset_id)} group by w.id", con)
    df["split_group"] = np.select([df.split.str.endswith("-val"), df.split.str.endswith("-train")],
                                  ["val", "train"], default="other")
    df["west"], df["north"] = webmerc_pixel_to_lonlat(df.col0, df.row0)
    df["east"], df["south"] = webmerc_pixel_to_lonlat(df.col0 + df.width, df.row0 + df.height)
    return df


def load_point_labels(db_path: str | Path = AI2_DB) -> pd.DataFrame:
    """Dataset-1 point labels (one row per labelled vessel)."""
    with _connect(db_path) as con:
        df = pd.read_sql_query(
            "select l.id as label_id, l.window_id, w.image_id, l.column as col, l.row as row, l.properties "
            "from labels l join windows w on w.id = l.window_id where w.dataset_id = 1", con)
    # AI2's training code drops helper labels whose properties contain 'OnKey'; none exist in dataset 1
    # (all properties are '{}' or NULL), but keep the rule for safety.
    helper = df.properties.fillna("").str.contains("OnKey")
    df = df[~helper].drop(columns=["properties"]).reset_index(drop=True)
    df["lon"], df["lat"] = webmerc_pixel_to_lonlat(df.col, df.row)
    return df


def load_attributes(db_path: str | Path = AI2_DB) -> pd.DataFrame:
    """Dataset-2 vessel attributes keyed by (image_id, col, row) of the window centre."""
    with _connect(db_path) as con:
        df = pd.read_sql_query(
            "select w.image_id, w.column + w.width / 2 as col, w.row + w.height / 2 as row, w.split as attr_split, "
            "l.properties from windows w join labels l on l.window_id = w.id where w.dataset_id = 2", con)
    props = df.properties.map(json.loads)
    out = df[["image_id", "col", "row", "attr_split"]].copy()
    out["col"] = out.col.astype(int)
    out["row"] = out.row.astype(int)
    for key, name in (("Length", "length_m"), ("Width", "width_m"), ("Heading", "heading_deg"),
                      ("Speed", "speed_kn"), ("ShipAndCargoType", "ship_type")):
        out[name] = props.map(lambda p: p.get(key)).astype(float)
    # 17,582 attribute windows sit on 16,494 distinct positions. Where duplicates disagree on
    # Length (843 positions, checked here) the AIS match is ambiguous: keep the row but blank
    # the attributes and flag it, so the recall-by-length table only uses consistent lengths.
    key = ["image_id", "col", "row"]
    n_len = out.groupby(key).length_m.transform("nunique")
    out["attr_conflict"] = n_len > 1
    out = out.drop_duplicates(key).reset_index(drop=True)
    for name in ("length_m", "width_m", "heading_deg", "speed_kn", "ship_type"):
        out.loc[out.attr_conflict, name] = np.nan
    return out


def build_label_table(db_path: str | Path = AI2_DB) -> pd.DataFrame:
    """Dataset-1 labels with product id, time, split, region and joined attribute lengths."""
    images = load_images(db_path)
    windows = load_windows(db_path, 1)
    labels = load_point_labels(db_path)
    attrs = load_attributes(db_path)
    df = labels.merge(windows[["window_id", "split", "split_group", "width", "height"]], on="window_id", how="left")
    df = df.rename(columns={"width": "window_width_px", "height": "window_height_px"})
    df = df.merge(images[["image_id", "product_id", "mission", "scene_time_utc", "region", "aws_path"]],
                  on="image_id", how="left")
    df = df.merge(attrs, on=["image_id", "col", "row"], how="left")
    df["attr_conflict"] = df.attr_conflict.fillna(False).astype(bool)
    df["has_attrs"] = df.length_m.notna()
    df["label_source"] = "AI2 Skylight S1 point labels, dataset 1"
    df["license"] = AI2_LICENSE
    cols = ["label_id", "window_id", "image_id", "product_id", "mission", "scene_time_utc", "split", "split_group",
            "region", "aws_path", "col", "row", "lon", "lat", "window_width_px", "window_height_px", "has_attrs",
            "attr_conflict", "length_m", "width_m", "heading_deg", "speed_kn", "ship_type", "attr_split",
            "label_source", "license"]
    return df[cols].sort_values("label_id").reset_index(drop=True)


def build_window_table(db_path: str | Path = AI2_DB) -> pd.DataFrame:
    """Dataset-1 windows with lon/lat bounds, product id and region."""
    images = load_images(db_path)
    windows = load_windows(db_path, 1)
    df = windows.merge(images[["image_id", "product_id", "mission", "scene_time_utc", "region", "aws_path"]],
                       on="image_id", how="left")
    df["ground_m_per_px"] = ground_m_per_px((df.north + df.south) / 2)
    return df


def length_bin(length_m, bins=(0, 15, 25, 50, 100, np.inf)) -> pd.Categorical:
    """AIS length bins used in the flagship recall-by-length analysis."""
    labels = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        labels.append(f"{lo:g}-{hi:g} m" if np.isfinite(hi) else f"{lo:g}+ m")
    return pd.cut(pd.Series(length_m, dtype=float), bins, labels=labels, right=False)
