// Lead card (spec section 4.1): radar chip, factor list with points, the full caveat, lawful explanations,
// "what would change this", decision buttons with the reason picker. Decisions go through the adapter
// (POST in http mode; page memory plus JSONL export in embedded mode).
import { useEffect, useMemo, useState } from "react";
import { Button, ButtonGroup, Callout, Dialog, DialogBody, DialogFooter, HTMLSelect, Tag, TextArea, useHotkeys, type HotkeyConfig } from "@blueprintjs/core";
import type { Lead, LeadState } from "../adapters/types";
import { useApp } from "../app/state";
import { CHANGE_TEXT, EXPLAINED_REASONS, FACTOR_LABEL, FALSE_ALARM_REASONS, LAWFUL_TEXT, LEAD_TYPE_NAME, STATE_LABEL, STORAGE_WARNING, UNCALIBRATED, codeText } from "../app/text";
import { fmtRelative, fmtTime, priorityBand } from "../app/format";
import { Field, ProvChip, sourceFor } from "./Provenance";
import { CaveatCallout, ObjectLink, StatusChip, downloadText } from "./common";
import { ChipImage } from "./ChipImage";

export type DecisionKind = "reviewing" | "closed_explained" | "closed_unexplained" | "closed_false_alarm" | "reopen";

export function PriorityBar({ p, compact }: { p: number; compact?: boolean }) {
  const band = priorityBand(p);
  return (
    <span className="scs-prio" aria-label={`review priority ${p} of 100, ${band}`} title={`review priority ${p} of 100, ${band}`}>
      <span className={"scs-prio-bar " + band}><i style={{ width: `${Math.max(2, Math.min(100, p))}%` }} /></span>
      <span>{p}</span>{!compact && <span className="scs-muted">{band}</span>}
    </span>
  );
}

export function DecisionDialog({ lead, kind, onClose, onDone }: { lead: Lead; kind: DecisionKind | null; onClose: () => void; onDone: (l: Lead) => void }) {
  const { adapter, bumpDecisions } = useApp();
  const [reason, setReason] = useState<string>("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    // No preselected reason: the decision log calibrates the priorities, so a reason must be a choice (owner question 6).
    setReason("");
    setNote("");
    setErr(null);
  }, [kind, lead.lead_id]);
  if (!kind) return null;
  const to: LeadState = kind === "reopen" ? "reviewing" : kind;
  const reasons = kind === "closed_explained" ? EXPLAINED_REASONS : kind === "closed_false_alarm" ? FALSE_ALARM_REASONS : null;
  const needsNote = kind === "closed_unexplained" || kind === "reopen" || (reason.startsWith("other"));
  const needsReason = !!reasons && !reason;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const updated = await adapter.decide(lead.lead_id, to, reasons ? reason : null, note || null, "analyst");
      bumpDecisions();
      onDone(updated);
      onClose();
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog isOpen onClose={onClose} title={`${lead.lead_id}: ${STATE_LABEL[to]}`} canOutsideClickClose>
      <DialogBody>
        {kind === "closed_unexplained" && <p>Evidence reviewed, no explanation found. This is not a finding of wrongdoing. A note is required.</p>}
        {kind === "reviewing" && <p>Set to reviewing. The note is optional.</p>}
        {reasons && (
          <label className="bp6-label">Reason
            <HTMLSelect value={reason} onChange={(e) => setReason(e.currentTarget.value)} options={[{ value: "", label: "Choose a reason" }, ...reasons.map((r) => ({ value: r, label: r }))]} fill aria-label="Reason" />
          </label>
        )}
        <label className="bp6-label">Note{needsNote ? " (required)" : ""}
          <TextArea value={note} onChange={(e) => setNote(e.currentTarget.value)} fill rows={3} aria-label="Decision note" />
        </label>
        {err && <Callout intent="danger" compact>{err}</Callout>}
      </DialogBody>
      <DialogFooter actions={<><Button text="Cancel" onClick={onClose} /><Button intent="primary" text="Record decision" onClick={submit} loading={busy} disabled={needsReason || (needsNote && !note.trim())} /></>} />
    </Dialog>
  );
}

