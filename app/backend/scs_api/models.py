"""Pydantic models of app/CONTRACT.md section 3 and the section 5 envelope. Field names are the contract's, in its order.

The Contact model is built per build: the research model has the 32 D1 fields of `darkvessel.ais.gfw_identity`, the
open model the 31 of `darkvessel.live.schema` (no `gfw_vessel_id`) and none of the GFW evidence fields, so no `gfw_`
name reaches the open build's OpenAPI document or its responses. Routes return prebuilt JSON (records.py builds each
record from these field lists); the models generate `/openapi.json` and the tests validate responses against them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model

AisStatus = Literal["matched", "unmatched", "no_coverage", "not_checked"]
AIS_STATUS_VALUES = ("matched", "unmatched", "no_coverage", "not_checked")
Confidence = Literal["high", "medium", "fixed", "low"]
LeadState = Literal["new", "reviewing", "closed_explained", "closed_unexplained", "closed_false_alarm"]
LEAD_STATES = ("new", "reviewing", "closed_explained", "closed_unexplained", "closed_false_alarm")
Num = Optional[float]
Str = Optional[str]
Int = Optional[int]
Bool = Optional[bool]
Time = Optional[datetime]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------- Contact (3.1)
D1_SPEC = [
    ("det_id", str), ("run_id", str), ("mission", Literal["S1C", "S1D"]), ("acq_utc", datetime), ("lon", float), ("lat", float),
    ("length_est_m", Num), ("confidence", Confidence), ("cnn_score", Num), ("cnn_vessel", Bool),
    ("ais_status", AisStatus), ("ais_source", Optional[Literal["aisstream", "gfw"]]), ("match_method", Str), ("match_dist_m", Num),
    ("match_dt_s", Num), ("match_quality", Optional[Literal["high", "medium", "low"]]),
    ("mmsi", Str), ("imo", Str), ("vessel_name", Str), ("call_sign", Str), ("flag", Str), ("ship_type", Str), ("length_ais_m", Num),
    ("identity_source", Str), ("gfw_vessel_id", Str),
    ("nearest_ais_mmsi", Str), ("nearest_ais_dist_m", Num), ("nearest_ais_dt_s", Num), ("n_ais_10km", Int), ("ais_reach", Num),
    ("research_only", bool), ("caveat", str),
]
CONTACT_EXT_SPEC = [
    ("view", Literal["regional", "live", "camau"]), ("dark_lead", Bool), ("vessel_key", Str), ("nearest_vessel_key", Str),
    ("scene_id", Str), ("pass_id", Str), ("pass_dir", Str), ("orbit_rel", Int), ("inc_angle_deg", Num), ("pol_class", Str),
    ("n_pixels", Int), ("scr_vv_db", Num), ("scr_vh_db", Num), ("low_reason", Str), ("persist_dates", Int),
    ("persist_dates_checked", Int), ("n_low_1km", Int), ("near_fixed_m", Num), ("match_gate_m", Num), ("ais_sog_kn", Num),
    ("length_ratio", Num), ("ais_class", Str), ("mmsi_mid", Int), ("ais_footprint_positions", Int), ("pred_method", Str),
    ("cnn_chip_valid_frac", Num), ("ais_recorded_hours", Int), ("row", Num), ("col", Num),
]
# Research-only evidence columns (darkvessel.ais.gfw_identity.EVIDENCE_COLUMNS without pass_id, which is above).
CONTACT_RESEARCH_SPEC = [
    ("identity_kind", Str), ("gfw_sar_pair", Str), ("gfw_sar_n_cand", Int), ("gfw_sar_n_rivals", Int),
    ("gfw_sar_ambiguous_cell", Bool), ("gfw_geartype", Str), ("gfw_neural_type", Str), ("pres_speed_kmh", Num),
    ("pres_n_cells", Int), ("pres_n_cand", Int), ("n_gear_10km", Int), ("ais_presence_h_day", Num),
    ("ais_presence_h_window", Num), ("nearest_ais_vessel_id", Str), ("nearest_ais_name", Str), ("n_gfw_gaps_50km_24h", Int),
    ("nearest_gfw_gap_km", Num), ("n_gfw_encounters_10km_24h", Int), ("n_gfw_loitering_10km_24h", Int),
]
CONTACT_TAIL_SPEC = [
    ("cnn_threshold", Num), ("cnn_model_id", Str), ("cnn_score_source", Str), ("bg_vv_db", Num), ("bg_vh_db", Num),
    ("wind_ms", Num), ("ctt_k", Num), ("deep_convection", Bool), ("optical_object", Bool), ("optical_kind", Str),
    ("s2_item", Str), ("satlas_m", Num), ("cell_id", Str), ("lead_ids", list[str]), ("chip", Str),
    ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any]),
]
SUMMARY_EXT = ["view", "pass_id", "lead_ids"]


def _model(name: str, spec, doc: str = "") -> type[Record]:
    fields = {}
    for fname, ftype in spec:
        default = ... if not _nullable(ftype) else None
        kind = _container(ftype)
        if kind is not None:
            default = Field(default_factory=kind)
        if fname == "class":
            fields["class_"] = (ftype, Field(default, alias="class"))
            continue
        fields[fname] = (ftype, default)
    m = create_model(name, __base__=Record, **fields)
    m.__doc__ = doc or name
    return m


def _nullable(t) -> bool:
    return getattr(t, "__origin__", None) is not None and type(None) in getattr(t, "__args__", ()) or t is Any


def _container(t):
    """list or dict for a (non-optional) container annotation, else None."""
    origin = getattr(t, "__origin__", None)
    return origin if origin in (list, dict) else None


def container_defaults(model: type[BaseModel]) -> dict:
    """Fields of a model whose null value is an empty list or map (record building fills them)."""
    out = {}
    for n, f in model.model_fields.items():
        kind = _container(f.annotation)
        if kind is not None:
            out[f.alias or n] = kind
    return out


def int_fields(model: type[BaseModel]) -> set:
    """Fields typed int or Optional[int] (a float column with nulls is written back as integers)."""
    out = set()
    for n, f in model.model_fields.items():
        t = f.annotation
        if t is int or (getattr(t, "__args__", None) and int in t.__args__ and float not in t.__args__ and bool not in t.__args__):
            out.add(f.alias or n)
    return out


def field_names(model: type[BaseModel]) -> list[str]:
    """Contract names of a model's fields, in order (aliases where a name is a Python keyword)."""
    return [f.alias or n for n, f in model.model_fields.items()]


