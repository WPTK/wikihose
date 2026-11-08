# Deferred

One line per known gap, in the form `- Area: what is missing and what would fix it.` Anything bigger gets an issue.

- CI: no lowest-dependency job yet; adapt eyewall's ci.yml "lowest" job.
- CLI: no `--diagnose` (it would print `RequestBudget.stats()`, the paths and the feed health) and no `--offline` flag yet.
- Store: the baseline methods arrive with the features that use them; only `edit_minutes`, the watchlist and `pageviews` have any.
- Watchlist: a page that is deleted and then recreated gets a new page ID and is not matched again; the old entry stays marked deleted until it is removed and the page is added afresh.
- Watchlist: the catch-up counts at most 200 unseen edits per page (4 history requests of 50) and looks back at most 30 days.
- Watchlist: only Wikipedia and Wiktionary editions can be watched; pages on Commons, Wikidata and other sites are refused with a message.
- Watchlist: a misspelt title is refused as "no page called ..."; there is no search fallback that suggests the right one.
- Watchlist: non-article titles are caught offline by a list of English namespace names, and the wiki's answer settles the rest; `NamespaceFilter` (localized names from siteinfo) is not used there yet.
- Watchlist: `d` and `m` mean different things on the Live tab (`d` shows the diff of the selected edit, `m` sets the minimum size) and on the Watchlist tab (`d` removes the page, `m` marks it seen); a consistent scheme needs a key review.
- Replay: `w` and `W` need a connection to look a page up, so in a replay they say so; the saved watchlist is copied read-only for live matching, but a replay cannot add pages.
- Replay: the saved watchlist is read as an immutable snapshot (no migration, no lock files), so a database written by an older or newer wikihose, or one being written at that moment, gives an empty list in a replay.
- Watchlist: a page with no pageview data (404) is asked for again after a restart; only a page that has data is cached in `pageviews`.
- Packaging: Apple signing and notarization (an owner decision; it costs $99 a year for the Apple Developer Program). The macOS binaries are unsigned, so a browser download is quarantined and the README tells users to run `xattr -d com.apple.quarantine`. Signing would need a Developer ID Application certificate and an app-specific password held as repository secrets, and `codesign` plus `xcrun notarytool` steps in `build.yml`. Windows code signing is the same kind of decision.
- Packaging: macOS and Linux binaries are tar.gz archives, not `.pkg`, `.deb` or Homebrew packages; a Homebrew tap or `.deb` would need a decision on who maintains it.
- Packaging: the Linux binaries are built in `python:3.12-bullseye` (Debian 11, past its LTS end, OpenSSL 1.1.1) to keep the glibc 2.31 floor, and the binary bundles that OpenSSL. Moving to bookworm (glibc 2.36, OpenSSL 3) trades the floor for current TLS libraries; decide before 0.2.0 ships, and update the README and `docs/release.md` with it.
