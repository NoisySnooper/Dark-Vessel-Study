"""Build the dark-vessel corpus and its summary tables from the scan cache.

Input:  data/cache/openalex/filtered/*.parquet (loose matches written by scan.py),
        the snapshot's deleted-ID list and its countries table.
Output: CSV tables under data/biblio/ (see build_all).

Counting rules
    papers per year   one paper counts once in every theme it matches, and once in "all_papers".
    venues            one paper counts once, for the OpenAlex primary-location source.
    countries         full counting: a paper counts once for each distinct country among its
                      authors' institutions (so a paper with authors in two countries counts twice).
    institutions      full counting by OpenAlex institution ID, same rule.
    For a merged group (preprint + published version) the affiliations of all merged records are
    combined, because they describe the same paper.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import dedupe as D
from . import themes as T
from . import venues as V
from .snapshot import WORKS_DELETED_URL, RangeClient, https_url

COUNTRIES_MANIFEST = "https://openalex.s3.amazonaws.com/data/parquet/countries/manifest.json"
PRECISION_SEED = 20260923
PRECISION_N = 50

CORPUS_COLUMNS = [
    "openalex_id",
    "doi",
    "title",
    "year",
    "type",
    "venue",
    "venue_type",
    "issn",
    "cited_by_count",
    "countries",
    "institutions",
    "themes",
    "sea_flag",
    "vn_flag",
]
EXTRA_COLUMNS = [
    "venue_series",
    "venue_group",
    "venue_method",
    "cited_by_with_merged",
    "n_authors",
    "first_author",
    "authors",
    "language",
    "source_id",
    "is_preprint",
    "is_retracted",
    "is_xpac",
    "sea_places",
    "vn_places",
    "sea_affiliation_countries",
    "merged_from",
    "merge_reasons",
]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_filtered(cache_dir: Path) -> list[dict]:
    """All stored loose matches as a list of dict rows."""
    tables = []
    for path in sorted((Path(cache_dir) / "filtered").glob("*.parquet")):
        if pq.ParquetFile(path).metadata.num_rows:
            tables.append(pq.read_table(path))
    if not tables:
        return []
    return pa.concat_tables(tables).to_pylist()


def ensure_deleted_ids_file(cache_dir: Path) -> Path:
    """Download the snapshot's deleted work IDs (about 170 MB gzip) once."""
    path = Path(cache_dir) / "deleted_ids.csv.gz"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    client = RangeClient(pool_size=2)
    resp = client.http.request("GET", WORKS_DELETED_URL, preload_content=False, retries=False)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        for chunk in resp.stream(4 << 20):
            fh.write(chunk)
    resp.release_conn()
    tmp.replace(path)
    return path


def find_deleted(cache_dir: Path, candidate_ids: set[str]) -> tuple[set[str], int]:
    """IDs from candidate_ids that appear in the deleted list. Returns (hits, rows in list)."""
    import hashlib

    key = hashlib.sha1("\n".join(sorted(candidate_ids)).encode()).hexdigest()
    cache_file = Path(cache_dir) / "deleted_hits.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        if cached.get("key") == key:
            return set(cached["hits"]), cached["total"]
    path = ensure_deleted_ids_file(cache_dir)
    hits: set[str] = set()
    total = 0
    for chunk in pd.read_csv(path, chunksize=2_000_000, dtype=str):
        total += len(chunk)
        short = chunk["work_id"].str.rsplit("/", n=1).str[-1]
        hits.update(short[short.isin(candidate_ids)].tolist())
    cache_file.write_text(json.dumps({"key": key, "hits": sorted(hits), "total": total}))
    return hits, total


def load_country_names(cache_dir: Path) -> dict[str, str]:
    """ISO code to country name, from the OpenAlex countries table in the same snapshot."""
    path = Path(cache_dir) / "countries.csv"
    if not path.exists():
        client = RangeClient(pool_size=2)
        manifest = json.loads(client.get_all(COUNTRIES_MANIFEST))
        frames = []
        for entry in manifest["files"]:
            tbl = pq.read_table(io.BytesIO(client.get_all(https_url(entry["url"]))), columns=["country_code", "display_name"])
            frames.append(tbl.to_pandas())
        pd.concat(frames).drop_duplicates("country_code").to_csv(path, index=False)
    df = pd.read_csv(path, keep_default_na=False)
    return dict(zip(df["country_code"], df["display_name"]))


# ---------------------------------------------------------------------------
# preparation
# ---------------------------------------------------------------------------
def _doc_kind(row: dict) -> str:
    if row.get("type") == "preprint":
        return "preprint"
    raw = (row.get("loc_raw_type") or "").lower()
    if row.get("type") == "conference-paper" or "proceedings" in raw or row.get("source_type") == "conference":
        return "conference paper"
    if row.get("type") == "review":
        return "review"
    return "article"


