"""Command line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from wikihose import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wikihose",
        description=(
            "Watch Wikipedia live in your terminal: edits, reading spikes and the pages you track. "
            "wikihose is unofficial and not affiliated with the Wikimedia Foundation."
        ),
    )
    parser.add_argument("--version", action="version", version=f"wikihose {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("wikihose is not built yet. Try --version.")
    return 0
