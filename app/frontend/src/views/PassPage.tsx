// Pass page (spec section 4.7): one Sentinel-1 pass. For a processed live pass it leads with the identification result:
// contacts by AIS status, the AIS heard in the footprint, near it and in the AOI, the AIS recording window, and a table
// of the pass's contacts with each one's identity (matched), nearest AIS vessel (no match) or coverage rule.
import { useEffect, useMemo, useState } from "react";
import { Button, ButtonGroup, Callout, Section, SectionCard, Tag } from "@blueprintjs/core";
import type { AisOnlyVessel, AisStatus, Contact, Pass } from "../adapters/types";
import { useApp, useAsync } from "../app/state";
import { AISSTREAM_LABEL, AIS_ONLY_NOTE, AIS_STATUS_LABEL, DARK_CAVEAT_SHORT, LENGTH_NOTE, LOW_QUALITY_LABEL } from "../app/text";
import { ambiguousCandidates, isAmbiguous, lowQualityPairing, reviewGrade, shipTypeText } from "../app/identity";
import { fmtDuration, fmtMetres, fmtNum, fmtRelative, fmtTime, pct, sentinelName } from "../app/format";
import { Field, ProvChip, ProvenanceTable } from "./Provenance";
import { Loading, Missing, ObjectHeader, ObjectLink, StatusChip } from "./common";

const STATUS_ORDER: AisStatus[] = ["matched", "unmatched", "no_coverage", "not_checked"];
const PAGE = 25;

/** Minutes before and after the scene time that the live matcher reads AIS (the live file's `ais_window` rule). */
function windowMinutes(rule: string | null | undefined): [number, number] {
  const m = /(\d+)\s*min before to (\d+)\s*min after/i.exec(rule || "");
  return m ? [Number(m[1]), Number(m[2])] : [30, 30];
}

function overlaps(a0: number, a1: number, b0: number, b1: number): boolean {
  return a0 < b1 && b0 < a1;
}

