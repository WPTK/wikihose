# Deferred

One line per known gap, in the form `- Area: what is missing and what would fix it.` Anything bigger gets an issue.

- CI: no lowest-dependency job yet; adapt eyewall's ci.yml "lowest" job.
- CLI: no `--diagnose` (it would print `RequestBudget.stats()`, the paths and the feed health) and no `--offline` flag yet.
- Store: the watchlist, pageview and baseline methods arrive with the features that use them; only `edit_minutes` has any.
