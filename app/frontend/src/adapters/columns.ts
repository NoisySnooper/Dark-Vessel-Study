// Decoders for the contract 6.2 encodings. The frontend reads `t` and never assumes a type.
// Typed little-endian arrays in base64; `raw == na` means null; `s` is the scale (value = raw / s).
import type { GeoLayer } from "./types";

export interface ColumnSpec {
  t: string;
  b?: string;
  s?: number;
  na?: number;
  dict?: string[];
  e?: string;
  v?: unknown;
  pfx?: string[];
  w?: number[] | number;
  p?: string;
  q?: string;
  to?: string;
}

export interface ColumnarPart {
  type: string;
  n: number;
  columns: Record<string, ColumnSpec>;
  records?: Record<string, Record<string, unknown>>;
  prov?: Record<string, string>;
  note?: string;
  [k: string]: unknown;
}

export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function typed(t: string, b: string): ArrayLike<number> & { length: number } {
  const bytes = b64ToBytes(b);
  const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  switch (t) {
    case "i32": return new Int32Array(buf);
    case "u32": return new Uint32Array(buf);
    case "i16": return new Int16Array(buf);
    case "u16": return new Uint16Array(buf);
    case "u8": case "bool8": case "dict8": return new Uint8Array(buf);
    case "dict16": case "ref16": return new Uint16Array(buf);
    case "f32": return new Float32Array(buf);
    case "time": return new Uint32Array(buf);
    default: throw new Error("unknown column type " + t);
  }
}

/** A decoded column: value(i) returns the contract value (number, string, boolean or null). */
export type Getter = (i: number) => unknown;

export function pad(n: number, w: number): string {
  let s = String(n);
  while (s.length < w) s = "0" + s;
  return s;
}

export function decodeColumn(spec: ColumnSpec, n: number, ctx: { satellite?: Getter; time_utc?: Getter } = {}): Getter {
  const t = spec.t;
  if (t === "const") return () => (spec.v === undefined ? null : spec.v);
  if (t === "str") {
    const v = (spec.v as (string | null)[]) || [];
    return (i) => (v[i] === undefined ? null : v[i]);
  }
  if (t === "detid") {
    const p = typed("u16", spec.p || "");
    const q = typed("u32", spec.q || "");
    const pfx = spec.pfx || [];
    const w = spec.w;
    return (i) => {
      const k = p[i];
      const width = Array.isArray(w) ? (w.length === 1 ? w[0] : w[k]) : (w as number) || 5;
      return pfx[k] + "_" + pad(q[i], width);
    };
  }
  if (t === "lightid") {
    const q = typed("u32", spec.q || "");
    const w = typeof spec.w === "number" ? spec.w : 6;
    const code: Record<string, string> = { "S-NPP": "SPP", "NOAA-20": "N20", "NOAA-21": "N21" };
    return (i) => {
      const sat = ctx.satellite ? String(ctx.satellite(i)) : "";
      const ts = ctx.time_utc ? String(ctx.time_utc(i)) : "";
      const stamp = ts.replace(/[-:]/g, "").slice(0, 15); // yyyymmddThhmmss
      return (code[sat] || sat) + "_" + stamp + "_" + pad(q[i], w);
    };
  }
  if (!spec.b) return () => null;
  const arr = typed(t, spec.b);
  const s = spec.s || 1;
  const na = spec.na;
  if (t === "dict8" || t === "dict16") {
    const d = spec.dict || [];
    const naV = na === undefined ? (t === "dict8" ? 255 : 65535) : na;
    return (i) => (arr[i] === naV ? null : d[arr[i]] ?? null);
  }
  if (t === "bool8") {
    const naV = na === undefined ? 255 : na;
    return (i) => (arr[i] === naV ? null : arr[i] === 1);
  }
  if (t === "time") {
    const epoch = Date.parse(spec.e || "1970-01-01T00:00:00Z");
    const naV = na === undefined ? 4294967295 : na;
    return (i) => (arr[i] === naV ? null : new Date(epoch + arr[i] * 1000).toISOString().replace(/\.000Z$/, "Z"));
  }
  if (t === "ref16") {
    const naV = na === undefined ? 65535 : na;
    return (i) => (arr[i] === naV ? null : arr[i]);
  }
  if (t === "f32") return (i) => (Number.isNaN(arr[i]) ? null : arr[i]);
  return (i) => (na !== undefined && arr[i] === na ? null : arr[i] / s);
}

