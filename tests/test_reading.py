"""The Reading tab's data layer: parsing, bounded baselines, exact history alignment and the full load."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.reading import (
    DAYS,
    ReadingData,
    SpikeRow,
    bounded_baseline,
    floor_of,
    parse_history,
    parse_summary,
    parse_top,
    project_of,
    spike_multiple,
)

SITEINFO = json.loads((Path(__file__).parent / "fixtures" / "siteinfo" / "enwiki.json").read_text(encoding="utf-8"))
NEWEST = date(2026, 11, 6)  # the VirtualClock starts on 7 Nov 2026


def top_payload(rows: dict[str, int]) -> dict[str, Any]:
    articles = [{"article": a, "views": v, "rank": i + 1} for i, (a, v) in enumerate(rows.items())]
    return {"items": [{"project": "en.wikipedia", "access": "all-access", "articles": articles}]}


def history_payload(first: date, views: dict[int, int]) -> dict[str, Any]:
    """Items for the given day offsets only, as the API leaves out days with no data."""
    items = []
    for offset in sorted(views):
        day = date.fromordinal(first.toordinal() + offset)
        items.append({"article": "x", "timestamp": f"{day:%Y%m%d}00", "views": views[offset]})
    return {"items": items}


# --- pure parts ------------------------------------------------------------------------------------------------------


def test_project_of() -> None:
    assert project_of("enwiki") == "en.wikipedia"
    assert project_of("be_x_oldwiki") == "be-x-old.wikipedia"
    with pytest.raises(ValueError, match="unsupported"):
        project_of("commonswiktionary")


def test_parse_top_keeps_rank_order_and_drops_junk() -> None:
    payload = {
        "items": [{"articles": [{"article": "A", "views": 5}, {"article": "B"}, 7, {"article": "A", "views": 9}]}]
    }
    assert parse_top(payload) == {"A": 5}
    assert parse_top(None) == {}
    assert parse_top({"items": []}) == {}


def test_parse_history_aligns_by_timestamp_not_position() -> None:
    first = date(2026, 10, 8)
    series = parse_history(history_payload(first, {0: 5, 2: 7, 29: 9}), first)
    assert len(series) == DAYS
    assert (series[0], series[1], series[2], series[29]) == (5, 0, 7, 9)


def test_parse_history_ignores_out_of_window_and_bad_items() -> None:
    first = date(2026, 10, 8)
    payload = {"items": [{"timestamp": "2020010100", "views": 4}, {"timestamp": "oops", "views": 1}, {"views": 3}, 9]}
    assert parse_history(payload, first) == [0] * DAYS


def test_bounded_baseline_counts_a_missing_day_at_the_floor() -> None:
    days = [{"A": 100, "Z": 10}, {"Z": 10}, {"Z": 10}]  # A listed once, floor is 10 on days 2 and 3, 10 on day 1
    assert floor_of(days[0]) == 10
    assert bounded_baseline("A", days) == 10  # median of 100, 10, 10
    assert bounded_baseline("missing", days) == 10
    assert bounded_baseline("A", []) == 0.0


def test_spike_multiple_never_divides_by_zero() -> None:
    assert spike_multiple(50, 0.0) == 50
    assert spike_multiple(300, 100.0) == 3.0


def test_parse_summary() -> None:
    assert parse_summary({"title": "T", "extract": " text ", "description": "d"}) is not None
    assert parse_summary({"title": "T", "extract": " "}) is None
    assert parse_summary([]) is None


# --- the full load against mocked endpoints --------------------------------------------------------------------------


def mock_wiki(router: respx.MockRouter, *, newest: date = NEWEST, lists: int = DAYS) -> dict[str, int]:
    """30 daily lists: a steady giant, a one-day spike, a non-article, a small page and Main_Page."""
    router.get("https://en.wikipedia.org/w/api.php").mock(return_value=httpx.Response(200, json=SITEINFO))
    today = {
        "Steady": 40000,
        "Spiky": 90000,
        "Special:Search": 70000,
        "Ramayana:_Part_1": 60000,
        "Tiny": 900,
        "Main_Page": 99999,
    }
    for n in range(lists):
        day = date.fromordinal(newest.toordinal() - n)
        rows = dict(today) if n == 0 else {"Steady": 40000, "Special:Search": 70000, "Main_Page": 99999, "Filler": 8000}
        if n != 0:
            rows["Ramayana:_Part_1"] = 20000
        router.get(
            url=f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{day:%Y/%m/%d}"
        ).mock(return_value=httpx.Response(200, json=top_payload(rows)))
    return today


@respx.mock
async def test_load_ranks_spikes_and_filters(client: Client, clock: VirtualClock) -> None:
    mock_wiki(respx.mock)
    first = date.fromordinal(NEWEST.toordinal() - (DAYS - 1))
    # Spiky was tiny all month (days with no data are absent from the API's answer); Steady is steady.
    respx.get(url__regex=r".*/per-article/.*/Spiky/daily/.*").mock(
        return_value=httpx.Response(200, json=history_payload(first, dict.fromkeys(range(0, 28, 2), 200) | {29: 90000}))
    )
    respx.get(url__regex=r".*/per-article/.*/Steady/daily/.*").mock(
        return_value=httpx.Response(200, json=history_payload(first, dict.fromkeys(range(DAYS), 40000)))
    )
    respx.get(url__regex=r".*/per-article/.*/Ramayana%3A_Part_1/daily/.*").mock(return_value=httpx.Response(404))
    data = ReadingData(client, clock)
    seen: list[tuple[int, int]] = []
    rows = await data.load(lambda done, total: seen.append((done, total)))
    titles = [r.title for r in rows]
    assert titles == ["Spiky", "Ramayana:_Part_1", "Steady"]  # Special:Search, Main_Page and the tiny page are gone
    spiky, ramayana, steady = rows
    assert spiky.exact and spiky.baseline == 0.0 + sorted([200] * 14 + [0] * 15)[14] and spiky.views == 90000
    assert spiky.multiple == 90000  # a median below 1 counts as 1
    assert len(spiky.series) == DAYS and spiky.series[-1] == 90000 and spiky.series[1] == 0
    assert not ramayana.exact and ramayana.series == () and ramayana.baseline == 20000  # 404: keeps the bound
    assert steady.exact and steady.multiple == 1.0
    assert seen[-1][0] == seen[-1][1]  # progress ends complete even with few candidates
    await data.namespace_filter()


@respx.mock
async def test_load_falls_back_to_an_older_day_when_the_newest_is_missing(client: Client, clock: VirtualClock) -> None:
    mock_wiki(respx.mock, newest=date(2026, 11, 5))
    respx.get(url__regex=r".*/top/en.wikipedia/all-access/2026/11/06").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(404))
    rows = await ReadingData(client, clock).load()
    assert rows and rows[0].views == 90000


@respx.mock
async def test_load_with_no_lists_at_all_is_empty(client: Client, clock: VirtualClock) -> None:
    respx.get("https://en.wikipedia.org/w/api.php").mock(return_value=httpx.Response(200, json=SITEINFO))
    respx.get(url__regex=r".*/top/.*").mock(return_value=httpx.Response(404))
    assert await ReadingData(client, clock).load() == []


@respx.mock
async def test_summary_is_fetched_once(client: Client, clock: VirtualClock) -> None:
    route = respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Ramayana%3A_Part_1").mock(
        return_value=httpx.Response(
            200, json={"title": "Ramayana: Part 1", "extract": "A film.", "description": "2026"}
        )
    )
    data = ReadingData(client, clock)
    first = await data.summary("Ramayana:_Part_1")
    assert first is not None and first.extract == "A film."
    assert await data.summary("Ramayana:_Part_1") is first
    assert route.call_count == 1


def test_spike_row_display_title() -> None:
    assert SpikeRow("Ada_Lovelace", 1, 1.0, 1.0, (), False).display_title == "Ada Lovelace"


@respx.mock
async def test_window_and_utc_day(client: Client) -> None:
    """At 00:30 UTC on the 7th the newest list is the 6th; history runs from the 8th Oct to the 6th Nov."""
    mock_wiki(respx.mock)
    urls: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(404)

    respx.get(url__regex=r".*/per-article/.*").mock(side_effect=record)
    early = VirtualClock(datetime(2026, 11, 7, 0, 30, tzinfo=UTC))
    await ReadingData(client, early, exact_count=1).load()
    assert urls and urls[0].endswith("/daily/20261008/20261106")


@respx.mock
async def test_a_bad_history_answer_keeps_the_bound_and_a_404_is_remembered(
    client: Client, clock: VirtualClock
) -> None:
    mock_wiki(respx.mock)
    respx.get(url__regex=r".*/per-article/.*/Spiky/daily/.*").mock(return_value=httpx.Response(400))
    gone = respx.get(url__regex=r".*/per-article/.*/Ramayana.*").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r".*/per-article/.*/Steady/daily/.*").mock(return_value=httpx.Response(404))
    data = ReadingData(client, clock)
    rows = await data.load()
    assert rows[0].title == "Spiky" and not rows[0].exact
    await data.load()
    assert gone.call_count == 1


@respx.mock
async def test_too_few_earlier_lists_gives_no_ranking(client: Client, clock: VirtualClock) -> None:
    mock_wiki(respx.mock, lists=2)  # the newest day and one earlier
    respx.get(url__regex=r".*/top/.*").mock(return_value=httpx.Response(404))
    assert await ReadingData(client, clock).load() == []
