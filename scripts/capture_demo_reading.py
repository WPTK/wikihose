"""Remake src/wikihose/data/demo-reading-enwiki.json.gz, the Reading numbers ``--demo`` shows.

Goes live: about 110 requests to Wikimedia, 3 seconds apart (docs/development.md, "Recording live data"), so it takes
about six minutes. It ranks the newest full day the way the Reading tab does and keeps the summaries of the top 25
rows and of every ranked article the demo recording edits. Run it from the repository root, then read the result:

    .venv\\Scripts\\python scripts\\capture_demo_reading.py
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from pathlib import Path
from typing import Any

from wikihose.clock import SystemClock
from wikihose.demo_reading import FORMAT, VERSION
from wikihose.net import Client, FetchError, RequestBudget
from wikihose.reading import ReadingData, SpikeRow
from wikihose.sources.events import Edit, parse_recentchange
from wikihose.sources.recording import demo_recording, read_recording
from wikihose.titles import page_key

OUT = Path(__file__).resolve().parents[1] / "src" / "wikihose" / "data" / "demo-reading-enwiki.json.gz"
GAP = 3.0
SUMMARIES = 25


class Spaced:
    """Wraps the client so every request waits ``GAP`` seconds first. The client's own retries come on top: they
    honor Retry-After on a 429 or 503 and back off on other errors."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        await asyncio.sleep(GAP)
        print(url[:120], flush=True)
        return await self._client.get_json(url, params)


def edited_in_demo() -> set[str]:
    titles: set[str] = set()
    with demo_recording() as path:
        _, events = read_recording(path)
        for event in events:
            item = parse_recentchange(event.data)
            if isinstance(item, Edit) and item.namespace == 0 and not item.bot:
                titles.add(page_key(item.wiki, item.title)[1])
    return titles


def snapshot(day: str, rows: list[SpikeRow], summaries: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "day": day,
        "rows": [
            {
                "title": r.title,
                "views": r.views,
                "baseline": r.baseline,
                "series": list(r.series),
                "exact": r.exact,
            }
            for r in rows
        ],
        "summaries": summaries,
    }


def write(payload: dict[str, Any], out: Path = OUT) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with gzip.GzipFile(out, "wb", mtime=0) as handle:  # mtime 0: the same data gives the same bytes
        handle.write(text.encode("utf-8"))


async def main() -> int:
    clock = SystemClock()
    client = Client(clock, RequestBudget(clock))
    try:
        data = ReadingData(Spaced(client), clock)  # type: ignore[arg-type]
        rows = await data.load()
        if not rows or data.day is None:
            print("no ranking came back; try again later", file=sys.stderr)
            return 1
        edited = edited_in_demo()
        wanted = [r.title for r in rows[:SUMMARIES]]
        wanted += [r.title for r in rows if page_key("enwiki", r.title)[1] in edited and r.title not in wanted]
        summaries: dict[str, Any] = {}
        for title in wanted:
            try:
                summary = await data.summary(title)
            except FetchError as error:  # a missing summary is fine; losing six minutes of capture is not
                print(f"no summary for {title}: {error}", file=sys.stderr)
                continue
            if summary is not None:
                summaries[title] = {"title": summary.title, "description": summary.description}
                summaries[title]["extract"] = summary.extract
        write(snapshot(data.day.isoformat(), rows, summaries))
    finally:
        await client.aclose()
    print(f"wrote {OUT} ({len(rows)} rows, {len(summaries)} summaries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
