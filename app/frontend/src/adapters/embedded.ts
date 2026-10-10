// Embedded adapter: reads <script type="application/json" id="scs-part-NAME"> elements (contract 6.1 and 6.2),
// parses a part on first use and answers the same queries as the HTTP adapter with the same record shapes.
// Lead decisions are held in page memory (plus a guarded storage copy) and exported as JSONL (spec section 13).
import { decodeGeom, decodePart, numberArray, timeMs, type ColumnarPart, type GeomSpec, type Getter } from "./columns";
import { normalizeExpected, normalizeObjectContext } from "./context";
import { parseQuery } from "./search";
import { cellIdOf, haversineM, priorityBand } from "../app/format";
import { AIS_STATUS_LABEL, GAP_NOTE, LEAD_LAWFUL, LEAD_TYPE_NAME, OCEAN_CAVEAT, PRODUCT_CAVEAT } from "../app/text";
import { staleLeadReason } from "../app/identity";
import { readJson, writeJson } from "../storage";
import type {
  AisStatus, CellRecord, Contact, DataAdapter, Decision, EventRecord, GeoLayer, Lead, LeadFactor, LeadState, Light, ListQuery, ListResult,
  Meta, ObjectContext, Pass, PointLayerData, RasterEntry, SearchResponse, SearchResult, TimelineRows, Track, Vessel,
} from "./types";
import { AIS_STATUS_ORDER } from "./types";

interface Decoded {
  n: number;
  get: Record<string, Getter>;
  records: Record<string, Record<string, unknown>>;
  part: ColumnarPart;
}

export function hasEmbeddedBundle(): boolean {
  return !!document.getElementById("scs-part-meta");
}

function readPartJson<T>(name: string): T | null {
  const el = document.getElementById("scs-part-" + name);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent || "null") as T;
  } catch (e) {
    console.warn("part " + name + " did not parse", e);
    return null;
  }
}

function str(v: unknown): string | null {
  return v === null || v === undefined ? null : String(v);
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

const SHAPE: Record<string, number> = { high: 0, medium: 1, fixed: 2, low: 3 };

// Record keys that are not contact fields (bundle reading 1): provenance, pass-through extras and lead links.
const RECORD_META_KEYS = new Set(["prov", "extra", "lead_ids", "object_context"]);

type ContextDefaults = Record<string, { unit?: string | null; src?: string | null; time?: string | null }>;
interface ContextBlock {
  n: number;
  columns: ColumnarPart["columns"];
  /** Per field: unit, registry key or dataset, and the column holding its valid time or source (README reading 13). */
  fields?: Record<string, { unit?: string | null; src?: string | null; time?: string | null; time_col?: string | null; src_col?: string | null }>;
  caveat?: string;
}

/**
 * `object_context` of row `i` of a part (board D5.3, README reading 13): the record's own `object_context` when present,
 * else the part's columnar `object_context` block (rows parallel to the part's rows; a row whose `time_utc` is null has
 * no context). Units, times and sources a record omits come from the block's `fields` map.
 */
function contextOf(d: Decoded, i: number, rec: Record<string, unknown>, cache: Map<string, unknown>, cacheKey: string): ObjectContext | null {
  const block = d.part.object_context as ContextBlock | undefined;
  const defaults = (block?.fields || (d.part.context_fields as ContextDefaults | undefined)) as ContextDefaults | undefined;
  const caveat = block?.caveat || OCEAN_CAVEAT;
  if (rec.object_context !== undefined) return normalizeObjectContext(rec.object_context, defaults, caveat);
  if (!block || !block.columns) return null;
  if (!cache.has(cacheKey)) cache.set(cacheKey, decodePart({ type: "object_context", n: block.n, columns: block.columns }));
  const g = (cache.get(cacheKey) as { get: Record<string, Getter> }).get;
  const t = g.time_utc ? g.time_utc(i) : null;
  if (t === null || t === undefined) return null;
  const fields: Record<string, unknown> = {};
  for (const [name, spec] of Object.entries(block.fields || {})) {
    const col = g[name];
    if (!col) continue;
    fields[name] = {
      value: col(i),
      unit: spec.unit ?? null,
      time: spec.time_col && g[spec.time_col] ? g[spec.time_col](i) : spec.time ?? null,
      src: spec.src_col && g[spec.src_col] ? g[spec.src_col](i) : spec.src ?? null,
    };
  }
  return normalizeObjectContext({ time_utc: t, cell_id: g.cell_id ? g.cell_id(i) : null, region: g.region ? g.region(i) : null, fields, caveat }, defaults, caveat);
}

/** Provenance set of a contact row (bundle `prov_sets` with `prov_set_rule`): live, camau, research, structures or regional. */
function provSetName(view: unknown, researchOnly: unknown, confidence: unknown): string {
  if (view === "live") return "live";
  if (view === "camau") return "camau";
  if (researchOnly === true) return "research";
  if (confidence === "fixed") return "structures";
  return "regional";
}

/** Bulk point layer from a decoded columnar part (contacts, structures, lights or vessels). Shared with the HTTP adapter. */
export function pointsFromDecoded(d: Decoded, kind: "contacts" | "lights" | "vessels"): PointLayerData {
  const n = d.n;
  const lonCol = kind === "vessels" ? "last_lon" : "lon";
  const latCol = kind === "vessels" ? "last_lat" : "lat";
  const lon = numberArray(d.part.columns[lonCol], n);
  const lat = numberArray(d.part.columns[latCol], n);
  const status = new Uint8Array(n).fill(255);
  const shape = new Uint8Array(n);
  const size = new Uint8Array(n).fill(1);
  const faded = new Uint8Array(n);
  const timeCol = kind === "contacts" ? "acq_utc" : kind === "lights" ? "time_utc" : "last_seen_utc";
  const time = timeMs(d.part.columns[timeCol], n);
  const idGetter = kind === "contacts" ? d.get.det_id : kind === "lights" ? d.get.light_id : d.get.vessel_key;
  if (kind === "contacts") {
    const st = d.get.ais_status;
    const conf = d.get.confidence;
    const len = d.get.length_est_m;
    const cnn = d.get.cnn_vessel;
    for (let i = 0; i < n; i++) {
      const s = AIS_STATUS_ORDER.indexOf(st(i) as AisStatus);
      status[i] = s < 0 ? 255 : s;
      shape[i] = SHAPE[String(conf(i))] ?? 0;
      const L = len(i) as number | null;
      size[i] = L === null ? 1 : L < 25 ? 0 : L > 100 ? 2 : 1;
      faded[i] = cnn(i) === false ? 1 : 0;
    }
  } else if (kind === "vessels") {
    const cls = d.get.ais_class;
    for (let i = 0; i < n; i++) size[i] = cls(i) === "B" ? 0 : 1;
  } else {
    const q = d.get.quality;
    for (let i = 0; i < n; i++) faded[i] = q(i) === "under_cloud" ? 1 : 0;
  }
  return { n, lon, lat, ids: (i) => String(idGetter ? idGetter(i) : i), status, shape, size, faded, time };
}

/** Part-level code lists by lead type: `{L1: [codes]}` or `{L1: {code: sentence}}` (the builder may write either). */
function listsByType(raw: unknown): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [t, v] of Object.entries(raw as Record<string, unknown>)) {
    if (Array.isArray(v)) out[t] = v.map(String);
    else if (v && typeof v === "object") out[t] = Object.keys(v as Record<string, unknown>);
  }
  return out;
}

