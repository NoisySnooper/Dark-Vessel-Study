#!/usr/bin/env bash
# Session start: bring back the long-running processes that a container restart kills.
# 1. The regional CNN run (scripts/32_cnn_regional.py), only while a scene has no checkpoint: --if-incomplete makes it
#    a no-op when the run is complete. A run.pid whose pid is no longer a CNN run (dead, or reused by another program
#    after the restart) is removed first, because script 32 checks only that the pid exists. Skipped when python is
#    running 32_cnn_regional.py (a run, or a resume the watchdog started). The resume command runs in the
#    background: on a cold disk after a restart it needs about 50 s to list the scenes (2026-10-10), so the hook does
#    not wait for it; `timeout 600` bounds it, and the watchdog waits for it instead of racing it.
# 2. The live-pass watcher (detects, verifies and AIS-matches new Sentinel-1 scenes over the AOI).
# 3. The watchdog: it restarts the aisstream recorder, refreshes the Sentinel-1 pass plan and from then on keeps 1 and
#    2 alive. It starts last and adopts what 1 and 2 started, so nothing runs twice.
# Safe to run any number of times: each process is started only if it is not already running. Under 20 s on a cold
# container, about 1 s otherwise; the hook timeout is 60 s. Needs AISSTREAM_API_KEY in the git-ignored .env;
# does nothing without the project environment. Output goes to data/cache/ais/aisstream/watchdog.log.
cd "$(dirname "$0")/.." || exit 0
PY=/home/user/.mamba/envs/darkvessel/bin/python
[ -x "$PY" ] && [ -f .env ] || exit 0
mkdir -p data/cache/ais/aisstream data/cache/live data/cache/regional_cnn
LOG=data/cache/ais/aisstream/watchdog.log
echo "$(date -u '+%Y-%m-%d %H:%M:%S')Z [session_start] CNN resume, live watcher, watchdog" >> "$LOG"
if ! pgrep -f 'python[0-9.]* [^ ]*scripts/32_cnn_regional[.]py' > /dev/null; then
  PIDF=data/cache/regional_cnn/run.pid
  if [ -f "$PIDF" ] && ! tr '\0' ' ' < "/proc/$(tr -dc 0-9 < "$PIDF")/cmdline" 2> /dev/null | grep -q "32_cnn_regional.py"; then
    rm -f "$PIDF"
  fi
  nohup setsid timeout 600 nice -n 19 "$PY" scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low \
    >> "$LOG" 2>&1 < /dev/null &
fi
if ! pgrep -f "scripts/30_live_pass.py --watch" > /dev/null; then
  nohup setsid nice -n 10 "$PY" scripts/30_live_pass.py --watch >> data/cache/live/watch.log 2>&1 < /dev/null &
fi
timeout 45 "$PY" scripts/29_ais_watchdog.py --ensure >> "$LOG" 2>&1 < /dev/null
exit 0
