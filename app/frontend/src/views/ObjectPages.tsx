// Object pages (spec sections 4.2 to 4.7): Contact, Vessel, Lead, Light, Event, Pass. Every field has a provenance chip.
import { useEffect, useMemo, useState } from "react";
import { Button, ButtonGroup, Callout, Section, SectionCard, Tag } from "@blueprintjs/core";
import type { CellRecord, Contact, EventRecord, Lead, LeadEvidence, Light, Pass, Track, Vessel } from "../adapters/types";
import { useApp, useAsync } from "../app/state";
import {
  AISSTREAM_NOTE, AIS_STATUS_LABEL, AIS_STATUS_MEANING, CHANGE_TEXT, CNN_HELD_OUT, CNN_LIMITS, DARK_CAVEAT_SHORT, EEZ_STATEMENT, FACTOR_LABEL,
  GAP_NOTE, IDENTITY_NOTE, LAWFUL_TEXT, LENGTH_NOTE, LIGHT_NOTE, OCEAN_NOTE, PRODUCT_CAVEAT, codeText,
} from "../app/text";
import { fmtDtg, fmtDuration, fmtMetres, fmtNum, fmtRelative, fmtTime, pct, sentinelName } from "../app/format";
import { Field, ProvenanceTable } from "./Provenance";
import { CaveatCallout, ConfidenceTag, CoordBlock, Loading, Missing, NotBuilt, ObjectHeader, ObjectLink, StatusChip, copyText, downloadText } from "./common";
import { ChipImage } from "./ChipImage";
import { LeadCard, ExportDecisions } from "./LeadCard";
import { MapView } from "../map/MapView";
import { fmtDd, fmtDms, fmtMgrs } from "../app/format";

const CONTACT_FIELDS = ["det_id", "acq_utc", "lon", "lat", "length_est_m", "confidence", "cnn_score", "cnn_vessel", "ais_status", "match_method", "match_dist_m", "match_dt_s",
  "match_quality", "mmsi", "imo", "vessel_name", "call_sign", "flag", "ship_type", "length_ais_m", "identity_source", "nearest_ais_mmsi", "nearest_ais_dist_m", "nearest_ais_dt_s",
  "n_ais_10km", "ais_reach", "scr_vv_db", "scr_vh_db", "inc_angle_deg", "persist_dates", "n_low_1km", "near_fixed_m"];

function exportJson(name: string, rec: unknown) {
  downloadText(name, JSON.stringify(rec, null, 1), "application/json");
}

const EXPORT_LICENCE = "each source's own licence (see the provenance of each field and the About view)";

function toGeoJson(rec: Record<string, unknown>, lon: number, lat: number, caveat: string, build: string) {
  // Spec section 8: caveat, build and licence on every feature and in the collection's meta.
  return { type: "FeatureCollection", meta: { caveat, build, licence: EXPORT_LICENCE }, features: [{ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { ...rec, caveat, build, licence: EXPORT_LICENCE } }] };
}

