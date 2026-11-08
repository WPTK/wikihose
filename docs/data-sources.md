# Data sources

Checked live on 7 Nov 2026 (re-verified that day with slow requests). Wikimedia changes things; the daily feed canary
(Session 2) exists to catch drift.

## Live streams (SSE, no key)

### `https://stream.wikimedia.org/v2/stream/recentchange`

- About 300 events in 8 seconds across all wikis: roughly 57% `edit`, 34% `categorize`, 7% `log`, 2% `new`. About 55%
  of events had `bot: true` (counting categorize noise). About 13% came from `enwiki`.
- No server-side filter. The client filters.
- Every event has: `$schema`, `meta`, `type`, `namespace`, `title` (spaces, not underscores), `title_url`, `comment`,
  `timestamp`, `user`, `bot`, `server_url`, `server_name`, `wiki`, `parsedcomment`.
- Edit and new events add `length.old/new`, `revision.old/new` (`old` is absent on page creation) and `minor`.
- **There is no page ID.** A page is identified by `wiki` plus `title`.
- `timestamp` is unix seconds. The ISO time is `meta.dt`. `wiki` is a database name (for example `enwiki`,
  `be_x_oldwiki`), not a hostname; `server_name` is the hostname.
- The top-level `id` is the rc_id and is sometimes missing. Do not key on it.
- Each SSE `id:` line is a JSON array of Kafka positions, for example
  `[{"topic":"eqiad.mediawiki.recentchange","partition":0,"offset":-1},{"topic":"codfw.mediawiki.recentchange","partition":0,"timestamp":1794096548824}]`.
  Send the whole string back as `Last-Event-ID` to resume after a drop.
- A `since` query parameter (ISO timestamp) is accepted and replays from about that point.

### `https://stream.wikimedia.org/v2/stream/mediawiki.page_change.v1`

- About 100 events in 5 seconds across all wikis (edits and creates).
- Keys: `page.page_id`, `page.page_title` (underscores), `page.namespace_id`, `page_change_kind` (edit, create, move,
  delete and others), `performer.is_bot`, `revision.rev_id`, `revision.rev_size`, `prior_state.revision.rev_size`
  (missing on creates), `wiki_id`, `dt`, `changelog_kind`.
- A page ID survives renames, which matters for the watchlist.
- Same `id:` format as above.

### One connection for both

`https://stream.wikimedia.org/v2/stream/recentchange,mediawiki.page_change.v1` returned 200, and both kinds of event
arrived on the one connection (about 207 and 114 in 6 seconds). Tell them apart by `$schema`, or by the presence of
`page_change_kind`.

### Decision (Session 1, 7 Nov 2026)

The app opens one connection to `recentchange` only. In a two-minute recording of the combined connection (7,398
events), `mediawiki.page_change.v1` was 57% of the bytes for data recentchange already carries.

- Live reads edit and new events.
- The Watchlist gets each page's ID from the Action API (`prop=info`) when it is added, and matches live edits by
  (wiki, normalized title).
- It follows renames through recentchange `log` events with `log_type: move` (`log_params.target` holds the new title),
  and catches up by page ID through the Action API.
- If that proves unreliable, adding `mediawiki.page_change.v1` to the same connection is a one-line change to the
  stream URL.

## Pageviews

- **Daily top articles:** `https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{project}/all-access/{yyyy}/{mm}/{dd}`.
  - 1,000 rows per day. Rank 1,000 on 5 Nov had 8,489 views, and on 6 Nov 8,320.
  - Includes non-articles: `Main_Page`, `Special:Search`, and `Wikipedia:`, `Portal:`, `File:` and `Help:` pages. Filter
    with the wiki's namespace names and aliases from the Action API (`meta=siteinfo&siprop=namespaces|namespacealiases`),
    cached. Do not drop every title with a colon: `Avengers:_Doomsday` and `Ramayana:_Part_1` are articles.
  - The top list is not the all-agents count. For `Abdul_El-Sayed` on 5 Nov, the top list said 808,705 and per-article
    `all-agents` said 842,132. Use `agent=user` for baselines and confirm it matches the top list before building the
    score.
  - A day's list appears some hours after that UTC day ends. On 7 Nov late in the UTC day, 6 Nov was available and 7 Nov
    returned 404. Fall back to the newest day that exists.
  - Responses carry `cache-control: s-maxage=14400` and are served from a CDN cache.
- **Per-article history:** `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/{article}/daily/{start}/{end}`
  (the `user` agent variant and `all-agents` both return 200).

## Page content

- **Summary:** `https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}` returned 200.
- **Diff:** `https://{lang}.wikipedia.org/w/rest.php/v1/revision/{from}/compare/{to}` returned 200 for real revision IDs
  (404 for non-existent ones). It gives JSON (sections plus a line diff), which is easier to draw in a terminal than
  `action=compare` HTML.
- **Catch-up and page IDs:** the Action API at `https://{lang}.wikipedia.org/w/api.php` (`prop=revisions` since a
  timestamp, `prop=info` for page IDs). Siteinfo returned 30 namespaces and 5 aliases for enwiki; each namespace has `id`,
  `case`, `content` and the name.

### Action API calls the watchlist makes

Captured on 7 Nov 2026 (`tests/fixtures/actionapi/`, `tests/fixtures/rest/`). All use `format=json&formatversion=2`.

