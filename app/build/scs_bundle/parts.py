"""Bundle parts (contract 6.1 and 6.2, README readings 1 to 12) from the backend's loaders and record builders.

Every record comes from `scs_api.store.Store` (the same code that answers the API), so a value decoded from the page
equals the API value within the column's stated scale. Bulk parts are columnar; identity strings live once per vessel
(`vessel_ref`, `nearest_ref` into `vessels`); `records` hold per-object extras for the subsets the contract names.
"""

from __future__ import annotations

import hashlib
import io
import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import BUILDER_VERSION
from . import encode as E

CONF_ORDER = ["high", "medium", "fixed", "low"]
STATUS_ORDER = ["matched", "unmatched", "no_coverage", "not_checked"]
QUALITY_ORDER = ["high", "medium", "low"]
VIEW_ORDER = ["regional", "live", "camau"]
SRC_ORDER = ["det_regional", "det_live", "det_camau"]
LEAD_TYPES = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
LEAD_STATES = ["new", "reviewing", "closed_explained", "closed_unexplained", "closed_false_alarm"]
PRIMARY_PART = {"contact": "contacts", "light": "lights", "cell": "cells", "vessel": "vessels"}
IDENTITY_PAIRS = [("mmsi", "mmsi"), ("imo", "imo"), ("vessel_name", "name"), ("call_sign", "call_sign"), ("flag", "flag"),
                  ("ship_type", "ship_type"), ("length_ais_m", "length_ais_m"), ("identity_source", "identity_source")]
SIMPLIFIED = "simplified for display; the GeoPackage holds the published geometry"

# Contact columns (name, encoder). Core columns are always written; optional ones in this priority order while the part
# stays within its budget (the rest are listed in meta.dropped as columns left out).
CONTACT_CORE = [
    ("det_id", "detid"), ("run_id", "dict"), ("acq_utc", "time"), ("lon", 100000), ("lat", 100000), ("length_est_m", 10),
    ("confidence", CONF_ORDER), ("cnn_score", 250), ("cnn_vessel", "bool"), ("ais_status", STATUS_ORDER),
    ("ais_source", "dict"), ("match_method", "dict"), ("match_dist_m", 1), ("match_dt_s", 1), ("match_quality", QUALITY_ORDER),
    ("vessel_ref", "ref"), ("nearest_ref", "ref"), ("nearest_ais_dist_m", 1), ("nearest_ais_dt_s", 1), ("n_ais_10km", 1),
    ("ais_reach", 1000), ("research_only", "bool"), ("caveat", "const"), ("view", VIEW_ORDER), ("src", SRC_ORDER),
    ("pass_id", "dict"), ("dark_lead", "bool"),
]
# `mission` is not written: the adapter takes it from the first three characters of det_id (same value for every row).
CONTACT_OPTIONAL = [
    ("scene_id", "dict"), ("persist_dates", 1), ("persist_dates_checked", 1), ("cnn_threshold", 1000000),
    ("cnn_model_id", "dict"), ("scr_vv_db", 100), ("scr_vh_db", 100), ("wind_ms", 100), ("deep_convection", "bool"),
    ("inc_angle_deg", 100), ("cnn_chip_valid_frac", 1000), ("pass_dir", "dict"), ("orbit_rel", 1), ("pol_class", "dict"),
    ("n_pixels", 1), ("low_reason", "dict"), ("n_low_1km", 1), ("near_fixed_m", 10), ("match_gate_m", 1), ("ais_sog_kn", 10),
    ("length_ratio", 1000), ("ais_class", "dict"), ("ais_footprint_positions", 1), ("ais_recorded_hours", 1),
    ("pred_method", "dict"), ("bg_vv_db", 100), ("bg_vh_db", 100), ("ctt_k", 10), ("optical_object", "bool"),
    ("optical_kind", "dict"), ("satlas_m", 1), ("cnn_score_source", "dict"),
]
# Fields a contact record never repeats: bulk or identity-by-reference fields and values the adapter derives.
CONTACT_DERIVED = {"mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source",
                   "gfw_vessel_id", "nearest_ais_mmsi", "vessel_key", "nearest_vessel_key", "cell_id", "chip", "caveat",
                   "src", "mission"}

# Vessel columns: core = what identity resolution, the vessel list and the map need; optional in priority order while
# the part stays within its budget.
VESSEL_CORE = [
    ("vessel_key", "str"), ("mmsi", "str"), ("name", "str"), ("call_sign", "str"), ("imo", "str"), ("flag", "dict"),
    ("ship_type", "dict"), ("length_ais_m", 10), ("identity_source", "dict"), ("stub", "bool"), ("identity_note", "dict"),
    ("research_only", "bool"), ("caveat", "const"), ("src", "dict"), ("last_seen_utc", "time"), ("last_lon", 100000),
    ("last_lat", 100000), ("in_aoi", "bool"), ("ais_class", ["A", "B"]),
]
VESSEL_OPTIONAL = [
    ("length_m", 10), ("identity_kind", "dict"), ("gear_type", "dict"), ("first_seen_utc", "time"), ("n_positions", 1),
    ("mid", 1), ("tonnage_gt", 10), ("registry_sources", "dict"), ("registry_records", 1), ("dataset_version", "dict"),
    ("sog_kn", 10), ("cog_deg", 10), ("heading", 1), ("nav_status_label", "dict"), ("ever_in_aoi", "bool"),
    ("gear_beacon_like", "bool"), ("width_m", 10), ("static_seen_utc", "time"), ("destination", "str"), ("eta", "str"),
    ("n_messages", 1),
]
VESSEL_COLUMNS = VESSEL_CORE + VESSEL_OPTIONAL
# Light positions: longitude to 0.002 degree (u16) and latitude to 0.001 degree (i16), about 200 m and 110 m, well
# inside a VIIRS pixel (about 750 m: NASA LAADS VIIRS page, "Spatial Resolution: 750m"); the local app and the GeoPackage
# keep the full precision.
LIGHT_CORE = [("light_id", "lightid"), ("satellite", ["S-NPP", "NOAA-20", "NOAA-21"]), ("time_utc", "time"),
              ("night", "dict"), ("lon", 500), ("lat", 1000), ("radiance_nw", 100), ("quality", ["clear", "under_cloud"]),
              ("caveat", "const"), ("src", "const"), ("research_only", "const")]
LIGHT_OPTIONAL = [("nights_seen_500m", 1), ("class", "dict"), ("s1_passes_90d", 1), ("site_id", "dict"),
                  ("clear_nights_cell", 1), ("moon_illum_pct", 10), ("spike_nw", 100), ("isolation", 100),
                  ("satlas_infra_m", 1)]
LIGHT_POSITIONS_ONLY = ["light_id", "satellite", "time_utc", "night", "lon", "lat", "quality", "caveat", "src", "research_only"]


@dataclass
class Ctx:
    build: str
    store: Any
    log: Callable = print
    cache_dir: Path | None = None
    notes: dict = field(default_factory=dict)

    @property
    def research(self) -> bool:
        return self.build == "research"

    @property
    def cat(self):
        return self.store.cat


# ----------------------------------------------------------------------------------------------- helpers
def encode_col(values, how) -> dict:
    """One column by its encoder spec: an int is the scale of a numeric column, a list the dict order. A dict, bool or
    str column whose rows all hold the same value is written as `const` (the narrowest form); time columns never are,
    because the frontend reads acq_utc and time_utc as typed times."""
    values = list(values)
    if how in ("dict", "bool", "str") or isinstance(how, list):
        first = E.plain(values[0]) if values else None
        if values and all(E.plain(v) == first and (first is not None or E.plain(v) is None) for v in values):
            return E.const(first)
    if isinstance(how, int):
        return E.num(values, how)
    if isinstance(how, list):
        return E.dict_col(values, how)
    if how == "dict":
        return E.dict_col(values)
    if how == "bool":
        return E.bool8(values)
    if how == "time":
        return E.time_col(values)
    if how == "detid":
        return E.detid(values)
    if how == "str":
        return E.strs(values)
    raise ValueError(f"unknown encoder {how!r}")


def obj_series(s: pd.Series) -> list:
    return [E.plain(v) for v in s.tolist()]


_CODE_HASH: str | None = None


def code_hash() -> str:
    """Hash of the builder's and the backend's Python sources: a code change invalidates every cached part."""
    global _CODE_HASH
    if _CODE_HASH is None:
        h = hashlib.sha1()
        here = Path(__file__).resolve().parent
        for d in (here, here.parents[1] / "backend" / "scs_api"):
            for f in sorted(d.rglob("*.py")):
                h.update(f.read_bytes())
        _CODE_HASH = h.hexdigest()[:12]
    return _CODE_HASH


def cache_key(ctx: Ctx, name: str, deps: list[str], extra: str = "") -> str:
    sig = ctx.cat.signature()
    parts = [BUILDER_VERSION, code_hash(), ctx.build, name, extra] + [f"{k}={sig.get(k)}" for k in sorted(deps)]
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def cached(ctx: Ctx, name: str, deps: list[str], fn: Callable, extra: str = ""):
    """`fn()` cached under data/cache/bundle/<build>_<name>_<key>.pkl.gz, keyed by the input files' signatures."""
    if ctx.cache_dir is None:
        return fn()
    import gzip

    key = cache_key(ctx, name, deps, extra)
    path = ctx.cache_dir / f"{ctx.build}_{name}_{key}.pkl.gz"
    if path.exists():
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except Exception:  # noqa: BLE001 (a broken cache file is rebuilt)
            pass
    out = fn()
    ctx.cache_dir.mkdir(parents=True, exist_ok=True)
    for old in ctx.cache_dir.glob(f"{ctx.build}_{name}_*.pkl*"):
        old.unlink(missing_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=1) as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    return out