export function PassPage({ id }: { id: string }) {
  const { adapter, tz, meta } = useApp();
  const { data: p, loading } = useAsync(() => adapter.pass(id), [adapter, id]);
  if (loading) return <Loading what="pass" />;
  if (!p) return <Missing what="Pass" id={id} />;
  const n = p.n_contacts || {};
  const total = Object.values(n).reduce((a, b) => a + (b || 0), 0);
  // The identification result leads whenever any contact could be checked against AIS (matched or unmatched); the
  // coverage result leads only when none could (spec 4.7).
  const checked = (n.matched || 0) + (n.unmatched || 0);
  const mostlyNoCov = total > 0 && checked === 0 && (n.no_coverage || 0) > 0;
  const live = p.pass_id.startsWith("live_");
  const [before, after] = windowMinutes(meta.live_rules?.ais_window);
  const w0 = Date.parse(p.start_utc) - before * 60e3;
  const w1 = Date.parse(p.stop_utc || p.start_utc) + after * 60e3;
  const gaps = ((meta.ais_recording?.gaps || []) as { from_utc?: string; to_utc?: string; start_utc?: string; end_utc?: string; minutes?: number }[])
    .map((g) => ({ from: g.from_utc || g.start_utc || "", to: g.to_utc || g.end_utc || "", minutes: g.minutes }))
    .filter((g) => g.from && g.to && overlaps(Date.parse(g.from), Date.parse(g.to), w0, w1));
  const scenes = p.scene_counts || [];
  const recordedHours = scenes.reduce((m, s) => Math.max(m, typeof s.ais_recorded_hours === "number" ? s.ais_recorded_hours : 0), 0);
  return (
    <article className="scs-page" data-page="pass" data-pass-id={p.pass_id}>
      <ObjectHeader title={p.pass_id} icon="satellite" back="leads" subtitle={`${sentinelName(p.mission)} pass, relative orbit ${p.relative_orbit ?? "?"}, ${p.pass_dir?.toLowerCase() ?? ""}, ${fmtTime(p.start_utc, tz)} to ${fmtTime(p.stop_utc, tz, false)}`}
        tags={<span className="scs-tagrow"><Tag minimal intent={p.status === "upcoming" ? "primary" : undefined}>{p.status}</Tag>{p.sources.map((s) => <Tag key={s} minimal>{s === "esa_plan" ? "ESA acquisition plan" : s === "repeat_cycle" ? "12-day repeat prediction (not ESA's plan)" : s}</Tag>)}{p.research_only && <Tag minimal intent="warning">research</Tag>}</span>} />
      {p.processed && total > 0 && (
        <Callout intent={mostlyNoCov ? "primary" : undefined} compact icon="cell-tower" data-coverage-result={mostlyNoCov ? "1" : undefined} data-id-result="1">
          {mostlyNoCov
            ? <>Coverage result: {p.ais_footprint_positions === 0 ? "No AIS was heard inside" : `${fmtNum(p.ais_footprint_positions)} AIS positions were heard inside`}
              {p.ais_near_footprint_mmsi === 0 ? " or within 0.3 degree of" : ""} the {p.scenes.length} scene{p.scenes.length === 1 ? "" : "s"}, so {fmtNum(n.no_coverage || 0)} of the {fmtNum(total)} contacts could not be checked against AIS.
              {p.ais_aoi_positions ? ` The feed was up: ${fmtNum(p.ais_aoi_positions)} positions were recorded elsewhere in the AOI during the scene windows.` : ""} </>
            : <>Identification result: of {fmtNum(total)} contacts, <MatchedSplit pass={p} matched={n.matched || 0} />, {fmtNum(n.unmatched || 0)} with no AIS match although AIS was heard nearby, {fmtNum(n.no_coverage || 0)} where no AIS was heard (no coverage: nothing can be said about them).
              {p.ais_footprint_mmsi ? ` ${fmtNum(p.ais_footprint_mmsi)} MMSI were heard inside the footprint during the window.` : ""} </>}
          {DARK_CAVEAT_SHORT}
        </Callout>
      )}
      {p.note && <p className="scs-muted" style={{ fontSize: 12 }}>{p.note}</p>}
      {/* Identification first (owner priority P0): the pass's contacts with their identities, then the AIS-only vessels. */}
      {p.processed && <PassContacts pass={p} />}
      {p.processed && live && <AisOnly pass={p} />}
      <Section title="Pass" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Status" rec={p} field="status" value={`${p.status}${p.status === "upcoming" ? ` (${fmtRelative(p.start_utc)})` : ""}`} />
            <Field label="Source" rec={p} field="sources" value={p.sources.join(", ")} />
            <Field label="AOI overlap" rec={p} field="aoi_overlap_km2" value={p.aoi_overlap_km2 === null ? null : `${fmtNum(p.aoi_overlap_km2)} km2`} />
            <Field label="AOI parts" rec={p} field="aoi_parts" value={p.aoi_parts?.join(", ")} />
            <Field label="Scenes" rec={p} field="scenes" value={p.scenes.length ? `${p.scenes.length} processed` : p.processed ? "none" : "not processed yet"} />
            {p.ais_heard_share !== null && p.ais_heard_share !== undefined && <Field label="Share of the planned footprint with AIS heard so far" rec={p} field="ais_heard_share" value={pct(p.ais_heard_share, 1)} />}
          </div>
          {p.fixture_note && <p className="scs-muted" style={{ fontSize: 12 }}>{p.fixture_note}</p>}
        </SectionCard>
      </Section>
      {p.processed && live && (
        <Section title="AIS recorded during the window" compact collapsible>
          <SectionCard>
            <div className="scs-fields" data-ais-window="1">
              <Field label="AIS recording window" rec={p} field="ais_footprint_positions" value={`${fmtTime(new Date(w0).toISOString(), tz, false)} to ${fmtTime(new Date(w1).toISOString(), tz, false)} (${before} min before the first scene to ${after} min after the last)`} />
              <Field label="1. Inside the footprint" rec={p} field="ais_footprint_positions" value={`${fmtNum(p.ais_footprint_positions)} positions, ${fmtNum(p.ais_footprint_mmsi)} MMSI`} />
              <Field label="2. Within 0.3 degree of it (what the matcher sees)" rec={p} field="ais_near_footprint_mmsi" value={`${fmtNum(p.ais_near_footprint_mmsi)} MMSI`} />
              <Field label="3. Anywhere in the AOI (only shows that the feed was up)" rec={p} field="ais_aoi_positions" value={`${fmtNum(p.ais_aoi_positions)} positions, ${fmtNum(p.ais_aoi_mmsi)} MMSI`} />
              {recordedHours > 0 && <Field label="Hours of AIS recorded before the pass (reach history)" rec={p} field="ais_footprint_positions" value={`${fmtNum(recordedHours)} h`} />}
              <Field label="Recorder outages overlapping the window" rec={p} field="ais_footprint_positions" value={gaps.length ? gaps.map((g) => `${fmtTime(g.from, tz, false)} to ${fmtTime(g.to, tz, false)}`).join("; ") : "none recorded"} />
            </div>
            <p className="scs-muted" style={{ fontSize: 12 }}>The AIS source is the aisstream.io relay of shore receivers ({AISSTREAM_LABEL}). A recorder outage means nothing was heard then; it says nothing about any vessel.</p>
          </SectionCard>
        </Section>
      )}
      {p.processed && (
        <Section title="Contacts by AIS status" compact collapsible>
          <SectionCard>
            <div className="scs-fields" data-pass-counts="1">
              {STATUS_ORDER.map((s) => <Field key={s} label={AIS_STATUS_LABEL[s]} rec={p} field="n_contacts" value={fmtNum(n[s] || 0)} />)}
            </div>
            {scenes.length > 0 && (
              <div className="scs-table-scroll">
                <table className="bp6-html-table bp6-compact" style={{ width: "100%" }} data-scene-table="1">
                  <thead><tr><th>scene</th><th>contacts</th><th>matched</th><th>no AIS match</th><th>no coverage</th><th>AIS in footprint</th><th>AIS in the AOI</th></tr></thead>
                  <tbody>{scenes.map((s) => (
                    <tr key={String(s.product_id)}>
                      <td style={{ overflowWrap: "anywhere", minWidth: 140 }}>{String(s.product_id)}</td><td>{fmtNum(s.n_contacts as number)}</td>
                      <td>{fmtNum((s.n_matched as number) ?? null)}</td><td>{fmtNum((s.n_unmatched as number) ?? null)}</td><td>{fmtNum((s.n_no_coverage as number) ?? null)}</td>
                      <td>{fmtNum((s.ais_footprint_positions as number) ?? null)} pos, {fmtNum((s.ais_footprint_mmsi as number) ?? null)} MMSI</td>
                      <td>{fmtNum((s.ais_aoi_positions as number) ?? null)} pos, {fmtNum((s.ais_aoi_mmsi as number) ?? null)} MMSI</td>
                    </tr>))}</tbody>
                </table>
              </div>
            )}
            <p className="scs-muted" style={{ fontSize: 12 }}>{DARK_CAVEAT_SHORT} no_coverage contacts never form L1 leads.</p>
          </SectionCard>
        </Section>
      )}
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={p} fields={["pass_id", "start_utc", "status", "sources", "ais_footprint_positions", "ais_near_footprint_mmsi", "ais_aoi_positions", "n_contacts"]} /></SectionCard></Section>
    </article>
  );
}