export class EmbeddedAdapter implements DataAdapter {
  readonly kind = "embedded" as const;
  private cache = new Map<string, unknown>();
  private metaCache: Meta | null = null;
  private decisions: Decision[] = [];
  private stateOverride = new Map<string, { state: LeadState; reason: string | null; history: Decision[] }>();

  constructor() {
    const saved = readJson<Decision[]>("decisions", []);
    if (Array.isArray(saved)) {
      for (const d of saved) this.applyDecision(d, false);
    }
  }

  // ------------------------------------------------------------------ parts
  private part(name: string): Decoded | null {
    if (this.cache.has(name)) return this.cache.get(name) as Decoded | null;
    const raw = readPartJson<ColumnarPart>(name);
    const d = raw && raw.columns ? { ...decodePart(raw), part: raw } : null;
    this.cache.set(name, d);
    return d;
  }

  private index(name: string, idCol: string): Map<string, number> {
    const key = "idx:" + name;
    if (this.cache.has(key)) return this.cache.get(key) as Map<string, number>;
    const m = new Map<string, number>();
    const d = this.part(name);
    if (d && d.get[idCol]) for (let i = 0; i < d.n; i++) m.set(String(d.get[idCol](i)), i);
    this.cache.set(key, m);
    return m;
  }

  async meta(): Promise<Meta> {
    if (this.metaCache) return this.metaCache;
    const m = readPartJson<Meta>("meta");
    if (!m) throw new Error("scs-part-meta missing");
    this.metaCache = m;
    return m;
  }

  private caveat(): string {
    return this.metaCache?.caveat || PRODUCT_CAVEAT;
  }

  // ------------------------------------------------------------------ contacts
  private leadIndexByPrimary(): Map<string, string[]> {
    const key = "idx:leads-by-primary";
    if (this.cache.has(key)) return this.cache.get(key) as Map<string, string[]>;
    const m = new Map<string, string[]>();
    const d = this.part("leads");
    if (d) {
      for (let i = 0; i < d.n; i++) {
        const lead = this.leadFromRow(i, false);
        if (!lead) continue;
        const k = lead.primary_type + ":" + lead.primary_id;
        const arr = m.get(k) || [];
        arr.push(lead.lead_id);
        m.set(k, arr);
      }
    }
    this.cache.set(key, m);
    return m;
  }

  private contactFromRow(i: number): Contact {
    const d = this.part("contacts")!;
    const g = d.get;
    const v = (name: string) => (g[name] ? g[name](i) : null);
    const det_id = String(v("det_id"));
    const rec = d.records[det_id] || {};
    const vessels = this.part("vessels");
    const vref = v("vessel_ref") as number | null;
    const nref = v("nearest_ref") as number | null;
    const vrow = vessels && vref !== null ? vref : null;
    const nrow = vessels && nref !== null ? nref : null;
    const vg = (name: string, row: number | null) => (row === null || !vessels || !vessels.get[name] ? null : vessels.get[name](row));
    const lon = num(v("lon")) ?? 0;
    const lat = num(v("lat")) ?? 0;
    const mmsi = str(rec.mmsi) ?? str(v("mmsi")) ?? str(vg("mmsi", vrow));
    const nearestMmsi = str(rec.nearest_ais_mmsi) ?? str(v("nearest_ais_mmsi")) ?? str(vg("mmsi", nrow));
    const provSets = d.part.prov_sets as Record<string, Record<string, string>> | undefined;
    const baseProv = provSets ? (provSets[provSetName(v("view"), v("research_only"), v("confidence"))] || d.part.prov || {}) : (d.part.prov || {});
    const prov = { ...baseProv, ...((rec.prov as Record<string, string>) || {}) };
    // Vessel keys come from the referenced vessel row (research: <source>:<vessel_id>), else from the MMSI (open: mmsi:<mmsi>).
    const vesselKey = str(vg("vessel_key", vrow));
    const nearestKey = str(vg("vessel_key", nrow));
    const leadIds = [...new Set([...(((rec.lead_ids as string[]) || [])), ...(this.leadIndexByPrimary().get("contact:" + det_id) || [])])];
    const chips = this.chipsPart();
    const c: Contact = {
      det_id,
      run_id: String(v("run_id") ?? ""),
      mission: (v("mission") as "S1C" | "S1D") ?? (det_id.slice(0, 3) as "S1C" | "S1D"),
      acq_utc: String(v("acq_utc") ?? ""),
      lon, lat,
      length_est_m: num(v("length_est_m")),
      confidence: (v("confidence") as Contact["confidence"]) ?? "high",
      cnn_score: num(v("cnn_score")),
      cnn_vessel: bool(v("cnn_vessel")),
      ais_status: (v("ais_status") as AisStatus) ?? "not_checked",
      ais_source: str(v("ais_source")),
      match_method: str(v("match_method")),
      match_dist_m: num(v("match_dist_m")),
      match_dt_s: num(v("match_dt_s")),
      match_quality: (v("match_quality") as Contact["match_quality"]) ?? null,
      mmsi,
      imo: str(v("imo")) ?? str(vg("imo", vrow)),
      vessel_name: str(v("vessel_name")) ?? str(vg("name", vrow)),
      call_sign: str(v("call_sign")) ?? str(vg("call_sign", vrow)),
      flag: str(v("flag")) ?? str(vg("flag", vrow)),
      ship_type: str(v("ship_type")) ?? str(vg("ship_type", vrow)),
      length_ais_m: num(v("length_ais_m")) ?? num(vg("length_ais_m", vrow)),
      identity_source: str(v("identity_source")) ?? (vrow !== null ? str(vg("identity_source", vrow)) : null),
      nearest_ais_mmsi: nearestMmsi,
      nearest_ais_dist_m: num(v("nearest_ais_dist_m")),
      nearest_ais_dt_s: num(v("nearest_ais_dt_s")),
      n_ais_10km: num(v("n_ais_10km")),
      ais_reach: num(v("ais_reach")),
      research_only: bool(v("research_only")) ?? false,
      caveat: str(v("caveat")) ?? this.caveat(),
      view: (v("view") as Contact["view"]) ?? "regional",
      dark_lead: bool(v("dark_lead")),
      vessel_key: v("ais_status") === "matched" ? (vesselKey ?? (mmsi ? "mmsi:" + mmsi : null)) : null,
      nearest_vessel_key: nearestKey && str(vg("mmsi", nrow)) === nearestMmsi ? nearestKey : nearestMmsi ? "mmsi:" + nearestMmsi : null,
      nearest_vessel_name: str(rec.nearest_ais_name) ?? (str(vg("mmsi", nrow)) === nearestMmsi ? str(vg("name", nrow)) : null),
      scene_id: str(v("scene_id")),
      pass_id: str(v("pass_id")),
      pass_dir: str(v("pass_dir")),
      orbit_rel: num(v("orbit_rel")),
      inc_angle_deg: num(v("inc_angle_deg")),
      pol_class: str(v("pol_class")),
      n_pixels: num(v("n_pixels")),
      scr_vv_db: num(v("scr_vv_db")),
      scr_vh_db: num(v("scr_vh_db")),
      low_reason: str(v("low_reason")),
      persist_dates: num(v("persist_dates")),
      persist_dates_checked: num(v("persist_dates_checked")),
      n_low_1km: num(v("n_low_1km")),
      near_fixed_m: num(v("near_fixed_m")),
      match_gate_m: num(v("match_gate_m")),
      ais_sog_kn: num(v("ais_sog_kn")),
      length_ratio: num(v("length_ratio")),
      ais_class: str(v("ais_class")),
      ais_footprint_positions: num(v("ais_footprint_positions")),
      ais_recorded_hours: num(v("ais_recorded_hours")),
      cnn_threshold: num(v("cnn_threshold")),
      cnn_model_id: str(v("cnn_model_id")),
      cnn_chip_valid_frac: num(v("cnn_chip_valid_frac")),
      bg_vv_db: num(v("bg_vv_db")),
      bg_vh_db: num(v("bg_vh_db")),
      wind_ms: num(v("wind_ms")),
      ctt_k: num(v("ctt_k")),
      deep_convection: bool(v("deep_convection")),
      cell_id: cellIdOf(lon, lat),
      lead_ids: leadIds,
      chip: chips && chips[det_id] ? det_id : null,
      synthetic: bool(v("synthetic")),
      src: str(v("src")) ?? "det_regional",
      prov,
      extra: (rec.extra as Record<string, unknown>) || undefined,
    };
    // Fields that are not bulk columns, and identity strings that differ from the vessel row (contract behaviour rule 6),
    // are in the record: the record wins.
    for (const [k, val] of Object.entries(rec)) if (!RECORD_META_KEYS.has(k) && val !== undefined) (c as Record<string, unknown>)[k] = val;
    // A research vessel key is `<source>:<vessel_id>`; its id is the D1 field `<source>_vessel_id` (absent in the open build).
    if (vesselKey && !vesselKey.startsWith("mmsi:") && c.ais_status === "matched") {
      const [pfx, ...rest] = vesselKey.split(":");
      const f = `${pfx}_vessel_id`;
      if (c[f] === undefined) (c as Record<string, unknown>)[f] = rest.join(":");
    }
    c.object_context = contextOf(d, i, rec, this.cache, "ctx:contacts");
    return c;
  }

