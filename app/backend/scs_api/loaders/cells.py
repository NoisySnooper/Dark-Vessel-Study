"""Cell context (contract 3.7): static sea fields, nightly and per-pass ocean fields, AIS reach and look status from the
rasters, the GFW comparison in the research build, and the Marine Regions attributes only inside an `eez` object.
"""

from __future__ import annotations

import numpy as np

from ..config import EEZ_LABEL, EEZ_STATEMENT
from .common import GRID_RES, cell_centre

NIGHT_FIELDS = ["sst_mean_c", "sst_sd_c", "sst_grad_mean", "front_share", "dist_front_km", "chl_log10_mean",
                "chl_valid_share", "ssh_m", "ssh_anom_m", "ssh_grad", "current_speed_ms", "mld_m", "sbl_m", "wave_hs_m",
                "wind_ms", "moon_illum_pct", "sst_date", "chl_date", "rtofs_valid_utc", "wave_valid_utc", "wind_valid_utc",
                "sst_source", "chl_dataset", "chl_obs_dataset", "is_viirs_night", "is_s1_date"]
PASS_FIELDS = ["scene_id", "mission", "acq_utc", "utc_date", "wave_hs_m", "wave_valid_utc", "wind_ms", "wind_valid_utc",
               "sst_mean_c", "sst_sd_c", "sst_grad_mean", "front_share", "dist_front_km", "chl_log10_mean",
               "chl_valid_share", "ssh_m", "ssh_anom_m", "ssh_grad", "current_speed_ms", "mld_m", "sst_source",
               "chl_dataset", "rtofs_valid_utc"]
GFW_COLS = ["n_ours", "n_gfw", "n_gfw_matched", "n_gfw_unmatched", "n_pair"]
STATIC_PROV = {"depth": "gebco_2026", "share_sh": "gebco_2026", "slope": "gebco_2026", "dist_coast": "natural_earth",
               "dist_port": "wpi", "ship_presence": "worldbank_density", "aoi_": "natural_earth", "n_sea": "natural_earth",
               "sea_": "natural_earth"}


class CellsData:
    def __init__(self, static, daily, daily_index, passes, pass_index, gfw, summary):
        self.static = static
        self.daily = daily
        self.daily_index = daily_index
        self.passes = passes
        self.pass_index = pass_index
        self.gfw = gfw
        self.summary = summary or {}
        self.key = {} if static is None else {(int(r), int(c)): i for i, (r, c) in enumerate(zip(static["row"], static["col"]))}


def load(cat, settings) -> CellsData:
    static = cat.read_parquet("cells_static")
    daily = cat.read_parquet("cells_daily", ["night", "row", "col"] + NIGHT_FIELDS)
    passes = cat.read_parquet("cells_pass", ["row", "col"] + PASS_FIELDS)
    di = {} if daily is None else {(int(k[0]), int(k[1])): v for k, v in daily.groupby(["row", "col"]).indices.items()}
    pi = {} if passes is None else {(int(k[0]), int(k[1])): v for k, v in passes.groupby(["row", "col"]).indices.items()}
    gfw = None
    if settings.research:
        g = cat.read_parquet("radar_vs_gfw", ["lon", "lat"] + GFW_COLS)
        if g is not None and len(g):
            g["row"] = np.floor((24.0 - g["lat"]) / GRID_RES).astype(int)
            g["col"] = np.floor((g["lon"] - 99.0) / GRID_RES).astype(int)
            gfw = g.groupby(["row", "col"])[GFW_COLS].sum()
            gfw["cell_hours"] = g.groupby(["row", "col"]).size()
    summary = cat.read_json("ocean_static_summary")
    return CellsData(static, daily, di, passes, pi, gfw, summary)


def eez_block(row: dict) -> dict:
    return {"heading": EEZ_LABEL, "statement": EEZ_STATEMENT,
            **{k: row.get(k) for k in ("marineregions_mrgid", "marineregions_geoname", "marineregions_pol_type",
                                       "marineregions_share", "marineregions_n", "marineregions_overlap_share")}}


def static_prov(cols) -> dict:
    out = {}
    for c in cols:
        for p, s in STATIC_PROV.items():
            if c.startswith(p):
                out[c] = s
                break
    return out


def centre(row: int, col: int):
    return cell_centre(row, col)