def d1_spec(build: str):
    return [s for s in D1_SPEC if build == "research" or s[0] != "gfw_vessel_id"]


def contact_spec(build: str):
    return d1_spec(build) + CONTACT_EXT_SPEC + (CONTACT_RESEARCH_SPEC if build == "research" else []) + CONTACT_TAIL_SPEC


def contact_summary_spec(build: str):
    ext = dict(CONTACT_EXT_SPEC + CONTACT_TAIL_SPEC)
    return d1_spec(build) + [(n, ext[n]) for n in SUMMARY_EXT]


# ---------------------------------------------------------------- Vessel (3.2)
VESSEL_SPEC = [
    ("vessel_key", str), ("mmsi", Str), ("mid", Int), ("flag", Str), ("name", Str), ("call_sign", Str), ("imo", Str),
    ("ais_class", Optional[Literal["A", "B"]]), ("ship_type", Str), ("gear_type", Str),
    ("identity_kind", Optional[Literal["vessel", "gear", "unknown"]]), ("length_m", Num), ("width_m", Num),
    ("length_ais_m", Num), ("tonnage_gt", Num), ("identity_source", Str), ("destination", Str), ("eta", Str),
    ("first_seen_utc", Time), ("last_seen_utc", Time), ("static_seen_utc", Time), ("n_positions", Int), ("n_messages", Int),
    ("last_lon", Num), ("last_lat", Num), ("sog_kn", Num), ("cog_deg", Num), ("heading", Num), ("nav_status_label", Str),
    ("in_aoi", Bool), ("ever_in_aoi", Bool), ("gear_beacon_like", Bool), ("registry_sources", Str), ("registry_records", Int),
    ("dataset_version", Str), ("stub", bool), ("contacts_matched", list[str]), ("identity_note", str),
    ("research_only", bool), ("caveat", str), ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any]),
]
VESSEL_SUMMARY = ["vessel_key", "mmsi", "flag", "name", "call_sign", "imo", "ais_class", "ship_type", "identity_kind",
                  "length_m", "last_seen_utc", "last_lon", "last_lat", "in_aoi", "stub", "research_only", "caveat", "src"]


