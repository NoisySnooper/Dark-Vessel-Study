// Leaflet map (spec section 6): Web Mercator, no tile layer, land from the geo part, bulk points on typed-array canvas
// layers, EEZ boundary lines present but OFF by default and labelled as published, no external requests.
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { Callout } from "@blueprintjs/core";
import { useApp, type LayerId } from "../app/state";
import { PointLayer, type HitResult } from "./PointLayer";
import { featureRings } from "../adapters/columns";
import type { GeoLayer, Lead, PointLayerData } from "../adapters/types";
import { AIS_STATUS_ORDER } from "../adapters/types";
import { AIS_STATUS_LABEL, DARK_CAVEAT_SHORT, EEZ_DISCLAIMER, EEZ_STATEMENT, OCEAN_NOTE, REPORTING_BOX_NOTE } from "../app/text";
import type { RasterEntry } from "../adapters/types";
import { fmtLegendValue, isPresence, rampCss, rasterLabel, reprojectOverlay } from "./overlays";
import { SHIPPING_PRESENCE_NOTE } from "../adapters/context";
import { ProvChip } from "../views/Provenance";
import { fmtDd, fmtDms, fmtMgrs, fmtNum, fmtTime } from "../app/format";
import { cssToken } from "../theme/theme";
import { navigate } from "../app/router";

const AOI_BOUNDS: L.LatLngBoundsExpression = [[-3.3, 99.1], [23.8, 122.3]];
const LEAD_RING_MAX = 300;

// Leaflet 1.9.4's Canvas renderer can run a queued redraw after its map was removed (route change while a vector draw
// is pending), when `_ctx` is already deleted: "Cannot read properties of undefined (reading 'clearRect')". Skip such
// a redraw; a live renderer is unaffected.
type CanvasInternals = { _redraw: () => void; _ctx?: CanvasRenderingContext2D; _map?: L.Map | null; _redrawRequest: number | null; _scsGuard?: boolean };
const canvasProto = L.Canvas.prototype as unknown as CanvasInternals;
if (!canvasProto._scsGuard) {
  const redraw = canvasProto._redraw;
  canvasProto._redraw = function (this: CanvasInternals) {
    if (!this._ctx || !this._map) {
      this._redrawRequest = null;
      return;
    }
    redraw.call(this);
  };
  canvasProto._scsGuard = true;
}

function statusColors(): string[] {
  return [cssToken("--status-matched"), cssToken("--status-unmatched"), cssToken("--status-nocov"), cssToken("--status-notchecked")];
}

function addGeo(layer: GeoLayer, make: (rings: [number, number][][], j: number) => L.Layer | null, group: L.LayerGroup): void {
  for (let j = 0; j < layer.n; j++) {
    const rings = featureRings(layer, j);
    if (!rings.length) continue;
    const l = make(rings, j);
    if (l) group.addLayer(l);
  }
}

export interface MapViewProps {
  focus?: { lon: number; lat: number; zoom?: number } | null;
  height?: number | string;
  compact?: boolean; // object-page map: fewer controls
  marker?: { lon: number; lat: number; label?: string } | null;
}