export function ContactPage({ id }: { id: string }) {
  const { adapter, tz, units, meta, focusMap, phone } = useApp();
  const { data: c, loading } = useAsync(() => adapter.contact(id), [adapter, id]);
  const { data: nearest } = useAsync(() => (c?.nearest_vessel_key ? adapter.vessel(c.nearest_vessel_key) : Promise.resolve(null)), [adapter, c?.nearest_vessel_key]);
  const { data: leads } = useAsync(async () => (c ? (await Promise.all(c.lead_ids.map((l) => adapter.lead(l)))).filter(Boolean) as Lead[] : []), [adapter, c?.det_id]);
  const { data: pass } = useAsync(() => (c?.pass_id ? adapter.pass(c.pass_id) : Promise.resolve(null)), [adapter, c?.pass_id]);
  const { data: nearby } = useAsync(async () => {
    if (!c) return [];
    const d = 2000 / 111320;
    const r = await adapter.contacts({ bbox: [c.lon - d / Math.cos((c.lat * Math.PI) / 180), c.lat - d, c.lon + d / Math.cos((c.lat * Math.PI) / 180), c.lat + d], pass_id: c.pass_id || undefined, limit: 50 });
    return r.items.filter((x) => x.det_id !== c.det_id);
  }, [adapter, c?.det_id]);
  if (loading) return <Loading what="contact" />;
  if (!c) return <Missing what="Contact" id={id} />;
  const subtitle = `Radar contact, ${sentinelName(c.mission)} IW, ${fmtTime(c.acq_utc, tz)} (${fmtDtg(c.acq_utc)})`;
  const ident = c.ais_status;
  const cnnVerdict = c.cnn_score === null ? "not scored" : c.cnn_vessel ? "CNN accepts" : "CNN rejects";
  return (
    <article className="scs-page" data-page="contact" data-det-id={c.det_id} data-ais-status={c.ais_status}>
      <ObjectHeader title={c.det_id} subtitle={subtitle} icon="satellite" back="leads" tags={<span className="scs-tagrow">
        <ConfidenceTag c={c.confidence} /><Tag minimal className="judgment">{cnnVerdict}</Tag><StatusChip status={c.ais_status} />
        {c.research_only && <Tag minimal intent="warning">research</Tag>}{c.synthetic && <Tag minimal intent="danger">SYNTHETIC fixture status</Tag>}</span>} />
      <ButtonGroup className="scs-actions" style={{ marginBottom: 8 }}>
        <Button small icon="locate" text="Centre map" onClick={() => { focusMap(c.lon, c.lat, 10); if (phone) window.location.hash = "#/map"; }} />
        <Button small icon="clipboard" text="Copy position" onClick={() => copyText(`${fmtDd(c.lat, c.lon)} | ${fmtDms(c.lat, c.lon)} | ${fmtMgrs(c.lat, c.lon)}`)} />
        <Button small icon="export" text="Export GeoJSON" onClick={() => downloadText(`${c.det_id}.geojson`, JSON.stringify(toGeoJson(c as unknown as Record<string, unknown>, c.lon, c.lat, c.caveat, meta.build)), "application/geo+json")} />
        <Button small icon="export" text="Export JSON" onClick={() => exportJson(`${c.det_id}.json`, c)} />
      </ButtonGroup>
      <Section title="Radar chip" compact collapsible><SectionCard><ChipImage det_id={c.det_id} chip={c.chip ?? null} /></SectionCard></Section>
      <Section title="Detection" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Detector class" rec={c} field="confidence"><ConfidenceTag c={c.confidence} /></Field>
            <Field label="Clutter zone (5 or more weak returns within 1 km)" rec={c} field="n_low_1km" value={c.n_low_1km === null ? "not computed for this run" : c.n_low_1km >= 5 ? `yes (${c.n_low_1km} low objects)` : `no (${c.n_low_1km} low objects)`} />
            <Field label="Near fixed structure (within 250 m)" rec={c} field="near_fixed_m" value={c.near_fixed_m === null ? "not computed for this run" : c.near_fixed_m <= 250 ? `yes (${fmtMetres(c.near_fixed_m, units)})` : `no (nearest ${fmtMetres(c.near_fixed_m, units)})`} />
            <Field label="Persistence (bright on earlier passes)" rec={c} field="persist_dates" value={c.persist_dates_checked ? `${c.persist_dates ?? 0} of ${c.persist_dates_checked} earlier passes` : "no earlier pass checked"} />
            <Field label="Radar length estimate" rec={c} field="length_est_m" value={c.length_est_m === null ? "unknown" : `${fmtNum(c.length_est_m)} m (${LENGTH_NOTE})`} />
            <Field label="Signal to clutter VV / VH" rec={c} field="scr_vv_db" value={`${c.scr_vv_db === null ? "none" : c.scr_vv_db.toFixed(1) + " dB"} / ${c.scr_vh_db === null ? "none" : c.scr_vh_db.toFixed(1) + " dB"}`} />
            <Field label="Incidence angle" rec={c} field="inc_angle_deg" value={c.inc_angle_deg === null ? "unknown" : `${c.inc_angle_deg.toFixed(1)} deg`} />
            <Field label="Polarisation class" rec={c} field="pol_class" value={c.pol_class || (c.confidence === "high" ? "VV+VH" : c.confidence === "medium" ? "one channel" : "unknown")} />
            <Field label="Pixels" rec={c} field="n_pixels" value={c.n_pixels === null ? "unknown" : String(c.n_pixels)} />
          </div>
        </SectionCard>
      </Section>
      <Section title="Verification" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="CNN score" rec={c} field="cnn_score" judgment modelId={c.cnn_model_id} value={c.cnn_score === null ? "not scored" : `${c.cnn_score.toFixed(3)} against threshold ${c.cnn_threshold ?? 0.6318} (${cnnVerdict})`} />
            <Field label="Held-out performance" rec={c} field="cnn_model_id" judgment value={CNN_HELD_OUT} />
            <Field label="Knowledge limits" rec={c} field="cnn_model_id" judgment value={CNN_LIMITS} />
            <Field label="Chip valid fraction" rec={c} field="cnn_chip_valid_frac" value={c.cnn_chip_valid_frac === null || c.cnn_chip_valid_frac === undefined ? "unknown" : pct(c.cnn_chip_valid_frac)} />
            <Field label="Optical check" rec={c} field="optical_object" value={c.optical_object === undefined || c.optical_object === null ? "not in the optical sample" : `${c.optical_object ? "object seen" : "no object"}${c.optical_kind ? `, ${c.optical_kind}` : ""}`} />
            <Field label="Satlas infrastructure distance" rec={c} field="satlas_m" value={c.satlas_m === undefined || c.satlas_m === null ? "not in the Satlas sample" : fmtMetres(c.satlas_m, units)} />
          </div>
        </SectionCard>
      </Section>
      <Section title="Identification: who is it" compact collapsible>
        <SectionCard>
          <p><StatusChip status={ident} /> <span className="scs-muted">{AIS_STATUS_MEANING[ident]}</span></p>
          {ident === "matched" && (
            <div className="scs-fields" data-identification="matched">
              <Field label="MMSI" rec={c} field="mmsi" value={c.mmsi ? <ObjectLink type="vessel" id={`mmsi:${c.mmsi}`} label={c.mmsi} /> : null} />
              <Field label="Vessel name" rec={c} field="vessel_name" value={c.vessel_name} />
              <Field label="Call sign" rec={c} field="call_sign" value={c.call_sign} />
              <Field label="IMO" rec={c} field="imo" value={c.imo} />
              <Field label="Flag (MID country, as claimed by the transponder)" rec={c} field="flag" value={c.flag} />
              <Field label="Ship type" rec={c} field="ship_type" value={c.ship_type} />
              <Field label="AIS length against radar length" rec={c} field="length_ais_m" value={c.length_ais_m === null ? "no AIS length" : `${fmtNum(c.length_ais_m)} m against ${fmtNum(c.length_est_m)} m${c.length_ratio ? ` (ratio ${c.length_ratio.toFixed(2)})` : ""}`} />
              <Field label="Match distance" rec={c} field="match_dist_m" value={fmtMetres(c.match_dist_m, units)} />
              <Field label="Time offset" rec={c} field="match_dt_s" value={fmtDuration(c.match_dt_s)} />
              <Field label="Gate used" rec={c} field="match_gate_m" value={c.match_gate_m === null ? "unknown" : fmtMetres(c.match_gate_m, units)} />
              <Field label="Method" rec={c} field="match_method" value={c.match_method} />
              <Field label="Match quality" rec={c} field="match_quality" judgment value={c.match_quality ? `${c.match_quality} (rule: ${meta.live_rules?.match_quality_rule || "see the file's about layer"})` : null} />
              <Field label="Identity source" rec={c} field="identity_source" value={c.identity_source} />
            </div>
          )}
          {ident === "matched" && <p className="scs-muted" style={{ fontSize: 12 }}>Every identity field is a transponder self-report. {AISSTREAM_NOTE}</p>}
          {ident === "unmatched" && (
            <div className="scs-fields" data-identification="unmatched">
              <Field label="Nearest AIS vessel" rec={c} field="nearest_ais_mmsi" value={c.nearest_ais_mmsi ? <><ObjectLink type="vessel" id={`mmsi:${c.nearest_ais_mmsi}`} label={nearest?.name || c.nearest_ais_mmsi} /> (MMSI {c.nearest_ais_mmsi}), {fmtMetres(c.nearest_ais_dist_m, units)} away, {fmtDuration(c.nearest_ais_dt_s)} offset</> : "none heard"} />
              <Field label="AIS vessels within 10 km during the window" rec={c} field="n_ais_10km" value={c.n_ais_10km === null ? "unknown" : String(c.n_ais_10km)} />
              <Field label="AIS reach of the cell (share of recorded hours with any AIS)" rec={c} field="ais_reach" value={pct(c.ais_reach)} />
              <Field label="VIIRS light within 2 km the same night" rec={c} field="light_ids" value="see Links below" />
              <Field label="Lead" rec={c} field="lead_ids" value={c.lead_ids.length ? c.lead_ids.map((l) => <ObjectLink key={l} type="lead" id={l} />) : "no lead (the L1 rule adds CNN, weather and clutter conditions)"} />
            </div>
          )}
          {ident === "no_coverage" && (
            <div data-identification="no_coverage">
              <p>No AIS coverage here: nothing was heard in this cell during the window. {c.ais_reach !== null ? `AIS reach of the cell: ${pct(c.ais_reach)}.` : ""}</p>
              <p className="scs-muted" style={{ fontSize: 12 }}>Rule: {meta.live_rules?.no_coverage_rule || "no AIS position recorded during the window in the contact's 0.25 degree cell or within 20 km"}. This says nothing about the contact.</p>
              {c.nearest_ais_mmsi && <Field label="Nearest AIS vessel heard anywhere, for scale" rec={c} field="nearest_ais_mmsi" value={<><ObjectLink type="vessel" id={`mmsi:${c.nearest_ais_mmsi}`} label={nearest?.name || c.nearest_ais_mmsi} />, {fmtMetres(c.nearest_ais_dist_m, units)} away</>} />}
            </div>
          )}
          {ident === "not_checked" && <p data-identification="not_checked">AIS not checked for this run in this build.</p>}
          <p className="scs-muted" style={{ fontSize: 12, marginTop: 8 }}>{DARK_CAVEAT_SHORT}</p>
        </SectionCard>
      </Section>
      <Section title="Context" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Wind (GFS 10 m)" rec={c} field="wind_ms" value={c.wind_ms === undefined || c.wind_ms === null ? "weather unknown for this run" : `${c.wind_ms.toFixed(1)} m/s`} />
            <Field label="Cloud-top temperature (Himawari-9)" rec={c} field="ctt_k" value={c.ctt_k === undefined || c.ctt_k === null ? "unknown" : `${c.ctt_k.toFixed(0)} K${c.deep_convection ? ", deep convection" : ""}`} />
            <Field label="Cell" rec={c} field="cell_id" value={c.cell_id} />
            <Field label="Last radar look" rec={c} field="acq_utc" value={fmtTime(c.acq_utc, tz)} />
            <Field label="Next planned pass" rec={pass} field="start_utc" value={leads && leads[0]?.next_look_utc ? `${fmtTime(leads[0].next_look_utc, tz, false)} (${fmtRelative(leads[0].next_look_utc)})` : "see the Pass pages"} />
          </div>
          <p className="scs-muted" style={{ fontSize: 12 }}>{OCEAN_NOTE} Ocean fields (depth, distance to coast and port, SST, fronts, currents, waves, shipping density) arrive with the cells part.</p>
        </SectionCard>
      </Section>
      <Section title="Position" compact collapsible><SectionCard><CoordBlock lat={c.lat} lon={c.lon} /><div className="scs-object-map"><MapView compact marker={{ lon: c.lon, lat: c.lat }} /></div></SectionCard></Section>
      <Section title="Links" compact collapsible>
        <SectionCard>
          <ul className="scs-linklist">
            <li>Pass: {c.pass_id ? <ObjectLink type="pass" id={c.pass_id} /> : "unknown"}{c.scene_id ? <span className="scs-muted"> scene {c.scene_id}</span> : null}</li>
            <li>Leads citing this contact: {c.lead_ids.length ? c.lead_ids.map((l) => <ObjectLink key={l} type="lead" id={l} />) : "none"}</li>
            <li>Contacts within 2 km on the same pass: {nearby && nearby.length ? nearby.slice(0, 12).map((x) => <span key={x.det_id}><ObjectLink type="contact" id={x.det_id} /> </span>) : "none in this build"}</li>
            <li>Lights within 2 km the same night: see the Light pages (join in the local app)</li>
            <li>Cell: {c.cell_id}</li>
          </ul>
        </SectionCard>
      </Section>
      {leads && leads.length > 0 && <Section title="Lead" compact collapsible><SectionCard><LeadCard lead={leads[0]} /></SectionCard></Section>}
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={c} fields={CONTACT_FIELDS} /></SectionCard></Section>
      {c.extra?.synthetic_note ? <Callout intent="danger" compact>{String(c.extra.synthetic_note)}</Callout> : null}
    </article>
  );
}

