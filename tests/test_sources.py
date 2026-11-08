"""Events and the parser, the live source, the recording format and the replay source."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.sources.events import Edit, FeedStatus, PageMove, StreamItem, parse_recentchange
from wikihose.sources.live import RECENTCHANGE_URL, LiveSource
from wikihose.sources.protocol import DataSource
from wikihose.sources.recording import RecordedEvent, Recorder, RecordingError, read_recording
from wikihose.sources.replay import ReplaySource
from wikihose.sse import SseEvent

AT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def read_all(path: Path) -> tuple[list[RecordedEvent], int]:
    _, events = read_recording(path)
    return list(events), events.skipped


def expected_kind(raw: str) -> str | None:
    """What the sample line should parse to, decided from the JSON itself rather than by the parser."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if data["type"] in ("edit", "new"):
        return data["type"]
    if data["type"] == "log" and data["log_type"] == "move":
        return "move"
    return None


def make_edit(**changes: object) -> str:
    data: dict[str, object] = {
        "type": "edit",
        "namespace": 0,
        "title": "Ada Lovelace",
        "comment": "fix",
        "timestamp": 1794098367,
        "user": "Someone",
        "bot": False,
        "minor": True,
        "length": {"old": 100, "new": 130},
        "revision": {"old": 5, "new": 6},
        "server_name": "en.wikipedia.org",
        "wiki": "enwiki",
    }
    data.update(changes)
    return json.dumps({k: v for k, v in data.items() if v is not None or k == "length"})


# --- parse_recentchange ----------------------------------------------------------------------------------------------


def test_every_sample_line_parses_as_the_json_says(sample_path: Path) -> None:
    events, skipped = read_all(sample_path)
    assert skipped == 0
    kinds = []
    for event in events:
        item = parse_recentchange(event.data)
        kind = expected_kind(event.data)
        kinds.append(kind)
        match kind:
            case "edit" | "new":
                assert isinstance(item, Edit)
                assert item.kind == kind
            case "move":
                assert isinstance(item, PageMove)
            case None:
                assert item is None
    # the sample has each case, including the deliberately broken line
    assert {"edit", "new", "move", None} <= set(kinds)
    assert any(e.data == "{not json" for e in events)


def test_parse_an_edit() -> None:
    edit = parse_recentchange(make_edit())
    assert edit == Edit(
        wiki="enwiki",
        title="Ada Lovelace",
        namespace=0,
        user="Someone",
        bot=False,
        kind="edit",
        minor=True,
        old_len=100,
        new_len=130,
        rev_old=5,
        rev_new=6,
        comment="fix",
        at=datetime.fromtimestamp(1794098367, UTC),
        server_name="en.wikipedia.org",
    )
    assert isinstance(edit, Edit)
    assert edit.delta == 30


def test_parse_a_new_page_has_no_old_values_and_no_id_is_fine() -> None:
    raw = make_edit(type="new", length={"new": 8419}, revision={"new": 9})
    page = parse_recentchange(raw)
    assert isinstance(page, Edit)
    assert (page.kind, page.old_len, page.rev_old, page.delta) == ("new", None, None, 8419)
    assert '"id"' not in raw


def test_parse_a_move() -> None:
    raw = json.dumps(
        {
            "type": "log",
            "log_type": "move",
            "log_params": {"target": "New name"},
            "namespace": 0,
            "title": "Old name",
            "user": "U",
            "wiki": "ukwiki",
            "timestamp": 1794098395,
        }
    )
    assert parse_recentchange(raw) == PageMove(
        wiki="ukwiki",
        title="Old name",
        target="New name",
        namespace=0,
        user="U",
        at=datetime.fromtimestamp(1794098395, UTC),
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{not json",
        "[]",
        "null",
        '{"type": "categorize", "wiki": "enwiki"}',
        '{"type": "log", "log_type": "delete", "wiki": "enwiki"}',
        '{"type": "log", "log_type": "move", "log_params": [], "wiki": "enwiki", "title": "x"}',
        '{"type": "log", "log_type": "move", "log_params": {}, "wiki": "enwiki", "title": "x"}',
        '{"wiki": "enwiki"}',
        make_edit(title=5),
        make_edit(wiki=None),
        make_edit(user=None),
        make_edit(namespace="0"),
        make_edit(namespace=True),
        make_edit(timestamp="now"),
        make_edit(timestamp=1e30),
        make_edit(bot="no"),
        make_edit(length=[1, 2]),
        make_edit(length={"old": "x", "new": 1}),
    ],
)
def test_parse_gives_none_and_never_raises(raw: str) -> None:
    assert parse_recentchange(raw) is None


