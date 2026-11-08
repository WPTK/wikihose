# User acceptance testing

The UAT that gates a release. The person running the release runs it on the release candidate, using the built exe,
and fills in the results at the bottom. The target behaviour comes from [design.md](design.md). Session 4 ran it
first on 2026-11-08; its results are in the last section.

## Severity

| Level | Meaning | Example |
| --- | --- | --- |
| P0 | The app crashes, hangs, corrupts data or breaks the terminal. | A traceback on start; the terminal left unusable after `q`. |
| P1 | A headline feature does not work, or a README claim is false. | The Live log never updates; `w` does not add to the Watchlist. |
| P2 | A feature works but is wrong or awkward in a way a user would notice. | A key missing from the footer; a layout that scrolls sideways at 80x24. |
| P3 | Cosmetic or minor. | A one-character misalignment; a wording nit. |

Orange used as a primary color or a background ([design.md](design.md), "Colors") is a P2 at least, and P1 if it is
on a main screen. In the real-terminal run, a dead advertised key, a click that does not stick and a screen that
contradicts itself are P1.

## Exit criteria

A release candidate passes when all of these hold:

- 0 open P0 and 0 open P1.
- Every README claim in the table below has a test or a walkthrough step that passed on the built exe.
- P2 and P3 findings are filed as issues, or as lines in [deferred.md](deferred.md), with the release notes naming any
  that a user would meet.
- `--version` and `--selftest` pass on the exe and from PyPI ([smoke-test.md](smoke-test.md)); the wheel and sdist are
  checked by `build.yml`.
- The state matrix has no empty cell.

## How to run it

Automated first, then by hand:

```powershell
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m pytest tests/realterm -m uat -n 4
$env:WIKIHOSE_EXE = "$PWD\dist\wikihose.exe"; .venv\Scripts\python -m pytest tests/realterm -m uat -n 4
```

The first is the default run (Pilot tests, snapshots, the input inventory, the colour tests). The second adds the tests
that drive the whole program through a real pseudo-terminal with `tui-test`, keys and mouse ([development.md](development.md),
"Real-terminal run"); the third runs the same journeys on the built exe. Then the walkthroughs below by hand in Windows
Terminal. Floor-size pictures of any tab come from `scripts/capture_demo.py --size 80x24 --tab N --out FILE`.

Test sizes: 80x24 (the floor), 120x30, 160x48 and 240x60. File each finding as a GitHub issue labelled with its
severity (`P0` to `P3`) and link it in the results table.

## README claims mapped to tests

The claims the README and [design.md](design.md) make. "Test" is the automated test (default run unless marked) or the
walkthrough step that proves it on the exe.

