from __future__ import annotations

import pytest

from wikihose import __version__
from wikihose.cli import main


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"wikihose {__version__}" == "wikihose 0.1.0"


def test_help_says_unofficial(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "unofficial" in out
    assert "Wikimedia Foundation" in out


def test_no_arguments_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "not built yet" in capsys.readouterr().out
