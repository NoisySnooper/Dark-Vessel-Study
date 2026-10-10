// Context raster overlays (spec section 6.2, contract 3.7 Raster layers): labels, groups, legend ramps and the
// reprojection of a lon/lat (EPSG:4326) image onto the Web Mercator map. Every overlay is off at load.
import type { RasterEntry } from "../adapters/types";

/** Display names; an unknown raster shows its registry name humanised. */
const LABEL: Record<string, string> = {
  depth_m: "Depth",
  dist_coast_km: "Distance to the coast",
  dist_port_km: "Distance to the nearest major port",
  sst_mean_c: "Sea surface temperature, window mean",
  sst_showcase_c: "Sea surface temperature, showcase night",
  sst_grad_mean_c_per_km: "SST gradient, window mean",
  front_freq: "SST front frequency",
  chl_mean_mg_m3: "Chlorophyll-a, window mean",
  chl_valid_share: "Chlorophyll-a, share of valid days",
  current_speed_mean_ms: "Current speed, window mean",
  mld_mean_m: "Mixed layer depth, window mean",
  ssh_grad_mean: "Sea surface height gradient, window mean",
  wave_hs_mean_m: "Significant wave height, window mean",
  wind_mean_ms: "Wind speed, window mean",
  ship_density_all: "Shipping presence, all types",
  ship_density_commercial: "Shipping presence, commercial",
  ship_density_fishing: "Shipping presence, fishing",
  ship_density_oilgas: "Shipping presence, oil and gas",
  ship_density_passenger: "Shipping presence, passenger",
  ship_density_leisure: "Shipping presence, leisure",
  ais_reach_share: "AIS reach, share of recorded hours",
  ais_reach_mmsi: "AIS reach, vessels heard",
  s1_look_prob_1d: "Sentinel-1 look probability, 1 day",
  s1_look_prob_7d: "Sentinel-1 look probability, 7 days",
  s1_look_prob_30d: "Sentinel-1 look probability, 30 days",
  s1_passes: "Sentinel-1 passes, 90 days",
  vessel_density_regional: "Radar vessel candidates per look",
  viirs_lit_density: "Lit vessel candidates per VIIRS pass",
  viirs_lit_density_clear: "Lit vessel candidates per clear VIIRS pass",
};

export function rasterLabel(r: Pick<RasterEntry, "name" | "label">): string {
  return LABEL[r.name] || r.name.replace(/_/g, " ");
}

/** Shipping density grids are drawn as presence only (board D4.3): one mask colour where the published value is above 0. */
export function isPresence(r: Pick<RasterEntry, "name" | "unit">): boolean {
  return r.name.startsWith("ship_density") || /^presence/i.test(r.unit || "");
}

export const OVERLAY_GROUPS = ["Sea floor and coast", "Ocean conditions", "Shipping presence", "Coverage", "Activity", "Research build"] as const;
export function rasterGroup(r: Pick<RasterEntry, "name" | "research_only">): (typeof OVERLAY_GROUPS)[number] {
  const n = r.name;
  if (r.research_only) return "Research build";
  if (/^(depth|dist_)/.test(n)) return "Sea floor and coast";
  if (n.startsWith("ship_density")) return "Shipping presence";
  if (/^(ais_reach|s1_)/.test(n)) return "Coverage";
  if (/^(vessel_density|viirs_)/.test(n)) return "Activity";
  return "Ocean conditions";
}

// Five-stop approximations of the named colour maps (legend only; the image carries the real colours).
const RAMPS: Record<string, string[]> = {
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  plasma: ["#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"],
  inferno: ["#000004", "#57106e", "#bc3754", "#f98e09", "#fcffa4"],
  magma: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
  cividis: ["#00224e", "#434e6c", "#7d7c78", "#bcaf6f", "#fee838"],
  Blues: ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
  Greys: ["#ffffff", "#d9d9d9", "#969696", "#525252", "#000000"],
};
export function rampCss(colormap: string): string {
  const stops = RAMPS[colormap] || RAMPS.viridis;
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

export function fmtLegendValue(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "?";
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(2);
}

function mercY(latDeg: number): number {
  const phi = (Math.max(-85, Math.min(85, latDeg)) * Math.PI) / 180;
  return Math.log(Math.tan(Math.PI / 4 + phi / 2));
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("overlay image did not load"));
    img.src = url;
  });
}

/**
 * The overlay images are on a lon/lat grid (EPSG:4326); the map is Web Mercator. Stretching the image into its bounds
 * would shift features by up to about 0.36 degree of latitude over this AOI, so each output row takes the source row
 * at its Mercator latitude. Presence layers become a hatch in one mask colour. Returns a data URI (no request leaves
 * the page).
 */
export async function reprojectOverlay(url: string, bounds: [number, number, number, number], presence: boolean, maskColor: string): Promise<string> {
  const img = await loadImage(url);
  const [, south, , north] = bounds;
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const src = document.createElement("canvas");
  src.width = w;
  src.height = h;
  const sctx = src.getContext("2d");
  if (!sctx) return url;
  sctx.drawImage(img, 0, 0);
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const octx = out.getContext("2d");
  if (!octx) return url;
  const yN = mercY(north);
  const yS = mercY(south);
  for (let r = 0; r < h; r++) {
    // output row r spans Mercator y from yN down to yS; take the source row at the same latitude
    const y = yN - ((r + 0.5) / h) * (yN - yS);
    const lat = (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * (180 / Math.PI);
    const sr = Math.min(h - 1, Math.max(0, Math.floor(((north - lat) / (north - south)) * h)));
    octx.drawImage(src, 0, sr, w, 1, 0, r, w, 1);
  }
  if (presence) {
    const data = octx.getImageData(0, 0, w, h);
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(maskColor.trim()) || ["", "c5", "cb", "d3"];
    const [cr, cg, cb] = [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
    // A diagonal hatch, so the mask reads as presence (not as a filled quantity) and other overlays show through.
    const px = data.data;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i + 3] === 0) continue;
      const p = i / 4;
      const x = p % w;
      const y = (p - x) / w;
      const on = (x + y) % 4 === 0;
      px[i] = cr;
      px[i + 1] = cg;
      px[i + 2] = cb;
      px[i + 3] = on ? 220 : 0;
    }
    octx.putImageData(data, 0, 0);
  }
  return out.toDataURL("image/png");
}
