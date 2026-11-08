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
