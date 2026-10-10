// Leads queue (spec section 4.1): Table2 on desktop, card list on phone; default sort priority high first;
// default filter state new or reviewing; keys J/K/N/Enter/R/E/U/X.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Checkbox, HTMLSelect, NonIdealState, Tag } from "@blueprintjs/core";
import { Cell, Column, RenderMode, Regions, SelectionModes, Table2 } from "@blueprintjs/table";
import { useHotkeys, type HotkeyConfig } from "@blueprintjs/core";
import type { Lead, LeadState } from "../adapters/types";
import { useApp } from "../app/state";
import { LEAD_TYPE_NAME, STATE_LABEL } from "../app/text";
import { fmtNum, fmtTime, priorityBand } from "../app/format";
import { navigate } from "../app/router";
import { DecisionDialog, PriorityBar, setReviewing, type DecisionKind } from "./LeadCard";
import { StatusChip } from "./common";

export interface QueueFilters {
  states: LeadState[];
  lead_type: string;
  band: string;
  region_box: string;
  pass_id: string;
  ais_status: string;
  cnn_accepted: boolean;
}

export const DEFAULT_FILTERS: QueueFilters = { states: ["new", "reviewing"], lead_type: "", band: "", region_box: "", pass_id: "", ais_status: "", cnn_accepted: false };

