// Radar chip (spec section 4.2): VV and VH side by side, 130 x 64 px source shown at 2x, "radar geometry, not north-up".
// `chip` is the record's chip field (contract 3.1): null means no cached or embedded chip, so nothing is requested.
// Undefined means the caller does not know it; the adapter then reads it from the contact record.
import { useEffect, useState } from "react";
import { Button, Callout } from "@blueprintjs/core";
import { useApp, useAsync } from "../app/state";
import { CHIP_CAPTION } from "../app/text";

export function ChipImage({ det_id, chip }: { det_id: string; chip?: string | null }) {
  const { adapter } = useApp();
  const { data, loading } = useAsync(() => adapter.chip(det_id, chip), [adapter, det_id, chip]);
  const [fetched, setFetched] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setFetched(null);
    setErr(null);
    setBroken(false);
  }, [det_id]);
  if (loading) return null;
  const src = data || fetched;
  if (!src) {
    const canFetch = adapter.kind === "http" && !!adapter.fetchChip;
    const doFetch = async () => {
      if (!adapter.fetchChip) return;
      setBusy(true);
      setErr(null);
      try {
        setFetched(await adapter.fetchChip(det_id));
      } catch (e) {
        setErr(String((e as Error).message || e));
      } finally {
        setBusy(false);
      }
    };
    return (
      <div data-chip="none">
        <Callout compact icon="media" title={canFetch ? "No cached radar chip" : "No radar chip in this page"} className="scs-chip-none">
          <div style={{ fontSize: 12 }}>{canFetch ? "Fetch chip asks the local app to build it from the Sentinel-1 scene." : "Chips are embedded by the bundle builder up to a byte budget; the local app can build any chip."}</div>
          {canFetch && <Button small icon="download" text="Fetch chip" loading={busy} onClick={doFetch} data-fetch-chip="1" style={{ marginTop: 6 }} />}
        </Callout>
        {err && <Callout compact intent="warning" style={{ marginTop: 6, fontSize: 12 }} data-chip-error="1">Chip not available: {err}</Callout>}
      </div>
    );
  }
  return (
    <div className="scs-chip-box" data-chip="shown">
      {!broken && <img src={src} alt={`Radar chip of ${det_id}: VV left, VH right, 64 x 64 pixels each at 10 m spacing`} onError={() => setBroken(true)} />}
      <div className="scs-muted" style={{ fontSize: 12 }}>{broken ? "The chip image did not load." : CHIP_CAPTION}</div>
    </div>
  );
}