def prepare_rows(rows: list[dict], recovery: dict | None = None) -> list[dict]:
    """Add strict themes, flags, venue and normalised fields to every stored row.

    recovery is the output of venues.load_recovery (works without an OpenAlex primary source).
    """
    out = []
    for r in rows:
        rec = dict(r)
        rec["themes_strict"] = T.match_themes(r["title"], r["abstract"], "strict")
        flags = T.sea_flags(r["title"], r["abstract"], r["countries"])
        rec.update(flags)
        resolved = V.resolve_venue(r, recovery)
        rec["venue"] = resolved["venue"]
        rec["venue_type"] = resolved["venue_type"]
        rec["venue_method"] = resolved["method"]
        rec["venue_series"] = V.series_name(resolved["venue"], resolved["venue_type"])
        rec["venue_group"] = V.venue_group(resolved["venue_type"], resolved["venue"])
        rec["issn"] = r["source_issn_l"] or (r["source_issn"].split(";")[0] if r["source_issn"] else "")
        rec["kind"] = _doc_kind(r)
        out.append(rec)
    return out


def eligible(rec: dict) -> tuple[bool, str]:
    """Whether a stored row can enter the corpus, and why not."""
    if rec["type"] not in T.KEEP_TYPES:
        return False, f"type:{rec['type']}"
    if rec.get("is_paratext"):
        return False, "paratext"
    year = rec["publication_year"]
    if year is None or not (T.YEAR_MIN <= year <= T.YEAR_MAX):
        return False, "year"
    if not rec["themes_strict"]:
        return False, "no_strict_theme"
    return True, ""


def _dedupe_record(rec: dict) -> dict:
    return {
        "id": rec["id"],
        "doi": rec["doi"],
        "title": rec["title"],
        "year": rec["publication_year"],
        "type": rec["type"],
        "cited_by_count": rec["cited_by_count"] or 0,
        "themes": list(rec["themes_strict"]),
        "venue": rec["venue"],
        "source_type": rec["venue_type"],
        "first_author": rec["authors"][0] if rec["authors"] else "",
    }


