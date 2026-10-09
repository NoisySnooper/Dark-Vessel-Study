"""Record live AIS from aisstream.io over the South China Sea AOI into hourly parquet partitions.

Purpose: the live AIS feed is the truth that defines "dark" (a radar contact with no AIS match) during the
Sentinel-1 passes predicted by scripts/28_ais_reach.py. The recorder runs unattended for days.

Method: one websocket connection to wss://stream.aisstream.io/v0/stream (darkvessel.ais.aisstream), three
lat/lon boxes that hug the AOI, retry forever with capped exponential backoff (1 s to 60 s, jitter), flush every
60 s. Position reports become data/cache/ais/aisstream/positions/YYYYMMDD/HH.parquet, static reports
data/cache/ais/aisstream/static/YYYYMMDD.parquet, and every frame is kept raw in
data/cache/ais/aisstream/raw/YYYYMMDD/HH.jsonl.gz. status.json is written at start and at every flush; the
watchdog (scripts/29_ais_watchdog.py) reads it. Messages stamped more than 1 h from the local clock stay in the
raw log only. Only one recorder runs: a live pid in recorder.pid whose command line is this script blocks a
second start (a stale pid left by a container restart does not).

Inputs:  AISSTREAM_API_KEY in the git-ignored .env at the repo root (loaded with python-dotenv; never printed or
         logged; every log line and status field is passed through aisstream.redact).
Output:  the partitions above, status.json, recorder.pid (data/cache/ais/aisstream/).
Usage:
  python scripts/29_ais_watchdog.py --ensure    # preferred: the watchdog starts and restarts the recorder
  nohup setsid python scripts/26_ais_record.py --hours 0 >> data/cache/ais/aisstream/record.log 2>&1 &
  python scripts/26_ais_record.py --status      # messages per hour, distinct MMSI, last write
  python scripts/26_ais_record.py --stop        # SIGTERM to the pid in recorder.pid, waits for the final flush
  python scripts/26_ais_record.py --hours 2     # fixed-length run (0 = until stopped)
Options: --flush-seconds 60, --no-raw (skip the gzip raw log), --idle-timeout 120.
Exit codes: 0 stopped normally, 2 no key, 3 another recorder is running, 4 the service rejected the key.

"Dark" never means illegal; see darkvessel.config.DARK_CAVEAT. The feed hears what shore receivers hear, so
silence at sea is not evidence of an empty sea.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from darkvessel.ais import aisstream as ais
from darkvessel.config import REPO_ROOT

ROOT = ais.AIS_CACHE
SCRIPT_NAME = "26_ais_record.py"


def log(msg: str) -> None:
    print(f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M:%S}Z {ais.redact(msg)}", flush=True)


def is_recorder_pid(pid: int) -> bool:
    """True when `pid` is a live (not zombie) process running this script in recording mode."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False
    if stat.rsplit(")", 1)[-1].split()[0] in ("Z", "X"):
        return False
    args = [c.decode("utf-8", "replace") for c in cmd if c]
    return any(a.endswith(SCRIPT_NAME) for a in args) and not ({"--status", "--stop"} & set(args))