export function VesselPage({ id }: { id: string }) {
  const { adapter, tz, units } = useApp();
  const { data: v, loading } = useAsync(() => adapter.vessel(id), [adapter, id]);
  const { data: track } = useAsync(() => adapter.track(id), [adapter, id]);
  const { data: matched } = useAsync(async () => (v ? (await Promise.all(v.contacts_matched.map((d) => adapter.contact(d)))).filter(Boolean) as Contact[] : []), [adapter, v?.vessel_key]);
  if (loading) return <Loading what="vessel" />;
  if (!v) return <Missing what="Vessel" id={id} />;
  const pos = v.last_lon !== null && v.last_lat !== null ? { lon: v.last_lon, lat: v.last_lat } : null;
  return (
    <article className="scs-page" data-page="vessel" data-vessel-key={v.vessel_key}>
      <ObjectHeader title={v.name || v.vessel_key} icon="drive-time" back="leads" subtitle={`AIS vessel, MMSI ${v.mmsi ?? "unknown"}, last heard ${fmtTime(v.last_seen_utc, tz)}`}
        tags={<span className="scs-tagrow">{v.ais_class && <Tag minimal>class {v.ais_class}</Tag>}{v.ship_type && <Tag minimal>{v.ship_type}</Tag>}{v.gear_beacon_like && <Tag minimal intent="warning">MMSI pattern of nets and buoys</Tag>}{v.stub && <Tag minimal>stub</Tag>}</span>} />
      <Callout compact icon="info-sign">{IDENTITY_NOTE} {AISSTREAM_NOTE}</Callout>
      <Section title="Identity" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="MMSI" rec={v} field="mmsi" value={v.mmsi} />
            <Field label="Name" rec={v} field="name" value={v.name} />
            <Field label="Call sign" rec={v} field="call_sign" value={v.call_sign} />
            <Field label="IMO" rec={v} field="imo" value={v.imo} />
            <Field label="Flag (MID country, as claimed)" rec={v} field="flag" value={v.flag ? `${v.flag} (MID ${v.mid})` : v.mid ? `MID ${v.mid}, country not resolved` : null} />
            <Field label="Ship type" rec={v} field="ship_type" value={v.ship_type} />
            <Field label="Length and width" rec={v} field="length_m" value={v.length_m === null ? null : `${fmtNum(v.length_m)} m x ${v.width_m === null ? "?" : fmtNum(v.width_m)} m`} />
            <Field label="AIS class" rec={v} field="ais_class" value={v.ais_class} />
            <Field label="Destination (self-reported)" rec={v} field="destination" value={v.destination} />
            <Field label="ETA (self-reported)" rec={v} field="eta" value={v.eta} />
            <Field label="First heard" rec={v} field="first_seen_utc" value={fmtTime(v.first_seen_utc, tz)} />
            <Field label="Last heard" rec={v} field="last_seen_utc" value={fmtTime(v.last_seen_utc, tz)} />
            <Field label="Static message seen" rec={v} field="static_seen_utc" value={fmtTime(v.static_seen_utc, tz)} />
            <Field label="Positions recorded" rec={v} field="n_positions" value={fmtNum(v.n_positions)} />
            <Field label="Identity source" rec={v} field="identity_source" value={v.identity_source} />
            <Field label="Navigation status (self-reported)" rec={v} field="nav_status_label" value={v.nav_status_label} />
            <Field label="Speed and course at last report" rec={v} field="sog_kn" value={v.sog_kn === null ? null : `${v.sog_kn.toFixed(1)} kn, ${v.cog_deg === null ? "?" : v.cog_deg.toFixed(0)} deg`} />
            <Field label="In the AOI at last report" rec={v} field="in_aoi" value={v.in_aoi === null ? null : v.in_aoi ? "yes" : `no${v.ever_in_aoi ? " (was earlier)" : ""}`} />
          </div>
        </SectionCard>
      </Section>
      <Section title="Identity history" compact collapsible><SectionCard><p className="scs-muted">Static message changes over the recording are read from the daily static files in the local app (event E12). Not in the single-file page.</p></SectionCard></Section>
      <Section title="Track" compact collapsible>
        <SectionCard>
          {track ? (
            <div>
              <p className="scs-muted" style={{ fontSize: 12 }}>{track.points.length} vertices{track.simplified ? ", simplified for display" : ""}, {fmtTime(track.start_utc, tz)} to {fmtTime(track.end_utc, tz)}. {track.note}</p>
              {track.gaps.length > 0 ? (
                <ul className="scs-linklist">{track.gaps.map((g, i) => <li key={i}>Gap {fmtTime(g.from_utc, tz)} to {fmtTime(g.to_utc, tz)} ({fmtNum(g.minutes)} min). {GAP_NOTE}</li>)}</ul>
              ) : <p className="scs-muted" style={{ fontSize: 12 }}>No gap over 6 h computed for this page. {GAP_NOTE}</p>}
              <TrackMap track={track} pos={pos} />
            </div>
          ) : <p className="scs-muted">No track in this page (under 3 positions, or not embedded). {GAP_NOTE}</p>}
        </SectionCard>
      </Section>
      <Section title="Contacts matched" compact collapsible>
        <SectionCard>
          {matched && matched.length ? (
            <table className="bp6-html-table bp6-compact" style={{ width: "100%" }}>
              <thead><tr><th>det_id</th><th>time</th><th>match distance</th><th>time offset</th><th>quality</th></tr></thead>
              <tbody>{matched.map((c) => <tr key={c.det_id}><td><ObjectLink type="contact" id={c.det_id} /></td><td>{fmtTime(c.acq_utc, tz)}</td><td>{fmtMetres(c.match_dist_m, units)}</td><td>{fmtDuration(c.match_dt_s)}</td><td className="judgment">{c.match_quality}</td></tr>)}</tbody>
            </table>
          ) : <p className="scs-muted">No radar contact matched to this vessel in this build.</p>}
        </SectionCard>
      </Section>
      <Section title="Events" compact collapsible><SectionCard><NotBuilt what="aisstream events (silences, encounters, loitering, identity changes)" /></SectionCard></Section>
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={v} fields={["mmsi", "name", "call_sign", "imo", "flag", "ship_type", "length_m", "ais_class", "destination", "first_seen_utc", "last_seen_utc", "n_positions"]} /></SectionCard></Section>
    </article>
  );
}

