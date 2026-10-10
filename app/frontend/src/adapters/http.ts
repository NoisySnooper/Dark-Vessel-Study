// HTTP adapter: calls /api/v1 (contract section 5) and unwraps the envelope. Same record shapes as the embedded adapter.
import { decodePart, decodeGeom, type ColumnarPart } from "./columns";
import { pointsFromDecoded } from "./embedded";
import { parseCoordinates } from "./search";
import { normalizeExpected, normalizeObjectContext } from "./context";
import type {
  CellRecord, Contact, DataAdapter, Decision, EventRecord, GeoLayer, Lead, LeadState, Light, ListQuery, ListResult, Meta, Pass, PointLayerData,
  RasterEntry, SearchResponse, TimelineRows, Track, Vessel,
} from "./types";

interface Envelope<T> {
  contract_version: string;
  build: string;
  build_label: string;
  caveat: string;
  generated_utc: string;
  item?: T;
  items?: T[];
  total?: number;
  limit?: number;
  offset?: number;
  error?: { code: string; message: string };
  [k: string]: unknown;
}

// Single records read twice within one page view (the Pass page reads its matched contacts for the result line and again
// for the table, and the nearest-vessel names of its rows) share one request. Entries expire after RECORD_TTL_MS, so a
// backend that reloads its files is seen within a minute; a failed read is never kept.
const RECORD_TTL_MS = 60_000;
const RECORD_CACHE_MAX = 2000;

export class HttpAdapter implements DataAdapter {
  readonly kind = "http" as const;
  private base: string;
  private decisions: Decision[] = [];
  private metaCache: Meta | null = null;
  private records = new Map<string, { t: number; p: Promise<unknown> }>();

  private memo<T>(key: string, load: () => Promise<T>): Promise<T> {
    const now = Date.now();
    const hit = this.records.get(key);
    if (hit && now - hit.t < RECORD_TTL_MS) return hit.p as Promise<T>;
    const p = load();
    p.catch(() => this.records.delete(key));
    if (this.records.size >= RECORD_CACHE_MAX) this.records.delete(this.records.keys().next().value as string);
    this.records.set(key, { t: now, p });
    return p;
  }

  constructor(base = "") {
    this.base = base.replace(/\/$/, "") + "/api/v1";
  }

