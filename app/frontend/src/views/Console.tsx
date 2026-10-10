// Desktop console (spec section 3.1): filter bar, left rail (Leads, Layers, Find, Info), map, inspector, timeline.
// On phone the bottom tab bar picks one of Queue, Map, Search, Info (section 12).
import React, { useEffect, useMemo, useState } from "react";
import { Button, Callout, InputGroup, Switch, Tab, Tabs, Tag } from "@blueprintjs/core";
import { useApp, LAYER_DEFAULTS, type LayerId } from "../app/state";
import { MapView } from "../map/MapView";
import { LAYER_INFO } from "../map/layers";
import { DEFAULT_FILTERS, LeadsQueue, QueueFilterRail, RUN_LABEL, useLeads, useNewestLivePass, type QueueFilters } from "./LeadsQueue";
import { ExportDecisions, LeadCard } from "./LeadCard";
import { Timeline } from "../timeline/Timeline";
import { ProvChip } from "./Provenance";
import { EEZ_DISCLAIMER, EEZ_DISCLAIMER_URL, EEZ_STATEMENT, LOW_QUALITY_LABEL, OCEAN_NOTE } from "../app/text";
import { ambiguousCandidates, identityLabel, isAmbiguous, lowQualityPairing, shipTypeText } from "../app/identity";
import { OVERLAY_GROUPS, isPresence, rasterGroup, rasterLabel } from "../map/overlays";
import { SHIPPING_PRESENCE_NOTE } from "../adapters/context";
import { fmtDd, fmtMgrs, fmtTime, fmtNum } from "../app/format";
import { parseCoordinates } from "../adapters/search";
import type { Contact, Lead, Light, Vessel } from "../adapters/types";
import { StatusChip, ObjectLink } from "./common";
import { navigate } from "../app/router";
import { useAsync } from "../app/state";

export function LayersTab() {
  const { layers, setLayer, meta } = useApp();
  const [eezSeen, setEezSeen] = useState(false);
  const eezSource = meta.sources.find((s) => s.key === "marineregions_v12");
  return (
    <div className="scs-rail-section" data-rail="layers">
      {LAYER_INFO.map((li) => (
        <div className="scs-layer-row" key={li.id} data-layer={li.id} data-on={layers[li.id] ? "1" : "0"}>
          <Switch checked={layers[li.id]} onChange={(e) => { setLayer(li.id, e.currentTarget.checked); if (li.id === "eez_boundaries" && e.currentTarget.checked) setEezSeen(true); }}
            labelElement={<span><span>{li.name}</span>{LAYER_DEFAULTS[li.id] ? null : <span className="meta">off by default</span>}<span className="meta">{li.legend}</span>
              <span className="meta">licence: {meta.sources.find((s) => s.key === li.source)?.licence || "see provenance"}</span></span>}
            aria-label={`Layer ${li.name}`} />
          <ProvChip sourceKey={li.source} field={li.id} />
        </div>
      ))}
      {(layers.eez_boundaries || eezSeen) && (
        <Callout compact icon="info-sign" style={{ marginTop: 8, fontSize: 12 }} data-eez-statement="1">
          {EEZ_STATEMENT} <em>{EEZ_DISCLAIMER}</em> (<a href={EEZ_DISCLAIMER_URL} target="_blank" rel="noreferrer">source</a>). Licence: {eezSource?.licence || "CC BY 4.0"}. Drawn as thin neutral lines, no fill, no labels. The GeoPackage holds the published geometry.
        </Callout>
      )}
      <ContextLayers />
    </div>
  );
}

