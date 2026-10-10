// Timeline (spec section 7), simple version: passes, contacts per pass by AIS status, AIS recording with recorder gaps,
// VIIRS nights, upcoming passes. Hand-drawn SVG. Default window: 12 days ending at the latest processed pass.
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, ButtonGroup } from "@blueprintjs/core";
import type { TimelineRows } from "../adapters/types";
import { useApp } from "../app/state";
import { fmtDate, fmtTime } from "../app/format";
import { navigate } from "../app/router";

const ROWS = ["Sentinel-1 passes", "Contacts by AIS status", "AIS recording", "VIIRS nights", "Planned passes"];
const DAY = 86400e3;

export function Timeline() {
  const { adapter, tz, timelineOpen, setTimelineOpen, window: win, setWindow, phone } = useApp();
  const [rows, setRows] = useState<TimelineRows | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    let alive = true;
    adapter.timeline().then((r) => alive && setRows(r));
    return () => { alive = false; };
  }, [adapter]);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const ro = new ResizeObserver(() => setWidth(node.clientWidth || 800));
    ro.observe(node);
    setWidth(node.clientWidth || 800);
    return () => ro.disconnect();
  }, []);

  const domain = useMemo<[number, number]>(() => {
    if (!rows) return [Date.now() - 12 * DAY, Date.now() + 3 * DAY];
    const ts: number[] = [];
    for (const p of rows.passes) ts.push(Date.parse(p.start_utc));
    for (const c of rows.contactsByPass) ts.push(Date.parse(c.start_utc));
    for (const h of rows.aisHours) ts.push(Date.parse(h.start_utc), Date.parse(h.end_utc));
    for (const n of rows.viirsNights) ts.push(Date.parse(n.night + "T12:00:00Z"));
    const valid = ts.filter((t) => Number.isFinite(t));
    if (!valid.length) return [Date.now() - 12 * DAY, Date.now() + 3 * DAY];
    const lo = Math.min(...valid) - DAY;
    const hi = Math.max(...valid) + DAY;
    return [lo, hi];
  }, [rows]);

  const latestProcessed = useMemo(() => {
    if (!rows) return null;
    const p = rows.passes.filter((x) => x.processed).map((x) => Date.parse(x.stop_utc || x.start_utc)).filter(Number.isFinite);
    return p.length ? Math.max(...p) : null;
  }, [rows]);

  const labelW = phone ? 0 : 140;
  const x = (t: number) => labelW + ((t - domain[0]) / (domain[1] - domain[0])) * Math.max(10, width - labelW - 12);
  const rowH = 22;
  const top = 18;
  const statusColor: Record<string, string> = { matched: "var(--status-matched)", unmatched: "var(--status-unmatched)", no_coverage: "var(--status-nocov)", not_checked: "var(--status-notchecked)" };

  const ticks = useMemo(() => {
    const out: number[] = [];
    const start = new Date(domain[0]);
    start.setUTCHours(0, 0, 0, 0);
    const step = (domain[1] - domain[0]) > 20 * DAY ? 4 * DAY : (domain[1] - domain[0]) > 8 * DAY ? 2 * DAY : DAY;
    for (let t = start.getTime(); t <= domain[1]; t += step) out.push(t);
    return out;
  }, [domain]);

  const setLast12 = () => {
    const end = latestProcessed ?? Date.now();
    setWindow({ t0: end - 12 * DAY, t1: end });
  };

  return (
    <section className={"scs-timeline" + (timelineOpen ? "" : " collapsed")} aria-label="Timeline" data-timeline="1">
      <div className="scs-timeline-bar">
        <Button small minimal icon={timelineOpen ? "chevron-down" : "chevron-up"} aria-label={timelineOpen ? "Collapse the timeline" : "Expand the timeline"} onClick={() => setTimelineOpen(!timelineOpen)} />
        <strong>Timeline</strong>
        <span className="scs-muted scs-domain">{fmtDate(new Date(domain[0]).toISOString(), tz)} to {fmtDate(new Date(domain[1]).toISOString(), tz)} ({tz})</span>
        {win && <span className="scs-muted scs-window">window {fmtTime(new Date(win.t0).toISOString(), tz, false)} to {fmtTime(new Date(win.t1).toISOString(), tz, false)}</span>}
        <span className="scs-spacer" />
        <ButtonGroup>
          <Button small text="Last 12 days" active={!!win} onClick={setLast12} title="One Sentinel-1 repeat cycle ending at the latest processed pass" />
          <Button small text="All" active={!win} onClick={() => setWindow(null)} />
          <Button small className="scs-step" icon="step-backward" aria-label="Previous pass" onClick={() => stepPass(rows, -1)} />
          <Button small className="scs-step" icon="step-forward" aria-label="Next pass" onClick={() => stepPass(rows, 1)} />
        </ButtonGroup>
      </div>
      {timelineOpen && (
        <div ref={ref} style={{ flex: 1, minHeight: 0 }}>
          <svg height={top + ROWS.length * rowH + 4} role="img" aria-label="Timeline of passes, contacts, AIS recording and VIIRS nights">
            {ticks.map((t) => (
              <g key={t}><line x1={x(t)} x2={x(t)} y1={top - 4} y2={top + ROWS.length * rowH} stroke="var(--border)" /><text x={x(t) + 2} y={10}>{fmtDate(new Date(t).toISOString(), tz).slice(5)}</text></g>
            ))}
            {!phone && ROWS.map((r, i) => <text key={r} className="rowlabel" x={4} y={top + i * rowH + 14}>{r}</text>)}
            {win && <rect x={x(win.t0)} y={top - 2} width={Math.max(1, x(win.t1) - x(win.t0))} height={ROWS.length * rowH + 2} fill="var(--focus)" opacity={0.08} />}
            {rows?.passes.filter((p) => p.status !== "upcoming").map((p) => (
              <rect key={p.pass_id} className="pass-bar" x={x(Date.parse(p.start_utc))} y={top + 4} width={Math.max(3, x(Date.parse(p.stop_utc)) - x(Date.parse(p.start_utc)))} height={rowH - 8}
                fill={p.mission === "S1C" ? "var(--status-matched)" : "var(--fixed)"} opacity={p.processed ? 1 : 0.5} onClick={() => navigate("pass", p.pass_id)}>
                <title>{p.pass_id}: {p.mission} {fmtTime(p.start_utc, tz)} {p.processed ? "(processed)" : ""}</title>
              </rect>
            ))}
            {rows?.contactsByPass.map((c) => {
              const total = Object.values(c.counts).reduce((a, b) => a + b, 0);
              let acc = 0;
              const h = rowH - 6;
              return (
                <g key={c.pass_id}>
                  {Object.entries(c.counts).map(([s, n]) => {
                    const y0 = top + rowH + 3 + (acc / total) * h;
                    acc += n;
                    return <rect key={s} x={x(Date.parse(c.start_utc)) - 2} y={y0} width={5} height={Math.max(1, (n / total) * h)} fill={statusColor[s] || "var(--text-muted)"}><title>{c.pass_id}: {n} {s}</title></rect>;
                  })}
                </g>
              );
            })}
            {rows?.aisHours.map((h, i) => <rect key={"h" + i} x={x(Date.parse(h.start_utc))} y={top + 2 * rowH + 6} width={Math.max(2, x(Date.parse(h.end_utc)) - x(Date.parse(h.start_utc)))} height={rowH - 12} fill="var(--vessel)"><title>AIS recorded {fmtTime(h.start_utc, tz)} to {fmtTime(h.end_utc, tz)}</title></rect>)}
            {rows?.aisGaps.map((g, i) => <rect key={"g" + i} x={x(Date.parse(g.start_utc))} y={top + 2 * rowH + 6} width={Math.max(2, x(Date.parse(g.end_utc)) - x(Date.parse(g.start_utc)))} height={rowH - 12} fill="var(--text-muted)" opacity={0.4}><title>{g.label}: {fmtTime(g.start_utc, tz)} to {fmtTime(g.end_utc, tz)}</title></rect>)}
            {rows?.viirsNights.map((n) => <circle key={n.night} cx={x(Date.parse(n.night + "T12:00:00Z"))} cy={top + 3 * rowH + rowH / 2} r={Math.min(8, 2 + Math.sqrt(n.n))} fill="var(--light)"><title>{n.night}: {n.n} lights in this build</title></circle>)}
            {rows?.passes.filter((p) => p.status === "upcoming").map((p) => (
              <rect key={p.pass_id} className="pass-bar" x={x(Date.parse(p.start_utc))} y={top + 4 * rowH + 4} width={Math.max(3, x(Date.parse(p.stop_utc)) - x(Date.parse(p.start_utc)))} height={rowH - 8} fill="none" stroke="var(--footprint)" strokeDasharray="3 2" onClick={() => navigate("pass", p.pass_id)}>
                <title>{p.pass_id}: planned {fmtTime(p.start_utc, tz)} ({p.sources.join(", ")})</title>
              </rect>
            ))}
          </svg>
        </div>
      )}
    </section>
  );
}

function stepPass(rows: TimelineRows | null, dir: 1 | -1): void {
  if (!rows) return;
  const processed = rows.passes.filter((p) => p.processed).sort((a, b) => Date.parse(a.start_utc) - Date.parse(b.start_utc));
  if (!processed.length) return;
  const cur = window.location.hash.match(/^#\/pass\/(.+)$/);
  const id = cur ? decodeURIComponent(cur[1]) : null;
  const i = processed.findIndex((p) => p.pass_id === id);
  const next = i < 0 ? (dir > 0 ? processed[0] : processed[processed.length - 1]) : processed[Math.min(processed.length - 1, Math.max(0, i + dir))];
  navigate("pass", next.pass_id);
}
