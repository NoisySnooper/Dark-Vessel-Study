// Record shapes of app/CONTRACT.md section 3 (version 1.2.0, plus the board D5.3 and D5.4 shapes of 1.3.0). Both
// adapters return these.
// Field names are the contract's; nulls are null. `src` is the record's default source key and `prov` maps
// field names to source keys where they differ (section 2).

export type Build = "open" | "research";
export type AisStatus = "matched" | "unmatched" | "no_coverage" | "not_checked";
export type Confidence = "high" | "medium" | "fixed" | "low";
export type LeadState = "new" | "reviewing" | "closed_explained" | "closed_unexplained" | "closed_false_alarm";
export type PriorityBand = "low" | "medium" | "high";

export interface Provenanced {
  src: string;
  prov: Record<string, string>;
  caveat: string;
  research_only: boolean;
  extra?: Record<string, unknown>;
}

/** One ocean field at an object (board D5.3): value, unit, valid time and source as the context table states them. */
export interface ContextField {
  value: number | boolean | null;
  unit: string | null;
  time: string | null;
  src: string | null;
}
/** `object_context` of a Contact or Light (board D5.3); null when the object has no row in the context table. */
export interface ObjectContext {
  time_utc: string | null;
  cell_id: string | null;
  region: string | null;
  fields: Record<string, ContextField>;
  caveat: string;
}
/** One tested row of the expected-activity table (board D5.4). */
export interface ExpectedRow {
  unit_id: string;
  night: string;
  time_start_utc: string | null;
  time_end_utc: string | null;
  tested: boolean;
  observed: number | null;
  expected: number | null;
  z: number | null;
  q_bh: number | null;
  flag: string | null;
  flag_robust: string | null;
  calm: boolean | null;
  exposure_km2: number | null;
}
/** Per-target counts when only a summary is embedded (single-file page under its budget). */
export interface ExpectedSummary {
  n_tested: number | null;
  n_flag: number | null;
  n_flag_robust: number | null;
}
/** `expected_activity` of a Cell (board D5.4): tested rows per target, newest first; or a counts-only summary. */
export interface ExpectedActivity {
  model_id: string | null;
  caveat: string;
  targets: Record<string, ExpectedRow[]>;
  summary?: Record<string, ExpectedSummary> | null;
  note?: string | null;
}

/** Raster overlay registry entry (contract 3.7, Raster layers). `image` is a data URI in the single-file page. */
export interface RasterEntry {
  name: string;
  unit: string | null;
  resolution_deg: number | null;
  valid_period: string | null;
  colormap: string;
  vmin: number | null;
  vmax: number | null;
  bounds: [number, number, number, number];
  src: string;
  licence: string | null;
  default_on: boolean;
  note: string | null;
  research_only: boolean;
  label?: string | null;
  image?: string | null;
}

export interface Contact extends Provenanced {
  det_id: string;
  run_id: string;
  mission: "S1C" | "S1D";
  acq_utc: string;
  lon: number;
  lat: number;
  length_est_m: number | null;
  confidence: Confidence;
  cnn_score: number | null;
  cnn_vessel: boolean | null;
  ais_status: AisStatus;
  ais_source: string | null;
  match_method: string | null;
  match_dist_m: number | null;
  match_dt_s: number | null;
  match_quality: "high" | "medium" | "low" | null;
  mmsi: string | null;
  imo: string | null;
  vessel_name: string | null;
  call_sign: string | null;
  flag: string | null;
  ship_type: string | null;
  length_ais_m: number | null;
  identity_source: string | null;
  nearest_ais_mmsi: string | null;
  nearest_ais_dist_m: number | null;
  nearest_ais_dt_s: number | null;
  n_ais_10km: number | null;
  ais_reach: number | null;
  // extensions
  view: "regional" | "live" | "camau";
  dark_lead: boolean | null;
  vessel_key: string | null;
  nearest_vessel_key: string | null;
  nearest_vessel_name?: string | null;
  scene_id: string | null;
  pass_id: string | null;
  pass_dir: string | null;
  orbit_rel: number | null;
  inc_angle_deg: number | null;
  pol_class: string | null;
  n_pixels: number | null;
  scr_vv_db: number | null;
  scr_vh_db: number | null;
  low_reason?: string | null;
  persist_dates: number | null;
  persist_dates_checked: number | null;
  n_low_1km: number | null;
  near_fixed_m: number | null;
  match_gate_m: number | null;
  ais_sog_kn?: number | null;
  length_ratio: number | null;
  ais_class: string | null;
  ais_footprint_positions?: number | null;
  ais_recorded_hours?: number | null;
  cnn_threshold: number | null;
  cnn_model_id: string | null;
  cnn_chip_valid_frac?: number | null;
  bg_vv_db?: number | null;
  bg_vh_db?: number | null;
  wind_ms?: number | null;
  ctt_k?: number | null;
  deep_convection?: boolean | null;
  optical_object?: boolean | null;
  optical_kind?: string | null;
  satlas_m?: number | null;
  cell_id: string;
  lead_ids: string[];
  chip: string | null;
  synthetic?: boolean | null;
  /** Ocean context at the contact (board D5.3); null or absent until the context table has a row for it. */
  object_context?: ObjectContext | null;
  /** Live passes: the pairing could not tell this contact apart from two or more AIS vessels (darkvessel.live.assign). */
  match_ambiguous?: boolean | null;
  /** Candidate MMSIs of an ambiguous contact: one MMSI or several joined by ';' (contract 1.3.0). */
  ambiguous_mmsi?: string | null;
  match_alt_dist_m?: number | null;
  /** Hand check of R3-T7: '<grade>: <reason>' (grade confirmed, plausible or doubtful); null when not checked. */
  review_note?: string | null;
  review_grade?: "confirmed" | "plausible" | "doubtful" | null;
  /** Board D4.7 label of an aisstream-derived identity, as the API states it. */
  identity_label?: string | null;
  az_time_utc?: string | null;
  az_shift_m?: number | null;
  match_dist_uncorr_m?: number | null;
  velocity_source?: string | null;
  pred_method?: string | null;
  [k: string]: unknown;
}