class TrackGap(Record):
    from_utc: datetime
    to_utc: datetime
    minutes: float
    note: str
    overlaps_recorder_gap: bool = False


# ---------------------------------------------------------------- Light and site (3.3)
LIGHT_SPEC = [
    ("light_id", str), ("satellite", Literal["S-NPP", "NOAA-20", "NOAA-21"]), ("time_utc", datetime), ("night", str),
    ("lon", float), ("lat", float), ("radiance_nw", float), ("spike_nw", Num), ("isolation", Num),
    ("quality", Literal["clear", "under_cloud"]), ("class", str), ("nights_seen_500m", int), ("clear_nights_cell", int),
    ("moon_illum_pct", Num), ("satlas_infra_m", Num), ("s1_passes_90d", int), ("site_id", Str),
    ("contacts_2km_same_night", list[str]), ("cell_id", Str), ("research_only", bool), ("caveat", str), ("src", str),
    ("prov", dict[str, str]), ("extra", dict[str, Any]),
]
LIGHT_SUMMARY = ["light_id", "satellite", "time_utc", "night", "lon", "lat", "radiance_nw", "quality", "class",
                 "nights_seen_500m", "s1_passes_90d", "site_id", "cell_id", "research_only", "caveat", "src"]
SITE_SPEC = [
    ("site_id", str), ("lon", float), ("lat", float), ("n_lights", Int), ("nights", Int), ("radiance_med_nw", Num),
    ("radiance_max_nw", Num), ("satlas_infra_m", Num), ("nights_seen_max", Int), ("s1_passes_90d", Int), ("likely", Str),
    ("research_only", bool), ("caveat", str), ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any]),
]

# ---------------------------------------------------------------- Event (3.4)
EVENT_SPEC = [
    ("event_id", str), ("code", Literal["E6", "E7", "E8", "E9", "E10", "E11", "E12", "E13", "E14", "E15", "E16"]),
    ("event_type", str), ("start_utc", datetime), ("end_utc", Time), ("duration_h", Num), ("lon", float), ("lat", float),
    ("geometry", Optional[dict[str, Any]]), ("mmsi", list[str]), ("vessel_keys", list[str]), ("det_ids", list[str]),
    ("light_ids", list[str]), ("cell_ids", list[str]), ("params", dict[str, Any]), ("rule_text", str), ("grade", Str),
    ("source", Literal["aisstream", "gfw", "app"]), ("research_only", bool), ("caveat", str), ("src", str),
    ("prov", dict[str, str]), ("extra", dict[str, Any]),
]


# ---------------------------------------------------------------- Lead (3.5)
class LeadFactor(Record):
    factor: str
    value: Optional[Any] = None
    points: float
    max_points: float
    source: Optional[str] = None


class LeadEvidence(Record):
    type: str
    id: str
    role: str
    preview: Optional[dict[str, Any]] = None


class Decision(Record):
    lead_id: str
    time_utc: datetime
    user: str
    from_state: LeadState
    to_state: LeadState
    reason: Optional[str] = None
    note: Optional[str] = None
    build: Literal["open", "research"]
    app_version: str


LEAD_SPEC = [
    ("lead_id", str), ("lead_type", Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]), ("title", str),
    ("state", LeadState), ("reason", Str), ("priority", int), ("priority_band", Literal["low", "medium", "high"]),
    ("factors", list[LeadFactor]), ("priority_model_id", str), ("calibrated", bool),
    ("primary_type", Literal["contact", "vessel", "cell", "light"]), ("primary_id", str), ("evidence", list[LeadEvidence]),
    ("lon", float), ("lat", float), ("time_utc", datetime), ("region_box", Str), ("next_look_utc", Time),
    ("lawful_explanations", list[str]), ("change_indicators", list[str]), ("history", list[Decision]),
    # Queue convenience fields from the primary contact (spec 4.1 queue columns; proposed contract 1.2.1).
    ("ais_status", Optional[AisStatus]), ("cnn_score", Num), ("length_est_m", Num), ("pass_id", Str),
    ("research_only", bool), ("caveat", str), ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any]),
]