def _ordered_union(lists) -> list:
    seen, out = set(), []
    for items in lists:
        for x in items:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def build_corpus_rows(
    rows: list[dict], deleted: set[str], recovery: dict | None = None
) -> tuple[list[dict], list[dict], dict]:
    """Strict filter, then dedupe. Returns (corpus rows, merge log, funnel counts)."""
    prepared = prepare_rows(rows, recovery)
    funnel: dict = {"stored_loose_rows": len(prepared)}
    excluded: Counter = Counter()
    elig: list[dict] = []
    for rec in prepared:
        ok, why = eligible(rec)
        if ok:
            elig.append(rec)
        else:
            excluded[why if not why.startswith("type:") else "type:other"] += 1
            if why.startswith("type:"):
                excluded["type_detail:" + why[5:]] += 1
    funnel["excluded"] = dict(excluded)
    funnel["eligible_rows"] = len(elig)
    kept, log = D.dedupe([_dedupe_record(r) for r in elig], deleted_ids=deleted)
    funnel["deleted_ids_found"] = sum(1 for e in log if e["reason"] == "deleted")
    funnel["merges"] = dict(Counter(e["reason"] for e in log if e["reason"] != "deleted"))
    funnel["corpus_rows"] = len(kept)

    by_id = {D.normalize_id(r["id"]): r for r in elig}
    corpus: list[dict] = []
    for k in kept:
        rid = D.normalize_id(k["id"])
        base = by_id[rid]
        group = [base] + [by_id[m] for m in k["merged_from"] if m in by_id]
        countries = _ordered_union(g["countries"] for g in group)
        inst_ids, inst_names = [], []
        seen_inst: set[str] = set()
        for g in group:
            for iid, name in zip(g["inst_ids"], g["inst_names"]):
                if iid and iid not in seen_inst:
                    seen_inst.add(iid)
                    inst_ids.append(iid)
                    inst_names.append(name)
        authors = next((g["authors"] for g in group if g["authors"]), [])
        abstract = next((g["abstract"] for g in group if g["abstract"]), "")
        sea_places = _ordered_union(g["sea_text"] for g in group)
        vn_places = _ordered_union(g["vn_text"] for g in group)
        sea_aff = sorted(set(countries) & set(T.SEA_COUNTRY_CODES))
        corpus.append(
            {
                "openalex_id": rid,
                "doi": base["doi"] or "",
                "title": base["title"] or "",
                "year": base["publication_year"],
                "type": base["type"],
                "kind": base["kind"],
                "venue": base["venue"],
                "venue_type": base["venue_type"],
                "venue_method": base["venue_method"],
                "venue_series": base["venue_series"],
                "venue_group": base["venue_group"],
                "issn": base["issn"],
                "cited_by_count": base["cited_by_count"] or 0,
                "cited_by_with_merged": sum((g["cited_by_count"] or 0) for g in group),
                "countries": countries,
                "inst_ids": inst_ids,
                "inst_names": inst_names,
                "themes": k["themes"],
                "sea_flag": bool(sea_places or sea_aff),
                "vn_flag": bool(vn_places or "VN" in countries),
                "n_authors": max(g["n_authors"] or 0 for g in group),
                "authors": authors,
                "language": base["language"] or "",
                "source_id": base["source_id"] or "",
                "is_preprint": D.is_preprint(_dedupe_record(base)),
                "is_retracted": bool(base["is_retracted"]),
                "is_xpac": bool(base["is_xpac"]),
                "sea_places": sea_places,
                "vn_places": vn_places,
                "sea_affiliation_countries": sea_aff,
                "merged_from": k["merged_from"],
                "merge_reasons": k["merge_reasons"],
                "abstract": abstract,
            }
        )
    corpus.sort(key=lambda c: (-c["year"], -c["cited_by_count"], c["openalex_id"]))
    return corpus, log, funnel


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def corpus_frame(corpus: list[dict]) -> pd.DataFrame:
    rows = []
    for c in corpus:
        rows.append(
            {
                "openalex_id": c["openalex_id"],
                "doi": c["doi"],
                "title": c["title"],
                "year": c["year"],
                "type": c["type"],
                "venue": c["venue"],
                "venue_type": c["venue_type"],
                "issn": c["issn"],
                "cited_by_count": c["cited_by_count"],
                "countries": ";".join(c["countries"]),
                "institutions": " | ".join(c["inst_names"]),
                "themes": ";".join(c["themes"]),
                "sea_flag": c["sea_flag"],
                "vn_flag": c["vn_flag"],
                "cited_by_with_merged": c["cited_by_with_merged"],
                "venue_series": c["venue_series"],
                "venue_group": c["venue_group"],
                "venue_method": c["venue_method"],
                "n_authors": c["n_authors"],
                "first_author": c["authors"][0] if c["authors"] else "",
                "authors": "; ".join(c["authors"]),
                "language": c["language"],
                "source_id": c["source_id"],
                "is_preprint": c["is_preprint"],
                "is_retracted": c["is_retracted"],
                "is_xpac": c["is_xpac"],
                "sea_places": ";".join(c["sea_places"]),
                "vn_places": ";".join(c["vn_places"]),
                "sea_affiliation_countries": ";".join(c["sea_affiliation_countries"]),
                "merged_from": ";".join(c["merged_from"]),
                "merge_reasons": ";".join(c["merge_reasons"]),
            }
        )
    return pd.DataFrame(rows, columns=CORPUS_COLUMNS + EXTRA_COLUMNS)


def papers_per_year(corpus: list[dict]) -> pd.DataFrame:
    years = list(range(T.YEAR_MIN, T.YEAR_MAX + 1))
    table = {y: {t: 0 for t in T.THEMES} | {"all_papers": 0} for y in years}
    for c in corpus:
        row = table[c["year"]]
        row["all_papers"] += 1
        for t in c["themes"]:
            row[t] += 1
    df = pd.DataFrame.from_dict(table, orient="index")
    df.index.name = "year"
    return df.reset_index()[["year", *T.THEMES, "all_papers"]]


def _venue_groups(corpus: list[dict], groups: tuple[str, ...]) -> pd.DataFrame:
    """Count works per venue for the given venue groups. Conferences are grouped by series (years removed)."""
    agg: dict[tuple, dict] = {}
    for c in corpus:
        if c["venue_group"] not in groups:
            continue
        if c["venue_group"] == "conference":
            key = ("conference", c["venue_series"])
            label = c["venue_series"]
        else:
            key = (c["venue_group"], c["source_id"] or c["venue_series"])
            label = c["venue"]
        g = agg.setdefault(
            key,
            {"venue": label, "venue_group": c["venue_group"], "venue_type": c["venue_type"], "source_id": c["source_id"],
             "n_papers": 0, "cited_by_sum": 0, "first_year": c["year"], "last_year": c["year"],
             "n_dark_vessel_papers": 0, "themes": Counter()},
        )
        g["n_papers"] += 1
        g["cited_by_sum"] += c["cited_by_count"]
        g["first_year"] = min(g["first_year"], c["year"])
        g["last_year"] = max(g["last_year"], c["year"])
        g["n_dark_vessel_papers"] += 1 if "dark_vessels" in c["themes"] else 0
        g["themes"].update(c["themes"])
    rows = []
    for g in agg.values():
        top = g.pop("themes").most_common(1)
        g["main_theme"] = top[0][0] if top else ""
        rows.append(g)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["n_papers", "cited_by_sum"], ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["pct_of_corpus"] = (100 * df["n_papers"] / len(corpus)).round(2)
    return df


