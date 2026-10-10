"""Keep the long-running processes alive across proxy drops, crashes, hangs and container restarts: the live AIS
recorder (scripts/26_ais_record.py), the live-pass watcher (scripts/30_live_pass.py --watch) and the regional CNN run
(scripts/32_cnn_regional.py) while it is incomplete.

Purpose: a Sentinel-1 pass can be identified against live AIS only if AIS was recorded while the satellite looked and
the watcher picks the scene up from the mirror. The recorder died once (2026-10-08 15:20 UTC, the container went down)
and nothing restarted it for 7 hours; the container went down again six times on 2026-10-09. This watchdog checks
every 60 s and restarts what is dead, without ever running two copies of anything.

Method, recorder: each check (1) lists recorder processes from /proc (command line ends in 26_ais_record.py, not
--status or --stop, not a zombie) and reads recorder.pid and status.json; (2) adopts the recorder in recorder.pid, or
the one that wrote status.json, and SIGTERMs any other recorder; (3) calls the recorder healthy when its pid is alive
and status.json, written by that pid, has last_message_utc (started_utc before the first message) less than 5 min
old; a recorder this watchdog started gets a 3 min grace before status.json must be its own; (4) when unhealthy, and
the restart backoff allows, SIGTERMs the stale process (SIGKILL after 20 s), starts a new recorder with the same
command line (new session, stdin /dev/null, output appended to record.log: the nohup setsid equivalent) and writes
the new pid to recorder.pid. Consecutive restarts wait 1, 2, 5, then 10 min after the previous one; 15 min of health
resets the streak. A healthy recorder is never restarted. Every 6 h (age of generated_utc in
data/s1_next_passes.json) it refreshes the Sentinel-1 pass plan in the background with
`nice -n 10 python scripts/28_ais_reach.py --passes-only --fetch-plan`; a failed refresh is retried after 1 h.
Live watcher (on unless --no-live-watcher): watcher processes are found in /proc the same way (30_live_pass.py and
--watch on the command line), so a pid in data/cache/live/watch.pid that now belongs to another program does not
count; the one in watch.pid is kept (watch.pid is corrected when it is wrong) and any second watcher is SIGTERMed.
With none it starts `nohup nice -n 10 python scripts/30_live_pass.py --watch` (new session, stdin /dev/null, output
appended to data/cache/live/watch.log, as scripts/session_start.sh does) and writes watch.pid. Liveness only: one
cycle can take an hour, so a quiet watcher is never restarted. Restart backoff as for the recorder.
CNN run (on unless --no-cnn-resume): run processes are an argument ending in 32_cnn_regional.py without --status,
--stop, --build or --detach. While a resume command (the same with --detach) that this watchdog did not start is
running, less than 10 min old (the session hook starts one; on a cold disk it takes about 50 s), the watchdog waits
for it. With no run in two checks in a row (the run re-executes itself in place to shed memory)
a run.pid that is dead, a zombie or another program is removed, because script 32 tests only that the pid exists,
and `nice -n 19 python scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low` runs in the background;
script 32 decides whether a scene is left, and its answer "nothing to start" marks the run complete for the life of
this watchdog. Resumes are at least 30 min apart; after a resume
whose run ended without a new scene checkpoint the gap doubles (60, 120, at most 240 min).
An exclusive flock on watchdog.lock keeps a second watchdog out. A container restart kills the watchdog too: run
`--ensure` at session start (idempotent; scripts/session_start.sh does).

Inputs:  data/cache/ais/aisstream/recorder.pid, status.json (written by the recorder), data/s1_next_passes.json,
         data/cache/live/watch.pid, data/cache/regional_cnn/run.pid and its main/ and low/ scene checkpoints
Output:  data/cache/ais/aisstream/watchdog.pid, watchdog.lock, watchdog.log, watchdog_status.json (checks, restarts,
         streaks, last decisions, history; sections watcher and cnn), cnn_resume.out (answer of the last CNN resume);
         recorder.pid, watch.pid and run.pid when it starts or adopts a process
Usage:
  python scripts/29_ais_watchdog.py --ensure     # start detached unless a watchdog holds the lock (session start)
  nohup setsid python scripts/29_ais_watchdog.py --run >> data/cache/ais/aisstream/watchdog.log 2>&1 &
  python scripts/29_ais_watchdog.py --status     # watchdog, recorder, live watcher and CNN run
  python scripts/29_ais_watchdog.py --stop       # stop the watchdog only; what it supervises keeps running
  python scripts/29_ais_watchdog.py --once       # one check in the foreground, then exit
Options: --interval 60, --stale-minutes 5, --refresh-passes-hours 6 (0 = never), --no-live-watcher, --no-cnn-resume.

The recorder hears what aisstream.io shore receivers hear. "Dark" never means illegal (darkvessel.config.DARK_CAVEAT).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

AIS_DIR = REPO_ROOT / "data" / "cache" / "ais" / "aisstream"
RECORDER_SCRIPT = "26_ais_record.py"
DEFAULT_CMD = [sys.executable, f"scripts/{RECORDER_SCRIPT}", "--hours", "0"]
PASSES_JSON = REPO_ROOT / "data" / "s1_next_passes.json"
PASS_REFRESH_CMD = ["nice", "-n", "10", sys.executable, "scripts/28_ais_reach.py", "--passes-only", "--fetch-plan"]

LIVE_DIR = REPO_ROOT / "data" / "cache" / "live"
WATCHER_SCRIPT = "30_live_pass.py"
WATCHER_CMD = ["nohup", "nice", "-n", "10", sys.executable, f"scripts/{WATCHER_SCRIPT}", "--watch"]
CNN_DIR = REPO_ROOT / "data" / "cache" / "regional_cnn"
CNN_SCRIPT = "32_cnn_regional.py"
CNN_CMD = ["nice", "-n", "19", sys.executable, f"scripts/{CNN_SCRIPT}", "--detach", "--if-incomplete", "--phases",
           "main,low"]
CNN_PHASES = ("main", "low")
CNN_NOT_A_RUN = {"--status", "--stop", "--build", "--detach", "--help", "-h"}

BACKOFF_S = (60, 120, 300, 600)  # wait after the previous restart before restart 2, 3, 4, 5 and later of a streak
STALE_S = 300
GRACE_S = 180
STREAK_RESET_S = 900
TERM_WAIT_S = 20
REFRESH_RETRY_S = 3600
CNN_GAP_S = (1800, 14400)  # CNN resumes at least 30 min apart, doubled per resume without progress, at most 4 h
CNN_LAUNCH_TIMEOUT_S = 600  # the resume command takes about 10 s; one still running after 10 min is killed


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _ts(v) -> pd.Timestamp | None:
    if not v:
        return None
    try:
        t = pd.Timestamp(v)
    except (ValueError, TypeError):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t: pd.Timestamp | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------
# Decision logic (pure, tested offline)
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class Health:
    healthy: bool
    reason: str
    pid: int | None = None
    last_activity_utc: pd.Timestamp | None = None
    age_s: float | None = None


def assess(pid: int | None, alive: bool, status: dict | None, now: pd.Timestamp, *, stale_s: float = STALE_S,
           grace_s: float = GRACE_S, started_here_utc: pd.Timestamp | None = None) -> Health:
    """Is the recorder `pid` healthy? `status` is status.json (or None); `started_here_utc` is when this watchdog
    started that pid (None for an adopted recorder)."""
    if pid is None:
        return Health(False, "no recorder process")
    if not alive:
        return Health(False, f"pid {pid} is not a live recorder", pid)
    in_grace = started_here_utc is not None and (now - started_here_utc).total_seconds() < grace_s
    if status is None:
        return Health(True, "starting (no status.json yet)", pid) if in_grace else Health(False, "no status.json", pid)
    spid = status.get("pid")
    if spid != pid:
        if in_grace:
            return Health(True, f"starting (status.json still from pid {spid})", pid)
        return Health(False, f"status.json written by pid {spid}, not by the recorder pid {pid}", pid)
    last = _ts(status.get("last_message_utc")) or _ts(status.get("started_utc"))
    if last is None:
        return (Health(True, "starting (no message yet)", pid) if in_grace
                else Health(False, "status.json has no last_message_utc or started_utc", pid))
    age = (now - last).total_seconds()
    what = "last message" if status.get("last_message_utc") else "started, no message yet,"
    if age > stale_s:
        return Health(False, f"stale: {what} {age:.0f} s ago (limit {stale_s:.0f} s)", pid, last, age)
    return Health(True, f"ok: {what} {age:.0f} s ago", pid, last, age)


def restart_wait_s(now: pd.Timestamp, last_restart_utc: pd.Timestamp | None, streak: int,
                   backoff_s=BACKOFF_S) -> float:
    """Seconds still to wait before the next restart (0 = restart now). Streak 0 restarts at once; restart k + 1 of a
    streak waits backoff_s[k - 1] (capped at the last value) after restart k."""
    if streak <= 0 or last_restart_utc is None:
        return 0.0
    need = backoff_s[min(streak - 1, len(backoff_s) - 1)]
    return max(0.0, need - (now - last_restart_utc).total_seconds())


def cnn_wait_s(now: pd.Timestamp, last_launch_utc: pd.Timestamp | None, no_progress: int, gap_s=CNN_GAP_S) -> float:
    """Seconds still to wait before the next CNN resume: gap_s[0] after the last one, doubled for each resume in a
    row whose run ended without a new scene checkpoint, capped at gap_s[1]."""
    if last_launch_utc is None:
        return 0.0
    need = min(gap_s[0] * 2 ** max(0, int(no_progress)), gap_s[1])
    return max(0.0, need - (now - last_launch_utc).total_seconds())


def choose_current(procs: list[int], pid_file: int | None, status_pid: int | None) -> int | None:
    """The process to keep: the pid file's if it is running, else the one that wrote status.json, else the newest."""
    if pid_file in procs:
        return pid_file
    if status_pid in procs:
        return status_pid
    return max(procs) if procs else None


