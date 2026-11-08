"""Startup catch-up: what happened to the watched pages while wikihose was closed.

Two kinds of Action API request, both through the shared ``Client`` so the request budget applies:

1. ``prop=info&pageids=a|b|c`` for up to 50 pages of one wiki at once. It answers three questions: is the page still
   there (``missing``), was it renamed (``title`` differs from the stored one) and has it changed (``lastrevid`` is
   newer than the revision last seen).
2. ``prop=revisions&pageids=<one id>&rvlimit=50&rvend=<30 days ago>`` for each page that has changed. MediaWiki refuses
   ``rvlimit`` and ``rvend`` when several pages are asked for at once (error ``invalidparammix``), so the history
   cannot be batched; the cheap info check above keeps it to one request per changed page.

History reaches back at most 30 days and at most ``MAX_HISTORY_REQUESTS`` requests per page. Only revisions newer than
the last one seen count as unseen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from wikihose.clock import Clock
from wikihose.messages import describe_fetch_error
from wikihose.net import Client, FetchError
from wikihose.store import WatchedPage
from wikihose.titles import host_for_wiki
from wikihose.watchlist import Watchlist, api_url

LOOKBACK = timedelta(days=30)
INFO_BATCH = 50  # page IDs per request (the limit for ordinary users)
HISTORY_LIMIT = 50
MAX_HISTORY_REQUESTS = 4
_BASE = {"action": "query", "format": "json", "formatversion": "2"}
_HIDDEN = "(hidden)"
_UNREADABLE = "Wikipedia sent an answer wikihose could not read."
_MALFORMED = (KeyError, IndexError, TypeError, ValueError, AttributeError)


@dataclass(frozen=True, slots=True)
class CatchUpEdit:
    wiki: str
    page_id: int
    title: str
    rev_id: int
    parent_id: int
    at: datetime
    user: str
    comment: str
    size: int
    delta: int | None  # bytes added (positive) or removed; None when the earlier revision is out of range


@dataclass(slots=True)
class CatchUpResult:
    edits: list[CatchUpEdit] = field(default_factory=list)  # newest first within each page
    renamed: list[tuple[str, WatchedPage]] = field(default_factory=list)  # (old title, page under its new title)
    deleted: list[WatchedPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # plain-words messages for requests that failed


Progress = Callable[[int, int], None]  # (pages checked, pages in all)


async def catch_up(
    watchlist: Watchlist, client: Client, clock: Clock, progress: Progress | None = None
) -> CatchUpResult:
    """Check every watched page, update the watchlist and return what was found."""
    result = CatchUpResult()
    pages = watchlist.pages()
    done = 0
    if progress:
        progress(done, len(pages))
    cutoff = clock.now() - LOOKBACK
    for wiki in dict.fromkeys(page.wiki for page in pages):
        ofwiki = [page for page in pages if page.wiki == wiki]
        if host_for_wiki(wiki) is None:
            result.errors.append(f"wikihose cannot check pages on {wiki} yet.")
            done += len(ofwiki)
            if progress:
                progress(done, len(pages))
            continue
        for start in range(0, len(ofwiki), INFO_BATCH):
            batch = ofwiki[start : start + INFO_BATCH]
            try:
                infos = await _info(client, wiki, batch)
            except FetchError as exc:
                result.errors.append(describe_fetch_error(exc))
                infos = None
            for page in batch:
                if infos is not None:
                    try:
                        await _check_page(watchlist, client, cutoff, page, infos.get(page.page_id), result)
                    except FetchError as exc:
                        result.errors.append(f"{page.title}: {describe_fetch_error(exc)}")
                    except _MALFORMED:  # one odd answer must not stop the other pages
                        result.errors.append(f"{page.title}: {_UNREADABLE}")
                done += 1
                if progress:
                    progress(done, len(pages))
    return result


async def _info(client: Client, wiki: str, batch: list[WatchedPage]) -> dict[int, dict[str, Any]]:
    params = {**_BASE, "prop": "info", "pageids": "|".join(str(page.page_id) for page in batch)}
    data = await client.get_json(api_url(wiki), params)
    try:
        entries = data["query"]["pages"]
        return {int(entry["pageid"]): entry for entry in entries}
    except (KeyError, TypeError, ValueError):
        raise FetchError("bad_data", "page information was not in the expected shape") from None


async def _check_page(
    watchlist: Watchlist,
    client: Client,
    cutoff: datetime,
    page: WatchedPage,
    info: dict[str, Any] | None,
    result: CatchUpResult,
) -> None:
    """Bring one page up to date. The page may be removed or marked seen while a request is in flight, so it is read
    again from the watchlist after every wait; a removed page is left alone."""
    if info is None or info.get("missing"):
        if (now := watchlist.current(page)) is not None and not now.deleted:
            result.deleted.append(watchlist.set_deleted(now, True))
        return
    if (now := watchlist.current(page)) is None:
        return
    if now.deleted:
        now = watchlist.set_deleted(now, False)
    title = str(info.get("title", now.title))
    if title != now.title:
        old = now.title
        now = watchlist.rename(now, title)
        result.renamed.append((old, now))
    latest = int(info.get("lastrevid", 0))
    if latest <= now.last_rev_id:
        return  # nothing since the newest revision already known
    baseline = now.last_rev_id
    revisions = await _history(client, now, cutoff, baseline)
    if watchlist.current(now) is None:
        return
    edits = _edits(now, revisions, baseline)
    result.edits.extend(edits)
    watchlist.absorb(
        now,
        unseen=max(len(edits), 1),  # the page changed even if every change is older than the look-back
        last_rev_id=max(latest, edits[0].rev_id if edits else 0),
        last_edit_at=edits[0].at if edits else None,
    )


async def _history(client: Client, page: WatchedPage, cutoff: datetime, baseline: int) -> list[dict[str, Any]]:
    """Revisions of one page, newest first, back to the cutoff or the newest revision already known."""
    params: dict[str, Any] = {
        **_BASE,
        "prop": "revisions",
        "pageids": str(page.page_id),
        "rvprop": "ids|timestamp|user|comment|size",
        "rvlimit": str(HISTORY_LIMIT),
        "rvend": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    found: list[dict[str, Any]] = []
    for _ in range(MAX_HISTORY_REQUESTS):
        data = await client.get_json(api_url(page.wiki), params)
        try:
            found.extend(data["query"]["pages"][0].get("revisions", []))
            more = data.get("continue")
            if more and not isinstance(more, dict):
                raise TypeError("continue is not an object")
            oldest = found[-1]["revid"] if found else None
        except (KeyError, IndexError, TypeError, AttributeError):
            raise FetchError("bad_data", "revisions were not in the expected shape") from None
        if not more or (oldest is not None and oldest <= baseline):
            break
        params = {**params, **more}
    return found


def _edits(page: WatchedPage, revisions: list[dict[str, Any]], baseline: int) -> list[CatchUpEdit]:
    edits: list[CatchUpEdit] = []
    for index, revision in enumerate(revisions):
        if revision["revid"] <= baseline:
            break
        older = revisions[index + 1] if index + 1 < len(revisions) else None
        size = int(revision.get("size", 0))
        parent = int(revision.get("parentid", 0))
        delta = size if parent == 0 else (size - int(older["size"]) if older and older["revid"] == parent else None)
        edits.append(
            CatchUpEdit(
                wiki=page.wiki,
                page_id=page.page_id,
                title=page.title,
                rev_id=int(revision["revid"]),
                parent_id=parent,
                at=datetime.fromisoformat(revision["timestamp"]),
                user=_HIDDEN if revision.get("userhidden") else str(revision.get("user", "")),
                comment=_HIDDEN if revision.get("commenthidden") else str(revision.get("comment", "")),
                size=size,
                delta=delta,
            )
        )
    return edits
