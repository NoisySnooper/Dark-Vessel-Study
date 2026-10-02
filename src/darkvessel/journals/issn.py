"""ISSN and title normalisation used to match venues across lists.

Matching is done by ISSN first. A normalised title is only a fallback, because
unrelated journals can share a title (OpenAlex lists two sources named
"Remote Sensing", one of them from a different publisher with a different ISSN)
and because hijacked journals copy the ISSN of the genuine journal.
"""

from __future__ import annotations

import re
import unicodedata

# Hyphen, hyphen-like dashes (U+2010 to U+2015) and the minus sign are all seen in real lists.
_SEP = "‐-―−\\-\\s"
_ISSN_RE = re.compile(rf"(?<![0-9A-Za-z])(\d{{4}})[{_SEP}]?(\d{{3}}[0-9Xx])(?![0-9A-Za-z])")
_PREFIX_RE = re.compile(r"^\s*(?:e-|p-|print\s+|online\s+|electronic\s+)?issn(?:-l)?\s*[:#]?\s*", re.IGNORECASE)


def check_digit(first_seven: str) -> str:
    """ISSN check character for the first seven digits (weights 8 down to 2, modulus 11)."""
    if len(first_seven) != 7 or not first_seven.isdigit():
        raise ValueError(f"need exactly seven digits, got {first_seven!r}")
    total = sum(int(d) * w for d, w in zip(first_seven, range(8, 1, -1)))
    remainder = (11 - total % 11) % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid_issn(value: object) -> bool:
    """True when value is exactly one ISSN with a correct check character."""
    norm = normalize_issn(value)
    return norm is not None and check_digit(norm[:4] + norm[5:8]) == norm[8]


def normalize_issn(value: object) -> str | None:
    """Return 'NNNN-NNNC' (upper case X) when value is exactly one ISSN, else None.

    Accepts hyphenless forms ('00344257'), other dash characters, lower case x and
    a leading 'ISSN' label. The check character is not validated here.
    """
    if value is None:
        return None
    text = _PREFIX_RE.sub("", str(value)).strip()
    match = _ISSN_RE.fullmatch(text)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2).upper()}"


def extract_issns(text: object, validate: bool = True) -> list[str]:
    """All distinct ISSNs found in free text, in order of appearance.

    With validate=True (default) tokens whose check character is wrong are dropped,
    which removes most accidental 8-digit numbers. Use validate=False to keep
    typos that a list maintainer may have made.
    """
    if text is None:
        return []
    found: list[str] = []
    for m in _ISSN_RE.finditer(str(text)):
        norm = f"{m.group(1)}-{m.group(2).upper()}"
        if validate and check_digit(norm[:4] + norm[5:8]) != norm[8]:
            continue
        if norm not in found:
            found.append(norm)
    return found


_TRAILING_QUALIFIER = re.compile(r"\([^()]*\)\s*$")


def normalize_title(title: object) -> str:
    """Case, accent and punctuation insensitive form of a journal title.

    '&' is read as 'and', a leading 'The' is dropped and one trailing qualifier such
    as '(Basel)' or '(United States)' is removed. Returns '' when nothing usable is left,
    and an empty result never matches anything.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", str(title))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " and ")
    text = _TRAILING_QUALIFIER.sub(" ", text)
    text = re.sub(r"[^0-9a-z]+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return re.sub(r"\s+", " ", text)
