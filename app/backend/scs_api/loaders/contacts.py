"""Contacts (contract 3.1): live passes, the September run (open: not_checked; research: GFW identity), fixed
structures and the Ca Mau scene, mapped onto D1 plus the extension fields, with the CNN, weather and optical joins.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from ..config import AISSTREAM_LABEL, PRODUCT_CAVEAT
from ..models import AIS_STATUS_VALUES, contact_spec
from ..records import iso_series, to_utc_series
from .common import cell_ids

CNN_COLS = ["cnn_score", "cnn_vessel", "cnn_threshold", "cnn_model_id", "cnn_score_source", "cnn_chip_valid_frac",
            "bg_vv_db", "bg_vh_db"]
WEATHER_COLS = ["wind_ms", "ctt_k", "deep_convection", "himawari_start"]
OPTICAL_COLS = ["optical_object", "optical_kind", "s2_item", "satlas_m", "s2_datetime"]
DIGIT_COLS = ["mmsi", "imo", "nearest_ais_mmsi"]
INTERNAL = {"_source", "_t", "_source_caveat", "_source_ais_status", "_night"}
REGIONAL_DETECTOR = ["det_id", "scene_idx", "inc_angle_deg", "scr_vv_db", "scr_vh_db", "persist_dates", "persist_dates_checked"]

_LIVE_AIS = ["ais_status", "ais_source", "match_method", "match_dist_m", "match_dt_s", "match_quality", "mmsi", "imo",
             "vessel_name", "call_sign", "ship_type", "length_ais_m", "identity_source", "nearest_ais_mmsi",
             "nearest_ais_dist_m", "nearest_ais_dt_s", "n_ais_10km", "ais_reach", "dark_lead", "match_gate_m", "ais_sog_kn",
             "length_ratio", "ais_class", "ais_footprint_positions", "ais_recorded_hours", "pred_method"]
_GFW_IDENTITY = ["mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source",
                 "gfw_vessel_id", "identity_kind", "nearest_ais_vessel_id", "nearest_ais_name", "gfw_geartype"]
_GFW_MATCH = ["ais_status", "ais_source", "match_method", "match_dist_m", "match_dt_s", "match_quality", "nearest_ais_mmsi",
              "nearest_ais_dist_m", "nearest_ais_dt_s", "n_ais_10km", "ais_reach", "gfw_sar_pair", "gfw_sar_n_cand",
              "gfw_sar_n_rivals", "gfw_sar_ambiguous_cell", "gfw_neural_type", "pres_speed_kmh", "pres_n_cells",
              "pres_n_cand", "n_gear_10km", "ais_presence_h_day", "ais_presence_h_window"]
_GFW_EVENTS = ["n_gfw_gaps_50km_24h", "nearest_gfw_gap_km", "n_gfw_encounters_10km_24h", "n_gfw_loitering_10km_24h"]
# contract 1.3.0: live identification evidence. The pairing fields come from the AIS track at the contact's own azimuth
# time; az_time_utc and az_shift_m from the scene annotation and the vessel's velocity; review_note from the hand check.
LIVE_ID_FIELDS = ["az_time_utc", "match_dist_uncorr_m", "az_shift_m", "velocity_source", "match_ambiguous", "ambiguous_mmsi",
                  "match_alt_dist_m", "review_note"]
_LIVE_ID_PROV = {"az_time_utc": "det_live", "az_shift_m": "det_live", "match_dist_uncorr_m": "aisstream",
                 "velocity_source": "aisstream", "match_ambiguous": "aisstream", "ambiguous_mmsi": "aisstream",
                 "match_alt_dist_m": "aisstream", "review_note": "analyst", "review_grade": "analyst",
                 "identity_label": "aisstream"}
LIVE_WEATHER_COLS = ["wind_ms", "ctt_k", "deep_convection", "gfs_cycle_utc", "gfs_forecast_h", "himawari_key", "wind_source",
                     "cloud_source"]
_COMMON_PROV = {**{c: "cnn_v0" for c in CNN_COLS}, "wind_ms": "gfs_wind", "ctt_k": "himawari_ctt",
                "deep_convection": "himawari_ctt", "optical_object": "s2_optical", "optical_kind": "s2_optical",
                "s2_item": "s2_optical", "satlas_m": "satlas", "cell_id": "app", "view": "app", "lead_ids": "app",
                "vessel_key": "app", "nearest_vessel_key": "app", "chip": "s1_grd", "object_context": "ocean_context"}
PROV = {
    "live": {**_COMMON_PROV, **{c: "aisstream" for c in _LIVE_AIS}, **_LIVE_ID_PROV, "flag": "mid_itu", "mmsi_mid": "mid_itu"},
    "regional": {**_COMMON_PROV, "pass_id": "app", "ais_status": "app"},
    "research": {**_COMMON_PROV, **{c: "gfw_vessels" for c in _GFW_IDENTITY}, **{c: "gfw_4wings" for c in _GFW_MATCH},
                 **{c: "gfw_events" for c in _GFW_EVENTS}},
    "structures": {**_COMMON_PROV, "pass_id": "app", "ais_status": "app"},
    "camau": {**_COMMON_PROV, "ais_status": "app", "mission": "app", "run_id": "app"},
}
SRC = {"live": "det_live", "regional": "det_regional", "research": "det_regional", "structures": "det_regional",
       "camau": "det_camau"}
VIEW = {"live": "live", "regional": "regional", "research": "regional", "structures": "regional", "camau": "camau"}


def digit_series(s: pd.Series) -> pd.Series:
    """MMSI and IMO columns (int64, float with NaN, or text) as digit strings or None."""
    if pd.api.types.is_numeric_dtype(s):
        f = pd.to_numeric(s, errors="coerce")
        out = f.round().astype("Int64").astype("string")
        return out.astype(object).where(f.notna(), None)
    t = s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    return t.astype(object).where(t.notna() & (t != "") & t.str.fullmatch(r"\d+").fillna(False), None)


def bool_series(s: pd.Series) -> pd.Series:
    """Bool, Int16 (1, 0, null) or text booleans as object True/False/None."""
    if s.dtype == bool:
        return s.astype(object)
    if pd.api.types.is_numeric_dtype(s) or str(s.dtype) == "boolean":
        f = pd.to_numeric(s.astype("Float64"), errors="coerce")
        out = (f != 0).astype(object)
        return out.where(f.notna(), None)

    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            return None
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)
    return s.astype(object).map(one)


def group_passes(scenes: pd.DataFrame) -> pd.DataFrame:
    """Scenes grouped into passes: same mission, starts within 10 min of the previous scene; pass_id = mission + start."""
    sc = scenes.copy()
    sc["_start"] = to_utc_series(sc["start_utc"])
    sc = sc.sort_values(["mission", "_start"])
    ids, prev_m, prev_t, cur = [], None, None, None
    for m, t in zip(sc["mission"], sc["_start"]):
        if m != prev_m or prev_t is None or (t - prev_t) > pd.Timedelta(minutes=10):
            cur = f"{m}_{t:%Y%m%dT%H%M}"
        ids.append(cur)
        prev_m, prev_t = m, t
    sc["pass_id"] = ids
    return sc


class ContactsData:
    def __init__(self, df: pd.DataFrame, extra_cols: dict, scenes: pd.DataFrame | None, about: dict, fields: list[str]):
        self.df = df
        self.extra_cols = extra_cols
        self.scenes = scenes
        self.about = about
        self.fields = fields
        self.pos = pd.Series(np.arange(len(df)), index=df["det_id"].astype(str)) if len(df) else pd.Series(dtype=int)
        self.pos = self.pos[~self.pos.index.duplicated()]


def _index(other: pd.DataFrame | None) -> pd.DataFrame | None:
    """A join table keyed by det_id (first row per id, index named `det_id`, no det_id column). Built once per load and
    reused for every source frame: the weather and CNN tables have 10^5 to 10^6 rows."""
    if other is None:
        return None
    o = other.drop_duplicates("det_id")
    return o.set_index(pd.Index(o["det_id"].astype(str).to_numpy(), name="det_id")).drop(columns="det_id")


def _join(df: pd.DataFrame, other: pd.DataFrame | None, cols: list[str], fill: bool = True) -> pd.DataFrame:
    """Add `cols` to `df` from `other` by det_id, or fill their nulls when `fill`. `other` is a frame with a det_id column
    or an `_index` result."""
    if other is None or not len(df):
        return df
    o = _index(other) if "det_id" in other.columns else other
    have = [c for c in cols if c in o.columns]
    j = o[have].reindex(df["det_id"].astype(str).to_numpy())
    for c in have:
        vals = j[c].to_numpy()
        if c in df.columns and fill:
            cur = df[c]
            df[c] = cur.where(cur.notna(), pd.Series(vals, index=df.index))
        elif c not in df.columns:
            df[c] = vals
    return df


def read_cnn(cat, id_cols: list[pd.Series]) -> pd.DataFrame | None:
    """The regional CNN scores of the given det_ids only, keyed by det_id (`_index`). regional_cnn.parquet also scores
    the 823,285 low objects of the September run, which no product contact holds; filtering in Arrow before the pandas
    conversion keeps the start fast."""
    if not cat.exists("regional_cnn"):
        return None
    tb = cat.read_arrow("regional_cnn", ["det_id", "scene_id"] + CNN_COLS)
    if tb is None:
        return None
    ids = pd.unique(pd.concat([s.astype(str) for s in id_cols], ignore_index=True)) if id_cols else []
    tb = tb.filter(pc.is_in(tb["det_id"], value_set=pa.array(list(ids), type=tb.schema.field("det_id").type)))
    return _index(tb.to_pandas())


def _finish(df: pd.DataFrame, source: str, fields: set[str]) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    if "caveat" in df:
        df["_source_caveat"] = df.pop("caveat")
    st = df["ais_status"].astype("string")
    bad = ~st.isin(AIS_STATUS_VALUES)
    if bad.any():
        df["_source_ais_status"] = st.astype(object).where(bad, None)
        df.loc[bad, "ais_status"] = "not_checked"
    for c in DIGIT_COLS:
        if c in df:
            df[c] = digit_series(df[c])
    for c in ("cnn_vessel", "dark_lead", "deep_convection", "optical_object", "gfw_sar_ambiguous_cell"):
        if c in df:
            df[c] = bool_series(df[c])
    df["research_only"] = bool_series(df["research_only"]).fillna(False).astype(bool) if "research_only" in df else False
    df["_t"] = to_utc_series(df["acq_utc"])
    df["acq_utc"] = iso_series(df["acq_utc"])
    df["view"] = VIEW[source]
    df["cell_id"] = cell_ids(df["lon"].to_numpy(), df["lat"].to_numpy())
    df["_source"] = source
    extra = [c for c in df.columns if c not in fields and c not in INTERNAL and not str(c).startswith("_")]
    return df, extra


REVIEW_GRADES = ("confirmed", "plausible", "doubtful")


def review_grade(note: pd.Series) -> pd.Series:
    """The grade of a hand-check note '<grade>: <reason>' (live file about.review_note), or None."""
    g = note.astype("string").str.extract(r"^\s*([a-z]+)\s*:", expand=False).str.lower()
    return g.astype(object).where(g.isin(REVIEW_GRADES).fillna(False), None)


def _gfs_valid(cycle: pd.Series, step: pd.Series) -> pd.Series:
    """GFS valid time (cycle plus forecast hour) as ISO 8601 UTC with Z."""
    t = to_utc_series(cycle.astype("string").str.replace("Z", "+00:00", regex=False))
    h = pd.to_numeric(step, errors="coerce")
    return iso_series(t + pd.to_timedelta(h, unit="h"))


def _himawari_start(key: pd.Series) -> pd.Series:
    """Scan start of a Himawari AHI L2 file from its name (`_s<yyyymmddhhmmss><tenth>_`), ISO 8601 UTC with Z."""
    m = key.astype("string").str.extract(r"_s(\d{14})\d?_", expand=False)
    return iso_series(pd.to_datetime(m, format="%Y%m%d%H%M%S", utc=True, errors="coerce"))


def himawari_start_text(s: pd.Series) -> pd.Series:
    """`himawari_start` of data/weather_context.parquet as ISO 8601 UTC with Z (None where it does not parse).
    scripts/16_weather_context.py writes the first 13 digits of the file name's `_s<yyyymmddhhmmss><tenth>` field:
    yyyymmddHHMM and the tens digit of the seconds, so the scan start is known to 10 s (`2026092010402` is
    10:40:20Z). 12 and 14 digit strings are read the same way; any other text is read as ISO 8601. One value per
    scene, so only the distinct values are parsed."""
    codes, uniq = pd.factorize(s, use_na_sentinel=True)
    if len(uniq) < len(s):
        parsed = himawari_start_text(pd.Series(uniq, dtype=object)).to_numpy() if len(uniq) else np.array([], dtype=object)
        out = np.full(len(s), None, dtype=object)
        ok = codes >= 0
        out[ok] = parsed[codes[ok]]
        return pd.Series(out, index=s.index, dtype=object)
    t = s.astype("string").str.strip()
    dig = t.str.fullmatch(r"\d{12,14}").fillna(False).astype(bool)
    d = t.where(dig).str.pad(14, side="right", fillchar="0")
    out = pd.to_datetime(d, format="%Y%m%d%H%M%S", utc=True, errors="coerce")
    if (~dig & t.notna()).any():
        out = out.where(dig, to_utc_series(t.where(~dig)))
    return iso_series(out)


def live_weather(cat) -> pd.DataFrame | None:
    """The per-pass weather sidecars `data/live/<run_id>_weather.parquet`, keyed by det_id (`_index`), with the valid
    times and the source text the field provenance shows (`_wind_time`, `_cloud_time`, `_wind_source`, `_cloud_source`)."""
    frames = [cat.read_parquet("live_weather", ["det_id"] + LIVE_WEATHER_COLS, path=p) for p in cat.paths("live_weather")]
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return None
    w = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({"det_id": w["det_id"].astype(str)})
    for c in ("wind_ms", "ctt_k", "deep_convection"):
        out[c] = (pd.to_numeric(w[c], errors="coerce").round(2) if c != "deep_convection" else w[c]) if c in w else None
    out["_wind_time"] = _gfs_valid(w["gfs_cycle_utc"], w["gfs_forecast_h"]) if {"gfs_cycle_utc", "gfs_forecast_h"} <= set(w) else None
    out["_cloud_time"] = _himawari_start(w["himawari_key"]) if "himawari_key" in w else None
    out["_wind_source"] = w["wind_source"] if "wind_source" in w else None
    out["_cloud_source"] = w["cloud_source"] if "cloud_source" in w else None
    return _index(out)


def review_times(cat) -> pd.DataFrame | None:
    """`reviewed_utc` of each hand-checked contact from data/live/<run_id>_review_*.csv (R3-T7), keyed by det_id."""
    frames = []
    for p in cat.paths("live_review"):
        try:
            f = cat.read_csv("live_review", ["det_id", "reviewed_utc"], path=p)
        except Exception:  # noqa: BLE001  (a table being rewritten is read again on the next reload)
            f = None
        if f is not None and len(f) and {"det_id", "reviewed_utc"} <= set(f):
            f = f.dropna(subset=["reviewed_utc"]).copy()
            f["_review_file"] = _data_rel(cat, p)
            frames.append(f)
    if not frames:
        return None
    from .context import time_text

    r = pd.concat(frames, ignore_index=True).drop_duplicates("det_id", keep="last")
    # as the table wrote it: a date stays a date, a date-time becomes ISO 8601 UTC with Z
    return _index(pd.DataFrame({"det_id": r["det_id"].astype(str), "_review_time": [time_text(v) for v in r["reviewed_utc"]],
                                "_review_file": r["_review_file"].to_numpy()}))


def _data_rel(cat, p) -> str:
    """A data file's path as the docs name it (data/live/...), whatever the data directory is."""
    try:
        return "data/" + p.resolve().relative_to(cat.data_dir.resolve()).as_posix()
    except ValueError:
        return p.name


