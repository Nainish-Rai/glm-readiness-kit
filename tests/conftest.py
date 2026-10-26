from __future__ import annotations

import os

# Typer forces a colour terminal when GITHUB_ACTIONS is set, which splits option names in --help output with ANSI
# codes; it reads this at import time, so it has to be set before anything imports typer.
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")

from collections.abc import Iterator
from pathlib import Path

import pytest

from glmkit.contracts.profile import EndpointProfile, get_profile
from glmkit.mock import MockConfig, MockServer

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GLMKIT_PLUGINS", "GLMKIT_CASES_DIRS", "GLMKIT_PROFILES_DIRS", "GLMKIT_MOCK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mock_server() -> Iterator[MockServer]:
    with MockServer(MockConfig()) as srv:
        yield srv


def profile_at(name: str, srv: MockServer) -> EndpointProfile:
    p = get_profile(name)
    return p.model_copy(update={"base_url": p.base_url.replace("{mock}", srv.url), "spawn_mock": False})