/** Decode every column of a part once. */
export function decodePart(part: ColumnarPart): { n: number; get: Record<string, Getter>; records: Record<string, Record<string, unknown>> } {
  const get: Record<string, Getter> = {};
  const cols = part.columns || {};
  // satellite and time_utc first (lightid depends on them)
  for (const name of ["satellite", "time_utc"]) if (cols[name]) get[name] = decodeColumn(cols[name], part.n);
  for (const [name, spec] of Object.entries(cols)) {
    if (get[name]) continue;
    get[name] = decodeColumn(spec, part.n, { satellite: get.satellite, time_utc: get.time_utc });
  }
  return { n: part.n, get, records: part.records || {} };
}

/** Time column as ms since epoch (NaN when null), without per-row Date objects. */
export function timeMs(spec: ColumnSpec | undefined, n: number): Float64Array {
  const out = new Float64Array(n).fill(NaN);
  if (!spec || spec.t !== "time" || !spec.b) return out;
  const arr = typed("time", spec.b);
  const epoch = Date.parse(spec.e || "1970-01-01T00:00:00Z");
  const na = spec.na === undefined ? 4294967295 : spec.na;
  for (let i = 0; i < n; i++) if (arr[i] !== na) out[i] = epoch + arr[i] * 1000;
  return out;
}

export function numberArray(spec: ColumnSpec | undefined, n: number): Float64Array {
  const out = new Float64Array(n).fill(NaN);
  if (!spec || !spec.b) return out;
  const g = decodeColumn(spec, n);
  for (let i = 0; i < n; i++) {
    const v = g(i);
    if (typeof v === "number") out[i] = v;
  }
  return out;
}

export interface GeomSpec {
  kind: "point" | "line" | "polygon";
  n: number;
  xy: string;
  ring?: string;
  feat?: string;
  props?: Record<string, ColumnSpec>;
  note?: string;
}

export function decodeGeom(g: GeomSpec): GeoLayer {
  const xy = typed("i32", g.xy) as Int32Array;
  const ring = g.ring ? (typed("u32", g.ring) as Uint32Array) : new Uint32Array(0);
  const feat = g.feat ? (typed("u32", g.feat) as Uint32Array) : new Uint32Array(0);
  const props: Record<string, Getter> = {};
  for (const [k, spec] of Object.entries(g.props || {})) props[k] = decodeColumn(spec, g.n);
  return { kind: g.kind, n: g.n, xy, ring, feat, props, note: g.note || null };
}

/** Rings of feature j as arrays of [lat, lon] (Leaflet order). */
export function featureRings(layer: GeoLayer, j: number): [number, number][][] {
  const out: [number, number][][] = [];
  if (layer.kind === "point") {
    out.push([[layer.xy[2 * j + 1] / 10000, layer.xy[2 * j] / 10000]]);
    return out;
  }
  const r0 = layer.feat[j];
  const r1 = j + 1 < layer.feat.length ? layer.feat[j + 1] : layer.ring.length;
  const nv = layer.xy.length / 2;
  for (let r = r0; r < r1; r++) {
    const v0 = layer.ring[r];
    const v1 = r + 1 < layer.ring.length ? layer.ring[r + 1] : nv;
    const ringPts: [number, number][] = [];
    for (let v = v0; v < v1; v++) ringPts.push([layer.xy[2 * v + 1] / 10000, layer.xy[2 * v] / 10000]);
    out.push(ringPts);
  }
  return out;
}
