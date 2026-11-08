"""Fetching the REST compare payload and drawing it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx
from payloads import fixture

from wikihose.diff import (
    MAX_LINE_CHARS,
    DiffError,
    DiffHeader,
    DiffLine,
    RevisionDiff,
    Span,
    fetch_diff,
    parse_diff,
    render_diff,
)
from wikihose.net import Client
from wikihose.titles import diff_url


def load(name: str) -> RevisionDiff:
    return parse_diff("enwiki", 1, 2, fixture(f"rest/compare_{name}.json"))


def styles(diff: RevisionDiff, **kwargs: object) -> set[str]:
    return {str(span.style) for span in render_diff(diff, **kwargs).spans}  # type: ignore[arg-type]


# --- parsing ----------------------------------------------------------------------------------------------------------


def test_a_small_edit_is_one_added_line_between_context() -> None:
    diff = load("small_edit")
    assert [(line.kind, line.number) for line in diff.lines] == [
        ("context", 365),
        ("context", 366),
        ("added", 367),
        ("context", 368),
        ("context", 369),
    ]
    assert diff.lines[2].text == "[[Category:British computer scientists]]"


def test_a_changed_line_has_highlight_ranges_for_added_and_removed_words() -> None:
    diff = load("changed_line")
    (line,) = [item for item in diff.lines if item.kind == "changed"]
    assert line.text == "===Novels, plays and playspoetry==="
    assert [(span.added, line.text[span.start : span.end]) for span in line.spans] == [
        (True, ", plays"),
        (False, "plays"),
        (True, "poetry"),
    ]
    assert [item.kind for item in diff.lines].count("added") == 2


def test_highlight_offsets_are_utf8_bytes_so_text_after_a_wide_character_lines_up() -> None:
    diff = load("rewrite")
    spans = [(span.added, line.text[span.start : span.end]) for line in diff.lines for span in line.spans]
    assert (False, "father") in spans and (True, "husband") in spans and (False, "request") in spans
    assert any(len(line.text) != len(line.text.encode()) for line in diff.lines if line.spans)  # it does have wide text
    assert [item.kind for item in diff.lines].count("removed") == 4  # removed lines carry no line number
    assert all(item.number is None for item in diff.lines if item.kind == "removed")


def test_byte_offsets_are_converted_on_a_synthetic_line() -> None:
    payload = {"diff": [{"type": 3, "text": "café old new", "highlightRanges": [{"start": 6, "length": 3, "type": 1}]}]}
    (line,) = parse_diff("enwiki", 1, 2, payload).lines
    assert line.text[line.spans[0].start : line.spans[0].end] == "old"
    assert line.spans == (Span(5, 8, added=False),)


def test_moved_paragraphs_unknown_types_and_bad_entries() -> None:
    payload = {
        "diff": [
            {"type": 4, "text": "moved away", "offset": {"from": 1, "to": None}},
            {"type": 5, "lineNumber": 9, "text": "moved here"},
            {"type": 99, "lineNumber": 10, "text": "from the future"},
            {"type": 0, "lineNumber": True, "text": "odd number"},
            {
                "type": 3,
                "lineNumber": 11,
                "text": "x",
                "highlightRanges": [{"start": "a"}, {"start": 0, "length": 0, "type": 0}, 5],
            },
            {"type": 1},
            "junk",
        ]
    }
    diff = parse_diff("enwiki", 1, 2, payload)
    assert [(line.kind, line.number, line.spans) for line in diff.lines] == [
        ("moved_from", None, ()),
        ("moved_to", 9, ()),
        ("context", 10, ()),
        ("context", None, ()),
        ("changed", 11, ()),
    ]


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"diff": "no"}])
def test_a_payload_without_a_diff_list_is_an_error(payload: object) -> None:
    with pytest.raises(DiffError, match="could not read"):
        parse_diff("enwiki", 1, 2, payload)


def test_diff_url() -> None:
    assert diff_url("enwiki", 10, 20) == "https://en.wikipedia.org/w/index.php?diff=20&oldid=10"
    assert diff_url("be_x_oldwiki", 1, 2) == "https://be-x-old.wikipedia.org/w/index.php?diff=2&oldid=1"
    assert diff_url("commonswiki", 1, 2) is None


# --- drawing ----------------------------------------------------------------------------------------------------------


def test_added_lines_are_green_and_marked_with_a_plus() -> None:
    text = render_diff(load("small_edit"))
    lines = text.plain.splitlines()
    assert lines[2] == "+ [[Category:British computer scientists]]"
    assert lines[0].startswith("  [[Category:")
    green = [span for span in text.spans if span.style == "green"]
    assert text.plain[green[0].start : green[0].end].startswith("+ [[Category:British computer scientists")


def test_a_changed_line_highlights_added_text_green_and_removed_text_red() -> None:
    text = render_diff(load("changed_line"))
    marked = {
        text.plain[span.start : span.end]: str(span.style)
        for span in text.spans
        if str(span.style) in ("bold green", "bold red strike")
    }
    assert marked == {", plays": "bold green", "plays": "bold red strike", "poetry": "bold green"}
    assert "~ ===Novels, plays and playspoetry===" in text.plain


def test_removed_lines_are_red_with_a_minus() -> None:
    text = render_diff(load("rewrite"))
    removed = [line for line in text.plain.splitlines() if line.startswith("- ")]
    assert len(removed) == 4
    assert "red" in styles(load("rewrite"))


def test_a_gap_between_hunks_shows_an_ellipsis() -> None:
    diff = RevisionDiff(
        "enwiki", 1, 2, (DiffLine("context", 5, "a"), DiffLine("context", 6, "b"), DiffLine("added", 40, "c"))
    )
    assert render_diff(diff).plain == "  a\n  b\n  …\n+ c\n"


@pytest.mark.parametrize("name", ["small_edit", "changed_line", "rewrite"])
def test_no_style_is_orange_or_a_background(name: str) -> None:
    header = DiffHeader("Ada Lovelace", "Someone", "fix", datetime(2026, 11, 7, tzinfo=UTC), 10, 20)
    used = styles(load(name), header=header)
    assert used
    for style in used:
        assert "orange" not in style and " on " not in f" {style} " and "yellow" not in style
    assert used <= {"dim", "green", "red", "bold green", "bold red strike", "bold", "italic"}


def test_a_big_diff_is_cut_with_a_notice_and_a_link() -> None:
    lines = tuple(DiffLine("added", n, f"line {n}") for n in range(1, 11))
    text = render_diff(RevisionDiff("enwiki", 7, 9, lines), max_lines=3)
    assert text.plain.splitlines() == [
        "+ line 1",
        "+ line 2",
        "+ line 3",
        "… 7 more lines not shown. See all of it at https://en.wikipedia.org/w/index.php?diff=9&oldid=7",
    ]


def test_the_default_limit_keeps_a_huge_diff_small() -> None:
    lines = tuple(DiffLine("added", n, "x" * 10_000) for n in range(1, 2001))
    text = render_diff(RevisionDiff("enwiki", 7, 9, lines))
    assert len(text.plain) < 300 * (MAX_LINE_CHARS + 5) + 200
    assert "1700 more lines not shown" in text.plain


def test_an_empty_diff_says_so() -> None:
    assert render_diff(RevisionDiff("enwiki", 1, 2, ())).plain == "No changes to show.\n"


def test_the_heading_has_title_user_time_comment_and_sizes() -> None:
    header = DiffHeader(
        "Ada Lovelace",
        "Vitorperrut555",
        "Copying categories",
        datetime(2026, 11, 6, 19, 2, 23, tzinfo=UTC),
        size_before=98350,
        size_after=98391,
    )
    text = render_diff(load("small_edit"), header)
    assert text.plain.splitlines()[:5] == [
        "Ada Lovelace",
        "by Vitorperrut555 2026-11-06 19:02 UTC",
        "Copying categories",
        "98,350 to 98,391 bytes (+41)",
        "Revisions 1 to 2",
    ]
    eastern = render_diff(load("small_edit"), header, tz=timezone(timedelta(hours=-5), "EST"))
    assert "2026-11-06 14:02 EST" in eastern.plain


def test_a_minimal_heading() -> None:
    text = render_diff(RevisionDiff("enwiki", 1, 2, ()), DiffHeader("Page", size_after=5))
    assert text.plain.splitlines()[:3] == ["Page", "5 bytes", "Revisions 1 to 2"]


# --- fetching ---------------------------------------------------------------------------------------------------------

URL = "https://en.wikipedia.org/w/rest.php/v1/revision/1383337217/compare/1384127064"


@respx.mock
async def test_fetch_diff_gets_the_compare_endpoint(client: Client) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=fixture("rest/compare_small_edit.json")))
    diff = await fetch_diff(client, "enwiki", 1383337217, 1384127064)
    assert route.call_count == 1
    assert (diff.wiki, diff.from_id, diff.to_id) == ("enwiki", 1383337217, 1384127064)
    assert len(diff.lines) == 5


@respx.mock
async def test_fetch_diff_on_another_wiki_uses_its_host(client: Client) -> None:
    route = respx.get("https://de.wikipedia.org/w/rest.php/v1/revision/1/compare/2").mock(
        return_value=httpx.Response(200, json={"diff": []})
    )
    assert (await fetch_diff(client, "dewiki", 1, 2)).lines == ()
    assert route.called


@respx.mock
async def test_a_missing_revision_is_a_plain_error(client: Client) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(DiffError, match="could not be found"):
        await fetch_diff(client, "enwiki", 1383337217, 1384127064)


@respx.mock
async def test_fetch_failures_are_plain_errors(client: Client) -> None:
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(DiffError, match="could not be reached"):
        await fetch_diff(client, "enwiki", 1383337217, 1384127064)
    route.mock(return_value=httpx.Response(403))  # a suppressed revision
    with pytest.raises(DiffError, match="could not read"):
        await fetch_diff(client, "enwiki", 1383337217, 1384127064)
    route.mock(return_value=httpx.Response(200, json={"nothing": 1}))
    with pytest.raises(DiffError, match="could not read"):
        await fetch_diff(client, "enwiki", 1383337217, 1384127064)


async def test_fetch_diff_on_an_unknown_wiki_makes_no_request(client: Client) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(DiffError, match="cannot show changes on commonswiki"):
            await fetch_diff(client, "commonswiki", 1, 2)
        assert not router.calls
