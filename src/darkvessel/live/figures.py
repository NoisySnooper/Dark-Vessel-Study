"""Figures of one processed live pass: the map (footprints, the detector's tested sea, AIS tracks in the window,
contacts by AIS status, AIS-only vessels), the radar chips of the matched contacts with the AIS track and the
expected radar position, the AIS-only vessels on tested sea by length and speed, and the match quality panels.
Colours: the first slots of the reference categorical palette (validated all-pairs in light mode), neutral grey for
no_coverage, marker shapes as a second encoding; every panel names its classes in a legend. Every figure carries
the dark caveat in its caption.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COL = {"matched": "#2a78d6", "unmatched": "#eb6834", "ambiguous": "#1baf7a", "no_coverage": "#8a8985", "ais_only": "#7a3fb5"}
MARK = {"matched": "o", "unmatched": "^", "ambiguous": "D", "no_coverage": ".", "ais_only": "s"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
LAND, SEA, TESTED = "#ecebe6", "#fcfcfb", "#e3eef9"
CAPTION = ("'Dark' means only that no AIS position was matched. It does not mean illegal: AIS can be off for lawful reasons, "
           "many small boats need not carry it, and terrestrial AIS has blind spots. An AIS gap is not proof of intent.")
CREDIT = "Contains modified Copernicus Sentinel data 2026; live AIS relayed by aisstream.io (terms UNVERIFIED); land Natural Earth."


def status_class(c: pd.DataFrame) -> pd.Series:
    amb = c.match_ambiguous.fillna(False).astype(bool) if "match_ambiguous" in c else pd.Series(False, index=c.index)
    return pd.Series(np.where(amb, "ambiguous", c.ais_status.astype(str)), index=c.index)


def _style(ax):
    ax.set_facecolor(SEA)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7)
    ax.grid(color=GRID, lw=0.4)


def _caption(fig, extra: str = "", y: float = 0.005):
    import textwrap

    fig.text(0.01, y, "\n".join(textwrap.wrap(f"{CAPTION} {CREDIT} {extra}".strip(), 230)), fontsize=6.5, color=MUTED, va="bottom")


def _save(fig, path) -> Path:
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def pass_map(contacts: pd.DataFrame, scenes, ais_window: pd.DataFrame, path: Path, title: str, zoom=None, ais_only: pd.DataFrame | None = None,
             tested=None) -> Path:
    """Two panels: the whole pass, and `zoom` (west, south, east, north; default the box around the AIS-only and matched
    vessels). `tested` (shapely, EPSG:4326) is the detector's tested sea: water inside the footprint and the AOI but
    outside it is land, the 1 km shore buffer or outside the AOI, where nothing is tested."""
    import matplotlib

    matplotlib.use("Agg")
    import geopandas as gpd
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    from darkvessel.aoi import natural_earth_land

    cls = status_class(contacts)
    fp = scenes.geometry.union_all() if len(scenes) else None
    w, s, e, n = fp.bounds if fp is not None else (contacts.lon.min(), contacts.lat.min(), contacts.lon.max(), contacts.lat.max())
    pad = 0.15
    full = (w - pad, s - pad, e + pad, n + pad)
    if zoom is None:
        pts = [contacts.loc[contacts.ais_status == "matched", ["lon", "lat"]]]
        if ais_only is not None and len(ais_only):
            pts.append(ais_only[["lon", "lat"]])
        p = pd.concat(pts)
        zoom = (float(p.lon.quantile(0.02)) - 0.05, float(p.lat.quantile(0.02)) - 0.05,
                float(p.lon.quantile(0.98)) + 0.05, float(p.lat.quantile(0.98)) + 0.05) if len(p) else full
    land = natural_earth_land(bbox=full)
    fig, axes = plt.subplots(1, 2, figsize=(14, 7.2), dpi=170, gridspec_kw={"width_ratios": [1, 1.25]})
    if len(ais_window):  # the tracks the matcher saw: within 0.3 degree of the footprints
        b = fp.buffer(0.3).bounds if fp is not None else full
        ais_window = ais_window[(ais_window.lon >= b[0]) & (ais_window.lon <= b[2]) & (ais_window.lat >= b[1]) & (ais_window.lat <= b[3])]
    tracks = ais_window.sort_values("timestamp").groupby("mmsi") if len(ais_window) else []
    ao = ais_only if ais_only is not None else pd.DataFrame(columns=["lon", "lat", "on_tested_sea", "ambiguous_det_id"])
    for ax, ext, big in ((axes[0], full, False), (axes[1], zoom, True)):
        _style(ax)
        if tested is not None:
            gpd.GeoSeries([tested], crs="EPSG:4326").plot(ax=ax, color=TESTED, edgecolor="none", zorder=0.5)
        land.plot(ax=ax, color=LAND, edgecolor="#c9c8c2", lw=0.4, zorder=1)
        if fp is not None:
            scenes.boundary.plot(ax=ax, color=MUTED, lw=0.8, ls="--", zorder=2)
        for _, g in tracks:
            if len(g) > 1:
                ax.plot(g.lon.to_numpy(), g.lat.to_numpy(), color="#b9b8b2", lw=0.5 if big else 0.3, zorder=3, solid_capstyle="round")
        for k in ("no_coverage", "unmatched", "ambiguous", "matched"):
            sel = cls == k
            if sel.any():
                ax.scatter(contacts.lon[sel], contacts.lat[sel], s=(16 if big else 5) * (2.2 if k == "matched" else 1) if k != "no_coverage" else (8 if big else 2),
                           marker=MARK[k], c=COL[k], edgecolors=SEA if k != "no_coverage" else "none", linewidths=0.4,
                           zorder=9 if k == "matched" else 5,
                           label=None if big else f"contact, {k.replace('_', ' ')} ({int(sel.sum())})")
        if len(ao):
            ts = ao.on_tested_sea.fillna(False).astype(bool).to_numpy()
            for sel, lab, fc in ((ts, "AIS-only on tested sea", COL["ais_only"]), (~ts, "AIS-only, not on tested sea", "none")):
                if sel.any():
                    ax.scatter(ao.lon[sel], ao.lat[sel], s=22 if big else 7, marker=MARK["ais_only"], facecolors=fc, edgecolors=COL["ais_only"],
                               linewidths=0.6, zorder=7, label=None if big else f"{lab} ({int(sel.sum())})")
        ax.set_xlim(ext[0], ext[2])
        ax.set_ylim(ext[1], ext[3])
        ax.set_xlabel("longitude, degrees east", fontsize=8, color=MUTED)
        ax.set_ylabel("latitude, degrees north", fontsize=8, color=MUTED)
        ax.set_aspect(1 / np.cos(np.radians((ext[1] + ext[3]) / 2)))
    if zoom != full:
        z = zoom
        axes[0].plot([z[0], z[2], z[2], z[0], z[0]], [z[1], z[1], z[3], z[3], z[1]], color=INK, lw=0.8, zorder=8)
    axes[0].set_title("Whole pass: scene footprints (dashed), tested sea (blue) and contacts", fontsize=9, color=INK, loc="left")
    axes[1].set_title("Zoom: AIS tracks heard in the window (grey), contacts and AIS-only vessels", fontsize=9, color=INK, loc="left")
    h, lab = axes[0].get_legend_handles_labels()
    h.append(Line2D([0], [0], color="#b9b8b2", lw=1))
    lab.append(f"AIS tracks, scene time +/- 30 min, within 0.3 degree of the footprints ({ais_window.mmsi.nunique() if len(ais_window) else 0} MMSI)")
    if tested is not None:
        h.append(Patch(facecolor=TESTED, edgecolor="none"))
        lab.append("tested sea (beyond 1 km of land, inside the AOI)")
    fig.legend(h, lab, loc="lower center", ncol=4, frameon=False, fontsize=7.5, labelcolor=INK, markerscale=2.0, bbox_to_anchor=(0.5, 0.045))
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha="left")
    _caption(fig, "Water inside the footprints that is not blue lies within 1 km of land or outside the AOI: the detector tests nothing there.")
    fig.tight_layout(rect=(0, 0.13, 1, 0.95))
    return _save(fig, path)


def _orient(geocoder, row, col):
    """(flip_rows, flip_cols) so that north is up and east is right (approximately) for a chip around (row, col)."""
    lo0, la0 = geocoder.lonlat([row, row + 100.0, row], [col, col, col + 100.0])
    return bool(la0[1] > la0[0]), bool(lo0[2] < lo0[0])


def chip_panels(contacts: pd.DataFrame, ais: pd.DataFrame, static_latest: pd.DataFrame | None, scene_paths: dict, scene_times: dict,
                sat_speed: dict, path: Path, title: str, half: int = 120, review_table: pd.DataFrame | None = None,
                ncols: int = 7, log=print) -> Path:
    """Radar chip (VV, dB) of every matched contact with the vessel's AIS reports within 10 min (dots, line), its AIS
    position at the contact time (open square), its expected radar position after the azimuth shift (cross) and the
    other contacts nearby (small triangles). Chips come from the mirror's GRD (review.chip_windows, cached)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from darkvessel.live import review

    m = contacts[contacts.ais_status == "matched"].copy()
    qrank = {"high": 0, "medium": 1, "low": 2}
    m["_q"] = m.match_quality.map(qrank).fillna(3)
    m = m.sort_values(["_q", "match_dist_m"]).reset_index(drop=True)
    n = max(1, len(m))
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(2.05 * ncols, 2.6 * nrows + 1.3), dpi=160, squeeze=False)
    grades = {}
    if review_table is not None and len(review_table) and "grade" in review_table:
        grades = {(d, int(mm)): g for d, mm, g in zip(review_table.det_id, pd.to_numeric(review_table.mmsi, errors="coerce").fillna(-1),
                                                     review_table.grade) if isinstance(g, str)}
    ev = review.matched_evidence(contacts, ais, static_latest, scene_times=scene_times, sat_speed=sat_speed) if len(m) else pd.DataFrame()
    ev = ev.set_index("det_id") if len(ev) else ev
    for sid, g in m.groupby("scene_id"):
        chips = review.chip_windows(scene_paths[sid], g[["det_id", "row", "col"]], half=half, log=log)
        geo = review.scene_geocoder(scene_paths[sid])
        others = contacts[contacts.scene_id == sid]
        for idx, c in g.iterrows():
            ax = axes.flat[idx]
            ch = chips[c.det_id]
            vv = ch["vv"]
            r0, c0 = ch["r0"], ch["c0"]
            flip_r, flip_c = _orient(geo, c.row, c.col)

            def px(lon, lat):
                rr, cc = geo.rowcol(np.asarray(lon, float), np.asarray(lat, float))
                return np.asarray(cc, float) - c0, np.asarray(rr, float) - r0

            ax.imshow(vv, cmap="gray", vmin=-24, vmax=4, origin="lower" if flip_r else "upper", interpolation="nearest")
            t = pd.Timestamp(c.az_time_utc) if isinstance(c.get("az_time_utc"), str) else pd.Timestamp(scene_times[sid])
            t = t.tz_localize("UTC") if t.tzinfo is None else t
            tr = ais[(ais.mmsi == int(c.mmsi)) & ((ais.timestamp - t).abs() <= pd.Timedelta(minutes=10))].sort_values("timestamp")
            if len(tr):
                x, y = px(tr.lon, tr.lat)
                ax.plot(x, y, color=COL["matched"], lw=0.8, marker=".", ms=2.5, zorder=3)
            near = others[(others.det_id != c.det_id)]
            x, y = px(near.lon, near.lat)
            ok = (x > -5) & (x < vv.shape[1] + 5) & (y > -5) & (y < vv.shape[0] + 5)
            ax.scatter(x[ok], y[ok], s=14, marker="^", facecolors="none", edgecolors=COL["ambiguous"], linewidths=0.7, zorder=4)
            if c.det_id in ev.index and pd.notna(ev.loc[c.det_id].get("pred_lon")):
                e = ev.loc[c.det_id]
                x, y = px([e.pred_uncorr_lon, e.pred_lon], [e.pred_uncorr_lat, e.pred_lat])
                ax.scatter(x[:1], y[:1], s=26, marker="s", facecolors="none", edgecolors="#9fc3ef", linewidths=0.9, zorder=5)
                ax.scatter(x[1:], y[1:], s=34, marker="x", c=COL["matched"], linewidths=1.2, zorder=6)
            ax.scatter([c.col - c0], [c.row - r0], s=90, marker="o", facecolors="none", edgecolors=COL["unmatched"], linewidths=0.9, zorder=5)
            ax.set_xlim(0, vv.shape[1])
            ax.set_ylim((0, vv.shape[0]) if flip_r else (vv.shape[0], 0))
            if flip_c:
                ax.invert_xaxis()
            ax.set_xticks([])
            ax.set_yticks([])
            for s in ax.spines.values():
                s.set_color(GRID)
            name = c.vessel_name if isinstance(c.vessel_name, str) else "name not heard"
            la = f"{c.length_ais_m:.0f}" if pd.notna(c.length_ais_m) else "?"
            grade = grades.get((c.det_id, int(c.mmsi)))
            ax.set_title(f"{name[:20]}\n{int(c.mmsi)} | {c.match_quality} | {c.match_dist_m:.0f} m\nradar {c.length_est_m:.0f} m, AIS {la} m"
                         f"{' | ' + grade if grade else ''}", fontsize=6, color=INK, loc="left", pad=2)
    for ax in axes.flat[len(m):]:
        ax.axis("off")
    from matplotlib.lines import Line2D

    h = [Line2D([0], [0], marker="o", ls="", mfc="none", mec=COL["unmatched"], ms=8, label="radar contact (centre of chip)"),
         Line2D([0], [0], marker="x", ls="", color=COL["matched"], ms=7, label="expected radar position of the AIS vessel (after the azimuth shift)"),
         Line2D([0], [0], marker="s", ls="", mfc="none", mec="#9fc3ef", ms=6, label="AIS position at the contact time, no shift"),
         Line2D([0], [0], color=COL["matched"], marker=".", lw=0.8, label="AIS reports within 10 min"),
         Line2D([0], [0], marker="^", ls="", mfc="none", mec=COL["ambiguous"], ms=6, label="other contacts")]
    fig.legend(handles=h, loc="lower center", ncol=3, frameon=False, fontsize=7, bbox_to_anchor=(0.5, 0.45 / fig.get_figheight()))
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha="left")
    _caption(fig, f"Chips: Sentinel-1 VV sigma0, -24 to +4 dB, {2 * half * 10 / 1000:.1f} km wide, radar geometry turned so that north is roughly up. Panels ordered by match quality, "
                  "then distance; the hand-check grade follows the length line.")
    fig.subplots_adjust(left=0.01, right=0.99, top=1 - 0.75 / fig.get_figheight(), bottom=1.15 / fig.get_figheight(), wspace=0.06, hspace=0.42)
    return _save(fig, path)