def read_pid() -> int | None:
    """The pid in recorder.pid if it is a live recorder; None for a missing file, a dead pid or a reused pid."""
    try:
        pid = int(ais.PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if is_recorder_pid(pid) else None


def status() -> int:
    """Print what has been recorded: per-hour message counts, distinct MMSI, last write, process state."""
    pid = read_pid()
    print(f"recorder: {'running, pid ' + str(pid) if pid else 'not running'}")
    if ais.STATUS_PATH.exists():
        s = json.loads(ais.STATUS_PATH.read_text())
        print(f"started {s.get('started_utc')}  updated {s.get('updated_utc')}")
        print(f"messages {s.get('messages')}  positions {s.get('positions')}  static {s.get('static')}  other {s.get('other')}  "
              f"dropped {s.get('dropped')}  connects {s.get('connects')}  errors {s.get('errors')}")
        print(f"last message {s.get('last_message_utc')}  last write {s.get('last_write_utc')}")
        if s.get("last_error"):
            print(f"last error: {s['last_error']}")
        if s.get("by_type"):
            print("by type: " + ", ".join(f"{k} {v}" for k, v in sorted(s["by_type"].items(), key=lambda kv: -kv[1])))
    parts = ais.list_partitions(ROOT)
    if not parts:
        print("no position partitions yet")
        return 0
    rows = []
    for p in parts:
        try:
            df = pd.read_parquet(p, columns=["mmsi", "ais_class"])
        except Exception as exc:
            rows.append((p.parent.name, p.stem, None, None, None, type(exc).__name__))
            continue
        rows.append((p.parent.name, p.stem, len(df), df.mmsi.nunique(), int((df.ais_class == "B").sum()), ""))
    tab = pd.DataFrame(rows, columns=["day", "hour", "positions", "mmsi", "class_b_positions", "note"])
    print(tab.to_string(index=False))
    total = ais.load_positions(ROOT)
    print(f"total positions {len(total)}, distinct MMSI {total.mmsi.nunique()}, hours {len(parts)}, "
          f"period {total.timestamp.min()} to {total.timestamp.max()}")
    st = ais.load_static(ROOT)
    print(f"static rows {len(st)}, MMSI with static {st.mmsi.nunique() if len(st) else 0}")
    return 0


def stop() -> int:
    """SIGTERM the recorder in recorder.pid and wait for that pid to exit (SIGKILL after 30 s). If the watchdog is
    running it starts a new recorder within about a minute; stop the watchdog first to stop recording."""
    pid = read_pid()
    if not pid:
        print("recorder is not running (no live pid in recorder.pid)")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 0
    for _ in range(60):
        time.sleep(0.5)
        if not is_recorder_pid(pid):
            print(f"stopped pid {pid}")
            return 0
    print(f"pid {pid} still alive after 30 s; sending SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return 0


def run(hours: float, flush_s: float, keep_raw: bool, idle_timeout: float) -> int:
    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not key:
        print("AISSTREAM_API_KEY is not set (expected in .env at the repo root)", file=sys.stderr)
        return 2
    ais.register_secret(key)
    ROOT.mkdir(parents=True, exist_ok=True)
    other = read_pid()
    if other and other != os.getpid():
        print(f"another recorder is running (pid {other}); aisstream allows few connections per account. "
              f"Use --stop first.", file=sys.stderr)
        return 3
    ais.PID_PATH.write_text(str(os.getpid()))
    rec = ais.Recorder(ROOT, keep_raw=keep_raw, max_clock_skew=ais.MAX_CLOCK_SKEW)
    rec.write_status()  # the watchdog judges a fresh recorder by started_utc until the first message arrives
    stop_event = asyncio.Event()

    async def main():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, lambda s=sig: (log(f"signal {s.name}; flushing and stopping"), stop_event.set()))
        log(f"recording {'until stopped' if not hours else f'for {hours} h'}; boxes {len(ais.AOI_BOXES)}; "
            f"flush every {flush_s:.0f} s; raw {'on' if keep_raw else 'off'}")
        await ais.record(key, rec, hours=hours, flush_s=flush_s, stop=stop_event, log=log, idle_timeout_s=idle_timeout)

    try:
        asyncio.run(main())
    finally:
        s = rec.stats
        log(f"stopped: {s['messages']} messages, {s['positions']} positions, {s['static']} static, "
            f"{s['distinct_mmsi']} MMSI, {s['connects']} connects, {s['errors']} service errors")
        try:
            if ais.PID_PATH.exists() and ais.PID_PATH.read_text().strip() == str(os.getpid()):
                ais.PID_PATH.unlink()
        except OSError:
            pass
    return 4 if rec.stats.get("connection") == "stopped" and ais.is_key_rejection(rec.stats.get("last_error") or "") else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hours", type=float, default=0, help="run for N hours; 0 = until --stop or a signal")
    ap.add_argument("--status", action="store_true", help="report what has been recorded and exit")
    ap.add_argument("--stop", action="store_true", help="stop the running recorder (pid file) and exit")
    ap.add_argument("--flush-seconds", type=float, default=60.0)
    ap.add_argument("--idle-timeout", type=float, default=120.0, help="reconnect after this many silent seconds")
    ap.add_argument("--no-raw", action="store_true", help="do not keep the gzip raw JSON log")
    args = ap.parse_args()
    if args.status:
        return status()
    if args.stop:
        return stop()
    return run(args.hours, args.flush_seconds, not args.no_raw, args.idle_timeout)


if __name__ == "__main__":
    sys.exit(main())
