# Deferred

One line per known gap, in the form `- Area: what is missing and what would fix it.` Anything bigger gets an issue.

- CI: no lowest-dependency job yet; adapt eyewall's ci.yml "lowest" job.
- CLI: no `--diagnose` (it would print `RequestBudget.stats()`, the paths and the feed health) and no `--offline` flag yet.
- Store: the pageview and baseline methods arrive with the features that use them; only `edit_minutes` has any.
- Watchlist: a page that is deleted and then recreated gets a new page ID and is not matched again; the old entry stays marked deleted until it is removed and the page is added afresh.
- Watchlist: the catch-up counts at most 200 unseen edits per page (4 history requests of 50) and looks back at most 30 days.
- Watchlist: only Wikipedia and Wiktionary editions can be watched; pages on Commons, Wikidata and other sites are refused with a message.
- Watchlist: a misspelt title is refused as "no page called ..."; there is no search fallback that suggests the right one.
- Watchlist: the Trend column is a placeholder until the sparkline widget is wired in (step 3 of #4).
- Watchlist: non-article titles are caught offline by a list of English namespace names, and the wiki's answer settles the rest; `NamespaceFilter` (localized names from siteinfo) is not used there yet.
- Watchlist: `d` and `m` mean different things on the Live tab (`d` shows the diff of the selected edit, `m` sets the minimum size) and on the Watchlist tab (`d` removes the page, `m` marks it seen); a consistent scheme needs a key review.
- Replay: `w` and `W` need a connection to look a page up, so in a replay they say so; the saved watchlist is copied read-only for live matching, but a replay cannot add pages.
- Replay: at 80x24 the footer cuts off the `+ - ]` keys; the help screen (`?`) lists them all.
- Replay: the saved watchlist is read as an immutable snapshot (no migration, no lock files), so a database written by an older or newer wikihose, or one being written at that moment, gives an empty list in a replay.