class DecisionIn(BaseModel):
    to_state: LeadState
    reason: Optional[str] = None
    note: Optional[str] = None
    user: Optional[str] = None


class LabelIn(BaseModel):
    label: Literal["vessel", "structure", "clutter", "unsure"]
    user: Optional[str] = None
    note: Optional[str] = None


class LabelRecord(Record):
    det_id: str
    label: str
    user: str
    time_utc: datetime
    note: Optional[str] = None
    build: str
    caveat: str
    src: str = "analyst"


# ---------------------------------------------------------------- Pass (3.6)
PASS_SPEC = [
    ("pass_id", str), ("mission", Literal["S1C", "S1D"]), ("relative_orbit", Int),
    ("pass_dir", Optional[Literal["ASCENDING", "DESCENDING"]]), ("start_utc", datetime), ("stop_utc", datetime),
    ("status", Literal["past", "in_progress", "upcoming"]), ("sources", list[Literal["esa_plan", "repeat_cycle", "processed"]]),
    ("footprint", Optional[dict[str, Any]]), ("aoi_overlap_km2", Num), ("aoi_parts", Optional[list[str]]), ("scenes", list[str]),
    ("processed", bool), ("n_contacts", Optional[dict[str, int]]), ("ais_aoi_positions", Int), ("ais_aoi_mmsi", Int),
    ("ais_footprint_positions", Int), ("ais_footprint_mmsi", Int), ("ais_near_footprint_mmsi", Int), ("ais_heard_share", Num),
    ("note", Str), ("research_only", bool), ("caveat", str), ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any]),
]


# ---------------------------------------------------------------- Cell (3.7)
class EezAttrs(Record):
    heading: str
    statement: str
    marineregions_mrgid: Int = None
    marineregions_geoname: Str = None
    marineregions_pol_type: Str = None
    marineregions_share: Num = None
    marineregions_n: Int = None
    marineregions_overlap_share: Num = None


CELL_STATIC = [
    "aoi_centre", "aoi_share", "n_sea", "sea_share", "sea_area_km2", "depth_mean_m", "depth_median_m", "depth_min_m",
    "depth_max_m", "depth_std_m", "share_shallower_50m", "share_shallower_200m", "share_shelf_break_150_250m",
    "slope_mean_m_per_km", "dist_coast_km", "dist_coast_min_km", "dist_port_km", "dist_port_min_km",
    "ship_presence_share_all", "ship_presence_share_fishing", "ship_presence_share_commercial",
    "ship_presence_share_oilgas", "ship_presence_share_passenger", "ship_presence_share_leisure",
]
CELL_SPEC = (
    [("cell_id", str), ("row", int), ("col", int), ("lon", float), ("lat", float), ("region_box", str)]
    + [(n, Optional[Any]) for n in CELL_STATIC]
    + [("shipping_note", Str), ("nightly", Optional[dict[str, Any]]), ("nights_available", list[str]),
       ("pass_context", Optional[dict[str, Any]]), ("ais_reach_share", Num), ("ais_reach_mmsi", Num),
       ("look_prob_1d", Num), ("look_prob_7d", Num), ("look_prob_30d", Num), ("passes_90d", Num),
       ("object_context", Optional[dict[str, Any]]), ("expected_activity", Optional[dict[str, Any]]),
       ("gfw_comparison", Optional[dict[str, Any]]), ("eez", Optional[EezAttrs]),
       ("research_only", bool), ("caveat", str), ("src", str), ("prov", dict[str, str]), ("extra", dict[str, Any])]
)


class RasterEntry(Record):
    name: str
    unit: Optional[str] = None
    resolution_deg: float
    valid_period: Optional[str] = None
    colormap: str
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    bounds: list[float]
    src: str
    licence: Optional[str] = None
    default_on: bool = False
    note: Optional[str] = None
    research_only: bool
    caveat: str


