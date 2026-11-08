# Development

## Setup

Python 3.12 or newer. From a fresh clone:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

On macOS and Linux use `.venv/bin/python` and quote the extra, `".[dev]"`. The editable install means code changes take
effect without reinstalling; `__version__` is read from `src/wikihose/__init__.py` when the package is installed, so
reinstall after bumping it if you want `pip` metadata to agree.

Each git worktree needs its own `.venv`; create it before anything else.

## Checks

Run these before every pull request. CI runs the same steps on Windows, Ubuntu and macOS with Python 3.12 and 3.13, plus
Python 3.14 on Ubuntu, which is allowed to fail.

```powershell
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy
.venv\Scripts\python -m vulture --min-confidence 80
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m wikihose --version
.venv\Scripts\python -m wikihose --selftest
.venv\Scripts\python -m wikihose --replay src/wikihose/data/sample.jsonl.gz --headless-seconds 5
.venv\Scripts\python -m wikihose --demo --headless-seconds 5
```

Drop `--check` from `ruff format` to rewrite files. Ruff uses a 120 column limit. Mypy requires annotations on every
function in `src/`. Vulture only reports unused code it is nearly certain about, so a report means a real leftover. Its
paths are set in `pyproject.toml`, so it needs no arguments.

`--selftest` checks the install without the network (themes, config defaults, the database migration from version 1 to 2, the bundled sample
and demo recordings, the demo's Reading numbers and the request budget) and prints `selftest: ok`. It exits 1 and prints `selftest: FAILED (n)` otherwise.

`--replay FILE --headless-seconds N` (or `--demo`, the bundled six-minute recording) plays a recording with no screen
for N seconds of recorded time and prints `edits=..., edits_per_min=..., top=...`. CI runs it on the bundled sample and
on the demo to prove the data path end to end.

`scripts/capture_demo.py` remakes `docs/images/demo.svg`, the README picture: the Live tab five minutes into the demo,
120x30, dark theme, New York time. Run it after a visual change to the Live tab (with `NO_COLOR` unset) and read the
result; a test fails when the committed picture is out of date. The test suite clears `NO_COLOR` itself; the script
does not, and refuses to run while it is set.

For floor-size screenshots in a UAT, the script takes `--size WxH`, `--tab N` (press the number key of tab 1 to 5
before capturing) and `--out PATH`. The defaults (120x30, no tab key, `docs/images/demo.svg`) make the README picture, so
write other captures outside the repo:

```powershell
.venv\Scripts\python scripts\capture_demo.py --size 80x24 --tab 2 --out uat-80x24-reading.svg
```

`scripts/capture_demo_reading.py` remakes `src/wikihose/data/demo-reading-enwiki.json.gz`, the Reading numbers
`--demo` shows on the Reading and Convergence tabs: one day's ranking and the summaries of its top rows and of the
ranked articles the demo edits. It goes live, about 110 requests 3 seconds apart (six minutes). Remake it together with
the demo recording, so the day it ranks is the day before the recording.

## CI gates

| Job | What it runs | Blocks merge |
| --- | --- | --- |
| Test (windows, ubuntu, macos; Python 3.12 and 3.13) | ruff check, ruff format --check, mypy, vulture, `pytest -n auto`, `python -m wikihose --version`, `python -m wikihose --selftest`, the headless replay of the sample, the headless demo | Yes |
| Test (ubuntu, Python 3.14) | the same steps | No (informational) |

Changes that only touch Markdown under `docs/` or `LICENSE` skip CI. README changes do not. A lowest-dependency job is
listed in [deferred.md](deferred.md).

## Package layout

```text
src/wikihose/
  __init__.py   version
  __main__.py   python -m wikihose
  cli.py        argument parsing (build_parser) and the entry point: --wiki, --replay, --demo, --record, --headless-seconds
  headless.py   run_headless: feeds LiveStats from a source with no screen
  live_stats.py edits per minute, the 60-bucket series, the most edited, the people/bots split
  convergence.py the Convergence numbers: edits by people per article in 15 minutes, joined with Reading, and the
                surge trigger's queue (its constants are the product choices: window, surge size, minimum spike)
  selftest.py   the checks behind --selftest
  demo_reading.py  the Reading numbers recorded with the demo (a ReadingSource that never touches the network)
  clock.py      the injectable Clock: SystemClock, VirtualClock (the only place that sleeps)
  paths.py      config, data, log and cache directories, with env overrides
  config.py     the Config dataclass, load_config (warns, never raises) and save_config
  net.py        the shared HTTP client: User-Agent, request budget, retries, disk cache, event stream
  sse.py        the server-sent events parser
  titles.py     title normalization, article URL parsing and host_for_wiki (enwiki to en.wikipedia.org)
  messages.py   plain-words text for a FetchError
  store.py      SQLite storage with versioned migrations (schema version 2: the watchlist columns)
  watchlist_input.py  reading what the user types after w: a title or a pasted article URL
  watchlist.py  the Watchlist service: add by page ID, in-memory match by (wiki, title), moves, unseen counts, and
                subscribe/in/len for the tabs that redraw when it changes
  reading.py    the Reading data: the daily spike ranking, 30-day history, summaries, and spike_of (one page's spike,
                for the Convergence surge trigger)
  trend.py      the Watchlist tab's 30-day pageview trend: fetched through ReadingData.history, cached in the pageviews table
  catchup.py    startup catch-up of the watched pages through the Action API
  diff.py       the REST compare payload as data, and render_diff for a Rich Text
  sources/      one DataSource interface, two sources
    events.py     Edit, PageMove, FeedStatus and parse_recentchange
    protocol.py   the DataSource protocol
    live.py       the recentchange stream, with reconnects and back-off
    replay.py     plays a recording back, with pause, speed and skip
    recording.py  the recording file format: Recorder and read_recording
  data/         package data: sample.jsonl.gz (used by tests and --selftest), demo-enwiki.jsonl.gz (--demo) and
                demo-reading-enwiki.json.gz (the Reading numbers --demo shows)
  ui/           the app and its parts
    app.py        WikihoseApp and MainScreen: top bar, tab strip, health bar, footer, too-small cover
    bars.py       TopBar and HealthBar (which also carries the catch-up progress line)
    bindings.py   declared_bindings: our own bindings, for the help screen and the inventory test
    dialogs.py    help, add a page, stop watching a page, change wiki, edit detail
    widgets.py    PageTable (a table that can say which page the cursor is on) and spark, the sparkline text
    theme.py      the themes
    tabs/         live.py (Live), reading.py (Reading), watchlist.py (Watchlist), diff.py (Diff and the OpenDiff
                  message that asks for one) and convergence.py (Convergence)
```

## Tests

- The suite never touches the network. Saved real payloads go in `tests/fixtures/<source>/`, and HTTP is mocked with
  `respx`.
- Time comes from an injectable clock, never `sleep`. A test fails if `src/` calls `time.sleep(` or `asyncio.sleep(`
  outside `clock.py`. `conftest.py` turns stamina's waits off (`stamina.set_testing`).
- The sample recording lives in the package (`src/wikihose/data/sample.jsonl.gz`) and tests read it through
  `importlib.resources`; there is no second copy.
- Every test has a timeout: 60 seconds, set in `pyproject.toml` (`--timeout=0` turns it off). On Windows the plugin
  uses its thread method, which ends the whole pytest process, so a hang fails the run without a summary.
- Every screen gets a Pilot test, using Textual's `App.run_test()`.
- Snapshot tests use `pytest-textual-snapshot`. Update them with `python -m pytest --snapshot-update` only after an
  intentional visual change, and read each changed SVG before committing it. The Live tab is snapshotted at 80x24,
  120x30, 160x48 and 240x60 in the dark theme and 80x24 and 160x48 in the light one, plus the help screen and a
  rate-limited health bar. They play the bundled sample on a virtual clock, so they are the same on every run.
- A meta-test (`tests/uat/test_inventory.py`) checks an inventory of inputs: every key binding, help line, footer
  label and `@click=` action must map to a test. It fails on an input with no entry, an entry for a test that does not
  exist, and an entry that matches no input. To add a key: declare the `Binding` with a `tooltip`, add its
  `binding:`, `footer:` and `help:` entries to `INVENTORY`, and write the test. A `manual:` prefix on the test name
  marks a check made by hand.
- Text from the stream is untrusted. `parse_event` cleans user, title, comment and wiki (control and bidirectional
  characters) and keeps `server_name` only when it is a Wikimedia host; `tests/test_hardening.py` covers it. Every
  `notify` in `src/` passes `markup=False`.
- Colour tests (`tests/test_colours.py`, helper `tests/colour.py`) keep orange out of backgrounds, as
  [design.md](design.md) describes under "Colors". Orange means hue 15 to 45 degrees, saturation above 35% and
  lightness 20% to 85%. They check that each theme's `primary`, `background`, `surface`, `panel`, `warning` and `accent`
  are not orange, that no `<rect>` fill in a docs or snapshot SVG is orange, and that nothing in `src/` puts orange
  behind text with Rich markup or TCSS.
- Tests marked `live` call the real Wikimedia services and run only with `WIKIHOSE_LIVE=1`:

  ```powershell
  $env:WIKIHOSE_LIVE = "1"; .venv\Scripts\python -m pytest -m live
  ```

- Tests marked `uat` drive the whole app and are left out of the default run.

## Real-terminal run

`tests/realterm/` runs the real program in a real pseudo-terminal (ConPTY on Windows, a pty elsewhere) with
Microsoft's `tui-test`, through `tests/uat_kit/`, a kit vendored from the wxbrief repository (each file names the source
commit). It sends the bytes a terminal sends, keys and SGR mouse reports with pointer motion before each click, and
asserts on the rendered screen; a failed wait fails with a dump of the screen. The journeys run at 80x24 and 120x30:
start on the Live tab, keys `1` to `5`, clicking each tab label, clicking `Help` in the footer, `?` and Escape, `b` and
`a`, the replay keys (`+`, space), the too-small cover at 70x20 and back, and `q` exiting with code 0 and leaving no
process. The tests are marked `uat`, so the default run leaves them out; `tests/uat_kit/test_kit.py` (the kit's own
test, against a tiny echo program) is in the default run and skips itself when tui-test is missing (psutil, which it
also needs, is a dev dependency).

