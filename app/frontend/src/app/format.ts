// Time, coordinate and unit formatting. UTC default, ICT (UTC+7) by toggle; DTG as DDHHMMZ MON YY.
import { forward } from "mgrs";

export type TimeZone = "UTC" | "ICT";
export type Units = "km" | "nm";

const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const p2 = (n: number) => String(n).padStart(2, "0");

export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "08 OCT 2026 22:58:12 UTC" or the ICT equivalent. */
export function fmtTime(iso: string | null | undefined, tz: TimeZone = "UTC", seconds = true): string {
  const d = parseUtc(iso);
  if (!d) return "unknown";
  const t = new Date(d.getTime() + (tz === "ICT" ? 7 * 3600e3 : 0));
  const hm = `${p2(t.getUTCHours())}:${p2(t.getUTCMinutes())}` + (seconds ? `:${p2(t.getUTCSeconds())}` : "");
  return `${p2(t.getUTCDate())} ${MON[t.getUTCMonth()]} ${t.getUTCFullYear()} ${hm} ${tz}`;
}

export function fmtDate(iso: string | null | undefined, tz: TimeZone = "UTC"): string {
  const d = parseUtc(iso);
  if (!d) return "unknown";
  const t = new Date(d.getTime() + (tz === "ICT" ? 7 * 3600e3 : 0));
  return `${t.getUTCFullYear()}-${p2(t.getUTCMonth() + 1)}-${p2(t.getUTCDate())}`;
}

/** DTG form, always UTC: 082258Z OCT 26. */
export function fmtDtg(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "unknown";
  return `${p2(d.getUTCDate())}${p2(d.getUTCHours())}${p2(d.getUTCMinutes())}Z ${MON[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "unknown";
  const s = Math.abs(seconds);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 48 * 3600) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} days`;
}

export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  const d = parseUtc(iso);
  if (!d) return "unknown";
  const dt = (d.getTime() - now) / 1000;
  return dt >= 0 ? `in ${fmtDuration(dt)}` : `${fmtDuration(-dt)} ago`;
}

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "unknown";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtMetres(m: number | null | undefined, units: Units = "km"): string {
  if (m === null || m === undefined || !Number.isFinite(m)) return "unknown";
  if (units === "nm") {
    const nm = m / 1852;
    return nm < 1 ? `${fmtNum(m)} m` : `${fmtNum(nm, nm < 10 ? 2 : 1)} nm`;
  }
  return m < 1000 ? `${fmtNum(m)} m` : `${fmtNum(m / 1000, m < 10000 ? 2 : 1)} km`;
}

export function fmtKm(km: number | null | undefined, units: Units = "km"): string {
  return km === null || km === undefined ? "unknown" : fmtMetres(km * 1000, units);
}

export function fmtDd(lat: number, lon: number): string {
  return `${Math.abs(lat).toFixed(5)} ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(5)} ${lon >= 0 ? "E" : "W"}`;
}

function dms(v: number, pos: string, neg: string): string {
  const a = Math.abs(v);
  const d = Math.floor(a);
  const mFull = (a - d) * 60;
  const m = Math.floor(mFull);
  const s = (mFull - m) * 60;
  return `${d}°${p2(m)}'${s.toFixed(1).padStart(4, "0")}"${v >= 0 ? pos : neg}`;
}

export function fmtDms(lat: number, lon: number): string {
  return `${dms(lat, "N", "S")} ${dms(lon, "E", "W")}`;
}

function ddm(v: number, pos: string, neg: string): string {
  const a = Math.abs(v);
  const d = Math.floor(a);
  return `${d}°${((a - d) * 60).toFixed(3).padStart(6, "0")}'${v >= 0 ? pos : neg}`;
}

export function fmtDdm(lat: number, lon: number): string {
  return `${ddm(lat, "N", "S")} ${ddm(lon, "E", "W")}`;
}

export function fmtMgrs(lat: number, lon: number, accuracy = 5): string {
  try {
    return forward([lon, lat], accuracy);
  } catch {
    return "outside MGRS";
  }
}

/** Great-circle distance in metres (haversine, WGS 84 mean radius). */
export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371008.8;
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLon = (lon2 - lon1) * toRad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function cellIdOf(lon: number, lat: number): string {
  return `r${Math.floor((24.0 - lat) / 0.25)}c${Math.floor((lon - 99.0) / 0.25)}`;
}

export function priorityBand(p: number): "low" | "medium" | "high" {
  return p <= 33 ? "low" : p <= 66 ? "medium" : "high";
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "unknown" : `${(v * 100).toFixed(digits)} %`;
}

export function sentinelName(mission: string | null | undefined): string {
  return mission === "S1C" ? "Sentinel-1C" : mission === "S1D" ? "Sentinel-1D" : mission || "Sentinel-1";
}
