from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import stamina

from wikihose.clock import VirtualClock
from wikihose.net import Client, RequestBudget

# stamina waits for real between tries; turn the waits off but keep three tries, as in production.
stamina.set_testing(True, attempts=3)

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests marked ``live`` unless WIKIHOSE_LIVE=1."""
    if os.environ.get("WIKIHOSE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set WIKIHOSE_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads or writes the real config or data directory."""
    monkeypatch.setenv("WIKIHOSE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("WIKIHOSE_DATA_DIR", str(tmp_path / "data"))
    # Textual and Rich read NO_COLOR when an app or console is made; a shell that sets it would turn the snapshots mono.
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock(START)


@pytest.fixture
def budget(clock: VirtualClock) -> RequestBudget:
    return RequestBudget(clock)


@pytest.fixture
async def client(clock: VirtualClock, budget: RequestBudget) -> AsyncIterator[Client]:
    http = Client(clock, budget)
    yield http
    await http.aclose()


@pytest.fixture
def sample_path() -> Iterator[Path]:
    from wikihose.sources.recording import sample_recording

    with sample_recording() as path:
        yield path