def test_parse_survives_absurdly_deep_json() -> None:
    assert parse_recentchange("[" * 100000) is None


# --- FeedStatus ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "detail", "retry_in", "message"),
    [
        ("ok", "", None, "Stream: ok"),
        ("connecting", "", None, "Stream: connecting"),
        ("reconnecting", "", 8.0, "Stream: reconnecting in 8s"),
        ("reconnecting", "", 7.2, "Stream: reconnecting in 8s"),
        ("rate_limited", "", 12.0, "Stream: rate limited, retrying in 12s"),
        ("bad_data", "3 events", None, "Stream: bad data skipped (3 events)"),
        ("down", "replay finished", None, "Stream: down (replay finished)"),
        ("down", "", None, "Stream: down"),
    ],
)
def test_feed_status_message(state: str, detail: str, retry_in: float | None, message: str) -> None:
    status = FeedStatus("stream", state, detail=detail, retry_in=retry_in, at=AT)  # type: ignore[arg-type]
    assert status.message == message


# --- the live source -------------------------------------------------------------------------------------------------


def sse_body(events: list[RecordedEvent]) -> bytes:
    return "".join(f"id: {e.id}\ndata: {e.data}\n\n" for e in events).encode()


async def take(source: DataSource, count: int) -> list[StreamItem]:
    out: list[StreamItem] = []
    items = source.items()
    try:
        async for item in items:
            out.append(item)
            if len(out) >= count:
                break
    finally:
        await items.aclose()  # type: ignore[attr-defined]
    return out


async def take_until_reconnecting(source: DataSource, reconnects: int = 1) -> list[StreamItem]:
    """Items up to and including the ``reconnects``-th reconnecting status (a live source never ends by itself)."""
    out: list[StreamItem] = []
    items = source.items()
    try:
        async for item in items:
            out.append(item)
            if isinstance(item, FeedStatus) and item.state == "reconnecting":
                reconnects -= 1
                if reconnects == 0:
                    break
    finally:
        await items.aclose()  # type: ignore[attr-defined]
    return out