  private qs(q: ListQuery = {}): string {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) {
      if (v === undefined || v === null || v === "") continue;
      p.set(k, Array.isArray(v) ? v.join(",") : String(v));
    }
    const s = p.toString();
    return s ? "?" + s : "";
  }

  private async get<T>(path: string, q?: ListQuery): Promise<Envelope<T>> {
    const r = await fetch(this.base + path + this.qs(q), { headers: { Accept: "application/json" } });
    const body = (await r.json()) as Envelope<T>;
    if (!r.ok) throw new Error(body?.error?.message || `HTTP ${r.status} on ${path}`);
    return body;
  }

  private async getOrNull<T>(path: string): Promise<T | null> {
    const r = await fetch(this.base + path, { headers: { Accept: "application/json" } });
    if (r.status === 404) return null;
    const body = (await r.json()) as Envelope<T>;
    if (!r.ok) throw new Error(body?.error?.message || `HTTP ${r.status} on ${path}`);
    return (body.item as T) ?? null;
  }

  private list<T>(e: Envelope<T>): ListResult<T> {
    return { items: e.items || [], total: e.total ?? (e.items || []).length };
  }

  async meta(): Promise<Meta> {
    if (this.metaCache) return this.metaCache;
    const e = await this.get<Meta>("/meta");
    const m = (e.item as Meta) || (e as unknown as Meta);
    this.metaCache = { ...m, build: (m.build || e.build) as Meta["build"], build_label: m.build_label || e.build_label, caveat: m.caveat || e.caveat,
      research_label: (e.research_label as string) ?? m.research_label ?? null, attribution: (e.attribution as string) ?? m.attribution ?? null };
    return this.metaCache;
  }

  async contacts(q?: ListQuery): Promise<ListResult<Contact>> {
    return this.list(await this.get<Contact>("/contacts", q));
  }
  async contact(det_id: string): Promise<Contact | null> {
    return this.memo(`contact:${det_id}`, async () => {
      const c = await this.getOrNull<Contact>(`/contacts/${encodeURIComponent(det_id)}`);
      if (c) c.object_context = normalizeObjectContext(c.object_context);
      return c;
    });
  }
  private async cols(name: string, kind: "contacts" | "lights" | "vessels"): Promise<PointLayerData | null> {
    const r = await fetch(`${this.base}/layers/${name}.cols`);
    if (!r.ok) return null;
    const part = (await r.json()) as ColumnarPart & { item?: ColumnarPart };
    const p = part.item || part;
    if (!p.columns) return null;
    return pointsFromDecoded({ ...decodePart(p), part: p }, kind);
  }
  async contactPoints(): Promise<PointLayerData | null> {
    return this.cols("contacts", "contacts");
  }
  async vessels(q?: ListQuery): Promise<ListResult<Vessel>> {
    return this.list(await this.get<Vessel>("/vessels", q));
  }
  async vessel(vessel_key: string): Promise<Vessel | null> {
    return this.memo(`vessel:${vessel_key}`, () => this.getOrNull<Vessel>(`/vessels/${encodeURIComponent(vessel_key)}`));
  }
  async vesselPoints(): Promise<PointLayerData | null> {
    return this.cols("vessels", "vessels");
  }
  async track(vessel_key: string): Promise<Track | null> {
    const r = await fetch(`${this.base}/vessels/${encodeURIComponent(vessel_key)}/track`);
    if (!r.ok) return null;
    const fc = (await r.json()) as { features?: { geometry: { coordinates: number[] }; properties: Record<string, unknown> }[]; gaps?: Track["gaps"]; note?: string };
    const points = (fc.features || []).map((f) => ({
      t: (f.properties?.t as string) ?? null, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1],
      sog_kn: (f.properties?.sog_kn as number) ?? null, cog_deg: (f.properties?.cog_deg as number) ?? null,
    }));
    return { vessel_key, points, gaps: fc.gaps || [], start_utc: points[0]?.t ?? null, end_utc: points[points.length - 1]?.t ?? null, simplified: false, note: fc.note ?? null };
  }
  async lights(q?: ListQuery): Promise<ListResult<Light>> {
    return this.list(await this.get<Light>("/lights", q));
  }
  async light(light_id: string): Promise<Light | null> {
    const l = await this.getOrNull<Light>(`/lights/${encodeURIComponent(light_id)}`);
    if (l) l.object_context = normalizeObjectContext(l.object_context);
    return l;
  }
  async lightPoints(): Promise<PointLayerData | null> {
    return this.cols("lights", "lights");
  }
  async events(q?: ListQuery): Promise<ListResult<EventRecord>> {
    try {
      return this.list(await this.get<EventRecord>("/events", q));
    } catch {
      return { items: [], total: 0 };
    }
  }
  async event(event_id: string): Promise<EventRecord | null> {
    return this.getOrNull<EventRecord>(`/events/${encodeURIComponent(event_id)}`);
  }
  async leads(q?: ListQuery): Promise<ListResult<Lead>> {
    const qq = { ...(q || {}) };
    if (qq.state === "all") qq.state = "new,reviewing,closed_explained,closed_unexplained,closed_false_alarm";
    return this.list(await this.get<Lead>("/leads", qq));
  }
  async lead(lead_id: string): Promise<Lead | null> {
    return this.getOrNull<Lead>(`/leads/${encodeURIComponent(lead_id)}`);
  }
  async decide(lead_id: string, to_state: LeadState, reason: string | null, note: string | null, user: string): Promise<Lead> {
    const r = await fetch(`${this.base}/leads/${encodeURIComponent(lead_id)}/decision`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ to_state, reason, note, user }),
    });
    const body = (await r.json()) as Envelope<Lead>;
    if (!r.ok) throw new Error(body?.error?.message || `HTTP ${r.status}`);
    const lead = body.item as Lead;
    const last = lead.history?.[lead.history.length - 1];
    if (last) this.decisions.push(last);
    return lead;
  }
  decisionLog(): Decision[] {
    return [...this.decisions];
  }
  async passes(q?: ListQuery): Promise<ListResult<Pass>> {
    const r = this.list(await this.get<Pass>("/passes", q));
    return { ...r, items: r.items.map(passShape) };
  }
  async pass(pass_id: string): Promise<Pass | null> {
    const p = await this.getOrNull<Pass>(`/passes/${encodeURIComponent(pass_id)}`);
    return p ? passShape(p) : null;
  }
  async geo(name: string): Promise<GeoLayer | null> {
    const r = await fetch(`${this.base}/geo/${encodeURIComponent(name)}.geojson`);
    if (!r.ok) return null;
    const fc = (await r.json()) as { features?: { geometry: { type: string; coordinates: unknown }; properties?: Record<string, unknown> }[]; note?: string };
    return geoJsonToLayer(fc.features || [], fc.note ?? null);
  }
  async geoNote(name: string): Promise<string | null> {
    const layer = await this.geo(name);
    return layer?.note ?? null;
  }
  async search(q: string, limit = 20): Promise<SearchResponse> {
    const c = parseCoordinates(q);
    if (c && c.lon !== undefined && c.lat !== undefined) {
      return { results: [{ type: "point", id: c.value, label: "Go to point " + c.value, sublabel: c.interpretation || "", lon: c.lon, lat: c.lat, score: 1 }], interpretation: c.interpretation || null };
    }
    const e = await this.get<SearchResponse["results"][number]>("/search", { q, limit });
    return { results: e.items || [], interpretation: (e.interpretation as string) ?? null };
  }
  async timeline(): Promise<TimelineRows> {
    const e = await this.get<TimelineRows>("/timeline");
    const t = (e.item as TimelineRows) || ({} as TimelineRows);
    return { passes: t.passes || [], contactsByPass: t.contactsByPass || [], aisHours: t.aisHours || [], aisGaps: t.aisGaps || [], viirsNights: t.viirsNights || [], events: t.events || [] };
  }
  async chip(det_id: string, ref?: string | null): Promise<string | null> {
    // Contract 3.1: `chip` is the chip URL when cached, null otherwise. Never request a chip the record says is absent
    // (a 404 would log a console error); when the caller does not know the field, read it from the contact record.
    let r = ref;
    if (r === undefined) r = (await this.contact(det_id))?.chip ?? null;
    return r || null;
  }
  async fetchChip(det_id: string): Promise<string> {
    const r = await fetch(`${this.base}/contacts/${encodeURIComponent(det_id)}/chip.webp?fetch=1`);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try {
        const body = (await r.json()) as Envelope<unknown>;
        msg = body?.error?.message || msg;
      } catch {
        /* not JSON: keep the status */
      }
      throw new Error(msg);
    }
    return URL.createObjectURL(await r.blob());
  }
  async cell(cell_id: string): Promise<CellRecord | null> {
    const c = await this.getOrNull<CellRecord>(`/cells/${encodeURIComponent(cell_id)}`);
    if (c) c.expected_activity = normalizeExpected(c.expected_activity);
    return c;
  }
  private rasterCache: RasterEntry[] | null = null;
  async rasters(): Promise<RasterEntry[]> {
    if (this.rasterCache) return this.rasterCache;
    try {
      const e = await this.get<RasterEntry>("/rasters");
      this.rasterCache = (e.items || []).filter((x) => x && x.name && Array.isArray(x.bounds)).map((x) => ({ ...x, default_on: false }));
    } catch {
      this.rasterCache = [];
    }
    return this.rasterCache;
  }
  async rasterImage(name: string, theme: "dark" | "light"): Promise<string | null> {
    return `${this.base}/rasters/${encodeURIComponent(name)}.webp?theme=${theme}`;
  }
}