function TrackMap({ track, pos }: { track: Track; pos: { lon: number; lat: number } | null }) {
  // The compact MapView shows a marker at the last position; the simplified track vertices are listed as a coordinate range.
  const lons = track.points.map((p) => p.lon);
  const lats = track.points.map((p) => p.lat);
  const centre = pos || (track.points.length ? { lon: lons.reduce((a, b) => a + b, 0) / lons.length, lat: lats.reduce((a, b) => a + b, 0) / lats.length } : null);
  return (
    <div>
      {centre && <div className="scs-object-map"><MapView compact marker={centre} /></div>}
      {track.points.length > 0 && <p className="scs-muted" style={{ fontSize: 12 }}>Track extent: {fmtDd(Math.min(...lats), Math.min(...lons))} to {fmtDd(Math.max(...lats), Math.max(...lons))}. Enable the AIS tracks layer on the console map to draw it.</p>}
    </div>
  );
}

export function LeadPage({ id }: { id: string }) {
  const { adapter, tz, decisionsVersion, phone, focusMap } = useApp();
  const [lead, setLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    adapter.lead(id).then((l) => { if (alive) { setLead(l); setLoading(false); } });
    return () => { alive = false; };
  }, [adapter, id, decisionsVersion]);
  const { data: evidence } = useAsync(async () => {
    if (!lead) return [];
    // Contract 5: GET /leads/{id} carries resolved evidence previews, and an item without one is not resolvable in the
    // local app (fetching it would only 404). The embedded bundle has no previews: there records are read from the parts.
    let fetches = 0;
    return Promise.all(lead.evidence.map(async (e): Promise<{ e: LeadEvidence; rec: unknown }> => {
      if (e.preview || adapter.kind === "http") return { e, rec: null };
      if (fetches++ >= 25) return { e, rec: null };
      if (e.type === "contact") return { e, rec: await adapter.contact(e.id) };
      if (e.type === "vessel") return { e, rec: await adapter.vessel(e.id) };
      if (e.type === "light") return { e, rec: await adapter.light(e.id) };
      if (e.type === "pass") return { e, rec: await adapter.pass(e.id) };
      if (e.type === "cell") return { e, rec: await adapter.cell(e.id) };
      return { e, rec: null };
    }));
  }, [adapter, lead?.lead_id]);
  if (loading) return <Loading what="lead" />;
  if (!lead) return <Missing what="Lead" id={id} />;
  return (
    <article className="scs-page" data-page="lead" data-lead-id={lead.lead_id}>
      <ObjectHeader title={lead.lead_id} icon="inbox" back="leads" subtitle={`${lead.title}; ${fmtTime(lead.time_utc, tz)}`} />
      <ButtonGroup style={{ marginBottom: 8 }}>
        <Button small icon="locate" text="Centre map" onClick={() => { focusMap(lead.lon, lead.lat, 10); if (phone) window.location.hash = "#/map"; }} />
        <Button small icon="document" text="Export lead report (HTML)" onClick={() => downloadText(`${lead.lead_id}.html`, leadReportHtml(lead, tz), "text/html")} />
      </ButtonGroup>
      <LeadCard lead={lead} full onUpdate={setLead} />
      <Section title="Evidence" compact collapsible>
        <SectionCard>
          <ul className="scs-linklist" data-evidence="1">
            {(evidence || []).map(({ e, rec }, i) => (
              <li key={i}><Tag minimal>{e.role}</Tag> {e.type}: <ObjectLink type={e.type === "cell" ? "cell" : e.type} id={e.id} />
                {rec && e.type === "contact" ? <span className="scs-muted"> {(rec as Contact).confidence}, {fmtNum((rec as Contact).length_est_m)} m, CNN {(rec as Contact).cnn_score?.toFixed(2) ?? "none"}, {AIS_STATUS_LABEL[(rec as Contact).ais_status]}</span> : null}
                {rec && e.type === "vessel" ? <span className="scs-muted"> {(rec as Vessel).name || ""} MMSI {(rec as Vessel).mmsi}, {(rec as Vessel).ship_type || "type unknown"}</span> : null}
                {rec && e.type === "light" ? <span className="scs-muted"> {(rec as Light).satellite}, {(rec as Light).quality}, {(rec as Light).radiance_nw} nW cm-2 sr-1</span> : null}
                {rec && e.type === "pass" ? <span className="scs-muted"> {(rec as Pass).mission}, {fmtTime((rec as Pass).start_utc, tz)}</span> : null}
                {rec && e.type === "cell" ? <span className="scs-muted"> {(rec as CellRecord).region_box || "other"}, centre {fmtDd((rec as CellRecord).lat, (rec as CellRecord).lon)}</span> : null}
                {!rec && e.preview ? <span className="scs-muted"> {previewText(e.preview, tz)}</span> : null}
                {!rec && !e.preview && <span className="scs-muted"> {adapter.kind === "http" ? "not resolved in this build (no record with this id)" : e.type === "cell" ? "cell context in the local app" : "no preview in this page"}</span>}
              </li>
            ))}
          </ul>
        </SectionCard>
      </Section>
      <Section title="Map of the evidence" compact collapsible><SectionCard><div className="scs-object-map"><MapView compact marker={{ lon: lead.lon, lat: lead.lat }} /></div><CoordBlock lat={lead.lat} lon={lead.lon} /></SectionCard></Section>
      <Section title="Graph" compact collapsible><SectionCard><GraphTab lead={lead} /></SectionCard></Section>
      <Section title="Decisions export" compact collapsible><SectionCard><ExportDecisions highlight={decisionsVersion > 0} noWarning /></SectionCard></Section>
    </article>
  );
}