def then_fail(*first: httpx.Response) -> object:
    """A respx side effect: these responses in order, then HTTP 500 for ever."""
    queue = list(first)

    def next_response(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if queue else httpx.Response(500)

    return next_response


def states(items: list[StreamItem]) -> list[str]:
    return [i.state if isinstance(i, FeedStatus) else type(i).__name__ for i in items]


def two_events(sample_path: Path) -> list[RecordedEvent]:
    events, _ = read_all(sample_path)
    return [e for e in events if isinstance(parse_recentchange(e.data), Edit)][:2]


@pytest.fixture
def live(client: Client, clock: VirtualClock) -> LiveSource:
    return LiveSource(client, clock)


@respx.mock
async def test_live_yields_connecting_ok_then_items_in_order(live: LiveSource, sample_path: Path) -> None:
    first, second = two_events(sample_path)
    respx.get(RECENTCHANGE_URL).mock(return_value=httpx.Response(200, content=sse_body([first, second])))
    items = await take(live, 4)
    assert states(items) == ["connecting", "ok", "Edit", "Edit"]
    assert isinstance(items[2], Edit) and items[2] == parse_recentchange(first.data)
    assert isinstance(items[3], Edit) and items[3] == parse_recentchange(second.data)
    assert all(i.at == AT for i in items if isinstance(i, FeedStatus))


@respx.mock
async def test_live_reconnects_with_last_event_id_and_backs_off(
    live: LiveSource, clock: VirtualClock, sample_path: Path
) -> None:
    first, second = two_events(sample_path)
    route = respx.get(RECENTCHANGE_URL).mock(
        side_effect=then_fail(
            httpx.Response(200, content=sse_body([first])),  # a drop after one event
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, content=sse_body([second])),
        )
    )
    items = await take_until_reconnecting(live, 4)
    assert states(items) == [
        "connecting",
        "ok",
        "Edit",
        "reconnecting",
        "reconnecting",
        "reconnecting",
        "ok",
        "Edit",
        "reconnecting",
    ]
    assert [i.retry_in for i in items if isinstance(i, FeedStatus) and i.state == "reconnecting"] == [1, 2, 4, 1]
    assert route.calls[0].request.headers.get("last-event-id") is None
    assert route.calls[1].request.headers["last-event-id"] == first.id
    assert route.calls[3].request.headers["last-event-id"] == first.id
    assert clock.monotonic() >= 1 + 2 + 4


@respx.mock
async def test_live_backoff_is_capped_at_60_and_resets_after_an_event(live: LiveSource, sample_path: Path) -> None:
    first, _ = two_events(sample_path)
    respx.get(RECENTCHANGE_URL).mock(
        side_effect=then_fail(*[httpx.Response(500)] * 8, httpx.Response(200, content=sse_body([first])))
    )
    items = await take_until_reconnecting(live, 11)
    waits = [i.retry_in for i in items if isinstance(i, FeedStatus) and i.state == "reconnecting"]
    assert waits == [1, 2, 4, 8, 16, 32, 60, 60, 1, 2, 4]


@respx.mock
async def test_live_rate_limited_waits_at_least_its_retry_after(live: LiveSource, clock: VirtualClock) -> None:
    respx.get(RECENTCHANGE_URL).mock(
        side_effect=then_fail(
            httpx.Response(429, headers={"Retry-After": "12"}),
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, content=b""),
        )
    )
    items = await take_until_reconnecting(live)
    limited = [i for i in items if isinstance(i, FeedStatus) and i.state == "rate_limited"]
    assert [i.retry_in for i in limited] == [12.0, 5.0]
    assert limited[0].message == "Stream: rate limited, retrying in 12s"
    assert clock.monotonic() >= 17.0


@respx.mock
async def test_live_reports_bad_data_once_a_minute_then_ok_again(live: LiveSource, sample_path: Path) -> None:
    first, second = two_events(sample_path)
    bad = RecordedEvent(0, "5", "{not json")
    body = sse_body([first, bad, bad, second, bad])
    respx.get(RECENTCHANGE_URL).mock(side_effect=then_fail(httpx.Response(200, content=body)))
    items = await take_until_reconnecting(live)
    assert states(items) == ["connecting", "ok", "Edit", "bad_data", "ok", "Edit", "reconnecting"]
    bad_status = items[3]
    assert isinstance(bad_status, FeedStatus)
    assert bad_status.message == "Stream: bad data skipped (1 event)"


@respx.mock
async def test_live_reports_bad_data_again_after_a_minute(
    live: LiveSource, clock: VirtualClock, sample_path: Path
) -> None:
    first, _ = two_events(sample_path)
    bad = RecordedEvent(0, "5", "{not json")
    respx.get(RECENTCHANGE_URL).mock(side_effect=then_fail(httpx.Response(200, content=sse_body([first, bad, bad]))))
    reports: list[str] = []
    async for item in live.items():
        if isinstance(item, FeedStatus):
            if item.state == "bad_data":
                reports.append(item.detail)
                clock.advance(61)  # a minute passes before the next bad event
            elif item.state == "reconnecting":
                break
    assert reports == ["1 event", "1 event"]


