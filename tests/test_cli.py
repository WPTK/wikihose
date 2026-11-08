from __future__ import annotations

from pathlib import Path

import pytest

from wikihose import __version__
from wikihose.cli import main


def quick(sample: Path) -> list[str]:
    """Arguments that run the replay of the sample without a screen and stop at once."""
    return ["--replay", str(sample), "--headless-seconds", "0"]


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


def test_bad_config_warns_on_stderr_and_no_config_skips_it(
    capsys: pytest.CaptureFixture[str], sample_path: Path
) -> None:
    from wikihose.paths import config_dir

    config_dir().mkdir(parents=True)
    (config_dir() / "config.toml").write_text('mystery = 1\nwiki = "nope"\n', encoding="utf-8")
    assert main([*quick(sample_path)]) == 0
    assert "unknown key 'mystery'" in capsys.readouterr().err
    assert main(["--no-config", *quick(sample_path)]) == 0
    assert capsys.readouterr().err == ""


def test_debug_logs_to_the_log_dir(capsys: pytest.CaptureFixture[str], sample_path: Path) -> None:
    import logging

    from wikihose.paths import log_dir

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert main(["--debug", *quick(sample_path)]) == 0
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


def test_replay_headless_prints_the_summary(capsys: pytest.CaptureFixture[str], sample_path: Path) -> None:
    assert main(["--replay", str(sample_path), "--headless-seconds", "2", "--speed", "16"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("edits=")
    assert ", edits_per_min=" in out
    assert ", top=" in out
    assert len(out.splitlines()) == 1


def test_replay_of_a_missing_file_says_so(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--replay", str(tmp_path / "nope.jsonl.gz"), "--headless-seconds", "0"]) == 1
    assert "wikihose:" in capsys.readouterr().err


def test_record_refuses_to_overwrite_without_force(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    target = tmp_path / "out.jsonl.gz"
    target.write_bytes(b"keep me")
    with pytest.raises(SystemExit) as exit_info:
        main(["--record", str(target)])
    assert exit_info.value.code == 2
    assert "--force" in capsys.readouterr().err
    assert target.read_bytes() == b"keep me"


@pytest.mark.parametrize(
    "args",
    [["--wiki", "nope"], ["--speed", "2"], ["--force"], ["--replay", "a", "--record", "b"]],
)
def test_bad_flag_combinations_are_refused(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    assert exit_info.value.code == 2


def test_wiki_flag_picks_the_wiki_for_the_summary(capsys: pytest.CaptureFixture[str], sample_path: Path) -> None:
    assert main(["--wiki", "cywiki", "--replay", str(sample_path), "--headless-seconds", "60", "--speed", "16"]) == 0
    assert "top=Tylluan sgops Mantanani (1)" in capsys.readouterr().out


async def test_headless_run_counts_the_sample_on_a_virtual_clock(clock, sample_path: Path) -> None:
    from wikihose.headless import run_headless
    from wikihose.sources.replay import ReplaySource

    summary = await run_headless(ReplaySource(sample_path, clock), "enwiki", 1000)
    assert summary.startswith("edits=")
    assert "edits_per_min=" in summary
