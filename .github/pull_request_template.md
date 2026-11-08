## Purpose

<!-- Why this change: the problem it solves or, for a fix, the root cause. -->

## What changed

<!-- The change in a few lines. Mention anything removed. -->

## How it was tested

<!-- Commands run, new or updated tests, manual checks. For interface changes, drive the flow in the running app
(Pilot or --replay) and attach a screenshot. -->

Closes #

## Checklist

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy`
- [ ] `vulture --min-confidence 80`
- [ ] `pytest -n auto`
- [ ] Changed snapshot SVGs read before committing, if any changed
- [ ] `CHANGELOG.md` entry, if the change is user-visible
