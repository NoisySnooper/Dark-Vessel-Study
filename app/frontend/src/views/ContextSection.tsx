// Ocean context of an object (board D5.3) and expected activity of a cell (board D5.4), with the ocean and anomaly
// caveats. Every value carries its unit, valid time and a source chip; shipping is presence as published, never a number.
import { Section, SectionCard, Tag } from "@blueprintjs/core";
import type { ExpectedActivity, ExpectedRow, ObjectContext } from "../adapters/types";
import { CONTEXT_FIELDS, CONTEXT_SPEC, SHIPPING_PRESENCE_NOTE } from "../adapters/context";
import { ANOMALY_CAVEAT, NO_CONTEXT, OCEAN_CAVEAT, OCEAN_NOTE } from "../app/text";
import { fmtMetres, fmtNum, fmtTime } from "../app/format";
import { useApp } from "../app/state";
import { ProvChip } from "./Provenance";
import { ObjectLink } from "./common";

/** The source registry key of a context field: the record's `src` when it is a registry key, else the field's producer. */
function sourceKey(name: string, src: string | null, keys: Set<string>): string {
  if (src && keys.has(src)) return src;
  return CONTEXT_SPEC[name]?.key || "app";
}

function valueText(name: string, value: number | boolean | null, unit: string | null, units: "km" | "nm"): string {
  const spec = CONTEXT_SPEC[name];
  if (value === null || value === undefined) return "no value at this object";
  if (spec?.kind === "presence" || typeof value === "boolean") return value ? "present (published value above 0)" : "not present (published value 0)";
  if (name.endsWith("_km")) return fmtMetres(value * 1000, units);
  const digits = spec?.digits ?? 2;
  return `${value.toFixed(digits)}${unit ? " " + unit : ""}`;
}