/** Record `reviewing` directly (spec sections 4.1 and 9: no input needed). */
export async function setReviewing(adapter: ReturnType<typeof useApp>["adapter"], lead: Lead): Promise<Lead | null> {
  if (lead.state !== "new") return null;
  return adapter.decide(lead.lead_id, "reviewing", null, null, "analyst");
}

export function ExportDecisions({ highlight, noWarning }: { highlight?: boolean; noWarning?: boolean }) {
  const { adapter, storageOk: storageWorks, decisionsVersion } = useApp();
  // Only the single-file page keeps decisions in the browser; the local app writes them to data/labels/.
  const storageOk = storageWorks || adapter.kind !== "embedded";
  const [shown, setShown] = useState(false);
  const log = adapter.decisionLog();
  const jsonl = log.map((d) => JSON.stringify(d)).join("\n");
  void decisionsVersion;
  return (
    <div>
      {!storageOk && !noWarning && <Callout compact intent="warning" data-storage-warning="1">{STORAGE_WARNING}</Callout>}
      <ButtonGroup>
        <Button icon="download" intent={highlight && !storageOk ? "primary" : undefined} data-export-highlight={highlight && !storageOk ? "1" : "0"} text={`Export decisions (${log.length})`} onClick={() => { downloadText("lead_decisions.jsonl", jsonl + (jsonl ? "\n" : ""), "application/x-ndjson"); setShown(true); }} />
        <Button minimal text={shown ? "Hide" : "Show JSONL"} onClick={() => setShown((s) => !s)} />
      </ButtonGroup>
      {shown && <TextArea className="scs-export-area" readOnly value={jsonl || "(no decisions yet)"} fill aria-label="Decision log as JSONL" />}
    </div>
  );
}