  private chipsPart(): Record<string, string> | null {
    if (!this.cache.has("chips-raw")) this.cache.set("chips-raw", readPartJson<Record<string, string>>("chips"));
    return this.cache.get("chips-raw") as Record<string, string> | null;
  }

  async contacts(q: ListQuery = {}): Promise<ListResult<Contact>> {
    const d = this.part("contacts");
    if (!d) return { items: [], total: 0 };
    const limit = q.limit ?? 100;
    const offset = q.offset ?? 0;
    const statuses = typeof q.ais_status === "string" ? String(q.ais_status).split(",") : null;
    const t0 = q.t0 ? Date.parse(q.t0) : null;
    const t1 = q.t1 ? Date.parse(q.t1) : null;
    const time = timeMs(d.part.columns.acq_utc, d.n);
    const rows: number[] = [];
    for (let i = 0; i < d.n; i++) {
      if (statuses && !statuses.includes(String(d.get.ais_status(i)))) continue;
      if (q.pass_id && String(d.get.pass_id?.(i)) !== q.pass_id) continue;
      if (q.view && String(d.get.view?.(i)) !== q.view) continue;
      if (q.confidence && String(d.get.confidence(i)) !== q.confidence) continue;
      if (q.mmsi && String(d.get.mmsi?.(i) ?? "") !== q.mmsi) continue;
      if (t0 !== null && !(time[i] >= t0)) continue;
      if (t1 !== null && !(time[i] <= t1)) continue;
      if (q.bbox) {
        const [w, s, e, n] = q.bbox;
        const lo = d.get.lon(i) as number;
        const la = d.get.lat(i) as number;
        if (lo < w || lo > e || la < s || la > n) continue;
      }
      rows.push(i);
    }
    const sort = String(q.sort || "-acq_utc");
    const desc = sort.startsWith("-");
    const key = sort.replace(/^-/, "");
    rows.sort((a, b) => {
      const va = key === "acq_utc" ? time[a] : (num(d.get[key]?.(a)) ?? -Infinity);
      const vb = key === "acq_utc" ? time[b] : (num(d.get[key]?.(b)) ?? -Infinity);
      return desc ? vb - va : va - vb;
    });
    return { items: rows.slice(offset, offset + limit).map((i) => this.contactFromRow(i)), total: rows.length };
  }

  async contact(det_id: string): Promise<Contact | null> {
    const i = this.index("contacts", "det_id").get(det_id);
    return i === undefined ? null : this.contactFromRow(i);
  }

  async contactPoints(): Promise<PointLayerData | null> {
    const d = this.part("contacts");
    return d ? pointsFromDecoded(d, "contacts") : null;
  }