def _runs_script(args: list[str], script: str) -> bool:
    """One argument is the script's path (a shell running a command string that ends in the name does not count)."""
    return any(a.endswith(script) and " " not in a for a in args)


def is_recorder_args(args: list[str]) -> bool:
    return _runs_script(args, RECORDER_SCRIPT) and not ({"--status", "--stop"} & set(args))


def is_watcher_args(args: list[str]) -> bool:
    return _runs_script(args, WATCHER_SCRIPT) and "--watch" in args


def is_cnn_run_args(args: list[str]) -> bool:
    """A scoring run of script 32 (also while still `nohup nice ...` before the exec), not a --status, --stop, --build
    or --detach call."""
    return _runs_script(args, CNN_SCRIPT) and not (CNN_NOT_A_RUN & set(args))


def is_cnn_resume_args(args: list[str]) -> bool:
    """A resume command of script 32 (`--detach`, also under nice or timeout) still deciding whether to start a run."""
    return _runs_script(args, CNN_SCRIPT) and "--detach" in args


def parse_cnn_resume(text: str) -> tuple[str, int | None]:
    """Answer of `32_cnn_regional.py --detach --if-incomplete`: ('started', pid), ('complete', None),
    ('running', pid) or ('error', None)."""
    if m := re.search(r"started pid (\d+)", text):
        return "started", int(m.group(1))
    if "nothing to start" in text:
        return "complete", None
    if m := re.search(r"already running \(pid (\d+)\)", text):
        return "running", int(m.group(1))
    return "error", None