/** Board D6.2 on the result line: matches split into identifications (high or medium quality, not graded doubtful by the
 * hand check) and low-quality pairings, which are not identifications. The local app's list rows are summaries without
 * the hand-check grade, so its matched rows are read in full (a pass has tens of matches, not thousands). */
function MatchedSplit({ pass, matched }: { pass: Pass; matched: number }) {
  const { adapter } = useApp();
  const { data } = useAsync(async () => {
    if (!matched) return { ident: 0, low: 0, of: 0 };
    const r = await adapter.contacts({ pass_id: pass.pass_id, ais_status: "matched", limit: 500 });
    const rows = adapter.kind === "http" && r.items.length <= 200 ? await Promise.all(r.items.map((c) => adapter.contact(c.det_id).catch(() => null).then((x) => x || c))) : r.items;
    const low = rows.filter((c) => lowQualityPairing(c)).length;
    return { ident: rows.length - low, low, of: rows.length };
  }, [adapter, pass.pass_id, matched]);
  if (!matched) return <>0 matched to an AIS vessel</>;
  if (!data || data.of !== matched) return <span data-matched-split="pending">{fmtNum(matched)} matched to an AIS vessel (named below with the quality of each pairing)</span>;
  return <span data-matched-split="1"><strong>{fmtNum(data.ident)} identified</strong> (an AIS pairing of high or medium quality that the hand check does not doubt), {fmtNum(data.low)} {data.low === 1 ? "low-quality pairing" : "low-quality pairings"} whose identity is not confirmed</span>;
}

