"""Keep the live AIS recorder (scripts/26_ais_record.py) alive across proxy drops, crashes and hangs.

Purpose: a Sentinel-1 pass can be identified against live AIS only if AIS was recorded while the satellite looked.
The recorder died once (2026-10-08 15:20 UTC, the container went down) and nothing restarted it for 7 hours. This
watchdog checks it every 60 s and restarts it when it is dead or silent, without ever running two recorders.

Method: each check (1) lists recorder processes from /proc (command line ends in 26_ais_record.py, not --status or
--stop, not a zombie) and reads recorder.pid and status.json; (2) adopts the recorder in recorder.pid, or the one that
wrote status.json, and SIGTERMs any other recorder; (3) calls the recorder healthy when its pid is alive and
status.json, written by that pid, has last_message_utc (started_utc before the first message) less than 5 min old; a
recorder this watchdog started gets a 3 min grace before status.json must be its own; (4) when unhealthy, and the
restart backoff allows, SIGTERMs the stale process (SIGKILL after 20 s), starts a new recorder with the same command
line (new session, stdin /dev/null, output appended to record.log: the nohup setsid equivalent) and writes the new
pid to recorder.pid. Consecutive restarts wait 1, 2, 5, then 10 min after the previous one; 15 min of health resets
the streak. A healthy recorder is never restarted. An exclusive flock on watchdog.lock keeps a second watchdog out.
Every 6 h (age of generated_utc in data/s1_next_passes.json) it refreshes the Sentinel-1 pass plan in the background
with `nice -n 10 python scripts/28_ais_reach.py --passes-only --fetch-plan`; a failed refresh is retried after 1 h.
A container restart kills the watchdog too: run `--ensure` at session start (idempotent).

Inputs:  data/cache/ais/aisstream/recorder.pid, status.json (written by the recorder), data/s1_next_passes.json
Output:  data/cache/ais/aisstream/watchdog.pid, watchdog.lock, watchdog.log, watchdog_status.json (checks,
         restarts, streak, last decision, restart history), recorder.pid on a restart
Usage:
  python scripts/29_ais_watchdog.py --ensure     # start detached unless a watchdog holds the lock (session start)
  nohup setsid python scripts/29_ais_watchdog.py --run >> data/cache/ais/aisstream/watchdog.log 2>&1 &
  python scripts/29_ais_watchdog.py --status     # watchdog and recorder state
  python scripts/29_ais_watchdog.py --stop       # stop the watchdog only; the recorder keeps running
  python scripts/29_ais_watchdog.py --once       # one check in the foreground, then exit
Options: --interval 60, --stale-minutes 5, --refresh-passes-hours 6 (0 = never).

The recorder hears what aisstream.io shore receivers hear. "Dark" never means illegal (darkvessel.config.DARK_CAVEAT).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
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

BACKOFF_S = (60, 120, 300, 600)  # wait after the previous restart before restart 2, 3, 4, 5 and later of a streak
STALE_S = 300
GRACE_S = 180
STREAK_RESET_S = 900
TERM_WAIT_S = 20
REFRESH_RETRY_S = 3600


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


def choose_current(procs: list[int], pid_file: int | None, status_pid: int | None) -> int | None:
    """The recorder to keep: the pid file's if it is running, else the one that wrote status.json, else the newest."""
    if pid_file in procs:
        return pid_file
    if status_pid in procs:
        return status_pid
    return max(procs) if procs else None


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
    history: list[dict] = field(default_factory=list)


