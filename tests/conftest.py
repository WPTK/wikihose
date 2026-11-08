from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests marked ``live`` unless WIKIHOSE_LIVE=1."""
    if os.environ.get("WIKIHOSE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set WIKIHOSE_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
