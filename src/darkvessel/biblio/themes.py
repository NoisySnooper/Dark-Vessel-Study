"""Theme definitions for the dark-vessel bibliometric scan.

Pure Python (no pyarrow, no pandas) so it can be tested offline.

A work is tagged with every theme it matches. Matching runs on
"<title> . <abstract>" where the abstract is rebuilt from the OpenAlex inverted
index. There are two modes:

    loose   used while scanning the snapshot. Close to the project brief, with
            bare "SAR" accepted as a SAR term. Everything stored in the scan
            cache matched at this level, so tightening later needs no rescan.
    strict  used to build the final corpus. Adds guards for the known false
            positive classes (search and rescue, specific absorption rate,
            blood vessels, infrared small targets, artisanal mining).

"Dark" in this project means only that a vessel does not broadcast AIS. It does
not mean illegal.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager

THEMES: tuple[str, ...] = (
    "sar_ship_detection",
    "dark_vessels",
    "sar_ais_fusion",
    "xview3",
    "iuu_remote_sensing",
    "small_vessel",
    "viirs_boats",
)

THEME_LABELS: dict[str, str] = {
    "sar_ship_detection": "SAR ship detection",
    "dark_vessels": "Dark vessels",
    "sar_ais_fusion": "SAR and AIS fusion",
    "xview3": "xView3",
    "iuu_remote_sensing": "IUU and fishing by remote sensing",
    "small_vessel": "Small vessels",
    "viirs_boats": "VIIRS boats",
}

THEME_QUERIES: dict[str, str] = {
    "sar_ship_detection": "SAR term AND (ship|vessel|boat) AND (detect|recogni|classif|segment)",
    "dark_vessels": (
        "(dark vessel/ship/fleet/fishing/boat/target | non-broadcasting | AIS gap/disabling | going dark) "
        "AND maritime term (vessel|ship|boat|fishing|AIS|maritime); medical 'dark vessel' excluded"
    ),
    "sar_ais_fusion": "(AIS | automatic identification system) AND SAR term AND (fusion|fuse|match|correlat|associat|integrat|combin)",
    "xview3": "xView3 | xView-3 | xView 3",
    "iuu_remote_sensing": (
        "(IUU | illegal fishing | unreported and unregulated | fishing effort | fishing vessel/boat/fleet | fishing activity) "
        "AND (satellite | remote sensing | SAR | VIIRS | Sentinel | imagery | night light)"
    ),
    "small_vessel": (
        "(small vessel/boat/ship/fishing | small target + maritime term | artisanal | small-scale fish*) "
        "AND detect* AND (SAR | satellite | remote sensing | imagery | radar)"
    ),
    "viirs_boats": (
        "(VIIRS | day/night band | night-time light | nighttime light | low light imaging) AND (boat|fishing|vessel|ship)"
    ),
}

YEAR_MIN = 2015
YEAR_MAX = 2026
KEEP_TYPES: tuple[str, ...] = ("article", "conference-paper", "review", "preprint")

SEA_COUNTRY_CODES: tuple[str, ...] = ("VN", "TH", "MY", "ID", "PH", "SG", "KH", "LA", "MM", "BN", "TL")

_I = re.IGNORECASE


def _c(pattern: str, flags: int = _I) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# ---------------------------------------------------------------------------
# text preparation
# ---------------------------------------------------------------------------
_DASH_MAP = {ord(ch): "-" for ch in "\u2010\u2011\u2012\u2013\u2212\u2043\u00ad"}
_DASH_MAP[0x2014] = " - "


def normalize_text(text: str) -> str:
    """Map unicode dashes to ASCII hyphens and collapse all whitespace (no-break spaces included)."""
    if not text.isascii():
        text = text.translate(_DASH_MAP)
    return " ".join(text.split())


def reconstruct_abstract(inverted_index) -> str:
    """Rebuild abstract text from an OpenAlex abstract_inverted_index (JSON string or dict)."""
    if inverted_index is None:
        return ""
    if isinstance(inverted_index, (str, bytes)):
        try:
            inverted_index = json.loads(inverted_index)
        except ValueError:
            return ""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    top = -1
    for positions in inverted_index.values():
        if positions:
            top = max(top, max(positions))
    if top < 0 or top > 200_000:
        return ""
    words = [""] * (top + 1)
    for word, positions in inverted_index.items():
        for p in positions:
            if 0 <= p <= top:
                words[p] = word
    return " ".join(w for w in words if w)


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------
_SHIP = _c(r"\b(?:ships?|vessels?|boats?)\b")
_VESSEL = _c(r"\bvessels?\b")
_DETECT = _c(r"\b(?:detect|recogni[sz]|classif|segment)\w*")
_DETECT_ONLY = _c(r"\bdetect\w*")

_NONMARITIME_VESSEL = _c(
    r"\b(?:blood|lymph\w*|vascular|coronary|cerebral|retinal|cardiac|pulmonary|renal|arterial|venous|capillary|"
    r"microvascular|pressure|reaction|reactor|xylem|tumou?r|brain|hepatic|cardiovascular)[\s-]+vessels?\b"
    r"|\bvessels?[\s-]+(?:wall|walls|segmentation|occlusion|extraction|tree|diameter|tortuosity|density|lumen|"
    r"stenosis|bifurcation|branching)\b"
)
_MEDICAL = _c(
    r"\b(?:blood|vascular|mri|magnetic resonance|angiogra\w*|arter\w*|vein|veins|cerebr\w*|retina|retinal|tumou?r|"
    r"endotheli\w*|aneurysm|stroke|patients?|lymph\w*|capillar\w*|thromb\w*|clinical|histolog\w*|ultrasound|"
    r"tomograph\w*)\b"
)
_SHIP_UNAMBIGUOUS = _c(r"\b(?:ships?|boats?)\b")
_CRAFT_UNAMBIGUOUS = _c(r"\b(?:ships?|boats?|fishing)\b")
# strict bare SAR must sit in a radar, satellite or maritime text, not an MRI or biomedical one
_RS_CONTEXT = _c(
    r"\b(?:radars?|satellites?|spaceborne|airborne|backscatter\w*|polarimetr\w*|interferometr\w*|microwave|"
    r"sentinel|imagery|remote[\s-]*sens\w*|oceans?|maritime|marine|seas?|offshore|coastal|ships?|boats?|fishing)\b"
)


def _is_medical(text: str) -> bool:
    """Two or more distinct biomedical terms (blood, patients, MRI, ...)."""
    return len({m.lower() for m in _MEDICAL.findall(text)}) >= 2


# strict VIIRS boats: bare "fishing" also names fishing cats and fishing poles, so it must be a fishing phrase
_FISHING_PHRASE = _c(
    r"\bfishing[\s-]+(?:vessels?|boats?|fleets?|lights?|lamps?|grounds?|zones?|effort|activit(?:y|ies)|operations?|"
    r"areas?|pressure|traffic|ships?)\b|\bfishing\s+(?:with|using)\s+lights?\b"
)


def _has_craft(text: str, strict: bool, fishing_counts: bool = False) -> bool:
    """Ship, boat or vessel mentioned. Strict mode ignores blood and other non-maritime vessels.

    With fishing_counts (VIIRS boats) the loose test also accepts bare "fishing"; the strict test
    accepts only fishing phrases such as "fishing vessels" or "fishing lights".
    """
    if not strict:
        return bool(_VIIRS_CRAFT.search(text)) if fishing_counts else bool(_SHIP.search(text))
    t = _strip_nonmaritime_vessels(text)
    if _SHIP_UNAMBIGUOUS.search(t):
        return True
    if fishing_counts and _FISHING_PHRASE.search(text):
        return True
    return bool(_VESSEL.search(t)) and not _is_medical(text)

# SAR terms. Case-sensitive "SAR" is scoped with (?-i:...).
_SAR_LONG = _c(
    r"synthetic[\s-]+aperture[\s-]+radars?"
    r"|\bsentinel[\s-]*1[a-d]?\b"
    r"|\bradarsat\w*"
    r"|\bterrasar\w*"
    r"|\bcosmo[\s-]*sky[\s-]*med\b"
    r"|\bgaofen[\s-]*3\b"
    r"|\bgf[\s-]*3\b"
    r"|\biceye\b"
    r"|\bcapella\b"
)
_SAR_CTX_STRONG = _c(
    r"(?-i:\bSAR)[\s-]+(?:images?|imagery|imaging|data|datasets?|scenes?|sensors?|satellites?|chips?|patch(?:es)?|"
    r"backscatter|amplitude|intensity|echoes?|acquisitions?|signatures?|products?)\b"
)
_SAR_BARE = re.compile(r"\bSAR\b")
_HK_SAR = _c(r"\b(?:hong[\s-]+kong|macao|macau)[\s,(-]*(?:special[\s-]+administrative[\s-]+region)?[\s,(-]*SAR\)?")
_SAR_GUARD = _c(
    r"search[\s-]*(?:and|&|/)[\s-]*rescue|\brescue\b|specific[\s-]+absorption[\s-]+rate"
    r"|structure[\s-]*(?:activity|affinity)|special[\s-]+administrative[\s-]+region"
)


def has_sar_term(text: str, strict: bool = True) -> bool:
    """True if text names a SAR system or uses SAR in a radar sense.

    Long names and "SAR image/imagery/data/..." always count. A bare "SAR" counts
    in loose mode. In strict mode a bare SAR is rejected when the text also talks
    about search and rescue, specific absorption rate, structure-activity
    relationships or a Special Administrative Region, and it needs a radar,
    satellite or maritime word somewhere in the text (so MRI and biomedical
    uses of the abbreviation fall out).
    """
    if _SAR_LONG.search(text) or _SAR_CTX_STRONG.search(text):
        return True
    if not _SAR_BARE.search(text):
        return False
    if not strict:
        return True
    cleaned = _HK_SAR.sub(" ", text)
    return bool(_SAR_BARE.search(cleaned)) and not _SAR_GUARD.search(cleaned) and bool(_RS_CONTEXT.search(cleaned))


_AIS_ABBR = re.compile(r"\bAIS\b")
_AIS_LONG = _c(r"automatic[\s-]+identification[\s-]+systems?")
_FUSE = _c(r"\b(?:fusion|fus(?:e|ed|es|ing)|match|correlat|associat|integrat|combin)\w*")
_MARITIME_ANY = _c(r"\b(?:ships?|vessels?|boats?|maritime|marine|sea|seas|ocean|oceans|fishing|ports?|shipping|coastal)\b")

_DARK_PHRASE_LOOSE = (
    r"\bdark[\s-]+(?:vessels?|ships?|fleets?|fishing|boats?|targets?|shipping|trawlers?|activit(?:y|ies)|maritime)\b"
)
# strict: no "dark target" (MODIS Dark Target aerosol retrieval, dark infrared targets) and no "dark activity"
_DARK_PHRASE_STRICT = r"\bdark[\s-]+(?:vessels?|ships?|fleets?|fishing|boats?|shipping|trawlers?)\b"
_DARK_REST = (
    r"|\bnon[\s-]?broadcast\w*"
    r"|(?-i:\bAIS)[\s-]+(?:gaps?|disabl\w+|dark\w*|silen\w+|outages?|(?:switch|turn|shut)\w*[\s-]+off)\b"
    r"|\bgaps?\s+in\s+(?:the\s+)?(?:(?-i:AIS)|automatic[\s-]+identification[\s-]+systems?)\b"
    r"|\b(?:go|goes|going|gone|went)\s+dark\b"
    r"|\b(?:disabl\w+|(?:switch|turn|shut)\w*\s+(?:off|down))\s+(?:\w+\s+){0,3}?"
    r"(?:(?-i:AIS)|automatic[\s-]+identification[\s-]+systems?)\b"
    r"|(?-i:\bAIS)\s+(?:\w+\s+){0,2}?(?:disabled|(?:switched|turned|shut)\s+off)\b"
)
_DARK = _c(_DARK_PHRASE_LOOSE + _DARK_REST)
_DARK_STRICT = _c(_DARK_PHRASE_STRICT + _DARK_REST)
_MARITIME_STRONG = _c(
    r"\b(?:ships?|boats?|fishing|fisher(?:y|ies|men|man)|maritime|shipping|automatic[\s-]+identification[\s-]+systems?)\b"
    r"|(?-i:\bAIS\b)"
)
# strict dark vessels: context outside the dark phrase itself. Plain "vessel" does not count
# (liquid vessels, blood vessels) and neither does the abbreviation AIS in a medical text
# (acute ischaemic stroke).
_MARITIME_CTX = _c(
    r"\b(?:ships?|boats?|fishing|fisher(?:y|ies|men|man)|maritime|marine|oceans?|seas?|offshore|coastal|shipping|"
    r"trawl\w*|fleets?|ports?|satellites?|radar|SAR|sentinel|tankers?|cargo|sanctions?|"
    r"automatic[\s-]+identification[\s-]+systems?)\b"
)
_AIS_ABBR_ANY = re.compile(r"\bAIS\b")

# strict SAR and AIS fusion: the abbreviation AIS also means Antarctic Ice Sheet, Amery Ice Shelf and
# acute ischaemic stroke, so it needs a ship-like word in the text
_SHIP_CTX = _c(r"\b(?:ships?|vessels?|boats?|maritime|shipping|fishing|ports?|fleets?|trawlers?|tankers?)\b")

_XVIEW3 = _c(r"\bxview[\s-]*3")

_IUU = _c(
    r"(?-i:\bIUU\b)"
    r"|\billegal(?:ly)?[\s,]+(?:and[\s,]+)?(?:unreported[\s,]+(?:and[\s,]+)?(?:unregulated[\s,]+)?)?fishing\b"
    r"|\bunreported[\s,]+(?:and[\s,]+)?unregulated\b"
    r"|\bfishing[\s-]+(?:effort|activit(?:y|ies)|vessels?|boats?|fleets?)\b"
)
# strict IUU: "fishing effort" and "fishing activity" alone also describe fishing-ground and stock studies,
# so they need a vessel, fleet, AIS or VMS word somewhere in the text
_IUU_STRONG = _c(
    r"(?-i:\bIUU\b)"
    r"|\billegal(?:ly)?[\s,]+(?:and[\s,]+)?(?:unreported[\s,]+(?:and[\s,]+)?(?:unregulated[\s,]+)?)?fishing\b"
    r"|\bunreported[\s,]+(?:and[\s,]+)?unregulated\b"
    r"|\bfishing[\s-]+(?:vessels?|boats?|fleets?)\b"
)
_VESSEL_CTX = _c(r"\b(?:vessels?|ships?|boats?|fleets?|AIS|VMS|trawlers?|surveillance|patrol)\b|automatic[\s-]+identification")
_RS_IUU = _c(r"\bsatellites?\b|remote[\s-]*sens\w*|\bVIIRS\b|\bsentinel\b|\bimagery\b|night[\s-]*(?:time[\s-]*)?lights?\b")
# strict: satellite telemetry of animals is not remote sensing of fishing
_ANIMAL_TELEMETRY = _c(
    r"\bpop-?up\s+satellite\b|\bsatellite[\s-]+(?:tags?|tagged|tagging|telemetry|transmitters?|collars?|linked)\b"
    r"|\bsatellite[\s-]+tracking\s+of\s+(?:\w+\s+){1,3}(?:sharks?|turtles?|whales?|seals?|seabirds?|birds?|penguins?|tuna|rays?)\b"
)

_SMALL_CRAFT = _c(
    r"\bsmall(?:[\s-]*(?:sized?|scale))?[\s-]+(?:vessels?|boats?|ships?|fishing)\b"
    r"|\bsmall[\s-]*scale[\s-]+fish\w*"
)
_SMALL_TARGET = _c(r"\bsmall(?:[\s-]*sized?)?[\s-]+(?:\w+[\s-]+)?targets?\b")
_MARITIME_TERM = _c(r"\b(?:ships?|vessels?|boats?|maritime|fishing)\b")
_ARTISANAL = _c(r"\bartisanal\b")
_ARTISANAL_CTX = _c(r"\b(?:fish\w*|vessels?|boats?|canoes?|fleets?|ships?|marine|coastal|pirogues?)\b")
_RS_SMALL = _c(r"\bsatellites?\b|remote[\s-]*sens\w*|\bimagery\b|\bradars?\b")

_VIIRS = _c(
    r"\bVIIRS\b|visible[\s-]+infrared[\s-]+imaging[\s-]+radiometer|day[\s/-]*night[\s-]+band"
    r"|night[\s-]*(?:time[\s-]*)?lights?\b|low[\s-]+light[\s-]+imaging"
)
_VIIRS_CRAFT = _c(r"\b(?:boats?|fishing|vessels?|ships?)\b")


def _strip_nonmaritime_vessels(text: str) -> str:
    return _NONMARITIME_VESSEL.sub(" ", text)


# ---------------------------------------------------------------------------
# theme tests
# ---------------------------------------------------------------------------
# Each test first checks cheap substring gates on the lower-cased text. A gate only
# says "the regex below could possibly match"; it never changes a result. The
# regexes are slow in Python (case-insensitive alternations), the gates are not,
# and most prefiltered candidates fail a gate.
_SAR_WORDS = ("sar", "synthetic", "sentinel", "radarsat", "terrasar", "cosmo", "gaofen", "gf", "iceye", "capella")
_DETECT_WORDS = ("detect", "recogni", "classif", "segment")
_SHIP_WORDS = ("ship", "vessel", "boat")

_GATES_ON = [True]


@contextmanager
def gates_disabled():
    """Run theme tests without the substring gates (used to check that gates never change a result)."""
    _GATES_ON[0] = False
    try:
        yield
    finally:
        _GATES_ON[0] = True


def _gate(condition: bool) -> bool:
    """A gate passes when its condition holds, or always when gates are disabled."""
    return condition or not _GATES_ON[0]


def _any(low: str, words: tuple[str, ...]) -> bool:
    return _gate(any(w in low for w in words))


def _t_sar_ship_detection(text: str, low: str, strict: bool) -> bool:
    if not (_any(low, _SHIP_WORDS) and _any(low, _DETECT_WORDS) and _any(low, _SAR_WORDS)):
        return False
    return _has_craft(text, strict) and bool(_DETECT.search(text)) and has_sar_term(text, strict)


def _t_dark_vessels(text: str, low: str, strict: bool) -> bool:
    if not (_any(low, ("dark", "broadcast")) or _gate("AIS" in text or ("automatic" in low and "identification" in low))):
        return False
    if strict:
        if not _DARK_STRICT.search(text):
            return False
        t = _strip_nonmaritime_vessels(text)
        rest = _DARK_STRICT.sub(" ", t)
        if _MARITIME_CTX.search(rest):
            return True
        return bool(_AIS_ABBR_ANY.search(rest)) and not _MEDICAL.search(text)
    if not _DARK.search(text):
        return False
    if _MARITIME_STRONG.search(text):
        return True
    return bool(_VESSEL.search(text))


def _t_sar_ais_fusion(text: str, low: str, strict: bool) -> bool:
    if not (_gate("AIS" in text or ("automatic" in low and "identification" in low)) and _any(low, _SAR_WORDS)):
        return False
    if not _any(low, ("fus", "match", "correlat", "associat", "integrat", "combin")):
        return False
    if not _FUSE.search(text) or not has_sar_term(text, strict):
        return False
    if _AIS_LONG.search(text):
        return True
    if _AIS_ABBR.search(text):
        return (not strict) or bool(_SHIP_CTX.search(_strip_nonmaritime_vessels(text)))
    return False


def _t_xview3(text: str, low: str, strict: bool) -> bool:
    return _gate("xview" in low) and bool(_XVIEW3.search(text))


def _t_iuu_remote_sensing(text: str, low: str, strict: bool) -> bool:
    if not _any(low, ("iuu", "illegal", "unreported", "fishing")):
        return False
    if not _IUU.search(text):
        return False
    if strict and not _IUU_STRONG.search(text) and not _VESSEL_CTX.search(_strip_nonmaritime_vessels(text)):
        return False
    rs_text = _ANIMAL_TELEMETRY.sub(" ", text) if strict else text
    return bool(_RS_IUU.search(rs_text)) or has_sar_term(text, strict)


def _t_small_vessel(text: str, low: str, strict: bool) -> bool:
    if not (_gate("detect" in low) and _gate("small" in low or "artisanal" in low)):
        return False
    small = bool(_SMALL_CRAFT.search(text))
    if small and strict and _is_medical(text) and not _CRAFT_UNAMBIGUOUS.search(text):
        small = False  # "small vessel disease"
    if not small and _SMALL_TARGET.search(text) and _MARITIME_TERM.search(text):
        small = True
    if not small and _ARTISANAL.search(text):
        small = (not strict) or bool(_ARTISANAL_CTX.search(text))
    if not small or not _DETECT_ONLY.search(text):
        return False
    return bool(_RS_SMALL.search(text)) or has_sar_term(text, strict)


def _t_viirs_boats(text: str, low: str, strict: bool) -> bool:
    if not _any(low, ("viirs", "night", "low light", "low-light", "visible infrared", "visible-infrared")):
        return False
    return bool(_VIIRS.search(text)) and _has_craft(text, strict, fishing_counts=True)


_TESTS = {
    "sar_ship_detection": _t_sar_ship_detection,
    "dark_vessels": _t_dark_vessels,
    "sar_ais_fusion": _t_sar_ais_fusion,
    "xview3": _t_xview3,
    "iuu_remote_sensing": _t_iuu_remote_sensing,
    "small_vessel": _t_small_vessel,
    "viirs_boats": _t_viirs_boats,
}


def match_themes(title: str | None, abstract: str | None, mode: str = "strict") -> list[str]:
    """Return the themes (in THEMES order) matched by a title and abstract.

    mode is "strict" or "loose". The abstract is plain text (use
    reconstruct_abstract on an inverted index first).
    """
    if mode not in ("strict", "loose"):
        raise ValueError("mode must be 'strict' or 'loose'")
    strict = mode == "strict"
    text = normalize_text(f"{title or ''} . {abstract or ''}")
    if not text.strip(" ."):
        return []
    low = text.lower()
    return [name for name in THEMES if _TESTS[name](text, low, strict)]


# ---------------------------------------------------------------------------
# Arrow (RE2) prefilter used by the scanner on the raw inverted-index JSON
# ---------------------------------------------------------------------------
# A row can only match a loose theme if its title or abstract contains at least
# one of these anchors. Every theme needs one of them: ship/vessel/boat (SAR ship
# detection, dark vessels, small vessels, VIIRS boats), fishing/fisher* (IUU,
# VIIRS boats, small-scale fisheries), AIS (SAR and AIS fusion, AIS gaps), maritime
# (dark vessels), IUU or unreported (IUU), artisanal, xview3. The pattern only uses
# syntax shared by RE2 and Python's re.
ARROW_PREFILTER = (
    r"(?i)(?:\b(?:ships?|vessels?|boats?|fishing|fisher(?:y|ies|men|man|s)|maritime|artisanal|ais|iuu)\b"
    r"|unreported|xview|automatic[\s-]*identification)"
)


# ---------------------------------------------------------------------------
# Southeast Asia and Vietnam
# ---------------------------------------------------------------------------
_SEA_PLACE_PATTERNS: dict[str, str] = {
    "Vietnam": r"\bviet[\s-]?nam\w*",
    "Tonkin": r"\btonkin\b",
    "Mekong": r"\bmekong\b",
    "Ca Mau": r"\bca[\s-]+mau\b|\bc[a\u00e0][\s-]+m[a\u00e2]u\b",
    "Gulf of Thailand": r"\bgulf\s+of\s+thailand\b",
    "South China Sea": r"\bsouth\s+china\s+sea\b",
    "Spratly": r"\bspratlys?\b",
    "Paracel": r"\bparacels?\b",
    "Malacca": r"\bmalacca\b",
    "Sulu": r"\bsulu\b",
    "Celebes": r"\bcelebes\b",
    "Java Sea": r"\bjava\s+sea\b",
    "Andaman": r"\bandaman\b",
    "Indonesia": r"\bindonesia\w*",
    "Philippines": r"\bphilippin\w*|\bfilipino\b",
    "Malaysia": r"\bmalaysia\w*",
    "Thailand": r"\bthailand\b|\bthai\b",
    "Cambodia": r"\bcambodia\w*",
    "Myanmar": r"\bmyanmar\b",
    "Brunei": r"\bbrunei\b",
    "Singapore": r"\bsingapore\b",
    "Timor-Leste": r"\btimor[\s-]*leste\b|\beast\s+timor\b",
    # "East Sea" alone also names the Sea of Japan, so only the Vietnamese uses count.
    "East Sea (Vietnam)": r"\b(?:vietnam\w*|viet\s+nam)[\s'\u2019s]*east\s+sea\b|\beast\s+sea\s+of\s+vietnam\b|\bbi[e\u1ec3]n\s+[d\u0111][o\u00f4]ng\b",
}
_SEA_PLACES = {name: _c(p) for name, p in _SEA_PLACE_PATTERNS.items()}
_VN_PLACE_NAMES = ("Vietnam", "Tonkin", "Ca Mau", "East Sea (Vietnam)")
_MEKONG_DELTA = _c(r"\bmekong\s+delta\b")


def sea_places(title: str | None, abstract: str | None) -> list[str]:
    """Southeast Asian place names mentioned in the title or abstract."""
    text = normalize_text(f"{title or ''} . {abstract or ''}")
    return [name for name, rx in _SEA_PLACES.items() if rx.search(text)]


def vn_places(title: str | None, abstract: str | None) -> list[str]:
    """Vietnam-specific place names (Vietnam, Tonkin, Ca Mau, Mekong Delta, East Sea of Vietnam)."""
    text = normalize_text(f"{title or ''} . {abstract or ''}")
    hits = [name for name in _VN_PLACE_NAMES if _SEA_PLACES[name].search(text)]
    if _MEKONG_DELTA.search(text):
        hits.append("Mekong Delta")
    return hits


def sea_flags(title, abstract, country_codes) -> dict:
    """Return the SEA and Vietnam flags and what triggered them."""
    codes = {c for c in (country_codes or []) if c}
    places = sea_places(title, abstract)
    vn = vn_places(title, abstract)
    sea_affil = sorted(codes & set(SEA_COUNTRY_CODES))
    return {
        "sea_text": places,
        "sea_affil": sea_affil,
        "vn_text": vn,
        "vn_affil": "VN" in codes,
        "sea_flag": bool(places or sea_affil),
        "vn_flag": bool(vn or "VN" in codes),
    }