| # | Claim | Test |
| --- | --- | --- |
| 1 | The Live tab shows a scrolling edit log with +/- bytes. | `test_log_cells_show_local_time_coloured_bytes_and_new_pages`, `test_log_defaults_to_articles_by_people_newest_first`; real-terminal start journey; L2 |
| 2 | An edits-per-minute sparkline, top-edited articles (15 minutes) and the bot vs human ratio show beside the log. | `test_side_column_shows_the_numbers`, `test_the_split_counts_bots_even_while_they_are_hidden`, Live snapshots at four sizes; L3 |
| 3 | Live filters: wiki, hide bots, articles only, minimum byte change. | `test_b_hides_and_shows_bot_edits`, `test_a_toggle_refills_the_log_from_the_last_edits`, `test_size_key_cycles_through_the_steps`, `test_i_switches_wiki_and_starts_the_numbers_over`, `test_clicking_the_filter_words_does_what_the_keys_do`; real-terminal filter journey; L4 |
| 4 | The Reading tab ranks articles by spike (today's views over the 30-day median). | `test_load_ranks_spikes_and_filters`, `test_shows_25_rows_by_default_with_the_day_and_the_columns`; R2 |
| 5 | Each Reading row has views, baseline, spike multiple and a 30-day sparkline; a detail pane shows the summary. | `test_the_detail_pane_follows_the_cursor`, `test_spark_scales_to_the_largest_and_merges_days`, Reading snapshots; R3 |
| 6 | `w` on any article row adds it to the Watchlist, as does typing a title or pasting a URL. | `test_w_on_a_log_row_adds_that_article`, `test_w_on_a_top_row_adds_that_article`, `test_w_on_a_reading_row_watches_that_page`, `test_the_table_takes_the_focus_and_w_names_the_page_at_the_cursor` (Convergence: it checks the page `w` gets; the add is the shared path the other tests cover), `test_the_dialog_reads_a_pasted_wikipedia_url`, `test_w_on_the_same_page_twice_says_so_and_does_not_double_count`; W2 |
| 7 | Live edits to watched pages are highlighted and the Watchlist tab shows a count badge. | `test_live_edits_to_watched_pages_count_as_unseen_without_any_request`, `test_a_watched_edit_is_marked_in_the_log_and_others_are_not`, `test_the_tab_lists_each_page_with_its_unseen_count_and_the_badge_adds_them_up`; W3 |
| 8 | On start, edits made while the app was closed are caught up. | `test_the_catch_up_runs_after_startup_and_feeds_the_badge`, `tests/test_catchup.py`; W4 |
| 9 | Enter on a tracked edit shows a red/green diff. | `test_enter_opens_the_diff_from_the_revision_last_seen_and_marks_the_page_seen`, `test_added_lines_are_green_and_marked_with_a_plus`, `test_removed_lines_are_red_with_a_minus`; D1, D2 |
| 10 | Convergence ranks articles hot in both Live and Reading; the surge trigger fetches history on its own. | `test_ranked_by_edits_times_spike_and_only_when_hot_in_both`, `test_opening_the_tab_loads_reading_and_ranks_the_overlap`, `test_a_surge_fetches_history_once_and_joins_the_list`, `test_spike_of_fetches_one_history_and_caches_it`; C2, C3 |
| 11 | `--demo` works with no network. | `test_demo_headless_opens_no_socket` and `test_the_demo_app_opens_no_socket_on_any_tab` (every connection and name lookup blocked at the socket), `test_demo_headless_runs_with_no_network`; G2 |
| 12 | `--version` and `--selftest` work, offline. | `test_version_prints_and_exits_zero`, `test_selftest_exits_zero_and_prints_ok`; `build.yml` runs both on the exe; smoke-test.md section 1 |
| 13 | Works at 80x24 with nothing scrolling sideways; a too-small window gets a message that clears on resize. | `test_columns_follow_the_width_and_nothing_scrolls_sideways`, `test_columns_fit_without_sideways_scrolling` (Reading), `test_the_columns_fit_without_scrolling_sideways` (Convergence), `test_too_small_covers_the_screen_and_clears_on_resize`; real-terminal resize journey; G3 |
| 14 | `?` help lists only real bindings; every footer key is clickable; the mouse works on rows, tabs and buttons. | `tests/uat/test_inventory.py` (every key, footer label, help line and click has a test), `test_clicking_each_live_footer_key_does_what_its_key_does`, `test_clicking_a_tab_switches_to_it`, `test_clicking_a_log_row_opens_its_detail`; real-terminal mouse journey; G4 |
| 15 | A failing feed is named with its reason in a bar above the footer, and the bar clears on recovery. | `test_health_bar_shows_a_failing_feed_and_hides_on_recovery`, `test_a_failed_load_says_why` (Reading), `test_a_rate_limited_surge_waits_and_is_tried_again`; G5 |
| 16 | The app is described as unofficial, and the README and `--help` say so. | `test_help_says_unofficial`; README review (manual) |
| 17 | Panes showing Wikipedia text link to the article. | `test_enter_on_a_log_row_opens_the_detail_with_the_article_link`, `test_the_diff_always_shows_the_address_of_the_article`; R3, D2 |
| 18 | Orange is never a primary color or a background. | `tests/test_colours.py` (themes, every snapshot and docs SVG, the source) |
| 19 | It installs with `pipx` from PyPI and as a single Windows exe. | smoke-test.md sections 1 and 2; `post-release.yml` (after the tag) |

## State matrix

Each cell names what shows, and the test or step that saw it. "Snapshot" means a committed SVG under
`tests/__snapshots__/`. States that cannot happen on a tab are `n/a`.

| State | Live | Reading | Watchlist | Diff | Convergence |
| --- | --- | --- | --- | --- | --- |
| First run, no data yet | "Waiting for edits" side column: `test_the_side_column_before_any_edit` | Nothing fetched until opened: `test_nothing_is_fetched_until_the_tab_is_opened` | How to add a page: `test_an_empty_watchlist_says_how_to_add_a_page` | How to open a diff: `test_the_diff_tab_with_nothing_open_says_how_to_open_one` | "Waiting for the Reading numbers" and the rule: `test_the_empty_tab_says_why` |
| Loading | Stream connecting shows in the health bar: `test_health_bar_shows_a_failing_feed_and_hides_on_recovery` | "Loading… n/total": `test_shows_25_rows_by_default_with_the_day_and_the_columns` | Dot in the trend cell: `test_the_cell_shows_a_dot_while_the_history_is_loading` | "Loading": `test_a_diff_that_is_loading_says_so` | Waiting text until Reading arrives: `test_the_empty_tab_says_why` |
| Normal, data arriving | Live snapshots at 80x24 to 240x60 | Reading snapshots | `test_the_tab_lists_each_page_with_its_unseen_count_and_the_badge_adds_them_up` | `test_a_changed_line_highlights_added_text_green_and_removed_text_red` | Convergence snapshots, `test_opening_the_tab_loads_reading_and_ranks_the_overlap` |
| Empty (no matching rows) | Side column before any edit: `test_the_side_column_before_any_edit` | "No spikes found": `test_an_empty_day_says_so` | `test_an_empty_watchlist_says_how_to_add_a_page` | n/a | "Nothing is hot in both right now": `test_the_empty_tab_says_why` |
| Filtered to nothing | Empty log with the filter line still showing: `test_b_hides_and_shows_bot_edits` | n/a | n/a | n/a | n/a |
| Feed failing (the bar names it) | Health bar: `test_health_bar_shows_a_failing_feed_and_hides_on_recovery` | Status line says why: `test_a_failed_load_says_why` | Trend says why: `test_a_failed_request_says_so_in_words_and_leaves_a_dash` | `test_a_diff_that_cannot_be_loaded_says_why_in_plain_words_and_leaves_the_page_unseen` | Reading failed: says so and points to the Reading tab (`test_the_empty_tab_says_why`); a surge fetch failed: `test_a_surge_that_cannot_reach_wikimedia_waits_a_minute`, `test_bad_data_for_one_surge_moves_on_to_the_next` |
| Rate limited (Retry-After honored) | n/a (one stream) | "rate limited, retrying in Ns": `test_a_failed_load_says_why`, `test_a_rate_limit_retry_is_cancelled_by_a_wiki_change` | `test_a_rate_limited_request_says_when_it_will_retry` | `test_fetch_failures_are_plain_errors` | `test_a_rate_limited_surge_waits_and_is_tried_again` |
| Recovered (the bar clears) | `test_health_bar_shows_a_failing_feed_and_hides_on_recovery` | `r` loads again: `test_r_fetches_again` | `test_a_rate_limited_fill_retries_after_the_delay` | The next Enter loads again (walkthrough D1) | `test_a_rate_limited_surge_waits_and_is_tried_again` |
| Offline (a replay or `--demo`) | Plays the recording: `test_demo_headless_runs_with_no_network` | Replay: "needs a live connection" (`test_a_replay_has_no_reading_data`); demo: the recorded ranking (`test_the_demo_fills_the_reading_tab`) | `w` says it needs a connection: `test_w_in_a_replay_says_it_needs_a_connection` | n/a (needs a connection; says so) | Replay: "needs the Reading numbers"; demo: the recorded ranking (`test_the_demo_fills_the_convergence_tab`) |
| Replay: paused, fast, skip | `test_replay_keys_pause_speed_and_skip`; real-terminal replay journey | n/a | n/a | n/a | n/a |
| Very long or non-Latin titles | Cut with an ellipsis, cleaned: `tests/test_hardening.py` | `test_a_long_summary_is_cut_with_an_ellipsis_inside_the_pane`, `test_wide_numbers_fit_their_columns` | Titles cut with an ellipsis (walkthrough W5 with a long title) | `test_highlight_offsets_are_utf8_bytes_so_text_after_a_wide_character_lines_up` | Long titles cut with an ellipsis: `test_the_columns_fit_without_scrolling_sideways`; `test_spike_of_uses_the_top_list_figure_and_handles_no_data` refuses a lone surrogate |
| Window resized while open | `test_columns_change_when_the_window_is_resized` | Columns follow the width: `test_columns_fit_without_sideways_scrolling`; real-terminal resize journey | `test_the_narrow_layout_has_no_trend_column_and_asks_for_nothing` | Real-terminal resize journey (every tab) | `test_the_columns_fit_without_scrolling_sideways`; real-terminal resize journey |
| 80x24 / 120x30 / 160x48 / 240x60 | Snapshots at all four | Snapshots 80x24 and 140x40; columns tested to 240 | Snapshot 120x30 | Snapshot via Watchlist; real-terminal 80x24 and 120x30 | Snapshots 80x24 and 120x30; columns tested at all four |
| Light and dark theme | Snapshots in both | Snapshots in both | Dark snapshot; theme slots tested | Colour test on every diff style | Snapshots in both |

## Walkthroughs

Each step says what to do and what should happen. Do the keyboard pass first, then the same steps with the mouse.
"Pass" needs both. Steps marked (auto) are also driven by the real-terminal journeys on the exe.

### G. Whole app

- G1. Start `wikihose.exe` with no arguments in Windows Terminal. The app opens on the Live tab within a few seconds.
- G2. Start with `--demo` and the network off (turn Wi-Fi off). Edits appear from the bundled recording; Reading shows
  the recorded ranking; nothing errors. (auto, network not blocked)
- G3. At 80x24 every tab fits with no sideways scrolling. Shrink below 80x24: a "make the window bigger" message shows
  and clears when the window grows again. (auto)
- G4. `1` to `5` change tab; clicking a tab does the same. `?` opens help and every line in it is a real key. Click each
  footer key. `q` quits and leaves the terminal usable. (auto)
- G5. With the network off in a normal (not demo) run, the bar above the footer names the failing feed and why, and
  clears when the network is back.
- G6. Open Task Manager: memory stays flat over ten minutes of Live (no steady climb).

### L. Live tab

- L1. The tab opens showing articles only, bots hidden.
- L2. Edits scroll, newest at the top, with time, bytes and article. Added and removed bytes read differently (a plus
  or minus, and color).
- L3. At 80 columns the right column shows the sparkline, the people/bots split and the top articles of the last 15
  minutes. The editor column appears from about 105 columns and the summary from about 145.
- L4. Each filter key toggles its filter and the filter line shows its state: `i` wiki (live only; off in a replay), `b` bots, `a` articles only,
  `m` minimum size. (auto for `a` and `b`)
- L5. Select a row (arrow keys, then click): `w` adds the article to the Watchlist; Enter opens the edit's detail with
  a link; `d` opens its diff.
- L6. `p` pauses the log; it keeps its place while the numbers keep counting. In a replay, space, `+`, `-` and `]`
  pause, speed and skip. (auto for space and `+`)

### R. Reading tab

- R1. First open shows "Loading… n/total", then the ranking for the newest full day (UTC).
- R2. The top row's spike multiple equals views divided by the usual figure, checked against the numbers on screen. A
  `~` marks a rough figure.
- R3. Highlight a row: the detail pane shows the description and summary.
- R4. `w` on a row adds it to the Watchlist. `n` cycles 25, 50 and 100 rows; `r` fetches again.
- R5. A Wikimedia rate-limit response shows "rate limited, retrying in Ns" and recovers.

### W. Watchlist tab

- W1. Empty state explains how to add a page.
- W2. Add a page by `w`, by `W` and typing a title, and by pasting a Wikipedia URL; a duplicate is not added twice.
- W3. While the app runs, an edit to a watched page is highlighted in the Live log and the tab badge counts it.
- W4. Quit, wait, restart: edits made while closed are caught up and a notice says how many pages changed.
- W5. Each page shows a 30-day sparkline at 120 columns and up; `m` marks seen; `d` or Delete removes a page after a
  question, by key and by mouse.

### D. Diff view

- D1. Enter on a watched page with new edits opens the diff; Escape or `b` returns to the tab it came from.
- D2. Removed text is red with a minus, added text is green with a plus, and the article's address is shown.
- D3. A very large edit is cut with a notice and a link; a new page explains there is nothing to compare.

### C. Convergence tab

- C1. With nothing hot in both, the tab says so and explains the rule. In a replay it says it needs the Reading
  numbers. (auto)
- C2. In a live run (or the demo), an article edited by people in the last 15 minutes whose last full day of reading was
  at least twice its usual is listed, ranked by edits times spike; the From column says Reading. The heading reads
  "Live edits vs. the last full day of reading, <day> (UTC)".
- C3. An article with 10 edits by people in 15 minutes that is not in the ranking gets its own 30-day history fetched
  once (the status line counts surge checks; no second request for it in `--debug`'s log); it is listed as "surge" if
  its spike is at least 2.
- C4. `w` on a row watches it; the detail pane shows its edits, views and usual figure.

## Results

| Date | Build (commit, exe SHA-256) | Tester | P0 open | P1 open | P2 open | P3 open | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-08 | `21cf62a`, `38cb4a70711d398d9dde3ecd4b074c271c96056c21cd0e5615d87ae01782f0b5`; recheck after the fixes below on the release-candidate branch at `175ae87`, `59c0579dcb811870283d3f7547f8bc6541dad9ff3abaf938229e1c2c00f6825d` | Session 4 (automated run and captures; the by-hand walkthroughs in Windows Terminal are still to do) | 0 | 0 | 0 | 3 | Pass for the automated part |

Findings from that run, and where each went:

| Finding | Severity | Where |
| --- | --- | --- |
| At 80x24 the demo's Live footer was cut at `+ Faste`; Slower and Skip could not be seen or clicked | P2 | Fixed in the release-candidate PR: `+` and `-` are in `?` help only (the top bar shows the speed); `test_the_footer_fits_80_columns_on_every_tab` |
| `scripts/capture_demo.py --tab 2` and `--tab 5` showed "needs a live connection" instead of the demo's Reading numbers | P2 | Fixed in the release-candidate PR |
| The `?` journey could read the help screen before all of it was drawn (one failure in three exe runs) | P3 | Fixed in the release-candidate PR: it waits for every line it checks |
| A page with a usual of 0 tops the Reading ranking with a five-digit spike | P3 | #26 |
| Live log rows are in arrival order, not time order | P3 | #27 |
| Reading at 240 columns spreads its numbers far from the titles; a blank cell in one sparkline | P3 | #28 |
| A plain `--replay` footer (with `i Wiki`) is cut at 80 columns, so `] Skip` cannot be seen or clicked | P2 | Fixed for #29: `i` is off in every replay (a recording holds one wiki; `--wiki` picks it); `test_the_footer_fits_80_columns_on_every_tab[replay]` |

No orange anywhere in the captures (the one orange-range value is the window-frame dot the SVG export draws). Every
tab fits 80x24 and 240x60 with nothing cut sideways, and Reading and Convergence show the demo's recorded day.

### Real-input run (tui-test)

The 28 journeys of `tests/realterm` (14 at each of 80x24 and 120x30), driven through a real pseudo-terminal.

| Date | Build (commit, exe SHA-256) | Target | Result |
| --- | --- | --- | --- |
| 2026-11-08 | `5bc6813` (source before the rebase onto main), `e192d20d4f0d1152f30540894f0a5200bb55dede4ad115c79e4fe4c327450b78` | `wikihose.exe --demo` | 28 passed, 0 failed |
| 2026-11-08 | `5bc6813` | `python -m wikihose --demo` (Python 3.13) | 28 passed, 0 failed |
| 2026-11-08 | `21cf62a`, `38cb4a70…` | `wikihose.exe --demo` | 27 passed, 1 failed, then 28 and 28 (the `?` flake above) |
| 2026-11-08 | `21cf62a` | `python -m wikihose --demo` (Python 3.13) | 28 passed, 0 failed |
| 2026-11-08 | release-candidate branch at `175ae87`, `59c0579d…` | `wikihose.exe --demo` | 28 passed, 0 failed, twice |
| 2026-11-08 | release-candidate branch | `python -m wikihose --demo` (Python 3.13) | 28 passed, 0 failed |