@dataclass
class State:
    pid: int = field(default_factory=os.getpid)
    started_utc: str = field(default_factory=lambda: _iso(now_utc()))
    checks: int = 0
    restarts: int = 0
    streak: int = 0
    last_restart_utc: str | None = None
    last_restart_reason: str | None = None
    started_here_pid: int | None = None
    started_here_utc: str | None = None
    recorder_pid: int | None = None
    recorder_cmd: list[str] | None = None
    last_check_utc: str | None = None
    last_decision: str | None = None
    healthy_since_utc: str | None = None
    extra_recorders_killed: int = 0
    pass_refresh_last_attempt_utc: str | None = None
    pass_refresh_last_result: str | None = None
    previous_watchdog: dict | None = None
    supervise: dict = field(default_factory=dict)
    watcher: dict = field(default_factory=dict)
    cnn: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)


class Ops:
    """Process effects of one supervised program: procs() -> live pids, start(cmd) -> pid (or a launch handle for
    the CNN resume), stop(pid); for the CNN run also resumes() -> [(pid, age_s)] of resume commands in progress that
    this watchdog did not start. Tests pass fakes."""

    def __init__(self, procs, start, stop, resumes=None):
        self.procs, self.start, self.stop = procs, start, stop
        self.resumes = resumes or (lambda: [])


class Launch:
    """The CNN resume command in the background; its whole output goes to one file, read once it has exited."""

    def __init__(self, cmd: list[str], out_path: Path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path, self.t0 = out_path, time.monotonic()
        with open(out_path, "wb") as out, open(os.devnull, "rb") as devnull:
            self.proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                                         start_new_session=True, close_fds=True)

    def poll(self):
        return self.proc.poll()

    def age_s(self) -> float:
        return time.monotonic() - self.t0

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass

    def output(self) -> str:
        try:
            return self.out_path.read_text(errors="replace")
        except OSError:
            return ""