def top_venues(corpus: list[dict], n: int = 50) -> pd.DataFrame:
    """Journals and conference series only. Repositories and works with no venue are ranked elsewhere."""
    return _venue_groups(corpus, ("journal", "conference")).head(n)


def top_repositories(corpus: list[dict], n: int = 30) -> pd.DataFrame:
    """Repositories and other non-journal, non-conference sources, reported separately from the venue ranking."""
    return _venue_groups(corpus, ("repository", "other")).head(n)


def venue_coverage(corpus: list[dict]) -> dict:
    """How venues were found and how the corpus splits by venue group."""
    return {
        "by_group": dict(Counter(c["venue_group"] for c in corpus)),
        "by_method": dict(Counter(c["venue_method"] for c in corpus)),
        "unattributed": sum(1 for c in corpus if c["venue_group"] == "unattributed"),
    }


def top_countries(corpus: list[dict], names: dict[str, str], n: int = 50) -> pd.DataFrame:
    counts: Counter = Counter()
    with_data = 0
    for c in corpus:
        if c["countries"]:
            with_data += 1
        for cc in set(c["countries"]):
            counts[cc] += 1
    rows = [
        {
            "country_code": cc,
            "country": names.get(cc, cc),
            "n_papers": k,
            "pct_of_corpus": round(100 * k / len(corpus), 2),
            "pct_of_papers_with_country_data": round(100 * k / with_data, 2) if with_data else 0.0,
        }
        for cc, k in counts.most_common(n)
    ]
    return pd.DataFrame(rows)


def top_institutions(corpus: list[dict], country_of: dict[str, str] | None = None, n: int = 50) -> pd.DataFrame:
    counts: Counter = Counter()
    names: dict[str, str] = {}
    for c in corpus:
        for iid, name in zip(c["inst_ids"], c["inst_names"]):
            names.setdefault(iid, name)
        for iid in set(c["inst_ids"]):
            counts[iid] += 1
    rows = [
        {
            "institution_id": iid,
            "institution": names[iid],
            "country_code": (country_of or {}).get(iid, ""),
            "n_papers": k,
            "pct_of_corpus": round(100 * k / len(corpus), 2),
        }
        for iid, k in counts.most_common(n)
    ]
    return pd.DataFrame(rows)


def institution_countries(rows: list[dict]) -> dict[str, str]:
    """Institution ID to country code, from the stored rows."""
    out: dict[str, str] = {}
    for r in rows:
        for iid, cc in zip(r["inst_ids"], r["inst_countries"]):
            if iid and cc:
                out.setdefault(iid, cc)
    return out


def top_cited(corpus: list[dict], n: int = 20) -> pd.DataFrame:
    """Top n by citations summed over the kept record and any preprint or copy merged into it."""
    ordered = sorted(corpus, key=lambda c: (-c["cited_by_with_merged"], c["year"], c["openalex_id"]))[:n]
    return pd.DataFrame(
        [
            {
                "rank": i + 1,
                "openalex_id": c["openalex_id"],
                "doi": c["doi"],
                "title": c["title"],
                "year": c["year"],
                "venue": c["venue"],
                "cited_by_count": c["cited_by_count"],
                "cited_by_with_merged": c["cited_by_with_merged"],
                "themes": ";".join(c["themes"]),
                "first_author": c["authors"][0] if c["authors"] else "",
            }
            for i, c in enumerate(ordered)
        ]
    )


