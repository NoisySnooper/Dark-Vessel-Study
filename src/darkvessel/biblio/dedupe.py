"""Duplicate removal for the bibliometric corpus.

Pure Python so it can be unit tested with tiny fake records.

Order of operations (see dedupe()):

1. drop OpenAlex IDs that appear in the snapshot's deleted-ID list;
2. merge records that share an OpenAlex ID;
3. merge records that share a DOI (case and URL prefix ignored);
4. merge records that share a normalised title and the same year;
5. merge a preprint with a published record that has the same normalised title
   when the years differ by at most one (the published paper usually appears the
   year after the preprint).

Within each merged group the published version is kept over the preprint. The
kept record gets the union of the themes and a note listing what was merged in.
Titles shorter than min_title_len characters are never merged on title alone,
because generic titles ("Introduction") would collide.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

_TAGS = re.compile(r"<[^>]+>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
_PREPRINT_DOI_PREFIXES = (
    "10.48550/arxiv",  # arXiv
    "10.1101/",  # bioRxiv, medRxiv
    "10.20944/preprints",  # Preprints.org
    "10.31219/osf.io",  # OSF preprints
    "10.36227/techrxiv",  # TechRxiv
    "10.22541/",  # Authorea
    "10.21203/rs.",  # Research Square
    "10.2139/ssrn",  # SSRN
    "10.31223/",  # EarthArXiv
    "10.31234/",  # PsyArXiv
    "10.32920/",  # Figshare-hosted preprints (TechRxiv mirror)
)


def normalize_doi(doi: str | None) -> str | None:
    """Lower-case bare DOI, or None."""
    if not doi:
        return None
    value = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value or None


def normalize_id(oa_id: str | None) -> str | None:
    """https://openalex.org/W123 -> W123."""
    if not oa_id:
        return None
    return oa_id.strip().rsplit("/", 1)[-1].upper() or None


def normalize_title(title: str | None) -> str:
    """Fold case, accents, markup and punctuation so near-identical titles compare equal."""
    if not title:
        return ""
    text = _TAGS.sub(" ", title)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return _NON_ALNUM.sub(" ", text).strip()


def is_preprint(rec: dict) -> bool:
    """True for preprints: OpenAlex type 'preprint' or a DOI issued by a preprint server."""
    if (rec.get("type") or "") == "preprint":
        return True
    doi = normalize_doi(rec.get("doi")) or ""
    if doi.startswith(_PREPRINT_DOI_PREFIXES):
        return True
    return False


class _UnionFind:
    """Minimal union-find. dedupe() sets parents directly so the best-ranked record stays the root."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i


def _rank(rec: dict) -> tuple:
    """Sort key: smaller is preferred as the surviving record."""
    return (
        1 if is_preprint(rec) else 0,
        1 if rec.get("source_type") == "repository" else 0,
        0 if normalize_doi(rec.get("doi")) else 1,
        0 if rec.get("venue") else 1,
        -(rec.get("cited_by_count") or 0),
        rec.get("year") or 9999,
        normalize_id(rec.get("id")) or "",
    )


def dedupe(
    records: list[dict],
    deleted_ids: set[str] | frozenset[str] = frozenset(),
    min_title_len: int = 25,
    preprint_year_gap: int = 1,
) -> tuple[list[dict], list[dict]]:
    """Remove duplicates. Returns (kept records, merge log).

    Each record is a dict with at least: id, doi, title, year, type, cited_by_count,
    themes (list). Optional: venue. The input dicts are not modified. Kept records
    carry merged_from (list of dropped IDs) and merge_reasons (list of reasons).
    The log has one row per dropped record: {"dropped", "kept", "reason"}.
    """
    deleted = {normalize_id(x) for x in deleted_ids}
    log: list[dict] = []
    live: list[dict] = []
    for rec in records:
        rid = normalize_id(rec.get("id"))
        if rid in deleted:
            log.append({"dropped": rid, "kept": None, "reason": "deleted"})
            continue
        live.append(rec)

    n = len(live)
    uf = _UnionFind(n)
    reason: dict[int, str] = {}  # index of a dropped record -> reason it was merged

    def merge(i: int, j: int, why: str) -> None:
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            return
        # the loser is whichever root ranks worse; record why
        best, worst = (ri, rj) if _rank(live[ri]) <= _rank(live[rj]) else (rj, ri)
        uf.parent[worst] = best
        reason[worst] = why

    def group_by(keyfunc) -> dict:
        groups: dict = defaultdict(list)
        for idx, rec in enumerate(live):
            key = keyfunc(rec)
            if key:
                groups[key].append(idx)
        return groups

    for members in group_by(lambda r: normalize_id(r.get("id"))).values():
        for other in members[1:]:
            merge(members[0], other, "same_id")
    for members in group_by(lambda r: normalize_doi(r.get("doi"))).values():
        for other in members[1:]:
            merge(members[0], other, "same_doi")

    def title_year_key(rec: dict):
        t = normalize_title(rec.get("title"))
        if len(t) < min_title_len or not rec.get("year"):
            return None
        return (t, rec["year"])

    for members in group_by(title_year_key).values():
        for other in members[1:]:
            merge(members[0], other, "same_title_year")

    def title_key(rec: dict):
        t = normalize_title(rec.get("title"))
        return t if len(t) >= min_title_len else None

    for members in group_by(title_key).values():
        if len(members) < 2:
            continue
        pre = [i for i in members if is_preprint(live[i])]
        pub = [i for i in members if not is_preprint(live[i])]
        for p in pre:
            for q in pub:
                yp, yq = live[p].get("year"), live[q].get("year")
                if yp and yq and abs(yp - yq) <= preprint_year_gap:
                    merge(p, q, "preprint_published_pair")

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[uf.find(idx)].append(idx)

    kept: list[dict] = []
    for root in sorted(groups):
        members = groups[root]
        survivor = dict(live[root])
        themes: set[str] = set(survivor.get("themes") or [])
        merged_from: list[str] = []
        reasons: list[str] = []
        for idx in members:
            if idx == root:
                continue
            other = live[idx]
            themes.update(other.get("themes") or [])
            oid = normalize_id(other.get("id"))
            merged_from.append(oid or "")
            why = reason.get(idx, "merged")
            reasons.append(why)
            log.append({"dropped": oid, "kept": normalize_id(survivor.get("id")), "reason": why})
        survivor["themes"] = sorted(themes)
        survivor["merged_from"] = merged_from
        survivor["merge_reasons"] = reasons
        kept.append(survivor)
    return kept, log