/** One line from an evidence preview (label, time, radiance, depth and the like), without guessing at missing fields. */
function previewText(p: Record<string, unknown>, tz: "UTC" | "ICT"): string {
  const bits: string[] = [];
  if (p.label) bits.push(String(p.label));
  if (typeof p.time_utc === "string") bits.push(fmtTime(p.time_utc, tz));
  if (typeof p.radiance_nw === "number") bits.push(`${p.radiance_nw} nW cm-2 sr-1`);
  if (typeof p.length_est_m === "number") bits.push(`${fmtNum(p.length_est_m)} m`);
  if (typeof p.cnn_score === "number") bits.push(`CNN ${p.cnn_score.toFixed(2)}`);
  if (typeof p.depth_mean_m === "number") bits.push(`depth ${fmtNum(p.depth_mean_m)} m`);
  if (typeof p.region_box === "string") bits.push(p.region_box);
  return bits.join(", ");
}

function GraphTab({ lead }: { lead: Lead }) {
  // Small node and link picture: lead at the centre, evidence around it (interface brief section 8, kept small).
  const shown = lead.evidence.slice(0, 16);
  const nodes = [{ id: lead.lead_id, type: "lead" }, ...shown.map((e) => ({ id: e.id, type: e.type }))];
  const R = 110;
  const cx = 200;
  const cy = 130;
  return (
    <svg viewBox="0 0 400 260" width="100%" height="260" role="img" aria-label={`Graph of ${lead.lead_id} and ${shown.length} of its ${lead.evidence.length} evidence objects`}>
      {nodes.slice(1).map((n, i) => {
        const a = (i / Math.max(1, nodes.length - 1)) * Math.PI * 2;
        const x = cx + R * Math.cos(a);
        const y = cy + R * Math.sin(a);
        return (
          <g key={n.id}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--text-muted)" strokeWidth={1} />
            <circle cx={x} cy={y} r={14} fill="var(--bg-elevated)" stroke="var(--text-muted)" />
            <text x={x} y={y + 4} textAnchor="middle" fontSize={9} fill="var(--text)">{n.type}</text>
            <text x={x} y={y + 26} textAnchor="middle" fontSize={8} fill="var(--text-muted)">{n.id.length > 22 ? n.id.slice(0, 20) + ".." : n.id}</text>
          </g>
        );
      })}
      <circle cx={cx} cy={cy} r={18} fill="var(--bg-elevated)" stroke="var(--link)" strokeWidth={2} />
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize={10} fill="var(--text)">lead</text>
    </svg>
  );
}