  // ------------------------------------------------------------------ vessels
  private vesselFromRow(i: number): Vessel {
    const d = this.part("vessels")!;
    const v = (name: string) => (d.get[name] ? d.get[name](i) : null);
    const mmsi = str(v("mmsi"));
    const key = str(v("vessel_key")) ?? (mmsi ? "mmsi:" + mmsi : "row:" + i);
    const rec = d.records[key] || {};
    return {
      vessel_key: key,
      mmsi,
      mid: num(v("mid")) ?? (mmsi ? parseInt(mmsi.slice(0, 3), 10) : null),
      flag: str(v("flag")),
      name: str(v("name")),
      call_sign: str(v("call_sign")),
      imo: str(v("imo")),
      ais_class: (v("ais_class") as Vessel["ais_class"]) ?? null,
      ship_type: str(v("ship_type")),
      gear_type: str(v("gear_type")),
      identity_kind: str(v("identity_kind")),
      length_m: num(v("length_m")),
      width_m: num(v("width_m")),
      length_ais_m: num(v("length_ais_m")) ?? num(v("length_m")),
      tonnage_gt: num(v("tonnage_gt")),
      identity_source: str(v("identity_source")),
      destination: str(v("destination")),
      eta: str(v("eta")),
      first_seen_utc: str(v("first_seen_utc")),
      last_seen_utc: str(v("last_seen_utc")),
      static_seen_utc: str(v("static_seen_utc")),
      n_positions: num(v("n_positions")),
      n_messages: num(v("n_messages")),
      last_lon: num(v("last_lon")),
      last_lat: num(v("last_lat")),
      sog_kn: num(v("sog_kn")),
      cog_deg: num(v("cog_deg")),
      heading: num(v("heading")),
      nav_status_label: str(v("nav_status_label")),
      in_aoi: bool(v("in_aoi")),
      ever_in_aoi: bool(v("ever_in_aoi")),
      gear_beacon_like: bool(v("gear_beacon_like")),
      registry_sources: str(v("registry_sources")),
      dataset_version: str(v("dataset_version")),
      stub: bool(v("stub")) ?? false,
      contacts_matched: this.contactsMatchedTo(i),
      identity_note: str(v("identity_note")) ?? "Identity fields are self-reported by the transponder; they can be wrong, reused or spoofed.",
      research_only: bool(v("research_only")) ?? false,
      caveat: str(v("caveat")) ?? this.caveat(),
      src: str(v("src")) ?? "aisstream",
      prov: { ...(d.part.prov || {}), ...((d.part.prov_by_src as Record<string, Record<string, string>> | undefined)?.[str(v("src")) ?? "aisstream"] || {}), ...((rec.prov as Record<string, string>) || {}) },
    };
  }

  private contactsMatchedTo(vesselRow: number): string[] {
    const key = "idx:matched-by-vessel";
    let m = this.cache.get(key) as Map<number, string[]> | undefined;
    if (!m) {
      m = new Map();
      const d = this.part("contacts");
      if (d && d.get.vessel_ref) {
        for (let i = 0; i < d.n; i++) {
          const r = d.get.vessel_ref(i) as number | null;
          if (r === null) continue;
          const arr = m.get(r) || [];
          arr.push(String(d.get.det_id(i)));
          m.set(r, arr);
        }
      }
      this.cache.set(key, m);
    }
    return m.get(vesselRow) || [];
  }

  async vessels(q: ListQuery = {}): Promise<ListResult<Vessel>> {
    const d = this.part("vessels");
    if (!d) return { items: [], total: 0 };
    const limit = q.limit ?? 100;
    const offset = q.offset ?? 0;
    const needle = q.q ? String(q.q).toLowerCase() : null;
    const rows: number[] = [];
    for (let i = 0; i < d.n; i++) {
      if (q.in_aoi !== undefined && d.get.in_aoi && d.get.in_aoi(i) !== (q.in_aoi === true || q.in_aoi === "true")) continue;
      if (q.ais_class && d.get.ais_class(i) !== q.ais_class) continue;
      if (needle) {
        const hay = [d.get.name?.(i), d.get.call_sign?.(i), d.get.mmsi?.(i), d.get.imo?.(i)].map((x) => String(x ?? "").toLowerCase());
        if (!hay.some((h) => h.includes(needle))) continue;
      }
      rows.push(i);
    }
    return { items: rows.slice(offset, offset + limit).map((i) => this.vesselFromRow(i)), total: rows.length };
  }

  async vessel(vessel_key: string): Promise<Vessel | null> {
    const i = this.index("vessels", "vessel_key").get(vessel_key);
    return i === undefined ? null : this.vesselFromRow(i);
  }

  async vesselPoints(): Promise<PointLayerData | null> {
    const d = this.part("vessels");
    return d ? pointsFromDecoded(d, "vessels") : null;
  }

  async track(vessel_key: string): Promise<Track | null> {
    const d = this.part("vessels");
    const row = this.index("vessels", "vessel_key").get(vessel_key);
    if (!d || row === undefined) return null;
    const tracks = d.part.tracks as GeomSpec | undefined;
    if (!tracks) return null;
    const key = "geom:tracks";
    if (!this.cache.has(key)) this.cache.set(key, decodeGeom(tracks));
    const layer = this.cache.get(key) as GeoLayer;
    const refs = layer.props.vessel_ref;
    if (!refs) return null;
    for (let j = 0; j < layer.n; j++) {
      if (refs(j) !== row) continue;
      const r0 = layer.feat[j];
      const r1 = j + 1 < layer.feat.length ? layer.feat[j + 1] : layer.ring.length;
      const points: Track["points"] = [];
      const nv = layer.xy.length / 2;
      for (let r = r0; r < r1; r++) {
        const v0 = layer.ring[r];
        const v1 = r + 1 < layer.ring.length ? layer.ring[r + 1] : nv;
        for (let v = v0; v < v1; v++) points.push({ t: null, lon: layer.xy[2 * v] / 10000, lat: layer.xy[2 * v + 1] / 10000 });
      }
      return {
        vessel_key, points, gaps: [],
        start_utc: str(layer.props.start_utc?.(j)), end_utc: str(layer.props.end_utc?.(j)), simplified: true,
        note: (layer.note || "simplified for display") + ". Per-position times and gap detection are in the local app. " + GAP_NOTE,
      };
    }
    return null;
  }

  // ------------------------------------------------------------------ lights
  private lightFromRow(i: number): Light {
    const d = this.part("lights")!;
    const v = (name: string) => (d.get[name] ? d.get[name](i) : null);
    const lon = num(v("lon")) ?? 0;
    const lat = num(v("lat")) ?? 0;
    const light_id = String(v("light_id"));
    const rec = d.records[light_id] || {};
    return {
      light_id,
      satellite: (v("satellite") as Light["satellite"]) ?? "S-NPP",
      time_utc: String(v("time_utc") ?? ""),
      night: String(v("night") ?? ""),
      lon, lat,
      radiance_nw: num(v("radiance_nw")) ?? 0,
      spike_nw: num(v("spike_nw")),
      isolation: num(v("isolation")),
      quality: (v("quality") as Light["quality"]) ?? "clear",
      class: String(v("class") ?? ""),
      nights_seen_500m: num(v("nights_seen_500m")) ?? 0,
      clear_nights_cell: num(v("clear_nights_cell")) ?? 0,
      moon_illum_pct: num(v("moon_illum_pct")),
      satlas_infra_m: num(v("satlas_infra_m")),
      s1_passes_90d: num(v("s1_passes_90d")) ?? 0,
      site_id: str(v("site_id")),
      contacts_2km_same_night: this.contactsNear(lon, lat, String(v("night") ?? ""), 2000),
      cell_id: cellIdOf(lon, lat),
      research_only: false,
      caveat: str(v("caveat")) ?? this.caveat(),
      src: str(v("src")) ?? "viirs_dnb",
      prov: { ...(d.part.prov || {}), ...((rec.prov as Record<string, string>) || {}) },
      object_context: contextOf(d, i, rec, this.cache, "ctx:lights"),
    };
  }

