// Shared app state: adapter, meta, theme, time zone, units, selection, layer toggles, decision version.
// React context plus small reducers; no state library (stack decision section 4.2).
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { DataAdapter, Meta } from "../adapters/types";
import type { TimeZone, Units } from "./format";
import { applyTheme, currentTheme, type Theme } from "../theme/theme";
import { readItem, writeItem, storageWorks } from "../storage";

export type Selection = { type: "contact" | "vessel" | "light" | "lead" | "event" | "pass" | "point"; id: string; lon?: number; lat?: number } | null;

export const LAYER_IDS = [
  "land", "aoi", "contacts", "leads", "vessels", "footprints",
  "structures", "lights", "tracks", "reporting_boxes", "eez_boundaries", "next_passes",
] as const;
export type LayerId = (typeof LAYER_IDS)[number];

/** Defaults of spec section 6.2: EEZ, boxes, lights, structures, tracks and next passes OFF. */
export const LAYER_DEFAULTS: Record<LayerId, boolean> = {
  land: true, aoi: true, contacts: true, leads: true, vessels: true, footprints: true,
  structures: false, lights: false, tracks: false, reporting_boxes: false, eez_boundaries: false, next_passes: false,
};

export interface TimeWindow {
  t0: number; // ms
  t1: number; // ms
}

export interface AppState {
  adapter: DataAdapter;
  meta: Meta;
  theme: Theme;
  setTheme: (t: Theme) => void;
  tz: TimeZone;
  setTz: (tz: TimeZone) => void;
  units: Units;
  setUnits: (u: Units) => void;
  selection: Selection;
  setSelection: (s: Selection) => void;
  layers: Record<LayerId, boolean>;
  setLayer: (id: LayerId, on: boolean) => void;
  /** Context raster overlays by registry name (contract 3.7): every one off at load (spec section 6.2). */
  overlays: Record<string, boolean>;
  setOverlay: (name: string, on: boolean) => void;
  clearOverlays: () => void;
  window: TimeWindow | null;
  setWindow: (w: TimeWindow | null) => void;
  decisionsVersion: number;
  bumpDecisions: () => void;
  storageOk: boolean;
  omnibarOpen: boolean;
  setOmnibarOpen: (open: boolean) => void;
  phone: boolean;
  tablet: boolean;
  railTab: string;
  setRailTab: (t: string) => void;
  inspectorOpen: boolean;
  setInspectorOpen: (o: boolean) => void;
  timelineOpen: boolean;
  setTimelineOpen: (o: boolean) => void;
  mapFocus: { lon: number; lat: number; zoom?: number; nonce: number } | null;
  focusMap: (lon: number, lat: number, zoom?: number) => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AppProvider missing");
  return v;
}

function useMedia(query: string): boolean {
  const [m, setM] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return m;
}

export function AppProvider({ adapter, meta, children }: { adapter: DataAdapter; meta: Meta; children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => currentTheme());
  const [tz, setTzState] = useState<TimeZone>(() => (readItem("tz") === "ICT" ? "ICT" : "UTC"));
  const [units, setUnitsState] = useState<Units>(() => (readItem("units") === "nm" ? "nm" : "km"));
  const [selection, setSelection] = useState<Selection>(null);
  const [layers, setLayers] = useState<Record<LayerId, boolean>>({ ...LAYER_DEFAULTS });
  const [overlays, setOverlays] = useState<Record<string, boolean>>({});
  const [win, setWindow] = useState<TimeWindow | null>(null);
  const [decisionsVersion, setDv] = useState(0);
  const [omnibarOpen, setOmnibarOpen] = useState(false);
  const [railTab, setRailTab] = useState<string>("leads");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [mapFocus, setMapFocus] = useState<AppState["mapFocus"]>(null);
  const phone = useMedia("(max-width: 767px)");
  const tablet = useMedia("(min-width: 768px) and (max-width: 1279px)");
  const storageOk = useMemo(() => storageWorks(), []);

  const setTheme = useCallback((t: Theme) => {
    applyTheme(t);
    setThemeState(t);
  }, []);
  const setTz = useCallback((t: TimeZone) => {
    writeItem("tz", t);
    setTzState(t);
  }, []);
  const setUnits = useCallback((u: Units) => {
    writeItem("units", u);
    setUnitsState(u);
  }, []);
  const setLayer = useCallback((id: LayerId, on: boolean) => setLayers((prev) => ({ ...prev, [id]: on })), []);
  const setOverlay = useCallback((name: string, on: boolean) => setOverlays((prev) => ({ ...prev, [name]: on })), []);
  const clearOverlays = useCallback(() => setOverlays({}), []);
  const bumpDecisions = useCallback(() => setDv((v) => v + 1), []);
  const focusMap = useCallback((lon: number, lat: number, zoom?: number) => setMapFocus({ lon, lat, zoom, nonce: Date.now() }), []);

  const value = useMemo<AppState>(
    () => ({
      adapter, meta, theme, setTheme, tz, setTz, units, setUnits, selection, setSelection, layers, setLayer, overlays, setOverlay, clearOverlays,
      window: win, setWindow, decisionsVersion, bumpDecisions, storageOk, omnibarOpen, setOmnibarOpen, phone, tablet,
      railTab, setRailTab, inspectorOpen, setInspectorOpen, timelineOpen, setTimelineOpen, mapFocus, focusMap,
    }),
    [adapter, meta, theme, setTheme, tz, setTz, units, setUnits, selection, layers, setLayer, overlays, setOverlay, clearOverlays, win, decisionsVersion,
      bumpDecisions, storageOk, omnibarOpen, phone, tablet, railTab, inspectorOpen, timelineOpen, mapFocus, focusMap],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Read a promise once per key; re-runs when deps change. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (e) => alive && setState({ data: null, error: String(e && e.message ? e.message : e), loading: false }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
