"""Live Sentinel-1 passes over the South China Sea AOI: detect, verify, match to recorded AIS, identify.

Purpose: real dark-vessel detection AND identification in the open build. For every Sentinel-1C/1D IW scene that
lands on the AWS mirror while the aisstream recorder (scripts/26_ais_record.py) is running, write every radar contact
with an AIS status: matched (with the vessel's identity), unmatched (a dark lead with its evidence) or no_coverage
(the live feed heard nothing there, so nothing can be said).

Method (darkvessel.live): poll the mirror every 10 min for AOI scenes since --since; skip scenes with no AIS in the
plus or minus 30 min window; detect with the regional CA-CFAR settings, persistence, clutter and near-fixed rules;
score every vessel candidate with the CNN verifier (chips read remotely, no scene download); place every AIS vessel
at each contact's azimuth time (track interpolation or dead reckoning) plus the Sentinel-1 azimuth shift of a moving
target, speed-aware distance gate (500 m + 125 s x SOG, at most 2 km), minimum-cost one-to-one assignment, ambiguity
test (a pair too close to call stays unmatched and is not a lead), gear beacons as coverage only (darkvessel.live.assign),
match_quality from distance, time and length agreement; identity from aisstream static messages and the MMSI's MID;
AIS status from whether the feed heard anything in the contact's 0.25 degree cell or within 20 km; GFS wind and
Himawari cloud tops per contact for the lead gate (darkvessel.live.weather). Checkpointed per scene; a processed scene is
never redone and a crash resumes; --rematch redoes the pairing from the stored objects without detecting again.

Inputs:  AWS mirror sentinel-s1-l1c (productInfo.json, manifest.safe, COG tiles), data/s1_next_passes.json (pass
         naming, re-read each cycle), data/cache/ais/aisstream/ (positions, static), data/models/verifier_v0.pt,
         data/s1_footprints.gpkg (persistence archive), data/outputs/small/dist_coast_km_4326.tif (optional, AIS-only
         shore test), data/aoi.gpkg via darkvessel.aoi
Output:  data/live/live_<mission>_<yyyymmddThhmm>.gpkg per pass (contacts_*, ais_only_*, scenes_*, about),
         data/live/live_contacts.gpkg (all passes), data/live/live_summary.json,
         data/live/<run_id>_weather.parquet (GFS wind, Himawari cloud tops per contact; darkvessel.live.weather);
         checkpoints and caches in data/cache/live/ (scenes/, products/, passes.json, watch.pid, watch.log)
Usage:
  python scripts/30_live_pass.py --once                      one cycle: process every new AOI scene, rebuild products
  nohup setsid nice -n 10 python scripts/30_live_pass.py --watch >> data/cache/live/watch.log 2>&1 &
  python scripts/30_live_pass.py --scene S1D_IW_GRDH_1SDV_2026...   one named scene (add --force to ignore the AIS test)
  python scripts/30_live_pass.py --dry-run                   list AOI scenes on the mirror and their AIS coverage
  python scripts/30_live_pass.py --rebuild                   rewrite data/live/ from the checkpoints only
  python scripts/30_live_pass.py --rematch [RUN_ID ...]      redo AIS pairing, status and identity from the stored objects
                                                             (no detection) with the AIS recorded now, then rebuild
  python scripts/30_live_pass.py --weather [--force]         GFS wind and Himawari cloud tops for every processed pass
  python scripts/30_live_pass.py --review RUN_ID             AIS evidence per matched and unmatched contact for the hand
                                                             check: data/live/<run_id>_review_{matched,unmatched}.csv
  python scripts/30_live_pass.py --figures RUN_ID [--zoom W S E N] [--map-png P] [--matches-png P] [--ais-only-png P]
                                                             map, radar chips of the matches, AIS-only and quality panels
  python scripts/30_live_pass.py --stop                      SIGTERM to the watcher in data/cache/live/watch.pid
Options: --since 2026-10-08T14:30Z, --poll-minutes 10, --workers 2, --io-threads 4, --no-cnn, --no-persistence, --no-weather, --pfa 1e-6,
         --wait (block until the watcher's current cycle ends instead of exiting when it holds the lock)

One writer at a time: every mode that writes (--once, --scene, --watch, --rebuild, --rematch, --weather) holds
data/cache/live/cycle.lock, so one of them beside the detached watcher exits with a message (exit code 2) unless --wait
is given (--rematch always waits); --dry-run, --review and --figures only read (--review writes its two CSV tables).
Rerunning is a no-op once every scene has a checkpoint: the products are rewritten only when a scene was processed or
the summary file is missing, and complete weather sidecars are not fetched again.

"Dark" never means illegal (darkvessel.config.DARK_CAVEAT). Terrestrial AIS is silent over most of the open sea;
a contact there is no_coverage, not dark. An AIS gap is not proof of intent.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

os.environ.setdefault("OMP_NUM_THREADS", "2")  # numpy, scipy and torch share four cores with other jobs
os.environ.setdefault("MKL_NUM_THREADS", "2")
# GDAL's own error text (for example "TIFFFillTile: Read error ... got 0 bytes") reaches the log through rasterio's logger
logging.basicConfig(level=logging.ERROR, stream=sys.stdout, format="%(asctime)s [gdal] %(name)s %(levelname)s %(message)s")

import pandas as pd  # noqa: E402

import darkvessel  # noqa: E402,F401  (PROJ_DATA before rasterio and pyogrio)
from darkvessel.live import watch as lw  # noqa: E402
from darkvessel.live.schema import LIVE_CACHE, WATCH_PID  # noqa: E402


def stop_watcher() -> int:
    try:
        pid = int(WATCH_PID.read_text().strip())
    except (OSError, ValueError):
        print("no watcher pid file")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"sent SIGTERM to {pid}")
        return 0
    except ProcessLookupError:
        print(f"pid {pid} is not running; removing the pid file")
        WATCH_PID.unlink(missing_ok=True)
        return 1


def _pass_inputs(run_id: str, ctx, ckpt):
    """Everything --review and --figures read for one pass: contacts (with the imaging geometry of the stored objects),
    AIS-only, scenes, the scene records, the AIS of the pass window, the latest static messages and the gear beacons."""
    import pyogrio

    from darkvessel.ais import aisstream
    from darkvessel.live import matching
    from darkvessel.live.rules import ship_stations

    path = ctx.out_dir / f"{run_id}.gpkg"
    contacts = pyogrio.read_dataframe(path, layer="contacts_4326", read_geometry=False)
    ais_only = pyogrio.read_dataframe(path, layer="ais_only_4326", read_geometry=False)
    scenes = pyogrio.read_dataframe(path, layer="scenes_4326")
    recs = {pid: ckpt.status(pid) for pid in scenes.product_id}
    geo = []
    for pid in scenes.product_id:
        o = ckpt.objects(pid)
        if o is not None:
            geo.append(o[[c for c in ("det_id", "slant_range_m", "az_e", "az_n", "rg_e", "rg_n", "inc_angle_deg") if c in o]])
    if geo:
        g = pd.concat(geo, ignore_index=True).drop_duplicates("det_id")
        contacts = contacts.merge(g.drop(columns=[c for c in ("inc_angle_deg",) if c in contacts]), on="det_id", how="left")
    t = pd.to_datetime(scenes.scene_time_utc, utc=True)
    half = pd.Timedelta(minutes=30)
    ais = ship_stations(aisstream.load_positions(start=t.min() - half, end=t.max() + half))
    static = aisstream.latest_static(aisstream.load_static())
    gear = matching.gear_mmsi(ais, static)
    return contacts, ais_only, scenes, recs, ais, static, gear


def review_or_figures(a, ctx, ckpt) -> int:
    from darkvessel.config import DOCS_DIR
    from darkvessel.live import figures, review

    run_id = a.review or a.figures
    contacts, ais_only, scenes, recs, ais, static, gear = _pass_inputs(run_id, ctx, ckpt)
    times = dict(zip(scenes.product_id, pd.to_datetime(scenes.scene_time_utc, utc=True)))
    speeds = {pid: (r or {}).get("sat_speed_ms") for pid, r in recs.items()}
    if a.review:
        mpath, upath = review.review_paths(run_id, ctx.out_dir)
        me = review.matched_evidence(contacts, ais, static, scene_times=times, sat_speed=speeds, gear=gear)
        parts = [review.unmatched_evidence(g, ais, ais_only[ais_only.scene_id == sid], times[sid], sat_speed_ms=speeds.get(sid),
                                           gear=gear, static_latest=static)
                 for sid, g in contacts.groupby("scene_id") if sid in times]
        ue = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else pd.DataFrame()
        if len(me):
            me["sample"] = "all_matched"
        if len(ue):
            ue["sample"] = review.pick_samples(ue).to_numpy()
        me = review.keep_hand_check(me, review.read_review(mpath), key=("det_id", "mmsi"))
        ue = review.keep_hand_check(ue, review.read_review(upath), key=("det_id", "ais_status"))
        for df, out in ((me, mpath), (ue, upath)):
            tmp = out.with_name(f"{out.stem}.{os.getpid()}.tmp.csv")
            df.to_csv(tmp, index=False)
            os.replace(tmp, out)
            graded = int(df.grade.isin(review.HAND_GRADES).sum()) if len(df) else 0
            print(f"wrote {out} ({len(df)} rows, {graded} hand-graded)")
        if len(me):
            print(me.check.value_counts().to_string())
        return 0
    figs = DOCS_DIR / "figures"
    title = f"{run_id}: Sentinel-1 contacts and live AIS (aisstream)"
    mp = figures.pass_map(contacts, scenes, ais[~ais.mmsi.astype("int64").isin(gear)], a.map_png or figs / f"live_pass_{run_id}.png", title,
                          zoom=tuple(a.zoom) if a.zoom else None, ais_only=ais_only, tested=_tested_areas(ckpt, scenes.product_id))
    paths = {pid: (r or {}).get("path") for pid, r in recs.items()}
    cp = figures.chip_panels(contacts, ais[~ais.mmsi.astype("int64").isin(gear)], static, paths, times, speeds,
                             a.matches_png or figs / f"live_pass_{run_id}_matches.png", f"{run_id}: matched contacts, radar chips and AIS",
                             review_table=review.read_review(review.review_paths(run_id, ctx.out_dir)[0]))
    ap = figures.ais_only_panel(ais_only, contacts, a.ais_only_png or figs / f"live_pass_{run_id}_ais_only.png",
                                f"{run_id}: AIS vessels on tested sea and the radar")
    print(f"wrote {mp}, {cp} and {ap}")
    if a.quality_png:
        print(f"wrote {figures.match_panels(contacts, a.quality_png, f'{run_id}: match quality')}")
    return 0


def _tested_areas(ckpt, product_ids):
    """Union of the scenes' tested-sea polygons (checkpoint <id>.tested.wkb), or None when none is stored."""
    import shapely

    polys = [g for g in (ckpt.tested(pid) for pid in product_ids) if g is not None]
    return shapely.union_all(polys) if polys else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one cycle and exit")
    mode.add_argument("--watch", action="store_true", help="poll every --poll-minutes until stopped")
    mode.add_argument("--scene", nargs="+", metavar="PRODUCT_ID", help="process these product ids only")
    mode.add_argument("--dry-run", action="store_true", help="list AOI scenes and their AIS coverage, process nothing")
    mode.add_argument("--rebuild", action="store_true", help="rewrite data/live/ from the checkpoints")
    mode.add_argument("--stop", action="store_true", help="stop the detached watcher")
    mode.add_argument("--rematch", nargs="*", metavar="RUN_ID", help="redo the AIS pairing of processed scenes (all, or these passes)")
    mode.add_argument("--weather", action="store_true", help="build or refresh the weather sidecars of every processed pass")
    mode.add_argument("--review", metavar="RUN_ID", help="write the hand-check evidence tables of one pass")
    mode.add_argument("--figures", metavar="RUN_ID", help="draw the map and match panels of one pass")
    ap.add_argument("--zoom", nargs=4, type=float, metavar=("W", "S", "E", "N"), help="with --figures: the zoom panel's extent")
    ap.add_argument("--map-png", help="with --figures: map output path (default docs/figures/live_pass_<run_id>.png)")
    ap.add_argument("--matches-png", help="with --figures: chip panels output path (default docs/figures/live_pass_<run_id>_matches.png)")
    ap.add_argument("--ais-only-png", help="with --figures: AIS-only panel output path (default docs/figures/live_pass_<run_id>_ais_only.png)")
    ap.add_argument("--quality-png", help="with --figures: also draw the match quality panels to this path")
    ap.add_argument("--since", default=str(lw.SINCE_DEFAULT.isoformat()), help="earliest scene start (UTC) to consider")
    ap.add_argument("--poll-minutes", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=2, help="CPU threads for CNN chips and persistence (at most 2 on this host)")
    ap.add_argument("--io-threads", type=int, default=4, help="parallel COG tile fetches per scene")
    ap.add_argument("--pfa", type=float, default=1e-6)
    ap.add_argument("--no-cnn", action="store_true")
    ap.add_argument("--no-persistence", action="store_true")
    ap.add_argument("--no-weather", action="store_true", help="do not fetch GFS wind and Himawari cloud tops after a cycle")
    ap.add_argument("--force", action="store_true", help="with --scene: process even when no AIS was recorded in the window; "
                                                          "with --weather: rebuild every sidecar")
    ap.add_argument("--wait", action="store_true", help="with --once, --scene, --rebuild or --weather: wait for the cycle lock instead of exiting")
    ap.add_argument("--nice", type=int, default=10, help="run at least this nice (0 = leave the priority alone)")
    a = ap.parse_args()
    if a.stop:
        return stop_watcher()
    if a.nice:
        try:  # raise the niceness to at least --nice (a launcher's own `nice -n 10` is not added to)
            cur = os.nice(0)
            if cur < a.nice:
                os.nice(a.nice - cur)
        except OSError:
            pass
    since = pd.Timestamp(a.since)
    since = since.tz_localize("UTC") if since.tzinfo is None else since.tz_convert("UTC")
    LIVE_CACHE.mkdir(parents=True, exist_ok=True)
    ctx = lw.Context(workers=min(max(a.workers, 1), 2), do_cnn=not a.no_cnn, do_persistence=not a.no_persistence, pfa=a.pfa,
                     io_threads=max(1, a.io_threads), do_weather=not a.no_weather)
    ckpt = lw.Checkpoint()
    if a.rebuild or a.weather:
        try:
            with lw.CycleLock(ctx.lock_path, blocking=a.wait):
                if a.rebuild:
                    lw.rebuild_outputs(ctx, ckpt, since)
                else:
                    print(lw.update_weather(ctx, ckpt, force=a.force))
        except BlockingIOError:
            lw.log(f"another live-pass process holds {ctx.lock_path} (the watcher?); nothing done (use --wait)")
            return 2
        return 0
    if a.rematch is not None:
        n = lw.rematch(ctx, ckpt, since, run_ids=a.rematch or None)
        lw.log(f"rematched {n} scenes")
        if n and ctx.do_weather:
            with lw.CycleLock(ctx.lock_path, blocking=True):
                lw.update_weather(ctx, ckpt)
        return 0
    if a.review or a.figures:
        return review_or_figures(a, ctx, ckpt)
    if a.dry_run:
        lw.cycle(ctx, ckpt, since, dry_run=True, blocking=a.wait)
        return 0
    if a.watch:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        lw.log(f"watcher started, pid {os.getpid()}, poll every {a.poll_minutes:g} min, since {since:%Y-%m-%d %H:%M} UTC, "
               f"{ctx.workers} CPU threads, {ctx.io_threads} I/O threads")
        lw.watch(ctx, ckpt, since, poll_minutes=a.poll_minutes, pid_path=WATCH_PID)
        return 0
    if a.scene:
        res = lw.cycle(ctx, ckpt, since, scene_ids=a.scene, force=a.force, blocking=a.wait)
    else:
        res = lw.cycle(ctx, ckpt, since, blocking=a.wait)  # --once and the default
    return 2 if res.get("locked") else 0


if __name__ == "__main__":
    sys.exit(main())