  /** Contacts within `radius` m whose local evening date (UTC+7) equals `night`. */
  private contactsNear(lon: number, lat: number, night: string, radius: number): string[] {
    const d = this.part("contacts");
    if (!d) return [];
    const out: string[] = [];
    const time = timeMs(d.part.columns.acq_utc, d.n);
    const dlat = radius / 111320;
    for (let i = 0; i < d.n; i++) {
      const la = d.get.lat(i) as number;
      if (Math.abs(la - lat) > dlat) continue;
      const lo = d.get.lon(i) as number;
      if (Math.abs(lo - lon) > dlat / Math.cos((lat * Math.PI) / 180)) continue;
      if (haversineM(lat, lon, la, lo) > radius) continue;
      if (night) {
        const t = new Date(time[i] + 7 * 3600e3 - 12 * 3600e3); // evening date: shift by -12 h so a 02:00 local pass belongs to the previous evening
        const nd = t.toISOString().slice(0, 10);
        if (nd !== night) continue;
      }
      out.push(String(d.get.det_id(i)));
    }
    return out;
  }

  async lights(q: ListQuery = {}): Promise<ListResult<Light>> {
    const d = this.part("lights");
    if (!d) return { items: [], total: 0 };
    const limit = q.limit ?? 100;
    const offset = q.offset ?? 0;
    const rows: number[] = [];
    for (let i = 0; i < d.n; i++) {
      if (q.night && d.get.night(i) !== q.night) continue;
      if (q.quality && d.get.quality(i) !== q.quality) continue;
      rows.push(i);
    }
    return { items: rows.slice(offset, offset + limit).map((i) => this.lightFromRow(i)), total: rows.length };
  }

  async light(light_id: string): Promise<Light | null> {
    const i = this.index("lights", "light_id").get(light_id);
    return i === undefined ? null : this.lightFromRow(i);
  }

  async lightPoints(): Promise<PointLayerData | null> {
    const d = this.part("lights");
    return d ? pointsFromDecoded(d, "lights") : null;
  }

  // ------------------------------------------------------------------ events (pending in the open build)
  async events(): Promise<ListResult<EventRecord>> {
    const raw = readPartJson<{ records?: EventRecord[] }>("events");
    const items = raw?.records || [];
    return { items, total: items.length };
  }

  async event(event_id: string): Promise<EventRecord | null> {
    const { items } = await this.events();
    return items.find((e) => e.event_id === event_id) || null;
  }

  // ------------------------------------------------------------------ leads
  private primaryId(primaryType: string, row: number): string {
    const d = this.part(primaryType);
    if (!d) return `${primaryType}:${row}`;
    const g = primaryType === "contacts" ? d.get.det_id : primaryType === "lights" ? d.get.light_id : primaryType === "vessels" ? d.get.vessel_key : d.get.cell_id;
    return g ? String(g(row)) : `${primaryType}:${row}`;
  }

  private leadFromRow(i: number, resolvePrimary = true): Lead | null {
    const d = this.part("leads");
    if (!d) return null;
    const v = (name: string) => (d.get[name] ? d.get[name](i) : null);
    const lead_type = String(v("lead_type") ?? "L1");
    const primaryType = String(v("primary_type") ?? "contacts");
    const prow = num(v("primary")) ?? 0;
    const primary_id = this.primaryId(primaryType, prow);
    const lead_id = `${lead_type}-${primary_id}`;
    const rec = d.records[lead_id] || {};
    const factorsSpec = (d.part.factors as { factor: string; column: string; max_points: number; source: string }[]) || [];
    // A record's full factor list (with each factor's value text) wins over the per-factor point columns (reading 6).
    const recFactors = Array.isArray(rec.factors) ? (rec.factors as LeadFactor[]).filter((f) => f && typeof f.factor === "string") : null;
    const factors: LeadFactor[] = recFactors && recFactors.length ? recFactors.map((f) => ({ ...f, value: f.value ?? null, points: num(f.points) ?? 0, max_points: num(f.max_points) ?? 0, source: String(f.source ?? "app") })) : factorsSpec.map((f) => ({
      factor: f.factor, value: null, points: num(v(f.column)) ?? 0, max_points: f.max_points, source: f.source,
    }));
    const priority = num(v("priority")) ?? factors.reduce((s, f) => s + f.points, 0);
    const over = this.stateOverride.get(lead_id);
    const history = [...(((rec.history as Decision[]) || [])), ...(over?.history || [])];
    const state = (over?.state ?? (v("state") as LeadState)) || "new";
    const reason = over ? over.reason : str(v("reason"));
    const lawfulByType = listsByType(d.part.lawful_explanations);
    const changeByType = listsByType(d.part.change_indicators);
    const provByType = (d.part.prov_by_type as Record<string, Record<string, string>> | undefined)?.[lead_type] || {};
    const lon = num(v("lon")) ?? 0;
    const lat = num(v("lat")) ?? 0;
    const ptype = (primaryType.replace(/s$/, "") as Lead["primary_type"]);
    let primary: Contact | null = null;
    if (resolvePrimary && primaryType === "contacts") {
      const c = this.part("contacts");
      if (c && prow < c.n) primary = this.contactFromRow(prow);
    }
    const box = str(v("region_box"));
    const title = str(rec.title) ?? `${LEAD_TYPE_NAME[lead_type] || lead_type}${primary?.length_est_m ? `, ${Math.round(primary.length_est_m)} m` : ""}${box ? `, ${box}` : ""}`;
    return {
      lead_id, lead_type, title, state, reason, priority, priority_band: priorityBand(priority), factors,
      priority_model_id: str(v("priority_model_id")) ?? this.metaCache?.priority_model_id ?? "unknown",
      calibrated: bool(v("calibrated")) ?? false,
      primary_type: ptype, primary_id,
      evidence: (rec.evidence as Lead["evidence"]) || [{ type: ptype, id: primary_id, role: "primary" }],
      lon, lat,
      time_utc: str(v("time_utc")) ?? primary?.acq_utc ?? "",
      region_box: box,
      next_look_utc: str(v("next_look_utc")),
      lawful_explanations: (rec.lawful_explanations as string[]) || lawfulByType[lead_type] || LEAD_LAWFUL[lead_type] || [],
      change_indicators: (rec.change_indicators as string[]) || changeByType[lead_type] || ["late_ais_match", "next_radar_look", "optical_view"],
      history,
      synthetic: bool(v("synthetic")),
      ais_status: primary?.ais_status ?? null,
      cnn_score: primary?.cnn_score ?? null,
      length_est_m: primary?.length_est_m ?? null,
      pass_id: primary?.pass_id ?? null,
      stale_reason: primary ? staleLeadReason({ lead_type, primary_type: ptype }, primary) : null,
      research_only: bool(v("research_only")) ?? false,
      caveat: str(v("caveat")) ?? this.caveat(),
      src: str(v("src")) ?? "app",
      prov: { priority: "app", factors: "app", ...provByType, ...((rec.prov as Record<string, string>) || {}) },
      extra: rec.synthetic_note ? { synthetic_note: rec.synthetic_note, weather: rec.weather } : undefined,
    };
  }