# Catalog entries no contact record reads: the recorder's growing position and static folders (they would invalidate the
# cache every few minutes) and the chip cache (the `chip` field is never in a bundle record).
RECORD_DEPS_SKIP = {"ais_positions", "ais_static", "chips"}


def record_deps(ctx: Ctx, vessels: bool = False) -> list[str]:
    """Cache dependencies of the record frames: every catalog file of the build (records join many files: live review
    tables, weather sidecars, object context, leads), except RECORD_DEPS_SKIP. Vessel records also read the AIS
    position folder (last positions of stubs), so it stays in for them."""
    skip = RECORD_DEPS_SKIP - ({"ais_positions", "ais_static"} if vessels else set())
    return sorted(k for k in ctx.cat.specs if k not in skip)


def same(a, b) -> bool:
    a, b = E.plain(a), E.plain(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
    return str(a) == str(b)


# ----------------------------------------------------------------------------------------------- contacts
def contact_frame(ctx: Ctx) -> pd.DataFrame:
    """Full API Contact records of the bundle rows: open = product contacts (low left out, contract 4.4) plus every Ca
    Mau object; research = product contacts without the Ca Mau scene (contract 6.3, camau left out)."""
    st = ctx.store

    def build():
        df = st.data["contacts"].df
        if not len(df):
            return pd.DataFrame()
        view = df["view"].astype(str).to_numpy()
        mask = st.product_mask.copy()
        if ctx.research:
            mask &= view != "camau"
        else:
            mask |= view == "camau"
        pos = np.flatnonzero(mask)
        frames = []
        for chunk in np.array_split(pos, max(1, len(pos) // 20000)):
            recs = st.contact_rows(chunk, full=True)
            fr = pd.DataFrame.from_records(recs)
            for c in ("lead_ids", "prov", "extra"):
                if c in fr:
                    fr[c] = fr[c].map(lambda v: json.dumps(v, separators=(",", ":")) if v not in (None, [], {}) else None)
            frames.append(fr)
        out = pd.concat(frames, ignore_index=True)
        out["_source"] = df["_source"].to_numpy()[pos]
        return out

    return cached(ctx, "contacts", record_deps(ctx), build)


def prov_sets() -> dict:
    """Per-source provenance maps (the loader's PROV) and the rule that picks one for a row."""
    from scs_api.loaders.contacts import PROV

    return {k: dict(v) for k, v in PROV.items()}


def prov_set_of(fr: pd.DataFrame) -> np.ndarray:
    return fr["_source"].astype(str).to_numpy()


def order_contacts(fr: pd.DataFrame, lead_rank: dict[str, int]) -> pd.DataFrame:
    """Identity first: live passes (newest first), lead primaries in queue order, matched contacts, then the rest of
    the September run, fixed structures, the Ca Mau scene."""
    view = fr["view"].astype(str)
    group = np.select([view.eq("live"), fr["_source"].eq("structures"), view.eq("camau")], [0, 3, 4], 2)
    rank = fr["det_id"].map(lambda d: lead_rank.get(d, 10**9)).to_numpy()
    group = np.where((group == 2) & (rank < 10**9), 1, group)
    matched = (fr["ais_status"] == "matched").to_numpy()
    t = pd.to_datetime(fr["acq_utc"], utc=True, errors="coerce", format="ISO8601")
    key = pd.DataFrame({"g": group, "r": rank, "m": ~matched, "t": -(t.astype("int64") // 10**9), "id": fr["det_id"]})
    order = key.sort_values(["g", "r", "m", "t", "id"], kind="stable").index
    return fr.loc[order].reset_index(drop=True)


def identity_resolution(fr: pd.DataFrame, vidx: dict[str, int], vdf: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    """vessel_ref and nearest_ref per contact, plus the contacts whose own identity strings differ from the vessel row
    they reference (behaviour rule 6) or whose vessel is not in the part: {det_id: {field: own value}}."""
    vref = np.array([vidx.get(k, -1) if isinstance(k, str) else -1 for k in fr.get("vessel_key", pd.Series([None] * len(fr)))])
    nref = np.array([vidx.get(k, -1) if isinstance(k, str) else -1 for k in fr.get("nearest_vessel_key", pd.Series([None] * len(fr)))])
    own: dict[str, dict] = {}
    stats = {"matched": 0, "by_reference": 0, "own_strings": 0, "vessel_missing": 0, "nearest_overrides": 0}
    vcols = {vf: vdf[vf].tolist() if vf in vdf else [None] * len(vdf) for _, vf in IDENTITY_PAIRS}
    vmmsi = vdf["mmsi"].tolist() if "mmsi" in vdf else [None] * len(vdf)
    matched = (fr["ais_status"] == "matched").to_numpy()
    det = fr["det_id"].tolist()
    for i in np.flatnonzero(matched):
        stats["matched"] += 1
        r = int(vref[i])
        diff = {}
        for cf, vf in IDENTITY_PAIRS:
            mine = fr.at[i, cf] if cf in fr else None
            theirs = vcols[vf][r] if r >= 0 else None
            if not same(mine, theirs):
                diff[cf] = E.plain(mine)
        if r < 0:
            stats["vessel_missing"] += 1
        if diff:
            own[det[i]] = diff
            stats["own_strings"] += 1
        else:
            stats["by_reference"] += 1
    # nearest AIS vessel: the adapter takes nearest_ais_mmsi from the nearest_ref row; record the contact's own value
    # where that differs (GFW identity of the vessel id changed between the presence data and the vessels API)
    near = fr["nearest_ais_mmsi"].tolist() if "nearest_ais_mmsi" in fr else [None] * len(fr)
    for i, m in enumerate(near):
        r = int(nref[i])
        theirs = vmmsi[r] if r >= 0 else None
        if not same(m, theirs):
            own.setdefault(det[i], {})["nearest_ais_mmsi"] = E.plain(m)
            stats["nearest_overrides"] += 1
    return vref, nref, own, stats


# Record fields the page does not carry: contract 1.3.0 field-level provenance (`field_prov`: source key, valid time and
# source text per field). The embedded adapter does not read it; the local app serves it. Listed in meta.dropped.
CONTACT_RECORD_SKIP = {"field_prov"}
# Record fields the part carries in a block instead: `object_context` (README reading 13 block, see context_block).
CONTACT_RECORD_BLOCK = {"object_context"}


def prov_set_name(view, research_only, confidence) -> str:
    """The embedded adapter's provSetName (README reading 16): the `prov_sets` entry that is a contact row's default
    provenance. A record's `prov` holds only what differs from it."""
    if view == "live":
        return "live"
    if view == "camau":
        return "camau"
    if research_only is True or research_only == 1:
        return "research"
    if confidence == "fixed":
        return "structures"
    return "regional"


def contact_record(row: dict, bulk: set[str], part_prov: dict) -> dict:
    """The fields of one API record that are not bulk columns, with nulls left out (contract 6.2). `part_prov` is the
    row's default provenance (its prov_sets entry); the record's `prov` keeps only the fields that differ."""
    out = {}
    for k, v in row.items():
        if (k.startswith("_") or k in bulk or k in CONTACT_DERIVED or k in CONTACT_RECORD_SKIP or k in CONTACT_RECORD_BLOCK
                or k in ("vessel_ref", "nearest_ref")):
            continue
        if k in ("lead_ids", "prov", "extra"):
            v = json.loads(v) if isinstance(v, str) else v
            if E.is_null(v):
                continue
            if k == "prov":
                v = {f: s for f, s in (v or {}).items() if part_prov.get(f) != s}
            if not v:
                continue
        v = E.plain(v)
        if v is None or (k == "match_ambiguous" and v is not True):  # README reading 16: only when true
            continue
        out[k] = v
    return out


def unrecorded_fields(rows: pd.DataFrame, bulk: set[str], records: dict, prov_base: Callable[[int], dict],
                      lead_primary: set[str] | None = None, derived: dict[str, list] | None = None) -> tuple[int, dict]:
    """Record-only fields of the contacts that have no record in the page: {field: rows holding a value} (the keys of
    `extra` as `extra.<key>`, `prov` where it differs from the row's prov set). Not counted, because the page holds
    them another way: extension columns (their own meta.dropped entry); `lead_ids` that are only the leads whose
    primary is the contact (the adapter takes them from the leads part); the aisstream `identity_label` of an
    aisstream match (the frontend's identityLabel rule); `match_ambiguous` false (README reading 16 carries it only
    when true); a value equal to what the adapter derives for the row (`derived`: {field: value per row of `rows`},
    for example the nearest AIS vessel's name from its `nearest_ref` row). Returns (rows without a record, counts)."""
    if not len(rows):
        return 0, {}
    pos = np.flatnonzero(~rows["det_id"].isin(set(records)).to_numpy())
    sub = rows.iloc[pos]
    from scs_api.config import AISSTREAM_LABEL  # board D4.7

    lead_primary = lead_primary or set()
    det = sub["det_id"].astype(str).tolist()
    src = sub["ais_source"].tolist() if "ais_source" in sub else [None] * len(sub)
    skip = (bulk | CONTACT_DERIVED | CONTACT_RECORD_SKIP | CONTACT_RECORD_BLOCK | {"vessel_ref", "nearest_ref"}
            | {n for n, _ in CONTACT_OPTIONAL})
    counts: dict[str, int] = {}
    for c in sub.columns:
        if c.startswith("_") or c in skip or c == "prov":
            continue
        if c in ("extra", "lead_ids"):
            for j, v in enumerate(sub[c].tolist()):
                v = json.loads(v) if isinstance(v, str) else v
                if E.is_null(v) or not v:  # None, NaN (a pandas str column's missing value) or empty
                    continue
                if c == "lead_ids" and det[j] in lead_primary and all(str(x).endswith("-" + det[j]) for x in v):
                    continue
                for k in (v if c == "extra" and isinstance(v, dict) else [None]):
                    name = f"extra.{k}" if k is not None else c
                    counts[name] = counts.get(name, 0) + 1
            continue
        vals = sub[c].tolist()
        if derived and c in derived:
            dv = [derived[c][int(i)] for i in pos]
            vals = [None if same(v, w) else v for v, w in zip(vals, dv)]
        if c == "identity_label":
            vals = [None if (v == AISSTREAM_LABEL and s_ == "aisstream") else v for v, s_ in zip(vals, src)]
        elif c == "match_ambiguous":
            vals = [v if v is True or v == 1 else None for v in vals]
        k = int(sum(1 for v in vals if E.plain(v) is not None and not (isinstance(v, (list, dict)) and not v)))
        if k:
            counts[c] = k
    if "prov" in sub:
        k = 0
        for j, v in zip(range(len(sub)), sub["prov"].tolist()):
            v = json.loads(v) if isinstance(v, str) else ({} if E.is_null(v) else (v or {}))
            base = prov_base(int(pos[j]))
            if any(base.get(f) != s for f, s in (v or {}).items()):
                k += 1
        if k:
            counts["prov"] = k
    return int(len(sub)), dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def contacts_columns(fr: pd.DataFrame, vref, nref, optional: list[tuple], caveat: str) -> dict:
    cols = {}
    core = list(CONTACT_CORE)
    if len(fr) and "mission" in fr and not (fr["mission"].astype(str) == fr["det_id"].astype(str).str[:3]).all():
        core.append(("mission", ["S1C", "S1D"]))  # the adapter's det_id rule would not hold: write the column
    for name, how in core + optional:
        if name == "vessel_ref":
            cols[name] = E.ref16(vref, "vessels")
        elif name == "nearest_ref":
            cols[name] = E.ref16(nref, "vessels")
        elif name == "caveat":
            cols[name] = E.const(caveat)
        elif name not in fr:
            continue
        else:
            cols[name] = encode_col(obj_series(fr[name]), how)
    return cols


def columns_bytes(cols: dict) -> int:
    return sum(E.column_bytes(c) + len(k) + 4 for k, c in cols.items())


# ----------------------------------------------------------------------------------------------- vessels
def vessel_frame(ctx: Ctx) -> pd.DataFrame:
    """Full API Vessel records: aisstream vessels (both builds) and, in the research build, the GFW rows (gfw_vessels,
    gfw_events_vessels and stubs for nearest-AIS vessels) as the backend loads them."""
    st = ctx.store

    def build():
        vd = st.data["vessels"]
        df = vd.df
        if not len(df):
            return pd.DataFrame(columns=[n for n, _ in VESSEL_COLUMNS])
        recs = []
        pos = np.arange(len(df))
        for chunk in np.array_split(pos, max(1, len(pos) // 20000)):
            recs.extend(st.vessel_rows(chunk, full=True))
        fr = pd.DataFrame.from_records(recs)
        fr = fr.drop(columns=[c for c in ("contacts_matched", "prov", "extra") if c in fr])
        fr["_src"] = df["_src"].to_numpy()
        return fr

    return cached(ctx, "vessels", record_deps(ctx, vessels=True), build)


def vessels_part(ctx: Ctx, vdf: pd.DataFrame, tracks: dict | None, note: str, optional: list | None = None) -> dict:
    cols = {}
    for name, how in VESSEL_CORE + (VESSEL_OPTIONAL if optional is None else optional):
        if name == "caveat":
            cols[name] = E.const(ctx.store.cav())
        elif name in vdf:
            cols[name] = encode_col(obj_series(vdf[name]), how)
    prov = {"flag": "mid_itu", "mid": "mid_itu", "contacts_matched": "app"}
    p = E.part("vessels", len(vdf), cols, records={}, prov=prov, note=note)
    if ctx.research:
        p["prov_by_src"] = {"aisstream": prov, "gfw_vessels": {"contacts_matched": "app", "mid": "app"}}
    if tracks is not None:
        p["tracks"] = tracks
    return p


def tracks_block(ctx: Ctx, vidx: dict[str, int]) -> tuple[dict | None, dict]:
    """aisstream tracks (`tracks_4326`, one line per MMSI) simplified at 0.002 degree (README reading 5)."""
    def build():
        g = ctx.cat.read_gpkg("ais_tracks", geometry=True)
        if g is None or not len(g):
            return None
        g = g[["mmsi", "start_utc", "end_utc", "n_positions", "geometry"]].copy()
        g["geometry"] = g.geometry.simplify(0.002, preserve_topology=False)
        return g
    deps = ["ais_tracks"]
    g = cached(ctx, "tracks", [d for d in deps if d in ctx.cat.specs], build)
    if g is None:
        return None, {"tracks": 0}
    from scs_api.loaders.contacts import digit_series

    keys = "mmsi:" + digit_series(g["mmsi"]).astype(str)
    ref = np.array([vidx.get(k, -1) for k in keys])
    keep = (ref >= 0) & g.geometry.notna().to_numpy() & ~g.geometry.is_empty.to_numpy()
    g, ref = g[keep], ref[keep]
    block = E.geom(list(g.geometry), "line", props={
        "vessel_ref": E.ref16(ref, "vessels"), "start_utc": E.time_col(g["start_utc"]), "end_utc": E.time_col(g["end_utc"]),
        "n_positions": E.num(g["n_positions"], 1)},
        note="simplified for display at 0.002 degree; per-position times and gap detection stay in the local app")
    return block, {"tracks": int(len(g)), "vertices": int(sum(len(x.coords) for x in g.geometry if x.geom_type == "LineString"))}


# ----------------------------------------------------------------------------------------------- lights
def light_frame(ctx: Ctx) -> pd.DataFrame:
    st = ctx.store

    def build():
        ld = st.data["lights"]
        df = ld.lights
        if not len(df):
            return pd.DataFrame()
        recs = st.light_rows(np.arange(len(df)), full=False)
        fr = pd.DataFrame.from_records(recs)
        for c in ("spike_nw", "isolation", "clear_nights_cell", "moon_illum_pct", "satlas_infra_m"):
            if c in df:
                fr[c] = df[c].to_numpy()
        return fr
    deps = ["lights", "sites"]
    return cached(ctx, "lights", [d for d in deps if d in ctx.cat.specs], build)


def sites_block(ctx: Ctx) -> dict | None:
    ld = ctx.store.data["lights"]
    s = ld.sites
    if not len(s):
        return None
    cols = {"site_id": E.strs(s["site_id"]), "lon": E.num(s["lon"], 100000), "lat": E.num(s["lat"], 100000),
            "n_lights": E.num(s.get("n_lights"), 1), "nights": E.num(s.get("nights"), 1),
            "radiance_med_nw": E.num(s.get("radiance_med_nw"), 10), "radiance_max_nw": E.num(s.get("radiance_max_nw"), 10),
            "satlas_infra_m": E.num(s.get("satlas_infra_m"), 1), "nights_seen_max": E.num(s.get("nights_seen_max"), 1),
            "s1_passes_90d": E.num(s.get("s1_passes_90d"), 1), "likely": E.dict_col(s.get("likely"))}
    return E.part("sites", len(s), cols)


def lights_part(ctx: Ctx, lf: pd.DataFrame, optional: list[tuple], positions_only_cloud: bool,
                with_sites: bool = False) -> tuple[dict, dict]:
    """Columnar lights. Drop rule 2: the lights under cloud leave the columns and keep only their position, time and
    night in the `under_cloud_positions` block (shown as positions, no Light record)."""
    cav = ctx.store.cav(_light_caveat())
    full = lf
    cloud_block = None
    if positions_only_cloud and len(lf):
        cloud = (lf["quality"] == "under_cloud").to_numpy()
        c = lf[cloud]
        cloud_block = {"n": int(len(c)), "columns": {
            "lon": encode_col(obj_series(c["lon"]), 500), "lat": encode_col(obj_series(c["lat"]), 1000),
            "time_utc": E.time_col(c["time_utc"]), "night": E.dict_col(obj_series(c["night"]))},
            "note": "drop rule 2: lights under cloud as positions only; their records are in the local app"}
        full = lf[~cloud].reset_index(drop=True)
    cols = {}
    for name, how in LIGHT_CORE + optional:
        if name == "light_id":
            continue
        if name == "caveat":
            cols[name] = E.const(cav)
        elif name == "src":
            cols[name] = E.const("viirs_dnb")
        elif name == "research_only":
            cols[name] = E.const(False)
        elif name in full:
            cols[name] = encode_col(obj_series(full[name]), how)
    cols = {"light_id": E.lightid(full["light_id"], full["satellite"], full["time_utc"]), **cols}
    info = {"lights": int(len(full)), "under_cloud": int((lf["quality"] == "under_cloud").sum()) if len(lf) else 0,
            "positions_only_cloud": bool(positions_only_cloud), "sites": bool(with_sites)}
    p = E.part("lights", len(full), cols, records={}, prov={"satlas_infra_m": "satlas", "site_id": "app",
                                                            "contacts_2km_same_night": "app", "cell_id": "app"},
               note=("contacts_2km_same_night and cell_id are computed in the page; positions to 0.002 degree longitude and "
                     "0.001 degree latitude (VIIRS pixels are about 750 m); full record in the local app"))
    if cloud_block is not None:
        p["under_cloud_positions"] = cloud_block
    if with_sites:
        sb = sites_block(ctx)
        if sb is not None:
            p["sites"] = sb
    return p, info


def _light_caveat() -> str:
    from scs_api.loaders.lights import LIGHT_CAVEAT

    return LIGHT_CAVEAT


# ----------------------------------------------------------------------------------------------- object context
# README reading 13 block: per field the block column, its unit, and either the registry key (`src`) or the column of
# dataset names (`src_col`), and the column of valid times (`time_col`); static fields have no time column (time null).
CONTEXT_BLOCK_SRC = {"sst_source": "sst_src", "chl_dataset": "chl_src"}  # table column -> block column (reading 13)
# Page scale per field (value = raw / s): the API's decimals where that keeps the narrowest integer type, else the
# precision the page shows (the km fields in metres below 1 km to 10 km, digits of app/frontend CONTEXT_FIELDS).
CONTEXT_PAGE_SCALE = {"depth_m": 10, "dist_coast_km": 100, "dist_port_km": 10, "sst_c": 1000, "sst_grad": 1000,
                      "dist_front_km": 10, "chl_log10": 1000, "current_speed_ms": 100, "mld_m": 100, "wave_hs_m": 100}
# Fill order when the page has no room for every field (radar and light interpretation first; then the rest). Each
# item brings its own valid-time and dataset columns. `time_utc` and `region` are always written.
CONTEXT_PRIORITY = ["depth_m", "dist_coast_km", "ship_presence_all", "ship_presence_fishing", "sst_c", "wave_hs_m",
                    "dist_port_km", "chl_log10", "cell_id", "dist_front_km", "current_speed_ms", "ship_presence_commercial",
                    "sst_grad", "mld_m", "ship_presence_oilgas", "ship_presence_passenger", "ship_presence_leisure"]
CONTEXT_BASE = ["time_utc", "region"]


def context_block(ctx: Ctx, kind: str, ids, sources=None) -> tuple[dict | None, dict]:
    """Board D5.3 `object_context` of the rows of a part as the README reading 13 columnar block, rows parallel to the
    part: `time_utc` (null = the context table has no row for the object), `region`, `cell_id`, one column per field
    (presence as bool8) at CONTEXT_PAGE_SCALE, the valid-time and dataset columns. Values come from the same table rows
    and the same rounding, time and source rules as `ContextData.records`, so a decoded field equals the API field
    within its scale. `time_utc` is `time` or a dict of its ISO text, whichever is smaller (both decode to the same
    text; the adapter reads it as text). A column that is the same for every row with context is `const` (rows without
    context are never read: their `time_utc` is null). kind: "contact" (sources = each row's `_source`) or "light".
    Returns (block with every field, or None when no row has context; info)."""
    from scs_api.loaders import context as LC
    from scs_api.records import iso_series

    ids = [str(i) for i in ids]
    n = len(ids)
    cd = ctx.store.data.get("context")
    if cd is None or getattr(cd, "objects", None) is None or not n:
        return None, {"rows": n, "with_context": 0, "note": "no context table loaded"}
    pos = np.full(n, -1, dtype=np.int64)
    if kind == "light":
        pos = cd.positions(LC.LIGHT_TYPES, ids)
    else:
        groups: dict[tuple, list[int]] = {}
        for i, src in enumerate(sources if sources is not None else ["regional"] * n):
            groups.setdefault(LC.CONTACT_TYPES.get(str(src), ("radar",)), []).append(i)
        for types, idx in groups.items():
            pos[np.asarray(idx)] = cd.positions(types, [ids[i] for i in idx])
    have = np.flatnonzero(pos >= 0)
    info = {"rows": n, "with_context": int(len(have))}
    if not len(have):
        return None, info
    sub = cd.objects.iloc[pos[have]]

    def spread(values, fill=None) -> list:
        out = [fill] * n
        for j, i in enumerate(have):
            out[i] = values[j]
        return out

    def text_col(values) -> dict:
        """dict column of text; const when every row with context holds the same value."""
        present = {v for v in values}
        if len(present) == 1:
            return E.const(next(iter(present)))
        return E.dict_col(spread(values))

    cols: dict[str, dict] = {}
    cols["time_utc"] = E.time_or_dict(spread(iso_series(sub["time_utc"]).tolist()))
    # region and cell_id exactly as ContextData.records writes them
    cols["region"] = text_col([None if x is None else str(x) for x in sub["region"].astype(object).tolist()]
                              if "region" in sub else [None] * len(sub))
    cols["cell_id"] = text_col([E.plain(x) for x in sub["cell_id"].tolist()] if "cell_id" in sub else [None] * len(sub))
    fields: dict[str, dict] = {}
    aux: dict[str, list[str]] = {}
    for f, (unit, tcol, scol, key, dec) in LC.CONTEXT_FIELDS.items():
        if f not in sub:
            continue
        vals = LC._plain(sub[f].to_numpy(), dec)
        if dec is None:  # presence (board D4.3): bool8, never a count
            cols[f] = E.bool8(spread(vals))
        else:
            cols[f] = E.num(spread(vals), CONTEXT_PAGE_SCALE.get(f, 10 ** dec), allow_const=False)
        spec: dict = {"unit": unit}
        aux[f] = []
        if scol and scol in sub:
            bc = CONTEXT_BLOCK_SRC[scol]
            if bc not in cols:  # the API's src: the table's dataset name, else the producer's registry key
                cols[bc] = text_col([(None if LC._null(x) else str(x)) or key for x in sub[scol].tolist()])
            spec["src_col"] = bc
            aux[f].append(bc)
        else:
            spec["src"] = key
        if tcol and tcol in sub:
            if tcol not in cols:
                cols[tcol] = text_col([LC.time_text(x) for x in sub[tcol].tolist()])
            spec["time_col"] = tcol
            aux[f].append(tcol)
        fields[f] = spec
    cav_rows = sub["caveat"].astype(object).tolist() if "caveat" in sub else [None]
    caveats = sorted({cd._caveat(c) for c in cav_rows})
    if len(caveats) > 1:
        raise E.EncodeError(f"object context rows carry {len(caveats)} caveats; the block holds one")
    info["caveats"] = len(caveats)
    block = {"n": n, "columns": cols, "fields": fields, "caveat": caveats[0], "_aux": aux,
             "note": "board D5.3 ocean context at each object, rows parallel to the part's rows; time_utc null = no row "
                     "in data/ocean_context_objects.parquet (live passes until the table is rebuilt after the pass)"}
    return block, info


def context_subset(block: dict | None, items: list[str], total_fields: int = 16) -> dict | None:
    """The block with CONTEXT_BASE plus `items` (fields and `cell_id`) and the valid-time and dataset columns those
    fields use. When fields are left out, the caveat says so, so every Context section names the gap."""
    if block is None:
        return None
    cols = block["columns"]
    keep = [c for c in CONTEXT_BASE if c in cols]
    fields = {}
    for it in items:
        if it in block["fields"]:
            fields[it] = block["fields"][it]
            keep += [it] + block["_aux"].get(it, [])
        elif it in cols:
            keep.append(it)
    keep = list(dict.fromkeys(keep))
    out = {"n": block["n"], "columns": {c: cols[c] for c in keep}, "fields": fields, "caveat": block["caveat"],
           "note": block["note"]}
    if len(fields) < total_fields:
        out["caveat"] = (block["caveat"] + f" This page carries {len(fields)} of the {total_fields} context fields to stay "
                         "within its size cap; the local app shows all of them.")
    return out


def context_bytes(block: dict | None) -> int:
    """Bytes the block adds to its part's element (key, colon, comma)."""
    return 0 if block is None else len(E.dumps(block)) + len('"object_context":,')


# ----------------------------------------------------------------------------------------------- leads
def lead_frame(ctx: Ctx) -> list[dict]:
    st = ctx.store
    ld = st.data["leads"]
    if not len(ld.df):
        return []
    pos, _ = st.leads_query({"state": ",".join(LEAD_STATES)})
    return st.lead_rows(pos, summary=True)


def leads_part(ctx: Ctx, leads: list[dict], row_of: dict[str, dict[str, int]], budget: int,
               record_reserve: int = 0) -> tuple[dict | None, dict]:
    """Columnar leads in priority order (contract 6.2, README reading 6); records for leads that are not new and for
    the highest-priority leads while the part budget allows; leads that do not fit are dropped from the end."""
    info = {"leads": len(leads), "kept": 0, "dropped_primary_missing": 0, "dropped_budget": 0, "records": 0,
            "id_rebuild_mismatch": 0, "primary_ids": [], "kept_ids": []}
    if not leads:
        return None, info
    rows = []
    for L in leads:
        part_name = PRIMARY_PART.get(L.get("primary_type"), "contacts")
        r = row_of.get(part_name, {}).get(str(L.get("primary_id")))
        if r is None:
            info["dropped_primary_missing"] += 1
            continue
        if L["lead_id"] != f"{L['lead_type']}-{L['primary_id']}":
            info["id_rebuild_mismatch"] += 1
        rows.append((L, part_name, r))
    factor_names: list[str] = []
    fmeta: dict[str, dict] = {}
    for L, _, _ in rows:
        for f in L.get("factors") or []:
            n = f.get("factor")
            if n not in fmeta:
                factor_names.append(n)
                fmeta[n] = {"factor": n, "column": "f_" + n, "max_points": f.get("max_points"), "source": f.get("source"),
                            "source_by_type": {}}
            fmeta[n]["source_by_type"].setdefault(L["lead_type"], f.get("source"))
    lawful: dict[str, list] = {}
    change: dict[str, list] = {}
    lawful_varies, change_varies = set(), set()
    for L, _, _ in rows:
        t = L["lead_type"]
        le, ch = list(L.get("lawful_explanations") or []), list(L.get("change_indicators") or [])
        if t not in lawful:
            lawful[t], change[t] = le, ch
        if lawful[t] != le:
            lawful_varies.add(t)
        if change[t] != ch:
            change_varies.add(t)

    def encode(rows_):
        L_ = [r[0] for r in rows_]
        cols = {
            "lead_type": E.dict_col([x["lead_type"] for x in L_], LEAD_TYPES),
            "primary_type": E.dict_col([r[1] for r in rows_], ["contacts", "lights", "cells", "vessels"]),
            "primary": E.u32_index([r[2] for r in rows_]),
            "priority": E.num([x["priority"] for x in L_], 1, allow_dict=False),
            "state": E.dict_col([x["state"] for x in L_], LEAD_STATES) if len({x["state"] for x in L_}) > 1 else E.const(L_[0]["state"]),
            "reason": E.dict_col([x.get("reason") for x in L_]) if any(x.get("reason") for x in L_) else E.const(None),
            "region_box": E.dict_col([x.get("region_box") for x in L_]),
            "time_utc": E.time_or_dict([x.get("time_utc") for x in L_]),
            "next_look_utc": E.time_or_dict([x.get("next_look_utc") for x in L_]),
            "lon": E.num([x["lon"] for x in L_], 100000, allow_dict=False),
            "lat": E.num([x["lat"] for x in L_], 100000, allow_dict=False),
        }
        for n in factor_names:
            pts = []
            for x in L_:
                v = next((f.get("points") for f in x.get("factors") or [] if f.get("factor") == n), None)
                pts.append(v)
            cols["f_" + n] = E.num(pts, 1)
        mids = {x.get("priority_model_id") for x in L_}
        cols["priority_model_id"] = E.const(L_[0].get("priority_model_id")) if len(mids) == 1 else E.dict_col([x.get("priority_model_id") for x in L_])
        cols["calibrated"] = E.bool8([x.get("calibrated") for x in L_]) if len({bool(x.get("calibrated")) for x in L_}) > 1 else E.const(bool(L_[0].get("calibrated")))
        ro = {bool(x.get("research_only")) for x in L_}
        cols["research_only"] = E.const(ro.pop()) if len(ro) == 1 else E.bool8([x.get("research_only") for x in L_])
        cols["caveat"] = E.const(ctx.store.cav())
        cols["src"] = E.const("app")
        return cols

    # provenance shared by most leads of a type goes to the part; a record keeps only what differs
    prov_by_type: dict[str, dict] = {}
    for t in lawful:
        maps = [json.dumps(r[0].get("prov") or {}, sort_keys=True) for r in rows if r[0]["lead_type"] == t]
        if maps:
            prov_by_type[t] = json.loads(max(set(maps), key=maps.count))

    def record(L):
        """Evidence, history, and what differs from the part-level defaults; codes only (no title, no factor sentences:
        the frontend builds the title and maps codes to text)."""
        t = L["lead_type"]
        prov = {k: v for k, v in (L.get("prov") or {}).items() if prov_by_type.get(t, {}).get(k) != v}
        rec = {"evidence": L.get("evidence") or [], "history": L.get("history") or [], "prov": prov,
               "change_indicators": L.get("change_indicators") or []}  # the adapter reads them per record only
        if t in lawful_varies:
            rec["lawful_explanations"] = L.get("lawful_explanations") or []
        return {k: v for k, v in rec.items() if v not in (None, [], {})}

    shell = {"factors": [{k: v for k, v in fmeta[n].items() if not (k == "source_by_type" and len(set(v.values())) <= 1)}
                         for n in factor_names],
             "lawful_explanations": lawful, "change_indicators": change, "prov_by_type": prov_by_type,
             "note": ("lead_id = <lead_type>-<primary id>; evidence and history in records for the leads that are not new "
                      "and the highest-priority leads, while the part budget allows; lawful_explanations, change_indicators and "
                      "prov per lead type at part level (a record holds only what differs); codes, not sentences (board D5.1); "
                      "the frontend builds the title")}
    if not rows:
        return None, info
    keep = rows
    cols = encode(keep)
    size = E.element_bytes("leads", {**E.part("leads", len(keep), cols, records={}), **shell})
    if size > budget - record_reserve:
        # drop from the end (lowest priority) until the columns fit: sizes are close to linear in the row count
        half = max(1, len(rows) // 2)
        s_half = E.element_bytes("leads", {**E.part("leads", half, encode(rows[:half]), records={}), **shell})
        per = max(1e-9, (size - s_half) / max(1, len(rows) - half))
        n = int(min(len(rows), max(0, half + ((budget - record_reserve) - s_half) / per)))
        while n > 0 and E.element_bytes("leads", {**E.part("leads", n, encode(rows[:n]), records={}), **shell}) > budget - record_reserve:
            n = int(n * 0.995) if n > 200 else n - 1
        keep = rows[:n]
        info["dropped_budget"] = len(rows) - n
        cols = encode(keep) if keep else {}
        size = E.element_bytes("leads", {**E.part("leads", len(keep), cols, records={}), **shell})
    records: dict[str, dict] = {}
    order = [i for i, r in enumerate(keep) if r[0]["state"] != "new"] + [i for i, r in enumerate(keep) if r[0]["state"] == "new"]
    for i in order:
        L = keep[i][0]
        rec = record(L)
        add = len(E.dumps({L["lead_id"]: rec})) + 1
        if size + add > budget:
            break
        records[L["lead_id"]] = rec
        size += add
    info["kept"] = len(keep)
    info["records"] = len(records)
    info["records_top_priority_through"] = None if not records else min(keep[i][0]["priority"] for i in order[:len(records)])
    p = {**E.part("leads", len(keep), cols, records=records), **shell}
    info["primary_ids"] = [(r[1], str(r[0]["primary_id"])) for r in keep]
    info["kept_ids"] = [r[0]["lead_id"] for r in keep]
    return p, info


# ----------------------------------------------------------------------------------------------- passes
SCENE_COUNT_KEYS = ["product_id", "start_utc", "n_contacts", "n_high", "n_medium", "n_fixed", "n_matched", "n_unmatched",
                    "n_no_coverage", "n_dark_leads", "n_ambiguous", "n_cnn_scored", "n_cnn_vessel", "ais_aoi_positions",
                    "ais_aoi_mmsi", "ais_footprint_positions", "ais_footprint_mmsi", "ais_near_footprint_mmsi",
                    "ais_recorded_hours", "status"]
PART_LEVEL_EXTRA = ("plan_note", "stop_note", "plan_generated_utc")
# Pass fields kept even when null (the embedded adapter gives them a default when missing); every other null field and
# research_only false are left out, which the adapter reads as null and false (README reading 15).
PASS_KEEP_NULL = {"pass_id", "mission", "status", "src", "start_utc", "stop_utc", "caveat"}


def compact_pass(r: dict) -> dict:
    """A Pass record without its null fields (except PASS_KEEP_NULL), research_only false and an empty field_prov."""
    out = {}
    for k, v in r.items():
        if k not in PASS_KEEP_NULL and (v is None or (k == "field_prov" and not v)):
            continue
        if k == "research_only" and v is False:
            continue
        out[k] = v
    return out


def _round_geojson(g: dict | None, tol: float = 0.01, nd: int = 3) -> dict | None:
    if not g:
        return g
    from shapely.geometry import mapping, shape

    s = shape(g).simplify(tol, preserve_topology=True)
    if s.is_empty:
        return None

    def rnd(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(float(x), nd) for x in c]
        return [rnd(x) for x in c]
    m = mapping(s)
    return {"type": m["type"], "coordinates": rnd(m["coordinates"])}


# Fields of an AIS-only vessel the Pass page reads (app/frontend/src/views/PassPage.tsx, AisOnly), plus its position.
AIS_ONLY_KEEP = ["mmsi", "vessel_key", "vessel_name", "ship_type", "length_ais_m", "sog_kn", "pred_method", "pred_dt_s",
                 "dist_coast_km", "nearest_object_m", "nearest_object_class", "ambiguous_det_id", "oversized_det_id",
                 "on_tested_sea", "lon", "lat"]


def slim_ais_only(v: dict) -> dict:
    """An AIS-only vessel (contract 1.3.0 Pass `ais_only`) with the fields the page reads, nulls left out, and no
    `vessel_key` when it is `mmsi:<mmsi>` (the page rebuilds it). The identity label is the pass's `identity_label`."""
    out = {k: E.plain(v.get(k)) for k in AIS_ONLY_KEEP if E.plain(v.get(k)) is not None}
    if out.get("vessel_key") == f"mmsi:{out.get('mmsi')}":
        out.pop("vessel_key")
    return out


def add_ais_only(ctx: Ctx, part: dict, budget: int) -> tuple[int, int]:
    """Fill each live pass's `ais_only` list (README reading 15: it may be a subset; `n_ais_only` is the pass total)
    while the part stays within `budget`: passes with the most matched contacts first, and within a pass the page's
    own order (tested sea first, then the longest). Returns (vessels in the page, vessels in total)."""
    st = ctx.store
    feats = [f for f in part["features"] if f["properties"].get("n_ais_only")]
    feats.sort(key=lambda f: -int(((f["properties"].get("n_contacts") or {}).get("matched")) or 0))
    size = E.element_bytes("passes", part)
    total = sum(int(f["properties"]["n_ais_only"]) for f in feats)
    added = 0
    for f in feats:
        pr = f["properties"]
        try:
            full = st.pass_(str(pr["pass_id"])).get("ais_only") or []
        except Exception:  # noqa: BLE001 (a pass the store cannot serve keeps n_ais_only only)
            continue
        rows = sorted(full, key=lambda v: (not bool(v.get("on_tested_sea")), -(E.plain(v.get("length_ais_m")) or 0)))
        size += len(',"ais_only":[]')
        if size > budget:
            break
        keep = []
        for v in rows:
            item = slim_ais_only(v)
            add = len(E.dumps(item)) + 1
            if size + add > budget:
                break
            keep.append(item)
            size += add
        if keep:
            pr["ais_only"] = keep
            added += len(keep)
    return added, total


def passes_part(ctx: Ctx, contacts_dropped_passes: set[str], contacts_in_bundle: dict[str, int],
                budget: int | None = None) -> tuple[dict, dict]:
    """GeoJSON FeatureCollection of the Pass records (README reading 7). Constant notes move to the part, footprints are
    simplified at 0.01 degree, and live passes carry their per-scene counts as `scene_counts` (the fixture's reading)."""
    st = ctx.store
    df = st.data["passes"]
    recs = st.pass_rows(np.arange(len(df))) if len(df) else []
    feats = []
    caveat = recs[0].get("caveat") if recs else st.cav()
    notes: dict[str, str] = {}
    for r in recs:
        fp = _round_geojson(r.pop("footprint", None))
        if r.get("caveat") == caveat:
            r.pop("caveat")
        ex = dict(r.get("extra") or {})
        for k in PART_LEVEL_EXTRA:
            if k in ex:
                notes.setdefault(k, ex.pop(k))
        sc = ex.pop("scene_counts", None)
        if sc:
            r["scene_counts"] = [{k: E.plain(x.get(k)) for k in SCENE_COUNT_KEYS if k in x} for x in sc]
        ex.pop("summary", None)  # data/live/live_summary.json: the local app shows it
        r["extra"] = ex
        if r.get("processed"):
            r["contacts_in_bundle"] = r["pass_id"] not in contacts_dropped_passes and contacts_in_bundle.get(r["pass_id"], 0) > 0
            if r["pass_id"] in contacts_dropped_passes:
                r["bundle_note"] = "contacts of this pass are in the local app (left out of this page to meet its size budget)"
        feats.append({"type": "Feature", "geometry": fp, "properties": compact_pass(r)})
    part = {"type": "FeatureCollection", "n": len(feats), "features": feats, "caveat": caveat, "notes": notes,
            "note": ("pass_id = pass_group (plan), run_id (live), mission plus first scene start (regional); footprints "
                     "simplified at 0.01 degree for display; every pass's caveat is the part's caveat; constant extra notes "
                     "are in `notes`; null fields and research_only false are left out (read as null and false); ais_only "
                     "may be a subset (tested sea first, then the longest; n_ais_only is the pass total, the local app has "
                     "every vessel), its items carry the fields the Pass page reads and the pass's identity_label applies")}
    added, total = add_ais_only(ctx, part, budget) if budget else (0, int(sum((f["properties"].get("n_ais_only") or 0) for f in feats)))
    info = {"passes": len(feats), "live": sum(1 for f in feats if str(f["properties"]["pass_id"]).startswith("live_")),
            "ais_only_in_bundle": int(added), "ais_only_total": int(total)}
    return part, info


# ----------------------------------------------------------------------------------------------- cells
CELL_SCALES = {"aoi_share": 100, "n_sea": 1, "sea_share": 100, "sea_area_km2": 1, "depth_mean_m": 1, "depth_median_m": 1,
               "depth_min_m": 1, "depth_max_m": 1, "depth_std_m": 1, "share_shallower_50m": 100, "share_shallower_200m": 100,
               "share_shelf_break_150_250m": 100, "slope_mean_m_per_km": 1, "dist_coast_km": 1, "dist_coast_min_km": 1,
               "dist_port_km": 1, "dist_port_min_km": 1, "ais_reach_share": 100, "ais_reach_mmsi": 1,
               "look_prob_1d": 1, "look_prob_7d": 1, "look_prob_30d": 1, "passes_90d": 1}  # look_prob in percent
NIGHT_SCALES = {"sst_mean_c": 100, "sst_sd_c": 100, "sst_grad_mean": 1000, "front_share": 100, "dist_front_km": 1,
                "chl_log10_mean": 100, "chl_valid_share": 100, "ssh_m": 100, "ssh_anom_m": 100, "ssh_grad": 1000000,
                "current_speed_ms": 100, "mld_m": 1, "sbl_m": 1, "wave_hs_m": 10, "wind_ms": 10, "moon_illum_pct": 1}


def cell_frame(ctx: Ctx) -> tuple[list[dict], str | None]:
    st = ctx.store

    def build():
        cd = st.data["cells"]
        if cd.static is None:
            return [], None
        from scs_api.envelope import ApiError

        ids = [f"r{int(r)}c{int(c)}" for r, c in zip(cd.static["row"], cd.static["col"])]
        night = None
        if cd.daily is not None and len(cd.daily):
            night = str(sorted(cd.daily["night"].astype(str).unique())[-1])
        recs = []
        for i in ids:
            try:
                recs.append(st.cell(i, night=night))
            except ApiError:  # this cell has no row for the newest night: static fields only
                r = st.cell(i)
                r["nightly"] = None
                recs.append(r)
        return recs, night
    deps = ["cells_static", "cells_daily", "cells_pass", "ocean_static_summary", "rasters", "radar_vs_gfw"]
    return cached(ctx, "cells", [d for d in deps if d in ctx.cat.specs], build)


def expected_summary(ctx: Ctx) -> tuple[dict | None, dict]:
    """Per cell and target: tested nights, flagged and robust-flagged nights, the newest tested night's observed,
    expected and z (board D5.4 rows summarised; the local app serves every row)."""
    df = ctx.cat.read_parquet("expected_activity", ["target", "night", "row", "col", "tested", "observed", "expected", "z",
                                                     "flag", "flag_robust", "calm"])
    if df is None or not len(df):
        return None, {"expected_activity": "missing"}
    t = df[df["tested"].astype(bool)]
    out = {}
    for target, g in t.groupby("target"):
        g = g.sort_values("night")
        agg = g.groupby(["row", "col"]).agg(n_tested=("night", "size"), n_flag=("flag", lambda s: int(np.sum(s.astype(bool)))),
                                            n_flag_robust=("flag_robust", lambda s: int(np.sum(s.astype(bool)))))
        last = g.groupby(["row", "col"]).tail(1).set_index(["row", "col"])
        agg = agg.join(last[["night", "observed", "expected", "z"]])
        out[str(target)] = agg
    return out, {"expected_activity": "summary", "targets": list(out)}


def cells_part(ctx: Ctx, recs: list[dict], night: str | None, with_expected: bool | str) -> tuple[dict | None, dict]:
    """with_expected: True (summary with the newest tested night), "counts" (tested and flagged night counts only) or
    False (no expected-activity block)."""
    if not recs:
        return None, {"cells": 0}
    fr = pd.DataFrame.from_records([{k: v for k, v in r.items() if k not in ("nightly", "eez", "extra", "prov", "pass_context",
                                                                             "object_context", "expected_activity", "gfw_comparison",
                                                                             "nights_available")} for r in recs])
    cols = {"cell_id": E.strs(fr["cell_id"]), "row": E.num(fr["row"], 1, allow_dict=False), "col": E.num(fr["col"], 1, allow_dict=False),
            "lon": E.num(fr["lon"], 8, allow_dict=False), "lat": E.num(fr["lat"], 8, allow_dict=False),
            "region_box": E.dict_col(fr["region_box"])}
    from scs_api.models import CELL_STATIC

    for c in CELL_STATIC + ["ais_reach_share", "ais_reach_mmsi", "look_prob_1d", "look_prob_7d", "look_prob_30d", "passes_90d"]:
        if c in fr:
            if fr[c].dropna().map(lambda v: isinstance(v, bool)).all() and fr[c].notna().any():
                cols[c] = encode_col(obj_series(fr[c]), "bool")
            else:
                cols[c] = E.num(obj_series(fr[c]), CELL_SCALES.get(c, 100))
    for c in ("shipping_note", "src"):
        if c in fr and fr[c].nunique(dropna=False) == 1:
            cols[c] = E.const(fr[c].iloc[0])
    cols["research_only"] = E.const(False)
    cols["caveat"] = E.const(recs[0].get("caveat"))
    info = {"cells": len(recs), "night": night}
    p = E.part("cells", len(recs), cols, records={}, prov=dict(recs[0].get("prov") or {}),
               note="static sea fields per 0.25 degree cell; nightly fields of the newest night in `nightly`; Marine Regions "
                    "attributes only in `eez_attrs`, shown only while the EEZ layer is on")
    # newest night of the daily fields
    nk = [r.get("nightly") or {} for r in recs]
    if night and any(nk):
        nf = pd.DataFrame.from_records(nk)
        ncols = {}
        for c in nf.columns:
            if c in NIGHT_SCALES:
                ncols[c] = E.num(obj_series(nf[c]), NIGHT_SCALES[c])
            elif c == "night":
                continue
            elif nf[c].dropna().map(lambda v: isinstance(v, bool)).all() and nf[c].notna().any():
                ncols[c] = encode_col(obj_series(nf[c]), "bool")
            else:
                ncols[c] = encode_col(obj_series(nf[c]), "dict")
        valid = {}
        for c in list(ncols):
            vals = [v for v in obj_series(nf[c]) if v is not None]
            if len(set(map(str, vals))) == 1:
                valid[c] = vals[0]
                ncols.pop(c)
        p["nightly"] = {"night": night, "n": len(recs), "columns": ncols, "valid": valid,
                        "note": ("valid times and sources per field are in the *_date, *_valid_utc, sst_source and chl_dataset "
                                 "columns; a field with one value wherever it is present is in `valid` instead")}
    # EEZ attributes (contract 3.7): separate block, as published
    ez = [r.get("eez") or {} for r in recs]
    if any(ez):
        ef = pd.DataFrame.from_records(ez)
        ecols = {}
        for c in ef.columns:
            if c in ("heading", "statement"):
                continue
            if c in ("marineregions_share", "marineregions_overlap_share"):
                ecols[c] = E.num(obj_series(ef[c]), 100)
            elif c in ("marineregions_mrgid", "marineregions_n"):
                ecols[c] = E.num(obj_series(ef[c]), 1)
            else:
                ecols[c] = encode_col(obj_series(ef[c]), "dict")
        p["eez_attrs"] = {"heading": ez[0].get("heading"), "statement": ez[0].get("statement"), "n": len(recs),
                          "columns": ecols, "src": "marineregions_v12",
                          "note": "As published by Marine Regions; shown only while the EEZ layer is on; never a filter, factor or feature"}
    if with_expected:
        es, einfo = expected_summary(ctx)
        info.update(einfo)
        if es:
            blk = {}
            keys = list(zip(fr["row"].astype(int), fr["col"].astype(int)))
            for target, agg in es.items():
                a = agg.reindex(keys)
                blk[target] = {"n_tested": E.num(a["n_tested"], 1), "n_flag": E.num(a["n_flag"], 1),
                               "n_flag_robust": E.num(a["n_flag_robust"], 1)}
                if with_expected is True:
                    blk[target].update({"last_night": encode_col([None if pd.isna(v) else str(v) for v in a["night"]], "dict"),
                                        "last_observed": E.num(a["observed"], 1), "last_expected": E.num(a["expected"], 100),
                                        "last_z": E.num(a["z"], 10)})
            info["expected_activity"] = "summary" if with_expected is True else "counts only"
            try:
                from darkvessel.ocean.grid import OCEAN_CAVEAT
            except Exception:  # noqa: BLE001
                OCEAN_CAVEAT = None
            p["expected_activity"] = {"n": len(recs), "targets": blk, "caveat": OCEAN_CAVEAT, "form": info["expected_activity"],
                                      "note": ("summary of data/expected_activity.parquet per cell: tested nights, flagged and "
                                               "robust-flagged nights, and the newest tested night; every row in the local app")}
    return p, info


# ----------------------------------------------------------------------------------------------- geo
def geo_part(ctx: Ctx) -> tuple[dict, dict]:
    from shapely.geometry import shape

    def build():
        g = ctx.store.geo
        layers, info = {}, {}
        spec = {  # name: (kind, extra simplification tolerance, props)
            "land": ("polygon", 0.0, []), "aoi": ("polygon", 0.0, ["label"]), "reporting_boxes": ("polygon", 0.0, ["name"]),
            "eez_boundaries": ("line", 0.005, ["line_name", "line_type", "length_km"]),
            "depth_contours": ("line", 0.01, ["depth_m", "depth", "level"]), "ports": ("point", 0.0, ["name", "port_name", "size", "source"]),
            "fronts": ("line", 0.01, ["night", "length_km", "grad_mean"]),
            "footprints": ("polygon", 0.02, ["mission", "start_utc"]),
        }
        for name, (kind, tol, props) in spec.items():
            fc = g.layer(name)
            if fc is None:
                info[name] = 0
                continue
            geoms, rows = [], []
            for f in fc["features"]:
                if not f.get("geometry"):
                    continue
                s = shape(f["geometry"])
                if tol:
                    s = s.simplify(tol, preserve_topology=True)
                if s.is_empty:
                    continue
                if kind == "point" and s.geom_type != "Point":
                    s = s.representative_point()
                geoms.append(s)
                rows.append(f.get("properties") or {})
            pr = {}
            for p in props:
                vals = [r.get(p) for r in rows]
                if any(v is not None for v in vals):
                    if p == "start_utc":
                        pr[p] = E.time_col(vals)
                    elif all(isinstance(v, (int, float)) or v is None for v in vals):
                        pr[p] = E.num(vals, 10)
                    else:
                        pr[p] = E.dict_col([None if v is None else str(v) for v in vals]) if len({str(v) for v in vals}) < 255 else E.strs(vals)
            note = fc.get("note") or ""
            if name not in ("reporting_boxes",):
                note = (note + " " + SIMPLIFIED).strip()
            layers[name] = E.geom(geoms, kind, props=pr or None, note=note)
            info[name] = len(geoms)
        return layers, info
    deps = ["land", "aoi", "eez_boundaries", "depth_contours", "ports", "fronts", "footprints"]
    layers, info = cached(ctx, "geo", [d for d in deps if d in ctx.cat.specs], build)
    eez_statement = ("Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY "
                     "4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. This "
                     "product takes no position on any boundary or claim.")
    eez_disclaimer = ("VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning "
                      "its delimitation, frontier or borders. The data has no legal value whatsoever.")
    return {"type": "geo", "layers": layers, "eez_statement": eez_statement, "eez_disclaimer": eez_disclaimer,
            "eez_note": "EEZ polygons are not embedded; the map draws the published boundary lines with no fill (local app serves both)"}, info


# ----------------------------------------------------------------------------------------------- rasters
RASTER_NAMES = ["depth_m", "sst_mean_c", "front_freq", "chl_mean_mg_m3", "current_speed_mean_ms", "wave_hs_mean_m",
                "ship_density_all"]
RASTER_RES = 0.05


def _resampled(path: Path, name: str, presence: bool):
    """The band on a 0.05 degree grid: block mean (presence: block maximum) of a finer grid, as is when coarser."""
    import rasterio

    with rasterio.open(path) as ds:
        b = ds.bounds
        a = ds.read(1, masked=True).astype(float).filled(np.nan)
        nd = ds.nodata
        if nd is not None:
            a[a == nd] = np.nan
        f = int(round(RASTER_RES / ds.res[0]))
        if f > 1 and abs(RASTER_RES / ds.res[0] - f) < 1e-6:
            h, w = a.shape[0] // f, a.shape[1] // f
            blk = a[: h * f, : w * f].reshape(h, f, w, f)
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                a = np.nanmax(blk, axis=(1, 3)) if presence else np.nanmean(blk, axis=(1, 3))
            right, bottom = b.left + w * f * ds.res[0], b.top - h * f * ds.res[1]
            return a, [b.left, bottom, right, b.top]
        return a, [b.left, b.bottom, b.right, b.top]


def raster_entry(ctx: Ctx, rs, name: str) -> dict | None:
    import matplotlib
    from PIL import Image

    from scs_api.loaders.rasters import cmap_of, is_presence

    if name not in rs.paths:
        return None
    e = rs.entry(name, ctx.store.cav())
    presence = is_presence(name)
    a, bounds = _resampled(ctx.cat.guard(rs.paths[name]), name, presence)
    ok = np.isfinite(a)
    if presence:
        z = np.where(ok & (a > 0), 1.0, 0.0)
        ok = ok & (a > 0)
        vmin, vmax = 0.0, 1.0
    else:
        vmin, vmax = e["vmin"], e["vmax"]
        span = (vmax - vmin) if vmin is not None and vmax is not None and vmax > vmin else 1.0
        z = np.clip((np.nan_to_num(a, nan=vmin or 0.0) - (vmin or 0.0)) / span, 0, 1)
    rgba = (matplotlib.colormaps[cmap_of(name)](z) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(ok, 210, 0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=80, method=6)
    import base64

    out = {k: e.get(k) for k in ("name", "unit", "valid_period", "colormap", "src", "licence", "default_on", "note",
                                 "research_only")}
    out.update({"bounds": [round(x, 6) for x in bounds], "vmin": vmin, "vmax": vmax, "resolution_deg": RASTER_RES,
                "width": int(a.shape[1]), "height": int(a.shape[0]),
                "image": "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")})
    if e.get("research_only"):
        out["label"] = "Research build, noncommercial, CC BY-NC 4.0. Powered by Global Fishing Watch."
    if presence:
        out["note"] = (out.get("note") or "") + " Drawn as a 0/1 presence mask (any published value above 0 in the 0.05 degree cell)."
    return out


def rasters_part(ctx: Ctx) -> tuple[dict, dict]:
    rs = ctx.store.data["rasters"]
    names = list(RASTER_NAMES)
    if ctx.research:
        names += sorted(n for n in rs.paths if n.startswith("gfw_"))

    def build():
        out = []
        for n in names:
            try:
                e = raster_entry(ctx, rs, n)
            except Exception as exc:  # noqa: BLE001 (one raster failing is reported, not fatal)
                ctx.log(f"  raster {n}: {type(exc).__name__}: {exc}")
                e = None
            if e is not None:
                out.append(e)
        return out
    layers = cached(ctx, "rasters", ["rasters", "rasters_research"] if ctx.research else ["rasters"], build,
                    extra=",".join(names))
    try:
        from darkvessel.ocean.grid import OCEAN_CAVEAT
    except Exception:  # noqa: BLE001
        OCEAN_CAVEAT = None
    return ({"type": "rasters", "n": len(layers), "layers": layers, "caveat": OCEAN_CAVEAT,
             "note": "WebP overlays at 0.05 degree, colour-mapped with the registry's vmin and vmax; off by default"},
            {"rasters": [x["name"] for x in layers], "missing": [n for n in names if n not in {x["name"] for x in layers}]})


# ----------------------------------------------------------------------------------------------- camau
CAMAU_TIF = "outputs/small/sigma0_vv_db_utm48n_40m_u8.tif"
CAMAU_WIDTH = 2562  # contract 6.3: 2,562 x 2,600 px (about 71 m pixels); drop rule 3 halves it


def camau_part(ctx: Ctx, half: bool) -> tuple[dict | None, dict]:
    """Ca Mau radar backdrop (8-bit VV dB, 40 m, UTM 48N) warped to EPSG:4326 for the map, WebP q75 (contract 6.2)."""
    path = ctx.cat.guard(ctx.cat.data_dir / CAMAU_TIF)
    if not path.exists():
        return None, {"camau": "missing"}

    def build(half_):
        import base64

        import rasterio
        from PIL import Image
        from rasterio.warp import Resampling, calculate_default_transform, reproject

        with rasterio.open(path) as ds:
            width = CAMAU_WIDTH // 2 if half_ else CAMAU_WIDTH
            height = round(ds.height * width / ds.width)
            src = ds.read(1, out_shape=(height, width), resampling=Resampling.average)
            src_tr = ds.transform * ds.transform.scale(ds.width / width, ds.height / height)
            dst_crs = "EPSG:4326"
            tr, w, h = calculate_default_transform(ds.crs, dst_crs, width, height, *ds.bounds)
            dst = np.zeros((h, w), np.uint8)
            reproject(src, dst, src_transform=src_tr, src_crs=ds.crs, dst_transform=tr, dst_crs=dst_crs,
                      resampling=Resampling.nearest, src_nodata=ds.nodata, dst_nodata=0)
            utm = {"crs": str(ds.crs), "transform": list(ds.transform)[:6], "width": ds.width, "height": ds.height,
                   "nodata": ds.nodata, "tags": {k: v for k, v in ds.tags().items() if len(str(v)) < 400}}
        buf = io.BytesIO()
        Image.fromarray(dst, "L").save(buf, "WEBP", quality=75, method=6)
        bounds = [tr.c, tr.f + h * tr.e, tr.c + w * tr.a, tr.f]
        return {"image": "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
                "width": int(w), "height": int(h), "bounds": [round(x, 7) for x in bounds], "crs": "EPSG:4326",
                "transform": list(tr)[:6], "scene_grid": utm}
    blk = cached(ctx, "camau", [], lambda: build(half), extra=f"half={half}|{path.stat().st_mtime_ns}")
    p = {"type": "camau", "backdrop": blk, "half_resolution": bool(half), "src": "s1_grd", "scene_src": "det_camau",
         "note": ("Ca Mau detail scene, Sentinel-1D 2026-09-29: VV sigma0 in dB (8-bit), averaged from 40 m to about 71 m "
                  "(142 m at half resolution) and warped from UTM 48N "
                  "(EPSG:32648) to EPSG:4326 for display; its 6,005 objects are rows of the contacts part with view camau "
                  "(low objects included, shown only in the Ca Mau view)")}
    return p, {"camau_width": blk["width"], "camau_height": blk["height"], "half": bool(half)}


# ----------------------------------------------------------------------------------------------- events
def events_part(ctx: Ctx, contacts_fr: pd.DataFrame) -> tuple[dict, dict]:
    st = ctx.store
    ed = st.data["events"]
    df = ed.df
    if not ctx.research:
        recs = st.event_rows(np.arange(len(df))) if len(df) else []
        return ({"type": "events", "n": len(recs), "records": recs,
                 "note": "open build: aisstream-derived events (data/events_open.gpkg); not built yet" if not recs else
                 "open build: aisstream-derived events"}, {"events": len(recs)})
    code = df["code"].astype(str).to_numpy()
    info = {}
    gaps = np.flatnonzero(code == "E8")
    recs = st.event_rows(gaps)
    info["gaps"] = len(recs)
    # encounters within 10 km and 24 h of a September contact (the rule behind n_gfw_encounters_10km_24h)
    enc = df[code == "E10"].reset_index(drop=True)
    sept = contacts_fr[contacts_fr["_source"].isin(["research", "regional"])]  # September contacts, not structures
    sel = _near_contacts(enc, sept, 10.0, 24.0) if len(enc) else np.zeros(0, bool)
    sub = enc[sel]
    other = enc[~sel]
    info["encounters_rows"], info["encounters_in_cells"] = int(len(sub)), int(len(other))
    cols = {}
    if len(sub):
        cols = {"event_id": E.strs(sub["event_id"]), "start_utc": E.time_col(sub["start"]), "end_utc": E.time_col(sub["end"]),
                "duration_h": E.num(sub.get("duration_h"), 100), "lon": E.num(sub["lon"], 100000), "lat": E.num(sub["lat"], 100000),
                "vessel_key": E.strs(["gfw:" + str(v) if E.plain(v) is not None else None for v in sub["vessel_id"]]),
                "mmsi": E.strs([E.plain(v) for v in sub["ssvid"]]), "name": E.strs(sub.get("vessel_name")),
                "flag": E.dict_col(obj_series(sub.get("flag", pd.Series([None] * len(sub))))),
                "encounter_vessel_key": E.strs(["gfw:" + str(v) if E.plain(v) is not None else None
                                                for v in sub.get("encounter_vessel_id", pd.Series([None] * len(sub)))]),
                "encounter_mmsi": E.strs([E.plain(v) for v in sub.get("encounter_ssvid", pd.Series([None] * len(sub)))]),
                "encounter_name": E.strs(sub.get("encounter_vessel_name", pd.Series([None] * len(sub)))),
                "encounter_flag": E.dict_col(obj_series(sub.get("encounter_vessel_flag", pd.Series([None] * len(sub))))),
                "code": E.const("E10"), "event_type": E.const("ENCOUNTER"), "source": E.const("gfw"),
                "research_only": E.const(True), "src": E.const("gfw_events")}
    from scs_api.loaders.events import RULE_TEXT, TYPE_CAVEAT

    def cells(frame, res=0.1, hours=True):
        if not len(frame):
            return None
        cx = np.floor(frame["lon"].to_numpy(float) / res).astype(int)
        cy = np.floor(frame["lat"].to_numpy(float) / res).astype(int)
        g = pd.DataFrame({"cx": cx, "cy": cy, "h": pd.to_numeric(frame.get("duration_h"), errors="coerce")})
        a = g.groupby(["cx", "cy"]).agg(n=("h", "size"), hours=("h", "sum")).reset_index()
        out = {"res_deg": res, "n": int(len(a)), "columns": {
            "lon": E.num((a["cx"] + 0.5) * res, 100, allow_dict=False), "lat": E.num((a["cy"] + 0.5) * res, 100, allow_dict=False),
            "n_events": E.num(a["n"], 1)}}
        if hours:
            out["columns"]["hours"] = E.num(a["hours"].round(1), 10)
        return out

    enc_cells = cells(other)
    info["encounter_cells"] = int(enc_cells["n"]) if enc_cells else 0
    loit = df[code == "E11"]
    loit_cells = cells(loit)
    info["loitering_events"], info["loitering_cells"] = int(len(loit)), int(loit_cells["n"]) if loit_cells else 0
    pv = df[code == "E14"]
    anch = None
    if len(pv):
        key = pv["port_id"].astype(str) if "port_id" in pv else pv.get("port_name", pd.Series(["?"] * len(pv))).astype(str)
        a = pv.assign(_k=key.to_numpy()).groupby("_k").agg(lon=("lon", "mean"), lat=("lat", "mean"), n=("lon", "size"),
                                                            name=("port_name", "first") if "port_name" in pv else ("lon", "size"),
                                                            flag=("port_flag", "first") if "port_flag" in pv else ("lon", "size"))
        anch = {"n": int(len(a)), "columns": {"lon": E.num(a["lon"], 10000), "lat": E.num(a["lat"], 10000),
                                             "n_visits": E.num(a["n"], 1), "port_name": E.strs(a["name"]),
                                             "port_flag": E.dict_col(obj_series(a["flag"]))}}
        info["port_visits"], info["anchorages"] = int(len(pv)), int(len(a))
    caveats = {c: st.cav(TYPE_CAVEAT.get(c)) for c in ("E8", "E10", "E11", "E14")}
    p = {"type": "events", "n": len(recs), "records": recs,
         "encounters": {"n": int(len(sub)), "columns": cols, "rule_text": RULE_TEXT["E10"], "caveat": caveats["E10"],
                        "selection": "encounters within 10 km of a September contact whose time span, widened by 24 h, holds "
                                     "the contact time (the rule behind n_gfw_encounters_10km_24h, over every September contact)"},
         "encounter_cells": enc_cells, "loitering_cells": loit_cells, "port_anchorages": anch,
         "rule_text": {k: RULE_TEXT[k] for k in RULE_TEXT}, "caveats": caveats,
         "note": ("research build: the 5 GFW gaps as records, encounters near September contacts as rows, other encounters and "
                  "loitering as counts and hours per 0.1 degree cell, port visits per anchorage; every event in the local app. "
                  "Events as published by Global Fishing Watch; an AIS gap is not proof of intent.")}
    return p, info


def _near_contacts(events: pd.DataFrame, contacts: pd.DataFrame, max_km: float, max_h: float) -> np.ndarray:
    """Events with at least one contact within max_km whose time lies in the event span widened by max_h hours."""
    from scipy.spatial import cKDTree

    if not len(events) or not len(contacts):
        return np.zeros(len(events), bool)

    def unit(lon, lat):
        lo, la = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
        return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])

    chord = 2 * np.sin(max_km * 1000 / 6371008.8 / 2)
    tree = cKDTree(unit(contacts["lon"], contacts["lat"]))
    ct = pd.to_datetime(contacts["acq_utc"], utc=True, format="ISO8601").astype("int64").to_numpy() // 10**9
    t0 = (pd.to_datetime(events["start"], utc=True, format="ISO8601") - pd.Timedelta(hours=max_h)).astype("int64").to_numpy() // 10**9
    t1 = (pd.to_datetime(events["end"], utc=True, format="ISO8601") + pd.Timedelta(hours=max_h)).astype("int64").to_numpy() // 10**9
    hits = tree.query_ball_point(unit(events["lon"], events["lat"]), chord)
    out = np.zeros(len(events), bool)
    for k, h in enumerate(hits):
        if h:
            tt = ct[np.asarray(h)]
            out[k] = bool(((tt >= t0[k]) & (tt <= t1[k])).any())
    return out