@respx.mock
async def test_live_ignores_events_it_does_not_use_without_calling_them_bad(live: LiveSource) -> None:
    categorize = json.dumps({"type": "categorize", "wiki": "enwiki"})
    body = f"id: 1\ndata: {categorize}\n\nid: 2\ndata: {make_edit()}\n\n".encode()
    respx.get(RECENTCHANGE_URL).mock(side_effect=then_fail(httpx.Response(200, content=body)))
    items = await take_until_reconnecting(live)
    assert states(items) == ["connecting", "ok", "Edit", "reconnecting"]


@respx.mock
async def test_live_hands_every_raw_event_to_the_recorder(
    client: Client, clock: VirtualClock, sample_path: Path, tmp_path: Path
) -> None:
    events, _ = read_all(sample_path)
    respx.get(RECENTCHANGE_URL).mock(side_effect=then_fail(httpx.Response(200, content=sse_body(events[:12]))))
    out = tmp_path / "rec" / "out.jsonl.gz"
    source = LiveSource(client, clock, recorder=Recorder(out, clock, None))
    await take_until_reconnecting(source)
    await source.aclose()
    recorded, skipped = read_all(out)
    assert skipped == 0
    assert [(e.id, e.data) for e in recorded] == [(e.id, e.data) for e in events[:12]]


async def test_live_url_is_defined_once() -> None:
    assert RECENTCHANGE_URL == "https://stream.wikimedia.org/v2/stream/recentchange"


# --- recording -------------------------------------------------------------------------------------------------------


def write_lines(path: Path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


HEADER = '{"format":"wikihose-recording","version":1,"recorded_at":"2026-11-07T12:00:00+00:00","stream":"recentchange","wiki":"all"}'


async def test_recorder_writes_a_v1_file_that_reads_back_identically(
    clock: VirtualClock, sample_path: Path, tmp_path: Path
) -> None:
    sample, _ = read_all(sample_path)
    out = tmp_path / "out.jsonl.gz"
    recorder = Recorder(out, clock, None)
    last = 0.0
    for event in sample:
        await clock.sleep(event.t - last)
        last = event.t
        recorder.write(SseEvent(event.id, "message", event.data))
    recorder.close()
    recorder.close()  # closing twice is harmless
    recorder.write(SseEvent("x", "message", "{}"))  # writing after close is ignored

    header, events = read_recording(out)
    again = list(events)
    assert header.stream == "recentchange" and header.wiki == "all"
    assert header.recorded_at == AT
    assert [(e.id, e.data) for e in again] == [(e.id, e.data) for e in sample]
    assert all(abs(a.t - b.t) < 0.002 for a, b in zip(again, sample, strict=True))
    with gzip.open(out, "rt", encoding="utf-8") as handle:
        first_line = handle.readline()
    assert json.loads(first_line) == {
        "format": "wikihose-recording",
        "version": 1,
        "recorded_at": "2026-11-07T12:00:00+00:00",
        "stream": "recentchange",
        "wiki": "all",
    }


async def test_recorder_filters_to_one_wiki(clock: VirtualClock, tmp_path: Path) -> None:
    out = tmp_path / "en.jsonl.gz"
    recorder = Recorder(out, clock, "enwiki")
    recorder.write(SseEvent("1", "message", make_edit(wiki="enwiki")))
    recorder.write(SseEvent("2", "message", make_edit(wiki="dewiki")))
    recorder.write(SseEvent("3", "message", "{not json"))
    recorder.close()
    header, events = read_recording(out)
    assert header.wiki == "enwiki"
    assert [e.id for e in events] == ["1"]


async def test_recorder_flushes_every_second_of_clock_time(clock: VirtualClock, tmp_path: Path) -> None:
    out = tmp_path / "flush.jsonl.gz"
    recorder = Recorder(out, clock, None)
    recorder.write(SseEvent("1", "message", "{}"))
    await clock.sleep(1.0)
    recorder.write(SseEvent("2", "message", "{}"))  # a second has passed: flushed to disk, file not yet closed
    events, _ = read_all(out)
    assert [e.id for e in events][:2] == ["1", "2"]
    recorder.close()


def test_read_recording_skips_and_counts_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl.gz"
    write_lines(
        path,
        [
            HEADER,
            '{"t": 0.5, "id": "1", "data": "{}"}',
            "garbage",
            "",
            '{"t": "late", "id": null, "data": "{}"}',
            '{"t": 1, "id": 7, "data": "{}"}',
            '{"t": 2, "data": "{}"}',
            '["not", "an", "object"]',
        ],
    )
    events, skipped = read_all(path)
    assert [(e.t, e.id) for e in events] == [(0.5, "1"), (2.0, None)]
    assert skipped == 5


@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["not json"],
        ['{"format": "something-else", "version": 1}'],
        ['{"format": "wikihose-recording", "version": 2, "recorded_at": "2026-11-07T12:00:00+00:00"}'],
        ['{"format": "wikihose-recording", "version": 1}'],
        ['{"format": "wikihose-recording", "version": 1, "recorded_at": "yesterday", "stream": "x", "wiki": "all"}'],
    ],
)
def test_read_recording_rejects_a_bad_header(tmp_path: Path, lines: list[str]) -> None:
    path = tmp_path / "bad.jsonl.gz"
    write_lines(path, lines) if lines else gzip.open(path, "wb").close()
    with pytest.raises(RecordingError):
        read_recording(path)