export interface Vessel extends Provenanced {
  vessel_key: string;
  mmsi: string | null;
  mid: number | null;
  flag: string | null;
  name: string | null;
  call_sign: string | null;
  imo: string | null;
  ais_class: "A" | "B" | null;
  ship_type: string | null;
  gear_type: string | null;
  identity_kind: string | null;
  length_m: number | null;
  width_m: number | null;
  length_ais_m: number | null;
  tonnage_gt: number | null;
  identity_source: string | null;
  destination: string | null;
  eta: string | null;
  first_seen_utc: string | null;
  last_seen_utc: string | null;
  static_seen_utc: string | null;
  n_positions: number | null;
  n_messages: number | null;
  last_lon: number | null;
  last_lat: number | null;
  sog_kn: number | null;
  cog_deg: number | null;
  heading: number | null;
  nav_status_label: string | null;
  in_aoi: boolean | null;
  ever_in_aoi: boolean | null;
  gear_beacon_like: boolean | null;
  registry_sources?: string | null;
  registry_records?: number | null;
  dataset_version?: string | null;
  stub: boolean;
  contacts_matched: string[];
  identity_note: string;
}

export interface TrackPoint {
  t: string | null;
  lon: number;
  lat: number;
  sog_kn?: number | null;
  cog_deg?: number | null;
}
export interface TrackGap {
  from_utc: string;
  to_utc: string;
  minutes: number;
  note: string;
}
export interface Track {
  vessel_key: string;
  points: TrackPoint[];
  gaps: TrackGap[];
  start_utc: string | null;
  end_utc: string | null;
  simplified: boolean;
  note: string | null;
}

export interface Light extends Provenanced {
  light_id: string;
  satellite: "S-NPP" | "NOAA-20" | "NOAA-21";
  time_utc: string;
  night: string;
  lon: number;
  lat: number;
  radiance_nw: number;
  spike_nw: number | null;
  isolation: number | null;
  quality: "clear" | "under_cloud";
  class: string;
  nights_seen_500m: number;
  clear_nights_cell: number;
  moon_illum_pct: number | null;
  satlas_infra_m: number | null;
  s1_passes_90d: number;
  site_id: string | null;
  contacts_2km_same_night: string[];
  cell_id: string;
  /** Ocean context at the light (board D5.3). */
  object_context?: ObjectContext | null;
}

export interface EventRecord extends Provenanced {
  event_id: string;
  code: string;
  event_type: string;
  start_utc: string;
  end_utc: string | null;
  duration_h: number | null;
  lon: number;
  lat: number;
  geometry: unknown | null;
  mmsi: string[];
  vessel_keys: string[];
  det_ids: string[];
  light_ids: string[];
  cell_ids: string[];
  params: Record<string, unknown>;
  rule_text: string;
  grade: string | null;
  source: string;
}

