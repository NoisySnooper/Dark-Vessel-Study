"""Offline tests for the AIS recorder watchdog (scripts/29_ais_watchdog.py): health decision, restart backoff,
single-recorder rule, adoption, lock. Fake status files, fake pids and fake process tables; no network, no real
recorder is started or killed.
"""

from __future__ import annotations

import importlib.util
import json
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
