"""Builds one single-file page: loads the backend store, encodes the parts, fits the budgets with the contract 6.3
drop rules, runs the content checks and injects the parts into the frontend shell."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import BUILDER_VERSION
from . import budget as B
from . import checks as C
from . import encode as E
from . import parts as P
from .chips import TIERS, ChipCache, fill

REPO = Path(__file__).resolve().parents[3]
CONTEXT_MARGIN = 20_000  # bytes kept free under the cap when the object context fills the room (meta grows with it)
# Live identification fields the spot check compares (check_bundle.mjs LIVE_ID_FIELDS)
LIVE_SPOT_FIELDS = ["review_note", "identity_label", "match_ambiguous", "ambiguous_mmsi", "az_shift_m", "match_alt_dist_m"]


@dataclass
class Options:
    build: str
    frontend: Path = REPO / "app" / "frontend" / "dist-single" / "index.html"
    out_dir: Path = REPO / "app" / "build" / "out"
    data_dir: Path | None = None
    chips_mb: float | None = None
    no_chips: bool = False
    fetch_chips: bool = True
    cache: bool = True
    spot_n: int = 50
    env_path: Path = REPO / ".env"
    budgets: dict | None = None  # override of budget.BUDGETS[build] (tests)
    cap: int = B.CAP
    log: object = print


@dataclass
class Plan:
    """Drop-rule state: which rules are on and what they removed."""
    rule4: bool = False
    rule5: bool = False
    rule6: bool = False
    lights_cloud: bool = False
    camau_half: bool = False
    dropped_passes: list = field(default_factory=list)
    chips_budget: int = 0
    contact_optional: list | None = None
    contact_left: list = field(default_factory=list)
    light_optional: list | None = None
    light_left: list = field(default_factory=list)
    light_sites: bool = False
    vessel_optional: list | None = None
    vessel_left: list = field(default_factory=list)


class Bundle:
    def __init__(self, opt: Options):
        self.opt = opt
        self.build = opt.build
        self.log = opt.log
        self.dropped: list[dict] = []
        self.timings: dict[str, float] = {}
        self.info: dict = {}
        self.context_sizes: dict[str, int] = {}
        self.light_ids: list = []
        self.t0 = time.time()

    # ------------------------------------------------------------------ setup
    def budget(self, part: str) -> int:
        if self.opt.budgets and part in self.opt.budgets:
            return int(self.opt.budgets[part])
        return B.budget_bytes(self.build, part)

    def tick(self, name: str, t: float):
        self.timings[name] = round(time.time() - t, 2)

    def load(self):
        t = time.time()
        from scs_api.config import Settings
        from scs_api.store import Store

        kw = {"build": self.build, "check_interval_s": 0.0, "frontend_dist": REPO / "app" / "build" / "no_frontend"}
        if self.opt.data_dir is not None:
            kw["data_dir"] = Path(self.opt.data_dir)
        self.settings = Settings(**kw)
        self.store = Store(self.settings)
        self.store.wait_background()
        cache_dir = (self.settings.data_dir / "cache" / "bundle") if self.opt.cache else None
        self.ctx = P.Ctx(self.build, self.store, self.log, cache_dir)
        self.tick("load_store", t)
        t = time.time()
        self.cfr_all = P.contact_frame(self.ctx)
        self.tick("contacts_records", t)
        t = time.time()
        self.vdf_all = P.vessel_frame(self.ctx)
        self.leads_all = P.lead_frame(self.ctx)
        self.lf = P.light_frame(self.ctx)
        self.tick("vessels_leads_lights_records", t)
        rank = {}
        for i, L in enumerate(self.leads_all):
            if L.get("primary_type") == "contact":
                rank.setdefault(str(L.get("primary_id")), i)
        self.lead_rank = rank
        self.cfr_all = P.order_contacts(self.cfr_all, rank) if len(self.cfr_all) else self.cfr_all
        # the gfw rows a contact references before any drop (the contract's research vessel table plus stubs)
        if self.build == "research" and len(self.cfr_all):
            keys = set(self.cfr_all["vessel_key"].dropna()) | set(self.cfr_all["nearest_vessel_key"].dropna())
            self.ref_all = keys
        else:
            self.ref_all = set()

    # ------------------------------------------------------------------ contacts and vessels
    def rule_masks(self) -> dict[int, np.ndarray]:
        """Rows of the full contact frame that drop rules 4 and 5 take out of the bulk columns."""
        fr = self.cfr_all
        sept = (fr["view"] == "regional") & (fr["_source"] != "structures")
        medium_false = (fr["confidence"] == "medium") & fr["cnn_vessel"].map(lambda v: v is False or v == 0)
        return {4: (sept & medium_false).to_numpy(), 5: (sept & medium_false & (fr["ais_status"] != "matched")).to_numpy()}

    def contact_rows(self, plan: Plan) -> pd.DataFrame:
        fr = self.cfr_all
        if not len(fr):
            return fr
        keep = np.ones(len(fr), bool)
        masks = self.rule_masks()
        if plan.rule4 and self.build == "open":
            keep &= ~masks[4]
        if plan.rule5 and self.build == "research":
            keep &= ~masks[5]
        if plan.dropped_passes:
            keep &= ~((fr["view"] == "live") & fr["pass_id"].isin(plan.dropped_passes)).to_numpy()
        return fr[keep].reset_index(drop=True)

    def count_drops(self):
        """`rows` of each rule 4, 5 and 7 entry in meta.dropped: the contacts that left the bulk columns."""
        if not len(self.cfr_all):
            return
        masks = self.rule_masks()
        live_n = self.cfr_all[self.cfr_all["view"] == "live"]["pass_id"].value_counts().to_dict()
        for d in self.dropped:
            if d.get("kind") != "rule" or d.get("part") != "contacts":
                continue
            if d.get("rule") in (4, 5):
                d["rows"] = int(masks[d["rule"]].sum())
            elif d.get("rule") == 7 and d.get("pass_id"):
                d["rows"] = int(live_n.get(d["pass_id"], 0))

    def vessel_rows(self, rows: pd.DataFrame, plan: Plan) -> pd.DataFrame:
        v = self.vdf_all
        if not len(v):
            return v
        if self.build == "open":
            return v[v["_src"] == "aisstream"].reset_index(drop=True)
        ais = v[v["_src"] == "aisstream"]
        g = v[(v["_src"] != "aisstream") & v["vessel_key"].isin(self.ref_all)]
        if plan.rule6:
            live = set(rows["vessel_key"].dropna()) | set(rows["nearest_vessel_key"].dropna())
            g = g[(~g["stub"].astype(bool)) | g["vessel_key"].isin(live)]
        return pd.concat([ais, g], ignore_index=True)

    def encode_contacts(self, rows, vdf, optional, records=None):
        vidx = {k: i for i, k in enumerate(vdf["vessel_key"])} if len(vdf) else {}
        vref, nref, own, idstats = P.identity_resolution(rows, vidx, vdf)
        cols = P.contacts_columns(rows, vref, nref, optional, self.store.cav())
        sets = P.prov_sets()
        names = self.prov_names(rows)
        present = sorted(set(names))
        main = pd.Series(names).value_counts().idxmax() if len(rows) else "regional"
        part = E.part("contacts", len(rows), cols, records=records if records is not None else {}, prov=sets.get(main, {}),
                      note=("Bulk columns of the D1 fields plus extensions; identity by reference (vessel_ref, nearest_ref); "
                            "mission is the first 3 characters of det_id when the column is absent; records hold the other "
                            "fields of the live contacts with identification evidence (matched, hand-checked, ambiguous), "
                            "the chip contacts, the lead evidence contacts and, while the budget allows, the other unmatched "
                            "live contacts; a contact whose identity strings differ from its vessel row carries them in its "
                            "record; field_prov (contract 1.3.0) is in the local app"))
        part["prov_sets"] = {s: sets[s] for s in present if s in sets}
        part["prov_set_rule"] = ("view live -> live; view camau -> camau; research_only true -> research; confidence fixed -> "
                                 "structures; else regional")
        return part, own, idstats

    @staticmethod
    def prov_names(rows: pd.DataFrame) -> list[str]:
        if not len(rows):
            return []
        ro = rows["research_only"] if "research_only" in rows else pd.Series([None] * len(rows))
        return [P.prov_set_name(v, r, c) for v, r, c in zip(rows["view"], ro, rows["confidence"])]

    def must_records(self, rows: pd.DataFrame, part: dict, own: dict) -> dict[str, tuple[str, dict]]:
        """{det_id: (reason, record)} of the records every page carries: full records of the live contacts with
        identification evidence (README reading 16: every matched one, every hand-checked one, every ambiguous one),
        and the identity strings of every contact whose own strings differ from its vessel row (behaviour rule 6;
        those alone when the contact needs no full record)."""
        out: dict[str, tuple[str, dict]] = {}
        if not len(rows):
            return out
        idx = {d: i for i, d in enumerate(rows["det_id"])}
        live = rows[rows["view"] == "live"]
        if len(live):
            def flag(col):
                return live[col].map(lambda v: v is True or v == 1) if col in live else pd.Series(False, index=live.index)
            noted = (live["review_note"].map(lambda v: isinstance(v, str) and bool(v.strip())) if "review_note" in live
                     else pd.Series(False, index=live.index))
            reasons = [(live["ais_status"] == "matched", "live_matched"), (noted, "live_hand_checked"),
                       (flag("match_ambiguous"), "live_ambiguous")]
            for mask, why in reasons:
                for d in live.loc[mask, "det_id"]:
                    if d not in out:
                        rec = self.full_record(rows, idx[d], part)
                        rec.update(own.get(d, {}))
                        out[d] = (why, rec)
        for d, diff in own.items():
            if d not in out:
                out[d] = ("identity_or_nearest_overrides", dict(diff))
        return out

    @staticmethod
    def prov_base(rows: pd.DataFrame, i: int, part: dict) -> dict:
        """The default provenance of row i: its prov_sets entry (README reading 16), else the part's prov."""
        row = rows.iloc[i]
        name = P.prov_set_name(row["view"], row.get("research_only"), row["confidence"])
        return (part.get("prov_sets") or {}).get(name) or part.get("prov") or {}

    def full_record(self, rows: pd.DataFrame, i: int, part: dict) -> dict:
        return P.contact_record(rows.iloc[i].to_dict(), set(part["columns"]), self.prov_base(rows, i, part))

    def fit_contacts(self, plan: Plan):
        """Core columns plus the must-have records (live identification evidence, own identity strings) must fit; rule
        4 or 5, then rule 7; then optional columns while they fit, keeping a reserve for the optional records."""
        budget = self.budget("contacts")
        reserve = min(int(0.1 * budget), 300_000 if self.build == "open" else 200_000)  # room kept for optional records
        while True:
            rows = self.contact_rows(plan)
            vdf = self.vessel_rows(rows, plan)
            part, own, idstats = self.encode_contacts(rows, vdf, [])
            must = self.must_records(rows, part, own)
            must_bytes = sum(len(E.dumps({d: rec})) for d, (_, rec) in must.items())
            size = E.element_bytes("contacts", part) + must_bytes
            if size <= budget or budget == 0:
                break
            if self.build == "open" and not plan.rule4:
                plan.rule4 = True
                self.drop(4, "contacts", "part over its budget", size)
                continue
            if self.build == "research" and not plan.rule5:
                plan.rule5 = True
                self.drop(5, "contacts", "part over its budget", size)
                continue
            nxt = self.next_live_pass(plan)
            if nxt is None:
                raise B.BudgetError(f"contacts part {size:,} bytes over its budget {budget:,} after rules 4/5 and 7")
            plan.dropped_passes.append(nxt)
            self.drop(7, "contacts", f"live pass {nxt} left the bulk columns", size, pass_id=nxt)
        # optional columns in priority order while the part stays under budget minus the record reserve
        if plan.contact_optional is None:
            chosen, left = [], []
            base = size
            for name, how in P.CONTACT_OPTIONAL:
                if name not in rows:
                    continue
                vals = P.obj_series(rows[name])
                if all(v is None for v in vals):
                    continue
                spec = P.encode_col(vals, how)
                add = E.column_bytes(spec) + len(name) + 4
                if base + add <= budget - reserve:
                    chosen.append((name, how))
                    base += add
                else:
                    left.append(name)
            plan.contact_optional = chosen
            plan.contact_left = left
        if plan.contact_left:
            self.dropped.append({"part": "contacts", "kind": "columns", "rule": None,
                                 "reason": "extension columns left out to keep the part within its budget (full record "
                                           "in the local app and the GeoPackage)", "columns": plan.contact_left})
        return rows, vdf

    def next_live_pass(self, plan: Plan) -> str | None:
        fr = self.cfr_all
        live = fr[fr["view"] == "live"]
        if not len(live):
            return None
        ps = []
        for pid, g in live.groupby("pass_id"):
            if pid in plan.dropped_passes:
                continue
            ps.append({"pass_id": pid, "start_utc": g["acq_utc"].min(), "n_matched": int((g["ais_status"] == "matched").sum()),
                       "n_unmatched": int((g["ais_status"] == "unmatched").sum())})
        order = B.live_pass_drop_order(ps)
        return order[0] if order else None

    def drop(self, rule: int, part: str, why: str, size: int | None = None, **more):
        d = {"part": part, "kind": "rule", "rule": rule, "reason": B.RULES[rule][1], "trigger": why}
        if size is not None:
            d["size_before"] = int(size)
        d.update(more)
        self.dropped.append(d)
        self.count_drops()
        self.log(f"  drop rule {rule} ({part}): {why}" + (f", {d['rows']:,} contacts" if "rows" in d else ""))

    # ------------------------------------------------------------------ chips
    def chip_candidates(self, rows: pd.DataFrame, lead_info: dict) -> pd.DataFrame:
        if not len(rows):
            return pd.DataFrame(columns=["det_id", "tier"])
        have = set(rows["det_id"])
        order: list[tuple[str, int]] = []
        seen = set()

        def add(ids, tier):
            for d in ids:
                if d in have and d not in seen:
                    seen.add(d)
                    order.append((d, tier))
        add([pid for part, pid in lead_info.get("primary_ids", []) if part == "contacts"], 1)
        live = rows[rows["view"] == "live"].sort_values(["acq_utc", "det_id"], ascending=[False, True])
        add(live.loc[live["ais_status"] == "matched", "det_id"], 2)
        acc = live["cnn_vessel"].map(lambda v: v is True or v == 1)
        add(live.loc[(live["ais_status"] == "unmatched") & acc, "det_id"], 3)
        if len(live):
            newest = live["pass_id"].iloc[0]
            lp = live[(live["pass_id"] == newest) & live["confidence"].isin(["high", "medium"])].copy()
            lp["_c"] = pd.to_numeric(lp["cnn_score"], errors="coerce").fillna(-1)
            lp["_k"] = lp["confidence"].map({"high": 0, "medium": 1})
            add(lp.sort_values(["_k", "_c"], ascending=[True, False])["det_id"], 4)
        sept = rows[(rows["view"] == "regional") & (rows["confidence"] == "high")]
        h = sept["det_id"].map(lambda s: hashlib.md5(s.encode()).hexdigest())
        add(sept.loc[h.sort_values().index, "det_id"].head(4000), 4)
        return pd.DataFrame(order, columns=["det_id", "tier"])

    def chip_positions(self, cand: pd.DataFrame) -> pd.DataFrame:
        """scene_id and GRD pixel row/col of each candidate: live and Ca Mau rows carry them; September rows come
        from data/detections_regional_all.gpkg (927,124 objects with their pixel positions)."""
        fr = self.cfr_all.set_index("det_id")
        out = cand.copy()
        out["scene_id"] = out["det_id"].map(fr["scene_id"]) if "scene_id" in fr else None
        for c in ("row", "col"):
            out[c] = out["det_id"].map(fr[c]) if c in fr else np.nan
        need = out["row"].isna() & out["det_id"].str.match(r"^S1[CD]_\d{8}T\d{6}_\d{5}$")
        if need.any():
            pos = self.regional_pixels()
            if pos is not None:
                m = out.loc[need, "det_id"].map(pos["row"])
                out.loc[need, "row"] = m
                out.loc[need, "col"] = out.loc[need, "det_id"].map(pos["col"])
                sc = out.loc[need, "det_id"].map(pos["scene_id"])
                out.loc[need, "scene_id"] = out.loc[need, "scene_id"].where(out.loc[need, "scene_id"].notna(), sc)
        return out

    def regional_pixels(self):
        p = self.store.cat.guard(self.settings.data_dir / "detections_regional_all.gpkg")
        if not p.exists():
            return None

        def build():
            import pyogrio

            df = pyogrio.read_dataframe(p, layer="detections_regional_4326", columns=["det_id", "row", "col", "scene_id"],
                                        read_geometry=False, where="confidence IN ('high', 'medium', 'fixed')")
            return df.set_index("det_id")
        return P.cached(self.ctx, "regional_pixels", [], build, extra=str(p.stat().st_mtime_ns))

    def make_chips(self, rows, lead_info, budget) -> tuple[dict, dict]:
        if self.opt.no_chips or budget <= 0:
            return {}, {"embedded": 0, "left_out": 0, "failed": 0, "note": "chips switched off" if self.opt.no_chips else "no budget"}
        cand = self.chip_candidates(rows, lead_info)
        cand = self.chip_positions(cand)
        cache = ChipCache(self.settings.data_dir / "cache" / "chips")
        chips, stats = fill(cand, budget - len('<script type="application/json" id="scs-part-chips"></script>\n'), cache,
                            fetch=self.opt.fetch_chips, log=self.log)
        stats["selection"] = {str(k): v for k, v in TIERS.items()}
        stats["candidates_by_tier"] = {str(k): int(v) for k, v in cand["tier"].value_counts().sort_index().items()}
        return chips, stats

    # ------------------------------------------------------------------ records
    def contact_records(self, rows, part, own, chips, lead_part, budget) -> dict:
        """Records within the part budget. Always (the build fails if they do not fit): own identity strings (rule 6)
        and the live contacts whose record holds identification evidence (README reading 16): every matched one
        (identity label, azimuth shift), every hand-checked one (review_note) and every ambiguous one (candidate
        MMSIs). Then, while the budget allows: chip contacts, lead evidence contacts, the other unmatched live
        contacts (newest pass first)."""
        records: dict[str, dict] = {}
        idx = {d: i for i, d in enumerate(rows["det_id"])}
        size = E.element_bytes("contacts", part)
        stats = {"identity_or_nearest_overrides": 0, "live_matched": 0, "live_hand_checked": 0, "live_ambiguous": 0,
                 "chip": 0, "lead_evidence": 0, "live_unmatched": 0, "left_out": 0}
        live = rows[rows["view"] == "live"] if len(rows) else rows
        minimal = set()  # own identity strings only, no full record yet
        for d, (why, rec) in self.must_records(rows, part, own).items():
            records[d] = rec
            size += len(E.dumps({d: rec}))
            stats[why] += 1
            if why == "identity_or_nearest_overrides":
                minimal.add(d)
        want = [(d, "chip") for d in chips if d in idx]
        if lead_part:
            for lid, rec in (lead_part.get("records") or {}).items():
                for e in rec.get("evidence") or []:
                    if e.get("type") == "contact" and str(e.get("id")) in idx:
                        want.append((str(e.get("id")), "lead_evidence"))
        if len(live):
            want += [(d, "live_unmatched") for d in live.loc[live["ais_status"] == "unmatched", "det_id"]]
        for d, why in want:
            if d in records and d not in minimal:
                continue
            rec = self.full_record(rows, idx[d], part)
            rec.update(own.get(d, {}))
            add = len(E.dumps({d: rec})) - (len(E.dumps({d: records[d]})) if d in records else 0)
            if size + add > budget:
                stats["left_out"] += 1
                continue
            records[d] = rec
            minimal.discard(d)
            size += add
            stats[why] += 1
        if size > budget:
            raise B.BudgetError(f"contacts part {size:,} bytes over its budget {budget:,} with only the identity and live identification records")
        return records, stats

    # ------------------------------------------------------------------ the whole page
    def assemble(self, plan: Plan) -> dict:
        t = time.time()
        self.dropped = [d for d in self.dropped if d.get("kind") == "rule" and d.get("rule") is not None]
        parts: dict[str, dict] = {}
        info: dict = {}
        rows, vdf = self.fit_contacts(plan)
        cpart, own, idstats = self.encode_contacts(rows, vdf, plan.contact_optional or [])
        info["identity"] = idstats
        # vessels (rule 6 when over)
        tracks, tinfo = P.tracks_block(self.ctx, {k: i for i, k in enumerate(vdf["vessel_key"])})
        vnote = ("aisstream vessels (flag = ITU country of the MID, as claimed by the transponder)" if self.build == "open" else
                 "aisstream vessels, then GFW identities referenced by the contacts of this page (stub = nearest-AIS vessel "
                 "known only from GFW presence data)") + "; 'An AIS gap is not proof of intent.'"
        vb = self.budget("vessels")
        vpart = P.vessels_part(self.ctx, vdf, tracks, vnote, optional=[])
        if E.element_bytes("vessels", vpart) > vb and self.build == "research" and not plan.rule6:
            plan.rule6 = True
            plan.vessel_optional = None
            self.drop(6, "vessels", "part over its budget", E.element_bytes("vessels", vpart))
            return self.assemble(plan)
        if E.element_bytes("vessels", vpart) > vb:
            raise B.BudgetError(f"vessels part {E.element_bytes('vessels', vpart):,} bytes over its budget {vb:,}")
        if plan.vessel_optional is None:
            chosen, left = [], []
            base = E.element_bytes("vessels", vpart)
            for name, how in P.VESSEL_OPTIONAL:
                if name not in vdf:
                    continue
                spec = P.encode_col(P.obj_series(vdf[name]), how) if how != "time" else E.time_col(vdf[name])
                add = E.column_bytes(spec) + len(name) + 4
                if base + add <= vb:
                    chosen.append((name, how))
                    base += add
                else:
                    left.append(name)
            plan.vessel_optional, plan.vessel_left = chosen, left
        if plan.vessel_left:
            self.dropped.append({"part": "vessels", "kind": "columns", "rule": None, "columns": plan.vessel_left,
                                 "reason": "vessel columns left out to keep the part within its budget (full record in the local app)"})
        vpart = P.vessels_part(self.ctx, vdf, tracks, vnote, optional=plan.vessel_optional)
        info["vessels"] = {"rows": int(len(vdf)), **tinfo,
                           "stubs": int(vdf["stub"].astype(bool).sum()) if "stub" in vdf and len(vdf) else 0}
        # lights (rule 2 when over), then optional columns and the sites block while they fit
        lb = self.budget("lights")
        lpart, linfo = P.lights_part(self.ctx, self.lf, [], plan.lights_cloud)
        if E.element_bytes("lights", lpart) > lb and not plan.lights_cloud:
            plan.lights_cloud = True
            plan.light_optional = None
            self.drop(2, "lights", "part over its budget", E.element_bytes("lights", lpart))
            lpart, linfo = P.lights_part(self.ctx, self.lf, [], True)
        if E.element_bytes("lights", lpart) > lb:
            raise B.BudgetError(f"lights part {E.element_bytes('lights', lpart):,} bytes over its budget {lb:,}")
        if plan.light_optional is None:
            chosen, left = [], []
            base = E.element_bytes("lights", lpart)
            rows_l = self.lf if not plan.lights_cloud else self.lf[self.lf["quality"] != "under_cloud"]
            for name, how in P.LIGHT_OPTIONAL:
                if name not in rows_l:
                    continue
                spec = P.encode_col(P.obj_series(rows_l[name]), how)
                add = E.column_bytes(spec) + len(name) + 4
                if base + add <= lb:
                    chosen.append((name, how))
                    base += add
                else:
                    left.append(name)
            sb = P.sites_block(self.ctx)
            plan.light_sites = sb is not None and base + len(E.dumps(sb)) + 10 <= lb
            if not plan.light_sites:
                left.append("sites (block)")
            plan.light_optional = chosen
            plan.light_left = left
        if plan.light_left:
            self.dropped.append({"part": "lights", "kind": "columns", "rule": None, "columns": plan.light_left,
                                 "reason": "light columns left out to keep the part within its budget (full record in the local app)"})
        lpart, linfo = P.lights_part(self.ctx, self.lf, plan.light_optional, plan.lights_cloud, with_sites=plan.light_sites)
        info["lights"] = linfo
        self.light_ids = (self.lf.loc[self.lf["quality"] != "under_cloud", "light_id"] if plan.lights_cloud and len(self.lf)
                          else (self.lf["light_id"] if len(self.lf) else pd.Series([], dtype=object))).tolist()
        # leads
        lids = self.lf["light_id"] if len(self.lf) else pd.Series([], dtype=object)
        if plan.lights_cloud and len(self.lf):
            lids = self.lf.loc[self.lf["quality"] != "under_cloud", "light_id"]
        recs_c, night = P.cell_frame(self.ctx)
        row_of = {"contacts": {d: i for i, d in enumerate(rows["det_id"])}, "lights": {d: i for i, d in enumerate(lids)},
                  "cells": {r["cell_id"]: i for i, r in enumerate(recs_c)}}
        # when not every lead fits, a quarter of the part is kept for the evidence records of the top leads
        lpart_leads, lead_info = P.leads_part(self.ctx, self.leads_all, row_of, self.budget("leads"),
                                              record_reserve=self.budget("leads") // 4)
        if lead_info.get("dropped_budget"):
            self.dropped.append({"part": "leads", "kind": "rule", "rule": None, "rows": lead_info["dropped_budget"],
                                 "reason": "lowest-priority leads left out to keep the part within its budget (contract 6.2: kept in priority order, the rest counted)"})
        if lead_info.get("dropped_primary_missing"):
            self.dropped.append({"part": "leads", "kind": "rows", "rule": None, "rows": lead_info["dropped_primary_missing"],
                                 "reason": "leads whose primary object is not in this page"})
        info["leads"] = {k: v for k, v in lead_info.items() if k not in ("primary_ids", "kept_ids")}
        # chips
        cb = plan.chips_budget
        chips, cstats = self.make_chips(rows, lead_info, cb)
        info["chips"] = cstats
        # contacts records
        cbud = self.budget("contacts")
        recs, rstats = self.contact_records(rows, cpart, own, chips, lpart_leads, cbud)
        cpart["records"] = recs
        info["contact_records"] = rstats
        if rstats.get("left_out"):
            self.dropped.append({"part": "contacts", "kind": "records", "rule": None, "rows": int(rstats["left_out"]),
                                 "reason": "records (the fields that are not bulk columns) of chip, lead evidence and other "
                                           "unmatched live contacts left out to keep the part within its budget; their bulk "
                                           "columns are in the page, the full record in the local app"})
        primaries = {str(pid) for part_, pid in lead_info.get("primary_ids", []) if part_ == "contacts"}
        # the nearest AIS vessel's name the adapter shows: its nearest_ref vessel row's name when the MMSI agrees
        vname = dict(zip(vdf["vessel_key"], vdf["name"])) if len(vdf) and "name" in vdf else {}
        vmmsi = dict(zip(vdf["vessel_key"], vdf["mmsi"])) if len(vdf) and "mmsi" in vdf else {}
        nk = rows["nearest_vessel_key"].tolist() if "nearest_vessel_key" in rows else [None] * len(rows)
        nm = rows["nearest_ais_mmsi"].tolist() if "nearest_ais_mmsi" in rows else [None] * len(rows)
        near_name = [vname.get(k) if isinstance(k, str) and P.same(vmmsi.get(k), m) else None for k, m in zip(nk, nm)]
        n_bare, bare = P.unrecorded_fields(rows, set(cpart["columns"]), recs, lambda i: self.prov_base(rows, i, cpart), primaries,
                                           derived={"nearest_ais_name": near_name})
        info["contacts_without_record"] = {"rows": n_bare, "fields": bare}
        if bare:
            self.dropped.append({"part": "contacts", "kind": "fields", "rule": None, "rows": n_bare, "columns": bare,
                                 "reason": "fields that are not bulk columns live only in records (contract 6.2): the "
                                           f"{n_bare:,} contacts without a record show their bulk columns, and these "
                                           "fields (rows holding a value) are in the local app and the GeoPackage"})
        if "field_prov" in rows:
            self.dropped.append({"part": "contacts", "kind": "fields", "rule": None, "columns": sorted(P.CONTACT_RECORD_SKIP),
                                 "reason": "contract 1.3.0 field-level provenance (source, valid time and source text per "
                                           "field) left out of the contact records: the page does not read it; the local "
                                           "app serves it; each field's source stays in prov"})
        info["contacts"] = {"rows": int(len(rows)), "by_view": rows["view"].value_counts().to_dict() if len(rows) else {},
                            "by_status": rows["ais_status"].value_counts().to_dict() if len(rows) else {},
                            "columns": list(cpart["columns"]),
                            "matched": int((rows["ais_status"] == "matched").sum()) if len(rows) else 0}
        parts["contacts"] = cpart
        parts["chips"] = chips
        parts["lights"] = lpart
        parts["vessels"] = vpart
        # events
        ep, einfo = P.events_part(self.ctx, self.cfr_all)  # the producer's rule: every September contact
        parts["events"] = ep
        info["events"] = einfo
        if lpart_leads is not None:
            parts["leads"] = lpart_leads
        live_in = rows[rows["view"] == "live"]["pass_id"].value_counts().to_dict() if len(rows) else {}
        pp, pinfo = P.passes_part(self.ctx, set(plan.dropped_passes), live_in, budget=self.budget("passes"))
        parts["passes"] = pp
        info["passes"] = pinfo
        left = int(pinfo.get("ais_only_total", 0)) - int(pinfo.get("ais_only_in_bundle", 0))
        if left > 0:
            self.dropped.append({"part": "passes", "kind": "rows", "rule": None, "columns": ["ais_only"], "rows": left,
                                 "reason": f"AIS-only vessels of the live passes beyond the part budget left out "
                                           f"({pinfo.get('ais_only_in_bundle', 0):,} of {pinfo.get('ais_only_total', 0):,} in "
                                           "the page, tested sea first, then the longest; n_ais_only is the pass total; the "
                                           "local app serves every vessel)"})
        cp, cinfo = P.cells_part(self.ctx, recs_c, night, with_expected=True)
        if cp is not None and E.element_bytes("cells", cp) > self.budget("cells"):
            cp, cinfo = P.cells_part(self.ctx, recs_c, night, with_expected="counts")
            self.dropped.append({"part": "cells", "kind": "block", "rule": None,
                                 "reason": "expected activity as counts only (tested, flagged and robust-flagged nights per cell); "
                                           "the newest night's observed, expected and z did not fit the cells budget (local app serves them)"})
        if cp is not None and E.element_bytes("cells", cp) > self.budget("cells"):
            cp, cinfo = P.cells_part(self.ctx, recs_c, night, with_expected=False)
            cinfo["expected_activity"] = "left out"
            self.dropped.append({"part": "cells", "kind": "block", "rule": None,
                                 "reason": "expected activity left out: even the counts did not fit the cells budget (local app serves it)"})
        if cp is not None:
            parts["cells"] = cp
        info["cells"] = cinfo
        gp, ginfo = P.geo_part(self.ctx)
        parts["geo"] = gp
        info["geo"] = ginfo
        rp, rinfo = P.rasters_part(self.ctx)
        parts["rasters"] = rp
        info["rasters"] = rinfo
        if self.build == "open" and self.budget("camau") > 0:
            cm, minfo = P.camau_part(self.ctx, plan.camau_half)
            if cm is not None and E.element_bytes("camau", cm) > self.budget("camau") and not plan.camau_half:
                plan.camau_half = True
                self.drop(3, "camau", "part over its budget", E.element_bytes("camau", cm))
                cm, minfo = P.camau_part(self.ctx, True)
            if cm is not None:
                parts["camau"] = cm
            info["camau"] = minfo
        self.count_drops()
        self.tick("assemble", t)
        self.info = info
        self.rows = rows
        self.vdf = vdf
        return parts

    def fit_context(self, parts: dict, plan: Plan, meta: dict) -> dict:
        """Board D5.3 object context of contacts and lights as README reading 13 blocks, in the room the other parts
        leave under the cap (its bytes are not in the contract 6.3 budgets, open item 8.19): every row with context
        keeps `time_utc` and `region`; fields join in CONTEXT_PRIORITY order while both blocks fit, so a Context
        section never says an object has no context when the table holds a row for it. Fields left out are listed in
        meta.dropped and named in the block's caveat. Context never makes another part drop. Returns the new meta."""
        t = time.time()
        self.context_sizes = {}
        full = {}
        cinfo: dict = {}
        if "contacts" in parts and len(self.rows):
            full["contacts"], cinfo["contacts"] = P.context_block(self.ctx, "contact", self.rows["det_id"], self.rows["_source"])
        if "lights" in parts and self.light_ids:
            full["lights"], cinfo["lights"] = P.context_block(self.ctx, "light", self.light_ids)
        full = {k: v for k, v in full.items() if v is not None}
        n_fields = len(next(iter(full.values()))["fields"]) if full else 0
        room = self.opt.cap - self.total(parts, meta) - CONTEXT_MARGIN

        def cost(items):
            return sum(P.context_bytes(P.context_subset(b, items, n_fields)) for b in full.values())
        chosen: list[str] = []
        base = cost([])
        if full and base <= room:
            for it in P.CONTEXT_PRIORITY:
                if cost(chosen + [it]) <= room:
                    chosen.append(it)
        while True:
            fits = bool(full) and base <= room
            for k, b in full.items():
                sub = P.context_subset(b, chosen, n_fields) if fits else None
                if sub is None:
                    parts[k].pop("object_context", None)
                else:
                    parts[k]["object_context"] = sub
                self.context_sizes[k] = P.context_bytes(sub)
            self.dropped = [d for d in self.dropped if d.get("kind") != "object_context"]
            for k, b in full.items():
                have = int(cinfo[k]["with_context"])
                fields = list(b["fields"])
                left = [f for f in fields if not fits or f not in chosen]
                if not fits:
                    self.dropped.append({"part": k, "kind": "object_context", "rule": None, "rows": have, "columns": fields,
                                         "reason": "object context (board D5.3) left out: no room under the cap; these "
                                                   "objects have context in the local app"})
                elif left or ("cell_id" not in chosen):
                    self.dropped.append({"part": k, "kind": "object_context", "rule": None, "rows": have,
                                         "columns": left + ([] if "cell_id" in chosen else ["cell_id"]),
                                         "reason": f"object context fields left out of the README reading 13 block to stay "
                                                   f"within the cap ({len(fields) - len(left)} of {len(fields)} fields "
                                                   "kept for every object with context); the local app serves all of them"})
                cinfo[k].update({"bytes": self.context_sizes[k], "fields": [f for f in chosen if f in fields] if fits else [],
                                 "left_out": left, "cell_id": fits and "cell_id" in chosen})
            self.info["object_context"] = {**cinfo, "room_bytes": int(room), "margin_bytes": CONTEXT_MARGIN,
                                           "scales": P.CONTEXT_PAGE_SCALE, "priority": P.CONTEXT_PRIORITY}
            meta = self.meta(parts, plan)
            if self.total(parts, meta) <= self.opt.cap or not fits:
                break
            if chosen:
                chosen.pop()  # meta grew past the margin: one field fewer
            else:
                base = room + 1  # not even the base fits
        self.tick("object_context", t)
        self.log(f"  object context: room {room:,} bytes; {', '.join(f'{k} {v:,} bytes' for k, v in self.context_sizes.items())}; "
                 f"fields kept {len([c for c in chosen if c != 'cell_id'])} of {n_fields}" + (", cell_id kept" if "cell_id" in chosen else ""))
        return meta

    def part_sizes(self, parts: dict) -> dict[str, int]:
        return {k: E.element_bytes(k, v) for k, v in parts.items()}

    def check_parts(self, sizes: dict[str, int]):
        """Each part within its contract 6.3 budget. The object context blocks are not in those budgets (contract 6.4,
        open item 8.19): they fill the room the other parts leave under the cap, so their bytes are left out here."""
        ctxb = self.context_sizes
        over = {k: (s - ctxb.get(k, 0), self.budget(k)) for k, s in sizes.items()
                if k != "meta" and s - ctxb.get(k, 0) > self.budget(k)}
        if over:
            raise B.BudgetError("parts over their budgets: " + ", ".join(f"{k} {s:,} > {b:,}" for k, (s, b) in over.items()))

    def run(self) -> dict:
        opt = self.opt
        shell = Path(opt.frontend).read_text(encoding="utf-8")
        if "</body>" not in shell:
            raise RuntimeError(f"no </body> in {opt.frontend}")
        if 'id="scs-part-' in shell:
            raise RuntimeError(f"{opt.frontend} already holds bundle parts; give the frontend's dist-single shell")
        self.shell_bytes = len(shell.encode("utf-8"))
        self.load()
        plan = Plan(chips_budget=int(min(opt.chips_mb * B.MB, self.budget("chips")) if opt.chips_mb is not None
                                     else self.budget("chips")))
        parts = self.assemble(plan)
        meta = self.meta(parts, plan)
        total = self.total(parts, meta)
        # total cap: rules 1 to 7 in order until the page fits
        step = 1
        while total > opt.cap:
            self.log(f"  page {total:,} bytes over the cap {opt.cap:,}: applying rule {step}")
            if step == 1 and plan.chips_budget > 0:
                plan.chips_budget = max(0, plan.chips_budget - B.CHIP_STEP)
                self.drop(1, "chips", f"page over the cap; chip budget now {plan.chips_budget:,} bytes", total)
            elif step == 2 and not plan.lights_cloud:
                plan.lights_cloud, plan.light_optional = True, None
                self.drop(2, "lights", "page over the cap", total)
                step += 1
            elif step == 3 and self.build == "open" and not plan.camau_half and "camau" in parts:
                plan.camau_half = True
                self.drop(3, "camau", "page over the cap", total)
                step += 1
            elif step == 4 and self.build == "open" and not plan.rule4:
                plan.rule4, plan.contact_optional = True, None
                self.drop(4, "contacts", "page over the cap", total)
                step += 1
            elif step == 5 and self.build == "research" and not plan.rule5:
                plan.rule5, plan.contact_optional = True, None
                self.drop(5, "contacts", "page over the cap", total)
                step += 1
            elif step == 6 and self.build == "research" and not plan.rule6:
                plan.rule6 = True
                self.drop(6, "vessels", "page over the cap", total)
                step += 1
            elif step == 7:
                nxt = self.next_live_pass(plan)
                if nxt is None:
                    raise B.BudgetError(f"page {total:,} bytes over the cap {opt.cap:,} after every drop rule")
                plan.dropped_passes.append(nxt)
                plan.contact_optional = None
                self.drop(7, "contacts", f"page over the cap; live pass {nxt} left the bulk columns", total, pass_id=nxt)
            else:
                step += 1
                continue
            parts = self.assemble(plan)
            meta = self.meta(parts, plan)
            total = self.total(parts, meta)
            if step == 1 and plan.chips_budget == 0:
                step = 2
        meta = self.fit_context(parts, plan, meta)
        sizes = self.part_sizes(parts)
        self.check_parts(sizes)
        if sizes.get("meta", 0) > self.budget("meta"):
            self.log(f"  note: meta {sizes['meta']:,} bytes over its {self.budget('meta'):,} byte budget")
        html = self.inject(shell, meta, parts)
        size = len(html.encode("utf-8"))
        if size > opt.cap:
            raise B.BudgetError(f"page {size:,} bytes over the cap {opt.cap:,}")
        problems = self.content_checks(html, parts, meta)
        if problems:
            raise RuntimeError("content checks failed: " + "; ".join(problems))
        out = self.write(html)
        report = self.report(out, size, sizes, meta, parts)
        return report

    # ------------------------------------------------------------------ meta
    def meta(self, parts: dict, plan: Plan) -> dict:
        from darkvessel.config import DARK_CAVEAT_SHORT, PRODUCT_CAVEAT

        from scs_api import APP_VERSION, CONTRACT_VERSION
        from scs_api.config import ATTRIBUTION, BUILD_LABELS, DATA_CREDIT, RESEARCH_LABEL

        st = self.store
        m = st.meta()
        files = m.get("files") or []
        in_bundle = {"live_contacts": self.info["contacts"]["by_view"].get("live", 0),
                     "lights": self.info["lights"]["lights"], "leads_open": self.info["leads"].get("kept", 0),
                     "leads_research": self.info["leads"].get("kept", 0)}
        for f in files:
            if f["key"] in in_bundle:
                f["in_bundle"] = in_bundle[f["key"]]
        counts = dict(m.get("counts") or {})
        counts["in_bundle"] = {"contacts": self.info["contacts"]["rows"], "vessels": self.info["vessels"]["rows"],
                               "lights": self.info["lights"]["lights"], "leads": self.info["leads"].get("kept", 0),
                               "chips": len(parts.get("chips") or {}), "passes": self.info["passes"]["passes"],
                               "cells": self.info["cells"].get("cells", 0), "events_records": len((parts.get("events") or {}).get("records") or [])}
        meta = {
            "contract_version": CONTRACT_VERSION, "build": self.build, "build_label": BUILD_LABELS[self.build],
            "caveat": PRODUCT_CAVEAT, "product_caveat": PRODUCT_CAVEAT, "caveat_short": DARK_CAVEAT_SHORT,
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "build_time_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "loaded_utc": m.get("loaded_utc"), "git_hash": m.get("git_hash"), "app_version": APP_VERSION,
            "builder_version": BUILDER_VERSION, "priority_model_id": m.get("priority_model_id"),
            "sources": m.get("sources"), "files": files, "counts": counts,
            "ais_recording": m.get("ais_recording"), "live_rules": m.get("live_rules"), "data_credit": DATA_CREDIT,
            "parts": {}, "budgets": {k: self.budget(k) for k in B.PART_ORDER}, "cap_bytes": self.opt.cap,
            "dropped": self.dropped, "chips": self.info.get("chips"), "identity": self.info.get("identity"),
            "identity_note": ("identity strings are stored once per vessel and resolved through vessel_ref; a contact whose "
                              "own strings differ from its vessel row carries them in its record"),
            "contact_records": self.info.get("contact_records"), "leads": self.info.get("leads"),
            "contacts_without_record": self.info.get("contacts_without_record"),
            "object_context": {**(self.info.get("object_context") or {}),
                               "budget": "not in the contract 6.3 budgets (open item 8.19): the blocks fill the room the "
                                         "other parts leave under the cap"},
            "cells": self.info.get("cells"), "rasters": self.info.get("rasters"), "events": self.info.get("events"),
            "precision": {"lon_lat_deg": 1e-5, "length_est_m": 0.1, "cnn_score": 0.001, "distances_m": 1, "ais_reach": 0.001,
                          "note": "values decode to raw / s; the column's t and s in each part are authoritative"},
            "fixture": None,
        }
        if self.build == "research":
            meta["research_label"] = RESEARCH_LABEL
            meta["attribution"] = ATTRIBUTION
            meta["licence"] = "CC BY-NC 4.0 for the whole research build (Global Fishing Watch data, noncommercial)"
        sizes = {k: E.element_bytes(k, v) for k, v in parts.items()}
        meta["parts"] = {**sizes, "meta": 0, "shell": self.shell_bytes}
        meta["parts_object_context"] = {k: v for k, v in self.context_sizes.items() if v}
        meta["total_bytes"] = 0
        for _ in range(6):  # meta's own size and the page total, written into meta itself, until they settle
            m = E.element_bytes("meta", meta)
            t = self.shell_bytes + sum(sizes.values()) + m
            if meta["parts"]["meta"] == m and meta["total_bytes"] == t:
                break
            meta["parts"]["meta"], meta["total_bytes"] = m, t
        return meta

    def total(self, parts: dict, meta: dict) -> int:
        return self.shell_bytes + sum(E.element_bytes(k, v) for k, v in parts.items()) + E.element_bytes("meta", meta)

    # ------------------------------------------------------------------ output
    def inject(self, shell: str, meta: dict, parts: dict) -> str:
        els = [E.element("meta", meta)] + [E.element(k, parts[k]) for k in B.PART_ORDER if k != "meta" and k in parts]
        i = shell.lower().rindex("</body>")
        return shell[:i] + "".join(els) + shell[i:]

    def content_checks(self, html: str, parts: dict, meta: dict) -> list[str]:
        probs = []
        allparts = {"meta": meta, **parts}
        if self.build == "open":
            probs += C.open_build_problems(allparts)
        hits = C.credential_hits(html, C.env_prefixes(self.opt.env_path))
        if hits:
            probs.append(f"credential prefixes of {', '.join(hits)} found in the page")
        vn = C.vendor_name(REPO / "app" / "frontend")
        data_text = "".join(E.dumps(v) for v in allparts.values()).lower()
        if vn and vn in data_text:
            probs.append("the toolkit holder's name appears in a data part (spec PW-16)")
        self.check_info = {"credential_scan": "clean" if not hits else "FOUND", "vendor_name_in_data": bool(vn and vn in data_text),
                           "vendor_name_checked": bool(vn), "dashes_in_data": C.dash_count(data_text),
                           "shell_mentions": C.shell_mentions(html[: html.find('<script type="application/json" id="scs-part-meta">')])}
        return probs

    def write(self, html: str) -> Path:
        out_dir = Path(self.opt.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"scs_vessel_watch_{self.build}.html"
        tmp = out.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, out)
        return out

    def spot_records(self, n: int, seed: int = 20261010) -> dict:
        """API records (GET /api/v1/contacts/{det_id} through the backend app) of n random contacts in the page,
        one of each AIS status present first, for check_bundle.mjs."""
        from fastapi.testclient import TestClient

        from scs_api.app import create_app
        from scs_api.models import d1_spec

        rows = self.rows
        rng = random.Random(seed)
        ids: list[str] = []
        for st in P.STATUS_ORDER:
            sub = rows[rows["ais_status"] == st]["det_id"].tolist()
            if sub:
                ids.append(rng.choice(sub))
        # matched live contacts first (the open build's identifications, P0), then other matched contacts
        live = rows["view"] == "live"
        live_matched = rows[live & (rows["ais_status"] == "matched")]["det_id"].tolist()
        other_matched = rows[~live & (rows["ais_status"] == "matched")]["det_id"].tolist()
        extra = rng.sample(live_matched, min(len(live_matched), max(0, n // 5)))
        extra += rng.sample(other_matched, min(len(other_matched), max(0, n // 5 - len(extra) // 2)))
        ids += [d for d in extra if d not in ids]
        taken = set(ids)
        pool = [d for d in rows["det_id"].tolist() if d not in taken]
        ids += rng.sample(pool, max(0, min(len(pool), n - len(ids))))
        ids = ids[:n]
        app = create_app(self.settings, store=self.store)
        client = TestClient(app)
        d1 = [f for f, _ in d1_spec(self.build)]
        recs, live_fields, contexts = {}, {}, {"contacts": {}, "lights": {}}
        lm = set(live_matched)
        view = dict(zip(rows["det_id"], rows["view"]))
        for d in ids:
            r = client.get(f"/api/v1/contacts/{d}")
            r.raise_for_status()
            item = r.json()["item"]
            recs[d] = {f: item.get(f) for f in d1}
            contexts["contacts"][d] = item.get("object_context")
            if view.get(d) == "live":  # identification evidence a live contact holds (README reading 16)
                live_fields[d] = {f: item[f] for f in LIVE_SPOT_FIELDS if item.get(f) not in (None, False)}
        # object context of 20 lights of the page (README reading 13), compared by check_bundle.mjs
        for lid in rng.sample(list(self.light_ids), min(20, len(self.light_ids))):
            r = client.get(f"/api/v1/lights/{lid}")
            r.raise_for_status()
            contexts["lights"][str(lid)] = r.json()["item"].get("object_context")
        oc = self.info.get("object_context") or {}
        return {"build": self.build, "d1_fields": d1, "det_ids": ids, "records": recs, "tolerance": {}, "seed": seed,
                "object_context": contexts, "context_fields": {k: (oc.get(k) or {}).get("fields", []) for k in ("contacts", "lights")},
                "context_cell_id": {k: bool((oc.get(k) or {}).get("cell_id")) for k in ("contacts", "lights")},
                "context_tolerance": {f: 0.5 / s for f, s in P.CONTEXT_PAGE_SCALE.items()},
                "status_present": sorted(set(rows["ais_status"])) if len(rows) else [],
                "live_matched": [d for d in ids if d in lm], "live_fields": live_fields,
                "views": {d: str(view[d]) for d in ids}}

    def report(self, out: Path, size: int, sizes: dict, meta: dict, parts: dict) -> dict:
        t = time.time()
        spot = self.spot_records(self.opt.spot_n)
        ccols = (parts.get("contacts") or {}).get("columns") or {}
        vcols = (parts.get("vessels") or {}).get("columns") or {}
        spot["tolerance"] = {f: E.tolerance(ccols[f]) for f in spot["d1_fields"] if f in ccols}
        spot["tolerance"]["length_ais_m"] = E.tolerance(vcols["length_ais_m"]) if "length_ais_m" in vcols else 0.0
        spot["column_types"] = {f: ccols[f]["t"] for f in ccols}
        self.tick("spot_records", t)
        self.timings["total"] = round(time.time() - self.t0, 2)
        rep = {"build": self.build, "out": str(out), "bytes": size, "cap": self.opt.cap, "shell_bytes": self.shell_bytes,
               "parts": sizes, "budgets": meta["budgets"], "dropped": self.dropped, "info": self.info,
               "checks": self.check_info, "timings_s": self.timings, "spot": spot}
        return rep
