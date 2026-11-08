# User acceptance testing

The framework for the full UAT run that gates a release. The person running the release runs it on the release candidate, using the built
exe, and fills in the results at the bottom. A row or step marked `TODO` belongs to a tab that has not merged yet; the
person running the UAT replaces it when the tab lands. The target behaviour comes from [design.md](design.md).

## Severity

| Level | Meaning | Example |
| --- | --- | --- |
| P0 | The app crashes, hangs, corrupts data or breaks the terminal. | A traceback on start; the terminal left unusable after `q`. |
| P1 | A headline feature does not work, or a README claim is false. | The Live log never updates; `w` does not add to the Watchlist. |
| P2 | A feature works but is wrong or awkward in a way a user would notice. | A key missing from the footer; a layout that scrolls sideways at 80x24. |
| P3 | Cosmetic or minor. | A one-character misalignment; a wording nit. |

Orange used as a primary color or a background ([design.md](design.md), "Colors")
is a P2 at least, and P1 if it is on a main screen.

## Exit criteria

A release candidate passes when all of these hold:

- 0 open P0 and 0 open P1.
- Every README claim in the table below has a test or a walkthrough step that passed on the built exe.
- P2 and P3 findings are filed as issues, or as lines in [deferred.md](deferred.md), with the release notes naming any
  that a user would meet.
- `--version` and `--selftest` pass on the exe and from PyPI ([smoke-test.md](smoke-test.md)); the wheel and sdist are checked by `build.yml`.
- The state matrix has no empty cell for a tab that exists.

## How to run it

Automated first, then by hand:

```powershell
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m pytest -m uat
```

`-m uat` runs the tests that drive the whole app (they are left out of the default run; exit code 5, "no tests collected", is not a failure until the first `uat` test exists). Then the real-input run on the
built exe (`tui-test`, keys and mouse on the actual `wikihose.exe`), then the walkthroughs below by hand in Windows
Terminal. Test sizes: 80x24 (the floor), 120x30, 160x48 and 240x60. File each finding as a GitHub issue labelled with
its severity (`P0` to `P3`) and link it in the results table.

## README claims mapped to tests

The README is short while the app is in development. These rows are the claims the README and
[design.md](design.md) make; keep the list in step with the README as it grows. "Test" is the automated test or the
walkthrough step that proves the claim.