function leadReportHtml(lead: Lead, tz: "UTC" | "ICT"): string {
  const esc = (s: unknown) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
  const caveat = lead.caveat || PRODUCT_CAVEAT;
  const banner = `<div style="background:#f0e8de;color:#77450d;padding:6px 12px;font:12px sans-serif"><b>${esc(DARK_CAVEAT_SHORT)}</b> ${esc(caveat)}</div>`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Lead report ${esc(lead.lead_id)}</title></head><body style="font-family:sans-serif;max-width:800px;margin:0 auto">${banner}
<h1>${esc(lead.lead_id)}</h1><p>${esc(lead.title)}</p><p><b>Review priority</b> ${lead.priority} (${lead.priority_band}, ${esc(lead.priority_model_id)}${lead.calibrated ? "" : ", uncalibrated"}); not a risk score.</p>
<p><b>State</b> ${esc(lead.state)}${lead.reason ? ", reason: " + esc(lead.reason) : ""}</p><p><b>Time</b> ${esc(fmtTime(lead.time_utc, tz))}; <b>position</b> ${esc(fmtDd(lead.lat, lead.lon))}, ${esc(fmtMgrs(lead.lat, lead.lon))}</p>
<h2>Factors</h2><table border="1" cellpadding="4">${lead.factors.map((f) => `<tr><td>${esc(codeText(f.factor, FACTOR_LABEL))}</td><td>${esc(f.value)}</td><td>${f.points} / ${f.max_points}</td><td>${esc(f.source)}</td></tr>`).join("")}</table>
<h2>Evidence</h2><ul>${lead.evidence.map((e) => `<li>${esc(e.role)}: ${esc(e.type)} ${esc(e.id)}</li>`).join("")}</ul>
<h2>Lawful explanations</h2><ul>${lead.lawful_explanations.map((x) => `<li>${esc(codeText(x, LAWFUL_TEXT))}</li>`).join("")}</ul>
<h2>What would change this</h2><ul>${lead.change_indicators.map((x) => `<li>${esc(codeText(x, CHANGE_TEXT))}</li>`).join("")}</ul>
<h2>Decision history</h2><ul>${lead.history.map((h) => `<li>${esc(h.time_utc)} ${esc(h.user)}: ${esc(h.from_state)} to ${esc(h.to_state)}${h.reason ? ", " + esc(h.reason) : ""}${h.note ? ", " + esc(h.note) : ""}</li>`).join("") || "<li>none</li>"}</ul>
${lead.synthetic ? "<p><b>SYNTHETIC fixture lead.</b></p>" : ""}${banner}</body></html>`;
}

export function LightPage({ id }: { id: string }) {
  const { adapter, tz, units, focusMap, phone } = useApp();
  const { data: L, loading } = useAsync(() => adapter.light(id), [adapter, id]);
  if (loading) return <Loading what="light" />;
  if (!L) return <Missing what="Light" id={id} />;
  return (
    <article className="scs-page" data-page="light" data-light-id={L.light_id}>
      <ObjectHeader title={L.light_id} icon="flash" back="leads" subtitle={`VIIRS Day/Night Band light, ${L.satellite}, ${fmtTime(L.time_utc, tz)}, night ${L.night} (local evening date)`}
        tags={<span className="scs-tagrow"><Tag minimal>{L.quality === "clear" ? "clear sky" : "under cloud"}</Tag><Tag minimal>{L.class}</Tag></span>} />
      <Callout compact icon="info-sign">{LIGHT_NOTE}</Callout>
      <ButtonGroup style={{ margin: "8px 0" }}><Button small icon="locate" text="Centre map" onClick={() => { focusMap(L.lon, L.lat, 10); if (phone) window.location.hash = "#/map"; }} /></ButtonGroup>
      <Section title="Light" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Radiance" rec={L} field="radiance_nw" value={`${L.radiance_nw} nW cm-2 sr-1`} />
            <Field label="Spike above background" rec={L} field="spike_nw" value={L.spike_nw === null ? null : `${L.spike_nw} nW cm-2 sr-1`} />
            <Field label="Isolation" rec={L} field="isolation" value={L.isolation === null ? null : L.isolation.toFixed(2)} />
            <Field label="Quality" rec={L} field="quality" value={L.quality === "clear" ? "clear sky" : "under cloud (cloud mask)"} />
            <Field label="Moon illumination" rec={L} field="moon_illum_pct" value={L.moon_illum_pct === null ? null : `${L.moon_illum_pct.toFixed(0)} %`} />
            <Field label="Nights seen within 500 m" rec={L} field="nights_seen_500m" value={`${L.nights_seen_500m} of ${L.clear_nights_cell} clear nights in the cell`} />
            <Field label="Recurring site" rec={L} field="site_id" value={L.site_id ? L.site_id : "none within 500 m in this build"} />
            <Field label="Satlas infrastructure distance" rec={L} field="satlas_infra_m" value={fmtMetres(L.satlas_infra_m, units)} />
            <Field label="Sentinel-1 passes in 90 days over the spot" rec={L} field="s1_passes_90d" value={String(L.s1_passes_90d)} />
            <Field label="Radar contacts within 2 km the same night" rec={L} field="contacts_2km_same_night" value={L.contacts_2km_same_night.length ? L.contacts_2km_same_night.map((d) => <span key={d}><ObjectLink type="contact" id={d} /> </span>) : "none in this build"} />
            <Field label="Cell" rec={L} field="cell_id" value={L.cell_id} />
          </div>
        </SectionCard>
      </Section>
      <Section title="Position" compact collapsible><SectionCard><CoordBlock lat={L.lat} lon={L.lon} /><div className="scs-object-map"><MapView compact marker={{ lon: L.lon, lat: L.lat }} /></div></SectionCard></Section>
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={L} fields={["light_id", "satellite", "time_utc", "radiance_nw", "quality", "moon_illum_pct", "satlas_infra_m", "s1_passes_90d"]} /></SectionCard></Section>
    </article>
  );
}

export function EventPage({ id }: { id: string }) {
  const { adapter, tz } = useApp();
  const { data: e, loading } = useAsync(() => adapter.event(id), [adapter, id]);
  if (loading) return <Loading what="event" />;
  if (!e) return (
    <article className="scs-page" data-page="event">
      <ObjectHeader title={id} icon="timeline-events" back="leads" subtitle="Observation event" />
      <NotBuilt what="Events" note="The open build's aisstream events (data/events_open.gpkg) are pending. Until then no Event object exists in this build." />
    </article>
  );
  return (
    <article className="scs-page" data-page="event" data-event-id={e.event_id}>
      <ObjectHeader title={e.event_id} icon="timeline-events" back="leads" subtitle={`${e.code} ${e.event_type}, ${fmtTime(e.start_utc, tz)}${e.end_utc ? " to " + fmtTime(e.end_utc, tz) : ""}`} tags={<span className="scs-tagrow"><Tag minimal>{e.source}</Tag>{e.research_only && <Tag minimal intent="warning">research</Tag>}</span>} />
      <CaveatCallout caveat={e.caveat} />
      <Section title="Event" compact>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Duration" rec={e} field="duration_h" value={e.duration_h === null ? "instant" : `${e.duration_h.toFixed(1)} h`} />
            <Field label="Rule as applied" rec={e} field="rule_text" value={e.rule_text} />
            <Field label="Parameters" rec={e} field="params" value={JSON.stringify(e.params)} />
            <Field label="Grade" rec={e} field="grade" value={e.grade} />
            <Field label="Vessels" rec={e} field="vessel_keys" value={e.vessel_keys.map((k) => <span key={k}><ObjectLink type="vessel" id={k} /> </span>)} />
            <Field label="Contacts" rec={e} field="det_ids" value={e.det_ids.length ? e.det_ids.map((k) => <span key={k}><ObjectLink type="contact" id={k} /> </span>) : "none"} />
          </div>
          <CoordBlock lat={e.lat} lon={e.lon} />
        </SectionCard>
      </Section>
    </article>
  );
}

export function PassPage({ id }: { id: string }) {
  const { adapter, tz } = useApp();
  const { data: p, loading } = useAsync(() => adapter.pass(id), [adapter, id]);
  const { data: contacts } = useAsync(() => (p?.processed ? adapter.contacts({ pass_id: p.pass_id, limit: 1000 }) : Promise.resolve({ items: [] as Contact[], total: 0 })), [adapter, p?.pass_id]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const x of contacts?.items || []) c[x.ais_status] = (c[x.ais_status] || 0) + 1;
    return c;
  }, [contacts]);
  if (loading) return <Loading what="pass" />;
  if (!p) return <Missing what="Pass" id={id} />;
  const n = p.n_contacts || counts;
  const total = Object.values(n).reduce((a, b) => a + b, 0);
  const mostlyNoCov = total > 0 && (n.no_coverage || 0) / total > 0.5;
  return (
    <article className="scs-page" data-page="pass" data-pass-id={p.pass_id}>
      <ObjectHeader title={p.pass_id} icon="satellite" back="leads" subtitle={`${sentinelName(p.mission)} pass, relative orbit ${p.relative_orbit ?? "?"}, ${p.pass_dir?.toLowerCase() ?? ""}, ${fmtTime(p.start_utc, tz)} to ${fmtTime(p.stop_utc, tz, false)}`}
        tags={<span className="scs-tagrow"><Tag minimal intent={p.status === "upcoming" ? "primary" : undefined}>{p.status}</Tag>{p.sources.map((s) => <Tag key={s} minimal>{s === "esa_plan" ? "ESA acquisition plan" : s === "repeat_cycle" ? "12-day repeat prediction (not ESA's plan)" : s}</Tag>)}</span>} />
      {mostlyNoCov && p.processed && (
        <Callout intent="primary" compact icon="cell-tower" data-coverage-result="1">
          Coverage result: {p.ais_footprint_positions === 0 ? "No AIS was heard inside" : `${fmtNum(p.ais_footprint_positions)} AIS positions were heard inside`}
          {p.ais_near_footprint_mmsi === 0 ? " or within 0.3 degree of" : ""} the {p.scenes.length} scene{p.scenes.length === 1 ? "" : "s"}, so {fmtNum(n.no_coverage || 0)} of the {fmtNum(total)} contacts could not be checked against AIS.
          {p.ais_aoi_positions ? ` The feed was up: ${fmtNum(p.ais_aoi_positions)} positions were recorded elsewhere in the AOI during the scene windows.` : ""} {DARK_CAVEAT_SHORT}
        </Callout>
      )}
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
          {p.note && <p className="scs-muted" style={{ fontSize: 12 }}>{p.note}</p>}
          {p.fixture_note && <p className="scs-muted" style={{ fontSize: 12 }}>{p.fixture_note}</p>}
        </SectionCard>
      </Section>
      {p.processed && (
        <Section title="AIS recorded during the window" compact collapsible>
          <SectionCard>
            <div className="scs-fields">
              <Field label="1. Inside the footprint" rec={p} field="ais_footprint_positions" value={`${fmtNum(p.ais_footprint_positions)} positions, ${fmtNum(p.ais_footprint_mmsi)} MMSI`} />
              <Field label="2. Within 0.3 degree of it (what the matcher sees)" rec={p} field="ais_near_footprint_mmsi" value={`${fmtNum(p.ais_near_footprint_mmsi)} MMSI`} />
              <Field label="3. Anywhere in the AOI (only shows that the feed was up)" rec={p} field="ais_aoi_positions" value={`${fmtNum(p.ais_aoi_positions)} positions, ${fmtNum(p.ais_aoi_mmsi)} MMSI`} />
            </div>
          </SectionCard>
        </Section>
      )}
      {p.processed && (
        <Section title="Contacts by AIS status" compact collapsible>
          <SectionCard>
            <div className="scs-fields">
              {(["matched", "unmatched", "no_coverage", "not_checked"] as const).map((s) => <Field key={s} label={AIS_STATUS_LABEL[s]} rec={p} field="n_contacts" value={fmtNum(n[s] || 0)} />)}
            </div>
            {contacts && contacts.items.length > 0 && (
              <p style={{ fontSize: 12 }}>{fmtNum(contacts.total)} contacts of this pass in this build: {contacts.items.slice(0, 20).map((c) => <span key={c.det_id}><ObjectLink type="contact" id={c.det_id} /> </span>)}{contacts.total > 20 ? "..." : ""}</p>
            )}
            {contacts && contacts.items.length === 0 && <p className="scs-muted">Contacts of this pass are in the local app.</p>}
            <p className="scs-muted" style={{ fontSize: 12 }}>{DARK_CAVEAT_SHORT} no_coverage contacts never form L1 leads.</p>
          </SectionCard>
        </Section>
      )}
      {p.scene_counts && (
        <Section title="Scenes" compact collapsible>
          <SectionCard>
            <table className="bp6-html-table bp6-compact" style={{ width: "100%" }}>
              <thead><tr><th>scene</th><th>contacts</th><th>both channels</th><th>one channel</th><th>fixed</th><th>AIS positions in the AOI</th></tr></thead>
              <tbody>{p.scene_counts.map((s) => <tr key={String(s.product_id)}><td style={{ overflowWrap: "anywhere" }}>{String(s.product_id)}</td><td>{String(s.n_contacts)}</td><td>{String(s.n_high)}</td><td>{String(s.n_medium)}</td><td>{String(s.n_fixed)}</td><td>{String(s.ais_aoi_positions)}</td></tr>)}</tbody>
            </table>
          </SectionCard>
        </Section>
      )}
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={p} fields={["pass_id", "start_utc", "status", "sources", "ais_footprint_positions", "ais_near_footprint_mmsi", "ais_aoi_positions", "n_contacts"]} /></SectionCard></Section>
    </article>
  );
}

// Nightly sea fields come from several sources; the API gives one key for the block, so the page names each field's source.
const NIGHTLY_SOURCE: Record<string, string> = {
  sst_mean_c: "mur_sst", sst_sd_c: "mur_sst", sst_grad_mean: "mur_sst", front_share: "mur_sst", dist_front_km: "mur_sst",
  chl_log10_mean: "chl_dineof", chl_valid_share: "chl_dineof", ssh_m: "rtofs", ssh_anom_m: "rtofs", current_speed_ms: "rtofs", mld_m: "rtofs",
  wave_hs_m: "gfs_wave", wind_ms: "gfs_wind", moon_illum_pct: "app",
};

function numText(v: unknown, digits: number, unit: string): string | null {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(digits)}${unit ? " " + unit : ""}` : null;
}

