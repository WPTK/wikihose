"""Saved real API payloads from tests/fixtures/, and helpers to serve them through respx."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from wikihose.sources.events import Edit

FIXTURES = Path(__file__).parent / "fixtures"
ADA_ID = 974
ADA_LATEST = 1384127064  # the newest revision in the saved history
ADA_PREVIOUS = 1383337217
TURING_ID = 1208
TURING_LATEST = 1383889451


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def action_page(page_id: int, title: str, lastrevid: int, **extra: Any) -> dict[str, Any]:
    return {"pageid": page_id, "ns": 0, "title": title, "lastrevid": lastrevid, **extra}


def action_response(*pages: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": list(pages)}})


def make_edit(title: str = "Ada Lovelace", rev_new: int | None = ADA_LATEST + 1, wiki: str = "enwiki") -> Edit:
    return Edit(
        wiki=wiki,
        title=title,
        namespace=0,
        user="Someone",
        bot=False,
        kind="edit",
        minor=False,
        old_len=100,
        new_len=130,
        rev_old=1,
        rev_new=rev_new,
        comment="fix",
        at=datetime(2026, 11, 7, 12, 30, tzinfo=UTC),
        server_name="en.wikipedia.org",
    )