| # | Claim | Test |
| --- | --- | --- |
| 1 | The Live tab shows a scrolling edit log with +/- bytes. | TODO (Live tab): Pilot test and walkthrough L2 |
| 2 | An edits-per-minute sparkline, top-edited articles (15 minutes) and the bot vs human ratio show beside the log. | TODO: walkthrough L3 |
| 3 | Live filters: wiki, hide bots, articles only, minimum byte change. | TODO: walkthrough L4 |
| 4 | The Reading tab ranks articles by spike (today's views over the 30-day median). | TODO (Reading tab): walkthrough R2 |
| 5 | Each Reading row has views, baseline, spike multiple and a 30-day sparkline; a detail pane shows the summary. | TODO: walkthrough R3 |
| 6 | `w` on any article row adds it to the Watchlist, as does typing a title or pasting a URL. | TODO: walkthrough W2 |
| 7 | Live edits to watched pages are highlighted and the Watchlist tab shows a count badge. | TODO: walkthrough W3 |
| 8 | On start, edits made while the app was closed are caught up. | TODO: walkthrough W4 |
| 9 | Enter on a tracked edit shows a red/green diff. | TODO (Diff view): walkthrough D2 |
| 10 | Convergence ranks articles hot in both Live and Reading; the surge trigger fetches history on its own. | TODO (Convergence tab): walkthrough C2 and C3 |
| 11 | `--demo` works with no network. | TODO: walkthrough G2 |
| 12 | `--version` and `--selftest` work, offline. | `tests/test_cli.py` (default run); smoke-test.md section 1 |
| 13 | Works at 80x24 with nothing scrolling sideways; a too-small window gets a message that clears on resize. | TODO: walkthrough G3 |
| 14 | `?` help lists only real bindings; every footer key is clickable; the mouse works on rows, tabs and buttons. | TODO: meta-test of the input inventory, walkthrough G4 |
| 15 | A failing feed is named with its reason in a bar above the footer, and the bar clears on recovery. | TODO: walkthrough G5 |
| 16 | The app is described as unofficial, and the README and `--help` say so. | `--help` text check; README review (manual) |
| 17 | Panes showing Wikipedia text link to the article. | TODO: walkthrough R3 and D2 |
| 18 | Orange is never a primary color or a background. | `tests/test_colours.py` (default run) |
| 19 | It installs with `pipx` from PyPI and as a single Windows exe. | smoke-test.md sections 1 and 2; `post-release.yml` |

## State matrix

For each tab, each state is looked at once at every test size. Write what you saw in the cell (or the issue link), not
just a tick. States that cannot happen on a tab are marked `n/a`.

| State | Live | Reading | Watchlist | Diff | Convergence |
| --- | --- | --- | --- | --- | --- |
| First run, no data yet | TODO | TODO | TODO | TODO | TODO |
| Loading | TODO | TODO | TODO | n/a | TODO |
| Normal, data arriving | TODO | TODO | TODO | TODO | TODO |
| Empty (no matching rows) | TODO | TODO | TODO | n/a | TODO |
| Filtered to nothing | TODO | n/a | n/a | n/a | n/a |
| Feed failing (the bar names it) | TODO | TODO | TODO | TODO | TODO |
| Rate limited (Retry-After honored) | n/a | TODO | TODO | TODO | TODO |
| Recovered (the bar clears) | TODO | TODO | TODO | TODO | TODO |
| Offline | TODO | TODO | TODO | TODO | TODO |
| Replay: paused, fast, skip | TODO | n/a | n/a | n/a | n/a |
| Very long or non-Latin titles | TODO | TODO | TODO | TODO | TODO |
| Window resized while open | TODO | TODO | TODO | TODO | TODO |
| 80x24 / 120x30 / 160x48 / 240x60 | TODO | TODO | TODO | TODO | TODO |
| Light and dark theme | TODO | TODO | TODO | TODO | TODO |

## Walkthroughs

Each step says what to do and what should happen. Do the keyboard pass first, then the same steps with the mouse.
"Pass" needs both. Record P-level issues as you go.

### G. Whole app

- G1. Start `wikihose.exe` with no arguments in Windows Terminal. The app opens on the Live tab within a few seconds.
- G2. Start with `--demo` and the network off (turn Wi-Fi off). Edits appear from the bundled recording; nothing errors.
- G3. At 80x24 every tab fits with no sideways scrolling. Shrink below 80x24: a "make the window bigger" message shows
  and clears when the window grows again.
- G4. `1` to `5` change tab; clicking a tab does the same. `?` opens help and every line in it is a real key. Click each
  footer key. `q` quits and leaves the terminal usable.
- G5. With the network off in a normal (not demo) run, the bar above the footer names the failing feed and why, and
  clears when the network is back.
- G6. Open Task Manager: memory stays flat over ten minutes of Live (no steady climb).

### L. Live tab

- L1. The tab opens showing articles only, bots hidden.
- L2. Edits scroll, newest at the bottom or top as designed, with time, bytes and article. Added bytes and removed
  bytes read differently (a plus or minus, and color).
- L3. At 80 columns the right column shows the sparkline, the people/bots split and the top articles of the last 15
  minutes. The editor column appears from about 105 columns and the summary from about 145.
- L4. Each filter key toggles its filter and the footer shows its state: wiki, hide bots, articles only, minimum byte
  change. Filter to nothing: an empty-state message, not a blank pane.
- L5. Select a row (arrow keys, then click): `w` adds the article to the Watchlist.
- L6. Pause and resume if the replay controls apply; the log keeps its place.

### R. Reading tab (TODO until it merges)

- R1. TODO: first open shows a loading state, then the ranking.
- R2. TODO: the top row's spike multiple equals views divided by baseline, checked against the numbers on screen.
- R3. TODO: select a row: the detail pane shows the summary and a link to the article.
- R4. TODO: `w` on a row adds it to the Watchlist.
- R5. TODO: a Wikimedia rate-limit response shows the feed bar and recovers.

### W. Watchlist tab (TODO until it merges)

- W1. TODO: empty state explains how to add a page.
- W2. TODO: add a page by `w`, by typing a title, and by pasting a Wikipedia URL; a duplicate is not added twice.
- W3. TODO: while the app runs, an edit to a watched page is highlighted and the tab badge counts it.
- W4. TODO: quit, wait, restart: edits made while closed are caught up.
- W5. TODO: each page shows a 30-day sparkline; removing a page works with key and mouse.

### D. Diff view (TODO until it merges)

- D1. TODO: Enter on a tracked edit opens the diff; Escape returns to the same row.
- D2. TODO: removed text is red, added text is green, and a link to the article is shown.
- D3. TODO: a very large edit and an edit that is only a page move both render without error.

### C. Convergence tab (TODO until it merges)

- C1. TODO: empty state before any article is hot in both feeds.
- C2. TODO: a replay that contains a surge puts the article on the list, ranked by edit velocity times spike multiple.
- C3. TODO: the surge trigger fetches pageview history once, with no duplicate requests; the label reads "live edits vs.
  the last full day of reading".

## Results

Fill in during the run.

| Date | Build (commit, exe SHA-256) | Tester | P0 open | P1 open | P2 open | P3 open | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