/** Pass records: the API keeps per-scene counts under `extra.scene_counts`; the views read `scene_counts`. */
function passShape(p: Pass): Pass {
  const extra = (p.extra || {}) as Record<string, unknown>;
  if (!p.scene_counts && Array.isArray(extra.scene_counts)) p.scene_counts = extra.scene_counts as Record<string, unknown>[];
  return p;
}

/** GeoJSON features to the GeoLayer shape used by the map (same as the decoded `geom` encoding). */
export function geoJsonToLayer(features: { geometry: { type: string; coordinates: unknown }; properties?: Record<string, unknown> }[], note: string | null): GeoLayer {
  const xy: number[] = [];
  const ring: number[] = [];
  const feat: number[] = [];
  let kind: GeoLayer["kind"] = "polygon";
  const propRows: Record<string, unknown>[] = [];
  for (const f of features) {
    feat.push(ring.length);
    propRows.push(f.properties || {});
    const g = f.geometry;
    if (!g) continue;
    const addRing = (coords: number[][]) => {
      ring.push(xy.length / 2);
      for (const c of coords) xy.push(Math.round(c[0] * 10000), Math.round(c[1] * 10000));
    };
    switch (g.type) {
      case "Point": kind = "point"; xy.push(Math.round((g.coordinates as number[])[0] * 10000), Math.round((g.coordinates as number[])[1] * 10000)); break;
      case "LineString": kind = "line"; addRing(g.coordinates as number[][]); break;
      case "MultiLineString": kind = "line"; for (const l of g.coordinates as number[][][]) addRing(l); break;
      case "Polygon": for (const r of g.coordinates as number[][][]) addRing(r); break;
      case "MultiPolygon": for (const p of g.coordinates as number[][][][]) for (const r of p) addRing(r); break;
      default: break;
    }
  }
  const props: GeoLayer["props"] = {};
  const keys = new Set<string>();
  for (const p of propRows) for (const k of Object.keys(p)) keys.add(k);
  for (const k of keys) props[k] = (i) => propRows[i]?.[k] ?? null;
  return { kind, n: features.length, xy: Int32Array.from(xy), ring: Uint32Array.from(ring), feat: Uint32Array.from(feat), props, note };
}

export { decodeGeom };
