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


def test_selftest_exits_zero_and_prints_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--selftest"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "selftest: ok"
    assert len(lines) > 1
    assert all(line.startswith("ok ") for line in lines[:-1])


def test_selftest_reports_a_failing_check(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from wikihose import selftest

    def broken() -> None:
        raise AssertionError("boom")

    monkeypatch.setattr(selftest, "CHECKS", [("a broken check", broken), *selftest.CHECKS[:1]])
    assert main(["--selftest"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  a broken check: AssertionError: boom" in out
    assert out.splitlines()[-1] == "selftest: FAILED (1)"


def test_help_lists_the_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for flag in ("--version", "--selftest", "--no-config", "--debug"):
        assert flag in out


def test_bad_config_warns_on_stderr_and_no_config_skips_it(capsys: pytest.CaptureFixture[str]) -> None:
    from wikihose.paths import config_dir

    config_dir().mkdir(parents=True)
    (config_dir() / "config.toml").write_text('mystery = 1\nwiki = "nope"\n', encoding="utf-8")
    assert main([]) == 0
    assert "unknown key 'mystery'" in capsys.readouterr().err
    assert main(["--no-config"]) == 0
    assert capsys.readouterr().err == ""


def test_debug_logs_to_the_log_dir(capsys: pytest.CaptureFixture[str]) -> None:
    import logging

    from wikihose.paths import log_dir

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert main(["--debug"]) == 0
        logging.getLogger("wikihose.test").debug("hello from the test")
        for handler in root.handlers:
            handler.flush()
        assert "hello from the test" in (log_dir() / "wikihose.log").read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(logging.WARNING)
