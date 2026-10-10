"""VIIRS lights and recurring light sites (contract 3.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..records import iso_series, to_utc_series
from .common import MetricTree, cell_ids

LIGHT_CAVEAT = ("A light at sea is not a vessel identity. Lit vessels are mostly fishing; moonlit clouds, lightning, "
                "image artefacts, near-shore lights and gas flares are known false sources, and platforms show as "
                "recurring lights.")


class LightsData:
    def __init__(self, lights: pd.DataFrame, sites: pd.DataFrame, nights: pd.DataFrame | None, extra_cols: dict):
        self.lights = lights
        self.sites = sites
        self.nights = nights
        self.extra_cols = extra_cols
        self.pos = pd.Series(np.arange(len(lights)), index=lights["light_id"].astype(str)) if len(lights) else pd.Series(dtype=int)
        self.site_pos = pd.Series(np.arange(len(sites)), index=sites["site_id"].astype(str)) if len(sites) else pd.Series(dtype=int)


def load(cat, settings, light_fields: list[str], site_fields: list[str]) -> LightsData:
    lights = cat.read_gpkg("lights")
    sites = cat.read_gpkg("sites")
    nights = cat.read_gpkg("viirs_nights")
    lights = lights if lights is not None else pd.DataFrame(columns=light_fields)
    sites = sites if sites is not None else pd.DataFrame(columns=site_fields)
    for df in (lights, sites):
        if "caveat" in df:
            df["_source_caveat"] = df.pop("caveat")
    if len(lights):
        lights["_t"] = to_utc_series(lights["time_utc"])
        lights["time_utc"] = iso_series(lights["time_utc"])
        lights["night"] = lights["night"].astype(str)
        lights["cell_id"] = cell_ids(lights["lon"].to_numpy(), lights["lat"].to_numpy())
        lights["site_id"] = None
        if len(sites):
            tree = MetricTree(sites["lon"].to_numpy(), sites["lat"].to_numpy())
            pos, _ = tree.nearest(lights["lon"].to_numpy(), lights["lat"].to_numpy(), 500.0)
            sid = sites["site_id"].astype(str).to_numpy()
            lights["site_id"] = np.where(pos >= 0, sid[np.maximum(pos, 0)], None)
    internal = {"_t", "_source_caveat"}
    extra = {"light": [c for c in lights.columns if c not in set(light_fields) and c not in internal],
             "site": [c for c in sites.columns if c not in set(site_fields) and c not in internal]}
    return LightsData(lights, sites, nights, extra)
