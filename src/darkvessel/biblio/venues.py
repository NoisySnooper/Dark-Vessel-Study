"""Venue recovery and grouping for works whose OpenAlex primary location has no source.

About a quarter of the corpus (mostly proceedings papers with a Crossref DOI) has no
primary_location.source in the snapshot, which hides conference series such as IGARSS
from any venue ranking. Recovery order, first hit wins:

1. primary_location.source               OpenAlex's own venue (method "openalex_primary_source")
2. another entry of locations[] whose source is a journal, conference, book series or
   ebook platform                        (method "other_location_source")
3. primary_location.raw_source_name       the venue string as harvested (method "primary_raw_source_name")
4. a raw_source_name on any location      (method "location_raw_source_name")
5. a rule on the DOI prefix, for example 10.1109/igarss (method "doi_prefix")
6. nothing                               (method "unattributed")

Steps 2 to 4 need columns the main scan did not keep, so fetch_locations() re-reads only the
row groups that hold works without a source (a few GB, not the whole snapshot).

Conference proceedings are issued per year, each as its own OpenAlex source. series_name()
strips years and ordinals so IGARSS 2019, IGARSS 2020 and so on count as one venue.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .snapshot import RangeClient, RemoteParquet

LOC_LEAVES = [
    "primary_location.raw_source_name",
    "locations.list.element.source.id",
    "locations.list.element.source.display_name",
    "locations.list.element.source.type",
    "locations.list.element.raw_source_name",
]

REPOSITORY_WORDS = re.compile(
    r"arxiv|zenodo|figshare|ssrn|researchgate|preprints?\b|techrxiv|biorxiv|medrxiv|osf\b|hal\b|repository|"
    r"digital commons|elib|doaj|semantic scholar|core\b|open access archive|eprints|dspace|institutional",
    re.I,
)
CONFERENCE_WORDS = re.compile(r"conference|symposium|proceedings|workshop|congress|meeting|\bigarss\b|\bicassp\b|\boceans\b", re.I)
GOOD_LOCATION_TYPES = ("journal", "conference", "book series", "ebook platform")


# ---------------------------------------------------------------------------
# targeted fetch of locations[]
# ---------------------------------------------------------------------------
def fetch_locations(cache_dir: Path, targets: list[dict], workers: int = 12, log=print) -> Path:
    """Fetch primary raw source name and all locations for the given stored rows.

    targets: dicts with id, file ("updated_date=YYYY-MM-DD/part_NNNN"), row_group, row.
    Writes data/cache/openalex/venue_recovery.parquet and returns its path.
    """
    cache_dir = Path(cache_dir)
    by_file: dict[str, dict[int, list[tuple[int, str]]]] = defaultdict(lambda: defaultdict(list))
    for t in targets:
        by_file[t["file"]][t["row_group"]].append((t["row"], t["id"]))

    client = RangeClient(pool_size=48)
    results: list[dict] = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(32) as fetch_pool, ThreadPoolExecutor(workers) as outer:

        def one_file(tag: str) -> list[dict]:
            out: list[dict] = []
            url = f"s3://openalex/data/parquet/works/{tag}.parquet"
            with RemoteParquet(client, url, LOC_LEAVES) as rp:
                for rg, items in by_file[tag].items():
                    for fut in rp.submit_rg(fetch_pool, rg):
                        fut.result()
                    tb = rp.read_rg(rg, LOC_LEAVES)
                    rows = [r for r, _ in items]
                    sel = tb.take(pa.array(rows, type=pa.int64())).to_pylist()
                    for (row, rid), rec in zip(items, sel):
                        prim = rec.get("primary_location") or {}
                        locs = []
                        for loc in rec.get("locations") or []:
                            src = loc.get("source") or {}
                            locs.append(
                                {
                                    "source_id": (src.get("id") or "").rsplit("/", 1)[-1] or None,
                                    "source_name": src.get("display_name"),
                                    "source_type": src.get("type"),
                                    "raw_source_name": loc.get("raw_source_name"),
                                }
                            )
                        out.append({"id": rid, "primary_raw_source_name": prim.get("raw_source_name"), "locations": locs})
                    rp.release_rg(rg)
            return out

        futures = [outer.submit(one_file, tag) for tag in by_file]
        for fut in futures:
            results.extend(fut.result())
            done += 1
            if done % 50 == 0 or done == len(futures):
                log(f"  venue recovery: {done}/{len(futures)} files, {client.bytes_read / 1e9:.2f} GB, {time.time() - t0:.0f}s")
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("primary_raw_source_name", pa.string()),
            (
                "locations",
                pa.list_(
                    pa.struct(
                        [
                            ("source_id", pa.string()),
                            ("source_name", pa.string()),
                            ("source_type", pa.string()),
                            ("raw_source_name", pa.string()),
                        ]
                    )
                ),
            ),
        ]
    )
    path = cache_dir / "venue_recovery.parquet"
    pq.write_table(pa.Table.from_pylist(results, schema=schema), path)
    log(f"  venue recovery: {len(results)} works, {client.bytes_read / 1e9:.2f} GB read in {time.time() - t0:.0f}s")
    return path


def load_recovery(cache_dir: Path) -> dict[str, dict]:
    path = Path(cache_dir) / "venue_recovery.parquet"
    if not path.exists():
        return {}
    return {r["id"]: r for r in pq.read_table(path).to_pylist()}


# ---------------------------------------------------------------------------
# DOI prefix rules: (regex on lower-case DOI, venue name, type)
# ---------------------------------------------------------------------------
DOI_PREFIX_RULES: list[tuple[str, str, str]] = [
    (r"^10\.1109/igarss", "IEEE International Geoscience and Remote Sensing Symposium (IGARSS)", "conference"),
    (r"^10\.1109/ingarss", "IEEE India Geoscience and Remote Sensing Symposium (InGARSS)", "conference"),
    (r"^10\.1109/icassp", "IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)", "conference"),
    (r"^10\.(?:1109|23919)/oceans", "OCEANS (IEEE/MTS)", "conference"),
    (r"^10\.1109/radarconf", "IEEE Radar Conference (RadarConf)", "conference"),
    (r"^10\.1109/radar\d", "International Conference on Radar (RADAR)", "conference"),
    (r"^10\.23919/irs", "International Radar Symposium (IRS)", "conference"),
    (r"^10\.1109/apsar", "Asia-Pacific Conference on Synthetic Aperture Radar (APSAR)", "conference"),
    (r"^10\.1109/bigsardata", "SAR in Big Data Era (BIGSARDATA)", "conference"),
    (r"^10\.1109/piers", "Progress in Electromagnetics Research Symposium (PIERS)", "conference"),
    (r"^10\.1109/icip", "IEEE International Conference on Image Processing (ICIP)", "conference"),
    (r"^10\.1109/cvpr", "IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)", "conference"),
    (r"^10\.1109/iccv", "IEEE/CVF International Conference on Computer Vision (ICCV)", "conference"),
    (r"^10\.1109/wacv", "IEEE/CVF Winter Conference on Applications of Computer Vision (WACV)", "conference"),
    (r"^10\.23919/fusion", "International Conference on Information Fusion (FUSION)", "conference"),
    (r"^10\.1109/aero", "IEEE Aerospace Conference", "conference"),
    (r"^10\.1117/12\.", "Proceedings of SPIE", "conference"),
    (r"^10\.5194/isprs-archives", "The International Archives of the Photogrammetry, Remote Sensing and Spatial Information Sciences", "journal"),
    (r"^10\.5194/isprs-annals", "ISPRS Annals of the Photogrammetry, Remote Sensing and Spatial Information Sciences", "journal"),
    (r"^10\.1088/1755-1315", "IOP Conference Series: Earth and Environmental Science", "conference"),
    (r"^10\.1088/1742-6596", "Journal of Physics: Conference Series", "conference"),
    (r"^10\.1088/1757-899x", "IOP Conference Series: Materials Science and Engineering", "conference"),
    (r"^10\.1049/cp", "IET Conference Proceedings", "conference"),
    (r"^10\.1145/", "ACM proceedings and journals (DOI prefix 10.1145)", "conference"),
    (r"^10\.36227/techrxiv", "TechRxiv", "repository"),
    (r"^10\.13140/rg", "ResearchGate", "repository"),
    (r"^10\.48550/arxiv", "arXiv", "repository"),
    (r"^10\.2139/ssrn", "SSRN", "repository"),
    (r"^10\.20944/preprints", "Preprints.org", "repository"),
    (r"^10\.21203/rs", "Research Square", "repository"),
    (r"^10\.5281/zenodo", "Zenodo", "repository"),
    (r"^10\.6084/m9\.figshare", "Figshare", "repository"),
    (r"^10\.52202/", "Advances in Neural Information Processing Systems (NeurIPS)", "conference"),
]
_DOI_RULES = [(re.compile(p), name, vt) for p, name, vt in DOI_PREFIX_RULES]


def venue_from_doi(doi: str | None) -> tuple[str, str] | None:
    if not doi:
        return None
    d = doi.lower()
    for rx, name, vt in _DOI_RULES:
        if rx.search(d):
            return name, vt
    return None


# ---------------------------------------------------------------------------
# resolving
# ---------------------------------------------------------------------------
def _classify_raw(name: str) -> str:
    if REPOSITORY_WORDS.search(name):
        return "repository"
    if CONFERENCE_WORDS.search(name):
        return "conference"
    return "journal"


def _clean_raw(name: str | None) -> str:
    """A harvested venue string, or "" when it is a URL or too short to be a venue name."""
    value = (name or "").strip()
    if re.match(r"^(?:https?://|www\.)", value, re.I) or len(value) < 3:
        return ""
    return value


def resolve_venue(row: dict, recovery: dict | None) -> dict:
    """Venue name, type and how it was found for one stored row."""
    if row.get("source_name"):
        return {"venue": row["source_name"], "venue_type": row.get("source_type") or "", "method": "openalex_primary_source"}
    rec = (recovery or {}).get(row["id"]) or {}
    for loc in rec.get("locations") or []:
        if loc.get("source_name") and (loc.get("source_type") in GOOD_LOCATION_TYPES):
            return {"venue": loc["source_name"], "venue_type": loc["source_type"], "method": "other_location_source"}
    raw = _clean_raw(rec.get("primary_raw_source_name"))
    if raw:
        return {"venue": raw, "venue_type": _classify_raw(raw), "method": "primary_raw_source_name"}
    for loc in rec.get("locations") or []:
        name = _clean_raw(loc.get("raw_source_name"))
        if name and not REPOSITORY_WORDS.search(name):
            return {"venue": name, "venue_type": _classify_raw(name), "method": "location_raw_source_name"}
    hit = venue_from_doi(row.get("doi"))
    if hit:
        return {"venue": hit[0], "venue_type": hit[1], "method": "doi_prefix"}
    for loc in rec.get("locations") or []:  # last resort: a repository location
        if loc.get("source_name"):
            return {"venue": loc["source_name"], "venue_type": loc.get("source_type") or "repository", "method": "other_location_source"}
    return {"venue": "", "venue_type": "", "method": "unattributed"}


# ---------------------------------------------------------------------------
# grouping conference years into series
# ---------------------------------------------------------------------------
_CURATED_SERIES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"india\s+geoscience|\bingarss\b", re.I), "IEEE India Geoscience and Remote Sensing Symposium (InGARSS)"),
    (re.compile(r"geoscience\s+and\s+remote\s+sensing\s+symposium|\bigarss\b", re.I), "IEEE International Geoscience and Remote Sensing Symposium (IGARSS)"),
    (re.compile(r"acoustics,?\s+speech\s+and\s+signal\s+processing|\bicassp\b", re.I), "IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)"),
    (re.compile(r"\boceans\b", re.I), "OCEANS (IEEE/MTS)"),
    (re.compile(r"radar\s+conference|radarconf", re.I), "IEEE Radar Conference (RadarConf)"),
    (re.compile(r"international\s+radar\s+symposium|\birs\b", re.I), "International Radar Symposium (IRS)"),
    (re.compile(r"conference\s+on\s+radar|\(radar\)", re.I), "International Conference on Radar (RADAR)"),
    (re.compile(r"synthetic\s+aperture\s+radar.*(?:asia|apsar)|\bapsar\b", re.I), "Asia-Pacific Conference on Synthetic Aperture Radar (APSAR)"),
    (re.compile(r"china\s+international\s+sar|\bciss\b", re.I), "China International SAR Symposium (CISS)"),
    (re.compile(r"bigsardata|big\s+data\s+era", re.I), "SAR in Big Data Era (BIGSARDATA)"),
    (re.compile(r"^proceedings\s+of\s+spie|\bspie\b", re.I), "Proceedings of SPIE"),
    (re.compile(r"iop\s+conference\s+series.*earth\s+and\s+environmental", re.I), "IOP Conference Series: Earth and Environmental Science"),
    (re.compile(r"iop\s+conference\s+series.*materials", re.I), "IOP Conference Series: Materials Science and Engineering"),
    (re.compile(r"journal\s+of\s+physics:?\s+conference\s+series", re.I), "Journal of Physics: Conference Series"),
    (re.compile(r"eusar|european\s+conference\s+on\s+synthetic\s+aperture", re.I), "European Conference on Synthetic Aperture Radar (EUSAR)"),
    (re.compile(r"piers|progress\s+in\s+electromagnetics\s+research\s+symposium", re.I), "Progress in Electromagnetics Research Symposium (PIERS)"),
    (re.compile(r"conference\s+on\s+information\s+fusion|\bfusion\b\)?$", re.I), "International Conference on Information Fusion (FUSION)"),
    (re.compile(r"neural\s+information\s+processing", re.I), "Advances in Neural Information Processing Systems (NeurIPS)"),
]
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_ORDINAL = re.compile(r"\b\d+(?:st|nd|rd|th)\b|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b", re.I)
_LEAD_ACRONYM = re.compile(r"^[A-Za-z0-9/\-]{2,20}\s*(?:19|20)\d{2}\s*-\s*")
_PROCEEDINGS = re.compile(r"^(?:the\s+)?proceedings\s+of\s+(?:the\s+)?", re.I)


def series_name(venue: str, venue_type: str) -> str:
    """Venue name with years and ordinals removed for conferences, unchanged for journals."""
    if not venue:
        return ""
    if venue_type not in ("conference", "") and not _CONFERENCE_NAME.search(venue):
        return venue
    for rx, label in _CURATED_SERIES:
        if rx.search(venue):
            return label
    name = _LEAD_ACRONYM.sub("", venue.strip())
    name = _PROCEEDINGS.sub("", name)
    name = _ORDINAL.sub(" ", _YEAR.sub(" ", name))
    name = re.sub(r"\s+", " ", name).strip(" -,:;.")
    return name or venue


_CONFERENCE_NAME = re.compile(r"\bconference\s+(?:proceedings|series)\b|^proceedings\b", re.I)


def venue_group(venue_type: str, venue: str = "") -> str:
    """journal, conference, repository, other or unattributed, for ranking separately.

    OpenAlex types some proceedings series as journals ("IET conference proceedings.",
    "Journal of Physics Conference Series"); those are moved to conference.
    """
    if venue_type == "conference":
        return "conference"
    if venue_type == "repository":
        return "repository"
    if venue_type in ("journal", "book series", "ebook platform"):
        return "conference" if _CONFERENCE_NAME.search(venue or "") else "journal"
    return "other" if venue_type else "unattributed"