export function ContextSection({ ctx, kind }: { ctx: ObjectContext | null | undefined; kind: "contact" | "light" }) {
  const { meta, units, tz } = useApp();
  const keys = new Set(meta.sources.map((s) => s.key));
  return (
    <Section title="Ocean context at the object" compact collapsible>
      <SectionCard>
        <p className="scs-ocean-caveat" style={{ fontSize: 12 }} data-ocean-caveat="1">{OCEAN_NOTE}</p>
        {!ctx ? (
          <p className="scs-muted" data-context="none">{NO_CONTEXT}. {kind === "contact" ? "Live passes get context when the context table is rebuilt after the pass." : "The context table has no row for this light."}</p>
        ) : (
          <>
            <p className="scs-muted" style={{ fontSize: 12 }} data-context="1">
              Sampled at {fmtTime(ctx.time_utc, tz)}{ctx.cell_id ? <>, cell <ObjectLink type="cell" id={ctx.cell_id} /></> : null}{ctx.region ? `, ${ctx.region} (reporting box, not a boundary)` : ""}.
            </p>
            <div className="scs-table-scroll"><table className="bp6-html-table bp6-compact scs-context-table" data-context-table="1">
              <thead><tr><th>field</th><th>value</th><th>valid</th><th>source</th></tr></thead>
              <tbody>
                {CONTEXT_FIELDS.filter((f) => ctx.fields[f.name] !== undefined).map((f) => {
                  const v = ctx.fields[f.name];
                  const key = sourceKey(f.name, v.src, keys);
                  const dataset = v.src && !keys.has(v.src) ? v.src : null;
                  return (
                    <tr key={f.name} data-context-field={f.name}>
                      <td>{f.label}</td>
                      <td>{valueText(f.name, v.value, v.unit, units)}</td>
                      <td className="scs-muted">{v.time ? (/T\d\d:/.test(v.time) ? fmtTime(v.time, tz, false) : v.time) : f.staticTime || "unknown"}</td>
                      <td><ProvChip sourceKey={key} field={f.name} />{dataset ? <span className="scs-muted" style={{ fontSize: 11 }}> {dataset}</span> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table></div>
            <p className="scs-muted" style={{ fontSize: 12 }}>Shipping: {SHIPPING_PRESENCE_NOTE}. {ctx.caveat && ctx.caveat !== OCEAN_NOTE ? ctx.caveat : OCEAN_CAVEAT}</p>
          </>
        )}
      </SectionCard>
    </Section>
  );
}

function flagText(f: string | null): string {
  if (!f || f === "none") return "none";
  if (f === "high") return "above expected";
  if (f === "low") return "below expected";
  return f;
}

/** Observed against expected per night, oldest left, as a small SVG line pair (no chart library). */
function Sparkline({ rows }: { rows: ExpectedRow[] }) {
  const pts = [...rows].reverse().filter((r) => r.observed !== null || r.expected !== null);
  if (pts.length < 2) return null;
  const W = 260;
  const H = 56;
  const max = Math.max(1, ...pts.map((r) => Math.max(r.observed ?? 0, r.expected ?? 0)));
  const x = (i: number) => 4 + (i * (W - 8)) / (pts.length - 1);
  const y = (v: number) => H - 4 - (v / max) * (H - 10);
  const line = (sel: (r: ExpectedRow) => number | null) => pts.map((r, i) => { const v = sel(r); return v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`; }).filter(Boolean).join(" ");
  return (
    <svg className="scs-sparkline" viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" data-sparkline="1"
      aria-label={`Observed and expected counts over ${pts.length} tested nights, maximum ${fmtNum(max, 1)}`}>
      <polyline points={line((r) => r.expected)} fill="none" stroke="var(--text-muted)" strokeWidth={1.5} strokeDasharray="4 3" />
      <polyline points={line((r) => r.observed)} fill="none" stroke="var(--link)" strokeWidth={1.5} />
      {pts.map((r, i) => (r.flag && r.flag !== "none" && r.observed !== null ? <circle key={i} cx={x(i)} cy={y(r.observed)} r={3} fill="none" stroke="var(--status-unmatched)" strokeWidth={1.5} /> : null))}
    </svg>
  );
}

const TARGET_LABEL: Record<string, string> = {
  viirs: "VIIRS lit vessel candidates per cell-night",
  radar: "Radar vessel candidates per cell and pass",
};

export function ExpectedActivitySection({ ea }: { ea: ExpectedActivity | null | undefined }) {
  const { tz } = useApp();
  const anomaly = ea?.caveat ? (ea.caveat.startsWith(OCEAN_CAVEAT) ? ea.caveat.slice(OCEAN_CAVEAT.length).trim() : ea.caveat) : ANOMALY_CAVEAT;
  return (
    <Section title="Expected activity" compact collapsible>
      <SectionCard>
        <p className="scs-ocean-caveat" style={{ fontSize: 12 }} data-ocean-caveat="1">{OCEAN_NOTE}</p>
        <p className="scs-muted" style={{ fontSize: 12 }} data-anomaly-caveat="1">{anomaly || ANOMALY_CAVEAT}</p>
        {!ea ? (
          <p className="scs-muted" data-expected="none">No expected-activity rows for this cell in this build (no tested night or pass, or the model output is not loaded).</p>
        ) : (
          <div data-expected="1">
            {ea.model_id && <p className="scs-muted" style={{ fontSize: 12 }}>Model <span className="judgment">{ea.model_id}</span>; expected counts are model output (judgment), observed counts are detections.</p>}
            {Object.entries(ea.targets).map(([t, rows]) => (
              <div key={t} data-expected-target={t} style={{ marginBottom: 10 }}>
                <h6 style={{ margin: "6px 0 2px" }}>{TARGET_LABEL[t] || t}: {rows.length} tested {t === "radar" ? "passes" : "nights"}, {rows.filter((r) => r.flag && r.flag !== "none").length} flagged ({rows.filter((r) => r.flag_robust && r.flag_robust !== "none").length} robust)</h6>
                <Sparkline rows={rows} />
                <div className="scs-muted" style={{ fontSize: 11 }}><span style={{ color: "var(--link)" }}>solid: observed</span>, dashed: expected, rings: flagged</div>
                <div className="scs-table-scroll">
                  <table className="bp6-html-table bp6-compact" style={{ width: "100%" }}>
                    <thead><tr><th>{t === "radar" ? "pass date" : "night"}</th><th>observed</th><th className="judgment">expected</th><th className="judgment">z</th><th className="judgment">flag</th><th>calm</th><th>exposure</th></tr></thead>
                    <tbody>
                      {rows.slice(0, 12).map((r) => (
                        <tr key={r.unit_id + r.night} data-flag={r.flag || "none"}>
                          <td title={r.time_start_utc ? fmtTime(r.time_start_utc, tz, false) : r.night}>{r.night}</td>
                          <td>{fmtNum(r.observed)}</td>
                          <td className="judgment">{fmtNum(r.expected, 2)}</td>
                          <td className="judgment">{r.z === null ? "unknown" : r.z.toFixed(2)}</td>
                          <td className="judgment">{flagText(r.flag)}{r.flag_robust && r.flag_robust !== "none" ? <Tag minimal style={{ marginLeft: 4 }}>robust</Tag> : null}</td>
                          <td>{r.calm === null ? "unknown" : r.calm ? "yes" : "no"}</td>
                          <td>{r.exposure_km2 === null ? "unknown" : `${fmtNum(r.exposure_km2)} km2`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {rows.length > 12 && <p className="scs-muted" style={{ fontSize: 11 }}>Newest 12 of {rows.length} shown; the local app and data/expected_activity.parquet hold all.</p>}
              </div>
            ))}
            {ea.summary && Object.entries(ea.summary).map(([t, s]) => (
              <div key={t} data-expected-summary={t} className="scs-field">
                <span className="k">{TARGET_LABEL[t] || t}</span>
                <span className="v">{fmtNum(s.n_tested)} tested, {fmtNum(s.n_flag)} flagged, {fmtNum(s.n_flag_robust)} robust</span>
              </div>
            ))}
            {ea.summary && !Object.keys(ea.targets).length && <p className="scs-muted" style={{ fontSize: 12 }}>This page carries counts only; the rows (observed, expected, z per night) are in the local app.</p>}
            {ea.note && <p className="scs-muted" style={{ fontSize: 11 }}>{ea.note}</p>}
          </div>
        )}
      </SectionCard>
    </Section>
  );
}