def test_read_recording_rejects_a_file_that_is_not_gzip(tmp_path: Path) -> None:
    path = tmp_path / "plain.jsonl.gz"
    path.write_text(HEADER, encoding="utf-8")
    with pytest.raises(RecordingError):
        read_recording(path)
    with pytest.raises(RecordingError):
        read_recording(tmp_path / "missing.jsonl.gz")


def test_read_recording_survives_a_file_cut_off_mid_write(sample_path: Path, tmp_path: Path) -> None:
    cut = tmp_path / "cut.jsonl.gz"
    data = sample_path.read_bytes()
    cut.write_bytes(data[: len(data) // 2])
    whole, _ = read_all(sample_path)
    events, skipped = read_all(cut)
    assert 0 < len(events) < len(whole)
    assert skipped >= 1


def test_a_recording_without_a_timezone_is_taken_as_utc(tmp_path: Path) -> None:
    path = tmp_path / "naive.jsonl.gz"
    write_lines(path, [HEADER.replace("+00:00", "")])
    header, _ = read_recording(path)
    assert header.recorded_at == AT


# --- the replay source -----------------------------------------------------------------------------------------------


def small_recording(tmp_path: Path, times: list[float]) -> Path:
    path = tmp_path / "small.jsonl.gz"
    write_lines(
        path,
        [HEADER] + [json.dumps({"t": t, "id": str(n), "data": make_edit(title=f"P{n}")}) for n, t in enumerate(times)],
    )
    return path


async def play(source: ReplaySource) -> list[StreamItem]:
    return [item async for item in source.items()]


async def test_replay_of_the_sample_matches_the_parser(clock: VirtualClock, sample_path: Path) -> None:
    sample, _ = read_all(sample_path)
    expected = [i for e in sample if (i := parse_recentchange(e.data)) is not None]
    items = await play(ReplaySource(sample_path, clock))
    assert states(items)[0] == "ok"
    assert [i for i in items if not isinstance(i, FeedStatus)] == expected
    last = items[-1]
    assert isinstance(last, FeedStatus) and (last.state, last.detail) == ("down", "replay finished")
    assert clock.monotonic() == pytest.approx(sample[-1].t, abs=1e-6)


async def test_replay_is_a_data_source(clock: VirtualClock, sample_path: Path) -> None:
    source: DataSource = ReplaySource(sample_path, clock)
    assert source.name == "replay" and source.clock is clock
    await source.aclose()


async def test_replay_waits_for_the_recorded_gaps_divided_by_speed(clock: VirtualClock, tmp_path: Path) -> None:
    path = small_recording(tmp_path, [0.0, 2.0, 5.0])
    source = ReplaySource(path, clock, speed=2.0)
    stamps = []
    async for item in source.items():
        if isinstance(item, Edit):
            stamps.append(clock.monotonic())
    assert stamps == pytest.approx([0.0, 1.0, 2.5])
    assert source.moment() == source.recorded_start + timedelta(seconds=5.0)


async def test_replay_moment_follows_recorded_time(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0, 3.0]), clock)
    assert source.recorded_start == AT
    assert source.moment() == AT
    seen = []
    async for _item in source.items():
        seen.append(source.moment())
    assert seen[-1] == AT + timedelta(seconds=3)