/** Context raster overlays (spec section 6.2): grouped, every one off by default, with unit, valid time, licence and source. */
function ContextLayers() {
  const { adapter, overlays, setOverlay, meta } = useApp();
  const { data: rasters, loading } = useAsync(() => adapter.rasters(), [adapter]);
  const anyOn = Object.values(overlays).some(Boolean);
  return (
    <div className="scs-context-layers" data-context-layers="1">
      <h6 className="scs-overlay-group" style={{ fontSize: 13 }}>Context layers</h6>
      <p className={anyOn ? "scs-ocean-caveat" : "scs-muted"} style={{ fontSize: 12, margin: "2px 0 6px" }}>{OCEAN_NOTE} All off by default; switching one on draws it under the radar contacts.</p>
      {loading && <p className="scs-muted" style={{ fontSize: 12 }}>Loading the raster list.</p>}
      {!loading && (!rasters || rasters.length === 0) && <p className="scs-muted" style={{ fontSize: 12 }} data-no-rasters="1">No context rasters in this build.</p>}
      {OVERLAY_GROUPS.map((g) => {
        const rows = (rasters || []).filter((r) => rasterGroup(r) === g);
        if (!rows.length) return null;
        return (
          <div key={g} data-overlay-group={g}>
            <div className="scs-overlay-group">{g}</div>
            {g === "Shipping presence" && <p className="scs-muted" style={{ fontSize: 11, margin: "0 0 4px" }}>Drawn as a mask: {SHIPPING_PRESENCE_NOTE}. Never a ranking or a lane.</p>}
            {rows.map((r) => (
              <div className="scs-layer-row" key={r.name} data-overlay-switch={r.name} data-on={overlays[r.name] ? "1" : "0"}>
                <Switch checked={!!overlays[r.name]} onChange={(e) => setOverlay(r.name, e.currentTarget.checked)} aria-label={`Context layer ${rasterLabel(r)}`}
                  labelElement={<span><span>{rasterLabel(r)}</span><span className="meta">off by default</span>
                    <span className="meta">{isPresence(r) ? "presence (1 = published value above 0)" : `unit: ${r.unit || "unknown"}`}; valid: {r.valid_period || "static layer"}</span>
                    <span className="meta">licence: {r.licence || meta.sources.find((x) => x.key === r.src)?.licence || "see provenance"}</span>
                    {r.research_only && <span className="meta">{r.label || meta.research_label || "research build only"}</span>}</span>} />
                <ProvChip sourceKey={r.src} field={r.name} />
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function FindTab() {
  const { adapter, focusMap, setSelection, tz } = useApp();
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const { data: recent } = useAsync(() => adapter.contacts({ limit: 15, sort: "-acq_utc" }), [adapter]);
  const go = () => {
    const c = parseCoordinates(q);
    if (!c || c.lon === undefined || c.lat === undefined) {
      setMsg("Not read as coordinates. Use DD (10.25, 107.5), DMS, DDM or MGRS.");
      return;
    }
    setMsg(c.interpretation || null);
    focusMap(c.lon, c.lat, 9);
    setSelection({ type: "point", id: c.value, lon: c.lon, lat: c.lat });
  };
  return (
    <div className="scs-rail-section" data-rail="find">
      <h6>Go to coordinates</h6>
      <InputGroup value={q} onChange={(e) => setQ(e.currentTarget.value)} placeholder="10.25, 107.5 or 48PVQ8899650631" onKeyDown={(e) => { if (e.key === "Enter") go(); }} rightElement={<Button minimal icon="arrow-right" aria-label="Go to point" onClick={go} />} aria-label="Coordinates" />
      {msg && <p className="scs-muted" style={{ fontSize: 12 }}>{msg}</p>}
      <h6>Latest contacts in this build</h6>
      <ul className="scs-linklist">
        {(recent?.items || []).map((c) => <li key={c.det_id}><ObjectLink type="contact" id={c.det_id} /> <span className="scs-muted">{fmtTime(c.acq_utc, tz, false)}</span> <StatusChip status={c.ais_status} short /></li>)}
      </ul>
      <p className="scs-muted" style={{ fontSize: 12 }}>Every map object is also reachable from this list and the queue, so nothing needs a pointer.</p>
    </div>
  );
}

export function InfoTab() {
  const { meta, adapter, decisionsVersion } = useApp();
  return (
    <div className="scs-rail-section scs-info" data-rail="info">
      <h6>Build</h6>
      <dl>
        <dt>build</dt><dd>{meta.build} ({meta.build_label})</dd>
        <dt>contract</dt><dd>{meta.contract_version}</dd>
        <dt>generated</dt><dd>{meta.generated_utc}</dd>
        <dt>git hash</dt><dd>{meta.git_hash}</dd>
        <dt>data access</dt><dd>{adapter.kind === "embedded" ? "embedded bundle (static snapshot)" : "local API, polls for new files"}</dd>
        <dt>priority model</dt><dd>{meta.priority_model_id || "none"}</dd>
      </dl>
      {meta.fixture && <Callout compact intent={meta.fixture.synthetic ? "danger" : "warning"} style={{ marginTop: 8 }} data-fixture="1"><strong>FIXTURE.</strong> {meta.fixture.note}</Callout>}
      <h6>Counts in this build</h6>
      <dl>{Object.entries(meta.counts || {}).map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{fmtNum(v)}</dd></React.Fragment>)}</dl>
      <h6>Files</h6>
      <dl>{(meta.files || []).map((f) => <React.Fragment key={f.path + (f.layer || "")}><dt>{f.path}{f.layer ? ` / ${f.layer}` : ""}</dt><dd>{f.status}{f.rows !== null && f.rows !== undefined ? `, ${fmtNum(f.rows)} rows` : ""}{f.in_bundle !== undefined && f.in_bundle !== null ? `, ${fmtNum(f.in_bundle)} in this page` : ""}</dd></React.Fragment>)}</dl>
      {meta.dropped && meta.dropped.length > 0 && (<><h6>Left out of this page</h6><ul style={{ paddingLeft: 18, fontSize: 12 }}>{meta.dropped.map((d, i) => <li key={i}>{d.part}: {d.reason}</li>)}</ul></>)}
      {meta.ais_recording && (<><h6>AIS recording</h6><dl>
        <dt>period</dt><dd>{meta.ais_recording.period_start_utc} to {meta.ais_recording.period_end_utc}</dd>
        <dt>positions</dt><dd>{fmtNum(meta.ais_recording.positions ?? null)} from {fmtNum(meta.ais_recording.mmsi_count ?? null)} MMSI</dd>
        <dt>gaps</dt><dd>{(meta.ais_recording.gaps || []).length} recorder gaps over 10 min</dd>
      </dl><p className="scs-muted" style={{ fontSize: 12 }}>{meta.ais_recording.note}</p></>)}
      <h6>Decisions</h6>
      <ExportDecisions highlight={decisionsVersion > 0} />
      <p style={{ marginTop: 8 }}><a href="#/about">About: method, sources, licences, limits</a></p>
    </div>
  );
}

function Inspector({ selectedLead, onLeadUpdate, onClose }: { selectedLead: Lead | null; onLeadUpdate: (l: Lead) => void; onClose?: () => void }) {
  const { selection, adapter, tz } = useApp();
  const { data: contact } = useAsync(() => (selection?.type === "contact" ? adapter.contact(selection.id) : Promise.resolve(null)), [adapter, selection?.type, selection?.id]);
  const { data: vessel } = useAsync(() => (selection?.type === "vessel" ? adapter.vessel(selection.id) : Promise.resolve(null)), [adapter, selection?.type, selection?.id]);
  const { data: light } = useAsync(() => (selection?.type === "light" ? adapter.light(selection.id) : Promise.resolve(null)), [adapter, selection?.type, selection?.id]);
  const { data: lead } = useAsync(() => (selection?.type === "lead" ? adapter.lead(selection.id) : Promise.resolve(selectedLead)), [adapter, selection?.type, selection?.id, selectedLead]);
  const title = selection ? `${selection.type}: ${selection.id}` : selectedLead ? `lead: ${selectedLead.lead_id}` : "Nothing selected";
  return (
    <aside className="scs-inspector open" aria-label="Inspector" data-inspector="1">
      <div className="scs-inspector-head">
        <h5 className="bp6-heading" aria-live="polite">{title}</h5>
        {onClose && <Button minimal small icon="cross" aria-label="Close the inspector" onClick={onClose} />}
      </div>
      {!selection && !selectedLead && <p className="scs-muted">Select a lead in the queue or an object on the map. J and K move through the queue; Enter opens the page.</p>}
      {selection?.type === "point" && selection.lon !== undefined && selection.lat !== undefined && (
        <div><p>{fmtDd(selection.lat, selection.lon)}</p><p className="scs-muted">{fmtMgrs(selection.lat, selection.lon)}</p><p className="scs-muted" style={{ fontSize: 12 }}>The map is centred on the point; the objects around it are on the map.</p></div>
      )}
      {(selection?.type === "lead" || (!selection && selectedLead)) && lead && <LeadCard lead={lead} onUpdate={onLeadUpdate} />}
      {selection?.type === "contact" && contact && <ContactSummary c={contact} />}
      {selection?.type === "vessel" && vessel && <VesselSummary v={vessel} />}
      {selection?.type === "light" && light && <LightSummary l={light} />}
      {selection && selection.type !== "point" && !contact && !vessel && !light && !lead && <p className="scs-muted">Loading or not in this build.</p>}
      {selection && selection.type !== "point" && <p style={{ marginTop: 8 }}><Button small text="Open full page" icon="document-open" onClick={() => navigate(selection.type === "point" ? "map" : selection.type, selection.id)} /></p>}
      {selection && <p className="scs-muted" style={{ fontSize: 11 }}>Selected at {fmtTime(new Date().toISOString(), tz, false)}</p>}
    </aside>
  );
}

function ContactSummary({ c }: { c: Contact }) {
  const { tz } = useApp();
  return (
    <div className="scs-fields">
      <div className="scs-field"><span className="k">AIS status</span><span className="v"><StatusChip status={c.ais_status} /></span></div>
      <div className="scs-field"><span className="k">time</span><span className="v">{fmtTime(c.acq_utc, tz)}</span></div>
      <div className="scs-field"><span className="k">class, length</span><span className="v">{c.confidence}, {c.length_est_m === null ? "?" : Math.round(c.length_est_m)} m</span></div>
      <div className="scs-field"><span className="k">CNN score</span><span className="v judgment">{c.cnn_score === null ? "not scored" : c.cnn_score.toFixed(3)}</span></div>
      {c.ais_status === "matched" && <div className="scs-field"><span className="k">identity</span><span className="v">{c.vessel_name || "?"} (MMSI {c.mmsi}), <span className="judgment">{c.match_quality || "?"} quality</span>
        {lowQualityPairing(c) ? <Tag minimal intent="warning" style={{ marginLeft: 4 }} data-low-quality="1">{LOW_QUALITY_LABEL}</Tag> : null}{identityLabel(c) ? <span className="scs-muted"> ({identityLabel(c)})</span> : null}</span></div>}
      {c.ais_status === "unmatched" && isAmbiguous(c) && <div className="scs-field"><span className="k">ambiguous</span><span className="v">candidates {ambiguousCandidates(c).join(", ") || "in the record"}; never a lead</span></div>}
      {c.ais_status === "unmatched" && <div className="scs-field"><span className="k">nearest AIS</span><span className="v">{c.nearest_vessel_name || c.nearest_ais_mmsi || "none"}, {c.nearest_ais_dist_m === null ? "?" : Math.round(c.nearest_ais_dist_m / 100) / 10 + " km"}</span></div>}
      {c.lead_ids.length > 0 && <div className="scs-field"><span className="k">cited by leads</span><span className="v">{c.lead_ids.map((l) => <ObjectLink key={l} type="lead" id={l} />)}</span></div>}
      {c.synthetic && <Tag intent="danger" minimal>SYNTHETIC fixture status</Tag>}
    </div>
  );
}

function VesselSummary({ v }: { v: Vessel }) {
  const { tz } = useApp();
  return (
    <div className="scs-fields">
      <div className="scs-field"><span className="k">name</span><span className="v">{v.name || "unknown"}</span></div>
      <div className="scs-field"><span className="k">MMSI, class</span><span className="v">{v.mmsi}, {v.ais_class || "?"}</span></div>
      <div className="scs-field"><span className="k">type, length</span><span className="v">{shipTypeText(v.ship_type) || "?"}, {v.length_m === null ? "?" : v.length_m + " m"}</span></div>
      <div className="scs-field"><span className="k">flag (as claimed)</span><span className="v">{v.flag || `MID ${v.mid}`}</span></div>
      <div className="scs-field"><span className="k">last heard</span><span className="v">{fmtTime(v.last_seen_utc, tz)}</span></div>
      <div className="scs-field"><span className="k">contacts matched</span><span className="v">{v.contacts_matched.length}</span></div>
    </div>
  );
}

function LightSummary({ l }: { l: Light }) {
  const { tz } = useApp();
  return (
    <div className="scs-fields">
      <div className="scs-field"><span className="k">satellite, time</span><span className="v">{l.satellite}, {fmtTime(l.time_utc, tz)}</span></div>
      <div className="scs-field"><span className="k">radiance</span><span className="v">{l.radiance_nw} nW cm-2 sr-1, {l.quality}</span></div>
      <div className="scs-field"><span className="k">nights seen within 500 m</span><span className="v">{l.nights_seen_500m}</span></div>
      <p className="scs-muted" style={{ fontSize: 12 }}>A light is not a vessel identity.</p>
    </div>
  );
}

export function FilterBar({ filters, setFilters }: { filters: QueueFilters; setFilters: (f: QueueFilters) => void }) {
  const { layers, setLayer, window: win, setWindow, overlays, setOverlay, clearOverlays } = useApp();
  const pills: { k: string; label: string; clear: () => void }[] = [];
  const stateDefault = filters.states.length === 2 && filters.states.includes("new") && filters.states.includes("reviewing");
  if (!stateDefault) pills.push({ k: "state", label: `state: ${filters.states.join(", ") || "none"}`, clear: () => setFilters({ ...filters, states: DEFAULT_FILTERS.states }) });
  if (filters.lead_type) pills.push({ k: "type", label: `type: ${filters.lead_type}`, clear: () => setFilters({ ...filters, lead_type: "" }) });
  if (filters.band) pills.push({ k: "band", label: `priority: ${filters.band}`, clear: () => setFilters({ ...filters, band: "" }) });
  if (filters.region_box) pills.push({ k: "box", label: `box: ${filters.region_box}`, clear: () => setFilters({ ...filters, region_box: "" }) });
  if (filters.pass_id) pills.push({ k: "pass", label: `pass: ${filters.pass_id}`, clear: () => setFilters({ ...filters, pass_id: "" }) });
  if (filters.run) pills.push({ k: "run", label: `run: ${RUN_LABEL[filters.run] || filters.run}`, clear: () => setFilters({ ...filters, run: "" }) });
  if (filters.ais_status) pills.push({ k: "ais", label: `AIS: ${filters.ais_status}`, clear: () => setFilters({ ...filters, ais_status: "" }) });
  if (filters.cnn_accepted) pills.push({ k: "cnn", label: "CNN accepted", clear: () => setFilters({ ...filters, cnn_accepted: false }) });
  if (win) pills.push({ k: "win", label: "time window: last 12 days", clear: () => setWindow(null) });
  for (const id of Object.keys(layers) as LayerId[]) if (layers[id] !== LAYER_DEFAULTS[id]) pills.push({ k: "layer:" + id, label: `${layers[id] ? "layer on" : "layer off"}: ${LAYER_INFO.find((l) => l.id === id)?.name || id}`, clear: () => setLayer(id, LAYER_DEFAULTS[id]) });
  for (const name of Object.keys(overlays)) if (overlays[name]) pills.push({ k: "overlay:" + name, label: `context layer on: ${rasterLabel({ name })}`, clear: () => setOverlay(name, false) });
  return (
    <div className="scs-filterbar" data-filterbar="1">
      <span className="scs-muted">Filters:</span>
      {pills.length === 0 && <span className="scs-muted">queue shows new and reviewing leads, sorted by priority; layer defaults</span>}
      {pills.map((p) => <Tag key={p.k} minimal onRemove={p.clear}>{p.label}</Tag>)}
      <span className="scs-spacer" />
      {pills.length > 0 && <Button small minimal text="Clear filters" onClick={() => { setFilters(DEFAULT_FILTERS); setWindow(null); clearOverlays(); for (const id of Object.keys(layers) as LayerId[]) setLayer(id, LAYER_DEFAULTS[id]); }} />}
    </div>
  );
}

function LeadRailList({ leads, selectedId, onSelect }: { leads: Lead[]; selectedId: string | null; onSelect: (l: Lead) => void }) {
  return (
    <ul className="scs-queue-rail-list" data-queue="raillist" aria-label="Leads in the queue">
      {leads.map((l) => (
        <li key={l.lead_id} className={l.lead_id === selectedId ? "selected" : ""} onClick={() => onSelect(l)} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") navigate("lead", l.lead_id); }} aria-pressed={l.lead_id === selectedId}>
          <div><strong>{l.priority}</strong> <span className="scs-muted">{l.priority_band}</span> {l.lead_type} <Tag minimal>{l.state}</Tag></div>
          <div className="scs-muted" style={{ overflowWrap: "anywhere" }}>{l.primary_id}{l.region_box ? `, ${l.region_box}` : ""}</div>
        </li>
      ))}
      {leads.length === 0 && <li className="scs-muted">No leads match the filters.</li>}
    </ul>
  );
}

export function Console({ focusMapTab }: { focusMapTab: boolean }) {
  const { phone, tablet, railTab, setRailTab, inspectorOpen, setInspectorOpen, selection, setSelection, bumpDecisions } = useApp();
  const [filters, setFilters] = useState<QueueFilters>(DEFAULT_FILTERS);
  const { leads, all, total, loading } = useLeads(filters);
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  const selectedLead = useMemo(() => leads.find((l) => l.lead_id === selectedLeadId) || all.find((l) => l.lead_id === selectedLeadId) || null, [leads, all, selectedLeadId]);
  const [phoneTab, setPhoneTab] = useState<"queue" | "map" | "search" | "info">(focusMapTab ? "map" : "queue");
  useEffect(() => { setPhoneTab(focusMapTab ? "map" : "queue"); }, [focusMapTab]);
  const [railOpen, setRailOpen] = useState(false);
  const onSelectLead = (l: Lead | null) => {
    setSelectedLeadId(l ? l.lead_id : null);
    if (l) setSelection({ type: "lead", id: l.lead_id, lon: l.lon, lat: l.lat });
    else setSelection(null);
    if (l && !phone) setInspectorOpen(true);
  };
  const newest = useNewestLivePass();
  const newestOn = !!newest && filters.pass_id === newest.pass_id;
  const queueHead = (
    <div className="scs-queue-head"><strong>Leads</strong><Tag minimal data-queue-count="1">{fmtNum(leads.length)} of {fmtNum(all.length)}</Tag>
      {total > all.length && <span className="scs-muted" title="The local app and the GeoPackage hold every lead">the {fmtNum(all.length)} highest priority of {fmtNum(total)} loaded</span>}
      {newest && <Button small minimal={!newestOn} active={newestOn} icon="satellite" data-quick-filter="newest-live-pass" aria-pressed={newestOn}
        text={phone ? "Newest live pass" : `Newest live pass (${fmtTime(newest.start_utc, "UTC", false)})`} title={newest.pass_id}
        onClick={() => setFilters({ ...filters, pass_id: newestOn ? "" : newest.pass_id })} />}
      {newest && <a href={`#/pass/${encodeURIComponent(newest.pass_id)}`} className="scs-muted" style={{ fontSize: 12 }}>pass page</a>}
      <span className="scs-muted">priority high first; new and reviewing by default</span><span className="scs-kbd">J K N Enter R E U X</span></div>
  );
  const queueTable = (
    <div className="scs-queue" data-view="queue">
      {queueHead}
      <LeadsQueue leads={leads} loading={loading} selectedId={selectedLeadId} onSelect={onSelectLead} />
    </div>
  );

  if (phone) {
    return (
      <>
        <FilterBar filters={filters} setFilters={setFilters} />
        <div className="scs-console" data-console="phone">
          {phoneTab === "queue" && (
            <div className="scs-rail">
              <details style={{ margin: "6px 0" }}><summary>Filters</summary><QueueFilterRail filters={filters} setFilters={setFilters} all={all} /></details>
              {queueTable}
              {selectedLead && <div className="scs-inspector"><LeadCard lead={selectedLead} onUpdate={() => bumpDecisions()} /></div>}
            </div>
          )}
          {phoneTab === "map" && <><MapView /><Timeline /></>}
          {phoneTab === "search" && <div className="scs-rail"><FindTab /><LayersTab /></div>}
          {phoneTab === "info" && <div className="scs-rail"><InfoTab /></div>}
        </div>
        <nav className="scs-tabbar" role="tablist" aria-label="Views">
          {(["queue", "map", "search", "info"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={phoneTab === t} data-tab={t} onClick={() => setPhoneTab(t)}>{t === "queue" ? "Queue" : t === "map" ? "Map" : t === "search" ? "Search" : "Info"}</button>
          ))}
        </nav>
      </>
    );
  }

  // Desktop and tablet: at #/leads the centre holds the queue table above a map strip and the Leads tab holds the
  // filters; at #/map the centre is the map and the Leads tab lists the queue compactly. The timeline sits under the
  // console in both (spec section 3.1). The inspector shows when something is selected and it is not toggled off.
  const leadsTab = focusMapTab
    ? <div className="scs-rail-section">{queueHead}<LeadRailList leads={leads} selectedId={selectedLeadId} onSelect={onSelectLead} /></div>
    : <QueueFilterRail filters={filters} setFilters={setFilters} all={all} />;
  const inspectorLead = selection?.type === "lead" || !selection ? selectedLead : null;
  const showInspector = inspectorOpen && (!!selection || !!inspectorLead);
  const openRailTab = (id: string) => {
    setRailTab(id);
    setRailOpen((o) => (railTab === id ? !o : true));
  };
  return (
    <>
      <FilterBar filters={filters} setFilters={setFilters} />
      <div className="scs-console-wrap">
        <div className={"scs-console" + (showInspector ? "" : " no-inspector")} data-console="desktop">
          {tablet && (
            <div className="scs-railstrip" role="toolbar" aria-label="Left rail" aria-orientation="vertical">
              <Button minimal icon="menu" aria-label={railOpen ? "Close the left rail" : "Open the left rail"} active={railOpen} onClick={() => setRailOpen((o) => !o)} />
              <Button minimal icon="inbox" aria-label={focusMapTab ? "Leads" : "Queue filters"} active={railOpen && railTab === "leads"} onClick={() => openRailTab("leads")} />
              <Button minimal icon="layers" aria-label="Layers" active={railOpen && railTab === "layers"} onClick={() => openRailTab("layers")} />
              <Button minimal icon="search-around" aria-label="Find" active={railOpen && railTab === "find"} onClick={() => openRailTab("find")} />
              <Button minimal icon="info-sign" aria-label="Info" active={railOpen && railTab === "info"} onClick={() => openRailTab("info")} />
              <Button minimal icon="panel-stats" aria-label="Toggle the inspector" active={showInspector} onClick={() => setInspectorOpen(!inspectorOpen)} />
            </div>
          )}
          <nav className={"scs-rail" + (railOpen ? " open" : "")} aria-label="Left rail">
            <Tabs id="rail" selectedTabId={railTab} onChange={(id) => setRailTab(String(id))} animate={false}>
              <Tab id="leads" title={focusMapTab ? "Leads" : "Filters"} panel={leadsTab} tagContent={leads.length} />
              <Tab id="layers" title="Layers" panel={<LayersTab />} />
              <Tab id="find" title="Find" panel={<FindTab />} />
              <Tab id="info" title="Info" panel={<InfoTab />} />
            </Tabs>
          </nav>
          <div className="scs-centre">{focusMapTab ? <MapView /> : <>{queueTable}<div className="scs-minimap"><MapView /></div></>}</div>
          {showInspector && <Inspector selectedLead={inspectorLead} onLeadUpdate={() => bumpDecisions()} onClose={tablet ? () => setInspectorOpen(false) : undefined} />}
        </div>
      </div>
      <Timeline />
    </>
  );
}
