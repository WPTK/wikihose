# Contributing

Thanks for helping with wikihose. For anything larger than a typo, open an issue first so the change can be agreed
before you write it. What the app is meant to be is in [docs/design.md](docs/design.md); where its data comes from and
the request limits are in [docs/data-sources.md](docs/data-sources.md). `docs/development.md` (layout, test tooling,
the CI gates table) is added with the project scaffold.

## Setup

Python 3.12 or newer. Windows paths shown; use `.venv/bin/python` elsewhere.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Checks

Run these before opening a PR. CI runs the same steps on Windows, Linux and macOS.

```powershell
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy
.venv\Scripts\python -m vulture --min-confidence 80
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m wikihose --version
.venv\Scripts\python -m wikihose --selftest
```

Python 3.12 (the floor) and 3.13 must pass. 3.14 runs but does not block a merge. Changes that only touch Markdown in
`docs/` or `LICENSE` skip CI. README changes do not.

## Branches and pull requests

- Work on a branch cut from `main`. Use a short descriptive name.
- Open a PR to `main`. The description says what changed, why (the root cause, for a fix) and how you verified it.
  Link the issue with `Closes #N`.
- For interface changes, drive the flow in the running app (Pilot or `--replay`) and include a screenshot.
- Keep prose and docs PRs separate from workflow and script PRs.
- PRs merge when CI is green on the latest commit after a review pass.

## Commits

Commit subjects are plain imperative sentences, such as "Add the watchlist badge". No prefixes or trailers.

## Tests

- No test touches the network. Saved real payloads live in `tests/fixtures/<source>/`; mock HTTP with `respx`.
- Time comes from the injectable clock, never `sleep`.
- Every screen has a Pilot test. Snapshot tests (`pytest-textual-snapshot`) are updated only after an intentional
  change, with `python -m pytest --snapshot-update`; read each changed SVG before committing it.
- Every test has a timeout (`pytest-timeout`).
- Every key binding, help line, footer label and clickable element has a test. A meta-test fails when one does not.
- Colour tests keep orange out of backgrounds; see [docs/design.md](docs/design.md) under "Colors".
- Fixtures must not contain secrets, key-looking strings or personal contact details.

## Versions and the changelog

Versions follow ZeroVer: `0.MINOR.PATCH`, with the major staying 0. `__version__` in `src/wikihose/__init__.py` is edited
by hand: minor for features, patch for fixes. Release tags are `v0.x.y` and match `__version__`.

`CHANGELOG.md` follows [Common Changelog](https://common-changelog.org/). Add an entry only for user-visible changes,
as one imperative line with a `(#PR)` reference under Changed, Added, Removed or Fixed, in that order. Docs, dotfiles,
dev-only dependency bumps and style edits get none.

## Vendored code

Code copied from other projects carries a comment naming the source repository and commit.

## License

By contributing you agree that your work is released under the [MIT License](LICENSE).
