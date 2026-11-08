"""Chaos tests: whatever Wikimedia sends back, the Reading data layer returns rows or raises FetchError, nothing else."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from itertools import cycle
from pathlib import Path
from typing import Any

import httpx
import respx
from hypothesis import given, settings
from hypothesis import strategies as st

from wikihose.clock import VirtualClock
from wikihose.net import Client, FetchError, RequestBudget
from wikihose.reading import DAYS, ReadingData, parse_history, parse_summary, parse_top

SITEINFO = json.loads((Path(__file__).parent / "fixtures" / "siteinfo" / "enwiki.json").read_text(encoding="utf-8"))

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4),
    max_leaves=12,
)

article_rows = st.lists(
    st.fixed_dictionaries({"article": st.text(max_size=12), "views": st.integers(-5, 10**7), "rank": st.integers()}),
    max_size=30,
)
good_top = article_rows.map(lambda rows: {"items": [{"articles": rows}]})
good_history = st.fixed_dictionaries(
    {"items": st.lists(st.fixed_dictionaries({"timestamp": st.text(max_size=12), "views": json_values}), max_size=40)}
)


@given(json_values)
def test_parsers_survive_any_json(payload: Any) -> None:
    assert isinstance(parse_top(payload), dict)
    assert len(parse_history(payload, date(2026, 10, 8))) == DAYS
    parse_summary(payload)


@given(good_top)
def test_parse_top_returns_only_non_negative_ints(payload: dict[str, Any]) -> None:
    assert all(isinstance(v, int) and v >= 0 for v in parse_top(payload).values())


@given(good_history)
def test_parse_history_is_always_30_non_negative_days(payload: dict[str, Any]) -> None:
    series = parse_history(payload, date(2026, 10, 8))
    assert len(series) == DAYS and all(isinstance(v, int) and v >= 0 for v in series)


responses = st.one_of(
    st.just(("status", 404)),
    st.just(("status", 429)),
    st.just(("status", 503)),
    st.just(("status", 500)),
    st.just(("status", 403)),
    st.just(("text", "<html>not json")),
    st.just(("error", "timeout")),
    json_values.map(lambda v: ("json", v)),
    good_top.map(lambda v: ("json", v)),
    good_history.map(lambda v: ("json", v)),
)


def _respond(kind: str, value: Any) -> httpx.Response:
    if kind == "status":
        return httpx.Response(value)
    if kind == "text":
        return httpx.Response(200, text=value)
    if kind == "error":
        raise httpx.ReadTimeout("chaos")
    return httpx.Response(200, json=value)


def test_load_returns_rows_or_a_fetch_error() -> None:
    # One client and event loop for all examples: building an SSL context per example is what makes this slow.
    clock = VirtualClock(datetime(2026, 11, 7, 12, 0, tzinfo=UTC))
    with asyncio.Runner() as runner:
        client = Client(clock, RequestBudget(clock))

        async def one(plan: list[tuple[str, Any]], good_siteinfo: bool) -> None:
            steps = cycle(plan)
            with respx.mock(assert_all_called=False) as router:
                router.get("https://en.wikipedia.org/w/api.php").mock(
                    return_value=httpx.Response(200, json=SITEINFO if good_siteinfo else {"query": []})
                )
                router.get(url__regex=r"https://(wikimedia|en\.wikipedia)\.org/api/rest_v1/.*").mock(
                    side_effect=lambda request: _respond(*next(steps))
                )
                try:
                    rows = await ReadingData(client, clock, exact_count=3).load()
                except FetchError:
                    return
                assert rows == sorted(rows, key=lambda r: (-r.multiple, r.title))
                assert all(r.views >= 5000 and r.multiple >= 0 for r in rows)
                assert all(len(r.series) in (0, DAYS) for r in rows)

        @settings(max_examples=60, deadline=None)
        @given(st.lists(responses, min_size=1, max_size=8), st.booleans())
        def examples(plan: list[tuple[str, Any]], good_siteinfo: bool) -> None:
            runner.run(one(plan, good_siteinfo))

        try:
            examples()
        finally:
            runner.run(client.aclose())