def load(cat, settings) -> ContactsData:
    fields = [f for f, _ in contact_spec(settings.build)]
    fset = set(fields)
    weather = _index(cat.read_parquet("weather", ["det_id"] + WEATHER_COLS))
    if weather is not None and "himawari_start" in weather:  # the producer's compact scan start, as ISO 8601
        weather["himawari_start"] = himawari_start_text(weather["himawari_start"]).to_numpy()
    optical = _index(cat.read_gpkg("optical", ["det_id"] + OPTICAL_COLS))
    scenes = cat.read_gpkg("regional_scenes")
    if scenes is not None and len(scenes):
        scenes = group_passes(scenes)
    frames, extras = [], {}

    def add(df, source):
        if df is None or not len(df):
            return
        df = _join(df, weather, WEATHER_COLS)  # himawari_start (kept in extra) is the field provenance time of ctt_k
        df = _join(df, optical, OPTICAL_COLS)
        df, ex = _finish(df, source, fset)
        frames.append(df)
        extras[source] = ex

    def regional_scene_cols(df):
        """scene_id, pass_id, pass_dir, orbit_rel through scene_idx; a value the file already holds is kept."""
        if scenes is None or "scene_idx" not in df:
            return df
        s = scenes.drop_duplicates("scene_idx").set_index("scene_idx")
        idx = pd.to_numeric(df["scene_idx"], errors="coerce")
        for col, src in (("scene_id", "product_id"), ("pass_id", "pass_id"), ("pass_dir", "pass_dir"), ("orbit_rel", "orbit_rel")):
            vals = pd.Series(s[src].reindex(idx).to_numpy(), index=df.index)
            df[col] = df[col].where(df[col].notna(), vals) if col in df else vals
        return df

    # live passes (D1 native)
    live_about = cat.read_gpkg("live_about")
    about = {} if live_about is None or not len(live_about) else live_about.iloc[0].to_dict()
    live = cat.read_gpkg("live_contacts")
    if live is not None and len(live):
        live = live[live["confidence"].astype(str) != "low"].copy()  # contract 4.4: live low objects stay out
        live["pass_id"] = live["run_id"]
        # weather sidecars first (the regional weather table holds no live det_id), then the hand-check times
        live = _join(live, live_weather(cat), ["wind_ms", "ctt_k", "deep_convection", "_wind_time", "_cloud_time",
                                               "_wind_source", "_cloud_source"])
        live = _join(live, review_times(cat), ["_review_time", "_review_file"])
        if "az_time_utc" in live:
            live["az_time_utc"] = iso_series(live["az_time_utc"])
        if "ambiguous_mmsi" in live:  # one MMSI or several joined by ';' as the producer wrote them
            amb = live["ambiguous_mmsi"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
            live["ambiguous_mmsi"] = amb.astype(object).where(amb.notna() & (amb != ""), None)
        if "review_note" in live:
            live["review_grade"] = review_grade(live["review_note"])
        has_id = live.get("mmsi", pd.Series(index=live.index, dtype=object)).notna() | \
            live.get("nearest_ais_mmsi", pd.Series(index=live.index, dtype=object)).notna()
        aiss = live.get("ais_source", pd.Series(index=live.index, dtype=object)).astype("string").eq("aisstream").fillna(False)
        live["identity_label"] = np.where(has_id & aiss, AISSTREAM_LABEL, None)
        if about.get("cnn_model_id") is not None:
            live["cnn_model_id"] = about.get("cnn_model_id")
            live["cnn_threshold"] = pd.to_numeric(pd.Series([about.get("cnn_threshold")]), errors="coerce").iloc[0]
        add(live, "live")

    # September run: research identity replaces the not_checked rows in the research build (contract 4.2). The run and
    # the structures are read before the CNN table, so only their CNN rows are converted.
    research = settings.research and cat.exists("regional_identity")
    if research:
        reg = cat.read_parquet("regional_identity")
        reg = _join(reg, cat.read_gpkg("regional_contacts", REGIONAL_DETECTOR), REGIONAL_DETECTOR[1:], fill=True)
    else:
        reg = cat.read_gpkg("regional_contacts")
    st = cat.read_gpkg("structures")
    cnn = read_cnn(cat, [f["det_id"] for f in (reg, st) if f is not None and len(f)])
    if research:
        reg = _join(reg, cnn, ["scene_id"] + CNN_COLS)
        reg = regional_scene_cols(reg)
        add(reg, "research")
    elif reg is not None and len(reg):
        reg["run_id"] = "regional_2026-09"
        reg = _join(reg, cnn, ["scene_id"] + CNN_COLS)
        reg = regional_scene_cols(reg)
        add(reg, "regional")

    if st is not None and len(st):
        st["run_id"] = "regional_2026-09"
        st = _join(st, cnn, ["scene_id"] + CNN_COLS)
        st = regional_scene_cols(st)
        add(st, "structures")

    cm = cat.read_gpkg("camau_contacts")
    if cm is not None and len(cm):
        cm["run_id"] = "camau_2026-09-29"
        cm["mission"] = cm["det_id"].astype(str).str[:3]
        add(cm, "camau")

    df = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame(columns=fields + ["_t", "_source"])
    for c in ("vessel_key", "nearest_vessel_key"):
        df[c] = None
    if len(df):
        matched = df["ais_status"].eq("matched")
        if "gfw_vessel_id" in df:
            g = df["gfw_vessel_id"].astype(object)
            df["vessel_key"] = np.where(matched & g.notna(), "gfw:" + g.astype(str), None)
        m = df["mmsi"].astype(object)
        live_rows = df["_source"].eq("live")
        df.loc[live_rows & matched & m.notna(), "vessel_key"] = "mmsi:" + m[live_rows & matched & m.notna()].astype(str)
        nm = df["nearest_ais_mmsi"].astype(object)
        df.loc[live_rows & nm.notna(), "nearest_vessel_key"] = "mmsi:" + nm[live_rows & nm.notna()].astype(str)
        if "nearest_ais_vessel_id" in df:
            nv = df["nearest_ais_vessel_id"].astype(object)
            res = df["_source"].eq("research") & nv.notna()
            df.loc[res, "nearest_vessel_key"] = "gfw:" + nv[res].astype(str)
        # local evening date (UTC+7) of the contact, for the same-night light links (contract 3.3)
        local = (df["_t"] + pd.Timedelta(hours=7) - pd.Timedelta(hours=12)).dt.tz_localize(None).to_numpy()
        df["_night"] = np.datetime_as_string(local.astype("datetime64[D]"), unit="D")
    return ContactsData(df, extras, scenes, about, fields)


def source_caveat_extra(row: dict) -> dict:
    ex = {}
    sc = row.get("_source_caveat")
    if isinstance(sc, str) and sc and sc != PRODUCT_CAVEAT:
        ex["source_caveat"] = sc
    sa = row.get("_source_ais_status")
    if isinstance(sa, str) and sa:
        ex["source_ais_status"] = sa
    return ex