/** The pass's contacts, one AIS status at a time, with the identity or the evidence that status calls for. */
function PassContacts({ pass }: { pass: Pass }) {
  const { adapter, phone, meta } = useApp();
  const counts = pass.n_contacts || {};
  const firstTab = STATUS_ORDER.find((s) => (counts[s] || 0) > 0) || "matched";
  const [tab, setTab] = useState<AisStatus>(firstTab);
  const [offset, setOffset] = useState(0);
  useEffect(() => { setTab(firstTab); setOffset(0); }, [pass.pass_id, firstTab]);
  // The local app's list returns summaries; the page's rows are read in full (review note, ambiguity, candidates).
  const { data, loading } = useAsync(async () => {
    const r = await adapter.contacts({ pass_id: pass.pass_id, ais_status: tab, limit: PAGE, offset, sort: "-cnn_score" });
    if (adapter.kind !== "http" || tab === "no_coverage") return r;
    const full = (await Promise.all(r.items.map((c) => adapter.contact(c.det_id).catch(() => null)))).map((x, i) => x || r.items[i]);
    if (tab !== "unmatched") return { ...r, items: full };
    // The local app's contact record names the nearest AIS vessel by MMSI only: read the names of the rows on screen
    // from the vessel table (an MMSI outside it keeps its number).
    const keys = [...new Set(full.filter((c) => !c.nearest_vessel_name && c.nearest_ais_mmsi).map((c) => c.nearest_vessel_key || `mmsi:${c.nearest_ais_mmsi}`))];
    const names = new Map((await Promise.all(keys.map(async (k) => [k, (await adapter.vessel(k).catch(() => null))?.name || null] as const))));
    return { ...r, items: full.map((c) => (c.nearest_vessel_name || !c.nearest_ais_mmsi ? c : { ...c, nearest_vessel_name: names.get(c.nearest_vessel_key || `mmsi:${c.nearest_ais_mmsi}`) ?? null })) };
  }, [adapter, pass.pass_id, tab, offset]);
  const items = data?.items || [];
  const total = data?.total ?? 0;
  const research = meta.build === "research";
  const label = useMemo(() => (items.some((c) => c.ais_source === "aisstream") ? AISSTREAM_LABEL : null), [items]);
  return (
    <Section title="Contacts of this pass" compact collapsible>
      <SectionCard>
        <ButtonGroup className="scs-status-tabs" data-pass-tabs="1" style={{ flexWrap: "wrap" }}>
          {STATUS_ORDER.filter((s) => (counts[s] || 0) > 0 || s === tab).map((s) => (
            <Button key={s} small active={tab === s} onClick={() => { setTab(s); setOffset(0); }} data-tab-status={s}>
              <StatusChip status={s} short /> {fmtNum(counts[s] || 0)}
            </Button>
          ))}
        </ButtonGroup>
        {tab === "matched" && <p style={{ fontSize: 12, margin: "6px 0" }} data-identity-label="1">Identity of every matched contact: {label || (research ? meta.research_label || "research source" : AISSTREAM_LABEL)}. Every identity field is a transponder self-report; flag is the country of the MMSI's MID, as claimed. A low-quality pairing, or one the hand check grades doubtful, is marked "{LOW_QUALITY_LABEL}" and is not an identification (board D6.2).</p>}
        {tab === "unmatched" && <p style={{ fontSize: 12, margin: "6px 0" }}>No AIS match although AIS was heard near the contact during the window. Nearest AIS vessel placed at the scene time; {label || AISSTREAM_LABEL}. Ambiguous contacts list their candidate vessels and are never leads; fixed returns are never leads. {DARK_CAVEAT_SHORT}</p>}
        {tab === "no_coverage" && <p style={{ fontSize: 12, margin: "6px 0" }} data-nocov-rule="1">Rule: {(meta.live_rules?.no_coverage_rule || "no AIS position heard during the window in the contact's 0.25 degree cell or within 20 km").trim().replace(/\.+$/, "")}. This says nothing about the contact.</p>}
        {loading && <p className="scs-muted">Loading contacts.</p>}
        {!loading && items.length === 0 && <p className="scs-muted" data-pass-contacts="none">{pass.contacts_in_bundle === false ? "Contacts of this pass are in the local app." : "No contact with this status in this build."}</p>}
        {!loading && items.length > 0 && (phone ? <ContactCards items={items} /> : <ContactTable items={items} tab={tab} />)}
        {total > PAGE && (
          <div className="scs-pager" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <Button small icon="arrow-left" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} aria-label="Previous contacts" />
            <span className="scs-muted" style={{ fontSize: 12 }}>{fmtNum(offset + 1)} to {fmtNum(Math.min(total, offset + PAGE))} of {fmtNum(total)} in this build, by CNN score</span>
            <Button small icon="arrow-right" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} aria-label="Next contacts" />
          </div>
        )}
        <p className="scs-muted" style={{ fontSize: 11 }}>Radar length: {LENGTH_NOTE}.</p>
      </SectionCard>
    </Section>
  );
}

