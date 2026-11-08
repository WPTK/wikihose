"""The HTTP client: User-Agent, Retry-After, the request budget, get_json, the SSE parser and streaming."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from wikihose import __version__
from wikihose.clock import VirtualClock
from wikihose.net import Client, FetchError, RequestBudget, retry_after, user_agent
from wikihose.sse import SseEvent, SseParser

URL = "https://example.test/api"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

# --- user agent and Retry-After --------------------------------------------------------------------------------------


def test_user_agent_without_and_with_contact() -> None:
    base = f"wikihose/{__version__} (https://github.com/WPTK/wikihose"
    tail = f" httpx/{httpx.__version__}"
    assert user_agent() == f"{base}){tail}"
    assert user_agent("https://example.org/me") == f"{base}; https://example.org/me){tail}"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("12", 12.0),
        ("0", 0.0),
        ("Sat, 07 Nov 2026 12:01:00 GMT", 60.0),
        ("Sat, 07 Nov 2026 11:00:00 GMT", 0.0),
        (None, 5.0),
        ("", 5.0),
        ("soon", 5.0),
        ("Sat, 99 Nov 2026 12:01:00 GMT", 5.0),
    ],
)
def test_retry_after(header: str | None, expected: float) -> None:
    assert retry_after(header, NOW) == expected


def test_retry_after_default_is_never_below_five() -> None:
    assert retry_after(None, NOW, default=1.0) == 5.0
    assert retry_after(None, NOW, default=20.0) == 20.0


# --- request budget --------------------------------------------------------------------------------------------------


async def test_budget_admits_150_then_the_next_waits_for_the_window(clock: VirtualClock, budget: RequestBudget) -> None:
    for _ in range(150):
        async with budget.slot():
            pass
    assert clock.monotonic() == 0.0
    async with budget.slot():
        pass
    assert clock.monotonic() == 60.0
    assert budget.stats()["used"] == 1


async def test_budget_window_rolls_with_spread_out_starts(clock: VirtualClock) -> None:
    budget = RequestBudget(clock, per_minute=2)
    async with budget.slot():
        pass
    await clock.sleep(30)
    async with budget.slot():
        pass
    async with budget.slot():  # the third has to wait for the first to leave the window
        pass
    assert clock.monotonic() == 60.0


async def test_budget_never_has_more_than_three_in_flight(budget: RequestBudget) -> None:
    running = 0
    peak = 0

    async def one() -> None:
        nonlocal running, peak
        async with budget.slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            running -= 1

    await asyncio.gather(*(one() for _ in range(10)))
    assert peak == 3


async def test_budget_pause_holds_every_caller(clock: VirtualClock, budget: RequestBudget) -> None:
    budget.pause_until(30.0)
    async with budget.slot():
        pass
    assert clock.monotonic() == 30.0
    assert budget.stats()["paused_until"] == 30.0
    budget.pause_until(10.0)  # an earlier pause never shortens a later one
    assert budget.stats()["paused_until"] == 30.0


# --- get_json --------------------------------------------------------------------------------------------------------


@respx.mock
async def test_get_json_200_sends_the_user_agent_and_params(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"a": 1}))
    assert await client.get_json(URL, {"x": "1"}) == {"a": 1}
    request = route.calls.last.request
    assert request.headers["user-agent"] == user_agent()
    assert request.url.params["x"] == "1"
    assert "gzip" in request.headers["accept-encoding"]


@respx.mock
async def test_get_json_sends_the_contact_in_the_user_agent(clock: VirtualClock, budget: RequestBudget) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    http = Client(clock, budget, contact="https://example.org/me")
    try:
        await http.get_json(URL)
    finally:
        await http.aclose()
    assert "; https://example.org/me)" in route.calls.last.request.headers["user-agent"]


@respx.mock
async def test_get_json_404_is_none_without_retrying(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    assert await client.get_json(URL) is None
    assert route.call_count == 1


@respx.mock
async def test_get_json_429_waits_for_retry_after_then_succeeds(client: Client, clock: VirtualClock) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "12"}), httpx.Response(200, json={"ok": True})]
    )
    assert await client.get_json(URL) == {"ok": True}
    assert route.call_count == 2
    assert clock.monotonic() >= 12.0


@respx.mock
async def test_get_json_429_three_times_is_rate_limited_with_the_wait(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "12"}))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert route.call_count == 3
    assert caught.value.kind == "rate_limited"
    assert caught.value.retry_after == 12.0


@respx.mock
async def test_get_json_429_without_retry_after_backs_off_exponentially(client: Client, clock: VirtualClock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert caught.value.retry_after == 20.0  # 5, 10, then 20
    assert clock.monotonic() >= 15.0  # waited the first two before trying again


@respx.mock
async def test_get_json_500s_are_down_after_three_tries(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert caught.value.kind == "down"
    assert route.call_count == 3


@respx.mock
async def test_get_json_network_error_is_down(client: Client) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert caught.value.kind == "down"


@respx.mock
async def test_get_json_500_then_200_recovers(client: Client) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(502), httpx.Response(200, json=[1])])
    assert await client.get_json(URL) == [1]


@respx.mock
async def test_get_json_bad_json_and_other_4xx_are_bad_data(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert caught.value.kind == "bad_data"
    route.mock(return_value=httpx.Response(400))
    with pytest.raises(FetchError) as caught:
        await client.get_json(URL)
    assert caught.value.kind == "bad_data"
    assert route.call_count == 2  # neither was retried


@respx.mock
async def test_a_429_makes_the_next_request_wait_too(client: Client, clock: VirtualClock) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "40"}), httpx.Response(200, json=1)])
    other = respx.get("https://example.test/other").mock(return_value=httpx.Response(200, json=2))
    first = asyncio.create_task(client.get_json(URL))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert await client.get_json("https://example.test/other") == 2
    assert other.call_count == 1
    assert clock.monotonic() >= 40.0
    assert await first == 1


@respx.mock
async def test_the_disk_cache_serves_a_fresh_response_once(
    clock: VirtualClock, budget: RequestBudget, tmp_path: Path
) -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, json={"n": 1}, headers={"Cache-Control": "max-age=3600"})
    )
    http = Client(clock, budget, cache_dir=tmp_path / "cache")
    try:
        assert await http.get_json(URL) == {"n": 1}
        assert await http.get_json(URL) == {"n": 1}
    finally:
        await http.aclose()
    assert route.call_count == 1
    assert any((tmp_path / "cache").iterdir())


@respx.mock
async def test_the_disk_cache_does_not_keep_uncacheable_responses(
    clock: VirtualClock, budget: RequestBudget, tmp_path: Path
) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}, headers={"Cache-Control": "no-store"}))
    http = Client(clock, budget, cache_dir=tmp_path / "cache")
    try:
        await http.get_json(URL)
        await http.get_json(URL)
    finally:
        await http.aclose()
    assert route.call_count == 2


@respx.mock
async def test_a_corrupt_cache_database_is_replaced_not_fatal(
    clock: VirtualClock, budget: RequestBudget, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "http-cache.db").write_bytes(b"this is not a database" * 100)
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[1], headers={"Cache-Control": "max-age=60"}))
    http = Client(clock, budget, cache_dir=cache)
    try:
        assert await http.get_json(URL) == [1]
        assert await http.get_json(URL) == [1]
    finally:
        await http.aclose()
    assert route.call_count == 1  # the cache works again after the bad file was dropped


# --- SSE parser ------------------------------------------------------------------------------------------------------


def feed(text: str) -> list[SseEvent]:
    parser = SseParser()
    return [e for line in text.split("\n") if (e := parser.feed(line)) is not None]


def test_sse_basic_event_and_default_name() -> None:
    assert feed("id: 1\ndata: hello\n\n") == [SseEvent("1", "message", "hello")]


def test_sse_comments_and_retry_are_ignored() -> None:
    assert feed(": keepalive\nretry: 3000\ndata: x\n\n") == [SseEvent(None, "message", "x")]


def test_sse_multiline_data_is_joined_with_newlines() -> None:
    assert feed("data: a\ndata: b\ndata:\ndata: c\n\n") == [SseEvent(None, "message", "a\nb\n\nc")]


def test_sse_id_carries_over_until_replaced() -> None:
    events = feed("id: 1\ndata: a\n\ndata: b\n\nid: 2\ndata: c\n\n")
    assert [e.id for e in events] == ["1", "1", "2"]


def test_sse_named_event_and_crlf() -> None:
    parser = SseParser()
    lines = ["event: message", 'id: [{"a":1}]', 'data: {"x":1}', ""]
    events = [e for line in lines if (e := parser.feed(line + "\r")) is not None]
    assert events == [SseEvent('[{"a":1}]', "message", '{"x":1}')]


def test_sse_event_without_data_is_not_dispatched_and_does_not_leak_its_name() -> None:
    assert feed("event: ping\n\ndata: x\n\n") == [SseEvent(None, "message", "x")]


def test_sse_id_with_nul_is_ignored_and_one_leading_space_is_stripped() -> None:
    assert feed("id: a\0b\ndata:  two spaces\n\n") == [SseEvent(None, "message", " two spaces")]


# --- stream ----------------------------------------------------------------------------------------------------------

BODY = b": hi\nid: 1\ndata: one\n\nid: 2\ndata: two\n\n"


@respx.mock
async def test_stream_yields_events_and_sends_the_headers(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    events = [e async for e in client.stream(URL, "prev")]
    assert [(e.id, e.data) for e in events] == [("1", "one"), ("2", "two")]
    request = route.calls.last.request
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["last-event-id"] == "prev"
    assert request.headers["user-agent"] == user_agent()


@respx.mock
async def test_stream_without_an_id_sends_no_last_event_id(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    [e async for e in client.stream(URL, None)]
    assert "last-event-id" not in route.calls.last.request.headers


@respx.mock
async def test_stream_connect_counts_once_against_the_budget(client: Client, budget: RequestBudget) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    [e async for e in client.stream(URL)]
    assert budget.stats()["used"] == 1


@respx.mock
async def test_stream_429_is_rate_limited_with_retry_after(client: Client, clock: VirtualClock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "12"}))
    with pytest.raises(FetchError) as caught:
        [e async for e in client.stream(URL)]
    assert caught.value.kind == "rate_limited"
    assert caught.value.retry_after == 12.0


@respx.mock
async def test_stream_errors_map_to_kinds(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503, headers={"Retry-After": "7"}))
    with pytest.raises(FetchError) as caught:
        [e async for e in client.stream(URL)]
    assert (caught.value.kind, caught.value.retry_after) == ("rate_limited", 7.0)
    route.mock(return_value=httpx.Response(500))
    with pytest.raises(FetchError) as caught:
        [e async for e in client.stream(URL)]
    assert caught.value.kind == "down"
    route.mock(return_value=httpx.Response(403))
    with pytest.raises(FetchError) as caught:
        [e async for e in client.stream(URL)]
    assert caught.value.kind == "bad_data"
    route.mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(FetchError) as caught:
        [e async for e in client.stream(URL)]
    assert caught.value.kind == "down"
