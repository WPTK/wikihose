"""Fetch every live Wikimedia endpoint wikihose uses once and check that its shape is still what we expect.

    python scripts/spot_check_feeds.py [--delay SECONDS] [--wiki enwiki]

It reads the real services, so it is a format-drift check, not a test: run it before a release and from the daily
canary (.github/workflows/canary.yml). Exit status is 0 when everything is fine, 1 when a response has the wrong shape
(lines starting FAIL), and 2 when the only problems are outages: a request that failed, was throttled or came back
empty (lines starting DOWN). An outage is not a format change.

What is fetched, in order, one request at a time with the wikihose User-Agent and a pause between requests:

- the recentchange event stream (the first edit events and the SSE id line, which must be a JSON array)
- siteinfo (namespaces, aliases, main page), run through the namespace filter
- the newest daily top-articles list (1,000 rows) and one article's daily history
- the REST summary of the top article
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from wikihose.namespaces import NamespaceFilter

USER_AGENT = "wikihose-canary/0 (https://github.com/WPTK/wikihose) httpx/" + httpx.__version__
STREAM = "https://stream.wikimedia.org/v2/stream/recentchange"
EDIT_KEYS = {"meta", "type", "namespace", "title", "timestamp", "user", "bot", "wiki", "server_name"}


class Down(Exception):
    """The service did not answer usefully (network error, 429/5xx, empty body); reported as an outage."""


class Fail(Exception):
    """The service answered with a shape wikihose does not expect; reported as format drift."""


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.outages = 0

    def run(self, name: str, check: Callable[..., tuple[Any, str]], *args: Any) -> Any:
        try:
            result, note = check(*args)
        except Down as exc:
            self.outages += 1
            print(f"DOWN {name}: {exc}")
        except Fail as exc:
            self.failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # a crash in a check counts as a parse failure
            self.failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}: {note}")
            return result
        return None

    @property
    def code(self) -> int:
        return 1 if self.failures else 2 if self.outages else 0


def get_json(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> Any:
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise Down(f"{type(exc).__name__} for {url}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise Down(f"HTTP {response.status_code} for {url} (Retry-After: {response.headers.get('retry-after')})")
    if response.status_code != 200:
        raise Fail(f"HTTP {response.status_code} for {url}")
    try:
        return response.json()
    except ValueError as exc:
        raise Fail(f"not JSON: {url}") from exc


def check_stream(client: httpx.Client) -> tuple[None, str]:
    events = 0
    last_id = ""
    deadline = time.monotonic() + 40
    try:
        with client.stream("GET", STREAM, headers={"Accept": "text/event-stream"}) as response:
            if response.status_code == 429 or response.status_code >= 500:
                raise Down(f"HTTP {response.status_code} for {STREAM}")
            if response.status_code != 200:
                raise Fail(f"HTTP {response.status_code} for {STREAM}")
            for line in response.iter_lines():
                if line.startswith("id: "):
                    last_id = line[4:]
                elif line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event.get("type") in {"edit", "new"}:
                        missing = EDIT_KEYS - event.keys()
                        if missing or "length" not in event or "revision" not in event:
                            raise Fail(f"edit event lacks {sorted(missing) or 'length/revision'}")
                        events += 1
                if events >= 5 or time.monotonic() > deadline:
                    break
    except httpx.HTTPError as exc:
        raise Down(f"{type(exc).__name__} on the stream") from exc
    if events == 0:
        raise Down("no edit events arrived in 40 seconds")
    ids = json.loads(last_id) if last_id else None
    if not isinstance(ids, list) or not ids:
        raise Fail(f"SSE id line is not a JSON array: {last_id[:80]!r}")
    return None, f"{events} edit events, id line has {len(ids)} positions"


def wiki_host(wiki: str) -> str:
    """enwiki -> en.wikipedia.org (only the plain language Wikipedias are checked)."""
    if not wiki.endswith("wiki"):
        raise SystemExit(f"unsupported wiki {wiki!r}")
    return f"{wiki.removesuffix('wiki')}.wikipedia.org"


def check_siteinfo(client: httpx.Client, wiki: str) -> tuple[NamespaceFilter, str]:
    params = {
        "action": "query",
        "meta": "siteinfo",
        "siprop": "namespaces|namespacealiases|general",
        "format": "json",
        "formatversion": "1",
    }
    data = get_json(client, f"https://{wiki_host(wiki)}/w/api.php", params)
    namespaces = data.get("query", {}).get("namespaces")
    if not namespaces or len(namespaces) < 20:
        raise Fail("siteinfo has fewer than 20 namespaces")
    flt = NamespaceFilter.from_siteinfo(data)
    if flt.is_article("Special:Search") or not flt.is_article("Ramayana:_Part_1"):
        raise Fail("namespace filter misjudged Special:Search or Ramayana:_Part_1")
    return flt, f"{len(namespaces)} namespaces, {len(flt.prefixes)} prefixes, main page {flt.main_page!r}"


def check_top(client: httpx.Client, wiki: str, flt: NamespaceFilter | None) -> tuple[list[str], str]:
    project = wiki_host(wiki).removesuffix(".org")
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    data: Any = None
    for _ in range(3):  # the newest day's list can lag by some hours; fall back to older ones
        url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{project}/all-access/{day:%Y/%m/%d}"
        try:
            data = get_json(client, url)
            break
        except Fail as exc:
            if "HTTP 404" not in str(exc):
                raise
            day -= dt.timedelta(days=1)
            time.sleep(3)
    if data is None:
        raise Down("no daily top list found for the last three days")
    rows = data["items"][0]["articles"]
    if len(rows) < 900 or not {"article", "views", "rank"} <= rows[0].keys():
        raise Fail(f"top list has {len(rows)} rows or lacks article/views/rank")
    titles = [r["article"] for r in rows]
    kept = flt.articles(titles) if flt else titles
    if len(kept) < 300:
        raise Fail(f"only {len(kept)} of {len(titles)} top titles survive the namespace filter")
    return kept, f"{day}: {len(rows)} rows, {len(kept)} articles after the filter, #1 {rows[0]['article']}"


def check_history(client: httpx.Client, wiki: str, article: str) -> tuple[None, str]:
    project = wiki_host(wiki).removesuffix(".org")
    end = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=29)
    url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/"
        f"{quote(article, safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    )
    items = get_json(client, url).get("items", [])
    if len(items) < 25 or not {"timestamp", "views"} <= items[0].keys():
        raise Fail(f"{len(items)} history items for {article}, or lacks timestamp/views")
    return None, f"{article}: {len(items)} days"


def check_summary(client: httpx.Client, wiki: str, article: str) -> tuple[None, str]:
    data = get_json(client, f"https://{wiki_host(wiki)}/api/rest_v1/page/summary/{quote(article, safe='')}")
    if not data.get("extract") or not data.get("title"):
        raise Fail(f"summary for {article} lacks title/extract")
    return None, f"{data['title']}: {len(data['extract'])} characters"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--delay", type=float, default=3.0, help="seconds to wait between requests")
    parser.add_argument("--wiki", default="enwiki")
    args = parser.parse_args()
    report = Report()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        report.run("stream", check_stream, client)
        time.sleep(args.delay)
        flt = report.run("siteinfo", check_siteinfo, client, args.wiki)
        time.sleep(args.delay)
        titles = report.run("top articles", check_top, client, args.wiki, flt)
        if titles:
            time.sleep(args.delay)
            report.run("article history", check_history, client, args.wiki, titles[0])
            time.sleep(args.delay)
            report.run("summary", check_summary, client, args.wiki, titles[0])
    print({0: "all feeds fine", 1: "format drift", 2: "outage only"}[report.code])
    return report.code


if __name__ == "__main__":
    sys.exit(main())
