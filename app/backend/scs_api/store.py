"""In-memory store of every product file: loads at start (in parallel), reloads a loader when one of its files changes
(mtime), links objects (contact lead_ids, vessel contacts_matched, light site and same-night contacts), and builds the
contract records and list queries the routes serve.

A reload builds a new State on the side (the changed loaders re-read, the others carried over with their caches) and
swaps it in with one assignment. Each request pins the State current when it starts (Store.pin, called by the app's
request dependency), so a request never mixes a new frame with an old index. The aisstream hour files, which the live
recorder rewrites every minute, feed only the `tracks` loader; it reloads in the background at most once per
`Settings.tracks_reload_s`, and no request waits for it.
"""

from __future__ import annotations

import bisect
import contextvars
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import APP_VERSION, CONTRACT_VERSION
from .catalog import Catalog
from .config import (AISSTREAM_LABEL, ATTRIBUTION, CAVEAT_SHORT, DATA_CREDIT, PRODUCT_CAVEAT, RESEARCH_LABEL, SHIPPING_LABEL,
                     Settings)
from .decisions import DecisionLog
from .envelope import ApiError
from .loaders import cells as L_cells
from .loaders import contacts as L_contacts
from .loaders import context as L_context
from .loaders import events as L_events
from .loaders import leads as L_leads
from .loaders import lights as L_lights
from .loaders import passes as L_passes
from .loaders import vessels as L_vessels
from .loaders.common import MetricTree, cell_ids, csv_list, mask_window, obj_cache, parse_cell_id
from .loaders.geo import Geo
from .loaders.rasters import Rasters
from .models import (AIS_STATUS_VALUES, LEAD_STATES, LIGHT_SUMMARY, VESSEL_SUMMARY, build_models, container_defaults,
                     field_names, int_fields)
from .records import build_record, clean, iso_z, json_list, json_map, prov_time, utc_now
from .sources import registry

DEPS = {
    "contacts": {"live_contacts", "live_about", "live_weather", "live_review", "regional_contacts", "regional_scenes",
                 "regional_identity", "structures", "camau_contacts", "regional_cnn", "weather", "optical"},
    "vessels": {"ais_vessels", "ais_summary", "live_ais_only", "gfw_vessels", "gfw_events_vessels"},
    "tracks": {"ais_positions", "gfw_presence_passes"},
    "lights": {"lights", "sites", "viirs_nights"},
    "lights_extra": {"lights_all"},
    "events": {"events_open", "gfw_gaps", "gfw_encounters", "gfw_loitering", "gfw_port_visits"},
    "leads": {"leads_open", "leads_open_detail", "leads_research", "leads_research_gpkg"},
    "passes": {"pass_plan", "pass_plan_layer", "live_scenes", "live_summary", "live_ais_only", "regional_scenes"},
    "cells": {"cells_static", "cells_daily", "cells_pass", "radar_vs_gfw", "ocean_static_summary"},
    "context": {"object_context", "expected_activity"},
    "rasters": {"rasters", "rasters_research"},
}
# loaders that read another loader's data of the same State: contacts feed vessels and passes; leads and lights feed the
# evidence lights (the L7 lights the lean light file lacks)
AFTER = {"contacts": {"vessels", "passes"}, "leads": {"lights_extra"}, "lights": {"lights_extra"}}
ORDER = ["contacts", "lights", "events", "leads", "cells", "context", "rasters", "tracks", "vessels", "passes", "lights_extra"]
SECOND = ("vessels", "passes")  # loaded after the first group, from the new contacts frame
BACKGROUND = {"context", "lights_extra", "events"}  # loaded in a thread after the others, in BG_ORDER (held until
# release_background when Settings.background_delay_s is set); the first request that needs them releases the hold and
# waits: object context and expected activity (360k and 87k rows; Contact, Light and Cell records), the evidence lights
# (the L7 lights outside the lean file, read from the leads and lights of the same State), research events (610k rows)
BG_ORDER = ["context", "lights_extra", "events"]
QUIET = {"tracks"}  # reloaded in the background, at most once per Settings.tracks_reload_s; requests never wait for them
LINKED = {"contacts", "leads", "vessels"}  # a reload of any of these rebuilds the cross-object indexes

_PINNED: contextvars.ContextVar = contextvars.ContextVar("scs_pinned_state", default=None)


class DataMap(dict):
    """Loaded data by loader name; a loader still running in the background is waited for on first access."""

    def __init__(self):
        super().__init__()
        self.pending: dict = {}
        self.on_wait = None  # called before waiting on a pending load (releases held background work)

    def __getitem__(self, key):
        fut = self.pending.get(key)
        if fut is not None:
            if not fut.done() and self.on_wait is not None:
                self.on_wait()
            dict.__setitem__(self, key, fut.result())
            self.pending.pop(key, None)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        return self[key] if key in self or key in self.pending else default

    def peek(self, key):
        """The loaded value, or None while its background load is still running (never waits)."""
        fut = self.pending.get(key)
        if fut is not None and not fut.done():
            return None
        return self.get(key)

    def copy_map(self) -> "DataMap":
        out = DataMap()
        dict.update(out, dict.items(self))
        out.pending = dict(self.pending)
        out.on_wait = self.on_wait
        return out


class State:
    """One consistent load: every loader's data plus the cross-object indexes built from them. Never changed after it is
    swapped in, except that a background loader resolves in place and caches fill on the data objects."""

    def __init__(self, data: DataMap):
        self.data = data
        self.contact_leads: dict = {}
        self.vessel_contacts: dict = {}
        self.product_mask = np.zeros(0, bool)
        self.loaded_utc = None
        self.load_seconds = None

    def derive(self, results: dict) -> "State":
        """A new State with `results` (loader name to data, or to a Future for a background load) in place of the old
        values and everything else, links included, carried over."""
        data = self.data.copy_map()
        for n, v in results.items():
            data.pending.pop(n, None)
            dict.pop(data, n, None)
            if isinstance(v, Future):
                data.pending[n] = v
            else:
                dict.__setitem__(data, n, v)
        new = State(data)
        new.contact_leads, new.vessel_contacts, new.product_mask = self.contact_leads, self.vessel_contacts, self.product_mask
        new.loaded_utc, new.load_seconds = self.loaded_utc, self.load_seconds
        return new


class PrefixIndex:
    """Sorted ids for exact and prefix lookups by binary search (the Omnibar)."""

    def __init__(self, ids):
        self.ids = sorted(str(x) for x in ids)

    def find(self, q: str, n: int = 10) -> list[str]:
        a = bisect.bisect_left(self.ids, q)
        out = []
        for x in self.ids[a:a + n]:
            if not x.startswith(q):
                break
            out.append(x)
        return out


# rules of the live file's `about` layer that /meta repeats (the Contact and Pass pages quote them)
LIVE_RULES = ("dark_lead_rule", "no_coverage_rule", "match_quality_rule", "ais_status_rule", "distance_gate", "ais_window",
              "azimuth_correction", "ambiguity", "review_note", "ais_only_layer", "weather")
CONTACT_SORTS = {"acq_utc": "_t", "length_est_m": "length_est_m", "cnn_score": "cnn_score", "det_id": "det_id"}
LEAD_SORTS = {"priority": "priority", "time_utc": "_t", "lead_id": "lead_id"}