export function LeadCard({ lead, onUpdate, full }: { lead: Lead; onUpdate?: (l: Lead) => void; full?: boolean }) {
  const { tz, adapter, storageOk: storageWorks, decisionsVersion, bumpDecisions } = useApp();
  const storageBlocked = !storageWorks && adapter.kind === "embedded";
  const [dlg, setDlg] = useState<DecisionKind | null>(null);
  const [current, setCurrent] = useState(lead);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setCurrent(lead), [lead]);
  const closed = current.state.startsWith("closed");
  const done = (l: Lead) => {
    setCurrent(l);
    onUpdate?.(l);
  };
  const reviewing = async () => {
    setErr(null);
    try {
      const l = await setReviewing(adapter, current);
      if (l) {
        bumpDecisions();
        done(l);
      }
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };
  // On the full lead page the card owns the decision keys; in the console the queue registers them.
  const hotkeys = useMemo<HotkeyConfig[]>(() => (full ? [
    { combo: "r", global: true, group: "Lead", label: "Set reviewing", onKeyDown: () => { if (!closed) void reviewing(); } },
    { combo: "e", global: true, group: "Lead", label: "Close as explained (reason picker)", onKeyDown: () => !closed && setDlg("closed_explained") },
    { combo: "u", global: true, group: "Lead", label: "Close as unexplained (note)", onKeyDown: () => !closed && setDlg("closed_unexplained") },
    { combo: "x", global: true, group: "Lead", label: "Close as false alarm (reason picker)", onKeyDown: () => !closed && setDlg("closed_false_alarm") },
  ] : []), // eslint-disable-next-line react-hooks/exhaustive-deps
  [full, closed, current]);
  useHotkeys(hotkeys);
  return (
    <div className="scs-leadcard" data-lead-id={current.lead_id} data-state={current.state}>
      <div className="scs-tagrow">
        <Tag minimal>{current.lead_type}: {LEAD_TYPE_NAME[current.lead_type] || current.lead_type}</Tag>
        <Tag minimal intent={current.state === "new" ? "primary" : undefined}>{STATE_LABEL[current.state]}</Tag>
        {!current.calibrated && <Tag minimal intent="warning">{UNCALIBRATED}</Tag>}
        {current.synthetic && <Tag intent="danger" minimal>SYNTHETIC fixture lead</Tag>}
      </div>
      {!full && <div className="title">{current.title}</div>}
      <div className="scs-fields" style={{ margin: "8px 0" }}>
        <Field label="Review priority (not a risk score)" rec={current} field="priority" judgment modelId={current.priority_model_id}><PriorityBar p={current.priority} /></Field>
        <Field label="Primary object" rec={current} field="primary_id">
          <ObjectLink type={current.primary_type === "contact" ? "contact" : current.primary_type === "light" ? "light" : current.primary_type === "vessel" ? "vessel" : "cell"} id={current.primary_id} />
        </Field>
        <Field label="Time" rec={current} field="time_utc" value={fmtTime(current.time_utc, tz)} />
        <Field label="Reporting box" rec={current} field="region_box" value={current.region_box ? `${current.region_box} (statistics only, not a boundary)` : "other"} />
        {current.ais_status && <Field label="AIS status of the contact" rec={current} field="ais_status"><StatusChip status={current.ais_status} /></Field>}
        <Field label="Next radar look" rec={current} field="next_look_utc" value={current.next_look_utc ? `${fmtTime(current.next_look_utc, tz, false)} (${fmtRelative(current.next_look_utc)}, from the acquisition plan)` : "none planned in the plan window"} />
      </div>
      {current.primary_type === "contact" && <ChipImage det_id={current.primary_id} />}
      <h6 className="bp6-heading">Factors <ProvChip sourceKey={sourceFor(current, "factors")} field="factors" /></h6>
      <table className="scs-factors judgment" data-factors="1">
        <tbody>
          {current.factors.map((f) => (
            <tr key={f.factor}><td>{codeText(f.factor, FACTOR_LABEL)}{f.value !== null && f.value !== undefined ? <span className="scs-muted"> ({String(f.value)})</span> : null}</td><td className="pts">{f.points} / {f.max_points}</td></tr>
          ))}
          <tr><td><strong>Priority</strong> ({current.priority_model_id}{current.calibrated ? "" : ", uncalibrated"})</td><td className="pts"><strong>{current.priority}</strong> / 100</td></tr>
        </tbody>
      </table>
      {current.extra?.weather ? <p className="scs-muted" style={{ fontSize: 12 }}>{String(current.extra.weather)}</p> : null}
      <CaveatCallout caveat={current.caveat} extra={
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {current.lawful_explanations.map((x) => <li key={x}>{codeText(x, LAWFUL_TEXT)}</li>)}
        </ul>
      } />
      <h6 className="bp6-heading">What would change this</h6>
      <ul style={{ margin: "0 0 8px", paddingLeft: 18, fontSize: 13 }}>
        {current.change_indicators.map((x) => <li key={x}>{codeText(x, CHANGE_TEXT)}</li>)}
      </ul>
      <div className="scs-decisions" role="group" aria-label="Lead decision">
        {current.state === "new" && <Button icon="eye-open" text="Reviewing (R)" onClick={() => void reviewing()} />}
        {current.state === "new" && <Button minimal small text="Reviewing with a note" onClick={() => setDlg("reviewing")} />}
        {!closed && <Button icon="tick" text="Explained (E)" onClick={() => setDlg("closed_explained")} />}
        {!closed && <Button icon="help" text="Unexplained (U)" onClick={() => setDlg("closed_unexplained")} />}
        {!closed && <Button icon="cross" text="False alarm (X)" onClick={() => setDlg("closed_false_alarm")} />}
        {closed && <Button icon="undo" text="Reopen to reviewing" onClick={() => setDlg("reopen")} />}
      </div>
      {err && <Callout compact intent="danger">{err}</Callout>}
      {storageBlocked && <Callout compact intent="warning" data-storage-warning="1" style={{ marginBottom: 8 }}>{STORAGE_WARNING}</Callout>}
      {storageBlocked && !full && <ExportDecisions highlight={decisionsVersion > 0} noWarning />}
      {current.history.length > 0 && (
        <div className="scs-history">
          <h6 className="bp6-heading">Decision history</h6>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {current.history.map((h, i) => (
              <li key={i}>{fmtTime(h.time_utc, tz)}: {h.user} set {STATE_LABEL[h.from_state]} to {STATE_LABEL[h.to_state]}{h.reason ? `, reason: ${h.reason}` : ""}{h.note ? `, note: ${h.note}` : ""}</li>
            ))}
          </ul>
        </div>
      )}
      {current.extra?.synthetic_note ? <Callout compact intent="danger" style={{ marginTop: 8 }}>{String(current.extra.synthetic_note)}</Callout> : null}
      <DecisionDialog lead={current} kind={dlg} onClose={() => setDlg(null)} onDone={done} />
    </div>
  );
}