- **Page ID on add:** `action=query&prop=info&titles=<title>&redirects=1`. An existing page has `pageid`, `ns`, `title`
  and `lastrevid`. A redirect is resolved: the answer is the target page and `query.redirects` lists `{from, to}`
  (`Augusta Ada King` gives Ada Lovelace, page 974). A missing page has `missing: true` and no `pageid`. Only `ns` 0 is
  watched.
- **Catch-up, step 1:** `prop=info&pageids=a|b|c` for up to 50 IDs. A deleted or unknown ID comes back as
  `{pageid, missing: true}`, a renamed page has its new `title`, and `lastrevid` says whether anything changed.
- **Catch-up, step 2:** `prop=revisions&pageids=<one id>&rvprop=ids|timestamp|user|comment|size&rvlimit=50&rvend=<30 days
  ago>`, newest first, with `continue.rvcontinue` when more are left. **`rvlimit` and `rvend` cannot be used with
  several pages**: MediaWiki answers `invalidparammix` ("may only be used on a single page"). Asking for several pages
  without them returns only each page's newest revision. So history is one request per changed page, and the step 1
  check keeps unchanged pages out of it. Hidden users and comments arrive as `userhidden` and `commenthidden`.
- **Diff:** the REST compare payload has `from` and `to` (revision `id`, `slot_role`, `sections`) and `diff`, a list of
  `{type, lineNumber, text, offset, highlightRanges?}`. `type` is 0 context, 1 added, 2 removed (removed lines can lack
  `lineNumber`), 3 a changed line. For type 3, `text` holds the old and new words merged, and `highlightRanges` marks
  each stretch `{start, length, type}` with type 0 added and 1 removed. **Offsets count UTF-8 bytes**, not characters:
  after a non-ASCII character the character offsets drift (checked on a real payload). Types 4 and 5 (moved paragraphs)
  did not occur in the payloads captured; the code accepts them. The payload has no user, comment or timestamp; those
  come from the edit.

## Rate limits (new in 2026)

From mediawiki.org, "Wikimedia APIs/Rate limits" and "Access policy":

- Unauthenticated requests with a compliant User-Agent get 200 per minute. Requests with no identifying User-Agent get
  10 per minute. At most 3 requests may be in flight.
- On 429 or 503, honor `Retry-After`. Without one, wait at least 5 seconds or back off exponentially. The REST calls we
  made returned no `Retry-After` or `x-ratelimit-*` headers, so a 429 is the only throttle signal.
- User-Agent format: `<client>/<version> (<contact>) <library>/<version>`, where the contact is a URL or an email. Use the
  repo URL by default, plus an optional `contact` value from the config file (the earthwave pattern).
- Shared cloud egress IPs can get 429s at about one request per second.

What the app does:

- One request budget for the whole app, well under 200 a minute, with at most 3 requests in flight.
- Cache everything that can be cached.
- Feed health shows "rate limited, retrying in Ns".
- Never retry in a tight loop.
- The SSE connection reconnects with back-off and `Last-Event-ID`.
- When capturing live data for fixtures or tests, wait 3 seconds between requests.

## Reading baselines

1,000 per-article calls at 200 a minute take 5 minutes, every day, so the app bounds baselines cheaply first and fetches
exact history only for a short list. Tested on 7 Nov 2026 against enwiki: 31 daily top lists (3 seconds apart) and exact
`agent=user` history for the 12 highest candidates.

1. Take the last 30 daily top lists (30 requests, cached; past days never change, so after the first run only the newest
   day is new). An article missing from a day's list had fewer views than that day's rank-1,000 count, which ranged from
   7,390 to 10,002 over the month. Treat a missing day as "at most that floor".
2. Score every article in today's list with a bounded baseline: the median of its listed days, with missing days counted
   at the floor itself, so the baseline is never below the truth and the score is a lower bound. 
   Rank by today divided by that baseline.
3. Fetch exact history for the top 50 candidates (50 requests, about a quarter of one minute's budget) and replace the
   bound with the true 30-day median. Cache the series in SQLite; yesterday's value is final, so later runs fetch only
   the new days.

What the test showed:

- Only 152 of 1,000 articles appear on all 30 daily lists. The rest are missing on some days, and the ones that spike are
  mostly missing on nearly all of them (Stephen Miller: 26 of 30 days missing).
- The bound is a **lower bound** on the spike. Where an article is listed on most days the estimate is within 2% of the
  true median (Russell Brand 12,909 vs 13,175; Ramayana: Part 1 32,740 vs 32,193). Where it is missing, the true median
  is far below the floor: Michael Locke 32 (test estimate 3,876), Kimmarie Johnson 9 (3,876), Joe Fagnano 279 (3,906). The
  test counted a missing day at half the floor; the app counts it at the full floor, which makes the baseline an upper
  bound and the score a true lower bound (half the floor could rank a ~7,000-view page too high). Step 3 shows the real
  number.
- All 12 top candidates had `today` in the top list equal to the exact `agent=user` figure, so the top list can be used
  as today's value.
- **Per-article history leaves out days with no data.** Two of the 12 returned 31 items for a 32-day window. Align the
  series by `timestamp`, never by position, and fill missing days with zero.
- Per-article history for a 30-day window returned 29 to 30 items for a steady article (the newest day can be absent).

Spike (owner's choice, 7 Nov 2026): today's views divided by the 30-day median, shown only for pages with at least 5,000
views today. The Reading tab shows 25 rows by default.