export function MapView({ compact, marker }: MapViewProps) {
  const { adapter, layers, overlays, theme, selection, setSelection, window: win, mapFocus, decisionsVersion, phone } = useApp();
  const overlayRefs = useRef<Record<string, { layer: L.ImageOverlay; theme: string }>>({});
  const [rasterList, setRasterList] = useState<RasterEntry[]>([]);
  const [overlayErr, setOverlayErr] = useState<string | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const groups = useRef<Record<string, L.LayerGroup>>({});
  const points = useRef<Record<string, PointLayer>>({});
  const selRing = useRef<L.CircleMarker | null>(null);
  const markerRef = useRef<L.CircleMarker | null>(null);
  const [readout, setReadout] = useState<string>("");
  const [hover, setHover] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const [eezNotice, setEezNotice] = useState(false);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [ringInfo, setRingInfo] = useState<{ shown: number; total: number }>({ shown: 0, total: 0 });
  const dataRef = useRef<{ contacts: PointLayerData | null; lights: PointLayerData | null; vessels: PointLayerData | null; leads: Lead[] }>({ contacts: null, lights: null, vessels: null, leads: [] });

  // Create the map once.
  useEffect(() => {
    if (!el.current || mapRef.current) return;
    const map = L.map(el.current, {
      preferCanvas: true, zoomControl: !compact, attributionControl: false, minZoom: 3, maxZoom: 14, worldCopyJump: false,
      zoomSnap: 0.5, keyboard: true,
    });
    map.fitBounds(AOI_BOUNDS, { padding: [4, 4] });
    // Context overlays sit under land, outlines and points (overlayPane is z-index 400).
    const ctxPane = map.createPane("scs-context");
    ctxPane.style.zIndex = "350";
    ctxPane.style.pointerEvents = "none";
    if (!compact) L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
    for (const id of ["land", "aoi", "reporting_boxes", "eez_boundaries", "footprints", "next_passes", "tracks", "leads"]) {
      groups.current[id] = L.layerGroup();
    }
    const bg = cssToken("--map-sea") || "#111418";
    const mk = (name: string, byStatus: boolean, color: string, hollow?: number) =>
      new PointLayer(name, { colors: statusColors(), color, outline: bg, byStatus, hollowStatus: hollow, dotZoom: 7, baseRadius: 3.5 });
    points.current.contacts = mk("contacts", true, cssToken("--status-nocov"), 3);
    points.current.structures = mk("structures", false, cssToken("--fixed"));
    points.current.lights = mk("lights", false, cssToken("--light"));
    points.current.vessels = mk("vessels", false, cssToken("--vessel"));
    points.current.structures.setFilter(() => false);
    mapRef.current = map;

    const onMove = (e: L.LeafletMouseEvent) => {
      const { lat, lng } = e.latlng;
      setReadout(`${fmtDd(lat, lng)}\n${fmtDms(lat, lng)}\n${fmtMgrs(lat, lng)}`);
      let hit: HitResult | null = null;
      let kind = "";
      for (const k of ["contacts", "structures", "vessels", "lights"]) {
        const pl = points.current[k];
        if (!pl || !map.hasLayer(pl)) continue;
        hit = pl.hitTest(e.containerPoint, 7);
        if (hit) {
          kind = k;
          break;
        }
      }
      if (!hit) {
        setHover(null);
        return;
      }
      const d = dataRef.current;
      const lines: string[] = [hit.id];
      if ((kind === "contacts" || kind === "structures") && d.contacts) {
        const s = d.contacts.status[hit.index];
        const st = AIS_STATUS_ORDER[s];
        lines.push(st ? AIS_STATUS_LABEL[st] : "radar contact");
        lines.push(fmtTime(new Date(d.contacts.time[hit.index]).toISOString()));
        if (st === "unmatched" || st === "no_coverage") lines.push(DARK_CAVEAT_SHORT);
      } else if (kind === "lights") {
        lines.push("VIIRS light; a light is not a vessel identity");
      } else if (kind === "vessels") {
        lines.push("AIS vessel, last recorded position");
      }
      setHover({ x: e.containerPoint.x, y: e.containerPoint.y, lines });
    };
    map.on("mousemove", onMove);
    map.on("mouseout", () => setHover(null));
    map.on("click", (e: L.LeafletMouseEvent) => {
      for (const k of ["contacts", "structures", "vessels", "lights"]) {
        const pl = points.current[k];
        if (!pl || !map.hasLayer(pl)) continue;
        const hit = pl.hitTest(e.containerPoint, 9);
        if (hit) {
          const type = k === "lights" ? "light" : k === "vessels" ? "vessel" : "contact";
          setSelection({ type, id: hit.id, lon: hit.lon, lat: hit.lat });
          return;
        }
      }
      setSelection(null);
    });
    return () => {
      mapRef.current = null;
      for (const g of Object.values(groups.current)) {
        g.clearLayers();
        g.remove();
      }
      for (const pl of Object.values(points.current)) pl.remove();
      for (const o of Object.values(overlayRefs.current)) o.layer.remove();
      overlayRefs.current = {};
      if (selRing.current) selRing.current.remove();
      if (markerRef.current) markerRef.current.remove();
      selRing.current = null;
      markerRef.current = null;
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Theme: recolour.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const bg = cssToken("--map-sea");
    const cols = statusColors();
    points.current.contacts?.setStyle({ colors: cols, color: cssToken("--status-nocov"), outline: bg, byStatus: true, hollowStatus: 3, dotZoom: 7, baseRadius: 3.5 });
    points.current.structures?.setStyle({ colors: cols, color: cssToken("--fixed"), outline: bg, byStatus: false, dotZoom: 7, baseRadius: 3.5 });
    points.current.lights?.setStyle({ colors: cols, color: cssToken("--light"), outline: bg, byStatus: false, dotZoom: 7, baseRadius: 3 });
    points.current.vessels?.setStyle({ colors: cols, color: cssToken("--vessel"), outline: bg, byStatus: false, dotZoom: 7, baseRadius: 3 });
    groups.current.land?.eachLayer((l) => (l as L.Polygon).setStyle({ fillColor: cssToken("--map-land"), color: cssToken("--map-land") }));
    groups.current.aoi?.eachLayer((l) => (l as L.Polygon).setStyle({ color: cssToken("--text-muted") }));
    groups.current.footprints?.eachLayer((l) => (l as L.Polygon).setStyle({ color: cssToken("--footprint") }));
    groups.current.next_passes?.eachLayer((l) => (l as L.Polygon).setStyle({ color: cssToken("--footprint") }));
    groups.current.eez_boundaries?.eachLayer((l) => (l as L.Polyline).setStyle({ color: cssToken("--eez-line") }));
    groups.current.tracks?.eachLayer((l) => (l as L.Polyline).setStyle({ color: cssToken("--vessel") }));
    groups.current.leads?.eachLayer((l) => (l as L.CircleMarker).setStyle({ color: cssToken("--lead-ring") }));
  }, [theme]);

  // Load geo layers and points once.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let alive = true;
    (async () => {
      const [land, aoi, boxes, eez, fps] = await Promise.all([adapter.geo("land"), adapter.geo("aoi"), adapter.geo("reporting_boxes"), adapter.geo("eez_boundaries"), adapter.geo("footprints")]);
      if (!alive || mapRef.current !== map) return;
      if (land) addGeo(land, (rings) => L.polygon(rings, { fillColor: cssToken("--map-land"), color: cssToken("--map-land"), weight: 0.5, fillOpacity: 1, interactive: false }), groups.current.land);
      if (aoi) addGeo(aoi, (rings) => L.polygon(rings, { fill: false, color: cssToken("--text-muted"), weight: 1, dashArray: "2 4", interactive: false }), groups.current.aoi);
      if (boxes) addGeo(boxes, (rings, j) => L.polygon(rings, { fill: false, color: cssToken("--text-muted"), weight: 1, dashArray: "6 4" }).bindTooltip(`${String(boxes.props.name?.(j) ?? "box")} (${REPORTING_BOX_NOTE})`, { sticky: true }), groups.current.reporting_boxes);
      if (eez) addGeo(eez, (rings, j) => L.polyline(rings, { color: cssToken("--eez-line"), weight: 1, opacity: 0.9 }).bindTooltip(`${String(eez.props.line_name?.(j) ?? "boundary line")}: ${String(eez.props.line_type?.(j) ?? "")} (as published by Marine Regions, no position taken)`, { sticky: true }), groups.current.eez_boundaries);
      if (fps) addGeo(fps, (rings, j) => L.polygon(rings, { fill: false, color: cssToken("--footprint"), weight: 1, dashArray: "4 3", interactive: !!fps.props.pass_id }).bindTooltip(`${String(fps.props.product_id?.(j) ?? "scene")}`, { sticky: true }).on("click", () => { const pid = fps.props.pass_id?.(j); if (pid) navigate("pass", String(pid)); }), groups.current.footprints);
      const upcoming = (await adapter.passes({ status: "upcoming" })).items;
      if (!alive || mapRef.current !== map) return;
      for (const p of upcoming) {
        const g = p.footprint as { type: string; coordinates: number[][][] | number[][][][] } | null;
        if (!g) continue;
        const polys = g.type === "Polygon" ? [g.coordinates as number[][][]] : (g.coordinates as number[][][][]);
        for (const poly of polys) {
          L.polygon(poly.map((ring) => ring.map((c) => [c[1], c[0]] as [number, number])), { fill: false, color: cssToken("--footprint"), weight: 1, dashArray: "2 6" })
            .bindTooltip(`${p.pass_id}: ${p.mission} ${fmtTime(p.start_utc, "UTC", false)} (planned)`, { sticky: true })
            .on("click", () => navigate("pass", p.pass_id))
            .addTo(groups.current.next_passes);
        }
      }
      const [contacts, lights, vessels] = await Promise.all([adapter.contactPoints(), adapter.lightPoints(), adapter.vesselPoints()]);
      if (!alive || mapRef.current !== map) return;
      dataRef.current.contacts = contacts;
      dataRef.current.lights = lights;
      dataRef.current.vessels = vessels;
      points.current.contacts.setData(contacts);
      points.current.structures.setData(contacts);
      points.current.contacts.setFilter(contacts ? (i) => contacts.shape[i] !== 2 : null);
      points.current.structures.setFilter(contacts ? (i) => contacts.shape[i] === 2 : () => false);
      points.current.lights.setData(lights);
      points.current.vessels.setData(vessels);
      setCounts({ contacts: contacts ? contacts.n : 0, lights: lights ? lights.n : 0, vessels: vessels ? vessels.n : 0 });
    })();
    return () => {
      alive = false;
    };
  }, [adapter]);

  // Leads rings.
  useEffect(() => {
    const g = groups.current.leads;
    if (!g) return;
    let alive = true;
    adapter.leads({ state: "new,reviewing", limit: 5000 }).then(({ items }) => {
      if (!alive || !mapRef.current) return;
      g.clearLayers();
      dataRef.current.leads = items;
      // Rings for the highest-priority open leads only: thousands of rings hide the map (2,137 L7 cells in the local app).
      const ringed = [...items].sort((a, b) => b.priority - a.priority).slice(0, LEAD_RING_MAX);
      setRingInfo({ shown: ringed.length, total: items.length });
      for (const L1 of ringed) {
        L.circleMarker([L1.lat, L1.lon], { radius: 9.5, color: cssToken("--lead-ring"), weight: 2, fill: false, interactive: true })
          .bindTooltip(`${L1.lead_id}: priority ${L1.priority}`, { sticky: true })
          .on("click", () => setSelection({ type: "lead", id: L1.lead_id, lon: L1.lon, lat: L1.lat }))
          .addTo(g);
      }
    });
    return () => {
      alive = false;
    };
  }, [adapter, decisionsVersion, setSelection]);

  // Layer toggles.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const toggle = (id: LayerId, layer: L.Layer | undefined) => {
      if (!layer) return;
      if (layers[id] && !map.hasLayer(layer)) layer.addTo(map);
      if (!layers[id] && map.hasLayer(layer)) map.removeLayer(layer);
    };
    toggle("land", groups.current.land);
    toggle("aoi", groups.current.aoi);
    toggle("reporting_boxes", groups.current.reporting_boxes);
    toggle("eez_boundaries", groups.current.eez_boundaries);
    toggle("footprints", groups.current.footprints);
    toggle("next_passes", groups.current.next_passes);
    toggle("tracks", groups.current.tracks);
    toggle("leads", groups.current.leads);
    toggle("contacts", points.current.contacts);
    toggle("structures", points.current.structures);
    toggle("lights", points.current.lights);
    toggle("vessels", points.current.vessels);
    setEezNotice(layers.eez_boundaries);
    // keep draw order: points above polygons
    for (const k of ["contacts", "structures", "lights", "vessels"]) {
      const pl = points.current[k];
      if (pl && map.hasLayer(pl)) pl.redraw();
    }
  }, [layers]);

  // Context raster overlays: off at load; each one reprojected to Web Mercator when first switched on (and per theme).
  const activeOverlays = useMemo(() => Object.keys(overlays).filter((k) => overlays[k]).sort(), [overlays]);
  useEffect(() => {
    if (compact || !activeOverlays.length || rasterList.length) return;
    let alive = true;
    adapter.rasters().then((r) => { if (alive) setRasterList(r); });
    return () => { alive = false; };
  }, [adapter, compact, activeOverlays.length, rasterList.length]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || compact) return;
    let alive = true;
    for (const [name, o] of Object.entries(overlayRefs.current)) {
      if (!overlays[name] || o.theme !== theme) {
        o.layer.remove();
        delete overlayRefs.current[name];
      }
    }
    (async () => {
      for (const name of activeOverlays) {
        if (overlayRefs.current[name]) continue;
        const entry = rasterList.find((r) => r.name === name);
        if (!entry) continue;
        try {
          const url = await adapter.rasterImage(name, theme);
          if (!url) throw new Error("no image for " + name);
          const img = await reprojectOverlay(url, entry.bounds, isPresence(entry), cssToken("--overlay-mask") || "#c5cbd3");
          if (!alive || mapRef.current !== map || !overlays[name] || overlayRefs.current[name]) continue;
          const [w, s2, e, n] = entry.bounds;
          const layer = L.imageOverlay(img, [[s2, w], [n, e]], { opacity: isPresence(entry) ? 1 : 0.85, interactive: false, pane: "scs-context", className: "scs-overlay" });
          layer.addTo(map);
          overlayRefs.current[name] = { layer, theme };
          setOverlayErr(null);
        } catch (err) {
          console.warn("overlay " + name + " not drawn", err);
          if (alive) setOverlayErr(`${rasterLabel(entry)} could not be drawn in this page.`);
        }
      }
    })();
    return () => { alive = false; };
  }, [adapter, compact, activeOverlays, rasterList, theme, overlays]);

  // Time window.
  useEffect(() => {
    const w: [number, number] | null = win ? [win.t0, win.t1] : null;
    for (const k of Object.keys(points.current)) points.current[k].setWindow(w);
  }, [win]);

  // Selection ring.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (selRing.current) {
      map.removeLayer(selRing.current);
      selRing.current = null;
    }
    if (selection && selection.lon !== undefined && selection.lat !== undefined) {
      selRing.current = L.circleMarker([selection.lat, selection.lon], { radius: 12, color: cssToken("--focus"), weight: 2, fill: true, fillOpacity: 0.08, interactive: false }).addTo(map);
    }
  }, [selection]);

  // Focus requests (Omnibar "go to point", object pages).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapFocus) return;
    map.setView([mapFocus.lat, mapFocus.lon], mapFocus.zoom ?? Math.max(map.getZoom(), 9), { animate: false });
    if (markerRef.current) map.removeLayer(markerRef.current);
    markerRef.current = L.circleMarker([mapFocus.lat, mapFocus.lon], { radius: 6, color: cssToken("--focus"), weight: 2, fill: false, interactive: false }).addTo(map);
  }, [mapFocus]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !marker) return;
    map.setView([marker.lat, marker.lon], 10, { animate: false });
    if (markerRef.current) map.removeLayer(markerRef.current);
    markerRef.current = L.circleMarker([marker.lat, marker.lon], { radius: 7, color: cssToken("--focus"), weight: 2, fill: false, interactive: false }).addTo(map);
    // Depend on the position, not the object: callers pass a fresh object on every render, which would re-centre the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marker?.lon, marker?.lat]);

  // Resize when the container changes (tab switches, phone rotation).
  useEffect(() => {
    const map = mapRef.current;
    const node = el.current;
    if (!map || !node) return;
    const ro = new ResizeObserver(() => {
      if (mapRef.current === map && node.isConnected) map.invalidateSize({ animate: false });
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  const legend = useMemo(() => (
    <div className="scs-map-legend" aria-label="Map legend">
      {AIS_STATUS_ORDER.map((s) => (
        <div className="row" key={s}><i className={"scs-swatch" + (s === "not_checked" ? " ring" : "")} style={{ background: `var(--status-${s === "no_coverage" ? "nocov" : s === "not_checked" ? "notchecked" : s})`, color: `var(--status-${s === "no_coverage" ? "nocov" : s === "not_checked" ? "notchecked" : s})` }} />{AIS_STATUS_LABEL[s]}</div>
      ))}
      {layers.structures && <div className="row"><i className="scs-swatch square" style={{ background: "var(--fixed)" }} />fixed structure</div>}
      {layers.lights && <div className="row"><i className="scs-swatch" style={{ background: "var(--light)" }} />VIIRS light</div>}
      {layers.vessels && <div className="row"><i className="scs-swatch" style={{ background: "var(--vessel)" }} />AIS vessel (last position)</div>}
      {!compact && <div className="row scs-muted">{fmtNum(counts.contacts ?? 0)} contacts in this build</div>}
      {!compact && layers.leads && ringInfo.total > ringInfo.shown && <div className="row scs-muted">rings: top {fmtNum(ringInfo.shown)} of {fmtNum(ringInfo.total)} open leads by priority</div>}
    </div>
  ), [layers.structures, layers.lights, layers.vessels, layers.leads, counts, compact, ringInfo]);

  const overlayLegend = (
        <div className="scs-overlay-legend" data-overlay-legend="1" aria-label="Context layer legend">
          <div className="scs-ocean-caveat" data-ocean-caveat="1">{OCEAN_NOTE}</div>
          {activeOverlays.map((name) => {
            const r = rasterList.find((x) => x.name === name);
            if (!r) return <div key={name} className="scs-muted">loading {name}</div>;
            const presence = isPresence(r);
            return (
              <div key={name} className="scs-overlay-row" data-overlay={name}>
                <div className="scs-overlay-title">{rasterLabel(r)} <ProvChip sourceKey={r.src} field={r.name} /></div>
                {presence
                  ? <div className="scs-overlay-scale"><i className="scs-swatch square" style={{ background: "repeating-linear-gradient(45deg, var(--overlay-mask) 0 2px, transparent 2px 4px)" }} /> hatched: published value above 0 ({SHIPPING_PRESENCE_NOTE})</div>
                  : <><div className="scs-overlay-ramp" style={{ background: rampCss(r.colormap) }} /><div className="scs-overlay-scale"><span>{fmtLegendValue(r.vmin)}</span><span>{r.unit || ""}</span><span>{fmtLegendValue(r.vmax)}</span></div></>}
                <div className="scs-muted">valid: {r.valid_period || "static layer"}{r.resolution_deg ? `; ${r.resolution_deg} degree grid` : ""}</div>
              </div>
            );
          })}
          {overlayErr && <div className="scs-muted">{overlayErr}</div>}
        </div>
  );

  return (
    <div className="scs-map-area" style={compact ? { height: 260 } : undefined}>
      <div ref={el} className="scs-map" role="application" aria-label="Map of radar contacts and AIS vessels over the South China Sea AOI" tabIndex={0} data-eez-on={layers.eez_boundaries ? "1" : "0"} />
      {!compact && (
        <div className="scs-map-side">
          {legend}
          {activeOverlays.length > 0 && overlayLegend}
        </div>
      )}
      {!compact && !phone && readout && <div className="scs-map-readout" aria-live="off">{readout}</div>}
      {hover && !compact && (
        <div className="scs-map-hover" style={{ left: Math.min(hover.x + 12, Math.max(0, (el.current?.clientWidth || 300) - 270)), top: hover.y + 12 }}>
          {hover.lines.map((l, i) => <div key={i} className={i === 0 ? "" : "muted"}>{l}</div>)}
        </div>
      )}
      {eezNotice && (
        <Callout className="scs-eez-notice" compact icon="info-sign" data-eez-notice="1">
          {EEZ_STATEMENT} {EEZ_DISCLAIMER}
        </Callout>
      )}
    </div>
  );
}
