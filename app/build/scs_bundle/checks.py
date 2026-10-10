"""Content checks of a built page: open-build cleanliness, credentials, the toolkit holder's name, dashes.

Credentials are never printed: the scan reports only the variable name whose 12-character prefix was found.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

GFW_NAME = "global fishing watch"


def env_prefixes(env_path: Path, n: int = 12) -> dict[str, str]:
    """{VAR: first n characters of its value} for every non-trivial value in .env (never printed)."""
    out = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if len(v) >= 8:
            out[k.strip().removeprefix("export ").strip()] = v[:n]
    return out


def credential_hits(text: str, prefixes: dict[str, str]) -> list[str]:
    return sorted(k for k, p in prefixes.items() if p and p in text)


def json_keys(obj, out: set | None = None) -> set:
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            json_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            json_keys(v, out)
    return out


def research_true(obj) -> int:
    """Count of `research_only` values that are true: plain fields, const columns, and true rows of bool8 columns."""
    import base64

    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "research_only":
                if v is True:
                    n += 1
                elif isinstance(v, dict) and v.get("t") == "const" and v.get("v") is True:
                    n += 1
                elif isinstance(v, dict) and v.get("t") == "bool8" and v.get("b"):
                    n += sum(1 for x in base64.b64decode(v["b"]) if x == 1)
            n += research_true(v)
    elif isinstance(obj, list):
        for v in obj:
            n += research_true(v)
    return n


def open_build_problems(parts: dict) -> list[str]:
    """Problems that make an open page unpublishable: a gfw field name, the GFW name, research_only true, EEZ polygons."""
    probs = []
    keys = json_keys(parts)
    bad = sorted(k for k in keys if "gfw" in k.lower())
    if bad:
        probs.append(f"field names with 'gfw': {bad[:10]}")
    text = json.dumps(parts, ensure_ascii=False).lower()
    if GFW_NAME in text:
        probs.append("the string 'Global Fishing Watch' in a data part")
    if "gfw_" in text:
        probs.append("'gfw_' in a data part")
    rt = research_true(parts)
    if rt:
        probs.append(f"{rt} research_only true values")
    geo = (parts.get("geo") or {}).get("layers") or {}
    if "eez" in geo or any(k.startswith("eez") and v.get("kind") == "polygon" for k, v in geo.items()):
        probs.append("EEZ polygons in the geo part (only boundary lines may be embedded)")
    return probs


def shell_mentions(shell: str) -> dict:
    """Mentions of research-only names in the frontend shell itself (shared by both builds; reported, not fatal)."""
    low = shell.lower()
    return {"Global Fishing Watch": low.count(GFW_NAME), "gfw": len(re.findall(r"gfw", low))}


def vendor_name(frontend_dir: Path) -> str | None:
    """The toolkit copyright holder's name, read at run time from @blueprintjs/core/package.json (spec PW-16)."""
    p = frontend_dir / "node_modules" / "@blueprintjs" / "core" / "package.json"
    try:
        author = json.loads(p.read_text()).get("author")
    except (OSError, ValueError):
        return None
    if isinstance(author, dict):
        author = author.get("name")
    if not author:
        return None
    return str(author).split()[0].strip(",").lower() or None


def dash_count(text: str) -> int:
    return text.count("\u2013") + text.count("\u2014")
