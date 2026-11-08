"""Fixtures of the real-terminal run. Every test gets a fresh program in a fresh pseudo-terminal, killed by PID.

The program is ``python -m wikihose --demo``, or the built executable when ``WIKIHOSE_EXE`` is set. The kit gives the
child empty ``COLUMNS``/``LINES``, ``PYTHONUTF8=1``, a dead proxy and scratch directories; here the config and data
directories of wikihose point into the kit's scratch directory too, so a run never touches the real ones.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator

import pytest
from uat_kit import Terminal, unavailable_reason  # tests/ is on sys.path (rootdir import mode)

REASON = unavailable_reason()
EXE_ENV = "WIKIHOSE_EXE"  # the built program, for the post-build run; unset means python -m wikihose
SIZES = [(80, 24), (120, 30)]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = __file__.rsplit("conftest.py", 1)[0]
    for item in items:
        if str(item.path).startswith(here):
            item.add_marker(pytest.mark.uat)
            item.add_marker(pytest.mark.timeout(90))
            if REASON:
                item.add_marker(pytest.mark.skip(reason=f"real-terminal layer unavailable: {REASON}"))


def launch(cols: int, rows: int) -> Terminal:
    exe = os.environ.get(EXE_ENV)
    argv = [exe, "--demo"] if exe else [sys.executable, "-m", "wikihose", "--demo"]
    env = {"WIKIHOSE_CONFIG_DIR": "{dir}/config", "WIKIHOSE_DATA_DIR": "{dir}/data"}
    return Terminal(argv, cols, rows, env=env)


@pytest.fixture(params=SIZES, ids=[f"{c}x{r}" for c, r in SIZES])
def size(request: pytest.FixtureRequest) -> tuple[int, int]:
    return request.param


@pytest.fixture
def start(size: tuple[int, int]) -> Iterator[Callable[[], Terminal]]:
    """``start()`` runs the program at the parametrized size and waits until the Live tab is drawn."""
    made: list[Terminal] = []

    def factory() -> Terminal:
        term = launch(*size)
        made.append(term)
        term.wait_for(lambda s: "wikihose" in s and "bots" in s, 45, "the Live tab")
        term.settle()
        return term

    yield factory
    for term in made:
        term.close()
