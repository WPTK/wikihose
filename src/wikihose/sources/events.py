"""The parsed event types every data source yields, and the recentchange parser.

``parse_recentchange`` turns the data of one recentchange server-sent event into an ``Edit`` or a ``PageMove``. It
never raises: bad JSON, missing fields, wrong types and event kinds the app ignores (``categorize`` and other log
types) all give None. ``parse_event`` also says whether the event was malformed, so a source can count bad data
separately from events it simply does not use.
"""

from __future__ import annotations

import json
import math
from dataclasses import KW_ONLY, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

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


def _str(value: Any, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise _Malformed
    return value


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
        server_name=_str(data.get("server_name"), ""),
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


def parse_event(raw: str) -> tuple[Edit | PageMove | None, bool]:
    """Parse one event. Returns ``(item, well_formed)``; ``item`` is None for events the app does not use."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise _Malformed
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