class RasterValue(Record):
    name: str
    lon: float
    lat: float
    value: Optional[float] = None
    presence: Optional[bool] = None  # shipping density only (board D4.3): value is 1.0 or 0.0
    value_as_published: Optional[float] = None  # shipping density only: the published number, not a count
    unit: Optional[str] = None
    valid_period: Optional[str] = None
    src: str
    note: Optional[str] = None
    research_only: bool
    caveat: str


class SearchResult(Record):
    type: Literal["contact", "vessel", "light", "lead", "event", "pass", "cell", "site", "point"]
    id: str
    label: str
    sublabel: str
    lon: Optional[float] = None
    lat: Optional[float] = None
    score: float
    caveat: str


class SourceEntry(Record):
    key: str
    name: str
    method: Optional[str] = None
    script: Optional[str] = None
    licence: Optional[str] = None
    licence_url: Optional[str] = None
    url: Optional[str] = None
    access_date: Optional[str] = None
    credit: Optional[str] = None
    research_only: bool
    git_hash: Optional[str] = None


class FileEntry(Record):
    key: str
    path: str
    layer: Optional[str] = None
    status: Literal["existing", "missing"]
    rows: Optional[int] = None
    mtime: Optional[datetime] = None


class MetaRecord(Record):
    contract_version: str
    build: Literal["open", "research"]
    build_label: str
    research_label: Optional[str] = None
    attribution: Optional[str] = None
    caveat: str
    caveat_short: str
    generated_utc: datetime
    loaded_utc: datetime
    git_hash: Optional[str] = None
    app_version: str
    priority_model_id: Optional[str] = None
    sources: list[SourceEntry]
    files: list[FileEntry]
    counts: dict[str, int]
    ais_recording: Optional[dict[str, Any]] = None
    live_rules: Optional[dict[str, Any]] = None
    data_credit: str
    load_seconds: Optional[float] = None
    loading: list[str] = []  # loaders still loading in the background; their counts come from the catalog
    reload_error: Optional[str] = None  # the last failed reload (the previous data is still served)


def build_models(build: str) -> dict[str, type[Record]]:
    """Every record model of one build, keyed by object type."""
    return {
        "contact": _model("Contact", contact_spec(build), "Radar contact (contract 3.1)"),
        "contact_summary": _model("ContactSummary", contact_summary_spec(build), "Contact summary: D1 plus view, pass_id, lead_ids"),
        "vessel": _model("Vessel", VESSEL_SPEC, "AIS identity (contract 3.2)"),
        "vessel_summary": _model("VesselSummary", [(n, dict(VESSEL_SPEC)[n]) for n in VESSEL_SUMMARY]),
        "light": _model("Light", LIGHT_SPEC, "VIIRS light (contract 3.3)"),
        "light_summary": _model("LightSummary", [(n, dict(LIGHT_SPEC)[n]) for n in LIGHT_SUMMARY]),
        "site": _model("LightSite", SITE_SPEC, "Recurring light site (contract 3.3)"),
        "event": _model("Event", EVENT_SPEC, "Observation event (contract 3.4)"),
        "lead": _model("Lead", LEAD_SPEC, "Lead for review (contract 3.5)"),
        "pass": _model("Pass", PASS_SPEC, "Sentinel-1 pass (contract 3.6)"),
        "cell": _model("Cell", [c for c in CELL_SPEC if build == "research" or not c[0].startswith("gfw_")],
                       "Cell context, 0.25 degree model grid (contract 3.7)"),
    }


# ---------------------------------------------------------------- Envelope (section 5)
T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    caveat: str
    build: str
    contract_version: str


class ItemEnvelope(BaseModel, Generic[T]):
    contract_version: str
    build: Literal["open", "research"]
    build_label: str
    caveat: str
    generated_utc: datetime
    research_label: Optional[str] = None
    attribution: Optional[str] = None
    item: T


class ListEnvelope(BaseModel, Generic[T]):
    contract_version: str
    build: Literal["open", "research"]
    build_label: str
    caveat: str
    generated_utc: datetime
    research_label: Optional[str] = None
    attribution: Optional[str] = None
    items: list[T]
    total: int
    limit: int
    offset: int