  async leads(q: ListQuery = {}): Promise<ListResult<Lead>> {
    const d = this.part("leads");
    if (!d) return { items: [], total: 0 };
    const states = q.state === undefined ? ["new", "reviewing"] : q.state === "" || q.state === "all" ? null : String(q.state).split(",");
    const all: Lead[] = [];
    for (let i = 0; i < d.n; i++) {
      const L = this.leadFromRow(i);
      if (!L) continue;
      // A lead that no longer stands against its primary contact (built before a rematch) is never listed as a lead;
      // its own page (lead()) still opens and says why.
      if (L.stale_reason) continue;
      if (states && !states.includes(L.state)) continue;
      if (q.lead_type && L.lead_type !== q.lead_type) continue;
      if (q.region_box && L.region_box !== q.region_box) continue;
      if (q.pass_id && L.pass_id !== q.pass_id) continue;
      if (q.min_priority !== undefined && L.priority < Number(q.min_priority)) continue;
      all.push(L);
    }
    const sort = String(q.sort || "-priority");
    const desc = sort.startsWith("-");
    const key = sort.replace(/^-/, "") as keyof Lead;
    all.sort((a, b) => {
      const va = a[key] as number | string;
      const vb = b[key] as number | string;
      const r = va < vb ? -1 : va > vb ? 1 : 0;
      return desc ? -r : r;
    });
    const offset = q.offset ?? 0;
    const limit = q.limit ?? 100;
    return { items: all.slice(offset, offset + limit), total: all.length };
  }

  async lead(lead_id: string): Promise<Lead | null> {
    const d = this.part("leads");
    if (!d) return null;
    for (let i = 0; i < d.n; i++) {
      const L = this.leadFromRow(i);
      if (L && L.lead_id === lead_id) return L;
    }
    return null;
  }

  private applyDecision(dec: Decision, persist: boolean): void {
    const prev = this.stateOverride.get(dec.lead_id);
    const history = [...(prev?.history || []), dec];
    this.stateOverride.set(dec.lead_id, { state: dec.to_state, reason: dec.reason, history });
    this.decisions.push(dec);
    if (persist) writeJson("decisions", this.decisions);
  }

