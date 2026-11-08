# Releasing wikihose

Versions are ZeroVer (`0.MINOR.PATCH`), edited by hand in `src/wikihose/__init__.py`. A release is a tag `v0.x.y` on
`main`. Pushing the tag is the only thing that starts `release.yml`: branches, pull requests and manual runs never
publish. Tag only when the owner says so.

## One-time setup at the release gate (the owner)

These are done on pypi.org and github.com, never by a workflow:

1. On GitHub, create an environment named `pypi` (Settings, Environments). Add the owner as a required reviewer, so
   the PyPI upload waits for a click.
2. On pypi.org, add a pending trusted publisher for the project `wikihose`: owner `WPTK`, repository `wikihose`,
   workflow `release.yml`, environment `pypi`.

3. Restrict who and what can release. The workflow's own checks run from the tagged commit, so they only help if the
   tag itself is controlled:
   - On the `pypi` environment, set "Deployment branches and tags" to selected tags with the pattern `v*`, and turn off
     "Allow administrators to bypass".
   - Add a tag ruleset on `refs/tags/v*` that limits creating, updating and deleting those tags to the owner.
   - Keep branch protection on `main`.

There are no API tokens or repository secrets anywhere. PyPI trusts the workflow's identity (OIDC) instead.

## Cut a release

1. Set `__version__` in `src/wikihose/__init__.py` if it is not already the version to ship.
2. In `CHANGELOG.md`, change the heading to `## [0.x.y] - YYYY-MM-DD` (today's date). The text under it becomes the
   release notes. A heading that still says `Unreleased` stops the release.
3. When a dependency moved, regenerate `.github/release-constraints.txt` (the command is at the top of the file) and
   commit it with the version.
4. Optionally check the Windows executable locally (below).
5. Merge that PR once CI is green. Then, on `main` at the merged commit: `git tag v0.x.y`, then
   `git push origin v0.x.y`.
6. Watch the run in the Actions tab and approve the `pypi` environment when asked. When it is green, `post-release.yml`
   starts by itself. Then do [smoke-test.md](smoke-test.md).

If the run fails before the `publish` job, nothing was released: fix the cause, delete the tag
(`git push origin :refs/tags/v0.x.y`, `git tag -d v0.x.y`) and tag again. If a release was created and is bad, mark it
as a draft or delete it and fix forward with a patch version. If only the `pypi` job failed (the GitHub release exists but PyPI has nothing), fix the cause and use "Re-run failed
jobs" on the run; do not retag. PyPI refuses a second upload of the same version.

## What the workflows do

| Workflow | Job | What it does |
| --- | --- | --- |
| `release.yml` | `verify` | The tagged commit must be on `main` and have a successful `ci.yml` run. The tag must be `v0.MINOR.PATCH`, equal `v` plus `__version__`, and `CHANGELOG.md` must have a dated section with entries. That section becomes the release notes. |
| | `build` | Calls `build.yml` (below). |
| | `publish` | Collects the files, writes `SHA256SUMS`, attests build provenance for each file, and creates the GitHub release. Only this job can write to the repository. |
| | `pypi` | After the release exists and the `pypi` environment is approved, uploads the wheel and sdist that `build` made (not rebuilt) by trusted publishing. |
| `build.yml` | `dist` | Builds the sdist and wheel, installs each in a clean venv and runs `--version` and `--selftest`. |
| | `exe` | Builds `wikihose.exe` with PyInstaller (`scripts/build_exe.ps1`) and runs `--version` and `--selftest` on it. |
| `post-release.yml` | `assets` | Downloads every published file from its public URL, checks `SHA256SUMS` and each build attestation. |
| | `pipx` | On Ubuntu and Windows runners, `pipx install wikihose==<version>` from PyPI, then `--version` and `--selftest`. |
| | `exe` | On a Windows runner with nothing of ours installed, downloads the published exe, checks its checksum, then runs `--version` and `--selftest`. |

`build.yml` also runs by hand (Actions tab, Run workflow) and on pull requests that touch the build, which is how the
build is proved without a release. It publishes nothing and has no secrets. `post-release.yml` can be run by hand
against an existing tag (input `tag`).

All third-party actions are pinned to a commit SHA. Dependencies for the builds, including the
build backend (hatchling), are pinned by `.github/release-constraints.txt`, so a dependency release between two tags cannot change what ships.

## Build the Windows executable locally

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -c .github\release-constraints.txt . "pyinstaller>=6.22,<7"
scripts\build_exe.ps1 -Python .venv\Scripts\python.exe
```

The script runs PyInstaller (output in `dist\`, work files in `.pyi-build\`, both gitignored), runs
`dist\wikihose.exe --version` and `--selftest`, and prints the SHA-256.

## Checking a download

```powershell
(Get-FileHash .\wikihose-0.1.0-windows-x86_64.exe -Algorithm SHA256).Hash.ToLower()   # compare with SHA256SUMS
gh attestation verify .\wikihose-0.1.0-windows-x86_64.exe --repo WPTK/wikihose --signer-workflow WPTK/wikihose/.github/workflows/release.yml
```