def sea_vietnam(corpus: list[dict], names: dict[str, str]) -> pd.DataFrame:
    rows = []
    for c in corpus:
        if not c["sea_flag"]:
            continue
        rows.append(
            {
                "openalex_id": c["openalex_id"],
                "doi": c["doi"],
                "title": c["title"],
                "year": c["year"],
                "type": c["type"],
                "venue": c["venue"],
                "cited_by_count": c["cited_by_count"],
                "themes": ";".join(c["themes"]),
                "vn_flag": c["vn_flag"],
                "sea_text_places": ";".join(c["sea_places"]),
                "vn_text_places": ";".join(c["vn_places"]),
                "sea_affiliation_countries": ";".join(c["sea_affiliation_countries"]),
                "vn_affiliation": "VN" in c["countries"],
                "countries": ";".join(c["countries"]),
                "institutions": " | ".join(c["inst_names"]),
                "basis": (
                    "text and affiliation" if (c["sea_places"] and c["sea_affiliation_countries"])
                    else "text only" if c["sea_places"] else "affiliation only"
                ),
            }
        )
    df = pd.DataFrame(rows)
    return df.sort_values(["vn_flag", "year", "cited_by_count"], ascending=[False, False, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# precision sample
# ---------------------------------------------------------------------------
def _hash_key(seed: int, tag: str, oid: str) -> str:
    return hashlib.sha256(f"{seed}:{tag}:{oid}".encode()).hexdigest()


def draw_sample(corpus: list[dict], n: int = PRECISION_N, seed: int = PRECISION_SEED) -> list[dict]:
    """Random sample of n corpus records with a fixed seed.

    Records are ranked by a seeded hash of their OpenAlex ID and the first n are taken, so the
    sample does not depend on corpus order and changes only where the corpus itself changes.
    """
    ranked = sorted(corpus, key=lambda c: _hash_key(seed, "main", c["openalex_id"]))[:n]
    return sorted(ranked, key=lambda c: c["openalex_id"])


def sample_frame(sample: list[dict], judgments: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for c in sample:
        j = judgments.get(c["openalex_id"], {})
        rows.append(
            {
                "openalex_id": c["openalex_id"],
                "doi": c["doi"],
                "year": c["year"],
                "themes": ";".join(c["themes"]),
                "title": c["title"],
                "abstract": c["abstract"][:1500],
                "judgment": j.get("judgment", ""),
                "note": j.get("note", ""),
            }
        )
    return pd.DataFrame(rows)


THEME_SAMPLE_SEED = 20260924
THEME_SAMPLE_N = {
    "dark_vessels": 15,
    "sar_ais_fusion": 15,
    "xview3": 12,
    "iuu_remote_sensing": 20,
    "small_vessel": 20,
    "viirs_boats": 15,
}


def draw_theme_samples(
    corpus: list[dict], exclude_ids: set[str], sizes: dict[str, int] | None = None, seed: int = THEME_SAMPLE_SEED
) -> list[tuple[str, dict]]:
    """Supplementary sample per theme (the 50-record random sample has too few records for small themes).

    Returns (theme, record) pairs. A work in two themes can appear twice. Works in
    exclude_ids are skipped. A theme with fewer works than requested is taken whole.
    """
    sizes = sizes or THEME_SAMPLE_N
    out: list[tuple[str, dict]] = []
    for theme in sizes:
        pool = [c for c in corpus if theme in c["themes"] and c["openalex_id"] not in exclude_ids]
        pool.sort(key=lambda c: _hash_key(seed, theme, c["openalex_id"]))
        out.extend((theme, c) for c in sorted(pool[: sizes[theme]], key=lambda c: c["openalex_id"]))
    return out


def theme_sample_frame(pairs: list[tuple[str, dict]], judgments: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for theme, c in pairs:
        j = judgments.get(c["openalex_id"], {})
        rows.append(
            {
                "sample_theme": theme,
                "openalex_id": c["openalex_id"],
                "doi": c["doi"],
                "year": c["year"],
                "themes": ";".join(c["themes"]),
                "title": c["title"],
                "abstract": c["abstract"][:1500],
                "judgment": j.get("judgment", ""),
                "note": j.get("note", ""),
            }
        )
    return pd.DataFrame(rows)


def theme_sample_precision(pairs: list[tuple[str, dict]], judgments: dict[str, dict]) -> dict:
    out: dict = {}
    for theme in dict.fromkeys(t for t, _ in pairs):
        js = [judgments[c["openalex_id"]]["judgment"] for t, c in pairs if t == theme and c["openalex_id"] in judgments]
        out[theme] = {
            "n": len(js),
            "relevant": sum(1 for j in js if j == "relevant"),
            "precision": round(sum(1 for j in js if j == "relevant") / len(js), 3) if js else None,
        }
    return out


def precision_by_theme(sample: list[dict], judgments: dict[str, dict]) -> dict:
    """Overall precision and precision per theme among judged sample records."""
    judged = [(c, judgments[c["openalex_id"]]["judgment"]) for c in sample if c["openalex_id"] in judgments]
    out: dict = {"n_judged": len(judged)}
    if not judged:
        return out
    rel = sum(1 for _, j in judged if j == "relevant")
    out["relevant"] = rel
    out["precision"] = round(rel / len(judged), 3)
    per = {}
    for t in T.THEMES:
        sub = [j for c, j in judged if t in c["themes"]]
        per[t] = {"n": len(sub), "relevant": sum(1 for j in sub if j == "relevant")}
        per[t]["precision"] = round(per[t]["relevant"] / len(sub), 3) if sub else None
    out["per_theme"] = per
    return out


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------
ANCHORS = [
    {
        "key": "paolo_2024_nature",
        "citation": "Paolo et al. 2024, Nature, Satellite mapping reveals extensive industrial activity at sea",
        "doi": "10.1038/s41586-023-06825-8",
        "title_words": ["satellite mapping reveals extensive industrial activity at sea"],
    },
    {
        "key": "park_2020_sciadv",
        "citation": "Park et al. 2020, Science Advances, Illuminating dark fishing fleets in North Korea",
        "doi": "10.1126/sciadv.abb1197",
        "title_words": ["illuminating dark fishing fleets in north korea"],
    },
    {
        "key": "xview3_sar_2022",
        "citation": "Paolo et al. 2022, NeurIPS Datasets and Benchmarks, xView3-SAR (arXiv 2206.00897)",
        "doi": "10.48550/arxiv.2206.00897",
        "title_words": ["xview3-sar: detecting dark fishing activity"],
    },
    {
        "key": "elvidge_2015_remsens",
        "citation": "Elvidge et al. 2015, Remote Sensing 7(3), Automatic Boat Identification System for VIIRS Low Light Imaging Data",
        "doi": "10.3390/rs70303020",
        "title_words": ["automatic boat identification system for viirs"],
    },
]


def find_anchor_rows(anchor: dict, rows: list[dict]) -> list[dict]:
    """Stored rows that match an anchor by DOI or by title."""
    hits = []
    for r in rows:
        doi = D.normalize_doi(r["doi"]) or ""
        title = D.normalize_title(r["title"])
        if doi == anchor["doi"].lower() or any(D.normalize_title(w) in title for w in anchor["title_words"]):
            hits.append(r)
    return hits


def anchors_table(prepared: list[dict], corpus: list[dict], summaries: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per stored record that matches an anchor paper, with its fate in the corpus."""
    summaries = summaries or {}
    in_corpus = {c["openalex_id"]: c for c in corpus}
    merged_into: dict[str, str] = {}
    for c in corpus:
        for m in c["merged_from"]:
            merged_into[m] = c["openalex_id"]
    rows = []
    for a in ANCHORS:
        hits = find_anchor_rows(a, prepared)
        if not hits:
            rows.append({"anchor_key": a["key"], "citation": a["citation"], "found_in_snapshot": False,
                         "expected_doi": a["doi"], "status": "not found in the stored scan rows"})
            continue
        for r in hits:
            rid = r["id"]
            if rid in in_corpus:
                status = "in corpus"
            elif rid in merged_into:
                status = f"merged into {merged_into[rid]} (preprint/published pair)"
            else:
                ok, why = eligible(r)
                status = f"not in corpus: {why}" if not ok else "not in corpus"
            authors = r["authors"]
            rows.append(
                {
                    "anchor_key": a["key"],
                    "citation": a["citation"],
                    "found_in_snapshot": True,
                    "expected_doi": a["doi"],
                    "openalex_id": rid,
                    "doi": r["doi"] or "",
                    "title": r["title"],
                    "year": r["publication_year"],
                    "publication_date": r["publication_date"] or "",
                    "type": r["type"],
                    "venue": r["venue"],
                    "venue_type": r["venue_type"],
                    "cited_by_count": r["cited_by_count"],
                    "authors": "; ".join(authors[:6]) + (" et al." if len(authors) > 6 else ""),
                    "n_authors": r["n_authors"],
                    "themes_loose": ";".join(r["themes_loose"].split(";")) if r["themes_loose"] else "",
                    "themes_strict": ";".join(r["themes_strict"]),
                    "status": status,
                    "summary": summaries.get(rid, summaries.get(a["key"], "")),
                    "abstract": r["abstract"],
                }
            )
    return pd.DataFrame(rows)


def elvidge_viirs_table(corpus: list[dict]) -> pd.DataFrame:
    """Corpus works with an author named Elvidge that match the VIIRS boats theme."""
    rows = []
    for c in corpus:
        if "viirs_boats" in c["themes"] and any("elvidge" in a.lower() for a in c["authors"]):
            rows.append(
                {
                    "openalex_id": c["openalex_id"],
                    "doi": c["doi"],
                    "year": c["year"],
                    "title": c["title"],
                    "venue": c["venue"],
                    "cited_by_count": c["cited_by_count"],
                    "authors": "; ".join(c["authors"][:8]) + (" et al." if len(c["authors"]) > 8 else ""),
                    "themes": ";".join(c["themes"]),
                }
            )
    return pd.DataFrame(rows).sort_values("year") if rows else pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# queries documents
# ---------------------------------------------------------------------------
def queries_json() -> dict:
    """Machine-readable definition of the final (strict) and scan (loose) queries."""
    return {
        "years": [T.YEAR_MIN, T.YEAR_MAX],
        "types_kept": list(T.KEEP_TYPES),
        "text_searched": "title + reconstructed abstract (OpenAlex abstract_inverted_index)",
        "themes": {
            t: {"label": T.THEME_LABELS[t], "query": T.THEME_QUERIES[t]} for t in T.THEMES
        },
        "scan_prefilter_re2": T.ARROW_PREFILTER,
        "modes": {
            "loose": (
                "used during the scan; bare SAR accepted; no medical, search-and-rescue or other context guards; "
                "stores a superset of the corpus"
            ),
            "strict": (
                "used for the corpus. Guards: (1) a bare SAR is rejected next to search and rescue, specific absorption "
                "rate, structure-activity, a Special Administrative Region or biomedical text, and needs a radar, satellite "
                "or maritime word; (2) blood vessels, small vessel disease and research vessels or shipboard "
                "instruments are not ships; (3) dark vessels: no 'dark target', 'dark activity', 'dark fishing spider' "
                "or 'non-broadcast film', and the phrase needs maritime context outside itself; (4) SAR and AIS fusion: "
                "the abbreviation AIS needs a ship-like word (it also means Antarctic Ice Sheet and acute ischaemic stroke); "
                "(5) IUU: 'fishing effort' and 'fishing activity' alone need a vessel, fleet, AIS or VMS word, and satellite "
                "telemetry of animals is not remote sensing; (6) small vessels: small target needs a maritime term, "
                "artisanal needs a fishing or boat context; (7) VIIRS boats: bare 'fishing' must be a fishing phrase, "
                "'low light imaging' is not a trigger by itself, and the text needs night imaging or vessel-position data"
            ),
        },
        "sea_vietnam": {
            "sea_country_codes": list(T.SEA_COUNTRY_CODES),
            "text_places": list(T._SEA_PLACE_PATTERNS.keys()),
            "vn_text_places": [*T._VN_PLACE_NAMES, "Mekong Delta"],
        },
    }


def queries_markdown(q: dict, snapshot: str) -> str:
    lines = [
        "# Queries",
        "",
        f"Snapshot: OpenAlex works, {snapshot}. Years {q['years'][0]} to {q['years'][1]}. "
        f"Types kept: {', '.join(q['types_kept'])}. Text searched: {q['text_searched']}.",
        "",
        "A work gets every theme it matches. Matching is case-insensitive except for the abbreviations SAR, AIS and IUU.",
        "",
        "| Theme | Query |",
        "|---|---|",
    ]
    for t, d in q["themes"].items():
        lines.append(f"| `{t}` | {d['query']} |")
    lines += [
        "",
        "## Loose and strict modes",
        "",
        f"- Loose: {q['modes']['loose']}.",
        f"- Strict: {q['modes']['strict']}.",
        "",
        "The scan stored every loose match. The corpus uses strict matching on the stored text. The exact regular "
        "expressions are in `src/darkvessel/biblio/themes.py`.",
        "",
        "## Scan prefilter (RE2, applied to the raw abstract JSON and the title)",
        "",
        "```",
        q["scan_prefilter_re2"],
        "```",
        "",
        "## Southeast Asia and Vietnam",
        "",
        f"- Affiliation countries: {', '.join(q['sea_vietnam']['sea_country_codes'])}.",
        f"- Place names in title or abstract: {', '.join(q['sea_vietnam']['text_places'])}. "
        "Plain \"East Sea\" is not counted because it also names the Sea of Japan.",
        f"- Vietnam flag: place names {', '.join(q['sea_vietnam']['vn_text_places'])}, or an author affiliation in VN.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def scan_totals(cache_dir: Path) -> dict:
    """Totals from the scan's progress and run logs."""
    cache_dir = Path(cache_dir)
    prog = [json.loads(line) for line in (cache_dir / "progress.jsonl").read_text().splitlines()]
    runs_path = cache_dir / "runs.jsonl"
    runs = [json.loads(line) for line in runs_path.read_text().splitlines()] if runs_path.exists() else []
    return {
        "files": len(prog),
        "rows": sum(p["rows"] for p in prog),
        "rows_in_range": sum(p["rows_in_range"] for p in prog),
        "candidates": sum(p["candidates"] for p in prog),
        "loose_matches": sum(p["matches"] for p in prog),
        "bytes_read": sum(p["bytes"] for p in prog),
        "worker_seconds": round(sum(p["seconds"] for p in prog), 1),
        "runs": len(runs),
        "wall_seconds": round(sum(r["seconds"] for r in runs), 1),
    }


def build_all(repo: Path, judgments: dict[str, dict] | None = None, log=print) -> dict:
    """Build every table. Returns the summary dict (also written to data/biblio/summary.json)."""
    repo = Path(repo)
    cache = repo / "data" / "cache" / "openalex"
    out = repo / "data" / "biblio"
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((cache / "manifest.json").read_text())

    rows = load_filtered(cache)
    log(f"loaded {len(rows)} stored loose matches")
    mismatches = sum(
        1 for r in rows if ";".join(T.match_themes(r["title"], r["abstract"], "loose")) != r["themes_loose"] and not r["watch"]
    )
    log(f"loose matcher reproduces the scan's stored themes: {len(rows) - mismatches}/{len(rows)} (mismatches {mismatches})")
    recovery = V.load_recovery(cache)
    if not recovery:
        log("WARNING: no venue_recovery.parquet; run scripts/biblio_venues.py first")
    deleted, deleted_total = find_deleted(cache, {r["id"] for r in rows})
    log(f"deleted-ID list: {deleted_total} rows, {len(deleted)} hit our matches")
    corpus, merge_log, funnel = build_corpus_rows(rows, deleted, recovery)
    names = load_country_names(cache)
    inst_country = institution_countries(rows)

    corpus_frame(corpus).to_csv(out / "corpus.csv", index=False)
    papers_per_year(corpus).to_csv(out / "papers_per_year.csv", index=False)
    top_venues(corpus).to_csv(out / "top_venues.csv", index=False)
    top_repositories(corpus).to_csv(out / "top_repositories.csv", index=False)
    top_countries(corpus, names).to_csv(out / "top_countries.csv", index=False)
    top_institutions(corpus, inst_country).to_csv(out / "top_institutions.csv", index=False)
    top_cited(corpus).to_csv(out / "top20_cited.csv", index=False)
    sea_vietnam(corpus, names).to_csv(out / "sea_vietnam.csv", index=False)
    pd.DataFrame(merge_log).to_csv(out / "dedupe_log.csv", index=False)

    q = queries_json()
    (out / "queries.json").write_text(json.dumps(q, indent=1))
    (out / "queries.md").write_text(queries_markdown(q, manifest["date"]))

    summaries_path = out / "anchor_summaries.json"
    summaries = json.loads(summaries_path.read_text()) if summaries_path.exists() else {}
    anchors = anchors_table(prepare_rows(rows, recovery), corpus, summaries)
    anchors.to_csv(out / "anchors.csv", index=False)
    elvidge_viirs_table(corpus).to_csv(out / "elvidge_viirs_boats.csv", index=False)

    sample = draw_sample(corpus)
    judgments = judgments or {}
    sample_frame(sample, judgments).to_csv(out / "precision_sample.csv", index=False)
    precision = precision_by_theme(sample, judgments)
    theme_judgments_path = out / "precision_theme_judgments.json"
    theme_judgments = json.loads(theme_judgments_path.read_text()) if theme_judgments_path.exists() else {}
    pairs = draw_theme_samples(corpus, set())
    theme_sample_frame(pairs, {**judgments, **theme_judgments}).to_csv(out / "precision_theme_sample.csv", index=False)
    precision["theme_sample"] = theme_sample_precision(pairs, {**judgments, **theme_judgments})

    theme_counts = Counter(t for c in corpus for t in c["themes"])
    summary = {
        "snapshot_date": manifest["date"],
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scan": scan_totals(cache),
        "funnel": funnel,
        "corpus_size": len(corpus),
        "theme_counts": {t: theme_counts[t] for t in T.THEMES},
        "multi_theme_papers": sum(1 for c in corpus if len(c["themes"]) > 1),
        "sea_papers": sum(1 for c in corpus if c["sea_flag"]),
        "vn_papers": sum(1 for c in corpus if c["vn_flag"]),
        "papers_without_country_data": sum(1 for c in corpus if not c["countries"]),
        "venues": venue_coverage(corpus),
        "loose_matcher_mismatches": mismatches,
        "precision": precision,
        "precision_seed": PRECISION_SEED,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    log(f"corpus {len(corpus)} works; summary written")
    return summary
