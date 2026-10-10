"""Build guard: the open build never opens anything under data/research/ (board D2, contract 1 Builds).

Every file read of the leads builder goes through `checked_path`, which raises OpenBuildGuardError for a research path
in the open build. The guard tests both the literal path text and the resolved location, so a symlink or a relative
spelling cannot slip past it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from darkvessel.config import DATA_DIR

RESEARCH_DIR = DATA_DIR / "research"


class OpenBuildGuardError(RuntimeError):
    """Raised when the open build tries to read a research-only path."""


def is_research_path(path: str | Path) -> bool:
    p = Path(path)
    if "data/research" in p.as_posix() or any(part == "research" for part in p.parts):
        return True
    try:
        return p.resolve().is_relative_to(RESEARCH_DIR.resolve())
    except (OSError, RuntimeError):
        return False


def checked_path(path: str | Path, build: str) -> Path:
    """Return `path` as a Path, or raise if `build` is 'open' and the path is research-only."""
    if build not in ("open", "research"):
        raise ValueError(f"unknown build {build!r}")
    p = Path(path)
    if build == "open" and is_research_path(p):
        raise OpenBuildGuardError(f"open build must never read {p.as_posix()} (data/research/ is research-only)")
    return p


def read_parquet(path: str | Path, build: str, columns: list[str] | None = None) -> pd.DataFrame:
    return pd.read_parquet(checked_path(path, build), columns=columns)


def read_layer(path: str | Path, layer: str, build: str, columns: list[str] | None = None,
               read_geometry: bool = False):
    import pyogrio

    p = checked_path(path, build)
    return pyogrio.read_dataframe(p, layer=layer, columns=columns, read_geometry=read_geometry)


def read_json(path: str | Path, build: str):
    with open(checked_path(path, build)) as fh:
        return json.load(fh)


def layers_of(path: str | Path, build: str) -> list[str]:
    import pyogrio

    return [l[0] for l in pyogrio.list_layers(checked_path(path, build))]