class Watchdog:
    """One check per `check()` call. Process and file effects go through injectable callables (tests use fakes)."""

    def __init__(self, root: Path = AIS_DIR, *, stale_s: float = STALE_S, grace_s: float = GRACE_S,
                 streak_reset_s: float = STREAK_RESET_S, backoff_s=BACKOFF_S, log=print, procs_fn=None,
                 alive_fn=None, start_fn=None, stop_fn=None, cmdline_fn=None, now_fn=now_utc,
                 refresh_hours: float = 0, refresh_fn=None, passes_json: Path = PASSES_JSON):
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
        self.children: dict[int, subprocess.Popen] = {}
        self.refresh_proc: subprocess.Popen | None = None
        self.state = State()
        self.pid_path = self.root / "recorder.pid"
        self.status_path = self.root / "status.json"
        self.wd_status_path = self.root / "watchdog_status.json"
        self._load_previous()

    def _load_previous(self) -> None:
        """Carry the restart count, streak and history over from the previous watchdog's status file."""
        try:
            prev = json.loads(self.wd_status_path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return
        for k in ("restarts", "streak", "last_restart_utc", "last_restart_reason", "started_here_pid", "started_here_utc",
                  "extra_recorders_killed", "pass_refresh_last_attempt_utc", "pass_refresh_last_result", "history",
                  "recorder_cmd"):
            if k in prev and prev[k] is not None:
                setattr(self.state, k, prev[k])
        self.state.previous_watchdog = {"pid": prev.get("pid"), "started_utc": prev.get("started_utc"),
                                        "last_check_utc": prev.get("last_check_utc"), "checks": prev.get("checks")}

    # -- file helpers -------------------------------------------------------------------------------------------
    def read_pid_file(self) -> int | None:
        try:
            return int(self.pid_path.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

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
        s["note"] = ("Watchdog for scripts/26_ais_record.py. Restarts only a dead or silent recorder; backoff "
                     f"{', '.join(str(b // 60) for b in self.backoff_s)} min between consecutive restarts.")
        _atomic_write(self.wd_status_path, json.dumps(s, indent=1, default=str))

    def _event(self, kind: str, **kw) -> None:
        self.state.history.append({"utc": _iso(self.now_fn()), "event": kind, **kw})
        self.state.history = self.state.history[-30:]

    # -- one check ----------------------------------------------------------------------------------------------
    def check(self) -> str:
        """Run one check; returns the decision ('ok', 'adopted', 'restarted', 'waiting', ...)."""
        now = self.now_fn()
        self._reap()
        st = self.state
        st.checks += 1
        st.last_check_utc = _iso(now)
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
            self.write_state()
            return decision
        st.healthy_since_utc = None
        wait = restart_wait_s(now, _ts(st.last_restart_utc), st.streak, self.backoff_s)
        if wait > 0:
            msg = f"recorder unhealthy ({h.reason}); restart {st.streak + 1} of this streak allowed in {wait:.0f} s"
            if st.last_decision != "waiting":
                self.log(msg)
            st.last_decision = "waiting"
            self.write_state()
            return "waiting"
        if current is not None and self.alive_fn(current):
            self.log(f"stopping unhealthy recorder pid {current}: {h.reason}")
            self.stop_fn(current)
        still = [p for p in self.procs_fn() if self.alive_fn(p)]
        if still:  # never start a second recorder next to one that would not die
            self.log(f"recorder pid(s) {still} still alive after SIGTERM and SIGKILL; not starting another")
            st.last_decision = "blocked"
            self.write_state()
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
        self.write_state()
        return "restarted"

    # -- processes ----------------------------------------------------------------------------------------------
    def _start_recorder(self, cmd: list[str]) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / "record.log", "ab") as out, open(os.devnull, "rb") as devnull:
            p = subprocess.Popen(cmd, cwd=REPO_ROOT, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
        self.children[p.pid] = p
        return p.pid

    def _reap(self) -> None:
        for pid, p in list(self.children.items()):
            if p.poll() is not None:
                self.log(f"recorder pid {pid} exited with code {p.returncode}")
                del self.children[pid]
        if self.refresh_proc is not None and self.refresh_proc.poll() is not None:
            rc = self.refresh_proc.returncode
            self.state.pass_refresh_last_result = f"exit {rc} at {_iso(self.now_fn())}"
            self.log(f"pass plan refresh finished with exit code {rc}")
            self.refresh_proc = None

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


def _proc_state(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        return None


def is_recorder_pid(pid: int) -> bool:
    """Live, not a zombie, and running 26_ais_record.py in recording mode (pid reuse after a reboot is rejected)."""
    if _proc_state(pid) in (None, "Z", "X"):
        return False
    args = read_cmdline(pid) or []
    return any(a.endswith(RECORDER_SCRIPT) for a in args) and not ({"--status", "--stop"} & set(args))


def recorder_processes() -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit() and int(d.name) != os.getpid() and is_recorder_pid(int(d.name)):
            out.append(int(d.name))
    return sorted(out)


def stop_process(pid: int, term_wait_s: float = TERM_WAIT_S) -> None:
    """SIGTERM (the recorder flushes and exits), SIGKILL after `term_wait_s`."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    t0 = time.monotonic()
    while time.monotonic() - t0 < term_wait_s:
        _reap_any()
        if not is_recorder_pid(pid):
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
    try:
        pid = int((root / "watchdog.pid").read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None
    if _proc_state(pid) in (None, "Z", "X"):
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
    wd = Watchdog(AIS_DIR, stale_s=args.stale_minutes * 60, log=log, refresh_hours=args.refresh_passes_hours)
    stopping = {"flag": False}

    def on_signal(sig, _frame):
        log(f"signal {signal.Signals(sig).name}; watchdog stopping (the recorder keeps running)")
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    log(f"started pid {os.getpid()}; check every {args.interval:.0f} s, stale after {args.stale_minutes:g} min, "
        f"restart backoff {', '.join(str(b // 60) for b in BACKOFF_S)} min, pass refresh every "
        f"{args.refresh_passes_hours:g} h")
    try:
        while not stopping["flag"]:
            try:
                wd.check()
            except Exception as exc:  # a failing check must not kill the watchdog
                log(f"check failed: {type(exc).__name__}: {exc}")
            if args.once:
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
        print("  no watchdog_status.json")
    procs = recorder_processes()
    print(f"recorder processes: {procs or 'none'}")
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
            print(f"stopped watchdog pid {pid}; the recorder was left running")
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
    g.add_argument("--stop", action="store_true", help="stop the watchdog (not the recorder)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between checks")
    ap.add_argument("--stale-minutes", type=float, default=STALE_S / 60)
    ap.add_argument("--refresh-passes-hours", type=float, default=6.0, help="0 = never refresh the pass plan")
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
