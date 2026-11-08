"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from wikihose import __version__
from wikihose.config import default_config_path, load_config
from wikihose.paths import log_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wikihose",
        description=(
            "Watch Wikipedia live in your terminal: edits, reading spikes and the pages you track. "
            "wikihose is unofficial and not affiliated with the Wikimedia Foundation."
        ),
    )
    parser.add_argument("--version", action="version", version=f"wikihose {__version__}")
    parser.add_argument("--selftest", action="store_true", help="check the install without the network, then exit")
    parser.add_argument("--no-config", action="store_true", help="ignore the config file and use the defaults")
    parser.add_argument("--debug", action="store_true", help="write debug logging to the log file")
    return parser


def setup_logging(debug: bool) -> None:
    """Warnings and above to stderr by default; with ``--debug``, everything to ``wikihose.log`` in the log dir."""
    if not debug:
        logging.basicConfig(level=logging.WARNING)
        return
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / "wikihose.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        from wikihose.selftest import run_selftest

        return run_selftest()
    setup_logging(args.debug)
    _config, warnings = load_config(None if args.no_config else default_config_path())
    for warning in warnings:
        print(warning, file=sys.stderr)
    print("wikihose is not built yet. Try --version.")
    return 0
