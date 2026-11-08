"""The parsed event types every data source yields, and the recentchange parser.

``parse_recentchange`` turns the data of one recentchange server-sent event into an ``Edit`` or a ``PageMove``. It
never raises: bad JSON, missing fields, wrong types and event kinds the app ignores (``categorize`` and other log
types) all give None. ``parse_event`` also says whether the event was malformed, so a source can count bad data
separately from events it simply does not use.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import KW_ONLY, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from wikihose.titles import valid_host

type FeedState = Literal["ok", "connecting", "reconnecting", "rate_limited", "bad_data", "down"]


@dataclass(frozen=True, slots=True)
class Edit:
    wiki: str
    title: str
    namespace: int
    user: str
    bot: bool
    kind: Literal["edit", "new"]
    minor: bool
    old_len: int | None
    new_len: int | None
    rev_old: int | None
    rev_new: int | None
    comment: str
    at: datetime
    server_name: str

    @property
    def delta(self) -> int:
        """Bytes added (positive) or removed (negative)."""
        return (self.new_len or 0) - (self.old_len or 0)


@dataclass(frozen=True, slots=True)
class PageMove:
    wiki: str
    title: str
    target: str
    namespace: int
    user: str
    at: datetime


@dataclass(frozen=True, slots=True)
class FeedStatus:
    feed: str  # "stream" for now
    state: FeedState
    _: KW_ONLY
    detail: str = ""
    retry_in: float | None = None
    at: datetime

    @property
    def message(self) -> str:
        """The status in plain words, for the feed-health line."""
        name = self.feed.capitalize()
        wait = "" if self.retry_in is None else f" in {math.ceil(self.retry_in)}s"
        match self.state:
            case "ok":
                return f"{name}: ok"
            case "connecting":
                return f"{name}: connecting"
            case "reconnecting":
                return f"{name}: reconnecting{wait}"
            case "rate_limited":
                return f"{name}: rate limited, retrying{wait}"
            case "bad_data":
                return f"{name}: bad data skipped" + (f" ({self.detail})" if self.detail else "")
            case "down":
                return f"{name}: down" + (f" ({self.detail})" if self.detail else "")


type StreamItem = Edit | PageMove | FeedStatus


class _Malformed(Exception):
    pass


def _int(value: Any, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Malformed
    return value


# Text from the stream is written by strangers and ends up on a terminal and in a database, so it is cleaned once,
# here. These become U+FFFD: control characters (which could start an escape sequence), line and paragraph separators,
# and lone surrogates (which sqlite and stdout cannot encode). A tab becomes a space. These are dropped: the
# characters that reorder or hide text (bidirectional marks, overrides and isolates, zero-width spaces, the word
# joiner, the byte order mark, the deprecated format controls and the interlinear annotation marks).
# U+200D and U+200C stay, since many scripts and emoji need them.
_REPLACE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_DROP = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c\u200b\u2060\ufeff\u206a-\u206f\ufff9-\ufffb]")


def clean_text(text: str) -> str:
    """``text`` with control and bidirectional characters removed or replaced, as described above."""
    return _REPLACE.sub("\ufffd", _DROP.sub("", text.replace("\t", " ")))


def _str(value: Any, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise _Malformed
    return clean_text(value)


def _host(value: Any) -> str:
    """The event's server_name if it is a plain Wikimedia host, else an empty string (no link will be shown)."""
    host = value if isinstance(value, str) else ""
    return host if valid_host(host) else ""


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _Malformed
    return value


def _when(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Malformed
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise _Malformed from exc


def _pair(block: Any, key: str) -> int | None:
    if block is None:
        return None
    if not isinstance(block, dict):
        raise _Malformed
    return _int(block.get(key), optional=True)


def _edit(data: dict[str, Any], kind: Literal["edit", "new"]) -> Edit:
    return Edit(
        wiki=_str(data.get("wiki")),
        title=_str(data.get("title")),
        namespace=_int(data.get("namespace")) or 0,
        user=_str(data.get("user")),
        bot=_bool(data.get("bot"), False),
        kind=kind,
        minor=_bool(data.get("minor"), False),
        old_len=_pair(data.get("length"), "old"),
        new_len=_pair(data.get("length"), "new"),
        rev_old=_pair(data.get("revision"), "old"),
        rev_new=_pair(data.get("revision"), "new"),
        comment=_str(data.get("comment"), ""),
        at=_when(data.get("timestamp")),
        server_name=_host(data.get("server_name")),
    )


def _move(data: dict[str, Any]) -> PageMove:
    params = data.get("log_params")
    if not isinstance(params, dict):
        raise _Malformed
    return PageMove(
        wiki=_str(data.get("wiki")),
        title=_str(data.get("title")),
        target=_str(params.get("target")),
        namespace=_int(data.get("namespace")) or 0,
        user=_str(data.get("user")),
        at=_when(data.get("timestamp")),
    )


def is_canary(raw: str) -> bool:
    """True for one of Wikimedia's canary events (see ``parse_event``)."""
    if '"canary"' not in raw:  # cheap test first; almost no event is a canary
        return False
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return False
    meta = data.get("meta") if isinstance(data, dict) else None
    return isinstance(meta, dict) and meta.get("domain") == "canary"


def parse_event(raw: str) -> tuple[Edit | PageMove | None, bool]:
    """Parse one event. Returns ``(item, well_formed)``; ``item`` is None for events the app does not use."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise _Malformed
        meta = data.get("meta")
        # Canary events are artificial events Wikimedia mixes into the public streams to tell a broken stream from an
        # empty one. Its EventStreams page (wikitech.wikimedia.org/wiki/Event_Platform/EventStreams, "Canary Events")
        # says to discard every event whose meta.domain is "canary".
        if isinstance(meta, dict) and meta.get("domain") == "canary":
            return None, True
        kind = data.get("type")
        if not isinstance(kind, str):
            raise _Malformed
        if kind == "edit" or kind == "new":
            return _edit(data, "edit" if kind == "edit" else "new"), True
        if kind == "log" and data.get("log_type") == "move":
            return _move(data), True
        return None, True
    except (ValueError, _Malformed, RecursionError):
        return None, False


def parse_recentchange(raw: str) -> Edit | PageMove | None:
    """The event in ``raw`` (the SSE data), or None when it is malformed or not one the app uses."""
    return parse_event(raw)[0]
