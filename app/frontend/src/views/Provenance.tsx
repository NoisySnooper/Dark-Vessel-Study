// Provenance chip on every field (spec section 4.8): Tooltip on hover and focus, Popover on click or tap.
import React from "react";
import { Popover, Tooltip } from "@blueprintjs/core";
import { useApp } from "../app/state";
import type { Provenanced, SourceEntry } from "../adapters/types";

export function useSource(key: string | null | undefined): SourceEntry | null {
  const { meta } = useApp();
  if (!key) return null;
  return meta.sources.find((s) => s.key === key) || null;
}

export function sourceFor(rec: Pick<Provenanced, "src" | "prov"> | null | undefined, field: string): string | null {
  if (!rec) return null;
  return (rec.prov && rec.prov[field]) || rec.src || null;
}

export function ProvChip({ sourceKey, field }: { sourceKey: string | null; field: string }) {
  const { meta } = useApp();
  const src = sourceKey ? meta.sources.find((s) => s.key === sourceKey) || null : null;
  const name = src?.name || sourceKey || "unknown source";
  const body = (
    <div className="scs-prov-pop">
      <strong>{name}</strong>
      <dl>
        <dt>field</dt><dd>{field}</dd>
        <dt>source key</dt><dd>{sourceKey || "unknown"}</dd>
        {src?.method && (<><dt>method</dt><dd>{src.method}</dd></>)}
        {src?.script && (<><dt>script</dt><dd>{src.script}</dd></>)}
        <dt>git hash</dt><dd>{meta.git_hash}</dd>
        <dt>licence</dt><dd>{src?.licence || "unknown"}{src?.licence_url ? <> (<a href={src.licence_url} target="_blank" rel="noreferrer">text</a>)</> : null}</dd>
        {src?.url && (<><dt>url</dt><dd><a href={src.url} target="_blank" rel="noreferrer">{src.url}</a></dd></>)}
        <dt>access date</dt><dd>{src?.access_date || "unknown"}</dd>
        {src?.credit && (<><dt>credit</dt><dd>{src.credit}</dd></>)}
        <dt>build time</dt><dd>{meta.generated_utc}</dd>
      </dl>
    </div>
  );
  return (
    <Popover content={body} interactionKind="click" placement="bottom-start" popoverClassName="scs-prov-popover">
      <Tooltip content={`Source: ${name}. Click for licence and access date.`} placement="top" hoverOpenDelay={200}>
        <button type="button" className="scs-prov-chip" aria-label={`Provenance of ${field}: ${name}`} data-prov={sourceKey || ""}>i</button>
      </Tooltip>
    </Popover>
  );
}

/** A labelled value with its chip. `judgment` renders model or analyst outputs in muted text. */
export function Field({ label, value, rec, field, judgment, modelId, children }: {
  label: string;
  value?: React.ReactNode;
  rec: Pick<Provenanced, "src" | "prov"> | null | undefined;
  field: string;
  judgment?: boolean;
  modelId?: string | null;
  children?: React.ReactNode;
}) {
  const v = value === undefined || value === null || value === "" ? children : value;
  return (
    <div className="scs-field" data-field={field}>
      <span className="k">{label} <ProvChip sourceKey={sourceFor(rec, field)} field={field} /></span>
      <span className={"v" + (judgment ? " judgment" : "")}>{v === undefined || v === null || v === "" ? <span className="scs-muted">unknown</span> : v}{judgment && modelId ? <span className="scs-muted"> ({modelId})</span> : null}</span>
    </div>
  );
}

export function ProvenanceTable({ rec, fields }: { rec: Provenanced; fields: string[] }) {
  const { meta } = useApp();
  return (
    <div className="scs-table-scroll">
    <table className="scs-provtable bp6-html-table bp6-compact">
      <thead><tr><th>field</th><th>source</th><th>licence</th><th>access date</th></tr></thead>
      <tbody>
        {fields.map((f) => {
          // A field without a value gets no source: falling back to the record's `src` would credit the wrong producer.
          const v = (rec as unknown as Record<string, unknown>)[f];
          const empty = v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
          if (empty) return (<tr key={f} data-prov-empty="1"><td>{f}</td><td colSpan={3} className="scs-muted">no value in this record</td></tr>);
          const key = sourceFor(rec, f);
          const s = meta.sources.find((x) => x.key === key);
          return (<tr key={f}><td>{f}</td><td>{s?.name || key || "unknown"}</td><td>{s?.licence || "unknown"}</td><td>{s?.access_date || "unknown"}</td></tr>);
        })}
      </tbody>
    </table>
    </div>
  );
}