export function useLeads(filters: QueueFilters): { leads: Lead[]; all: Lead[]; loading: boolean; reload: () => void } {
  const { adapter, decisionsVersion } = useApp();
  const [all, setAll] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    adapter.leads({ state: "all", limit: 10000, sort: "-priority" }).then(({ items }) => {
      if (!alive) return;
      setAll(items);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [adapter, decisionsVersion, tick]);
  const leads = useMemo(() => all.filter((L) =>
    (filters.states.length === 0 || filters.states.includes(L.state)) &&
    (!filters.lead_type || L.lead_type === filters.lead_type) &&
    (!filters.band || priorityBand(L.priority) === filters.band) &&
    (!filters.region_box || L.region_box === filters.region_box) &&
    (!filters.pass_id || L.pass_id === filters.pass_id) &&
    (!filters.ais_status || L.ais_status === filters.ais_status) &&
    (!filters.cnn_accepted || (L.cnn_score ?? 0) >= 0.631783),
  ).sort((a, b) => b.priority - a.priority), [all, filters]);
  return { leads, all, loading, reload: () => setTick((t) => t + 1) };
}

export function QueueFilterRail({ filters, setFilters, all }: { filters: QueueFilters; setFilters: (f: QueueFilters) => void; all: Lead[] }) {
  const types = [...new Set(all.map((l) => l.lead_type))].sort();
  const boxes = [...new Set(all.map((l) => l.region_box || "other"))].sort();
  const passes = [...new Set(all.map((l) => l.pass_id).filter(Boolean) as string[])].sort();
  const toggleState = (s: LeadState) => setFilters({ ...filters, states: filters.states.includes(s) ? filters.states.filter((x) => x !== s) : [...filters.states, s] });
  return (
    <div className="scs-rail-section" data-rail="filters">
      <h6>State</h6>
      {(["new", "reviewing", "closed_explained", "closed_unexplained", "closed_false_alarm"] as LeadState[]).map((s) => (
        <Checkbox key={s} checked={filters.states.includes(s)} label={`${STATE_LABEL[s]} (${all.filter((l) => l.state === s).length})`} onChange={() => toggleState(s)} />
      ))}
      <h6>Type</h6>
      <HTMLSelect fill value={filters.lead_type} onChange={(e) => setFilters({ ...filters, lead_type: e.currentTarget.value })} options={[{ value: "", label: "any type" }, ...types.map((t) => ({ value: t, label: `${t}: ${LEAD_TYPE_NAME[t] || t}` }))]} />
      <h6>Priority band</h6>
      <HTMLSelect fill value={filters.band} onChange={(e) => setFilters({ ...filters, band: e.currentTarget.value })} options={[{ value: "", label: "any band" }, { value: "high", label: "high (67 to 100)" }, { value: "medium", label: "medium (34 to 66)" }, { value: "low", label: "low (0 to 33)" }]} />
      <h6>Reporting box</h6>
      <HTMLSelect fill value={filters.region_box} onChange={(e) => setFilters({ ...filters, region_box: e.currentTarget.value })} options={[{ value: "", label: "any box" }, ...boxes.map((b) => ({ value: b, label: b }))]} />
      <h6>Pass</h6>
      <HTMLSelect fill value={filters.pass_id} onChange={(e) => setFilters({ ...filters, pass_id: e.currentTarget.value })} options={[{ value: "", label: "any pass" }, ...passes.map((p) => ({ value: p, label: p }))]} />
      <h6>AIS status</h6>
      <HTMLSelect fill value={filters.ais_status} onChange={(e) => setFilters({ ...filters, ais_status: e.currentTarget.value })} options={[{ value: "", label: "any" }, { value: "unmatched", label: "no AIS match" }, { value: "no_coverage", label: "no AIS coverage" }, { value: "matched", label: "matched" }, { value: "not_checked", label: "not checked" }]} />
      <Checkbox style={{ marginTop: 8 }} checked={filters.cnn_accepted} label="CNN accepted only (score at least 0.6318)" onChange={() => setFilters({ ...filters, cnn_accepted: !filters.cnn_accepted })} />
      <Button small minimal icon="filter-remove" text="Reset filters" onClick={() => setFilters(DEFAULT_FILTERS)} />
    </div>
  );
}

export function LeadsQueue({ leads, loading, selectedId, onSelect }: { leads: Lead[]; loading: boolean; selectedId: string | null; onSelect: (l: Lead | null) => void }) {
  const { tz, phone, adapter, bumpDecisions } = useApp();
  const [dlg, setDlg] = useState<{ lead: Lead; kind: DecisionKind } | null>(null);
  const selIndex = leads.findIndex((l) => l.lead_id === selectedId);
  const selected = selIndex >= 0 ? leads[selIndex] : null;

  const move = useCallback((d: number) => {
    if (!leads.length) return;
    const i = selIndex < 0 ? (d > 0 ? 0 : leads.length - 1) : Math.min(leads.length - 1, Math.max(0, selIndex + d));
    onSelect(leads[i]);
  }, [leads, selIndex, onSelect]);
  const nextNew = useCallback(() => {
    const from = selIndex < 0 ? 0 : selIndex + 1;
    const next = leads.slice(from).find((l) => l.state === "new") || leads.find((l) => l.state === "new");
    if (next) onSelect(next);
  }, [leads, selIndex, onSelect]);
  const openDecision = useCallback((kind: DecisionKind) => {
    if (!selected || selected.state.startsWith("closed")) return;
    if (kind === "reviewing") {
      // R needs no input (spec sections 4.1 and 9): record it directly.
      setReviewing(adapter, selected).then((l) => { if (l) bumpDecisions(); }, (e) => console.warn("decision not recorded", e));
      return;
    }
    setDlg({ lead: selected, kind });
  }, [selected, adapter, bumpDecisions]);

  const hotkeys = useMemo<HotkeyConfig[]>(() => [
    { combo: "j", global: true, group: "Queue", label: "Next row", onKeyDown: () => move(1) },
    { combo: "k", global: true, group: "Queue", label: "Previous row", onKeyDown: () => move(-1) },
    { combo: "n", global: true, group: "Queue", label: "Next new lead", onKeyDown: nextNew },
    { combo: "enter", global: true, group: "Queue", label: "Open the selected lead page", onKeyDown: () => { if (selected) navigate("lead", selected.lead_id); } },
    { combo: "r", global: true, group: "Lead", label: "Set reviewing", onKeyDown: () => openDecision("reviewing") },
    { combo: "e", global: true, group: "Lead", label: "Close as explained (reason picker)", onKeyDown: () => openDecision("closed_explained") },
    { combo: "u", global: true, group: "Lead", label: "Close as unexplained (note)", onKeyDown: () => openDecision("closed_unexplained") },
    { combo: "x", global: true, group: "Lead", label: "Close as false alarm (reason picker)", onKeyDown: () => openDecision("closed_false_alarm") },
  ], [move, nextNew, selected, openDecision]);
  useHotkeys(hotkeys);

  if (loading) return <NonIdealState icon="time" title="Loading leads" />;
  if (!leads.length) {
    return <NonIdealState icon="inbox" title="No leads match the filters" description="The queue shows Lead objects, never raw flags. No AIS match adds 0 points by itself." data-queue-empty="1" />;
  }

  if (phone) {
    return (
      <div className="scs-cards" data-queue="cards">
        {leads.map((l) => (
          <div key={l.lead_id} className={"scs-card" + (l.lead_id === selectedId ? " selected" : "")} onClick={() => onSelect(l)} role="button" tabIndex={0} aria-pressed={l.lead_id === selectedId}
            onKeyDown={(e) => { if (e.key === "Enter") navigate("lead", l.lead_id); }}>
            <div className="title">{l.lead_type}: {l.title}</div>
            <div className="row"><PriorityBar p={l.priority} /><Tag minimal>{STATE_LABEL[l.state]}</Tag></div>
            <div className="row"><span>{fmtTime(l.time_utc, tz, false)}</span><span>{l.region_box || "other"}</span></div>
            <div className="row">{l.ais_status && <StatusChip status={l.ais_status} short />}<span>CNN {l.cnn_score === null || l.cnn_score === undefined ? "none" : l.cnn_score.toFixed(2)}, {fmtNum(l.length_est_m)} m</span></div>
            <div className="row" style={{ marginTop: 6 }}><Button small text="Open lead" onClick={(e) => { e.stopPropagation(); navigate("lead", l.lead_id); }} /></div>
          </div>
        ))}
        {dlg && <DecisionDialog lead={dlg.lead} kind={dlg.kind} onClose={() => setDlg(null)} onDone={() => undefined} />}
      </div>
    );
  }

  const cell = (fn: (l: Lead) => React.ReactNode, cls?: string) => (row: number) => <Cell className={cls}>{fn(leads[row])}</Cell>;
  return (
    <div className="scs-queue-table" data-queue="table">
      <Table2
        numRows={leads.length}
        enableRowHeader={false}
        selectionModes={SelectionModes.ROWS_ONLY}
        selectedRegions={selIndex >= 0 ? [Regions.row(selIndex)] : []}
        onSelection={(regions) => {
          const r = regions[0]?.rows?.[0];
          onSelect(r === undefined ? null : leads[r]);
        }}
        cellRendererDependencies={[leads, tz]}
        defaultRowHeight={30}
        enableGhostCells={false}
        renderMode={RenderMode.NONE}
        columnWidths={[96, 56, 92, 200, 124, 116, 124, 52, 84, 72, 124]}
        enableFocusedCell={false}
      >
        <Column name="Priority" cellRenderer={cell((l) => <PriorityBar p={l.priority} compact />)} />
        <Column name="Type" cellRenderer={cell((l) => <span title={`${l.lead_type}: ${LEAD_TYPE_NAME[l.lead_type] || l.lead_type}`} aria-label={`${l.lead_type}: ${LEAD_TYPE_NAME[l.lead_type] || l.lead_type}`}>{l.lead_type}</span>)} />
        <Column name="State" cellRenderer={cell((l) => STATE_LABEL[l.state])} />
        <Column name="Primary object" cellRenderer={cell((l) => <a href={`#/${l.primary_type}/${encodeURIComponent(l.primary_id)}`}>{l.primary_id}</a>)} />
        <Column name={`Time (${tz})`} cellRenderer={cell((l) => fmtTime(l.time_utc, tz, false).replace(` ${tz}`, ""))} />
        <Column name="Reporting box" cellRenderer={cell((l) => l.region_box || "other")} />
        <Column name="AIS status" cellRenderer={cell((l) => (l.ais_status ? <StatusChip status={l.ais_status} short /> : "n/a"))} />
        <Column name="CNN" cellRenderer={cell((l) => (l.cnn_score === null || l.cnn_score === undefined ? "none" : l.cnn_score.toFixed(2)), "judgment")} />
        <Column name="Length (m)" cellRenderer={cell((l) => fmtNum(l.length_est_m))} />
        <Column name="Evidence" cellRenderer={cell((l) => String(l.evidence.length))} />
        <Column name="Next look" cellRenderer={cell((l) => (l.next_look_utc ? fmtTime(l.next_look_utc, tz, false).replace(` ${tz}`, "") : "none planned"))} />
      </Table2>
      {dlg && <DecisionDialog lead={dlg.lead} kind={dlg.kind} onClose={() => setDlg(null)} onDone={() => undefined} />}
    </div>
  );
}