```powershell
.venv\Scripts\python -m pytest tests/realterm -m uat -n 4
$env:WIKIHOSE_EXE = "$PWD\dist\wikihose.exe"; .venv\Scripts\python -m pytest tests/realterm -m uat -n 4
```

The first runs `python -m wikihose --demo`; the second runs the built exe (build it with `scripts\build_binary.py`, see [release.md](release.md)). The
child gets `WIKIHOSE_CONFIG_DIR` and `WIKIHOSE_DATA_DIR` in a scratch directory and a dead proxy, so a run never touches
your real settings or the network. Each test has a 90 second limit; the whole run takes about 20 to 35 seconds with
`-n 4`.

Not covered: Windows Terminal rendering (fonts, colours, glyph widths), real tmux or SSH sessions, the live stream (the
journeys play the bundled demo), and the Reading and Convergence tabs with live data (the demo shows its recorded day).

## Recording live data

Fixtures are real payloads. When you capture them:

- Wait 3 seconds between requests.
- Honor `Retry-After` on a 429 or 503.
- Send a User-Agent that carries the repository URL, https://github.com/WPTK/wikihose.

The limits are in [data-sources.md](data-sources.md). Fixtures must not contain secrets or personal contact details.

## Versions and the changelog

Versions follow ZeroVer and `CHANGELOG.md` follows Common Changelog. The rules are in
[CONTRIBUTING.md](../CONTRIBUTING.md#versions-and-the-changelog).
