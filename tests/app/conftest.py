"""Fixtures for the backend tests: synthetic data dirs and TestClients for the open and research builds (offline)."""

from __future__ import annotations

import shutil
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app" / "backend"))
warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)

from synthetic import write_all  # noqa: E402


@pytest.fixture(scope="session")
def fixture_src(tmp_path_factory) -> Path:
    return write_all(tmp_path_factory.mktemp("scs_data") / "data")


@pytest.fixture
def data_dir(fixture_src, tmp_path) -> Path:
    """A fresh copy of the synthetic data dir (tests that append decisions or touch files get their own)."""
    d = tmp_path / "data"
    shutil.copytree(fixture_src, d)
    return d


def make_client(data_dir: Path, build: str, frontend: Path | None = None):
    from fastapi.testclient import TestClient

    from scs_api.app import create_app
    from scs_api.config import Settings

    s = Settings(build=build, data_dir=data_dir, check_interval_s=0.0,
                 frontend_dist=frontend if frontend is not None else data_dir / "no_frontend")
    return TestClient(create_app(s))


@pytest.fixture
def open_client(data_dir):
    return make_client(data_dir, "open")


@pytest.fixture
def research_client(data_dir):
    return make_client(data_dir, "research")


@pytest.fixture(scope="session")
def shared_clients(fixture_src):
    """Read-only clients on the shared fixture (for the endpoint crawls)."""
    return {"open": make_client(fixture_src, "open"), "research": make_client(fixture_src, "research")}