class Watchdog:
    """One check per `check()` call. Process and file effects go through injectable callables (tests use fakes).
    The live watcher and the CNN run are supervised only when `watcher` and `cnn` are true (the CLI turns both on)."""

    def __init__(self, root: Path = AIS_DIR, *, stale_s: float = STALE_S, grace_s: float = GRACE_S,
                 streak_reset_s: float = STREAK_RESET_S, backoff_s=BACKOFF_S, log=print, procs_fn=None,
                 alive_fn=None, start_fn=None, stop_fn=None, cmdline_fn=None, now_fn=now_utc,
                 refresh_hours: float = 0, refresh_fn=None, passes_json: Path = PASSES_JSON,
                 watcher: bool = False, watcher_ops: Ops | None = None, live_dir: Path = LIVE_DIR,
                 cnn: bool = False, cnn_ops: Ops | None = None, cnn_dir: Path = CNN_DIR, cnn_gap_s=CNN_GAP_S,
                 cnn_confirm_checks: int = 2, scripts_dir: Path = REPO_ROOT / "scripts"):
        self.root = Path(root)
        self.stale_s, self.grace_s, self.streak_reset_s, self.backoff_s = stale_s, grace_s, streak_reset_s, backoff_s
        self.log = log
        self.procs_fn = procs_fn or recorder_processes
        self.alive_fn = alive_fn or is_recorder_pid
        self.start_fn = start_fn or self._start_recorder
        self.stop_fn = stop_fn or stop_process
        self.cmdline_fn = cmdline_fn or read_cmdline
        self.now_fn = now_fn
        self.refresh_hours = refresh_hours
        self.refresh_fn = refresh_fn or self._start_refresh
        self.passes_json = Path(passes_json)
        self.watcher, self.cnn = bool(watcher), bool(cnn)
        self.live_dir, self.cnn_dir, self.scripts_dir = Path(live_dir), Path(cnn_dir), Path(scripts_dir)
        self.cnn_gap_s, self.cnn_confirm_checks = cnn_gap_s, max(1, int(cnn_confirm_checks))
        self.watcher_ops = watcher_ops or Ops(
            lambda: find_processes(is_watcher_args), self._start_watcher,
            lambda pid: stop_process(pid, alive=lambda p: pid_matches(p, is_watcher_args)))
        self.cnn_ops = cnn_ops or Ops(lambda: find_processes(is_cnn_run_args),
                                      lambda cmd: Launch(cmd, self.root / "cnn_resume.out"),
                                      lambda pid: stop_process(pid, alive=lambda p: pid_matches(p, is_cnn_run_args)),
                                      cnn_resume_processes)
        self.children: dict[int, tuple[str, subprocess.Popen]] = {}
        self.refresh_proc: subprocess.Popen | None = None
        self.cnn_launch = None
        self.state = State()
        self.state.supervise = {"recorder": True, "live_watcher": self.watcher, "cnn_run": self.cnn}
        self.pid_path = self.root / "recorder.pid"
        self.status_path = self.root / "status.json"
        self.wd_status_path = self.root / "watchdog_status.json"
        self.watch_pid_path = self.live_dir / "watch.pid"
        self.cnn_pid_path = self.cnn_dir / "run.pid"
        self._load_previous()

    def _load_previous(self) -> None:
        """Carry the restart counts, streaks and history over from the previous watchdog's status file."""
        try:
            prev = json.loads(self.wd_status_path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return
        for k in ("restarts", "streak", "last_restart_utc", "last_restart_reason", "started_here_pid", "started_here_utc",
                  "extra_recorders_killed", "pass_refresh_last_attempt_utc", "pass_refresh_last_result", "history",
                  "recorder_cmd"):
            if k in prev and prev[k] is not None:
                setattr(self.state, k, prev[k])
        for k in ("watcher", "cnn"):
            if isinstance(prev.get(k), dict):
                setattr(self.state, k, dict(prev[k]))
        self.state.cnn.pop("complete", None)  # re-asked once per watchdog: a new phase or a lost checkpoint shows up
        self.state.previous_watchdog = {"pid": prev.get("pid"), "started_utc": prev.get("started_utc"),
                                        "last_check_utc": prev.get("last_check_utc"), "checks": prev.get("checks")}

    # -- file helpers -------------------------------------------------------------------------------------------
    def read_pid_file(self) -> int | None:
        return read_pid(self.pid_path)

    def read_status(self) -> dict | None:
        try:
            return json.loads(self.status_path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return None

    def write_pid_file(self, pid: int) -> None:
        _atomic_write(self.pid_path, str(pid))

    def write_state(self) -> None:
        s = dict(self.state.__dict__)
        s["history"] = s["history"][-30:]
        s["note"] = ("Watchdog for scripts/26_ais_record.py, the live watcher (scripts/30_live_pass.py --watch) and the "
                     "regional CNN run (scripts/32_cnn_regional.py). Restarts only what is dead or, for the recorder, "
                     f"silent; backoff {', '.join(str(b // 60) for b in self.backoff_s)} min between consecutive "
                     f"restarts; CNN resumes at least {self.cnn_gap_s[0] // 60} min apart.")
        _atomic_write(self.wd_status_path, json.dumps(s, indent=1, default=str))

    def _event(self, kind: str, **kw) -> None:
        self.state.history.append({"utc": _iso(self.now_fn()), "event": kind, **kw})
        self.state.history = self.state.history[-30:]

    # -- one check ----------------------------------------------------------------------------------------------
    def check(self) -> str:
        """Run one check; returns the recorder decision ('ok', 'adopted', 'restarted', 'waiting', ...). The watcher
        and CNN decisions are in state.watcher and state.cnn."""
        now = self.now_fn()
        self._reap()
        st = self.state
        st.checks += 1
        st.last_check_utc = _iso(now)
        try:
            return self._check_recorder(now)
        finally:
            for name, on, fn in (("live watcher", self.watcher, self._check_watcher),
                                 ("CNN run", self.cnn, self._check_cnn)):
                if on:
                    try:
                        fn(now)
                    except Exception as exc:  # one failing supervisor must not stop the others
                        self.log(f"{name} check failed: {type(exc).__name__}: {exc}")
            self.write_state()

    def _check_recorder(self, now: pd.Timestamp) -> str:
        st = self.state
        procs = sorted(set(self.procs_fn()))
        pid_file = self.read_pid_file()
        status = self.read_status()
        status_pid = status.get("pid") if status else None
        current = choose_current(procs, pid_file, status_pid)
        decision = "ok"
        for extra in [p for p in procs if p != current]:
            self.log(f"second recorder pid {extra} found next to pid {current}; stopping it (one connection only)")
            self.stop_fn(extra)
            st.extra_recorders_killed += 1
            self._event("killed_extra", pid=extra, kept=current)
        if current is not None and current != pid_file:
            self.write_pid_file(current)
            self.log(f"adopted running recorder pid {current} (recorder.pid said {pid_file})")
            self._event("adopted", pid=current)
            decision = "adopted"
        if current is not None and st.recorder_pid != current:
            if st.recorder_pid is None:
                self.log(f"watching recorder pid {current}")
            st.recorder_pid = current
            cmd = self.cmdline_fn(current)
            if cmd:
                st.recorder_cmd = cmd
        started_here = _ts(st.started_here_utc) if st.started_here_pid == current else None
        alive = current is not None and self.alive_fn(current)
        h = assess(current, alive, status, now, stale_s=self.stale_s, grace_s=self.grace_s, started_here_utc=started_here)
        if h.healthy:
            if st.healthy_since_utc is None:
                st.healthy_since_utc = _iso(now)
            healthy_s = (now - _ts(st.healthy_since_utc)).total_seconds()
            if st.streak and healthy_s >= self.streak_reset_s:
                self.log(f"recorder healthy for {healthy_s / 60:.0f} min since the last restart; backoff reset")
                st.streak = 0
            st.last_decision = f"{decision}: {h.reason}"
            self._maybe_refresh(now)
            return decision
        st.healthy_since_utc = None
        wait = restart_wait_s(now, _ts(st.last_restart_utc), st.streak, self.backoff_s)
        if wait > 0:
            msg = f"recorder unhealthy ({h.reason}); restart {st.streak + 1} of this streak allowed in {wait:.0f} s"
            if st.last_decision != "waiting":
                self.log(msg)
            st.last_decision = "waiting"
            return "waiting"
        if current is not None and self.alive_fn(current):
            self.log(f"stopping unhealthy recorder pid {current}: {h.reason}")
            self.stop_fn(current)
        still = [p for p in self.procs_fn() if self.alive_fn(p)]
        if still:  # never start a second recorder next to one that would not die
            self.log(f"recorder pid(s) {still} still alive after SIGTERM and SIGKILL; not starting another")
            st.last_decision = "blocked"
            return "blocked"
        cmd = st.recorder_cmd or list(DEFAULT_CMD)
        new = self.start_fn(cmd)
        self.write_pid_file(new)
        st.restarts += 1
        st.streak += 1
        st.last_restart_utc = _iso(now)
        st.last_restart_reason = h.reason
        st.started_here_pid, st.started_here_utc = new, _iso(now)
        st.recorder_pid = new
        st.last_decision = f"restarted: {h.reason}"
        nxt = self.backoff_s[min(st.streak - 1, len(self.backoff_s) - 1)]
        self.log(f"restart {st.restarts} (streak {st.streak}): {h.reason}; new recorder pid {new}; "
                 f"next restart no sooner than {nxt // 60} min")
        self._event("restarted", old_pid=current, new_pid=new, reason=h.reason, streak=st.streak)
        return "restarted"

    # -- live watcher -------------------------------------------------------------------------------------------
    def _check_watcher(self, now: pd.Timestamp) -> str:
        ws = self.state.watcher
        if not (self.scripts_dir / WATCHER_SCRIPT).exists():
            ws["last_decision"] = f"not supervised: scripts/{WATCHER_SCRIPT} is missing"
            return "missing"
        pid_file = read_pid(self.watch_pid_path)  # before the scan: a watcher that writes it later is in the scan
        procs = sorted(set(self.watcher_ops.procs()))
        current = choose_current(procs, pid_file, None)
        for extra in [p for p in procs if p != current]:
            self.log(f"live watcher: second watcher pid {extra} next to pid {current}; stopping it")
            self.watcher_ops.stop(extra)
            ws["extra_killed"] = int(ws.get("extra_killed") or 0) + 1
            self._event("watcher_killed_extra", pid=extra, kept=current)
        if current is not None:
            if current != pid_file:
                _atomic_write(self.watch_pid_path, str(current))
                self.log(f"live watcher: adopted pid {current} (watch.pid said {pid_file})")
            if ws.get("pid") != current:
                self.log(f"live watcher: watching pid {current}")
            ws["pid"] = current
            ws["alive_since_utc"] = ws.get("alive_since_utc") or _iso(now)
            alive_s = (now - _ts(ws["alive_since_utc"])).total_seconds()
            if ws.get("streak") and alive_s >= self.streak_reset_s:
                self.log(f"live watcher: alive for {alive_s / 60:.0f} min since the last restart; backoff reset")
                ws["streak"] = 0
            ws["last_decision"] = f"ok: pid {current} alive"
            return "ok"
        ws["alive_since_utc"] = None
        why = "no live watcher process" + (f" (watch.pid {pid_file} is not a live watcher)" if pid_file else "")
        streak = int(ws.get("streak") or 0)
        wait = restart_wait_s(now, _ts(ws.get("last_restart_utc")), streak, self.backoff_s)
        if wait > 0:
            if not str(ws.get("last_decision") or "").startswith("waiting"):
                self.log(f"live watcher: {why}; restart {streak + 1} of this streak allowed in {wait:.0f} s")
            ws["last_decision"] = f"waiting: {why}"
            return "waiting"
        new = self.watcher_ops.start(list(WATCHER_CMD))
        _atomic_write(self.watch_pid_path, str(new))
        ws.update(pid=new, restarts=int(ws.get("restarts") or 0) + 1, streak=streak + 1, last_restart_utc=_iso(now),
                  last_restart_reason=why, last_decision=f"restarted: {why}")
        nxt = self.backoff_s[min(streak, len(self.backoff_s) - 1)]
        self.log(f"live watcher: restart {ws['restarts']} (streak {streak + 1}): {why}; new watcher pid {new}; "
                 f"next restart no sooner than {nxt // 60} min")
        self._event("watcher_restarted", old_pid=pid_file, new_pid=new, reason=why, streak=streak + 1)
        return "restarted"

    # -- regional CNN run ---------------------------------------------------------------------------------------
    def _cnn_checkpoints(self) -> int:
        return sum(1 for ph in CNN_PHASES if (self.cnn_dir / ph).is_dir() for _ in (self.cnn_dir / ph).glob("*.parquet"))

    def _check_cnn(self, now: pd.Timestamp) -> str:
        cs = self.state.cnn
        if self.cnn_launch is not None:
            age = getattr(self.cnn_launch, "age_s", lambda: 0.0)()
            if age > CNN_LAUNCH_TIMEOUT_S:
                self.log(f"CNN run: resume command still running after {age:.0f} s; killing it")
                self.cnn_launch.kill()
            cs["last_decision"] = "launching: resume command running"
            return "launching"
        if not (self.scripts_dir / CNN_SCRIPT).exists():
            cs["last_decision"] = f"not supervised: scripts/{CNN_SCRIPT} is missing"
            return "missing"
        pid_file = read_pid(self.cnn_pid_path)  # before the scan, as for the watcher
        procs = sorted(set(self.cnn_ops.procs()))
        current = choose_current(procs, pid_file, None)
        for extra in [p for p in procs if p != current]:
            self.log(f"CNN run: second run pid {extra} next to pid {current}; stopping it (both would score the same "
                     "scenes; checkpoints are per scene)")
            self.cnn_ops.stop(extra)
            cs["extra_killed"] = int(cs.get("extra_killed") or 0) + 1
            self._event("cnn_killed_extra", pid=extra, kept=current)
        n_ck = self._cnn_checkpoints()
        cs["checkpoints"] = n_ck
        if current is not None:
            if current != pid_file:
                _atomic_write(self.cnn_pid_path, str(current))
                self.log(f"CNN run: adopted pid {current} (run.pid said {pid_file})")
            if cs.get("pid") != current:
                self.log(f"CNN run: watching pid {current}")
            cs["pid"], cs["missing_checks"] = current, 0
            cs["last_decision"] = f"running: pid {current}, {n_ck} scene checkpoints"
            return "running"
        # a resume command started elsewhere (the session hook) is still deciding: wait for its answer, so that two
        # resumes never race and start two runs
        busy = [(p, a) for p, a in self.cnn_ops.resumes() if a is None or a < CNN_LAUNCH_TIMEOUT_S]
        if busy:
            if not str(cs.get("last_decision") or "").startswith("waiting: resume command"):
                self.log(f"CNN run: not running; waiting for resume command pid {busy[0][0]} (not started here, "
                         f"{busy[0][1] or 0:.0f} s old)")
            cs["missing_checks"] = 0
            cs["last_decision"] = (f"waiting: resume command pid {busy[0][0]} ({busy[0][1] or 0:.0f} s old, not started "
                                   "by this watchdog) is running")
            return "resume_elsewhere"
        # act only when the run is missing in consecutive checks: the run re-executes itself in place when its memory
        # grows, and a command line read at that instant can come back empty
        cs["missing_checks"] = int(cs.get("missing_checks") or 0) + 1
        if cs["missing_checks"] < self.cnn_confirm_checks:
            cs["last_decision"] = "no run seen; confirming at the next check"
            return "unconfirmed"
        if cs.get("pid") is not None:
            self.log(f"CNN run: pid {cs['pid']} has ended; {n_ck} scene checkpoints")
            cs["pid"] = None
        if cs.get("awaiting_outcome"):  # the run of our last resume has ended: did it add a checkpoint?
            gained = n_ck - int(cs.get("launch_checkpoints") or 0)
            cs["no_progress"] = 0 if gained > 0 else int(cs.get("no_progress") or 0) + 1
            cs["awaiting_outcome"] = False
            self._event("cnn_ended", gained=gained, no_progress=cs["no_progress"])
        if pid_file is not None and read_pid(self.cnn_pid_path) == pid_file:
            self.cnn_pid_path.unlink(missing_ok=True)
            self.log(f"CNN run: removed stale run.pid ({pid_file} is not a live CNN run; script 32 checks only that "
                     "the pid exists)")
        if cs.get("complete"):
            cs["last_decision"] = "complete: every scene checkpointed"
            return "complete"
        no_progress = int(cs.get("no_progress") or 0)
        wait = cnn_wait_s(now, _ts(cs.get("last_launch_utc")), no_progress, self.cnn_gap_s)
        if wait > 0:
            if not str(cs.get("last_decision") or "").startswith("waiting"):
                self.log(f"CNN run: not running; next resume allowed in {wait / 60:.0f} min (resumes without "
                         f"progress in a row: {no_progress})")
            cs["last_decision"] = f"waiting: next resume after {_iso(now + pd.Timedelta(seconds=wait))}"
            return "waiting"
        cs.update(last_launch_utc=_iso(now), launch_checkpoints=n_ck, launches=int(cs.get("launches") or 0) + 1,
                  last_decision="launching: resume command running")
        self.log(f"CNN run: not running; resume {cs['launches']}: python {' '.join(CNN_CMD[4:])} "
                 f"({n_ck} scene checkpoints)")
        self.cnn_launch = self.cnn_ops.start(list(CNN_CMD))
        return "launching"

    def _cnn_launch_done(self) -> None:
        h, cs, now = self.cnn_launch, self.state.cnn, self.now_fn()
        self.cnn_launch = None
        rc, out = h.poll(), h.output() or ""
        kind, pid = parse_cnn_resume(out)
        last = (out.strip().splitlines() or [""])[-1][:240]
        cs["last_result"] = f"{kind} (exit {rc}) at {_iso(now)}: {last}"
        if kind == "started":
            cs.update(pid=pid, awaiting_outcome=True, starts=int(cs.get("starts") or 0) + 1)
            self.log(f"CNN run: started pid {pid}; log data/cache/regional_cnn/run.log")
            self._event("cnn_started", pid=pid, checkpoints=cs.get("launch_checkpoints"))
        elif kind == "complete":
            cs.update(complete=True, complete_utc=_iso(now))
            self.log("CNN run: every scene of main,low is checkpointed; nothing to resume")
            self._event("cnn_complete")
        elif kind == "running":
            self.log(f"CNN run: script 32 reports a run alive (pid {pid}); checked again at the next check")
        else:
            cs["no_progress"] = int(cs.get("no_progress") or 0) + 1
            cs["failures"] = int(cs.get("failures") or 0) + 1
            self.log(f"CNN run: resume command failed (exit {rc}): {last}")
            self._event("cnn_resume_failed", rc=rc)

    # -- processes ----------------------------------------------------------------------------------------------
    def _start_detached(self, label: str, cmd: list[str], log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as out, open(os.devnull, "rb") as devnull:
            p = subprocess.Popen(cmd, cwd=REPO_ROOT, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
        self.children[p.pid] = (label, p)
        return p.pid

    def _start_recorder(self, cmd: list[str]) -> int:
        return self._start_detached("recorder", cmd, self.root / "record.log")

    def _start_watcher(self, cmd: list[str]) -> int:
        return self._start_detached("live watcher", cmd, self.live_dir / "watch.log")

    def _reap(self) -> None:
        for pid, (label, p) in list(self.children.items()):
            if p.poll() is not None:
                self.log(f"{label} pid {pid} exited with code {p.returncode}")
                del self.children[pid]
        if self.refresh_proc is not None and self.refresh_proc.poll() is not None:
            rc = self.refresh_proc.returncode
            self.state.pass_refresh_last_result = f"exit {rc} at {_iso(self.now_fn())}"
            self.log(f"pass plan refresh finished with exit code {rc}")
            self.refresh_proc = None
        if self.cnn_launch is not None and self.cnn_launch.poll() is not None:
            self._cnn_launch_done()

    # -- pass plan refresh --------------------------------------------------------------------------------------
    def _maybe_refresh(self, now: pd.Timestamp) -> None:
        if self.refresh_hours <= 0 or self.refresh_proc is not None:
            return
        try:
            gen = _ts(json.loads(self.passes_json.read_text()).get("generated_utc"))
        except (FileNotFoundError, ValueError, OSError):
            gen = None
        if gen is not None and (now - gen).total_seconds() < self.refresh_hours * 3600:
            return
        last_try = _ts(self.state.pass_refresh_last_attempt_utc)
        if last_try is not None and (now - last_try).total_seconds() < REFRESH_RETRY_S:
            return
        self.state.pass_refresh_last_attempt_utc = _iso(now)
        self.log(f"pass plan older than {self.refresh_hours:g} h (generated {_iso(gen)}); refreshing in the background")
        self.refresh_proc = self.refresh_fn()

    def _start_refresh(self):
        with open(self.root / "pass_refresh.log", "ab") as out, open(os.devnull, "rb") as devnull:
            return subprocess.Popen(PASS_REFRESH_CMD, cwd=REPO_ROOT, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True, close_fds=True)


# ---------------------------------------------------------------------------------------------------------------
# /proc helpers
# ---------------------------------------------------------------------------------------------------------------

def read_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [c.decode("utf-8", "replace") for c in raw.split(b"\0") if c] or None


def read_pid(path: Path) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _proc_state(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        return None


def pid_matches(pid: int, match) -> bool:
    """Live, not a zombie, and its command line passes `match` (so pid reuse after a reboot is rejected)."""
    if _proc_state(pid) in (None, "Z", "X"):
        return False
    return bool(match(read_cmdline(pid) or []))


def is_recorder_pid(pid: int) -> bool:
    """Live, not a zombie, and running 26_ais_record.py in recording mode (pid reuse after a reboot is rejected)."""
    return pid_matches(pid, is_recorder_args)


def find_processes(match) -> list[int]:
    me = os.getpid()
    return sorted(int(d.name) for d in Path("/proc").iterdir()
                  if d.name.isdigit() and int(d.name) != me and pid_matches(int(d.name), match))


def recorder_processes() -> list[int]:
    return find_processes(is_recorder_args)


def process_age_s(pid: int) -> float | None:
    """Seconds since the process started (/proc/<pid>/stat field 22 against /proc/uptime), None if unreadable."""
    try:
        start_ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[19])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))


def cnn_resume_processes() -> list[tuple[int, float | None]]:
    """Resume commands of script 32 in progress, with their age; this watchdog's own (its child) included, but the
    check skips the scan while its own launch is running."""
    return [(p, process_age_s(p)) for p in find_processes(is_cnn_resume_args)]


def stop_process(pid: int, term_wait_s: float = TERM_WAIT_S, alive=is_recorder_pid) -> None:
    """SIGTERM (the recorder flushes and exits; the watcher exits between scenes or mid-scene, both safe), SIGKILL
    after `term_wait_s`. `alive(pid)` says whether the process is still the program being stopped."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    t0 = time.monotonic()
    while time.monotonic() - t0 < term_wait_s:
        _reap_any()
        if not alive(pid):
            return
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    time.sleep(0.5)
    _reap_any()


def _reap_any() -> None:
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
    except ChildProcessError:
        pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def _last_line(path: Path, width: int = 150) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, fh.seek(0, 2) - 4096))
            lines = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip()]
    except OSError:
        return "no log"
    return lines[-1][:width] if lines else "empty log"


# ---------------------------------------------------------------------------------------------------------------
# Lock, daemon and CLI
# ---------------------------------------------------------------------------------------------------------------

def acquire_lock(path: Path):
    """Exclusive non-blocking flock; returns the open file (keep it open) or None if another watchdog holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def watchdog_pid(root: Path = AIS_DIR) -> int | None:
    """pid of the running watchdog (from watchdog.pid, checked against /proc), or None."""
    pid = read_pid(root / "watchdog.pid")
    if pid is None or _proc_state(pid) in (None, "Z", "X"):
        return None
    args = read_cmdline(pid) or []
    return pid if any(a.endswith("29_ais_watchdog.py") for a in args) else None


def run(args) -> int:
    def log(msg: str) -> None:
        print(f"{now_utc():%Y-%m-%d %H:%M:%S}Z [watchdog] {msg}", flush=True)

    lock = acquire_lock(AIS_DIR / "watchdog.lock")
    if lock is None:
        log("another watchdog holds watchdog.lock; exiting")
        return 3
    _atomic_write(AIS_DIR / "watchdog.pid", str(os.getpid()))
    wd = Watchdog(AIS_DIR, stale_s=args.stale_minutes * 60, log=log, refresh_hours=args.refresh_passes_hours,
                  watcher=not args.no_live_watcher, cnn=not args.no_cnn_resume)
    stopping = {"flag": False}

    def on_signal(sig, _frame):
        log(f"signal {signal.Signals(sig).name}; watchdog stopping (the processes it supervises keep running)")
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    log(f"started pid {os.getpid()}; check every {args.interval:.0f} s, stale after {args.stale_minutes:g} min, "
        f"restart backoff {', '.join(str(b // 60) for b in BACKOFF_S)} min, pass refresh every "
        f"{args.refresh_passes_hours:g} h; live watcher {'supervised' if wd.watcher else 'not supervised'}; "
        f"CNN run {f'resumed while incomplete, at least {CNN_GAP_S[0] // 60} min apart' if wd.cnn else 'not supervised'}")
    try:
        while not stopping["flag"]:
            try:
                wd.check()
            except Exception as exc:  # a failing check must not kill the watchdog
                log(f"check failed: {type(exc).__name__}: {exc}")
            if args.once:
                t_end = time.monotonic() + 120  # let a CNN resume answer before exiting
                while wd.cnn_launch is not None and wd.cnn_launch.poll() is None and time.monotonic() < t_end:
                    time.sleep(1)
                wd._reap()
                wd.write_state()
                break
            t_next = time.monotonic() + args.interval
            while not stopping["flag"] and time.monotonic() < t_next:
                time.sleep(1)
    finally:
        try:
            if (AIS_DIR / "watchdog.pid").read_text().strip() == str(os.getpid()):
                (AIS_DIR / "watchdog.pid").unlink()
        except OSError:
            pass
        lock.close()
        log("stopped")
    return 0


def ensure(args) -> int:
    pid = watchdog_pid()
    if pid:
        print(f"watchdog already running, pid {pid}")
        return 0
    AIS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--run", "--interval", str(args.interval),
           "--stale-minutes", str(args.stale_minutes), "--refresh-passes-hours", str(args.refresh_passes_hours)]
    cmd += ["--no-live-watcher"] if args.no_live_watcher else []
    cmd += ["--no-cnn-resume"] if args.no_cnn_resume else []
    with open(AIS_DIR / "watchdog.log", "ab") as out, open(os.devnull, "rb") as devnull:
        p = subprocess.Popen(cmd, cwd=REPO_ROOT, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
    for _ in range(20):
        time.sleep(0.5)
        if watchdog_pid() == p.pid:
            print(f"watchdog started, pid {p.pid}; log {AIS_DIR / 'watchdog.log'}")
            return 0
        if p.poll() is not None:
            print(f"watchdog exited at once with code {p.returncode}; see {AIS_DIR / 'watchdog.log'}")
            return 1
    print(f"watchdog launched, pid {p.pid}, but watchdog.pid not written yet; check {AIS_DIR / 'watchdog.log'}")
    return 0


def status() -> int:
    pid = watchdog_pid()
    print(f"watchdog: {'running, pid ' + str(pid) if pid else 'not running'}")
    try:
        s = json.loads((AIS_DIR / "watchdog_status.json").read_text())
        for k in ("started_utc", "last_check_utc", "checks", "restarts", "streak", "last_restart_utc",
                  "last_restart_reason", "recorder_pid", "last_decision", "extra_recorders_killed",
                  "pass_refresh_last_attempt_utc", "pass_refresh_last_result"):
            print(f"  {k}: {s.get(k)}")
    except (FileNotFoundError, ValueError):
        s = {}
        print("  no watchdog_status.json")
    sup = s.get("supervise") or {}

    def supervised(key):
        if not pid:
            return "NOT supervised (no watchdog running)"
        if key not in sup:
            return "not supervised (the running watchdog predates this supervision; restart it)"
        return "supervised" if sup[key] else "not supervised (flag)"

    print(f"recorder processes: {recorder_processes() or 'none'}; recorder.pid {read_pid(AIS_DIR / 'recorder.pid')}")
    ws = s.get("watcher") or {}
    print(f"live watcher: {supervised('live_watcher')}; processes {find_processes(is_watcher_args) or 'none'}; "
          f"watch.pid {read_pid(LIVE_DIR / 'watch.pid')}")
    for k in ("restarts", "streak", "last_restart_utc", "last_restart_reason", "last_decision", "extra_killed"):
        print(f"  {k}: {ws.get(k)}")
    print(f"  watch.log: {_last_line(LIVE_DIR / 'watch.log')}")
    cs = s.get("cnn") or {}
    ck = {ph: len(list((CNN_DIR / ph).glob("*.parquet"))) if (CNN_DIR / ph).is_dir() else 0 for ph in CNN_PHASES}
    print(f"CNN run: {supervised('cnn_run')}; processes {find_processes(is_cnn_run_args) or 'none'}; "
          f"run.pid {read_pid(CNN_DIR / 'run.pid')}; scene checkpoints {', '.join(f'{k} {v}' for k, v in ck.items())}; "
          f"resume commands running {[p for p, _ in cnn_resume_processes()] or 'none'}")
    for k in ("launches", "starts", "failures", "no_progress", "last_launch_utc", "last_result", "complete",
              "last_decision"):
        print(f"  {k}: {cs.get(k)}")
    wait = cnn_wait_s(now_utc(), _ts(cs.get("last_launch_utc")), int(cs.get("no_progress") or 0))
    print(f"  next resume allowed: {'now' if wait <= 0 else _iso(now_utc() + pd.Timedelta(seconds=wait))}")
    print(f"  run.log: {_last_line(CNN_DIR / 'run.log')}")
    return 0


def stop() -> int:
    pid = watchdog_pid()
    if not pid:
        print("watchdog is not running")
        return 1
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.5)
        if watchdog_pid() is None:
            print(f"stopped watchdog pid {pid}; the recorder, the live watcher and the CNN run were left running")
            return 0
    print(f"watchdog pid {pid} still alive after 15 s")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--run", action="store_true", help="run the check loop in the foreground")
    g.add_argument("--once", action="store_true", help="one check, then exit")
    g.add_argument("--ensure", action="store_true", help="start detached unless a watchdog is running")
    g.add_argument("--status", action="store_true")
    g.add_argument("--stop", action="store_true", help="stop the watchdog (not what it supervises)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between checks")
    ap.add_argument("--stale-minutes", type=float, default=STALE_S / 60)
    ap.add_argument("--refresh-passes-hours", type=float, default=6.0, help="0 = never refresh the pass plan")
    ap.add_argument("--no-live-watcher", action="store_true", help="do not supervise scripts/30_live_pass.py --watch")
    ap.add_argument("--no-cnn-resume", action="store_true", help="do not resume scripts/32_cnn_regional.py")
    args = ap.parse_args(argv)
    if args.status:
        return status()
    if args.stop:
        return stop()
    if args.ensure:
        return ensure(args)
    if args.run or args.once:
        return run(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
