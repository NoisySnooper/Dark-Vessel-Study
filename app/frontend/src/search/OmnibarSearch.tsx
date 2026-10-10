// Omnibar (spec section 5): mod+K, / or shift+O; MMSI, IMO, det_id, light_id, lead_id, pass_id, name or call sign,
// coordinates in DD, DMS, DDM and MGRS. Results grouped by type; Enter opens; last 10 queries kept in the browser.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { MenuItem, Tag } from "@blueprintjs/core";
import { Omnibar } from "@blueprintjs/select";
import type { SearchResult } from "../adapters/types";
import { useApp } from "../app/state";
import { navigate } from "../app/router";
import { readJson, writeJson } from "../storage";

const TYPE_LABEL: Record<SearchResult["type"], string> = { contact: "Contact", vessel: "Vessel", light: "Light", lead: "Lead", event: "Event", pass: "Pass", point: "Go to point" };

export function OmnibarSearch() {
  const { adapter, omnibarOpen, setOmnibarOpen, focusMap, setSelection, phone } = useApp();
  const [query, setQuery] = useState("");
  // Results carry the query they answer, so a slow answer never shows under a newer query.
  const [result, setResult] = useState<{ q: string; items: SearchResult[] }>({ q: "", items: [] });
  const pending = query.trim() !== "" && result.q !== query.trim();
  const items = pending ? [] : result.items;
  const [recent, setRecent] = useState<string[]>(() => readJson<string[]>("recent", []));
  const seq = useRef(0);

  useEffect(() => {
    if (!omnibarOpen) return;
    const q = query.trim();
    const my = ++seq.current;
    if (!q) {
      setResult({ q: "", items: [] });
      return;
    }
    const t = window.setTimeout(() => {
      adapter.search(q, 20).then((r) => {
        if (my !== seq.current) return;
        setResult({ q, items: r.results });
      }, () => {
        if (my === seq.current) setResult({ q, items: [] });
      });
    }, 80);
    return () => window.clearTimeout(t);
  }, [query, adapter, omnibarOpen]);

  const grouped = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of items) counts[r.type] = (counts[r.type] || 0) + 1;
    return counts;
  }, [items]);

  const select = (r: SearchResult) => {
    const next = [query.trim(), ...recent.filter((x) => x !== query.trim())].slice(0, 10);
    setRecent(next);
    writeJson("recent", next);
    setOmnibarOpen(false);
    if (r.type === "point" && r.lon !== null && r.lat !== null) {
      focusMap(r.lon, r.lat, 9);
      setSelection({ type: "point", id: r.id, lon: r.lon, lat: r.lat });
      if (phone || !window.location.hash.startsWith("#/map")) navigate("map");
      return;
    }
    navigate(r.type as Exclude<SearchResult["type"], "point">, r.id);
  };

  return (
    <Omnibar<SearchResult>
      isOpen={omnibarOpen}
      onClose={() => setOmnibarOpen(false)}
      className="scs-omnibar"
      query={query}
      onQueryChange={setQuery}
      items={items}
      itemListPredicate={(_q, list) => list}
      itemsEqual={(a, b) => a.type === b.type && a.id === b.id}
      onItemSelect={select}
      resetOnSelect={false}
      inputProps={{ placeholder: "MMSI, IMO, det_id, light_id, lead or pass id, vessel name or call sign, coordinates (DD, DMS, DDM, MGRS)", "aria-label": "Search" }}
      itemRenderer={(r, { handleClick, handleFocus, modifiers, index }) => {
        const first = index === 0 || items[(index ?? 0) - 1]?.type !== r.type;
        return (
          <React.Fragment key={r.type + r.id}>
            {first && <li className="bp6-menu-header"><h6 className="bp6-heading">{TYPE_LABEL[r.type]} ({grouped[r.type]})</h6></li>}
            <MenuItem active={modifiers.active} disabled={modifiers.disabled} onClick={handleClick} onFocus={handleFocus} text={r.label} label={r.sublabel} roleStructure="listoption" labelElement={undefined} />
          </React.Fragment>
        );
      }}
      noResults={<MenuItem disabled text={pending ? "Searching" : query.trim() ? "No result in this build" : "Type to search"} roleStructure="listoption" />}
      initialContent={recent.length ? <ul className="bp6-menu">{recent.map((q) => <MenuItem key={q} icon="history" text={q} onClick={() => setQuery(q)} roleStructure="listoption" />)}</ul> : undefined}
      overlayProps={{ hasBackdrop: true }}
    />
  );
}

export function OmnibarHint({ interpretation }: { interpretation: string | null }) {
  return interpretation ? <div className="scs-omnibar-hint"><Tag minimal>{interpretation}</Tag></div> : null;
}
