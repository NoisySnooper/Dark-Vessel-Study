#!/usr/bin/env bash
# Session start: bring back the long-running AIS processes that a container restart kills.
# 1. The AIS watchdog (it restarts the aisstream recorder and refreshes the Sentinel-1 pass plan).
# 2. The live-pass watcher (detects, verifies and AIS-matches new Sentinel-1 scenes over the AOI).
# Safe to run any number of times: each process is started only if it is not already running.
# Needs AISSTREAM_API_KEY in the git-ignored .env; does nothing without the project environment.
cd "$(dirname "$0")/.." || exit 0
PY=/home/user/.mamba/envs/darkvessel/bin/python
[ -x "$PY" ] && [ -f .env ] || exit 0
mkdir -p data/cache/ais/aisstream data/cache/live
"$PY" scripts/29_ais_watchdog.py --ensure >> data/cache/ais/aisstream/watchdog.log 2>&1
if ! pgrep -f "scripts/30_live_pass.py --watch" > /dev/null; then
  nohup setsid nice -n 10 "$PY" scripts/30_live_pass.py --watch >> data/cache/live/watch.log 2>&1 < /dev/null &
fi
exit 0
