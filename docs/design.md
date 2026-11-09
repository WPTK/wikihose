# Design

wikihose is a terminal dashboard for watching Wikipedia live: what people are editing now, what people are suddenly
reading, where the two overlap, and edits to pages you choose to track. Python 3.12+ and Textual, pinned
`textual>=8.2,<9` (dev: `textual==8.2.8`). Installed with `pipx` from PyPI or as a GitHub release binary.

This file is the target. Session 1 starts with a design pass that may change the shell layout; record any change here.

## Tabs

1. **Live.** A scrolling edit log with +/- bytes, an edits-per-minute sparkline, top-edited articles over the last 15
   minutes, and the bot vs human ratio. Filters: wiki, hide bots, articles only, minimum byte change.
2. **Reading.** Daily spike rankings. Spike = today's views divided by the article's 30-day median. Columns: article,
   views, baseline, spike multiple, 30-day sparkline. A detail pane shows the article summary. A page whose median is
   below 100 views a day (`MIN_USUAL` in `src/wikihose/reading.py`) has too little history for a multiple: it shows
   `new`, ranks after every page with a multiple, and is never hot in Convergence (#26).
3. **Watchlist.** Pages you track.
   - Add one with `w` on any article row in any tab, or by typing a title or pasting a Wikipedia URL.
   - While the app runs, match live edits against the list with no extra requests, highlight matches and badge the tab.
   - On startup, catch up on edits made while the app was closed.
   - Each page has a 30-day pageview sparkline.
4. **Diff.** `d` on a Live edit, or on a watched page (what changed since you last looked), shows a red/green
   terminal diff. Escape goes back to the tab it came from.
5. **Convergence.** Articles hot in both Live and Reading, ranked by edit velocity times spike multiple.
   - Surge trigger: when an article passes an edit-rate threshold (start at 10 human edits in 15 minutes), fetch its
     pageview history automatically.
   - Label it "live edits vs. the last full day of reading".
   - As built (Session 4, owner to confirm; each is one constant in `src/wikihose/convergence.py`): edit velocity is
     edits by people (bots left out) to the article in the last 15 minutes; "hot in Reading" means the last full day
     had at least 2 times the usual views; the score is edits times spike. The trigger fetches each page's history
     once per run, only after the tab has been opened and the day's Reading ranking has arrived, through the shared
     request budget. Pages already in the Reading ranking are never fetched again. A queued page that stopped surging
     before its turn is dropped (it can surge again later). A surge row needs at least 1,000 views on that day, so a
     tiny or brand-new page cannot top the list.
6. **Hourly** (later, maybe): near-real-time reading data from Wikimedia's hourly pageview dumps. Left out of the app
   until it is built (owner, 7 Nov 2026).

Non-goals: no logins or accounts, no editing Wikipedia, no notifications outside the terminal, no AI summaries, no views
that follow one editor across edits.

## Shell layout (chosen 7 Nov 2026)

- Layout A: a tab strip across the top (Live, Reading, Watchlist, Diff, Convergence; keys 1-5 or a click), full-width
  content, and the key footer at the bottom. Every footer key is clickable. The Watchlist tab label carries a count
  badge. A one-line top bar names the app, the wiki and the local time.
- Layout B (a left rail holding navigation, feed health and the live numbers) was mocked up too and rejected: it costs
  24 columns on every tab, which Reading and Diff can't spare at 80x24.
- Feed health has no always-on line. While a feed is failing, a one-line bar above the footer names the feed and why
  ("Stream: reconnecting in 8s", "Pageviews: rate limited, retrying in 12s"), and it clears on recovery. Full feed
  status belongs in a later settings or `--diagnose` view (owner's choice).
- Floor size: 80x24.
- One key map across the tabs (owner's request, 8 Nov 2026): Enter, and a double click, on a row that names a page
  open that page on its wiki in the default browser (Live log and Most edited, Reading, Watchlist, Convergence); on the
  Diff tab they open the change. A single click only moves the cursor. `d` is Diff (Live: the selected edit, Watchlist:
  what changed since you last looked), `e` is the details of a Live edit, `s` is the Live size filter, `m` marks a
  watched page seen and `x` or Delete removes it after a question. Escape leaves the Diff tab. Opening is checked
  first: over SSH, or with a browser that would run in this terminal (lynx, w3m and the like), nothing is opened and a
  notice shows the address to copy. In a replay the Live footer leaves out the `enter Open` label, which would not fit
  80 columns; the key works.
- Live opens showing articles only with bots hidden. One key toggles each. The people/bots split always counts both
  (owner's choice).
- At 80 columns the Live log shows time, bytes and article, with a right-hand column holding the edits-per-minute
  sparkline, the people/bots split and the most edited articles in the last 15 minutes. The editor column appears from
  about 105 columns and the edit summary from about 145. Nothing scrolls sideways.

## Run modes

`--demo` (a bundled recording that works with no network), `--record <file>`, `--replay <file>` (pause, speed and skip
keys), `--offline` (saved data only), `--version`, `--selftest`, `--diagnose`, `--debug`, `--no-config`.

What the reader does is saved in every mode: the watchlist (add, remove, mark seen) and the settings (theme, filters, the
Reading row count). Recorded data is never saved: a replay or `--demo` writes no edit minutes and no pageview history to
the database, because a recording holds old edits. The one setting a mode does not save is the wiki in the demo, which is
always `enwiki`. `--no-config` still means the config file is never written. With no connection a page is watched under a
placeholder ID, and the next live start looks up the real one.

## Architecture

- One shared network client handles the User-Agent, gzip, timeouts, the request budget, `stamina` retries and the
  `hishel` cache (see earthwave's `net.py`).
- Every feed sits behind one `DataSource` interface with a live source and a replay source, so the rest of the app can't
  tell them apart (the eyewall pattern). Time comes from an injectable clock, never `sleep`.
- The stream runs in a Textual worker, and the UI never waits on the network. Keep rolling aggregates in memory, never
  every event, and cap the on-screen log.
- SQLite, keyed on `(wiki, page)`. Tables: pageview history and baselines, the watchlist (page ID plus last known
  title), the last-seen revision per watched page, and rolling edit aggregates. Normalize titles: spaces vs underscores,
  and first-letter case per wiki.
- Paths come from `platformdirs`, with env var overrides `WIKIHOSE_CONFIG_DIR` and `WIKIHOSE_DATA_DIR` (earthwave's
  `paths.py`).
- Package layout: `src/wikihose/` (the eyewall layout).
- Terminal size: set a minimum, with a "make the window bigger" message that clears on resize. Test sizes: the floor
  (80x24), 120x30 (the Windows Terminal default), 160x48 and 240x60.
- Feed health names each failing feed, says why (down, rate limited, bad data) and clears on recovery.
- `?` help lists only real bindings. The mouse works on rows, tabs, the footer and buttons.
- The UI uses plain words.
- Panes that show Wikipedia text link to the article, for CC BY-SA attribution.
- The README and `--help` say the app is unofficial and not affiliated with the Wikimedia Foundation.
- User-facing text describes this app as it is, with no comparisons to other tools.

## Colors

- Orange is allowed for text, borders, underlines, sparkline marks and small icons. It is never the primary color and
  never a background.
- A background is any filled area: the screen, panels, table rows, selection and cursor highlights, buttons, badges and
  toasts.
- Textual fills some widgets with theme colors. In a check on 7 Nov 2026, the default `textual-dark` theme (accent and
  warning both `#ffa62b`) drew a `variant="warning"` button with an orange background, `#fca834`.
- Ship our own dark and light themes. Their `primary`, `background`, `surface`, `panel` and `warning` are not orange.
  Offer a built-in theme only if snapshots of the main screens in it pass the SVG test below.
- In these tests, "orange" means a hue between 15 and 45 degrees, saturation above 35% and lightness between 20% and
  85%. Adapt wxbrief's `scripts/svgcolour.py` for the color math.
- Tests:
  - Every offered theme's `primary`, `background`, `surface`, `panel` and `warning` are not orange.
  - No snapshot or docs SVG has an orange `<rect>` fill. Textual's exporter draws backgrounds as
    `<rect fill="#...">` and text as CSS `fill:` classes, so orange text passes. The window's title-bar dots are
    `<circle>` elements and don't count.
  - A grep test fails on Rich markup that puts orange behind text, such as `on orange` or `on dark_orange`.
