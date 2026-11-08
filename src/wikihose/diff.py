"""Fetching and drawing the difference between two revisions.

The data is the REST compare endpoint, ``/w/rest.php/v1/revision/{from}/compare/{to}`` (see docs/data-sources.md). Its
``diff`` list holds one entry per line with a ``type``: 0 unchanged context, 1 added, 2 removed, 3 a changed line, 4 and
5 the two ends of a moved paragraph. A changed line carries the old and new words merged into one ``text`` with
``highlightRanges`` marking each stretch as added (0) or removed (1); the offsets count UTF-8 bytes, not characters.

``parse_diff`` turns the payload into plain data, ``render_diff`` into a Rich ``Text`` for a Textual widget. Added text
is green, removed text is red and each line starts with a marker (``+``, ``-``, ``~``) so the meaning does not depend on
colour. Orange is not used; see docs/design.md, "Colors".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal

from rich.text import Text

from wikihose.messages import describe_fetch_error
from wikihose.net import Client, FetchError
from wikihose.titles import host_for_wiki

MAX_LINES = 300  # lines shown; a bigger diff ends with a notice and a link
MAX_LINE_CHARS = 1500

type Kind = Literal["context", "added", "removed", "changed", "moved_from", "moved_to"]

_KINDS: dict[int, Kind] = {0: "context", 1: "added", 2: "removed", 3: "changed", 4: "moved_from", 5: "moved_to"}
_MARKERS: dict[Kind, str] = {
    "context": "  ",
    "added": "+ ",
    "removed": "- ",
    "changed": "~ ",
    "moved_from": "< ",
    "moved_to": "> ",
}
_LINE_STYLES: dict[Kind, str] = {
    "context": "dim",
    "added": "green",
    "removed": "red",
    "changed": "",
    "moved_from": "red italic",
    "moved_to": "green italic",
}
ADDED_STYLE = "bold green"
REMOVED_STYLE = "bold red strike"


class DiffError(Exception):
    """The difference could not be shown; ``str(error)`` says why in plain words."""


@dataclass(frozen=True, slots=True)
class Span:
    start: int  # character offsets into the line's text
    end: int
    added: bool  # False: removed


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: Kind
    number: int | None
    text: str
    spans: tuple[Span, ...] = ()


@dataclass(frozen=True, slots=True)
class RevisionDiff:
    wiki: str
    from_id: int
    to_id: int
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class DiffHeader:
    """What the heading above a diff says about the change. It comes from the edit, not from the compare payload."""

    title: str
    user: str = ""
    comment: str = ""
    at: datetime | None = None
    size_before: int | None = None
    size_after: int | None = None


def diff_url(wiki: str, from_id: int, to_id: int) -> str | None:
    """The page on the wiki that shows this difference, or None for a wiki without a known host."""
    host = host_for_wiki(wiki)
    return None if host is None else f"https://{host}/w/index.php?diff={to_id}&oldid={from_id}"


async def fetch_diff(client: Client, wiki: str, from_id: int, to_id: int) -> RevisionDiff:
    host = host_for_wiki(wiki)
    if host is None:
        raise DiffError(f"wikihose cannot show changes on {wiki} yet.")
    try:
        data = await client.get_json(f"https://{host}/w/rest.php/v1/revision/{from_id}/compare/{to_id}")
    except FetchError as exc:
        raise DiffError(describe_fetch_error(exc)) from exc
    if data is None:
        raise DiffError("That change could not be found. The page or the revision may have been deleted.")
    return parse_diff(wiki, from_id, to_id, data)


def parse_diff(wiki: str, from_id: int, to_id: int, payload: Any) -> RevisionDiff:
    entries = payload.get("diff") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise DiffError("Wikipedia sent a change that wikihose could not read.")
    lines: list[DiffLine] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        number = entry.get("lineNumber")
        text = entry["text"]
        lines.append(
            DiffLine(
                kind=_KINDS.get(_int(entry.get("type")), "context"),
                number=number if _int(number) >= 0 else None,
                text=text,
                spans=_spans(text, entry.get("highlightRanges")),
            )
        )
    return RevisionDiff(wiki, from_id, to_id, tuple(lines))


def _spans(text: str, ranges: Any) -> tuple[Span, ...]:
    """Highlight ranges counted in UTF-8 bytes, as spans counted in characters."""
    if not isinstance(ranges, list):
        return ()
    raw = text.encode()
    spans: list[Span] = []
    for item in ranges:
        try:
            start, length, kind = int(item["start"]), int(item["length"]), int(item["type"])
        except (KeyError, TypeError, ValueError):
            continue
        if length <= 0 or start < 0 or kind not in (0, 1):
            continue
        first = len(raw[:start].decode(errors="ignore"))
        last = len(raw[: start + length].decode(errors="ignore"))
        if last > first:
            spans.append(Span(first, last, added=kind == 0))
    return tuple(spans)


def render_diff(
    diff: RevisionDiff, header: DiffHeader | None = None, *, max_lines: int = MAX_LINES, tz: tzinfo = UTC
) -> Text:
    """The heading (when given) and the lines of the diff as one ``Text``. Gaps between hunks show as an ellipsis."""
    out = Text()
    if header is not None:
        out.append_text(_heading(header, diff, tz))
        out.append("\n")
    shown = diff.lines[:max_lines]
    previous: int | None = None
    for line in shown:
        if line.number is not None:
            if previous is not None and line.number > previous + 1:
                out.append("  …\n", style="dim")
            previous = line.number
        out.append_text(_line(line))
        out.append("\n")
    if not diff.lines:
        out.append("No changes to show.\n", style="dim")
    hidden = len(diff.lines) - len(shown)
    if hidden > 0:
        url = diff_url(diff.wiki, diff.from_id, diff.to_id)
        where = f" See all of it at {url}" if url else ""
        out.append(f"… {hidden} more lines not shown.{where}\n", style="dim")
    return out


def _line(line: DiffLine) -> Text:
    body = Text(line.text.replace("\t", "    "), style=_LINE_STYLES[line.kind])
    if line.kind == "changed" and "\t" not in line.text:
        for span in line.spans:
            body.stylize(ADDED_STYLE if span.added else REMOVED_STYLE, span.start, span.end)
    body.truncate(MAX_LINE_CHARS, overflow="ellipsis")
    return Text(_MARKERS[line.kind], style=_LINE_STYLES[line.kind]) + body


def _heading(header: DiffHeader, diff: RevisionDiff, tz: tzinfo) -> Text:
    out = Text(header.title, style="bold")
    who = f"by {header.user}" if header.user else ""
    when = header.at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if header.at else ""
    if who or when:
        out.append("\n" + " ".join(part for part in (who, when) if part), style="dim")
    if header.comment:
        out.append("\n" + header.comment, style="italic")
    if header.size_after is not None:
        size = f"{header.size_after:,} bytes"
        if header.size_before is not None:
            size = f"{header.size_before:,} to {size} ({header.size_after - header.size_before:+,})"
        out.append("\n" + size, style="dim")
    out.append(f"\nRevisions {diff.from_id} to {diff.to_id}", style="dim")
    return out


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1
