"""Leads (contract 3.5) from `data/leads_open.gpkg` (open) or `data/research/leads_research.parquet` (research; the
GeoPackage twin is the fallback). Written by `scripts/33_leads.py`; when the file is missing, /meta reports it missing
and /leads returns an empty list. State, reason and history come from the decision log on top of the file.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..records import iso_series, json_list, to_utc_series

JSON_LISTS = ["factors", "evidence", "lawful_explanations", "change_indicators", "history"]


class LeadsData:
    def __init__(self, df: pd.DataFrame, evidence: list[list[dict]], extra_cols: list[str], about: dict, source_key: str | None):
        self.df = df
        self.evidence = evidence
        self.extra_cols = extra_cols
        self.about = about
        self.source_key = source_key
        self.pos = pd.Series(np.arange(len(df)), index=df["lead_id"].astype(str)) if len(df) else pd.Series(dtype=int)
        self.pos = self.pos[~self.pos.index.duplicated()]
        self.by_det: dict[str, list[str]] = {}
        self.events_by_det: dict[str, list[str]] = {}
        for lid, prim, ptype, ev in zip(df.get("lead_id", []), df.get("primary_id", []), df.get("primary_type", []), evidence):
            for e in ev:
                if e.get("type") == "contact":
                    self.by_det.setdefault(str(e.get("id")), []).append(str(lid))
                elif e.get("type") == "event" and ptype == "contact":
                    self.events_by_det.setdefault(str(prim), []).append(str(e.get("id")))

    @property
    def priority_model_id(self) -> str | None:
        if len(self.df) and "priority_model_id" in self.df:
            return str(self.df["priority_model_id"].iloc[0])
        return self.about.get("priority_model_id")


def load(cat, settings, fields: list[str]) -> LeadsData:
    key = None
    if settings.research:
        for k in ("leads_research", "leads_research_gpkg"):
            if cat.exists(k):
                key = k
                break
        df = (cat.read_parquet(key) if key == "leads_research" else cat.read_gpkg(key)) if key else None
        about_key = "leads_research_gpkg"
    else:
        key = "leads_open" if cat.exists("leads_open") else None
        df = cat.read_gpkg(key) if key else None
        about_key = "leads_open"
    about = {}
    if cat.exists(about_key):
        try:
            a = cat.read_gpkg(about_key, layer="about")
            about = {} if a is None or not len(a) else {k: v for k, v in a.iloc[0].to_dict().items() if isinstance(v, (str, int, float, bool))}
        except Exception:  # noqa: BLE001  (an about layer is optional)
            about = {}
    if df is None or not len(df):
        return LeadsData(pd.DataFrame(columns=fields + ["_t"]), [], [], about, None)
    df = df.reset_index(drop=True)
    if "caveat" in df:
        df["_source_caveat"] = df.pop("caveat")
    df["_t"] = to_utc_series(df["time_utc"])
    df["time_utc"] = iso_series(df["time_utc"])
    if "next_look_utc" in df:
        df["next_look_utc"] = iso_series(df["next_look_utc"])
    df["priority"] = pd.to_numeric(df["priority"], errors="coerce").fillna(0).round().astype(int)
    df["state"] = df["state"].fillna("new").astype(str) if "state" in df else "new"
    evidence = [json_list(v) for v in df["evidence"]] if "evidence" in df else [[] for _ in range(len(df))]
    df["_evidence_source"] = None
    missing = [i for i, ev in enumerate(evidence) if not ev]
    if missing and not settings.research and cat.exists("leads_open_detail"):
        # board D6.3: the evidence table may move to data/leads_open_detail.gpkg; read it only for leads whose
        # leads_4326 row carries no evidence
        det = cat.read_gpkg("leads_open_detail", ["lead_id", "type", "id", "role"])
        if det is not None and len(det):
            by = {str(k): g[["type", "id", "role"]].to_dict("records") for k, g in det.groupby("lead_id", sort=False)}
            ids = df["lead_id"].astype(str).to_numpy()
            for i in missing:
                ev = by.get(ids[i])
                if ev:
                    evidence[i] = [{k: (None if v is None else str(v)) for k, v in e.items()} for e in ev]
                    df.at[i, "_evidence_source"] = "data/leads_open_detail.gpkg lead_evidence"
    extra = [c for c in df.columns if c not in set(fields) and c not in {"_t", "_source_caveat", "evidence"} and c != "geometry"
             and not str(c).startswith("_")]
    return LeadsData(df, evidence, extra, about, key)


def parse_json_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v
