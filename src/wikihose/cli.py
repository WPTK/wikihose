"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from wikihose import __version__
from wikihose.clock import SystemClock
from wikihose.config import Config, default_config_path, is_valid_wiki, load_config
from wikihose.paths import db_path, log_dir

if TYPE_CHECKING:
    from wikihose.clock import Clock
    from wikihose.net import Client
    from wikihose.ui.tabs.reading import Factory

DEMO_WIKI = "enwiki"


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
    parser.add_argument("--wiki", metavar="NAME", help="the wiki to watch this time, such as enwiki or dewiki")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--demo", action="store_true", help="play the bundled six-minute recording of English Wikipedia; no network"
    )
    source.add_argument("--replay", metavar="FILE", type=Path, help="play back a recording instead of going live")
    parser.add_argument("--speed", metavar="N", type=float, help="replay speed, 0.5 to 16 (default 1)")
    source.add_argument(
        "--record",
        metavar="FILE",
        type=Path,
        help="go live and save the stream to FILE (only the selected wiki's edits are saved)",
    )
    parser.add_argument("--force", action="store_true", help="let --record overwrite an existing file")
    parser.add_argument(
        "--headless-seconds",
        metavar="N",
        type=float,
        help="testing aid: run without a screen for N seconds of source time, print one summary line, then exit",
    )
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        from wikihose.selftest import run_selftest

        return run_selftest()
    if args.wiki is not None and not is_valid_wiki(args.wiki):
        parser.error(f"--wiki {args.wiki!r} is not a wiki name; use something like enwiki or dewiki")
    if args.demo and args.wiki is not None:
        parser.error("--wiki cannot be used with --demo; the demo is English Wikipedia (enwiki)")
    if args.speed is not None and args.replay is None:
        parser.error("--speed only applies to --replay")
    if args.force and args.record is None:
        parser.error("--force only applies to --record")
    if args.record is not None and args.record.exists() and not args.force:
        parser.error(f"{args.record} already exists; use --force to overwrite it")
    setup_logging(args.debug)
    config_path = None if args.no_config else default_config_path()
    config, warnings = load_config(config_path)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if args.wiki is not None:
        config = replace(config, wiki=args.wiki)
    if args.demo:
        config = replace(config, wiki=DEMO_WIKI)
        config_path = None  # the demo never writes the config
    return asyncio.run(run(args, config, config_path))


def reading_factory(client: Client | None, clock: Clock) -> Factory | None:
    """The Reading tab's data for a wiki, or None when there is no network client (a replay)."""
    if client is None:
        return None
    from wikihose.reading import ReadingData

    def make(wiki: str) -> ReadingData | None:
        try:
            return ReadingData(client, clock, wiki)
        except ValueError:
            return None  # a wiki the Pageviews project name cannot be worked out for

    return make


async def run(args: argparse.Namespace, config: Config, config_path: Path | None) -> int:
    """Build the data source the arguments ask for, then run the app (or the headless summary) on it."""
    with ExitStack() as resources:  # keeps the demo file's path valid for the whole run, since the demo rewinds
        return await _run(args, config, config_path, resources)


async def _run(args: argparse.Namespace, config: Config, config_path: Path | None, resources: ExitStack) -> int:
    from wikihose.net import Client, RequestBudget
    from wikihose.sources.live import LiveSource
    from wikihose.sources.protocol import DataSource
    from wikihose.sources.recording import Recorder, RecordingError, demo_recording
    from wikihose.sources.replay import ReplaySource

    clock = SystemClock()
    client: Client | None = None
    source: DataSource
    if args.replay is not None or args.demo:
        try:
            if args.demo:
                source = ReplaySource(resources.enter_context(demo_recording()), clock)
            else:
                source = ReplaySource(args.replay, clock, speed=args.speed or 1.0)
        except (RecordingError, OSError) as exc:
            print(f"wikihose: {exc}", file=sys.stderr)
            return 1
    else:
        client = Client(clock, RequestBudget(clock), config.contact)
        recorder = Recorder(args.record, clock, wiki=config.wiki) if args.record is not None else None
        source = LiveSource(client, clock, recorder=recorder)
    try:
        if args.headless_seconds is not None:
            from wikihose.headless import run_headless

            print(await run_headless(source, config.wiki, args.headless_seconds))
            return 0
        from wikihose.store import Store
        from wikihose.ui.app import WikihoseApp

        replaying = args.replay is not None or args.demo
        store = None if replaying else Store(db_path())  # a replay must not add old edits to the database
        try:
            app = WikihoseApp(
                source,
                config,
                clock=clock,
                store=store,
                config_path=config_path,
                reading=reading_factory(client, clock),
                loop_replay=args.demo,
            )
            await app.run_async()
        finally:
            if store is not None:
                store.close()
        return 0
    finally:
        await source.aclose()
        if client is not None:
            await client.aclose()