export interface LeadFactor {
  factor: string;
  value: string | number | null;
  points: number;
  max_points: number;
  source: string;
}
export interface LeadEvidence {
  /** `weather` is the weather sidecar row of the primary contact (id = det_id), not an object page. */
  type: "contact" | "vessel" | "light" | "event" | "cell" | "pass" | "weather";
  id: string;
  role: string;
  /** Resolved preview from GET /leads/{id} (contract section 5): label, lon, lat and a few type fields. */
  preview?: Record<string, unknown> | null;
}
export interface Decision {
  lead_id: string;
  time_utc: string;
  user: string;
  from_state: LeadState;
  to_state: LeadState;
  reason: string | null;
  note: string | null;
  build: Build;
  app_version: string;
}
export interface Lead extends Provenanced {
  lead_id: string;
  lead_type: string;
  title: string;
  state: LeadState;
  reason: string | null;
  priority: number;
  priority_band: PriorityBand;
  factors: LeadFactor[];
  priority_model_id: string;
  calibrated: boolean;
  primary_type: "contact" | "vessel" | "cell" | "light";
  primary_id: string;
  evidence: LeadEvidence[];
  lon: number;
  lat: number;
  time_utc: string;
  region_box: string | null;
  next_look_utc: string | null;
  lawful_explanations: string[];
  change_indicators: string[];
  history: Decision[];
  synthetic?: boolean | null;
  // convenience fields resolved by the adapter from the primary contact
  ais_status?: AisStatus | null;
  cnn_score?: number | null;
  length_est_m?: number | null;
  pass_id?: string | null;
  /** Set by the embedded adapter when the lead no longer stands against its primary contact (identity.ts staleLeadReason). */
  stale_reason?: string | null;
}

export interface Pass extends Provenanced {
  pass_id: string;
  mission: "S1C" | "S1D";
  relative_orbit: number | null;
  pass_dir: string | null;
  start_utc: string;
  stop_utc: string;
  status: "past" | "in_progress" | "upcoming";
  sources: string[];
  footprint: unknown | null;
  aoi_overlap_km2: number | null;
  aoi_parts: string[] | null;
  aoi_overlap_bbox?: number[] | null;
  scenes: string[];
  processed: boolean;
  n_contacts: Record<string, number> | null;
  ais_aoi_positions?: number | null;
  ais_aoi_mmsi?: number | null;
  ais_footprint_positions?: number | null;
  ais_footprint_mmsi?: number | null;
  ais_near_footprint_mmsi?: number | null;
  ais_heard_share?: number | null;
  scene_counts?: Record<string, unknown>[];
  note?: string | null;
  fixture_note?: string | null;
  contacts_in_bundle?: boolean | null;
  /** Contract 1.3.0: AIS vessels in the footprint that no contact matched (full list on the pass record only). */
  n_ais_only?: number | null;
  ais_only?: AisOnlyVessel[] | null;
  azimuth_check?: { by_scene: Record<string, unknown>[]; note?: string | null } | null;
  identity_label?: string | null;
}

/** One AIS vessel of a live pass that no radar contact matched (live file layer ais_only_4326). */
export interface AisOnlyVessel {
  mmsi: string | null;
  vessel_key?: string | null;
  vessel_name?: string | null;
  call_sign?: string | null;
  imo?: string | null;
  flag?: string | null;
  ship_type?: string | null;
  ais_class?: string | null;
  length_ais_m?: number | null;
  sog_kn?: number | null;
  lon?: number | null;
  lat?: number | null;
  scene_id?: string | null;
  pred_method?: string | null;
  pred_dt_s?: number | null;
  n_reports?: number | null;
  on_tested_sea?: boolean | null;
  dist_coast_km?: number | null;
  nearest_object_m?: number | null;
  nearest_object_class?: string | null;
  ambiguous_det_id?: string | null;
  oversized_det_id?: string | null;
  identity_source?: string | null;
  identity_label?: string | null;
}

export interface SourceEntry {
  key: string;
  name: string;
  method: string | null;
  script: string | null;
  licence: string | null;
  licence_url: string | null;
  url: string | null;
  access_date: string | null;
  credit: string | null;
  research_only: boolean;
}
export interface FileEntry {
  path: string;
  layer: string | null;
  status: "existing" | "missing";
  rows: number | null;
  in_bundle?: number | null;
  mtime?: string | null;
}
export interface Meta {
  contract_version: string;
  build: Build;
  build_label: string;
  research_label?: string | null;
  attribution?: string | null;
  caveat: string;
  caveat_short?: string;
  generated_utc: string;
  git_hash: string;
  priority_model_id: string | null;
  app_version?: string;
  sources: SourceEntry[];
  files: FileEntry[];
  counts: Record<string, number>;
  parts?: Record<string, number>;
  dropped?: { part: string; reason: string }[];
  ais_recording?: {
    period_start_utc: string | null;
    period_end_utc: string | null;
    hours_recorded: number | null;
    gaps: unknown[];
    positions?: number | null;
    mmsi_count?: number | null;
    note?: string;
  } | null;
  live_rules?: Record<string, string | null>;
  data_credit?: string;
  fixture?: { synthetic: boolean; note: string; synthetic_counts?: Record<string, number> } | null;
}

