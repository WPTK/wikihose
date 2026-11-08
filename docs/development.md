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

Run these before every pull request. CI runs the same steps on Windows, Linux and macOS with Python 3.12 and 3.13
(3.14 on Linux is informational).

```powershell
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy
.venv\Scripts\python -m vulture --min-confidence 80
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m wikihose --version
.venv\Scripts\python -m wikihose --selftest
.venv\Scripts\python -m wikihose --replay src/wikihose/data/sample.jsonl.gz --headless-seconds 5
```

Drop `--check` from `ruff format` to rewrite files. Ruff uses a 120 column limit. Mypy requires annotations on every
function in `src/`. Vulture only reports unused code it is nearly certain about, so a report means a real leftover. Its
paths are set in `pyproject.toml`, so it needs no arguments.

`--selftest` checks the install without the network (themes, config defaults, the database migration, the bundled sample
recording and the request budget) and prints `selftest: ok`. It exits 1 and prints `selftest: FAILED (n)` otherwise.

`--replay FILE --headless-seconds N` plays a recording with no screen for N seconds of recorded time and prints
`edits=..., edits_per_min=..., top=...`. CI runs it on the bundled sample to prove the data path end to end.

## CI gates

| Job | What it runs | Blocks merge |
| --- | --- | --- |
| Test (windows, ubuntu, macos; Python 3.12 and 3.13) | ruff check, ruff format --check, mypy, vulture, `pytest -n auto`, `python -m wikihose --version`, `python -m wikihose --selftest`, the headless replay of the sample | Yes |
| Test (ubuntu, Python 3.14) | the same steps | No (informational) |

Changes that only touch Markdown under `docs/` or `LICENSE` skip CI. README changes do not. A lowest-dependency job is
listed in [deferred.md](deferred.md).

## Package layout

```text
src/wikihose/
  __init__.py   version
  __main__.py   python -m wikihose
  cli.py        argument parsing (build_parser) and the entry point: --wiki, --replay, --record, --headless-seconds
  headless.py   run_headless: feeds LiveStats from a source with no screen
  live_stats.py edits per minute, the 60-bucket series, the most edited, the people/bots split
  watchlist.py  the in-memory watchlist
  selftest.py   the checks behind --selftest
  clock.py      the injectable Clock: SystemClock, VirtualClock (the only place that sleeps)
  paths.py      config, data, log and cache directories, with env overrides
  config.py     the Config dataclass, load_config (warns, never raises) and save_config
  net.py        the shared HTTP client: User-Agent, request budget, retries, disk cache, event stream
  sse.py        the server-sent events parser
  titles.py     title normalization and article URL parsing
  store.py      SQLite storage with versioned migrations
  sources/      one DataSource interface, two sources
    events.py     Edit, PageMove, FeedStatus and parse_recentchange
    protocol.py   the DataSource protocol
    live.py       the recentchange stream, with reconnects and back-off
    replay.py     plays a recording back, with pause, speed and skip
    recording.py  the recording file format: Recorder and read_recording
  data/         package data: sample.jsonl.gz, the recording used by tests and --selftest
  ui/           the app and its parts
    app.py        WikihoseApp and MainScreen: top bar, tab strip, health bar, footer, too-small cover
    bars.py       TopBar and HealthBar
    bindings.py   declared_bindings: our own bindings, for the help screen and the inventory test
    dialogs.py    help, add a page, change wiki, edit detail
    widgets.py    PageTable, a table that can say which page the cursor is on
    theme.py      the themes
    tabs/         live.py (the Live tab) and stub.py (the other four, until they are built)
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

## Recording live data

Fixtures are real payloads. When you capture them:

- Wait 3 seconds between requests.
- Honor `Retry-After` on a 429 or 503.
- Send a User-Agent that carries the repository URL, https://github.com/WPTK/wikihose.

The limits are in [data-sources.md](data-sources.md). Fixtures must not contain secrets or personal contact details.

## Versions and the changelog

Versions follow ZeroVer and `CHANGELOG.md` follows Common Changelog. The rules are in
[CONTRIBUTING.md](../CONTRIBUTING.md#versions-and-the-changelog).
