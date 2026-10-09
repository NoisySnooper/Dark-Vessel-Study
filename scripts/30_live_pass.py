"""Live Sentinel-1 passes over the South China Sea AOI: detect, verify, match to recorded AIS, identify.

Purpose: real dark-vessel detection AND identification in the open build. For every Sentinel-1C/1D IW scene that
lands on the AWS mirror while the aisstream recorder (scripts/26_ais_record.py) is running, write every radar contact
with an AIS status: matched (with the vessel's identity), unmatched (a dark lead with its evidence) or no_coverage
(the live feed heard nothing there, so nothing can be said).

Method (darkvessel.live): poll the mirror every 10 min for AOI scenes since --since; skip scenes with no AIS in the
plus or minus 30 min window; detect with the regional CA-CFAR settings, persistence, clutter and near-fixed rules;
score every vessel candidate with the CNN verifier (chips read remotely, no scene download); place every AIS vessel
at the scene time (track interpolation or dead reckoning), speed-aware distance gate (500 m + 125 s x SOG, at most
2 km, for the Sentinel-1 azimuth shift of moving targets), Hungarian one-to-one assignment, match_quality from
distance, time and length agreement; identity from aisstream static messages and the MMSI's MID (ITU table);
AIS status from whether the feed heard anything in the contact's 0.25 degree cell or within 20 km. Checkpointed per
scene; a processed scene is never redone and a crash resumes.

Inputs:  AWS mirror sentinel-s1-l1c (productInfo.json, manifest.safe, COG tiles), data/s1_next_passes.json (pass
         naming, re-read each cycle), data/cache/ais/aisstream/ (positions, static), data/models/verifier_v0.pt,
         data/s1_footprints.gpkg (persistence archive), data/outputs/small/dist_coast_km_4326.tif (optional, AIS-only
         shore test), data/aoi.gpkg via darkvessel.aoi
Output:  data/live/live_<mission>_<yyyymmddThhmm>.gpkg per pass (contacts_*, ais_only_*, scenes_*, about),
         data/live/live_contacts.gpkg (all passes), data/live/live_summary.json;
         checkpoints and caches in data/cache/live/ (scenes/, products/, passes.json, watch.pid, watch.log)
Usage:
  python scripts/30_live_pass.py --once                      one cycle: process every new AOI scene, rebuild products
  nohup setsid nice -n 10 python scripts/30_live_pass.py --watch >> data/cache/live/watch.log 2>&1 &
  python scripts/30_live_pass.py --scene S1D_IW_GRDH_1SDV_2026...   one named scene (add --force to ignore the AIS test)
  python scripts/30_live_pass.py --dry-run                   list AOI scenes on the mirror and their AIS coverage
  python scripts/30_live_pass.py --rebuild                   rewrite data/live/ from the checkpoints only
  python scripts/30_live_pass.py --stop                      SIGTERM to the watcher in data/cache/live/watch.pid
Options: --since 2026-10-08T14:30Z, --poll-minutes 10, --workers 2, --io-threads 4, --no-cnn, --no-persistence, --pfa 1e-6,
         --wait (block until the watcher's current cycle ends instead of exiting when it holds the lock)

One cycle at a time: every mode but --dry-run and --rebuild holds data/cache/live/cycle.lock, so `--once` or `--scene`
beside the detached watcher exits with a message (exit code 2) unless --wait is given. Rerunning is a no-op once every
scene has a checkpoint: the products are rewritten only when a scene was processed or the summary file is missing.

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one cycle and exit")
    mode.add_argument("--watch", action="store_true", help="poll every --poll-minutes until stopped")
    mode.add_argument("--scene", nargs="+", metavar="PRODUCT_ID", help="process these product ids only")
    mode.add_argument("--dry-run", action="store_true", help="list AOI scenes and their AIS coverage, process nothing")
    mode.add_argument("--rebuild", action="store_true", help="rewrite data/live/ from the checkpoints")
    mode.add_argument("--stop", action="store_true", help="stop the detached watcher")
    ap.add_argument("--since", default=str(lw.SINCE_DEFAULT.isoformat()), help="earliest scene start (UTC) to consider")
    ap.add_argument("--poll-minutes", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=2, help="CPU threads for CNN chips and persistence (at most 2 on this host)")
    ap.add_argument("--io-threads", type=int, default=4, help="parallel COG tile fetches per scene")
    ap.add_argument("--pfa", type=float, default=1e-6)
    ap.add_argument("--no-cnn", action="store_true")
    ap.add_argument("--no-persistence", action="store_true")
    ap.add_argument("--force", action="store_true", help="with --scene: process even when no AIS was recorded in the window")
    ap.add_argument("--wait", action="store_true", help="with --once or --scene: wait for the cycle lock instead of exiting")
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
                     io_threads=max(1, a.io_threads))
    ckpt = lw.Checkpoint()
    if a.rebuild:
        lw.rebuild_outputs(ctx, ckpt, since)
        return 0
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
