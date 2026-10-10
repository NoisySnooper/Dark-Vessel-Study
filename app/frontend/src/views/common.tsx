// Small shared pieces: status chip, object header frame, caveat callout, links, coordinates block, not-built state.
import React from "react";
import { Button, Callout, EntityTitle, H4, NonIdealState, Tag } from "@blueprintjs/core";
import type { AisStatus, Confidence } from "../adapters/types";
import { AIS_STATUS_LABEL, AIS_STATUS_SHORT, CONFIDENCE_LABEL, DARK_CAVEAT_SHORT, NOT_BUILT, PRODUCT_CAVEAT } from "../app/text";
import { fmtDd, fmtDdm, fmtDms, fmtMgrs } from "../app/format";
import { hrefFor, navigate, type RouteName } from "../app/router";
import { useApp } from "../app/state";

export function StatusChip({ status, short }: { status: AisStatus; short?: boolean }) {
  return (
    <span className={"scs-status " + status} data-status={status}>
      <i className="scs-swatch" aria-hidden="true" />
      <span className="label">{short ? AIS_STATUS_SHORT[status] : AIS_STATUS_LABEL[status]}</span>
    </span>
  );
}

export function ConfidenceTag({ c }: { c: Confidence }) {
  return <Tag minimal data-confidence={c}>{c}: {CONFIDENCE_LABEL[c]}</Tag>;
}

export function CaveatCallout({ caveat, extra }: { caveat?: string; extra?: React.ReactNode }) {
  const { meta } = useApp();
  return (
    <Callout compact intent="warning" icon="info-sign" title="Dark does not mean illegal" className="scs-caveat-callout">
      <div>{caveat || meta.caveat || PRODUCT_CAVEAT}</div>
      {extra}
    </Callout>
  );
}

export function ObjectLink({ type, id, label }: { type: RouteName; id: string; label?: React.ReactNode }) {
  return <a href={hrefFor(type, id)}>{label ?? id}</a>;
}

export function ObjectHeader({ title, subtitle, tags, icon, back }: {
  title: string; subtitle?: string | React.JSX.Element; tags?: React.JSX.Element; icon?: React.ComponentProps<typeof EntityTitle>["icon"]; back?: RouteName;
}) {
  const { phone } = useApp();
  return (
    <header>
      {(phone || back) && (
        <Button className="scs-back" small minimal icon="arrow-left" text="Back" onClick={() => (window.history.length > 1 ? window.history.back() : navigate(back || "leads"))} />
      )}
      <EntityTitle heading={H4} title={title} subtitle={subtitle} icon={icon} tags={tags} ellipsize={false} />
      <p className="scs-caveat-line" data-caveat="short">{DARK_CAVEAT_SHORT}</p>
    </header>
  );
}

export function CoordBlock({ lat, lon }: { lat: number; lon: number }) {
  return (
    <div className="scs-fields">
      <div className="scs-field"><span className="k">DD</span><span className="v">{fmtDd(lat, lon)}</span></div>
      <div className="scs-field"><span className="k">DMS</span><span className="v">{fmtDms(lat, lon)}</span></div>
      <div className="scs-field"><span className="k">DDM</span><span className="v">{fmtDdm(lat, lon)}</span></div>
      <div className="scs-field"><span className="k">MGRS</span><span className="v">{fmtMgrs(lat, lon)}</span></div>
    </div>
  );
}

export function NotBuilt({ what, note }: { what: string; note?: string }) {
  return <NonIdealState icon="build" title={`${what}: ${NOT_BUILT}`} description={note || "The producing step has not run yet. The view will fill when the file exists."} />;
}

export function Missing({ what, id }: { what: string; id: string }) {
  return <NonIdealState icon="search" title={`${what} not in this build`} description={<span>No record with id <code>{id}</code>. The local app and the GeoPackage hold every record; the single-file page holds a subset.</span>} action={<Button text="Back to the queue" onClick={() => navigate("leads")} />} />;
}

export function Loading({ what }: { what: string }) {
  return <NonIdealState icon="time" title={`Loading ${what}`} />;
}

export function copyText(text: string): void {
  try {
    void navigator.clipboard?.writeText(text);
  } catch {
    /* clipboard blocked: the text is visible on the page */
  }
}

/** Download a text file; works from file:// (object URL and an anchor with download). */
export function downloadText(name: string, text: string, mime = "text/plain"): void {
  try {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {
    /* no download: the export textarea stays as the fallback */
  }
}
