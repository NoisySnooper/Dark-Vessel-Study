// Ocean context at an object (board D5.3) and expected activity of a cell (board D5.4): field specifications and the
// normalisers both adapters use, so the views read one shape whatever the API or the bundle wrote.
import type { ContextField, ExpectedActivity, ExpectedRow, ExpectedSummary, ObjectContext } from "./types";

export interface ContextFieldSpec {
  name: string;
  label: string;
  unit: string;
  /** Source registry key for the provenance chip when the record's `src` is a dataset name, not a key. */
  key: string;
  digits: number;
  kind: "number" | "presence";
  /** Shown when the record gives no valid time (static layers). */
  staticTime?: string;
}

// Order of the Context section. Shipping is presence as published by the World Bank and IMF, never a count (board D4.3).
export const CONTEXT_FIELDS: ContextFieldSpec[] = [
  { name: "depth_m", label: "Depth", unit: "m", key: "gebco_2026", digits: 0, kind: "number", staticTime: "static (GEBCO_2026 grid)" },
  { name: "dist_coast_km", label: "Distance to the coast", unit: "km", key: "natural_earth", digits: 1, kind: "number", staticTime: "static (Natural Earth 10 m coast)" },
  { name: "dist_port_km", label: "Distance to the nearest major port", unit: "km", key: "wpi", digits: 1, kind: "number", staticTime: "static (World Port Index)" },
  { name: "ship_presence_all", label: "Shipping presence, all types", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "ship_presence_commercial", label: "Shipping presence, commercial", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "ship_presence_fishing", label: "Shipping presence, fishing", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "ship_presence_oilgas", label: "Shipping presence, oil and gas", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "ship_presence_passenger", label: "Shipping presence, passenger", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "ship_presence_leisure", label: "Shipping presence, leisure", unit: "presence", key: "worldbank_density", digits: 0, kind: "presence", staticTime: "January 2015 to February 2021 (as published)" },
  { name: "sst_c", label: "Sea surface temperature", unit: "degC", key: "mur_sst", digits: 2, kind: "number" },
  { name: "sst_grad", label: "SST gradient", unit: "degC/km", key: "mur_sst", digits: 3, kind: "number" },
  { name: "dist_front_km", label: "Distance to the nearest SST front", unit: "km", key: "mur_sst", digits: 1, kind: "number" },
  { name: "chl_log10", label: "Chlorophyll-a", unit: "log10 mg m-3", key: "chl_dineof", digits: 2, kind: "number" },
  { name: "current_speed_ms", label: "Current speed", unit: "m/s", key: "rtofs", digits: 2, kind: "number" },
  { name: "mld_m", label: "Mixed layer depth", unit: "m", key: "rtofs", digits: 1, kind: "number" },
  { name: "wave_hs_m", label: "Significant wave height", unit: "m", key: "gfs_wave", digits: 2, kind: "number" },
];
export const CONTEXT_SPEC: Record<string, ContextFieldSpec> = Object.fromEntries(CONTEXT_FIELDS.map((f) => [f.name, f]));

export const SHIPPING_PRESENCE_NOTE = "presence as published by the World Bank and IMF, not a count";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function str(v: unknown): string | null {
  return v === null || v === undefined || v === "" ? null : String(v);
}

/** One field: accepts `{value, unit, time, src}` or a bare value; fills unit and source from the spec and `defaults`. */
function field(name: string, raw: unknown, defaults?: Record<string, { unit?: string | null; src?: string | null; time?: string | null }>): ContextField {
  const spec = CONTEXT_SPEC[name];
  const d = defaults?.[name] || {};
  const r = raw !== null && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : { value: raw };
  let value: number | boolean | null = null;
  if (typeof r.value === "boolean") value = r.value;
  else if (spec?.kind === "presence" && (r.value === 0 || r.value === 1)) value = r.value === 1;
  else value = num(r.value);
  return {
    value,
    unit: str(r.unit) ?? str(d.unit) ?? spec?.unit ?? null,
    time: str(r.time) ?? str(d.time) ?? null,
    src: str(r.src) ?? str(d.src) ?? null,
  };
}

/** Board D5.3 `object_context`, or null. `defaults` (bundle part level) supply unit, time and source a field omits. */
export function normalizeObjectContext(raw: unknown, defaults?: Record<string, { unit?: string | null; src?: string | null; time?: string | null }>, caveat?: string): ObjectContext | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const fieldsRaw = (r.fields && typeof r.fields === "object" ? r.fields : null) as Record<string, unknown> | null;
  if (!fieldsRaw) return null;
  const fields: Record<string, ContextField> = {};
  for (const [k, v] of Object.entries(fieldsRaw)) fields[k] = field(k, v, defaults);
  return {
    time_utc: str(r.time_utc),
    cell_id: str(r.cell_id),
    region: str(r.region),
    fields,
    caveat: str(r.caveat) ?? caveat ?? "",
  };
}

function row(raw: Record<string, unknown>): ExpectedRow {
  const b = (v: unknown) => (typeof v === "boolean" ? v : v === 1 ? true : v === 0 ? false : null);
  return {
    unit_id: String(raw.unit_id ?? raw.night ?? ""),
    night: String(raw.night ?? ""),
    time_start_utc: str(raw.time_start_utc),
    time_end_utc: str(raw.time_end_utc),
    tested: raw.tested === undefined ? true : !!raw.tested,
    observed: num(raw.observed),
    expected: num(raw.expected),
    z: num(raw.z),
    q_bh: num(raw.q_bh),
    flag: str(raw.flag),
    flag_robust: str(raw.flag_robust),
    calm: b(raw.calm),
    exposure_km2: num(raw.exposure_km2),
  };
}

/** Board D5.4 `expected_activity`, or a counts-only summary (single-file page), or null. Rows newest first, tested only. */
export function normalizeExpected(raw: unknown, fallbackCaveat?: string): ExpectedActivity | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const targets: Record<string, ExpectedRow[]> = {};
  const summary: Record<string, ExpectedSummary> = {};
  const t = (r.targets && typeof r.targets === "object" ? r.targets : {}) as Record<string, unknown>;
  for (const [name, v] of Object.entries(t)) {
    if (Array.isArray(v)) {
      targets[name] = v.filter((x) => x && typeof x === "object").map((x) => row(x as Record<string, unknown>)).filter((x) => x.tested)
        .sort((a, b) => (b.time_start_utc || b.night).localeCompare(a.time_start_utc || a.night));
    } else if (v && typeof v === "object") {
      const s = v as Record<string, unknown>;
      summary[name] = { n_tested: num(s.n_tested), n_flag: num(s.n_flag), n_flag_robust: num(s.n_flag_robust) };
    }
  }
  if (!Object.keys(targets).length && !Object.keys(summary).length) return null;
  return {
    model_id: str(r.model_id),
    caveat: str(r.caveat) ?? fallbackCaveat ?? "",
    targets,
    summary: Object.keys(summary).length ? summary : null,
    note: str(r.note),
  };
}
