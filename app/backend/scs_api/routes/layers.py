"""Bulk columnar layers in the bundle format of contract 6.2 (`/layers/{name}.cols`)."""

from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import APIRouter

from .. import columnar as C
from ..envelope import ApiError, raw_response
from ..loaders.common import mask_window, obj_cache
from .common import window

NAMES = ("contacts", "structures", "lights", "sites", "vessels")


def contacts_part(store, sel: np.ndarray, kind: str) -> dict:
    df = store.data["contacts"].df.iloc[sel]
    cols = {
        "det_id": C.detid(df["det_id"].astype(str).tolist()),
        "lon": C.num(df["lon"], 100000), "lat": C.num(df["lat"], 100000), "acq_utc": C.time_col(df["_t"]),
        "mission": C.dict_col(df["mission"]), "run_id": C.dict_col(df["run_id"]), "view": C.dict_col(df["view"]),
        "pass_id": C.dict_col(df["pass_id"]), "confidence": C.dict_col(df["confidence"]),
        "ais_status": C.dict_col(df["ais_status"]), "length_est_m": C.num(df["length_est_m"], 10),
        "cnn_score": C.num(df["cnn_score"], 250), "cnn_vessel": C.bool8(df["cnn_vessel"]),
        "vessel_key": C.dict_col(df["vessel_key"]), "mmsi": C.dict_col(df["mmsi"]),
        "research_only": C.bool8(df["research_only"]) if df["research_only"].nunique() > 1 else C.const(bool(df["research_only"].iloc[0]) if len(df) else False),
        "caveat": C.const(store.cav()),
    }
    return C.part(kind, cols, len(df))


def lights_part(store, sel: np.ndarray) -> dict:
    df = store.data["lights"].lights.iloc[sel]
    cols = {"satellite": C.dict_col(df["satellite"]), "time_utc": C.time_col(df["_t"]),
            "light_id": C.lightid(df["light_id"].astype(str).tolist(), df["satellite"].tolist(), df["_t"]),
            "lon": C.num(df["lon"], 100000), "lat": C.num(df["lat"], 100000), "radiance_nw": C.num(df["radiance_nw"], kind="f32"),
            "quality": C.dict_col(df["quality"]), "class": C.dict_col(df["class"]),
            "nights_seen_500m": C.num(df["nights_seen_500m"]), "s1_passes_90d": C.num(df["s1_passes_90d"]),
            "site_id": C.dict_col(df["site_id"]), "night": C.dict_col(df["night"]), "caveat": C.const(store.cav())}
    return C.part("lights", cols, len(df))


def router(store) -> APIRouter:
    r = APIRouter(tags=["layers"])

    @r.get("/layers/{name}.cols", responses={200: {"description": "columnar part (contract 6.2) with the envelope fields"}})
    def layer(name: str, t0: Optional[str] = None, t1: Optional[str] = None, bbox: Optional[str] = None):
        if name not in NAMES:
            raise ApiError(404, "not_found", f"layer must be one of {', '.join(NAMES)}")
        src = {"contacts": "contacts", "structures": "contacts", "lights": "lights", "sites": "lights", "vessels": "vessels"}[name]
        cache = obj_cache(store.data[src], "layers")  # kept on the loaded data: a reload of it starts a new cache
        key = (name, t0, t1, bbox)
        if key not in cache:
            if len(cache) > 32:
                cache.clear()
            cache[key] = build(name, t0, t1, bbox)
        return cache[key]

    def build(name, t0, t1, bbox):
        w = window(t0, t1, bbox)
        if name in ("contacts", "structures"):
            df = store.data["contacts"].df
            if not len(df):
                return raw_response(store.settings, C.part(name, {}, 0))
            conf = df["confidence"].astype(str)
            m = mask_window(df, w["t0"], w["t1"], w["bbox"]) & (conf.isin(["high", "medium"]) if name == "contacts" else conf.eq("fixed")).to_numpy()
            return raw_response(store.settings, contacts_part(store, np.flatnonzero(m), name))
        if name == "lights":
            df = store.data["lights"].lights
            m = mask_window(df, w["t0"], w["t1"], w["bbox"]) if len(df) else np.zeros(0, bool)
            return raw_response(store.settings, lights_part(store, np.flatnonzero(m)))
        if name == "sites":
            df = store.data["lights"].sites
            m = mask_window(df, None, None, w["bbox"]) if len(df) else np.zeros(0, bool)
            d = df[m]
            cols = {"site_id": C.strs(d["site_id"].astype(str)), "lon": C.num(d["lon"], 100000), "lat": C.num(d["lat"], 100000),
                    "n_lights": C.num(d["n_lights"]), "likely": C.dict_col(d["likely"]), "caveat": C.const(store.cav())}
            return raw_response(store.settings, C.part("sites", cols, len(d)))
        df = store.data["vessels"].df
        m = np.ones(len(df), bool)
        if len(df) and w["bbox"] is not None:
            m &= mask_window(df, None, None, w["bbox"], lon="last_lon", lat="last_lat")
        if len(df) and (w["t0"] is not None or w["t1"] is not None) and "_t_last_seen_utc" in df:
            m &= mask_window(df, w["t0"], w["t1"], None, tcol="_t_last_seen_utc")
        d = df[m]
        cols = {"vessel_key": C.strs(d["vessel_key"]), "mmsi": C.strs(d["mmsi"]), "name": C.strs(d.get("name", [None] * len(d))),
                "last_lon": C.num(d.get("last_lon", [None] * len(d)), 100000), "last_lat": C.num(d.get("last_lat", [None] * len(d)), 100000),
                "last_seen_utc": C.time_col(d.get("_t_last_seen_utc", [None] * len(d))),
                "ais_class": C.dict_col(d.get("ais_class", [None] * len(d))), "ship_type": C.dict_col(d.get("ship_type", [None] * len(d))),
                "flag": C.dict_col(d.get("flag", [None] * len(d))), "stub": C.bool8(d["stub"]), "caveat": C.const(store.cav())}
        return raw_response(store.settings, C.part("vessels", cols, len(d)))

    return r