export function CellPage({ id }: { id: string }) {
  const { adapter, layers, units, focusMap, phone } = useApp();
  const { data: cell, loading } = useAsync(() => adapter.cell(id), [adapter, id]);
  if (loading) return <Loading what="cell" />;
  if (!cell) {
    return (
      <article className="scs-page" data-page="cell">
        <ObjectHeader title={id} icon="grid" back="map" subtitle="0.25 degree model-grid cell" />
        <Callout compact icon="info-sign">{OCEAN_NOTE}</Callout>
        <NotBuilt what="Cell context in this page" note={adapter.kind === "embedded"
          ? "This page does not carry the cells part. The local app serves every cell (static sea fields, nightly fields, AIS reach, look probability)."
          : "No cell with this id in the local app."} />
      </article>
    );
  }
  const c = cell;
  const nightly = (c.nightly || null) as Record<string, unknown> | null;
  const nightlyRec = nightly ? { src: c.prov?.nightly || c.src, prov: Object.fromEntries(Object.entries(NIGHTLY_SOURCE).filter(([, v]) => v)), caveat: c.caveat, research_only: c.research_only } : null;
  const ship = Object.keys(c).filter((k) => /^ship_(presence_share|density)_/.test(k));
  const km = (v: unknown) => (typeof v === "number" ? fmtMetres(v * 1000, units) : null);
  const share = (v: unknown) => (typeof v === "number" ? pct(v) : null);
  return (
    <article className="scs-page" data-page="cell" data-cell-id={c.cell_id}>
      <ObjectHeader title={c.cell_id} icon="grid" back="map" subtitle={`0.25 degree model-grid cell, centre ${fmtDd(c.lat, c.lon)}, ${c.region_box || "other"} (reporting box, not a boundary)`} />
      <Callout compact icon="info-sign">{OCEAN_NOTE}</Callout>
      <ButtonGroup style={{ margin: "8px 0" }}><Button small icon="locate" text="Centre map" onClick={() => { focusMap(c.lon, c.lat, 9); if (phone) window.location.hash = "#/map"; }} /></ButtonGroup>
      <Section title="Static sea fields" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="Sea share of the cell" rec={c} field="sea_share" value={share(c.sea_share)} />
            <Field label="Sea area" rec={c} field="sea_area_km2" value={numText(c.sea_area_km2, 0, "km2")} />
            <Field label="Depth mean (min to max)" rec={c} field="depth_mean_m" value={typeof c.depth_mean_m === "number" ? `${fmtNum(c.depth_mean_m)} m (${fmtNum(c.depth_min_m as number)} to ${fmtNum(c.depth_max_m as number)} m)` : null} />
            <Field label="Share shallower than 50 m / 200 m" rec={c} field="share_shallower_200m" value={typeof c.share_shallower_200m === "number" ? `${share(c.share_shallower_50m)} / ${share(c.share_shallower_200m)}` : null} />
            <Field label="Mean slope" rec={c} field="slope_mean_m_per_km" value={numText(c.slope_mean_m_per_km, 1, "m/km")} />
            <Field label="Distance to coast (mean, minimum)" rec={c} field="dist_coast_km" value={typeof c.dist_coast_km === "number" ? `${km(c.dist_coast_km)}, ${km(c.dist_coast_min_km)}` : null} />
            <Field label="Distance to the nearest major port (mean, minimum)" rec={c} field="dist_port_km" value={typeof c.dist_port_km === "number" ? `${km(c.dist_port_km)}, ${km(c.dist_port_min_km)}` : null} />
            {ship.map((k) => <Field key={k} label={k.replace(/^ship_(presence_share|density)_/, "Shipping, ") } rec={c} field={k} value={typeof c[k] === "number" ? (k.includes("share") ? share(c[k]) : fmtNum(c[k] as number)) : null} />)}
          </div>
          {typeof c.shipping_note === "string" && <p className="scs-muted" style={{ fontSize: 12 }}>Shipping: {c.shipping_note}.</p>}
        </SectionCard>
      </Section>
      <Section title={`Nightly sea fields${nightly?.night ? `, night ${String(nightly.night)}` : ""}`} compact collapsible>
        <SectionCard>
          {nightly && nightlyRec ? (
            <div className="scs-fields">
              <Field label="SST mean" rec={nightlyRec} field="sst_mean_c" value={numText(nightly.sst_mean_c, 2, "degC")} />
              <Field label="Distance to the nearest front" rec={nightlyRec} field="dist_front_km" value={km(nightly.dist_front_km)} />
              <Field label="Chlorophyll (log10 mg m-3)" rec={nightlyRec} field="chl_log10_mean" value={numText(nightly.chl_log10_mean, 2, "")} />
              <Field label="Current speed" rec={nightlyRec} field="current_speed_ms" value={numText(nightly.current_speed_ms, 2, "m/s")} />
              <Field label="Mixed layer depth" rec={nightlyRec} field="mld_m" value={numText(nightly.mld_m, 1, "m")} />
              <Field label="Significant wave height" rec={nightlyRec} field="wave_hs_m" value={numText(nightly.wave_hs_m, 2, "m")} />
              <Field label="Wind" rec={nightlyRec} field="wind_ms" value={numText(nightly.wind_ms, 1, "m/s")} />
              <Field label="Moon illumination" rec={nightlyRec} field="moon_illum_pct" value={numText(nightly.moon_illum_pct, 0, "%")} />
            </div>
          ) : <p className="scs-muted">No nightly fields for this cell.</p>}
          {nightly && <p className="scs-muted" style={{ fontSize: 12 }}>Valid times: SST {String(nightly.sst_date ?? "unknown")}, chlorophyll {String(nightly.chl_date ?? "unknown")}, currents {String(nightly.rtofs_valid_utc ?? "unknown")}, waves {String(nightly.wave_valid_utc ?? "unknown")}, wind {String(nightly.wind_valid_utc ?? "unknown")}.</p>}
        </SectionCard>
      </Section>
      <Section title="AIS reach and radar looks" compact collapsible>
        <SectionCard>
          <div className="scs-fields">
            <Field label="AIS reach (share of recorded hours with any AIS)" rec={c} field="ais_reach_share" value={share(c.ais_reach_share)} />
            <Field label="AIS vessels heard in the cell" rec={c} field="ais_reach_mmsi" value={typeof c.ais_reach_mmsi === "number" ? fmtNum(c.ais_reach_mmsi) : null} />
            <Field label="Sentinel-1 look probability, 1 / 7 / 30 days" rec={c} field="look_prob_7d" value={typeof c.look_prob_7d === "number" ? `${share(c.look_prob_1d)} / ${share(c.look_prob_7d)} / ${share(c.look_prob_30d)}` : null} />
            <Field label="Sentinel-1 passes in 90 days" rec={c} field="passes_90d" value={typeof c.passes_90d === "number" ? fmtNum(c.passes_90d) : null} />
            <Field label="Expected activity" rec={c} field="expected_activity" value={c.expected_activity ? JSON.stringify(c.expected_activity) : "absent (the model output is not in this build)"} />
          </div>
        </SectionCard>
      </Section>
      {layers.eez_boundaries && c.eez && (
        <Section title="As published by Marine Regions" compact collapsible>
          <SectionCard>
            <p className="scs-muted" style={{ fontSize: 12 }}>{String(c.eez.statement || EEZ_STATEMENT)}</p>
            <div className="scs-fields" data-eez-attrs="1">
              {Object.entries(c.eez).filter(([k]) => k.startsWith("marineregions_")).map(([k, v]) => <Field key={k} label={k.replace("marineregions_", "")} rec={c} field="eez" value={v === null || v === undefined ? null : typeof v === "number" && k.endsWith("share") ? pct(v) : String(v)} />)}
            </div>
          </SectionCard>
        </Section>
      )}
      {!layers.eez_boundaries && <p className="scs-muted" style={{ fontSize: 12 }}>Marine Regions attributes of this cell are shown only while the EEZ layer is on (Layers tab).</p>}
      <Section title="Provenance" compact collapsible><SectionCard><ProvenanceTable rec={c} fields={["depth_mean_m", "dist_coast_km", "dist_port_km", ...ship.slice(0, 1), "ais_reach_share", "look_prob_7d", "passes_90d"]} /></SectionCard></Section>
    </article>
  );
}

export { EventRecord };
