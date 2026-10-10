"""Offline tests for the watchdog (scripts/29_ais_watchdog.py). Recorder: health decision, restart backoff,
single-recorder rule, adoption, lock. Live watcher and CNN run: command-line matching, pid reuse, adoption, second
copies, restart backoff and streak reset, the CNN resume gap (30 min, doubled without progress), completion, a resume
started elsewhere (the session hook) is waited for, the on/off flags; static checks of scripts/session_start.sh.
Fake status files, fake pids and fake process tables; no network; no real recorder, watcher or CNN run is started or
killed (two tests start and stop a sleeping child).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location("ais_watchdog", Path(__file__).resolve().parents[1] / "scripts" / "29_ais_watchdog.py")
wdmod = importlib.util.module_from_spec(SPEC)
sys.modules["ais_watchdog"] = wdmod  # dataclasses look the module up by name
SPEC.loader.exec_module(wdmod)

T0 = pd.Timestamp("2026-10-08 23:00:00", tz="UTC")


def status(pid, last_msg=None, started=T0 - pd.Timedelta(hours=1)):
    return {"pid": pid, "started_utc": started.isoformat(), "last_message_utc": last_msg.isoformat() if last_msg else None}


# ---------------------------------------------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------------------------------------------

def test_assess_alive_and_fresh():
    h = wdmod.assess(515, True, status(515, T0 - pd.Timedelta(seconds=40)), T0)
    assert h.healthy and h.reason.startswith("ok") and h.age_s == pytest.approx(40)


def test_assess_dead_pid_and_no_process():
    assert not wdmod.assess(515, False, status(515, T0), T0).healthy
    h = wdmod.assess(None, False, None, T0)
    assert not h.healthy and h.reason == "no recorder process"


def test_assess_stale_status():
    h = wdmod.assess(515, True, status(515, T0 - pd.Timedelta(minutes=6)), T0)
    assert not h.healthy and h.reason.startswith("stale") and h.age_s == pytest.approx(360)
    # exactly at the limit is still healthy, one second over is not
    assert wdmod.assess(515, True, status(515, T0 - pd.Timedelta(seconds=300)), T0).healthy
    assert not wdmod.assess(515, True, status(515, T0 - pd.Timedelta(seconds=301)), T0).healthy


def test_assess_no_message_yet_uses_started_utc():
    fresh = status(600, None, started=T0 - pd.Timedelta(minutes=2))
    assert wdmod.assess(600, True, fresh, T0).healthy
    old = status(600, None, started=T0 - pd.Timedelta(minutes=9))
    assert not wdmod.assess(600, True, old, T0).healthy


def test_assess_grace_for_a_recorder_started_here():
    # the new recorder has not written status.json yet: the file still belongs to the old pid
    st = status(515, T0 - pd.Timedelta(minutes=20))
    assert wdmod.assess(700, True, st, T0, started_here_utc=T0 - pd.Timedelta(seconds=30)).healthy
    assert wdmod.assess(700, True, None, T0, started_here_utc=T0 - pd.Timedelta(seconds=30)).healthy
    # after the grace the mismatch is a failure; an adopted recorder never gets grace
    assert not wdmod.assess(700, True, st, T0, started_here_utc=T0 - pd.Timedelta(minutes=4)).healthy
    assert not wdmod.assess(700, True, st, T0).healthy


# ---------------------------------------------------------------------------------------------------------------
# backoff
# ---------------------------------------------------------------------------------------------------------------

def test_restart_backoff_sequence():
    last = T0
    assert wdmod.restart_wait_s(T0, None, 0) == 0
    assert wdmod.restart_wait_s(T0 + pd.Timedelta(seconds=5), last, 0) == 0  # a new streak restarts at once
    waits = [wdmod.restart_wait_s(T0, last, k) for k in (1, 2, 3, 4, 5, 9)]
    assert waits == [60, 120, 300, 600, 600, 600]
    assert wdmod.restart_wait_s(T0 + pd.Timedelta(seconds=45), last, 1) == pytest.approx(15)
    assert wdmod.restart_wait_s(T0 + pd.Timedelta(seconds=61), last, 1) == 0


def test_choose_current():
    assert wdmod.choose_current([515, 700], 515, 700) == 515
    assert wdmod.choose_current([700], 515, 700) == 700
    assert wdmod.choose_current([700, 701], None, None) == 701
    assert wdmod.choose_current([], 515, 515) is None


# ---------------------------------------------------------------------------------------------------------------
# Watchdog.check with a fake process table
# ---------------------------------------------------------------------------------------------------------------

class FakeWorld:
    """A process table and a clock. start() appends a new pid; stop() removes it."""

    def __init__(self, root: Path, pids=(), now=T0):
        self.root = root
        self.pids = set(pids)
        self.now = now
        self.started, self.stopped = [], []
        self.next_pid = 900

    def procs(self):
        return sorted(self.pids)

    def alive(self, pid):
        return pid in self.pids

    def start(self, cmd):
        assert not self.pids, "a second recorder would be started"
        self.next_pid += 1
        self.pids.add(self.next_pid)
        self.started.append((self.next_pid, list(cmd)))
        return self.next_pid

    def stop(self, pid):
        self.pids.discard(pid)
        self.stopped.append(pid)

    def write_status(self, pid, last_msg):
        (self.root / "status.json").write_text(json.dumps(status(pid, last_msg)))

    def watchdog(self, **kw):
        logs = []
        wd = wdmod.Watchdog(self.root, log=logs.append, procs_fn=self.procs, alive_fn=self.alive, start_fn=self.start,
                            stop_fn=self.stop, cmdline_fn=lambda pid: ["python", "scripts/26_ais_record.py", "--hours", "0"],
                            now_fn=lambda: self.now, **kw)
        return wd, logs


def test_check_adopts_a_healthy_recorder_without_restarting(tmp_path):
    w = FakeWorld(tmp_path, pids=[515])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(seconds=30))
    wd, logs = w.watchdog()
    assert wd.check() == "ok"
    assert w.started == [] and w.stopped == [] and wd.state.restarts == 0 and wd.state.recorder_pid == 515
    s = json.loads((tmp_path / "watchdog_status.json").read_text())
    assert s["restarts"] == 0 and s["recorder_cmd"][1] == "scripts/26_ais_record.py"


def test_check_adopts_when_pid_file_is_stale(tmp_path):
    w = FakeWorld(tmp_path, pids=[515])
    (tmp_path / "recorder.pid").write_text("42")  # left by a container restart
    w.write_status(515, T0 - pd.Timedelta(seconds=30))
    wd, logs = w.watchdog()
    assert wd.check() == "adopted"
    assert (tmp_path / "recorder.pid").read_text() == "515" and w.started == []


def test_check_restarts_a_dead_recorder_once(tmp_path):
    w = FakeWorld(tmp_path, pids=[])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(minutes=1))
    wd, logs = w.watchdog()
    assert wd.check() == "restarted"
    new = w.started[0][0]
    assert (tmp_path / "recorder.pid").read_text() == str(new) and w.started[0][1][1] == "scripts/26_ais_record.py"
    s = json.loads((tmp_path / "watchdog_status.json").read_text())
    assert s["restarts"] == 1 and s["streak"] == 1 and s["history"][-1]["event"] == "restarted"
    # next check 60 s later: the new recorder has not written status.json yet, the grace covers it
    w.now = T0 + pd.Timedelta(seconds=60)
    assert wd.check() == "ok" and len(w.started) == 1
    # it then writes its own status and keeps going
    w.now = T0 + pd.Timedelta(seconds=120)
    w.write_status(new, w.now - pd.Timedelta(seconds=10))
    assert wd.check() == "ok" and len(w.started) == 1


def test_check_restarts_a_stale_recorder_and_never_runs_two(tmp_path):
    w = FakeWorld(tmp_path, pids=[515])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(minutes=7))  # alive but silent
    wd, logs = w.watchdog()
    assert wd.check() == "restarted"
    assert w.stopped == [515] and len(w.pids) == 1 and len(w.started) == 1


def test_check_backoff_between_consecutive_restarts(tmp_path):
    w = FakeWorld(tmp_path, pids=[])
    wd, logs = w.watchdog()
    restart_times = []
    # a recorder that dies at once every time: check every 30 s for 25 minutes
    for i in range(51):
        w.now = T0 + pd.Timedelta(seconds=30 * i)
        w.pids.clear()  # whatever was started has died
        if wd.check() == "restarted":
            restart_times.append((w.now - T0).total_seconds())
    gaps = [b - a for a, b in zip(restart_times[:-1], restart_times[1:])]
    assert restart_times[0] == 0
    assert gaps[:4] == [60, 120, 300, 600] and all(g == 600 for g in gaps[4:])


def test_check_streak_resets_after_a_healthy_period(tmp_path):
    w = FakeWorld(tmp_path, pids=[])
    wd, logs = w.watchdog()
    assert wd.check() == "restarted" and wd.state.streak == 1
    new = w.started[0][0]
    for minutes in (1, 5, 10, 15):  # healthy from the first check after the restart (grace, then own status)
        w.now = T0 + pd.Timedelta(minutes=minutes)
        if minutes > 1:
            w.write_status(new, w.now - pd.Timedelta(seconds=20))
        wd.check()
    assert wd.state.streak == 1  # 14 min of health: not yet
    w.now = T0 + pd.Timedelta(minutes=16)
    w.write_status(new, w.now - pd.Timedelta(seconds=20))
    wd.check()
    assert wd.state.streak == 0
    # the next failure restarts at once again
    w.pids.clear()
    w.now += pd.Timedelta(seconds=30)
    assert wd.check() == "restarted" and wd.state.restarts == 2


def test_an_unhealthy_spell_restarts_the_health_clock(tmp_path):
    w = FakeWorld(tmp_path, pids=[])
    wd, _ = w.watchdog()
    assert wd.check() == "restarted"
    new = w.started[0][0]
    w.now = T0 + pd.Timedelta(minutes=5)
    w.write_status(new, w.now - pd.Timedelta(seconds=20))
    wd.check()
    w.now = T0 + pd.Timedelta(minutes=12)  # silent for 7 min: unhealthy, restart allowed (60 s after the last)
    assert wd.check() == "restarted" and wd.state.streak == 2


def test_check_kills_a_second_recorder(tmp_path):
    w = FakeWorld(tmp_path, pids=[515, 777])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(seconds=20))
    wd, logs = w.watchdog()
    assert wd.check() == "ok"
    assert w.stopped == [777] and w.pids == {515} and wd.state.extra_recorders_killed == 1


def test_pass_refresh_due_and_retry(tmp_path):
    w = FakeWorld(tmp_path, pids=[515])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(seconds=20))
    passes = tmp_path / "s1_next_passes.json"
    passes.write_text(json.dumps({"generated_utc": (T0 - pd.Timedelta(hours=7)).isoformat()}))
    calls = []

    class Proc:
        returncode = 0

        def poll(self):
            return 0

    wd, logs = w.watchdog(refresh_hours=6, refresh_fn=lambda: calls.append(1) or Proc(), passes_json=passes)
    wd.check()
    assert calls == [1]
    w.now += pd.Timedelta(minutes=1)
    wd.check()  # the refresh finished (exit 0) but generated_utc did not move: retried only after an hour
    assert calls == [1] and wd.state.pass_refresh_last_result.startswith("exit 0")
    passes.write_text(json.dumps({"generated_utc": w.now.isoformat()}))
    w.now += pd.Timedelta(hours=2)
    wd.check()
    assert calls == [1]  # fresh file: nothing to do


# ---------------------------------------------------------------------------------------------------------------
# lock and /proc helpers
# ---------------------------------------------------------------------------------------------------------------

def test_lock_is_exclusive(tmp_path):
    a = wdmod.acquire_lock(tmp_path / "watchdog.lock")
    assert a is not None
    assert wdmod.acquire_lock(tmp_path / "watchdog.lock") is None  # flock is per open file: a second open is refused
    a.close()
    b = wdmod.acquire_lock(tmp_path / "watchdog.lock")
    assert b is not None
    b.close()


def test_is_recorder_pid_rejects_other_processes():
    import os
    assert not wdmod.is_recorder_pid(os.getpid())  # pytest is not the recorder
    assert not wdmod.is_recorder_pid(2 ** 22 + 12345)  # no such pid


def test_state_carries_over_to_the_next_watchdog(tmp_path):
    w = FakeWorld(tmp_path, pids=[])
    wd, _ = w.watchdog()
    assert wd.check() == "restarted"
    wd2, _ = w.watchdog()  # the watchdog itself was restarted
    assert wd2.state.restarts == 1 and wd2.state.streak == 1 and wd2.state.history[-1]["event"] == "restarted"
    assert wd2.state.previous_watchdog["checks"] == 1
    # the backoff still applies across the watchdog restart
    w.pids.clear()
    w.now = T0 + pd.Timedelta(seconds=30)
    assert wd2.check() == "waiting"


# ---------------------------------------------------------------------------------------------------------------
# Live watcher and CNN run supervision (fake process tables; nothing real is started or killed)
# ---------------------------------------------------------------------------------------------------------------

def test_command_line_matchers():
    py = "/env/bin/python"
    assert wdmod.is_watcher_args([py, "scripts/30_live_pass.py", "--watch"])
    assert wdmod.is_watcher_args(["nohup", "nice", "-n", "10", py, "scripts/30_live_pass.py", "--watch"])
    assert not wdmod.is_watcher_args([py, "scripts/30_live_pass.py", "--once"])
    assert not wdmod.is_watcher_args(["bash", "-c", "nohup python scripts/30_live_pass.py --watch"])  # a shell, not it
    assert wdmod.is_cnn_run_args([py, "/repo/scripts/32_cnn_regional.py", "--phases", "low", "--threads=2"])
    assert wdmod.is_cnn_run_args(["nohup", "nice", "-n", "10", py, "/repo/scripts/32_cnn_regional.py", "--phases",
                                  "main,low"])
    for flag in ("--status", "--stop", "--build", "--detach"):
        assert not wdmod.is_cnn_run_args([py, "scripts/32_cnn_regional.py", flag])
    assert not wdmod.is_cnn_run_args(["bash", "-c", "tail -f data/cache/regional_cnn/run.log; 32_cnn_regional.py"])
    assert wdmod.is_recorder_args([py, "scripts/26_ais_record.py", "--hours", "0"])
    assert not wdmod.is_recorder_args([py, "scripts/26_ais_record.py", "--status"])
    assert not wdmod.is_recorder_args([py, "scripts/30_live_pass.py", "--watch"])


def test_parse_cnn_resume():
    assert wdmod.parse_cnn_resume("started pid 4242; log /x/run.log; stop with --stop\n") == ("started", 4242)
    assert wdmod.parse_cnn_resume("every scene of main,low is checkpointed; nothing to start\n") == ("complete", None)
    assert wdmod.parse_cnn_resume("already running (pid 77); log /x/run.log\n") == ("running", 77)
    assert wdmod.parse_cnn_resume("Traceback (most recent call last):\n  ...\nImportError: torch\n") == ("error", None)
    assert wdmod.parse_cnn_resume("") == ("error", None)


def test_cnn_resume_gap_doubles_without_progress():
    assert wdmod.cnn_wait_s(T0, None, 0) == 0
    assert [wdmod.cnn_wait_s(T0, T0, k) for k in (0, 1, 2, 3, 4, 9)] == [1800, 3600, 7200, 14400, 14400, 14400]
    assert wdmod.cnn_wait_s(T0 + pd.Timedelta(minutes=20), T0, 0) == pytest.approx(600)
    assert wdmod.cnn_wait_s(T0 + pd.Timedelta(minutes=31), T0, 0) == 0


class FakeTable:
    """Fake process table for one program. A pid can also be 'alive as another program' (pid reuse): such pids are
    in `others`, never in procs(), and must never be stopped."""

    def __init__(self, pids=(), first_pid=2000):
        self.pids, self.others = set(pids), set()
        self.started, self.stopped = [], []
        self.next_pid = first_pid

    def procs(self):
        return sorted(self.pids)

    def start(self, cmd):
        self.next_pid += 1
        self.pids.add(self.next_pid)
        self.started.append((self.next_pid, list(cmd)))
        return self.next_pid

    def stop(self, pid):
        assert pid not in self.others, "stopped a pid that belongs to another program"
        self.pids.discard(pid)
        self.stopped.append(pid)

    def ops(self):
        return wdmod.Ops(self.procs, self.start, self.stop)


class FakeLaunch:
    """The CNN resume command: answers at once with `text`; on 'started' the run appears in the table."""

    def __init__(self, table, text_fn):
        self.table, self.text_fn = table, text_fn
        self.launches = []

    def start(self, cmd):
        self.launches.append(list(cmd))
        text = self.text_fn(self)
        if text.startswith("started"):
            self.table.next_pid += 1
            self.table.pids.add(self.table.next_pid)
            text = text.format(pid=self.table.next_pid)
        out = text

        class H:
            def poll(self):
                return 0

            def output(self):
                return out
        return H()

    def ops(self):
        return wdmod.Ops(self.table.procs, self.start, self.table.stop)


def _world(tmp_path):
    """A healthy recorder (so only the new supervisors act), with the live and CNN cache folders under tmp_path."""
    w = FakeWorld(tmp_path, pids=[515])
    (tmp_path / "recorder.pid").write_text("515")
    w.write_status(515, T0 - pd.Timedelta(seconds=20))
    (tmp_path / "live").mkdir()
    (tmp_path / "cnn" / "main").mkdir(parents=True)
    (tmp_path / "cnn" / "low").mkdir()
    return w


def _keep_recorder_fresh(w):
    w.write_status(515, w.now - pd.Timedelta(seconds=20))


def _checkpoint(tmp_path, phase, n):
    d = tmp_path / "cnn" / phase
    for _ in range(n):
        (d / f"S1C_scene_{len(list(d.glob('*.parquet'))):03d}.parquet").write_text("x")


def _dog(w, tmp_path, *, watcher=None, cnn=None, **kw):
    def refuse(*_a):
        raise AssertionError("a supervisor that is switched off was used")

    off = wdmod.Ops(refuse, refuse, refuse)
    kw.setdefault("cnn_confirm_checks", 1)  # one check is enough here; the confirmation has its own test
    return w.watchdog(watcher=watcher is not None, watcher_ops=watcher.ops() if watcher else off,
                      cnn=cnn is not None, cnn_ops=cnn.ops() if cnn else off,
                      live_dir=tmp_path / "live", cnn_dir=tmp_path / "cnn", **kw)


def test_flags_off_leave_watcher_and_cnn_alone(tmp_path):
    w = _world(tmp_path)
    wd, logs = _dog(w, tmp_path)  # both off: the refusing fakes would raise if called
    assert wd.check() == "ok"
    assert not any("check failed" in m for m in logs)
    s = json.loads((tmp_path / "watchdog_status.json").read_text())
    assert s["supervise"] == {"recorder": True, "live_watcher": False, "cnn_run": False}
    # only the watcher on: the CNN fakes are never touched
    table = FakeTable()
    wd, logs = _dog(w, tmp_path, watcher=table)
    wd.check()
    assert len(table.started) == 1 and not any("check failed" in m for m in logs)
    s = json.loads((tmp_path / "watchdog_status.json").read_text())
    assert s["supervise"]["live_watcher"] and not s["supervise"]["cnn_run"]


def test_watcher_alive_is_left_alone_and_pid_file_corrected(tmp_path):
    w = _world(tmp_path)
    table = FakeTable(pids=[1852])
    (tmp_path / "live" / "watch.pid").write_text("167")  # left by a container restart
    wd, logs = _dog(w, tmp_path, watcher=table)
    wd.check()
    assert table.started == [] and table.stopped == []
    assert (tmp_path / "live" / "watch.pid").read_text() == "1852"
    assert wd.state.watcher["last_decision"].startswith("ok")


def test_watcher_pid_reuse_does_not_fool_it(tmp_path):
    # after a container restart watch.pid holds 165, which is now the recorder (alive, but not a watcher)
    w = _world(tmp_path)
    table = FakeTable()
    table.others.add(165)
    (tmp_path / "live" / "watch.pid").write_text("165")
    wd, logs = _dog(w, tmp_path, watcher=table)
    wd.check()
    new = table.started[0][0]
    assert table.started[0][1][:4] == ["nohup", "nice", "-n", "10"] and table.started[0][1][-2:] == [
        "scripts/30_live_pass.py", "--watch"]
    assert table.stopped == [] and (tmp_path / "live" / "watch.pid").read_text() == str(new)
    assert "watch.pid 165 is not a live watcher" in wd.state.watcher["last_restart_reason"]
    assert any(e["event"] == "watcher_restarted" for e in wd.state.history)


def test_watcher_killed_is_restarted_at_the_next_check(tmp_path):
    w = _world(tmp_path)
    table = FakeTable(pids=[1852])
    (tmp_path / "live" / "watch.pid").write_text("1852")
    wd, logs = _dog(w, tmp_path, watcher=table)
    wd.check()
    table.pids.clear()  # kill -TERM 1852
    w.now += pd.Timedelta(seconds=60)
    _keep_recorder_fresh(w)
    wd.check()
    assert len(table.started) == 1 and wd.state.watcher["restarts"] == 1
    assert any("live watcher: restart 1" in m for m in logs)


def test_watcher_backoff_and_streak_reset(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    wd, logs = _dog(w, tmp_path, watcher=table)
    times = []
    for i in range(51):  # a watcher that dies at once, checked every 30 s for 25 min
        w.now = T0 + pd.Timedelta(seconds=30 * i)
        _keep_recorder_fresh(w)
        table.pids.clear()
        n = len(table.started)
        wd.check()
        if len(table.started) > n:
            times.append((w.now - T0).total_seconds())
    gaps = [b - a for a, b in zip(times[:-1], times[1:])]
    assert times[0] == 0 and gaps[:4] == [60, 120, 300, 600] and all(g == 600 for g in gaps[4:])
    # now it stays up (the first minutes may still be inside the 10 min backoff): after 15 min alive the streak
    # resets and the next death restarts at once
    for minutes in range(1, 31):
        w.now += pd.Timedelta(minutes=1)
        _keep_recorder_fresh(w)
        wd.check()
    assert wd.state.watcher["streak"] == 0
    table.pids.clear()
    w.now += pd.Timedelta(seconds=30)
    _keep_recorder_fresh(w)
    n = len(table.started)
    wd.check()
    assert len(table.started) == n + 1


def test_watcher_second_copy_is_stopped(tmp_path):
    w = _world(tmp_path)
    table = FakeTable(pids=[1852, 1900])
    (tmp_path / "live" / "watch.pid").write_text("1852")
    wd, _ = _dog(w, tmp_path, watcher=table)
    wd.check()
    assert table.stopped == [1900] and table.pids == {1852} and table.started == []


def test_watcher_state_carries_over_a_watchdog_restart(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    wd, _ = _dog(w, tmp_path, watcher=table)
    wd.check()
    wd2, _ = _dog(w, tmp_path, watcher=table)  # new watchdog code, same status file
    assert wd2.state.watcher["restarts"] == 1 and wd2.state.watcher["streak"] == 1
    table.pids.clear()
    w.now += pd.Timedelta(seconds=30)
    _keep_recorder_fresh(w)
    wd2.check()
    assert len(table.started) == 1 and wd2.state.watcher["last_decision"].startswith("waiting")


def test_cnn_running_is_left_alone_and_adopted(tmp_path):
    w = _world(tmp_path)
    table = FakeTable(pids=[10608])
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    _checkpoint(tmp_path, "main", 3)
    wd, logs = _dog(w, tmp_path, cnn=launch)
    wd.check()
    assert launch.launches == [] and (tmp_path / "cnn" / "run.pid").read_text() == "10608"
    assert wd.state.cnn["last_decision"] == "running: pid 10608, 3 scene checkpoints"


def test_cnn_pid_reuse_removes_the_stale_pid_file_and_resumes(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    table.others.add(165)
    (tmp_path / "cnn" / "run.pid").write_text("165")  # script 32 alone would think a run is alive
    launch = FakeLaunch(table, lambda _l: "started pid {pid}; log run.log")
    wd, logs = _dog(w, tmp_path, cnn=launch)
    wd.check()
    assert not (tmp_path / "cnn" / "run.pid").exists() or (tmp_path / "cnn" / "run.pid").read_text() != "165"
    assert len(launch.launches) == 1 and table.stopped == []
    cmd = launch.launches[0]
    assert cmd[:3] == ["nice", "-n", "19"] and cmd[4:] == ["scripts/32_cnn_regional.py", "--detach", "--if-incomplete",
                                                           "--phases", "main,low"]
    w.now += pd.Timedelta(seconds=60)
    _keep_recorder_fresh(w)
    wd.check()  # the answer is read, the new run is seen alive
    assert wd.state.cnn["starts"] == 1 and wd.state.cnn["last_decision"].startswith("running")


def test_cnn_crash_loop_is_resumed_at_most_every_30_min_with_doubling(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    wd, logs = _dog(w, tmp_path, cnn=launch)
    times = []
    for i in range(12 * 60):  # 12 h, one check a minute; every run dies before its first checkpoint
        w.now = T0 + pd.Timedelta(minutes=i)
        _keep_recorder_fresh(w)
        table.pids.clear()
        n = len(launch.launches)
        wd.check()
        if len(launch.launches) > n:
            times.append((w.now - T0).total_seconds() / 60)
    gaps = [b - a for a, b in zip(times[:-1], times[1:])]
    assert times[0] == 0 and min(gaps) >= 30
    assert gaps[:3] == [60, 120, 240] and all(g == 240 for g in gaps[3:])  # 30 min doubled per run without progress
    assert wd.state.cnn["no_progress"] >= 4


def test_cnn_progress_keeps_the_30_min_gap(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    wd, _ = _dog(w, tmp_path, cnn=launch)
    wd.check()
    assert len(launch.launches) == 1
    for k in range(1, 4):  # each run scores a scene, then dies 40 min after its start
        w.now = T0 + pd.Timedelta(minutes=40 * k)
        _keep_recorder_fresh(w)
        _checkpoint(tmp_path, "low", 1)
        table.pids.clear()
        wd.check()
        assert len(launch.launches) == k + 1 and wd.state.cnn["no_progress"] == 0


def test_cnn_complete_stops_resuming(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    launch = FakeLaunch(table, lambda _l: "every scene of main,low is checkpointed; nothing to start")
    wd, logs = _dog(w, tmp_path, cnn=launch)
    wd.check()
    w.now += pd.Timedelta(seconds=60)
    _keep_recorder_fresh(w)
    wd.check()
    assert wd.state.cnn["complete"] and wd.state.cnn["last_decision"].startswith("complete")
    w.now += pd.Timedelta(hours=10)
    _keep_recorder_fresh(w)
    wd.check()
    assert len(launch.launches) == 1
    # a new watchdog asks once more (a new phase or a lost checkpoint would show up), then stops again
    wd2, _ = _dog(w, tmp_path, cnn=launch)
    assert not wd2.state.cnn.get("complete")
    wd2.check()
    assert len(launch.launches) == 2


def test_cnn_failed_resume_counts_as_no_progress(tmp_path):
    w = _world(tmp_path)
    table = FakeTable()
    launch = FakeLaunch(table, lambda _l: "Traceback (most recent call last):\nOSError: disk full")
    wd, logs = _dog(w, tmp_path, cnn=launch)
    wd.check()
    w.now += pd.Timedelta(minutes=1)
    _keep_recorder_fresh(w)
    wd.check()
    assert wd.state.cnn["failures"] == 1 and wd.state.cnn["no_progress"] == 1
    assert any("resume command failed" in m for m in logs)
    w.now = T0 + pd.Timedelta(minutes=59)
    _keep_recorder_fresh(w)
    wd.check()
    assert len(launch.launches) == 1  # 60 min after a failed resume, not 30
    w.now = T0 + pd.Timedelta(minutes=60)
    _keep_recorder_fresh(w)
    wd.check()
    assert len(launch.launches) == 2


def test_cnn_second_run_is_stopped(tmp_path):
    w = _world(tmp_path)
    table = FakeTable(pids=[10608, 10700])
    (tmp_path / "cnn" / "run.pid").write_text("10700")
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    wd, _ = _dog(w, tmp_path, cnn=launch)
    wd.check()
    assert table.stopped == [10608] and table.pids == {10700} and launch.launches == []


def test_a_failing_supervisor_does_not_stop_the_recorder_check(tmp_path):
    w = _world(tmp_path)

    def boom():
        raise RuntimeError("proc table unreadable")

    bad = wdmod.Ops(boom, boom, boom)
    wd, logs = w.watchdog(watcher=True, watcher_ops=bad, live_dir=tmp_path / "live", cnn=False)
    assert wd.check() == "ok"
    assert any("live watcher check failed: RuntimeError" in m for m in logs)
    assert json.loads((tmp_path / "watchdog_status.json").read_text())["checks"] == 1


def test_real_processes_pid_matches_find_and_stop(tmp_path):
    """A real child with a marker on its command line (not a name the running watchdog supervises)."""
    import subprocess
    import time as _t

    marker = f"dv_watchdog_test_{tmp_path.name}"
    match = lambda args: marker in args  # noqa: E731
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
    try:
        for _ in range(50):
            if wdmod.pid_matches(p.pid, match):
                break
            _t.sleep(0.05)
        assert wdmod.pid_matches(p.pid, match) and p.pid in wdmod.find_processes(match)
        assert not wdmod.pid_matches(p.pid, wdmod.is_watcher_args)
        wdmod.stop_process(p.pid, term_wait_s=5, alive=lambda q: wdmod.pid_matches(q, match))
        p.wait(timeout=5)  # an exiting process loses its command line before it is reaped: stop_process returns then
        assert not wdmod.pid_matches(p.pid, match) and p.pid not in wdmod.find_processes(match)
    finally:
        if p.poll() is None:
            p.kill()


def test_cnn_missing_run_is_confirmed_before_acting(tmp_path):
    # the run re-executes itself in place; one scan that misses it must not remove run.pid or resume a second run
    w = _world(tmp_path)
    table = FakeTable(pids=[10608])
    (tmp_path / "cnn" / "run.pid").write_text("10608")
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    wd, logs = _dog(w, tmp_path, cnn=launch, cnn_confirm_checks=2)
    assert wd._check_cnn(w.now) == "running"
    table.pids.clear()  # one scan misses it
    assert wd._check_cnn(w.now) == "unconfirmed"
    assert (tmp_path / "cnn" / "run.pid").read_text() == "10608" and launch.launches == []
    table.pids.add(10608)  # seen again: nothing happened
    assert wd._check_cnn(w.now) == "running" and wd.state.cnn["missing_checks"] == 0
    table.pids.clear()  # really gone: acted on at the second check
    assert wd._check_cnn(w.now) == "unconfirmed"
    w.now += pd.Timedelta(seconds=60)
    assert wd._check_cnn(w.now) == "launching"
    assert not (tmp_path / "cnn" / "run.pid").exists() and len(launch.launches) == 1


def test_cnn_resume_command_matcher():
    py = "/env/bin/python"
    hook = ["timeout", "600", "nice", "-n", "19", py, "scripts/32_cnn_regional.py", "--detach", "--if-incomplete",
            "--phases", "main,low"]
    assert wdmod.is_cnn_resume_args(hook) and wdmod.is_cnn_resume_args(hook[3:])
    assert not wdmod.is_cnn_run_args(hook)
    assert not wdmod.is_cnn_resume_args([py, "/repo/scripts/32_cnn_regional.py", "--phases", "main,low"])
    assert not wdmod.is_cnn_resume_args(["bash", "-c", "python scripts/32_cnn_regional.py --detach"])


def test_cnn_waits_for_a_resume_started_elsewhere(tmp_path):
    # the session hook's resume is still listing the scenes (about 50 s on a cold disk): the watchdog must not start a
    # second resume next to it, and starts counting missing checks again only once it has gone
    w = _world(tmp_path)
    table = FakeTable()
    launch = FakeLaunch(table, lambda _l: "started pid {pid}")
    busy = [(4321, 20.0)]
    ops = wdmod.Ops(table.procs, launch.start, table.stop, lambda: list(busy))
    wd, logs = _dog(w, tmp_path, cnn_confirm_checks=2)
    wd.cnn, wd.cnn_ops = True, ops
    for _ in range(3):
        assert wd._check_cnn(w.now) == "resume_elsewhere"
        w.now += pd.Timedelta(seconds=60)
    assert launch.launches == [] and "pid 4321" in wd.state.cnn["last_decision"]
    assert sum("waiting for resume command pid 4321" in m for m in logs) == 1  # logged once, not every check
    busy.clear()
    table.pids.add(5000)  # the hook's resume started the run
    assert wd._check_cnn(w.now) == "running" and launch.launches == []
    # a resume older than the launch timeout is hung: it is ignored (never signalled) and supervision goes on
    table.pids.clear()
    busy.append((4400, wdmod.CNN_LAUNCH_TIMEOUT_S + 5))
    assert wd._check_cnn(w.now) == "unconfirmed"
    w.now += pd.Timedelta(seconds=60)
    assert wd._check_cnn(w.now) == "launching" and len(launch.launches) == 1 and table.stopped == []


def test_process_age_of_a_real_child():
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        age = wdmod.process_age_s(p.pid)
        assert age is not None and 0 <= age < 30
        mine = wdmod.process_age_s(wdmod.os.getpid())
        assert mine is not None and mine >= age
    finally:
        p.kill()
        p.wait()
    assert wdmod.process_age_s(2 ** 22 + 7) is None  # above pid_max: never a live pid


def test_session_hook_resumes_the_cnn_run_and_is_safe_to_repeat():
    """Static checks of scripts/session_start.sh (running it would start the real processes)."""
    import subprocess

    hook = Path(__file__).resolve().parents[1] / "scripts" / "session_start.sh"
    assert subprocess.run(["bash", "-n", str(hook)], capture_output=True).returncode == 0
    text = hook.read_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    # the CNN resume is the idempotent form, under nice 19, in the background and bounded, and only with no run alive
    assert 'scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low' in body
    assert "nohup setsid timeout 600 nice -n 19" in body
    pat = re.search(r"pgrep -f '([^']*32_cnn_regional[^']*)'", body).group(1)
    # the pattern finds a run and a resume command (also under nohup, nice or timeout), not a shell that mentions it
    for line, hit in (("/env/bin/python /repo/scripts/32_cnn_regional.py --phases main,low", True),
                      ("nohup nice -n 10 /env/bin/python3.11 /repo/scripts/32_cnn_regional.py --phases low", True),
                      ("timeout 600 nice -n 19 /env/bin/python scripts/32_cnn_regional.py --detach --if-incomplete", True),
                      ("bash -c tail data/cache/regional_cnn/run.log; pgrep -af 32_cnn_regional", False),
                      ("/env/bin/python scripts/32_cnn_regionalXpy", False)):
        assert bool(re.search(pat, line)) == hit, line
    # every start is guarded, and the watchdog comes last and is bounded
    assert 'pgrep -f "scripts/30_live_pass.py --watch"' in body
    assert lines[-2].startswith("timeout 45") and "29_ais_watchdog.py --ensure" in lines[-2] and lines[-1] == "exit 0"
    assert "AISSTREAM_API_KEY" not in body and "GFW_API_TOKEN" not in body  # the key stays in .env