async def test_replay_speed_is_clamped(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0]), clock, speed=100.0)
    assert source.speed == 16.0
    source.speed = 0.01
    assert source.speed == 0.5
    source.speed = 4
    assert source.speed == 4.0


async def test_replay_changing_speed_takes_effect_mid_gap(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0, 10.0]), clock)
    count = 0
    async for item in source.items():
        if isinstance(item, Edit):
            count += 1
            if count == 1:
                source.speed = 4.0
    assert clock.monotonic() == pytest.approx(2.5)


async def test_replay_pause_and_resume(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0, 1.0, 2.0]), clock)
    got: list[StreamItem] = []

    async def consume() -> None:
        async for item in source.items():
            got.append(item)

    source.pause()
    assert source.paused
    task = asyncio.create_task(consume())
    for _ in range(20):
        await asyncio.sleep(0)
    assert [type(i).__name__ for i in got] == ["FeedStatus", "Edit"]  # the first event is at t=0; then it holds
    assert clock.monotonic() == 0.0
    source.resume()
    assert not source.paused
    await asyncio.wait_for(task, 5)
    assert len([i for i in got if isinstance(i, Edit)]) == 3
    assert clock.monotonic() == pytest.approx(2.0)


async def test_replay_skip_yields_the_skipped_items_without_waiting(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0, 10.0, 20.0, 100.0]), clock)
    source.skip(50)
    stamps = []
    async for item in source.items():
        if isinstance(item, Edit):
            stamps.append(clock.monotonic())
    assert len(stamps) == 4
    assert stamps[:3] == [0.0, 0.0, 0.0]
    assert clock.monotonic() == pytest.approx(50.0)
    assert source.moment() == AT + timedelta(seconds=100)


async def test_replay_aclose_ends_a_paused_replay(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(small_recording(tmp_path, [0.0, 5.0]), clock)
    source.pause()
    task = asyncio.create_task(play(source))
    for _ in range(10):
        await asyncio.sleep(0)
    await source.aclose()
    items = await asyncio.wait_for(task, 5)
    assert states(items)[0] == "ok"


async def test_replay_rejects_a_bad_file(clock: VirtualClock, tmp_path: Path) -> None:
    (tmp_path / "x.gz").write_bytes(b"nope")
    with pytest.raises(RecordingError):
        ReplaySource(tmp_path / "x.gz", clock)


# --- the sample ships with the package and holds nothing private -----------------------------------------------------


def test_the_sample_has_no_email_or_key_like_strings(sample_path: Path) -> None:
    with gzip.open(sample_path, "rt", encoding="utf-8") as handle:
        text = handle.read()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "email-like string"
    assert not re.search(r"(?i)api[_-]?key|secret|passw(or)?d|bearer |BEGIN [A-Z ]*PRIVATE", text)
    assert not re.search(r"[A-Fa-f0-9]{32,}", text), "hex key-like string"
    assert not re.search(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{32,}", text)
    assert not re.search(r"ghp_|github_pat_|sk-[A-Za-z0-9]|AKIA[A-Z0-9]{8}|xox[bp]-", text), "token prefix"
