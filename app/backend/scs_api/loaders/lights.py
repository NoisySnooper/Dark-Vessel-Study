"""VIIRS lights and recurring light sites (contract 3.3), and the lights that lead evidence cites outside the lean file.

The product's light list and layer are the lean file `data/viirs_lights.gpkg` (10 of 27 nights). L7 leads cite the
brightest lights of a cell on all 27 nights; those outside the lean file are read by id from
`data/viirs_lights_all.gpkg` (local app only, contract 1.3.0) so every light of an L7 lead resolves to a Light record.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..records import iso_series, to_utc_series
from .common import MetricTree, cell_ids

LIGHT_CAVEAT = ("A light at sea is not a vessel identity. Lit vessels are mostly fishing; moonlit clouds, lightning, "
                "image artefacts, near-shore lights and gas flares are known false sources, and platforms show as "
                "recurring lights.")
EXTRA_NOTE = ("not in the lean light file data/viirs_lights.gpkg (10 of 27 nights); read from data/viirs_lights_all.gpkg "
              "because a lead cites it; not in the light list or layer")
INTERNAL = {"_t", "_source_caveat"}


class LightsData:
    def __init__(self, lights: pd.DataFrame, sites: pd.DataFrame, nights: pd.DataFrame | None, extra_cols: dict):
        self.lights = lights
        self.sites = sites
        self.nights = nights
        self.extra_cols = extra_cols
        self.pos = pd.Series(np.arange(len(lights)), index=lights["light_id"].astype(str)) if len(lights) else pd.Series(dtype=int)
        self.site_pos = pd.Series(np.arange(len(sites)), index=sites["site_id"].astype(str)) if len(sites) else pd.Series(dtype=int)


class EvidenceLights:
    """Lights cited by lead evidence that the lean file does not hold (same columns, prepared the same way)."""

    def __init__(self, lights: pd.DataFrame, extra_cols: list[str], cited: int):
        self.lights = lights
        self.extra_cols = extra_cols
        self.cited = cited  # light ids cited by leads outside the lean file (resolved or not)
        self.pos = pd.Series(np.arange(len(lights)), index=lights["light_id"].astype(str)) if len(lights) else pd.Series(dtype=int)


def prepare(lights: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Times, night, cell id and the nearest light site within 500 m (contract 3.3)."""
    if "caveat" in lights:
        lights["_source_caveat"] = lights.pop("caveat")
    if not len(lights):
        return lights
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
    return lights


def load(cat, settings, light_fields: list[str], site_fields: list[str]) -> LightsData:
    lights = cat.read_gpkg("lights")
    sites = cat.read_gpkg("sites")
    nights = cat.read_gpkg("viirs_nights")
    lights = lights if lights is not None else pd.DataFrame(columns=light_fields)
    sites = sites if sites is not None else pd.DataFrame(columns=site_fields)
    if "caveat" in sites:
        sites["_source_caveat"] = sites.pop("caveat")
    lights = prepare(lights, sites)
    extra = {"light": [c for c in lights.columns if c not in set(light_fields) and c not in INTERNAL],
             "site": [c for c in sites.columns if c not in set(site_fields) and c not in INTERNAL]}
    return LightsData(lights, sites, nights, extra)


def load_evidence(cat, settings, leads, lights: LightsData | None, light_fields: list[str]) -> EvidenceLights:
    """The lights that lead evidence cites and the lean file lacks, from viirs_lights_all.gpkg."""
    ids = set()
    for ev in (leads.evidence if leads is not None else []):
        for e in ev:
            if e.get("type") == "light" and e.get("id") is not None:
                ids.add(str(e["id"]))
    if lights is not None and len(lights.pos):
        ids -= set(lights.pos.index)
    empty = EvidenceLights(pd.DataFrame(columns=light_fields), [], len(ids))
    if not ids or not cat.exists("lights_all"):
        return empty
    df = cat.read_gpkg("lights_all")
    if df is None or not len(df):
        return empty
    df = df[df["light_id"].astype(str).isin(ids)].reset_index(drop=True)
    df = prepare(df, lights.sites if lights is not None else pd.DataFrame())
    extra = [c for c in df.columns if c not in set(light_fields) and c not in INTERNAL]
    return EvidenceLights(df, extra, len(ids))