def ais_only_panel(ais_only: pd.DataFrame, contacts: pd.DataFrame, path: Path, title: str) -> Path:
    """AIS-only vessels on tested sea (the radar's misses, ambiguous ones apart) against the matched vessels: AIS length
    against speed over ground, and the counts per AIS length bin."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ao = ais_only.copy()
    ts = ao.on_tested_sea.fillna(False).astype(bool) if len(ao) else pd.Series(dtype=bool)
    amb = ao.ambiguous_det_id.notna() if len(ao) and "ambiguous_det_id" in ao else pd.Series(False, index=ao.index)
    miss = ao[ts & ~amb] if len(ao) else ao
    ambt = ao[ts & amb] if len(ao) else ao
    m = contacts[contacts.ais_status == "matched"]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.6), dpi=170, gridspec_kw={"width_ratios": [1.3, 1]})
    ax = axes[0]
    _style(ax)
    ax.set_facecolor("white")

    def L(df, col):
        v = pd.to_numeric(df[col], errors="coerce")
        return v.fillna(8.0)  # unknown length drawn at 8 m, left of the axis break

    over = miss.oversized_det_id.notna() if len(miss) and "oversized_det_id" in miss else pd.Series(False, index=miss.index)
    weak = (miss.nearest_object_m.le(500) & ~over) if len(miss) else pd.Series(dtype=bool)
    none_ = ~over & ~weak
    ax.scatter(L(m, "length_ais_m"), pd.to_numeric(m.ais_sog_kn, errors="coerce"), s=22, marker="o", c=COL["matched"], edgecolors="white",
               linewidths=0.4, label=f"matched ({len(m)})", zorder=4)
    if len(ambt):
        ax.scatter(L(ambt, "length_ais_m"), pd.to_numeric(ambt.sog_kn, errors="coerce"), s=22, marker="D", facecolors="none",
                   edgecolors=COL["ambiguous"], linewidths=0.8, label=f"AIS-only, held back as ambiguous ({len(ambt)})", zorder=3)
    if len(miss):
        for sel, lab, mk, fc in ((over, "AIS-only, paired with an oversized return (a large bright return the detector dropped)", "P", COL["ais_only"]),
                                 (weak, "AIS-only, a weak (low) return within 500 m", "s", "none"),
                                 (none_, "AIS-only, no return within 500 m", "s", COL["ais_only"])):
            if sel.any():
                ax.scatter(L(miss[sel], "length_ais_m"), pd.to_numeric(miss[sel].sog_kn, errors="coerce"), s=34, marker=mk, facecolors=fc,
                           edgecolors=COL["ais_only"], linewidths=0.9, label=f"{lab} ({int(sel.sum())})", zorder=5)
    ax.set_xscale("log")
    ax.set_xlim(6, 500)
    ax.axvline(10, color=GRID, lw=0.8)
    ax.text(8, -0.06, "length\nunknown", fontsize=6, color=MUTED, ha="center", va="top", transform=ax.get_xaxis_transform())
    ax.set_xlabel("AIS length (static message A + B), m", fontsize=8, color=MUTED)
    ax.set_ylabel("speed over ground, kn", fontsize=8, color=MUTED)
    ax.legend(fontsize=7, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2)
    ax.set_title("AIS vessels on tested sea: matched or not seen as a contact", fontsize=9, color=INK, loc="left")
    ax = axes[1]
    _style(ax)
    ax.set_facecolor("white")
    bins = [0, 25, 50, 100, 200, 500]
    labs = [f"{a}-{b}" for a, b in zip(bins[:-1], bins[1:])]
    cm = pd.cut(pd.to_numeric(m.length_ais_m, errors="coerce"), bins, labels=labs).value_counts().reindex(labs, fill_value=0)
    cmiss = pd.cut(pd.to_numeric(miss.length_ais_m, errors="coerce"), bins, labels=labs).value_counts().reindex(labs, fill_value=0) if len(miss) else pd.Series(0, index=labs)
    x = np.arange(len(labs))
    ax.bar(x - 0.2, cm.values, 0.4, color=COL["matched"], label="matched")
    ax.bar(x + 0.2, cmiss.values, 0.4, color=COL["ais_only"], label="AIS-only on tested sea, not ambiguous")
    for xi, a_, b_ in zip(x, cm.values, cmiss.values):
        if a_ + b_:
            ax.text(xi, max(a_, b_) + 0.3, f"{a_ / (a_ + b_):.0%}", ha="center", fontsize=7, color=INK)
    ax.set_xticks(x, labs, fontsize=7)
    ax.set_xlabel("AIS length bin, m (unknown lengths left out)", fontsize=8, color=MUTED)
    ax.set_ylabel("vessels", fontsize=8, color=MUTED)
    ax.set_ylim(0, max(1, int(max(cm.max(), cmiss.max()))) * 1.25)
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    ax.set_title("Per length bin; the label is matched / (matched + AIS-only)", fontsize=9, color=INK, loc="left")
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha="left")
    _caption(fig, "Tested sea = the detector's sea mask (water beyond 1 km of land, inside the AOI). Small numbers: read as counts, not rates.")
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    return _save(fig, path)


def match_panels(contacts: pd.DataFrame, path: Path, title: str) -> Path:
    """Match distance (with and without the azimuth shift), time to the nearest AIS report, radar vs AIS length."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    m = contacts[contacts.ais_status == "matched"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), dpi=150)
    for ax in axes:
        _style(ax)
        ax.set_facecolor("white")
    d = pd.to_numeric(m.match_dist_m, errors="coerce").dropna()
    du = pd.to_numeric(m.get("match_dist_uncorr_m"), errors="coerce").dropna() if "match_dist_uncorr_m" in m else pd.Series(dtype=float)
    bins = np.arange(0, max(2000.0, float(d.max()) if len(d) else 0) + 50, 50)
    axes[0].hist(d, bins=bins, color=COL["matched"], alpha=0.9, label=f"after azimuth shift (median {d.median():.0f} m)" if len(d) else "none",
                 edgecolor="white", linewidth=0.5)
    if len(du):
        axes[0].hist(du, bins=bins, histtype="step", color=INK, lw=1.0, label=f"without the shift (median {du.median():.0f} m)")
    axes[0].set_xlim(0, min(2000, bins[-1]))
    axes[0].set_xlabel("radar to AIS distance, m", fontsize=8, color=MUTED)
    axes[0].set_ylabel("matched contacts", fontsize=8, color=MUTED)
    axes[0].legend(fontsize=7, frameon=False)
    axes[0].set_title("Match distance", fontsize=9, color=INK, loc="left")
    t = pd.to_numeric(m.match_dt_s, errors="coerce").dropna()
    axes[1].hist(t, bins=np.arange(0, 1800 + 30, 30), color=COL["matched"], edgecolor="white", linewidth=0.5)
    axes[1].set_xlabel("time from the scene to the nearest AIS report used, s", fontsize=8, color=MUTED)
    axes[1].set_title(f"Time offset (median {t.median():.0f} s)" if len(t) else "Time offset", fontsize=9, color=INK, loc="left")
    la = pd.to_numeric(m.length_ais_m, errors="coerce")
    lr = pd.to_numeric(m.length_est_m, errors="coerce")
    ok = la.notna() & lr.notna() & (la > 0) & (lr > 0)
    axes[2].scatter(la[ok], lr[ok], s=12, c=COL["matched"], edgecolors="white", linewidths=0.4, label=f"{int(ok.sum())} matched with AIS length")
    lim = (5, 600)
    axes[2].plot(lim, lim, color=MUTED, lw=0.8, ls="--", label="radar = AIS")
    axes[2].set_xscale("log")
    axes[2].set_yscale("log")
    axes[2].set_xlim(*lim)
    axes[2].set_ylim(*lim)
    axes[2].set_xlabel("AIS length (static message A + B), m", fontsize=8, color=MUTED)
    axes[2].set_ylabel("radar length estimate, m", fontsize=8, color=MUTED)
    if ok.any():
        ratio = (lr[ok] / la[ok])
        axes[2].set_title(f"Length: median radar/AIS {ratio.median():.2f}", fontsize=9, color=INK, loc="left")
    axes[2].legend(fontsize=7, frameon=False, loc="upper left")
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha="left")
    _caption(fig)
    fig.tight_layout(rect=(0, 0.07, 1, 0.93))
    return _save(fig, path)