  async decide(lead_id: string, to_state: LeadState, reason: string | null, note: string | null, user: string): Promise<Lead> {
    const current = await this.lead(lead_id);
    if (!current) throw new Error("lead not found: " + lead_id);
    const from = current.state;
    const closing = to_state.startsWith("closed");
    if ((to_state === "closed_explained" || to_state === "closed_false_alarm") && !reason) throw new Error("a reason is required to close as " + to_state);
    if (to_state === "closed_unexplained" && !note) throw new Error("a note is required to close as unexplained");
    if (reason && reason.startsWith("other") && !note) throw new Error("a note is required with reason other");
    if (from.startsWith("closed") && to_state !== "reviewing") throw new Error("a closed lead can only be reopened to reviewing");
    if (from.startsWith("closed") && !note) throw new Error("reopening needs a note");
    if (from === to_state && !closing) return current;
    const meta = await this.meta();
    const dec: Decision = {
      lead_id, time_utc: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), user, from_state: from, to_state, reason, note,
      build: meta.build, app_version: meta.app_version || "frontend",
    };
    this.applyDecision(dec, true);
    this.cache.delete("idx:leads-by-primary");
    return (await this.lead(lead_id))!;
  }

  decisionLog(): Decision[] {
    return [...this.decisions];
  }

  // ------------------------------------------------------------------ passes
  private passList(): Pass[] {
    const key = "passes:list";
    if (this.cache.has(key)) return this.cache.get(key) as Pass[];
    const raw = readPartJson<{ features?: { geometry: unknown; properties: Record<string, unknown> }[]; caveat?: string; notes?: Record<string, string> }>("passes");
    const partCaveat = raw?.caveat;
    const list: Pass[] = (raw?.features || []).map((f) => {
      const p = f.properties || {};
      return {
        pass_id: String(p.pass_id), mission: (p.mission as Pass["mission"]) ?? "S1C", relative_orbit: num(p.relative_orbit),
        pass_dir: str(p.pass_dir), start_utc: String(p.start_utc), stop_utc: String(p.stop_utc ?? p.start_utc),
        status: (p.status as Pass["status"]) ?? "past", sources: (p.sources as string[]) || [], footprint: f.geometry ?? null,
        aoi_overlap_km2: num(p.aoi_overlap_km2), aoi_parts: (p.aoi_parts as string[]) ?? null, aoi_overlap_bbox: (p.aoi_overlap_bbox as number[]) ?? null,
        scenes: (p.scenes as string[]) || [], processed: Boolean(p.processed), n_contacts: (p.n_contacts as Record<string, number>) ?? null,
        ais_aoi_positions: num(p.ais_aoi_positions), ais_aoi_mmsi: num(p.ais_aoi_mmsi), ais_footprint_positions: num(p.ais_footprint_positions),
        ais_footprint_mmsi: num(p.ais_footprint_mmsi), ais_near_footprint_mmsi: num(p.ais_near_footprint_mmsi), ais_heard_share: num(p.ais_heard_share),
        scene_counts: (p.scene_counts as Record<string, unknown>[]) || ((p.extra as Record<string, unknown> | undefined)?.scene_counts as Record<string, unknown>[]) || undefined,
        note: str(p.note), fixture_note: str(p.fixture_note), contacts_in_bundle: bool(p.contacts_in_bundle),
        research_only: bool(p.research_only) ?? false, caveat: str(p.caveat) ?? partCaveat ?? this.caveat(), src: str(p.src) ?? "esa_acq_plan", prov: (p.prov as Record<string, string>) || {},
        extra: (p.extra as Record<string, unknown>) || undefined,
        n_ais_only: num(p.n_ais_only) ?? (Array.isArray(p.ais_only) ? p.ais_only.length : null),
        ais_only: Array.isArray(p.ais_only) ? (p.ais_only as Pass["ais_only"]) : null,
        azimuth_check: (p.azimuth_check as Pass["azimuth_check"]) ?? null,
        identity_label: str(p.identity_label),
      };
    });
    this.cache.set(key, list);
    return list;
  }

  async passes(q: ListQuery = {}): Promise<ListResult<Pass>> {
    let list = this.passList();
    if (q.status) list = list.filter((p) => p.status === q.status);
    if (q.mission) list = list.filter((p) => p.mission === q.mission);
    list = [...list].sort((a, b) => Date.parse(a.start_utc) - Date.parse(b.start_utc));
    return { items: list, total: list.length };
  }

  async pass(pass_id: string): Promise<Pass | null> {
    return this.passList().find((p) => p.pass_id === pass_id) || null;
  }

  // ------------------------------------------------------------------ geo
  private geoRaw(): { layers?: Record<string, GeomSpec>; [k: string]: unknown } | null {
    if (!this.cache.has("geo-raw")) this.cache.set("geo-raw", readPartJson("geo"));
    return this.cache.get("geo-raw") as { layers?: Record<string, GeomSpec> } | null;
  }

  async geo(name: string): Promise<GeoLayer | null> {
    const key = "geo:" + name;
    if (this.cache.has(key)) return this.cache.get(key) as GeoLayer | null;
    const raw = this.geoRaw();
    const spec = raw?.layers?.[name];
    const layer = spec ? decodeGeom(spec) : null;
    this.cache.set(key, layer);
    return layer;
  }

  async geoNote(name: string): Promise<string | null> {
    const raw = this.geoRaw();
    return (raw?.layers?.[name]?.note as string) ?? null;
  }

  // ------------------------------------------------------------------ search
  async search(q: string, limit = 20): Promise<SearchResponse> {
    const parsed = parseQuery(q);
    const results: SearchResult[] = [];
    const push = (r: SearchResult) => results.length < limit && results.push(r);
    if (parsed.kind === "point" && parsed.lon !== undefined && parsed.lat !== undefined) {
      push({ type: "point", id: parsed.value, label: "Go to point " + parsed.value, sublabel: parsed.interpretation || "", lon: parsed.lon, lat: parsed.lat, score: 1 });
      return { results, interpretation: parsed.interpretation || null };
    }
    const contacts = this.part("contacts");
    const vessels = this.part("vessels");
    const lights = this.part("lights");
    if (parsed.kind === "det_id" && contacts) {
      const idx = this.index("contacts", "det_id");
      const exact = idx.get(parsed.value);
      if (exact !== undefined) {
        const c = this.contactFromRow(exact);
        push({ type: "contact", id: c.det_id, label: c.det_id, sublabel: `radar contact, ${AIS_STATUS_LABEL[c.ais_status]}`, lon: c.lon, lat: c.lat, score: 1 });
      }
    }
    if ((parsed.kind === "mmsi" || parsed.kind === "imo" || parsed.kind === "text") && vessels) {
      const needle = parsed.value.toLowerCase();
      const scored: { i: number; s: number }[] = [];
      for (let i = 0; i < vessels.n; i++) {
        const mmsi = String(vessels.get.mmsi?.(i) ?? "");
        const imo = String(vessels.get.imo?.(i) ?? "");
        const name = String(vessels.get.name?.(i) ?? "").toLowerCase();
        const cs = String(vessels.get.call_sign?.(i) ?? "").toLowerCase();
        let s = 0;
        if (parsed.kind === "mmsi") s = mmsi === needle ? 1 : 0;
        else if (parsed.kind === "imo") s = imo === needle ? 1 : 0;
        else if (name === needle || cs === needle) s = 1;
        else if (name.startsWith(needle) || cs.startsWith(needle)) s = 0.8;
        else if (name.includes(needle) || mmsi.startsWith(needle)) s = 0.5;
        if (s > 0) scored.push({ i, s });
      }
      scored.sort((a, b) => b.s - a.s);
      for (const { i, s } of scored.slice(0, limit)) {
        const v = this.vesselFromRow(i);
        push({ type: "vessel", id: v.vessel_key, label: v.name || v.vessel_key, sublabel: `MMSI ${v.mmsi ?? "unknown"}${v.call_sign ? `, call sign ${v.call_sign}` : ""}${v.ship_type ? `, ${v.ship_type}` : ""}`, lon: v.last_lon, lat: v.last_lat, score: s });
      }
    }
    if (parsed.kind === "text" && contacts && parsed.value.length >= 4) {
      const needle = parsed.value.toUpperCase();
      const idx = this.index("contacts", "det_id");
      let n = 0;
      for (const [id, i] of idx) {
        if (!id.startsWith(needle)) continue;
        const c = this.contactFromRow(i);
        push({ type: "contact", id, label: id, sublabel: `radar contact, ${AIS_STATUS_LABEL[c.ais_status]}`, lon: c.lon, lat: c.lat, score: 0.6 });
        if (++n >= 5) break;
      }
    }
    if (parsed.kind === "light_id" && lights) {
      const i = this.index("lights", "light_id").get(parsed.value);
      if (i !== undefined) {
        const L = this.lightFromRow(i);
        push({ type: "light", id: L.light_id, label: L.light_id, sublabel: `VIIRS light, ${L.satellite}, ${L.quality}`, lon: L.lon, lat: L.lat, score: 1 });
      }
    }
    if (parsed.kind === "lead_id" || parsed.kind === "text") {
      const { items } = await this.leads({ state: "all", limit: 10000 });
      for (const L of items) {
        if (parsed.kind === "lead_id" ? L.lead_id.toLowerCase().startsWith(parsed.value.toLowerCase()) : L.lead_id.toLowerCase().includes(parsed.value.toLowerCase())) {
          push({ type: "lead", id: L.lead_id, label: L.lead_id, sublabel: L.title, lon: L.lon, lat: L.lat, score: 0.9 });
        }
      }
    }
    if (parsed.kind === "pass_id" || parsed.kind === "text") {
      for (const p of this.passList()) {
        if (p.pass_id.toLowerCase().includes(parsed.value.toLowerCase())) {
          push({ type: "pass", id: p.pass_id, label: p.pass_id, sublabel: `${p.mission} pass, ${p.status}, ${p.start_utc}`, lon: null, lat: null, score: 0.7 });
        }
      }
    }
    return { results, interpretation: parsed.interpretation || null };
  }

  // ------------------------------------------------------------------ timeline
  async timeline(): Promise<TimelineRows> {
    const passes = (await this.passes()).items;
    const contacts = this.part("contacts");
    const byPass = new Map<string, { start: number; counts: Record<string, number> }>();
    if (contacts) {
      const time = timeMs(contacts.part.columns.acq_utc, contacts.n);
      for (let i = 0; i < contacts.n; i++) {
        const pid = String(contacts.get.pass_id?.(i) ?? contacts.get.run_id(i));
        const st = String(contacts.get.ais_status(i));
        const e = byPass.get(pid) || { start: time[i], counts: {} };
        e.start = Math.min(e.start, time[i]);
        e.counts[st] = (e.counts[st] || 0) + 1;
        byPass.set(pid, e);
      }
    }
    const meta = await this.meta();
    const rec = meta.ais_recording;
    const aisHours: TimelineRows["aisHours"] = [];
    const aisGaps: TimelineRows["aisGaps"] = [];
    if (rec?.period_start_utc && rec.period_end_utc) {
      let cursor = rec.period_start_utc;
      const gaps = (rec.gaps || []) as { start_utc?: string; end_utc?: string; from_utc?: string; to_utc?: string; minutes?: number }[];
      for (const g of gaps) {
        const gs = g.start_utc || g.from_utc;
        const ge = g.end_utc || g.to_utc;
        if (!gs || !ge) continue;
        aisHours.push({ start_utc: cursor, end_utc: gs });
        aisGaps.push({ start_utc: gs, end_utc: ge, label: "recorder gap" });
        cursor = ge;
      }
      aisHours.push({ start_utc: cursor, end_utc: rec.period_end_utc });
    }
    const lights = this.part("lights");
    const nights = new Map<string, number>();
    if (lights) for (let i = 0; i < lights.n; i++) nights.set(String(lights.get.night(i)), (nights.get(String(lights.get.night(i))) || 0) + 1);
    return {
      passes,
      contactsByPass: [...byPass.entries()].map(([pass_id, e]) => ({ pass_id, start_utc: new Date(e.start).toISOString(), counts: e.counts })),
      aisHours, aisGaps,
      viirsNights: [...nights.entries()].map(([night, n]) => ({ night, n })).sort((a, b) => a.night.localeCompare(b.night)),
      events: (await this.events()).items,
    };
  }

  async chip(det_id: string, _ref?: string | null): Promise<string | null> {
    void _ref;
    const chips = this.chipsPart();
    return chips && chips[det_id] ? chips[det_id] : null;
  }

  /**
   * Cell context from the `cells` part (README reading 11): columnar static fields keyed by `cell_id` (or `row` and
   * `col`); a `nightly` block (columns parallel to the cells, plus `valid` values that hold for every cell); an
   * `eez_attrs` block (Marine Regions attributes as published, returned under `eez`); an `expected_activity` block
   * (counts per target) and, per cell, `records[cell_id].expected_activity` with the board D5.4 rows when embedded.
   */
  async cell(cell_id: string): Promise<CellRecord | null> {
    const d = this.part("cells");
    if (!d) return null;
    let i = d.get.cell_id ? this.index("cells", "cell_id").get(cell_id) : undefined;
    if (i === undefined && d.get.row && d.get.col) {
      const m = /^r(\d+)c(\d+)$/.exec(cell_id);
      if (m) for (let k = 0; k < d.n; k++) if (Number(d.get.row(k)) === Number(m[1]) && Number(d.get.col(k)) === Number(m[2])) { i = k; break; }
    }
    if (i === undefined) return null;
    const row = i;
    const rec: Record<string, unknown> = {};
    for (const [k, g] of Object.entries(d.get)) rec[k] = g(row);
    const extra = d.records[cell_id] || {};
    const partProv = (d.part as unknown as { prov?: Record<string, string> }).prov || {};
    const block = (name: string) => {
      const b = d.part[name] as { n?: number; columns?: ColumnarPart["columns"] } | undefined;
      if (!b || !b.columns) return null;
      const key = "cells:" + name;
      if (!this.cache.has(key)) this.cache.set(key, decodePart({ type: name, n: b.n ?? d.n, columns: b.columns }));
      return this.cache.get(key) as { get: Record<string, Getter> };
    };
    // nightly fields of the newest night
    let nightly: Record<string, unknown> | null = (extra.nightly as Record<string, unknown>) ?? null;
    const nb = d.part.nightly as { night?: string; valid?: Record<string, unknown> } | undefined;
    const nd = block("nightly");
    if (!nightly && nb && nd) {
      const vals: Record<string, unknown> = { night: nb.night ?? null, ...(nb.valid || {}) };
      let any = false;
      for (const [k, g] of Object.entries(nd.get)) {
        const val = g(row);
        vals[k] = val;
        if (val !== null && val !== undefined) any = true;
      }
      nightly = any ? vals : null;
    }
    // Marine Regions attributes, only ever under `eez` (contract 3.7)
    let eez: Record<string, unknown> | null = (extra.eez as Record<string, unknown>) ?? null;
    const eb = d.part.eez_attrs as { heading?: string; statement?: string } | undefined;
    const ed = block("eez_attrs");
    if (!eez && eb && ed) {
      const vals: Record<string, unknown> = { heading: eb.heading ?? null, statement: eb.statement ?? null };
      let any = false;
      for (const [k, g] of Object.entries(ed.get)) {
        const val = g(row);
        vals[k] = val;
        if (val !== null && val !== undefined) any = true;
      }
      eez = any ? vals : null;
    }
    // expected activity: the record's rows, else the part's counts
    const xb = d.part.expected_activity as { targets?: Record<string, { [c: string]: ColumnarPart["columns"][string] }>; caveat?: string; model_id?: string; note?: string; n?: number } | undefined;
    let expected = normalizeExpected(extra.expected_activity, xb?.caveat);
    if (!expected && xb?.targets) {
      const targets: Record<string, Record<string, unknown>> = {};
      for (const [t, cols] of Object.entries(xb.targets)) {
        const key = "cells:xa:" + t;
        if (!this.cache.has(key)) this.cache.set(key, decodePart({ type: "expected_activity", n: xb.n ?? d.n, columns: cols }));
        const g = (this.cache.get(key) as { get: Record<string, Getter> }).get;
        const counts: Record<string, unknown> = {};
        for (const [c, f] of Object.entries(g)) counts[c] = f(row);
        if (Object.values(counts).some((x) => x !== null && x !== undefined)) targets[t] = counts;
      }
      expected = Object.keys(targets).length ? normalizeExpected({ model_id: xb.model_id ?? null, caveat: xb.caveat, note: xb.note, targets }, xb.caveat) : null;
    }
    const out: CellRecord = {
      ...rec, ...extra, cell_id, row: num(rec.row) ?? 0, col: num(rec.col) ?? 0, lon: num(rec.lon) ?? 0, lat: num(rec.lat) ?? 0,
      region_box: str(rec.region_box ?? rec.region), src: str(rec.src) || "app", prov: { ...partProv, ...((extra.prov as Record<string, string>) || {}) },
      caveat: str(rec.caveat) ?? PRODUCT_CAVEAT, research_only: bool(rec.research_only) ?? false,
      nightly, eez, expected_activity: expected,
    } as CellRecord;
    return out;
  }

  // ------------------------------------------------------------------ rasters (README reading 14)
  private rasterList(): RasterEntry[] {
    if (!this.cache.has("rasters")) {
      const raw = readPartJson<{ layers?: RasterEntry[]; caveat?: string } | RasterEntry[]>("rasters");
      const layers = Array.isArray(raw) ? raw : raw?.layers || [];
      this.cache.set("rasters", layers.filter((l) => l && l.name && l.bounds));
    }
    return this.cache.get("rasters") as RasterEntry[];
  }

  async rasters(): Promise<RasterEntry[]> {
    return this.rasterList().map((l) => ({ ...l, default_on: false }));
  }

  async rasterImage(name: string, theme: "dark" | "light"): Promise<string | null> {
    const l = this.rasterList().find((x) => x.name === name) as (RasterEntry & { image_light?: string | null }) | undefined;
    if (!l) return null;
    return (theme === "light" ? l.image_light : null) || l.image || null;
  }
}