def _git_hash(repo) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _bool_param(v: str | None):
    if v is None or v == "":
        return None
    s = str(v).lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise ApiError(422, "bad_bool", f"expected true or false, not {v!r}")


def _order(df: pd.DataFrame, positions: np.ndarray, sort: str | None, allowed: dict, default: str) -> np.ndarray:
    key = (sort or default).strip()
    desc = key.startswith("-")
    name = key.lstrip("-+")
    if name not in allowed:
        raise ApiError(422, "bad_sort", f"sort must be one of {', '.join(sorted(allowed))} (prefix - for descending)")
    col = df[allowed[name]].iloc[positions]
    if pd.api.types.is_datetime64_any_dtype(col):
        vals = col.astype("int64").to_numpy().astype(float)
        vals[col.isna().to_numpy()] = np.nan
    elif pd.api.types.is_numeric_dtype(col):
        vals = col.to_numpy(dtype=float, na_value=np.nan)
    else:
        o = np.argsort(col.astype(str).to_numpy(), kind="stable")
        return positions[o[::-1]] if desc else positions[o]
    nan = np.isnan(vals)
    v = np.where(nan, np.inf if not desc else -np.inf, vals)
    o = np.argsort(-v if desc else v, kind="stable")
    return positions[o]


class Store:
    def __init__(self, settings: Settings, catalog: Catalog | None = None):
        self.settings = settings
        self.cat = catalog or Catalog(settings)
        self.models = build_models(settings.build)
        self.f = {k: field_names(m) for k, m in self.models.items()}
        self.d = {k: container_defaults(m) for k, m in self.models.items()}
        self.i = {k: int_fields(m) for k, m in self.models.items()}
        self.decisions = DecisionLog(settings.decisions_path, settings.build, guard=self.cat.guard)
        self.git_hash = _git_hash(settings.repo_root)
        self._lock = threading.Lock()  # one check or reload at a time; readers never take it
        self.decision_lock = threading.Lock()
        self._last_check = 0.0
        self._sig: dict = {}
        self._failed_sig: dict | None = None
        self.reload_error: str | None = None
        self._loaded_at: dict[str, float] = {}
        self._chips: tuple | None = None
        self._quiet: Future | None = None
        self._bg = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scs-bg")
        self._warm_ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scs-warm")
        self._quiet_ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scs-quiet")
        self._go = threading.Event()  # background work waits for it (serve.py releases it once the server listens)
        if settings.background_delay_s is None:
            self._go.set()
        self._state = State(DataMap())
        self.geo = Geo(self.cat, settings)
        self.load_all()

    def release_background(self):
        """Let the held background work start (serve.py after the server listens; any request that needs its data)."""
        self._go.set()

    def _hold(self):
        self._go.wait(timeout=60.0)  # never forever: without a release the work starts after a minute

    # ------------------------------------------------------------ the pinned State
    @property
    def st(self) -> State:
        """The State this request pinned, else the current one."""
        p = _PINNED.get()
        return p[1] if p is not None and p[0] is self else self._state

    def pin(self) -> State:
        """Pin the current State for the rest of this request (the app calls it in the request dependency)."""
        s = self._state
        _PINNED.set((self, s))
        return s

    @property
    def data(self) -> DataMap:
        return self.st.data

    @property
    def product_mask(self) -> np.ndarray:
        return self.st.product_mask

    @property
    def contact_leads(self) -> dict:
        return self.st.contact_leads

    @property
    def vessel_contacts(self) -> dict:
        return self.st.vessel_contacts

    @property
    def load_seconds(self):
        return self.st.load_seconds

    @property
    def loaded_utc(self):
        return self.st.loaded_utc

    # ------------------------------------------------------------ loading
    def _load_one(self, name: str, data: DataMap, changed: set | None = None):
        s, c = self.settings, self.cat
        if name == "contacts":
            return L_contacts.load(c, s)
        if name == "vessels":
            cd = data.get("contacts")
            return L_vessels.load(c, s, cd.df if cd else None, self.f["vessel"])
        if name == "tracks":
            return L_vessels.load_tracks(c, s, data.peek("tracks") if "tracks" in data else None, changed)
        if name == "lights":
            return L_lights.load(c, s, self.f["light"], self.f["site"])
        if name == "lights_extra":
            return L_lights.load_evidence(c, s, data.get("leads"), data.get("lights"), self.f["light"])
        if name == "context":
            return L_context.load(c, s)
        if name == "events":
            return L_events.load(c, s)
        if name == "leads":
            return L_leads.load(c, s, self.f["lead"])
        if name == "passes":
            cd = data.get("contacts")
            return L_passes.load(c, s, cd.df if cd else None)
        if name == "cells":
            return L_cells.load(c, s)
        if name == "rasters":
            return Rasters(c, s)
        raise KeyError(name)

    def _build(self, names: set[str], base: State | None, changed: set | None = None) -> State:
        """A new State with `names` re-read and every other loader carried over from `base`, built off to the side."""
        t = time.time()
        new = base.derive({}) if base is not None else State(DataMap())
        data = new.data
        data.on_wait = self.release_background
        later = []
        for n in [b for b in BG_ORDER if b in names]:  # a placeholder now; the load starts once the rest is in
            fut = Future()
            data.pending[n] = fut
            dict.pop(data, n, None)
            later.append((n, fut))
        first = [n for n in ORDER if n in names and n not in SECOND and n not in BACKGROUND]
        with ThreadPoolExecutor(max_workers=4) as ex:
            ex.submit(self.cat.entries)  # primes the row counts /meta reports (cached by mtime)
            for n, res in zip(first, ex.map(lambda n: self._load_one(n, data, changed), first)):
                data.pending.pop(n, None)
                dict.__setitem__(data, n, res)
            second = [n for n in SECOND if n in names]
            for n, res in zip(second, ex.map(lambda n: self._load_one(n, data, changed), second)):
                data.pending.pop(n, None)
                dict.__setitem__(data, n, res)
        if base is None or names & LINKED:
            self._link(new)
        for n, fut in later:
            self._bg.submit(self._fill, fut, n, data)
        new.load_seconds = round(time.time() - t, 2)
        new.loaded_utc = utc_now()
        return new

    def _fill(self, fut: Future, name: str, data: DataMap):
        self._hold()
        try:
            fut.set_result(self._load_one(name, data))
        except BaseException as e:  # noqa: BLE001  (the request that needs it gets the error, as an enveloped 500)
            fut.set_exception(e)

    def _swap(self, new: State, sig: dict, names: set[str]):
        now = time.time()
        self._state = new  # the one assignment readers see
        self._sig = sig
        for n in names:
            self._loaded_at[n] = now
        self._warm_ex.submit(contextvars.Context().run, self._warm, new)

    def load_all(self):
        """Load every loader (at start)."""
        with self._lock:
            sig = self.cat.signature()
            self._swap(self._build(set(ORDER), None), sig, set(ORDER))

    def check_due(self) -> bool:
        return time.time() - self._last_check >= self.settings.check_interval_s

    def ensure_fresh(self):
        """Reload the loaders whose files changed (mtime), at most once per check interval. Only one thread checks or
        reloads at a time; the others go on with the current State instead of waiting."""
        iv = self.settings.check_interval_s
        if time.time() - self._last_check < iv:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            if iv and time.time() - self._last_check < iv:
                return  # another request checked a moment ago
            self._last_check = time.time()
            sig = self.cat.signature()
            if sig == self._sig or sig == self._failed_sig:
                return  # nothing changed, or the same files already failed to load (retried once they change again)
            changed = {k for k in set(sig) | set(self._sig) if sig.get(k) != self._sig.get(k)}
            names = {n for n, deps in DEPS.items() if deps & changed}
            for n in list(names):
                names |= AFTER.get(n, set())
            quiet = names & QUIET
            names -= QUIET
            new_sig = dict(sig)
            if quiet:  # these files keep their old signature until the background reload swaps the new data in
                hold = set().union(*(DEPS[n] for n in quiet))
                for k in hold:
                    new_sig[k] = self._sig.get(k)
                self._reload_quiet(quiet, {k: sig.get(k) for k in hold}, changed)
            if names:
                try:
                    new = self._build(names, self._state, changed)
                except Exception as e:  # noqa: BLE001  (a file caught mid-write: keep serving the current State)
                    self._failed_sig = sig
                    self.reload_error = f"{utc_now()} reload of {', '.join(sorted(names))} failed: {type(e).__name__}: {e}"
                    print(f"scs_api: {self.reload_error}; still serving the data loaded at {self._state.loaded_utc}",
                          file=sys.stderr, flush=True)
                    return
                self._failed_sig = None
                self.reload_error = None
                self._swap(new, new_sig, names)
            else:
                self._sig = new_sig
        finally:
            self._lock.release()

    def _reload_quiet(self, names: set[str], sig_part: dict, changed: set):
        """Submit a background reload of `names` unless one is running or the last one is younger than the interval."""
        if self._quiet is not None and not self._quiet.done():
            return
        now = time.time()
        if now - min(self._loaded_at.get(n, 0.0) for n in names) < self.settings.tracks_reload_s:
            return
        for n in names:
            self._loaded_at[n] = now
        self._quiet = self._quiet_ex.submit(contextvars.Context().run, self._quiet_job, names, sig_part, changed)

    def _quiet_job(self, names: set[str], sig_part: dict, changed: set):
        base = self._state
        results = {n: self._load_one(n, base.data, changed) for n in sorted(names)}
        with self._lock:  # merge into whatever State is current now
            self._state = self._state.derive(results)
            self._sig.update(sig_part)

    def wait_background(self):
        """Wait for background loads and a running quiet reload (tests, the serve banner)."""
        if self._quiet is not None:
            self._quiet.result()
        for k in list(self._state.data.pending):
            self._state.data[k]

    def _link(self, st: State):
        cd: L_contacts.ContactsData = st.data["contacts"]
        ld: L_leads.LeadsData = st.data["leads"]
        df = cd.df
        st.contact_leads = ld.by_det
        m = df[df["ais_status"].eq("matched") & df["vessel_key"].notna()] if len(df) else df
        st.vessel_contacts = m.groupby("vessel_key")["det_id"].apply(list).to_dict() if len(m) else {}
        st.product_mask = (df["confidence"].astype(str) != "low").to_numpy() if len(df) else np.zeros(0, bool)

    def sorted_positions(self, owner, df: pd.DataFrame, m: np.ndarray, sort: str | None, allowed: dict, default: str,
                         tiebreak: str | None = None) -> np.ndarray:
        """Positions where `m` holds, in sort order. The full-frame order is computed once per loaded frame and sort and
        kept on the loader data object `owner` that holds `df`."""
        orders = obj_cache(owner, "orders")
        key = (id(df), (sort or default).strip(), tiebreak)
        full = orders.get(key)
        if full is None:
            base = np.arange(len(df))
            if tiebreak:
                base = _order(df, base, tiebreak, {**allowed, tiebreak.lstrip("-"): tiebreak.lstrip("-")}, tiebreak)
            full = _order(df, base, sort, allowed, default)
            if len(orders) > 64:
                orders.clear()
            orders[key] = full
        return full[np.asarray(m, bool)[full]]

    def prefix_index(self, name: str) -> PrefixIndex:
        """Prefix index of one id space (contacts, lights, leads, events), kept on that loader's data object."""
        owner = self.data[name]
        c = obj_cache(owner, "prefix")
        ix = c.get(name)
        if ix is None:
            ids = {"contacts": lambda: owner.pos.index, "lights": lambda: owner.pos.index,
                   "leads": lambda: owner.pos.index, "events": lambda: owner.df["event_id"]}[name]()
            ix = c[name] = PrefixIndex(ids)
        return ix

    def _warm(self, st: State):
        """Build the lazy indexes (event ids, Omnibar prefixes, the lead summaries) of `st` in the background. Indexes
        of loaders that did not reload are already built and kept."""
        self._hold()
        _PINNED.set((self, st))
        try:  # the lead queue first (the first view), then the Omnibar indexes, the 610k event ids last
            self.lead_rows(np.arange(len(st.data["leads"].df)), summary=True)
            for name in ("contacts", "lights", "leads"):
                self.prefix_index(name)
            st.data["events"].pos
            self.prefix_index("events")
        except Exception:  # noqa: BLE001  (warming is an optimisation; a request builds what it needs)
            pass

    @property
    def chips(self) -> set:
        k = self._sig.get("chips")
        c = self._chips
        if c is None or c[0] != k:
            c = self._chips = (k, {p.stem for p in self.cat.paths("chips")})
        return c[1]

    def contact_tree(self) -> MetricTree:
        cd = self.data["contacts"]
        c = obj_cache(cd, "tree")
        t = c.get("tree")
        if t is None:
            t = c["tree"] = MetricTree(cd.df["lon"].to_numpy(), cd.df["lat"].to_numpy())
        return t

    # ------------------------------------------------------------ caveats
    def cav(self, *extra) -> str:
        return self.settings.caveat(*extra)

    # ------------------------------------------------------------ contacts
    def object_contexts(self, kind: str, ids, sources=None) -> list[dict | None]:
        """Board D5.3 `object_context` of contacts (`kind` contact, with each row's `_source`) or lights; None where the
        context table has no row (live passes until the table is rebuilt after the pass) or is not loaded."""
        ctx = self.data.get("context")
        ids = [str(i) for i in ids]
        if ctx is None or ctx.objects is None or not ids:
            return [None] * len(ids)
        if kind == "light":
            return ctx.records(L_context.LIGHT_TYPES, ids)
        out: list = [None] * len(ids)
        groups: dict[tuple, list[int]] = {}
        for i, src in enumerate(sources if sources is not None else ["regional"] * len(ids)):
            groups.setdefault(L_context.CONTACT_TYPES.get(src, ("radar",)), []).append(i)
        for types, idx in groups.items():
            for i, rec in zip(idx, ctx.records(types, [ids[i] for i in idx])):
                out[i] = rec
        return out

    @staticmethod
    def contact_field_prov(r: dict) -> dict:
        """Field-level provenance (spec 4.8) of the contract 1.3.0 contact fields: source key, valid time, source text."""
        fp = {}

        def put(f, src, time=None, text=None):
            if clean(r.get(f)) is not None:
                fp[f] = {"src": src, "time": prov_time(clean(time)), "text": clean(text)}

        if r.get("_source") == "live":
            t = clean(r.get("az_time_utc")) or clean(r.get("acq_utc"))
            put("az_time_utc", "det_live", t, "azimuth time of the contact from the Sentinel-1 product annotation")
            put("az_shift_m", "det_live", t, "SAR azimuth shift of the matched vessel's velocity at this contact (live file about.azimuth_correction)")
            for f in ("match_dist_uncorr_m", "velocity_source", "match_ambiguous", "ambiguous_mmsi", "match_alt_dist_m"):
                put(f, "aisstream", t, "AIS track placed at the contact's azimuth time")
            rf = clean(r.get("_review_file"))
            put("review_note", "analyst", r.get("_review_time"), f"hand check, {rf} (reviewed_utc)" if rf else
                "hand check, review_note of data/live/live_contacts.gpkg (no row in a hand-check table, time unknown)")
            put("review_grade", "analyst", r.get("_review_time"), "grade of review_note")
            put("identity_label", "aisstream", None, "board D4.7")
        live_w = r.get("_source") == "live"  # live: the pass's weather sidecar; else data/weather_context.parquet
        put("wind_ms", "gfs_wind", r.get("_wind_time"), r.get("_wind_source") if live_w else
            "data/weather_context.parquet (GFS 0.25 degree 10 m wind; the GFS hour is not stored)")
        cloud_t = clean(r.get("_cloud_time")) if live_w else r.get("himawari_start")  # both ISO 8601 by now
        cloud_x = r.get("_cloud_source") if live_w else \
            "data/weather_context.parquet (Himawari-9 AHI L2 cloud tops; scan start from himawari_start, to 10 s)"
        put("ctt_k", "himawari_ctt", cloud_t, cloud_x)
        put("deep_convection", "himawari_ctt", cloud_t, cloud_x)
        return fp

    def contact_rows(self, positions, full: bool = True) -> list[dict]:
        cd = self.data["contacts"]
        if len(positions) == 0:
            return []
        rows = cd.df.iloc[np.asarray(positions)].to_dict("records")
        fields = self.f["contact"] if full else self.f["contact_summary"]
        dflt = self.d["contact"] if full else self.d["contact_summary"]
        ints = self.i["contact"] if full else self.i["contact_summary"]
        out = []
        cav = self.cav()
        chips = self.chips if full else set()
        ctxs = self.object_contexts("contact", [r["det_id"] for r in rows], [r["_source"] for r in rows]) if full else None
        for k, r in enumerate(rows):
            src = r["_source"]
            fixed = {"caveat": cav, "lead_ids": self.contact_leads.get(str(r["det_id"]), [])}
            if full:
                fixed.update({"src": L_contacts.SRC[src], "prov": dict(L_contacts.PROV[src]),
                              "chip": f"/api/v1/contacts/{r['det_id']}/chip.webp" if str(r["det_id"]) in chips else None,
                              "object_context": ctxs[k], "field_prov": self.contact_field_prov(r)})
                rec = build_record(fields, r, extra_cols=cd.extra_cols.get(src, []), extra=L_contacts.source_caveat_extra(r), defaults=dflt, ints=ints, **fixed)
            else:
                rec = build_record(fields, r, defaults=dflt, ints=ints, **fixed)
            out.append(rec)
        return out

    def contact(self, det_id: str) -> dict:
        cd = self.data["contacts"]
        if det_id not in cd.pos.index:
            raise ApiError(404, "not_found", f"no contact {det_id}")
        return self.contact_rows([int(cd.pos[det_id])])[0]

    def contacts_query(self, p: dict) -> tuple[np.ndarray, int]:
        cd = self.data["contacts"]
        df = cd.df
        if not len(df):
            return np.array([], int), 0
        m = mask_window(df, p.get("t0"), p.get("t1"), p.get("bbox"))
        views = csv_list(p.get("view"))
        confs = csv_list(p.get("confidence"))
        if not ((views and "camau" in views) or (confs and "low" in confs)):
            m &= self.product_mask  # contract 4.4: low objects only in the Ca Mau view
        for col, key in (("run_id", "run_id"), ("view", "view"), ("confidence", "confidence"), ("pass_id", "pass_id")):
            vals = csv_list(p.get(key))
            if vals:
                m &= df[col].astype(str).isin(vals).to_numpy()
        st = csv_list(p.get("ais_status"))
        if st:
            bad = [s for s in st if s not in AIS_STATUS_VALUES]
            if bad:
                raise ApiError(422, "bad_ais_status", f"ais_status must be among {', '.join(AIS_STATUS_VALUES)}")
            m &= df["ais_status"].isin(st).to_numpy()
        if p.get("cnn_min") is not None:
            m &= (pd.to_numeric(df["cnn_score"], errors="coerce") >= float(p["cnn_min"])).to_numpy()
        cv = _bool_param(p.get("cnn_vessel"))
        if cv is not None:
            m &= df["cnn_vessel"].map(lambda v: v is not None and bool(v) == cv).to_numpy(dtype=bool)
        if p.get("mmsi"):
            m &= df["mmsi"].astype(str).eq(str(p["mmsi"])).to_numpy()
        pos = self.sorted_positions(cd, df, m, p.get("sort"), CONTACT_SORTS, "-acq_utc", tiebreak="det_id")
        return pos, len(pos)

    # ------------------------------------------------------------ vessels
    def vessel_rows(self, positions, full: bool = True) -> list[dict]:
        vd = self.data["vessels"]
        if len(positions) == 0:
            return []
        rows = vd.df.iloc[np.asarray(positions)].to_dict("records")
        out = []
        for r in rows:
            key = str(r["vessel_key"])
            extra = {}
            sc = r.get("_source_caveat")
            if isinstance(sc, str) and sc and sc != PRODUCT_CAVEAT:
                extra["source_caveat"] = sc
            if r.get("_aisstream_key"):
                extra["aisstream_vessel_key"] = r["_aisstream_key"]
            prov = {"flag": "mid_itu", "mid": "mid_itu", "contacts_matched": "app"} if r["_src"] == "aisstream" else \
                {"contacts_matched": "app", "mid": "app"}
            if r["_src"] == "aisstream":
                prov["identity_label"] = "aisstream"
            fixed = {"caveat": self.cav(), "src": r["_src"], "prov": prov, "identity_note": r["_note"],
                     "contacts_matched": self.vessel_contacts.get(key, []), "stub": bool(r.get("stub")),
                     "identity_label": AISSTREAM_LABEL if r["_src"] == "aisstream" else None}
            if full:
                out.append(build_record(self.f["vessel"], r, extra_cols=vd.extra_cols["all"], extra=extra, defaults=self.d["vessel"], ints=self.i["vessel"], **fixed))
            else:
                out.append(build_record(VESSEL_SUMMARY, r, defaults=self.d["vessel_summary"], ints=self.i["vessel_summary"], **{k: v for k, v in fixed.items() if k in VESSEL_SUMMARY}))
        return out

    def vessel(self, key: str) -> dict:
        vd = self.data["vessels"]
        if key not in vd.pos.index:
            raise ApiError(404, "not_found", f"no vessel {key}")
        return self.vessel_rows([int(vd.pos[key])])[0]

    def vessels_query(self, p: dict) -> tuple[np.ndarray, int]:
        df = self.data["vessels"].df
        if not len(df):
            return np.array([], int), 0
        m = np.ones(len(df), bool)
        q = (p.get("q") or "").strip()
        if q:
            ql = q.lower()
            name = df["name"].astype(str).str.lower() if "name" in df else pd.Series("", index=df.index)
            cs = df["call_sign"].astype(str).str.lower() if "call_sign" in df else pd.Series("", index=df.index)
            digits = q.upper().removeprefix("IMO").strip()
            hit = name.str.contains(ql, regex=False) | cs.str.startswith(ql)
            if digits.isdigit():
                hit |= df["mmsi"].astype(str).str.startswith(digits) | df["imo"].astype(str).eq(digits)
            m &= hit.to_numpy()
        ia = _bool_param(p.get("in_aoi"))
        if ia is not None and "in_aoi" in df:
            m &= df["in_aoi"].map(lambda v: v is not None and bool(v) == ia).to_numpy(dtype=bool)
        if p.get("ais_class") and "ais_class" in df:
            m &= df["ais_class"].astype(str).eq(p["ais_class"]).to_numpy()
        pos = np.flatnonzero(m)
        if "_t_last_seen_utc" in df:
            pos = _order(df, pos, "-last_seen_utc", {"last_seen_utc": "_t_last_seen_utc"}, "-last_seen_utc")
        return pos, len(pos)

    def track(self, key: str, t0=None, t1=None, max_points: int = 2000) -> dict:
        v = self.vessel(key)
        mmsi = v.get("mmsi")
        return L_vessels.track(self.data["tracks"], key, mmsi, t0, t1, max_points)

    # ------------------------------------------------------------ lights and sites
    def light_rows(self, positions, full: bool = True, evidence: bool = False) -> list[dict]:
        """Light records of the lean light file, or (evidence=True) of the lights that leads cite outside it."""
        ld = self.data["lights_extra"] if evidence else self.data["lights"]
        if len(positions) == 0:
            return []
        rows = ld.lights.iloc[np.asarray(positions)].to_dict("records")
        extra_cols = ld.extra_cols if evidence else ld.extra_cols["light"]
        out = []
        cav = self.cav(L_lights.LIGHT_CAVEAT)
        cdf = self.data["contacts"].df
        ctxs = self.object_contexts("light", [r["light_id"] for r in rows]) if full else None
        for k, r in enumerate(rows):
            extra = {}
            sc = r.get("_source_caveat")
            if isinstance(sc, str) and sc and sc != PRODUCT_CAVEAT:
                extra["source_caveat"] = sc
            if evidence:
                extra["light_file"] = "data/viirs_lights_all.gpkg"
                extra["light_file_note"] = L_lights.EXTRA_NOTE
            near = []
            if full and len(cdf):
                hits = self.contact_tree().within(r["lon"], r["lat"], 2000.0)
                if len(hits):
                    sub = cdf.iloc[hits]
                    near = sub.loc[sub["_night"].astype(str) == str(r["night"]), "det_id"].astype(str).tolist()
            fixed = {"caveat": cav, "src": "viirs_dnb", "contacts_2km_same_night": near, "research_only": False,
                     "prov": {"satlas_infra_m": "satlas", "site_id": "app", "contacts_2km_same_night": "app", "cell_id": "app",
                              "object_context": "ocean_context"}}
            if full:
                fixed["object_context"] = ctxs[k]
                out.append(build_record(self.f["light"], r, extra_cols=extra_cols, extra=extra, defaults=self.d["light"], ints=self.i["light"], **fixed))
            else:
                out.append(build_record(LIGHT_SUMMARY, r, defaults=self.d["light_summary"], ints=self.i["light_summary"], **{k: v for k, v in fixed.items() if k in LIGHT_SUMMARY}))
        return out

    def light_where(self, light_id: str) -> tuple[int, bool] | None:
        """(position, evidence) of a light: the lean file first, then the lights leads cite outside it."""
        ld = self.data["lights"]
        if light_id in ld.pos.index:
            return int(ld.pos[light_id]), False
        ev = self.data.get("lights_extra")
        if ev is not None and light_id in ev.pos.index:
            return int(ev.pos[light_id]), True
        return None

    def light(self, light_id: str) -> dict:
        hit = self.light_where(light_id)
        if hit is None:
            raise ApiError(404, "not_found", f"no light {light_id}")
        return self.light_rows([hit[0]], evidence=hit[1])[0]

    def site(self, site_id: str) -> dict:
        ld = self.data["lights"]
        if site_id not in ld.site_pos.index:
            raise ApiError(404, "not_found", f"no light site {site_id}")
        r = ld.sites.iloc[int(ld.site_pos[site_id])].to_dict()
        extra = {}
        sc = r.get("_source_caveat")
        if isinstance(sc, str) and sc and sc != PRODUCT_CAVEAT:
            extra["source_caveat"] = sc
        return build_record(self.f["site"], r, extra_cols=ld.extra_cols["site"], extra=extra, defaults=self.d["site"], ints=self.i["site"],
                            caveat=self.cav(L_lights.LIGHT_CAVEAT), src="viirs_dnb", prov={"satlas_infra_m": "satlas"},
                            research_only=False)

    def lights_query(self, p: dict) -> tuple[np.ndarray, int]:
        ld = self.data["lights"]
        df = ld.lights
        if not len(df):
            return np.array([], int), 0
        m = mask_window(df, p.get("t0"), p.get("t1"), p.get("bbox"))
        for col in ("night", "quality"):
            vals = csv_list(p.get(col))
            if vals:
                m &= df[col].astype(str).isin(vals).to_numpy()
        pos = self.sorted_positions(ld, df, m, "-time_utc", {"time_utc": "_t"}, "-time_utc")
        return pos, len(pos)

    # ------------------------------------------------------------ events
    def event_rows(self, positions) -> list[dict]:
        ed = self.data["events"]
        if len(positions) == 0:
            return []
        rows = ed.df.iloc[np.asarray(positions)].to_dict("records")
        out = []
        for r in rows:
            if ed.open_fields is not None:  # open events file: contract names already
                extra = [c for c in ed.extra_cols if c not in self.f["event"]]
                out.append(build_record(self.f["event"], r, extra_cols=extra, defaults=self.d["event"], ints=self.i["event"], caveat=self.cav(L_events.TYPE_CAVEAT.get(r.get("code"))),
                                        src=r.get("src") or "aisstream", research_only=False))
                continue
            code = r["code"]
            vk = [f"gfw:{r['vessel_id']}"] if clean(r.get("vessel_id")) else []
            mm = [str(r["ssvid"])] if clean(r.get("ssvid")) else []
            if clean(r.get("encounter_vessel_id")):
                vk.append(f"gfw:{r['encounter_vessel_id']}")
            if clean(r.get("encounter_ssvid")):
                mm.append(str(r["encounter_ssvid"]))
            geom = None
            if code == "E8" and all(clean(r.get(k)) is not None for k in ("off_lon", "off_lat", "on_lon", "on_lat")):
                geom = {"type": "LineString", "coordinates": [[r["off_lon"], r["off_lat"]], [r["on_lon"], r["on_lat"]]]}
            dur = clean(r.get("duration_h"))
            params = {c: r.get(c) for c in ed.param_cols if clean(r.get(c)) is not None}
            out.append(build_record(
                self.f["event"], r, extra_cols=[c for c in ed.extra_cols if not c.startswith("encounter_")], defaults=self.d["event"], ints=self.i["event"],
                event_id=str(r["event_id"]), start_utc=iso_z(r["_t"]), end_utc=iso_z(r.get("end")), duration_h=dur,
                geometry=geom, mmsi=mm, vessel_keys=vk, det_ids=[], light_ids=[], cell_ids=[c for c in cell_ids([r["lon"]], [r["lat"]]) if c],
                params=params, rule_text=L_events.RULE_TEXT[code], grade=L_events.grade(code, dur), source="gfw",
                research_only=True, caveat=self.cav(L_events.TYPE_CAVEAT.get(code)), src="gfw_events", prov={"cell_ids": "app", "grade": "app"}))
        return out

    def event(self, event_id: str) -> dict:
        ed = self.data["events"]
        if event_id not in ed.pos.index:
            raise ApiError(404, "not_found", f"no event {event_id}")
        return self.event_rows([int(ed.pos[event_id])])[0]

    def events_query(self, p: dict) -> tuple[np.ndarray, int]:
        ed = self.data["events"]
        df = ed.df
        if not len(df):
            return np.array([], int), 0
        m = mask_window(df, p.get("t0"), p.get("t1"), p.get("bbox"))
        for col in ("code", "event_type"):
            vals = csv_list(p.get(col))
            if vals:
                m &= df[col].astype(str).isin(vals).to_numpy()
        vk = p.get("vessel_key")
        if vk:
            vid = vk.split(":", 1)[1] if ":" in vk else vk
            if vk.startswith("gfw:") and "vessel_id" in df:
                hit = df["vessel_id"].astype(str).eq(vid)
                if "encounter_vessel_id" in df:
                    hit |= df["encounter_vessel_id"].astype(str).eq(vid)
            elif "ssvid" in df:
                hit = df["ssvid"].astype(str).eq(vid)
            else:
                hit = pd.Series(False, index=df.index)
            m &= hit.to_numpy()
        det = p.get("det_id")
        if det:
            ids = set(self.data["leads"].events_by_det.get(det, []))
            m &= df["event_id"].astype(str).isin(ids).to_numpy()
        pos = self.sorted_positions(ed, df, m, "-start", {"start": "_t"}, "-start")
        return pos, len(pos)

    # ------------------------------------------------------------ leads
    def lead_state(self) -> pd.Series:
        df = self.data["leads"].df
        st = df["state"].astype(str).copy() if len(df) else pd.Series(dtype=str)
        log = self.decisions.all()
        if log and len(df):
            pos = self.data["leads"].pos
            for lid, ds in log.items():
                if lid in pos.index and ds:
                    st.iloc[int(pos[lid])] = ds[-1]["to_state"]
        return st

    def _lead_base(self, i: int, r: dict, summary: bool) -> dict:
        """A lead record from its file row alone (state and history as the file holds them)."""
        ld = self.data["leads"]
        extra = {}
        if not summary:
            sc = r.get("_source_caveat")
            if isinstance(sc, str) and sc and sc != PRODUCT_CAVEAT:
                extra["source_caveat"] = sc
            if "nights" in r:
                extra["nights"] = json_list(r.get("nights"))
            if r.get("_evidence_source"):
                extra["evidence_source"] = r["_evidence_source"]
        ex_cols = [] if summary else [c for c in ld.extra_cols if c != "nights"]
        hist = [h for h in json_list(r.get("history")) if isinstance(h, dict)]
        return build_record(
            self.f["lead"], r, extra_cols=ex_cols, extra=extra, defaults=self.d["lead"], ints=self.i["lead"],
            state=r.get("state") or "new", reason=r.get("reason"), factors=json_list(r.get("factors")),
            evidence=[dict(e) for e in ld.evidence[int(i)]], lawful_explanations=json_list(r.get("lawful_explanations")),
            change_indicators=json_list(r.get("change_indicators")), history=hist, prov=json_map(r.get("prov")),
            src=r.get("src") or "app", caveat=self.cav(), research_only=bool(clean(r.get("research_only")) or False))

    def lead_rows(self, positions, previews: bool = False, summary: bool = False) -> list[dict]:
        """Lead records with state, reason and history from the decision log. Summaries (the /leads list) leave out
        `extra` and are cached on the loaded leads data (a reload of any other file keeps them), so a full queue of 13,293 research leads is not rebuilt per request."""
        ld = self.data["leads"]
        positions = [int(p) for p in np.asarray(positions)]
        if not positions:
            return []
        cache = obj_cache(ld, "lead_summaries") if summary else {}
        todo = [p for p in positions if p not in cache]
        if todo:
            sub = ld.df.iloc[todo]
            if summary:  # a summary reads only contract fields: convert just those columns, column by column
                keep = set(self.f["lead"])
                names = [c for c in sub.columns if c in keep]
                cols = [sub[c].to_numpy(dtype=object, na_value=None) for c in names]
                rows = [dict(zip(names, vals)) for vals in zip(*cols)] if names else [{} for _ in todo]
            else:
                rows = sub.to_dict("records")
            for p, r in zip(todo, rows):
                cache[p] = self._lead_base(p, r, summary)
        log = self.decisions.all()
        out = []
        for p in positions:
            base = cache[p]
            ds = log.get(base["lead_id"])
            rec = base
            if ds or previews:
                rec = dict(base)
            if ds:
                hist = list(base["history"]) + [clean(d) for d in ds]
                state = hist[-1]["to_state"]
                rec.update(history=hist, state=state, reason=hist[-1].get("reason") if state.startswith("closed") else None)
            if previews:
                rec["evidence"] = [{**e, "preview": self.preview(e.get("type"), str(e.get("id")))} for e in base["evidence"]]
            out.append(rec)
        return out

    def lead(self, lead_id: str, previews: bool = True) -> dict:
        ld = self.data["leads"]
        if lead_id not in ld.pos.index:
            raise ApiError(404, "not_found", f"no lead {lead_id}")
        return self.lead_rows([int(ld.pos[lead_id])], previews=previews)[0]

    def leads_query(self, p: dict) -> tuple[np.ndarray, int]:
        ld = self.data["leads"]
        df = ld.df
        if not len(df):
            return np.array([], int), 0
        m = mask_window(df, p.get("t0"), p.get("t1"))
        states = csv_list(p.get("state")) or ["new", "reviewing"]
        bad = [s for s in states if s not in LEAD_STATES]
        if bad:
            raise ApiError(422, "bad_state", f"state must be among {', '.join(LEAD_STATES)}")
        m &= self.lead_state().isin(states).to_numpy()
        for col in ("lead_type", "region_box", "pass_id"):
            vals = csv_list(p.get(col))
            if vals and col in df:
                m &= df[col].astype(str).isin(vals).to_numpy()
        if p.get("min_priority") is not None:
            m &= (df["priority"] >= float(p["min_priority"])).to_numpy()
        pos = self.sorted_positions(ld, df, m, p.get("sort"), LEAD_SORTS, "-priority", tiebreak="lead_id")
        return pos, len(pos)

    def preview(self, typ: str | None, oid: str) -> dict | None:
        """A short preview of an evidence object (label, position, time), or None when it is not in this build."""
        try:
            if typ == "contact":
                c = self.contact_rows([int(self.data["contacts"].pos[oid])], full=False)[0]
                return {"label": f"{c['confidence']} contact, {c['ais_status']}", "lon": c["lon"], "lat": c["lat"],
                        "time_utc": c["acq_utc"], "length_est_m": c["length_est_m"], "cnn_score": c["cnn_score"]}
            if typ == "vessel":
                v = self.vessel_rows([int(self.data["vessels"].pos[oid])], full=False)[0]
                return {"label": v.get("name") or v.get("mmsi") or oid, "mmsi": v.get("mmsi"), "flag": v.get("flag"),
                        "ship_type": v.get("ship_type"), "stub": v.get("stub")}
            if typ == "light":
                hit = self.light_where(oid)
                if hit is None:
                    return None
                g = self.light_rows([hit[0]], full=False, evidence=hit[1])[0]
                return {"label": f"VIIRS light, {g['quality']}", "lon": g["lon"], "lat": g["lat"], "time_utc": g["time_utc"],
                        "radiance_nw": g["radiance_nw"]}
            if typ == "event":
                e = self.event(oid)
                return {"label": e["event_type"], "lon": e["lon"], "lat": e["lat"], "time_utc": e["start_utc"]}
            if typ == "pass":
                pdf = self.data["passes"]
                hit = pdf[pdf["pass_id"] == oid]
                if len(hit):
                    r = hit.iloc[0]
                    return {"label": f"{r['mission']} pass", "time_utc": r["start_utc"], "status": r["status"]}
            if typ == "cell":
                rc = parse_cell_id(oid)
                cd = self.data["cells"]
                if rc and rc in cd.key:
                    s = cd.static.iloc[cd.key[rc]]
                    return {"label": f"cell {oid}", "lon": float(s["lon"]), "lat": float(s["lat"]),
                            "depth_mean_m": clean(s.get("depth_mean_m")), "region_box": s.get("region")}
            if typ in ("site", "light_site", "recurring_site"):  # the lead builder writes light_site
                s = self.site(oid)
                return {"label": s.get("likely"), "lon": s["lon"], "lat": s["lat"]}
        except (KeyError, ApiError, IndexError):
            return None
        return None

    # ------------------------------------------------------------ passes
    def pass_rows(self, positions, footprint: bool = True, ais_only: bool = False) -> list[dict]:
        """Pass records. The AIS-only vessel list is filled only with ais_only=True (GET /passes/{pass_id}); lists carry
        its length in n_ais_only."""
        df = self.data["passes"]
        if len(positions) == 0:
            return []
        rows = df.iloc[np.asarray(positions)].to_dict("records")
        out = []
        for r in rows:
            fp = r.get("footprint") if footprint else None
            prov = {"n_contacts": "app", "note": "app"}
            fprov = {}
            live = clean(r.get("n_ais_only")) is not None or isinstance(r.get("azimuth_check"), dict)
            if live:
                prov.update({"n_ais_only": "aisstream", "ais_only": "aisstream", "azimuth_check": "det_live",
                             "identity_label": "aisstream"})
                if clean(r.get("n_ais_only")) is not None:
                    fprov["ais_only"] = {"src": "aisstream", "time": prov_time(clean(r.get("_ais_only_time"))),
                                         "text": "AIS vessels placed at the scene time inside the footprint that no contact "
                                                 "matched (live file layer ais_only_4326)"}
                if isinstance(r.get("azimuth_check"), dict):
                    fprov["azimuth_check"] = {"src": "det_live", "time": prov_time(clean(r.get("_azimuth_time"))),
                                              "text": "data/live/live_summary.json azimuth_check_by_scene"}
            lst = r.get("_ais_only")
            out.append(build_record(
                self.f["pass"], r, extra=dict(r.get("_extra") or {}), defaults=self.d["pass"], ints=self.i["pass"],
                footprint=fp, caveat=self.cav(), src=r["_src"], research_only=False, prov=prov, field_prov=fprov,
                ais_only=(lst if isinstance(lst, list) else None) if ais_only else None,
                azimuth_check=r.get("azimuth_check") if isinstance(r.get("azimuth_check"), dict) else None))
        return out

    def pass_(self, pass_id: str) -> dict:
        df = self.data["passes"]
        hit = np.flatnonzero(df["pass_id"].astype(str).eq(pass_id).to_numpy()) if len(df) else []
        if not len(hit):
            raise ApiError(404, "not_found", f"no pass {pass_id}")
        return self.pass_rows([int(hit[0])], ais_only=True)[0]

    def passes_query(self, p: dict) -> tuple[np.ndarray, int]:
        df = self.data["passes"]
        if not len(df):
            return np.array([], int), 0
        m = np.ones(len(df), bool)
        if p.get("t0") is not None:
            m &= (df["_t1"] >= p["t0"]).to_numpy()
        if p.get("t1") is not None:
            m &= (df["_t"] <= p["t1"]).to_numpy()
        for col in ("status", "mission"):
            vals = csv_list(p.get(col))
            if vals:
                m &= df[col].astype(str).isin(vals).to_numpy()
        pos = np.flatnonzero(m)
        return pos, len(pos)

    # ------------------------------------------------------------ cells
    def cell(self, cell_id: str, night: str | None = None, scene_id: str | None = None) -> dict:
        rc = parse_cell_id(cell_id)
        cd = self.data["cells"]
        if rc is None:
            raise ApiError(404, "not_found", f"{cell_id!r} is not a cell id of the form r<row>c<col>")
        if rc not in cd.key:
            raise ApiError(404, "not_found", f"cell {cell_id} is not a product cell (no AOI sea in it)")
        row, col = rc
        s = cd.static.iloc[cd.key[rc]].to_dict()
        nights, nightly = [], None
        if cd.daily is not None and rc in cd.daily_index:
            d = cd.daily.iloc[cd.daily_index[rc]].sort_values("night")
            nights = [str(x) for x in d["night"]]
            pick = d[d["night"].astype(str) == night] if night else d.tail(1)
            if night and not len(pick):
                raise ApiError(404, "not_found", f"no nightly fields for {cell_id} on {night}")
            if len(pick):
                nightly = {k: clean(v) for k, v in pick.iloc[0].to_dict().items() if k not in ("row", "col")}
        pass_ctx = None
        if scene_id:
            if cd.passes is None or rc not in cd.pass_index:
                raise ApiError(404, "not_found", f"no per-pass fields for {cell_id}")
            pp = cd.passes.iloc[cd.pass_index[rc]]
            pp = pp[pp["scene_id"].astype(str) == scene_id]
            if not len(pp):
                raise ApiError(404, "not_found", f"no per-pass fields for {cell_id} and scene {scene_id}")
            pass_ctx = {k: clean(v) for k, v in pp.iloc[0].to_dict().items() if k not in ("row", "col")}
        w, n = 99.0 + col * 0.25, 24.0 - row * 0.25
        e, so = w + 0.25, n - 0.25
        rs: Rasters = self.data["rasters"]
        rv = {}
        for fld, name, how in (("ais_reach_share", "ais_reach_share", "v"), ("ais_reach_mmsi", "ais_reach_mmsi", "v"),
                               ("look_prob_1d", "s1_look_prob_1d", "m"), ("look_prob_7d", "s1_look_prob_7d", "m"),
                               ("look_prob_30d", "s1_look_prob_30d", "m"), ("passes_90d", "s1_passes", "m")):
            if name in rs.paths:
                rv[fld] = rs.value(name, (w + e) / 2, (n + so) / 2) if how == "v" else rs.cell_mean(name, w, so, e, n)
        gfw = None
        if self.settings.research and cd.gfw is not None and rc in cd.gfw.index:
            gfw = {k: clean(v) for k, v in cd.gfw.loc[rc].to_dict().items()}
            gfw["note"] = "GFW SAR detections against ours per cell-hour (data/research/radar_vs_gfw.parquet), summed over the cell"
        from darkvessel.ocean.grid import OCEAN_CAVEAT

        prov = {**L_cells.static_prov(s.keys()), "nightly": "mur_sst", "ais_reach_share": "aisstream", "ais_reach_mmsi": "aisstream",
                "look_prob_1d": "s1_grd", "look_prob_7d": "s1_grd", "look_prob_30d": "s1_grd", "passes_90d": "s1_grd", "eez": "marineregions_v12"}
        if gfw is not None:
            prov["gfw_comparison"] = "gfw_4wings"
        known = set(self.f["cell"])
        extra_cols = [c for c in s if c not in known and not c.startswith("marineregions_") and c not in ("region",)]
        ctx = self.data.get("context")
        ea = ctx.expected_for(row, col) if ctx is not None and ctx.expected is not None else None
        fprov = {}
        if ea is not None:
            prov["expected_activity"] = "expected_activity"
            fprov["expected_activity"] = {"src": "expected_activity", "time": None,
                                          "text": f"model {ea.get('model_id')}; each row carries its own time_start_utc and time_end_utc"}
        return build_record(
            self.f["cell"], s, extra_cols=extra_cols, defaults=self.d["cell"], ints=self.i["cell"], cell_id=cell_id, row=row, col=col, region_box=s.get("region"),
            shipping_note=SHIPPING_LABEL, nightly=nightly, nights_available=nights, pass_context=pass_ctx,
            object_context=None, expected_activity=ea, eez=L_cells.eez_block(s), **({"gfw_comparison": gfw} if self.settings.research else {}),
            research_only=gfw is not None, caveat=self.cav(OCEAN_CAVEAT), src="app", prov=prov, field_prov=fprov, **rv)

    def cell_at(self, lon: float, lat: float) -> dict:
        from .loaders.common import cell_rc

        r, c, ok = cell_rc([lon], [lat])
        if not ok[0]:
            raise ApiError(404, "not_found", f"{lon}, {lat} is outside the model grid")
        return self.cell(f"r{int(r[0])}c{int(c[0])}")

    # ------------------------------------------------------------ meta
    def loading(self) -> list[str]:
        """Loaders still running in the background (their counts in /meta come from the catalog meanwhile)."""
        data = self.data
        return sorted(k for k in list(data.pending) if data.peek(k) is None)  # peek resolves done loads (pops)

    def counts(self) -> dict[str, int]:
        """Rows per object type. Never waits for a background load: a loader still loading is counted from the
        catalog's row counts of its files and named in /meta `loading`."""
        data = self.data
        cd = data["contacts"].df
        c = {"contacts": int(self.product_mask.sum()) if len(cd) else 0}
        if len(cd):
            for v, n in cd.groupby("view").size().items():
                c[f"contacts_{v}"] = int(n)
            c["structures"] = int((cd["confidence"].astype(str) == "fixed").sum())
            for st, n in cd.loc[self.product_mask, "ais_status"].value_counts().items():
                c[f"contacts_{st}"] = int(n)
        ev = data.peek("events")
        n_events = len(ev.df) if ev is not None else sum(self.cat.rows(k) or 0 for k in DEPS["events"] if k in self.cat.specs)
        tr = data["tracks"]
        ev_lights = data.peek("lights_extra")  # never waits: a count is absent while its loader loads (/meta loading)
        if ev_lights is not None:
            c["lights_evidence"] = len(ev_lights.lights)
        ctx = data.peek("context")
        if ctx is not None:
            c["context_objects"] = 0 if ctx.objects is None else len(ctx.objects)
            c["expected_activity_rows"] = 0 if ctx.expected is None else len(ctx.expected)
        c.update({"vessels": len(data["vessels"].df), "lights": len(data["lights"].lights),
                  "sites": len(data["lights"].sites), "events": int(n_events),
                  "leads": len(data["leads"].df), "passes": len(data["passes"]),
                  "cells": len(data["cells"].key), "rasters": len(data["rasters"].paths),
                  "decisions": self.decisions.count(), "chips": len(self.chips),

                  "track_positions": 0 if tr.positions is None else len(tr.positions)})
        return c


    def meta(self) -> dict:
        vd, tr = self.data["vessels"], self.data["tracks"]
        s = vd.summary
        rec = {
            "contract_version": CONTRACT_VERSION, "build": self.settings.build, "build_label": self.settings.build_label,
            "research_label": RESEARCH_LABEL if self.settings.research else None,
            "attribution": ATTRIBUTION if self.settings.research else None, "caveat": PRODUCT_CAVEAT,
            "caveat_short": CAVEAT_SHORT, "generated_utc": utc_now(), "loaded_utc": self.loaded_utc,
            "git_hash": self.git_hash, "app_version": APP_VERSION, "priority_model_id": self.data["leads"].priority_model_id,
            "sources": registry(self.settings.build, self.git_hash), "files": self.cat.entries(), "counts": self.counts(),
            "ais_recording": {"period_start_utc": iso_z(s.get("period_start_utc")), "period_end_utc": iso_z(s.get("period_end_utc")),
                              "hours_recorded": s.get("hours_recorded"), "gaps": s.get("recording_gaps_over_10_min") or [],
                              "positions": s.get("positions"), "mmsi_count": s.get("mmsi_count"),
                              "hours_in_cache": len(tr.recorded_hours),
                              "note": "aisstream.io relays shore receivers; the free feed is terrestrial. Terms UNVERIFIED."} if s or tr.recorded_hours else None,
            "live_rules": {k: clean(self.data["contacts"].about.get(k)) for k in LIVE_RULES}
            if self.data["contacts"].about else None,
            "data_credit": DATA_CREDIT, "load_seconds": self.load_seconds, "loading": self.loading(),
            "reload_error": self.reload_error,
        }
        return rec
