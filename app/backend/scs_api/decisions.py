"""Lead decisions (append-only JSONL) and contact labels (append-only CSV), spec section 4.1 and contract 3.5.

Transitions: new -> reviewing or any closed state; reviewing -> any closed state; closed -> reviewing (reopen, note
required). Anything else is 409. Required input (422 when missing): closed_explained and closed_false_alarm need a
reason from their picklist ("other" also needs a note); closed_unexplained needs a note. Nothing is ever deleted.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import threading
from pathlib import Path

from . import APP_VERSION
from .envelope import ApiError
from .records import utc_now

CLOSED = ("closed_explained", "closed_unexplained", "closed_false_alarm")
ALLOWED = {"new": {"reviewing", *CLOSED}, "reviewing": set(CLOSED), **{c: {"reviewing"} for c in CLOSED}}
EXPLAINED_REASONS = ["no carriage requirement (size or type)", "VMS fleet", "outside AIS reach", "weather or sea clutter",
                     "fixed structure", "fishing lights", "pilot or supply transfer", "other"]
FALSE_ALARM_REASONS = ["sea clutter", "rain cell", "fixed structure", "ambiguity or sidelobe", "duplicate", "other"]
REASONS = {"closed_explained": EXPLAINED_REASONS, "closed_false_alarm": FALSE_ALARM_REASONS}
LABELS = ("vessel", "structure", "clutter", "unsure")
LABEL_COLUMNS = ["det_id", "label", "user", "time_utc", "note", "build"]


def _norm(s: str) -> str:
    s = s.strip().lower().replace("_", " ")
    if s.endswith("(note required)"):
        s = s[: -len("(note required)")].strip()
    return s


def canonical_reason(to_state: str, reason: str | None) -> str | None:
    """The picklist value for a reason as the UI sends it (label, snake_case or with '(note required)'), else None."""
    if not reason:
        return None
    n = _norm(reason)
    for r in REASONS.get(to_state, []):
        if _norm(r) == n or _norm(r).split(" (")[0] == n:
            return r
    return None


def validate(from_state: str, to_state: str, reason: str | None, note: str | None) -> str | None:
    """Raise ApiError 409 or 422 per the rules above; return the canonical reason."""
    if to_state not in ALLOWED.get(from_state, set()):
        raise ApiError(409, "transition_not_allowed",
                       f"a lead in state {from_state} cannot move to {to_state}; allowed: {sorted(ALLOWED.get(from_state, []))}")
    note = (note or "").strip()
    if to_state in REASONS:
        r = canonical_reason(to_state, reason)
        if r is None:
            raise ApiError(422, "reason_required",
                           f"{to_state} needs a reason from: {', '.join(REASONS[to_state])}")
        if r == "other" and not note:
            raise ApiError(422, "note_required", "reason 'other' needs a note")
        return r
    if to_state == "closed_unexplained" and not note:
        raise ApiError(422, "note_required", "closed_unexplained needs a note (evidence reviewed, no explanation found)")
    if to_state == "reviewing" and from_state in CLOSED and not note:
        raise ApiError(422, "note_required", "reopening a closed lead needs a note")
    return None


class DecisionLog:
    """Append-only JSONL log; re-read when its mtime changes. One line per decision, never rewritten."""

    def __init__(self, path: Path, build: str, guard=None):
        self.path = Path(path)
        self.build = build
        self.guard = guard or (lambda p: p)
        self._lock = threading.Lock()
        self._mtime = None
        self._by_lead: dict[str, list[dict]] = {}
        self.bad_lines = 0

    def _load(self):
        p = self.guard(self.path)
        m = p.stat().st_mtime_ns if p.exists() else None
        if m == self._mtime:
            return
        by, bad = {}, 0
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    by.setdefault(str(d["lead_id"]), []).append(d)
                except (json.JSONDecodeError, KeyError, TypeError):
                    bad += 1
        self._by_lead, self.bad_lines, self._mtime = by, bad, m

    def all(self) -> dict[str, list[dict]]:
        with self._lock:
            self._load()
            return self._by_lead

    def history(self, lead_id: str) -> list[dict]:
        return list(self.all().get(lead_id, []))

    def count(self) -> int:
        return sum(len(v) for v in self.all().values())

    def append(self, lead_id: str, from_state: str, to_state: str, reason: str | None, note: str | None, user: str) -> dict:
        d = {"lead_id": lead_id, "time_utc": utc_now(), "user": user, "from_state": from_state, "to_state": to_state,
             "reason": reason, "note": (note or None), "build": self.build, "app_version": APP_VERSION}
        p = self.guard(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(d, ensure_ascii=False) + "\n"
        with self._lock, open(p, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return d


def append_label(path: Path, det_id: str, label: str, user: str, note: str | None, build: str) -> dict:
    """Append one owner label (`det_id,label,user,time_utc` as the demo page wrote them, plus note and build)."""
    if label not in LABELS:
        raise ApiError(422, "bad_label", f"label must be one of {', '.join(LABELS)}")
    row = {"det_id": det_id, "label": label, "user": user, "time_utc": utc_now(), "note": note or "", "build": build}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            new = os.fstat(f.fileno()).st_size == 0
            w = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return row
