// Omnibar query parsing shared by both adapters (docs/product_design.md section 5).
// Ids are recognised by shape; coordinates in DD, DMS, DDM and MGRS resolve to a point.
import { toPoint } from "mgrs";

export type QueryKind = "mmsi" | "imo" | "det_id" | "light_id" | "lead_id" | "pass_id" | "event_id" | "point" | "text";

export interface ParsedQuery {
  kind: QueryKind;
  value: string;
  lon?: number;
  lat?: number;
  interpretation?: string;
}

const RE_DET = /^S1[CD]_\d{8}T\d{4,6}_\d{4,5}$/i;
const RE_LIGHT = /^(SPP|N20|N21)_\d{8}T\d{6}_\d{6}$/i;
const RE_LEAD = /^L[1-8]-/i;
const RE_EVENT = /^E\d{1,2}-/i;
const RE_PASS = /^(S1[CD]_R\d+_\d{8}T\d{4}|live_S1[CD]_\d{8}T\d{4}|S1[CD]_\d{8}T\d{4})$/i;

function fmt(v: number): string {
  return (Math.round(v * 10000) / 10000).toString();
}

function hemi(v: number, pos: string, neg: string): string {
  return `${fmt(Math.abs(v))} ${v >= 0 ? pos : neg}`;
}

function point(lat: number, lon: number, how: string): ParsedQuery | null {
  if (!(Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) return null;
  return { kind: "point", value: `${fmt(lat)}, ${fmt(lon)}`, lat, lon, interpretation: `read as ${hemi(lat, "N", "S")}, ${hemi(lon, "E", "W")} (${how})` };
}

export function parseCoordinates(raw: string): ParsedQuery | null {
  const q = raw.trim().replace(/[’′]/g, "'").replace(/[”″]/g, '"').replace(/\s+/g, " ");
  // DMS: 10°15'00"N 107°30'00"E or 10 15 00 N 107 30 00 E
  let m = q.match(/^(\d{1,3})[°\s](\d{1,2})['\s](\d{1,2}(?:\.\d+)?)"?\s*([NS])[,\s]+(\d{1,3})[°\s](\d{1,2})['\s](\d{1,2}(?:\.\d+)?)"?\s*([EW])$/i);
  if (m) {
    const lat = (+m[1] + +m[2] / 60 + +m[3] / 3600) * (m[4].toUpperCase() === "S" ? -1 : 1);
    const lon = (+m[5] + +m[6] / 60 + +m[7] / 3600) * (m[8].toUpperCase() === "W" ? -1 : 1);
    return point(lat, lon, "degrees, minutes, seconds");
  }
  // DDM: 10°15.000'N 107°30.000'E
  m = q.match(/^(\d{1,3})[°\s](\d{1,2}(?:\.\d+)?)'?\s*([NS])[,\s]+(\d{1,3})[°\s](\d{1,2}(?:\.\d+)?)'?\s*([EW])$/i);
  if (m) {
    const lat = (+m[1] + +m[2] / 60) * (m[3].toUpperCase() === "S" ? -1 : 1);
    const lon = (+m[4] + +m[5] / 60) * (m[6].toUpperCase() === "W" ? -1 : 1);
    return point(lat, lon, "degrees and decimal minutes");
  }
  // Decimal with hemispheres, either order: 10.25N 107.5E or 107.5E 10.25N
  m = q.match(/^(-?\d{1,3}(?:\.\d+)?)°?\s*([NSEW])[,\s]+(-?\d{1,3}(?:\.\d+)?)°?\s*([NSEW])$/i);
  if (m) {
    const a = { v: +m[1], h: m[2].toUpperCase() };
    const b = { v: +m[3], h: m[4].toUpperCase() };
    const latPart = "NS".includes(a.h) ? a : "NS".includes(b.h) ? b : null;
    const lonPart = "EW".includes(a.h) ? a : "EW".includes(b.h) ? b : null;
    if (latPart && lonPart) {
      return point(latPart.v * (latPart.h === "S" ? -1 : 1), lonPart.v * (lonPart.h === "W" ? -1 : 1), "decimal degrees");
    }
  }
  // Bare decimal pair: lat, lon unless the first number is above 90
  m = q.match(/^(-?\d{1,3}(?:\.\d+)?)[,\s]+(-?\d{1,3}(?:\.\d+)?)$/);
  if (m) {
    let a = +m[1];
    let b = +m[2];
    let how = "decimal degrees, latitude first";
    if (Math.abs(a) > 90 && Math.abs(b) <= 90) {
      [a, b] = [b, a];
      how = "decimal degrees, longitude first because the first number is above 90";
    }
    return point(a, b, how);
  }
  // MGRS: 48PVQ8899650631, 48P VQ 88996 50631, 48PVQ889506
  const compact = q.replace(/\s+/g, "").toUpperCase();
  if (/^\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}(\d{2,10})$/.test(compact) && compact.length % 2 === 1 - (compact.match(/^\d{2}/) ? 0 : 1) * 0) {
    try {
      const [lon, lat] = toPoint(compact);
      if (Number.isFinite(lon) && Number.isFinite(lat)) {
        const digits = compact.replace(/^\d{1,2}[A-Z]{3}/, "").length / 2;
        const res = [100000, 10000, 1000, 100, 10, 1][digits] ?? 1;
        return point(lat, lon, `MGRS ${compact}, ${res} m square, centre`);
      }
    } catch {
      return null;
    }
  }
  return null;
}

export function parseQuery(raw: string): ParsedQuery {
  const q = raw.trim();
  if (!q) return { kind: "text", value: "" };
  if (/^\d{9}$/.test(q)) return { kind: "mmsi", value: q };
  const imo = q.match(/^(?:IMO\s*)?(\d{7})$/i);
  if (imo) return { kind: "imo", value: imo[1] };
  if (RE_DET.test(q)) return { kind: "det_id", value: q.toUpperCase().replace(/^S1([CD])/, "S1$1") };
  if (RE_LIGHT.test(q)) return { kind: "light_id", value: q.toUpperCase() };
  if (RE_LEAD.test(q)) return { kind: "lead_id", value: q.replace(/^l/, "L") };
  if (RE_EVENT.test(q)) return { kind: "event_id", value: q };
  if (RE_PASS.test(q)) return { kind: "pass_id", value: q };
  const c = parseCoordinates(q);
  if (c) return c;
  return { kind: "text", value: q };
}
