// Typed-array canvas layer for bulk points (stack decision section 4.1): one canvas in the overlay pane, viewport culling,
// marker styling by AIS status, detector class, radar length and CNN verdict (spec section 6.3). Hit testing is a linear
// scan over the points drawn in the last frame, which is fast enough for the fixture and for one pass; a grid index is a
// later optimisation for the full regional layer.
import L from "leaflet";
import type { PointLayerData } from "../adapters/types";

export interface PointStyle {
  colors: string[]; // by status index, used when `byStatus`
  color: string; // single colour otherwise
  outline: string; // 1 px outline in the map background colour
  byStatus: boolean;
  hollowStatus?: number; // status index drawn hollow (not_checked)
  dotZoom: number; // below this zoom, 3 px dots
  baseRadius: number;
}

export interface HitResult {
  index: number;
  id: string;
  lon: number;
  lat: number;
}

export class PointLayer extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private data: PointLayerData | null = null;
  private style: PointStyle;
  private filter: ((i: number) => boolean) | null = null;
  private window: [number, number] | null = null;
  private highlight = new Set<number>();
  private drawn: Int32Array = new Int32Array(0);
  private drawnPx: Float32Array = new Float32Array(0);
  private drawnN = 0;
  private mapRef: L.Map | null = null;
  readonly name: string;

  constructor(name: string, style: PointStyle) {
    super();
    this.name = name;
    this.style = style;
  }

  setData(d: PointLayerData | null): void {
    this.data = d;
    this.redraw();
  }
  setFilter(f: ((i: number) => boolean) | null): void {
    this.filter = f;
    this.redraw();
  }
  setWindow(w: [number, number] | null): void {
    this.window = w;
    this.redraw();
  }
  setStyle(s: PointStyle): void {
    this.style = s;
    this.redraw();
  }
  setHighlight(ids: Set<number>): void {
    this.highlight = ids;
    this.redraw();
  }
  count(): number {
    return this.drawnN;
  }

  onAdd(map: L.Map): this {
    this.mapRef = map;
    this.canvas = L.DomUtil.create("canvas", "scs-point-layer leaflet-layer") as HTMLCanvasElement;
    map.getPanes().overlayPane.appendChild(this.canvas);
    map.on("moveend zoomend viewreset resize", this.redraw, this);
    this.redraw();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off("moveend zoomend viewreset resize", this.redraw, this);
    if (this.canvas) this.canvas.remove();
    this.canvas = null;
    this.mapRef = null;
    return this;
  }

  /** Nearest drawn point within `tol` px of a container point. */
  hitTest(pt: L.Point, tol = 8): HitResult | null {
    if (!this.data) return null;
    let best = -1;
    let bestD = tol * tol;
    for (let k = 0; k < this.drawnN; k++) {
      const dx = this.drawnPx[2 * k] - pt.x;
      const dy = this.drawnPx[2 * k + 1] - pt.y;
      const d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        best = k;
      }
    }
    if (best < 0) return null;
    const i = this.drawn[best];
    return { index: i, id: this.data.ids(i), lon: this.data.lon[i], lat: this.data.lat[i] };
  }

  redraw = (): void => {
    const map = this.mapRef;
    const canvas = this.canvas;
    if (!map || !canvas) return;
    const size = map.getSize();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== size.x * dpr || canvas.height !== size.y * dpr) {
      canvas.width = size.x * dpr;
      canvas.height = size.y * dpr;
      canvas.style.width = size.x + "px";
      canvas.style.height = size.y + "px";
    }
    const tl = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, tl);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    const d = this.data;
    if (!d) {
      this.drawnN = 0;
      return;
    }
    const zoom = map.getZoom();
    const world = 256 * Math.pow(2, zoom);
    const origin = map.getPixelOrigin();
    const ox = origin.x + tl.x;
    const oy = origin.y + tl.y;
    const dots = zoom < this.style.dotZoom;
    const base = this.style.baseRadius;
    if (this.drawn.length < d.n) {
      this.drawn = new Int32Array(d.n);
      this.drawnPx = new Float32Array(2 * d.n);
    }
    let k = 0;
    const pad = 12;
    const w0 = this.window ? this.window[0] : -Infinity;
    const w1 = this.window ? this.window[1] : Infinity;
    const ctxOutline = this.style.outline;
    for (let i = 0; i < d.n; i++) {
      if (this.filter && !this.filter(i)) continue;
      const lon = d.lon[i];
      const lat = d.lat[i];
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
      const x = ((lon + 180) / 360) * world - ox;
      if (x < -pad || x > size.x + pad) continue;
      const latR = (lat * Math.PI) / 180;
      const y = (0.5 - Math.log(Math.tan(Math.PI / 4 + latR / 2)) / (2 * Math.PI)) * world - oy;
      if (y < -pad || y > size.y + pad) continue;
      const t = d.time[i];
      const inWindow = Number.isNaN(t) || (t >= w0 && t <= w1);
      const faded = d.faded[i] === 1;
      const color = this.style.byStatus ? this.style.colors[d.status[i]] || this.style.color : this.style.color;
      ctx.globalAlpha = (inWindow ? 1 : 0.2) * (faded ? 0.5 : 1);
      const hollow = this.style.byStatus && this.style.hollowStatus !== undefined && d.status[i] === this.style.hollowStatus;
      if (dots) {
        ctx.fillStyle = color;
        ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
      } else {
        const r = base + d.size[i] * 1.5;
        ctx.lineWidth = 1;
        ctx.strokeStyle = ctxOutline;
        ctx.fillStyle = color;
        const shape = d.shape[i];
        ctx.beginPath();
        if (shape === 2) ctx.rect(x - r, y - r, 2 * r, 2 * r);
        else if (shape === 3) ctx.arc(x, y, Math.max(1.5, r * 0.5), 0, Math.PI * 2);
        else ctx.arc(x, y, r, 0, Math.PI * 2);
        if (shape === 1 || hollow) {
          ctx.stroke();
          ctx.lineWidth = 2;
          ctx.strokeStyle = color;
          ctx.stroke();
        } else {
          ctx.fill();
          ctx.stroke();
        }
      }
      if (this.highlight.has(i)) {
        ctx.globalAlpha = 1;
        ctx.lineWidth = 2;
        ctx.strokeStyle = this.style.outline === "#111418" ? "#f6f7f9" : "#1c2127";
        ctx.beginPath();
        ctx.arc(x, y, base + 6, 0, Math.PI * 2);
        ctx.stroke();
      }
      this.drawn[k] = i;
      this.drawnPx[2 * k] = x;
      this.drawnPx[2 * k + 1] = y;
      k++;
    }
    ctx.globalAlpha = 1;
    this.drawnN = k;
  };
}