function H({ label, src, field }: { label: string; src: string | null; field: string }) {
  return <th><span style={{ whiteSpace: "nowrap" }}>{label} <ProvChip sourceKey={src} field={field} /></span></th>;
}

/** One AIS status at a time; the tab is the status, so the identity (or the evidence) comes first and the radar columns last. */
function ContactTable({ items, tab }: { items: Contact[]; tab: AisStatus }) {
  const { units } = useApp();
  const c0 = items[0];
  const src = (f: string) => (c0.prov && c0.prov[f]) || c0.src;
  const radarHead = <><H label="radar length, class" src={c0.src} field="length_est_m" /><H label="CNN" src={src("cnn_score")} field="cnn_score" /></>;
  const radarCells = (c: Contact) => <><td>{c.length_est_m === null ? "?" : `${fmtNum(c.length_est_m)} m`}, {c.confidence}</td><td className="judgment">{c.cnn_score === null ? "none" : c.cnn_score.toFixed(2)}</td></>;
  return (
    <div className="scs-table-scroll">
      <table className="bp6-html-table bp6-compact scs-pass-table" data-pass-contacts={tab}>
        <thead>
          <tr>
            <H label="contact" src={c0.src} field="det_id" />
            {tab === "matched" && <><H label="name" src={src("vessel_name")} field="vessel_name" /><H label="MMSI" src={src("mmsi")} field="mmsi" /><H label="quality" src={src("match_quality")} field="match_quality" /><H label="hand check" src={src("review_note")} field="review_note" /><H label="distance" src={src("match_dist_m")} field="match_dist_m" /><H label="time offset" src={src("match_dt_s")} field="match_dt_s" /><H label="ship type" src={src("ship_type")} field="ship_type" /><H label="AIS length" src={src("length_ais_m")} field="length_ais_m" />{radarHead}<H label="call sign" src={src("call_sign")} field="call_sign" /><H label="flag (as claimed)" src={src("flag")} field="flag" /></>}
            {tab === "unmatched" && <><H label="lead or ambiguity" src={src("dark_lead")} field="dark_lead" /><H label="nearest AIS vessel" src={src("nearest_ais_mmsi")} field="nearest_ais_mmsi" /><H label="distance" src={src("nearest_ais_dist_m")} field="nearest_ais_dist_m" /><H label="time offset" src={src("nearest_ais_dt_s")} field="nearest_ais_dt_s" /><H label="AIS within 10 km" src={src("n_ais_10km")} field="n_ais_10km" /><H label="AIS reach" src={src("ais_reach")} field="ais_reach" />{radarHead}</>}
            {tab === "no_coverage" && <><H label="coverage" src={src("ais_reach")} field="ais_reach" /><H label="nearest AIS anywhere" src={src("nearest_ais_dist_m")} field="nearest_ais_dist_m" />{radarHead}</>}
            {tab === "not_checked" && radarHead}
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.det_id} data-det-id={c.det_id}>
              <td><ObjectLink type="contact" id={c.det_id} /></td>
              {tab === "matched" && <>
                <td>{c.vessel_name || <span className="scs-muted">no name heard</span>}</td>
                <td>{c.mmsi ? <ObjectLink type="vessel" id={c.vessel_key || `mmsi:${c.mmsi}`} label={c.mmsi} /> : "?"}</td>
                <td className="judgment" data-quality={c.match_quality || ""}>{c.match_quality || "?"}{c.match_ambiguous ? " (ambiguous)" : ""}{lowQualityPairing(c) ? <Tag minimal intent="warning" style={{ marginLeft: 4 }} data-low-quality="1">{LOW_QUALITY_LABEL}</Tag> : null}</td>
                <td className="judgment">{reviewGrade(c) || (c.review_note ? "noted" : "not checked")}</td>
                <td>{fmtMetres(c.match_dist_m, units)}</td><td>{fmtDuration(c.match_dt_s)}</td>
                <td>{shipTypeText(c.ship_type) || "?"}</td><td>{c.length_ais_m === null ? "?" : `${fmtNum(c.length_ais_m)} m`}</td>
                {radarCells(c)}
                <td>{c.call_sign || "?"}</td><td>{c.flag || "?"}</td>
              </>}
              {tab === "unmatched" && <>
                <td><LeadOrAmbiguity c={c} /></td>
                <td data-nearest="1">{c.nearest_ais_mmsi ? <ObjectLink type="vessel" id={c.nearest_vessel_key || `mmsi:${c.nearest_ais_mmsi}`} label={c.nearest_vessel_name || c.nearest_ais_mmsi} /> : "none heard"}</td>
                <td>{fmtMetres(c.nearest_ais_dist_m, units)}</td><td>{fmtDuration(c.nearest_ais_dt_s)}</td><td>{fmtNum(c.n_ais_10km)}</td><td>{pct(c.ais_reach)}</td>
                {radarCells(c)}
              </>}
              {tab === "no_coverage" && <>
                <td>nothing heard in the cell or within 20 km{c.ais_reach !== null ? `; reach ${pct(c.ais_reach)}` : ""}</td>
                <td>{c.nearest_ais_dist_m === null ? "none" : fmtMetres(c.nearest_ais_dist_m, units)}</td>
                {radarCells(c)}
              </>}
              {tab === "not_checked" && radarCells(c)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContactCards({ items }: { items: Contact[] }) {
  const { units } = useApp();
  return (
    <div className="scs-cards" data-pass-contacts="cards">
      {items.map((c) => (
        <div key={c.det_id} className="scs-card" data-det-id={c.det_id}>
          <div className="title"><ObjectLink type="contact" id={c.det_id} /></div>
          <div className="row"><StatusChip status={c.ais_status} short /><span>{c.length_est_m === null ? "?" : `${fmtNum(c.length_est_m)} m`}, {c.confidence}, CNN {c.cnn_score === null ? "none" : c.cnn_score.toFixed(2)}</span></div>
          {c.ais_status === "matched" && <div className="row" style={{ flexWrap: "wrap" }}><span>{c.vessel_name || "no name heard"}</span><span>MMSI {c.mmsi ? <ObjectLink type="vessel" id={c.vessel_key || `mmsi:${c.mmsi}`} label={c.mmsi} /> : "?"}</span><span>{c.call_sign || ""} {c.flag || ""} {shipTypeText(c.ship_type) || ""}</span><span>{fmtMetres(c.match_dist_m, units)}, {fmtDuration(c.match_dt_s)}, <span className="judgment" data-quality={c.match_quality || ""}>{c.match_quality || "?"} quality</span></span>{lowQualityPairing(c) ? <Tag minimal intent="warning" data-low-quality="1">{LOW_QUALITY_LABEL}</Tag> : null}</div>}
          {c.ais_status === "unmatched" && <div className="row" style={{ flexWrap: "wrap" }}><span data-nearest="1">nearest AIS {c.nearest_ais_mmsi ? <ObjectLink type="vessel" id={c.nearest_vessel_key || `mmsi:${c.nearest_ais_mmsi}`} label={c.nearest_vessel_name || c.nearest_ais_mmsi} /> : "none"}</span><span>{fmtMetres(c.nearest_ais_dist_m, units)}, {fmtDuration(c.nearest_ais_dt_s)}</span><LeadOrAmbiguity c={c} /></div>}
          {c.ais_status === "no_coverage" && <div className="row"><span>nothing heard in the cell or within 20 km</span></div>}
        </div>
      ))}
    </div>
  );
}

/** Unmatched row: the candidates of an ambiguous contact (never a lead), else its lead or why it has none. */
function LeadOrAmbiguity({ c }: { c: Contact }) {
  if (isAmbiguous(c)) {
    const cand = ambiguousCandidates(c);
    return <span data-ambiguous="1">ambiguous, not a lead; candidates {cand.length ? cand.map((m, i) => <span key={m}>{i ? ", " : ""}<ObjectLink type="vessel" id={`mmsi:${m}`} label={m} /></span>) : "in the record"}</span>;
  }
  if (c.lead_ids && c.lead_ids.length) return <span data-dark-lead="1">dark lead: {c.lead_ids.map((l) => <ObjectLink key={l} type="lead" id={l} label={l.replace(/^L1-/, "L1 ")} />)}</span>;
  if (c.dark_lead) return <span data-dark-lead="1">dark lead candidate (no lead in this build)</span>;
  if (c.confidence === "fixed") return <span className="scs-muted">fixed return, never a lead</span>;
  return <span className="scs-muted">no lead</span>;
}

const AIS_ONLY_PAGE = 25;

/** AIS vessels placed in the footprint that no contact matched (contract 1.3.0 `ais_only`), tested sea first. */
function AisOnly({ pass }: { pass: Pass }) {
  const { units, phone } = useApp();
  const [offset, setOffset] = useState(0);
  const rows = useMemo(() => [...(pass.ais_only || [])].sort((a, b) => Number(!!b.on_tested_sea) - Number(!!a.on_tested_sea) || (b.length_ais_m ?? 0) - (a.length_ais_m ?? 0)), [pass.ais_only]);
  const n = pass.n_ais_only ?? rows.length;
  if (!n && !rows.length) return null;
  const page = rows.slice(offset, offset + AIS_ONLY_PAGE);
  const fate = (v: AisOnlyVessel) => v.ambiguous_det_id ? <>held back as ambiguous with <ObjectLink type="contact" id={v.ambiguous_det_id} /></> : v.oversized_det_id ? "paired with an oversized return (no identity given)" : v.on_tested_sea ? "on tested sea, no contact" : "not on tested sea (shore buffer or land cells)";
  return (
    <Section title={`AIS vessels with no matched contact (${fmtNum(n)})`} compact collapsible data-ais-only="1">
      <SectionCard>
        <p className="scs-muted" style={{ fontSize: 12 }}>{AIS_ONLY_NOTE} Identity source: {pass.identity_label || AISSTREAM_LABEL}.</p>
        {!rows.length && <p className="scs-muted" data-ais-only="none">The list is in the local app ({fmtNum(n)} vessels).</p>}
        {rows.length > 0 && (phone ? (
          <div className="scs-cards" data-ais-only-rows="cards">
            {page.map((v, i) => (
              <div key={`${v.mmsi}-${i}`} className="scs-card" data-mmsi={v.mmsi || ""}>
                <div className="title">{v.vessel_name || "no name heard"}</div>
                <div className="row" style={{ flexWrap: "wrap" }}><span>MMSI {v.mmsi ? <ObjectLink type="vessel" id={v.vessel_key || `mmsi:${v.mmsi}`} label={v.mmsi} /> : "?"}</span><span>{shipTypeText(v.ship_type) || "type not reported"}, {v.length_ais_m ? `${fmtNum(v.length_ais_m)} m` : "length unknown"}</span><span>{fate(v)}</span></div>
              </div>
            ))}
          </div>
        ) : (
          <div className="scs-table-scroll">
            <table className="bp6-html-table bp6-compact scs-pass-table" data-ais-only-rows="table">
              <thead><tr><th>name</th><th>MMSI</th><th>type</th><th>AIS length</th><th>SOG</th><th>placed by</th><th>to the coast</th><th>nearest radar object</th><th>fate</th></tr></thead>
              <tbody>
                {page.map((v, i) => (
                  <tr key={`${v.mmsi}-${i}`} data-mmsi={v.mmsi || ""}>
                    <td>{v.vessel_name || <span className="scs-muted">no name heard</span>}</td>
                    <td>{v.mmsi ? <ObjectLink type="vessel" id={v.vessel_key || `mmsi:${v.mmsi}`} label={v.mmsi} /> : "?"}</td>
                    <td>{shipTypeText(v.ship_type) || "?"}</td>
                    <td>{v.length_ais_m ? `${fmtNum(v.length_ais_m)} m` : "?"}</td>
                    <td>{typeof v.sog_kn === "number" ? `${v.sog_kn.toFixed(1)} kn` : "?"}</td>
                    <td>{v.pred_method || "?"}{typeof v.pred_dt_s === "number" ? `, ${fmtDuration(v.pred_dt_s)}` : ""}</td>
                    <td>{typeof v.dist_coast_km === "number" ? fmtMetres(v.dist_coast_km * 1000, units) : "?"}</td>
                    <td>{typeof v.nearest_object_m === "number" ? `${fmtMetres(v.nearest_object_m, units)}${v.nearest_object_class ? ` (${v.nearest_object_class})` : ""}` : "none"}</td>
                    <td>{fate(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {rows.length > AIS_ONLY_PAGE && (
          <div className="scs-pager" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <Button small icon="arrow-left" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - AIS_ONLY_PAGE))} aria-label="Previous AIS vessels" />
            <span className="scs-muted" style={{ fontSize: 12 }}>{fmtNum(offset + 1)} to {fmtNum(Math.min(rows.length, offset + AIS_ONLY_PAGE))} of {fmtNum(rows.length)}, tested sea first</span>
            <Button small icon="arrow-right" disabled={offset + AIS_ONLY_PAGE >= rows.length} onClick={() => setOffset(offset + AIS_ONLY_PAGE)} aria-label="Next AIS vessels" />
          </div>
        )}
        <p className="scs-muted" style={{ fontSize: 11 }}>{DARK_CAVEAT_SHORT} An AIS vessel the radar did not report is not evidence about that vessel either.</p>
      </SectionCard>
    </Section>
  );
}