export interface SearchResult {
  type: "contact" | "vessel" | "light" | "lead" | "event" | "pass" | "point";
  id: string;
  label: string;
  sublabel: string;
  lon: number | null;
  lat: number | null;
  score: number;
}
export interface SearchResponse {
  results: SearchResult[];
  interpretation: string | null;
}

// Bulk point layers for the map: parallel typed arrays (contract 6.2 columns decoded once).
export interface PointLayerData {
  n: number;
  lon: Float64Array;
  lat: Float64Array;
  ids: (i: number) => string;
  // per-point style classes
  status: Uint8Array; // index into AIS_STATUS_ORDER, 255 none
  shape: Uint8Array; // 0 circle (high), 1 ring (medium), 2 square (fixed), 3 dot (low)
  size: Uint8Array; // 0 small (<25 m), 1 medium, 2 large (>100 m)
  faded: Uint8Array; // 1 when cnn_vessel is false
  time: Float64Array; // ms since epoch, NaN when unknown
}

export interface GeoLayer {
  kind: "point" | "line" | "polygon";
  n: number;
  xy: Int32Array; // lon, lat x 10000 interleaved
  ring: Uint32Array; // start vertex of each ring or line
  feat: Uint32Array; // start ring of each feature
  props: Record<string, (i: number) => unknown>;
  note: string | null;
}

export interface ListQuery {
  t0?: string;
  t1?: string;
  bbox?: [number, number, number, number];
  limit?: number;
  offset?: number;
  sort?: string;
  [k: string]: unknown;
}
export interface ListResult<T> {
  items: T[];
  total: number;
}

export interface TimelineRows {
  passes: Pass[];
  contactsByPass: { pass_id: string; start_utc: string; counts: Record<string, number> }[];
  aisHours: { start_utc: string; end_utc: string }[];
  aisGaps: { start_utc: string; end_utc: string; label: string }[];
  viirsNights: { night: string; n: number }[];
  events: EventRecord[];
}

export interface DataAdapter {
  readonly kind: "embedded" | "http";
  meta(): Promise<Meta>;
  contacts(q?: ListQuery): Promise<ListResult<Contact>>;
  contact(det_id: string): Promise<Contact | null>;
  contactPoints(): Promise<PointLayerData | null>;
  vessels(q?: ListQuery): Promise<ListResult<Vessel>>;
  vessel(vessel_key: string): Promise<Vessel | null>;
  vesselPoints(): Promise<PointLayerData | null>;
  track(vessel_key: string): Promise<Track | null>;
  lights(q?: ListQuery): Promise<ListResult<Light>>;
  light(light_id: string): Promise<Light | null>;
  lightPoints(): Promise<PointLayerData | null>;
  events(q?: ListQuery): Promise<ListResult<EventRecord>>;
  event(event_id: string): Promise<EventRecord | null>;
  leads(q?: ListQuery): Promise<ListResult<Lead>>;
  lead(lead_id: string): Promise<Lead | null>;
  decide(lead_id: string, to_state: LeadState, reason: string | null, note: string | null, user: string): Promise<Lead>;
  decisionLog(): Decision[];
  passes(q?: ListQuery): Promise<ListResult<Pass>>;
  pass(pass_id: string): Promise<Pass | null>;
  geo(name: string): Promise<GeoLayer | null>;
  geoNote(name: string): Promise<string | null>;
  search(q: string, limit?: number): Promise<SearchResponse>;
  timeline(): Promise<TimelineRows>;
  /** Chip image URL. `ref` is the record's `chip` field when known: null means no cached or embedded chip. */
  chip(det_id: string, ref?: string | null): Promise<string | null>;
  /** Local app only: build a missing chip (`chip.webp?fetch=1`); resolves to an image URL or throws with the reason. */
  fetchChip?(det_id: string): Promise<string>;
  cell(cell_id: string): Promise<CellRecord | null>;
  /** Raster overlay registry (contract 3.7); empty when the build has none. */
  rasters(): Promise<RasterEntry[]>;
  /** Image URL of one overlay for the theme (a data URI in the single-file page). */
  rasterImage(name: string, theme: "dark" | "light"): Promise<string | null>;
}

/** Cell context record (contract section 3.7). Known fields typed; the rest pass through. */
export interface CellRecord extends Provenanced {
  cell_id: string;
  row: number;
  col: number;
  lon: number;
  lat: number;
  region_box: string | null;
  nightly?: Record<string, unknown> | null;
  pass_context?: Record<string, unknown> | null;
  object_context?: Record<string, unknown> | null;
  expected_activity?: ExpectedActivity | null;
  eez?: Record<string, unknown> | null;
  caveat: string;
  [k: string]: unknown;
}

export const AIS_STATUS_ORDER: AisStatus[] = ["matched", "unmatched", "no_coverage", "not_checked"];
