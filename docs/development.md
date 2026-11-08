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
```

Drop `--check` from `ruff format` to rewrite files. Ruff uses a 120 column limit. Mypy requires annotations on every
function in `src/`. Vulture only reports unused code it is nearly certain about, so a report means a real leftover. Its
paths are set in `pyproject.toml`, so it needs no arguments.

CONTRIBUTING.md also lists `python -m wikihose --selftest`. That flag does not exist yet and will arrive with the app.

## CI gates

| Job | What it runs | Blocks merge |
| --- | --- | --- |
| Test (windows, ubuntu, macos; Python 3.12 and 3.13) | ruff check, ruff format --check, mypy, vulture, `pytest -n auto`, `python -m wikihose --version` | Yes |
| Test (ubuntu, Python 3.14) | the same steps | No (informational) |

Changes that only touch Markdown under `docs/` or `LICENSE` skip CI. README changes do not. A lowest-dependency job is
listed in [deferred.md](deferred.md).

## Package layout

```text
src/wikihose/
  __init__.py   version
  __main__.py   python -m wikihose
  cli.py        argument parsing and the entry point
  ui/           screens, widgets and the themes (theme.py)
  net.py        PR 2: the shared HTTP client, User-Agent, request budget, retries and cache
  sources/      PR 2: live and replay sources behind one interface
  store.py      PR 2: SQLite storage
  config.py     PR 2: the config file
  paths.py      PR 2: config and data directories
  clock.py      PR 2: the injectable clock
```

## Tests

- The suite never touches the network. Saved real payloads go in `tests/fixtures/<source>/`, and HTTP is mocked with
  `respx`.
- Time comes from an injectable clock, never `sleep`.
- Every test has a timeout: 60 seconds, set in `pyproject.toml` (`--timeout=0` turns it off). On Windows the plugin
  uses its thread method, which ends the whole pytest process, so a hang fails the run without a summary.
- Every screen gets a Pilot test, using Textual's `App.run_test()`.
- Snapshot tests use `pytest-textual-snapshot`. Update them with `python -m pytest --snapshot-update` only after an
  intentional visual change, and read each changed SVG before committing it.
- A meta-test checks an inventory of inputs: every key binding, help line, footer label and clickable element must have
  a test. It arrives with the app shell.
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
