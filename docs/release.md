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
| | `attest` | Attests build provenance for each file. It runs in this workflow, not the shared one, because the certificate names the workflow that ran it and `gh attestation verify --signer-workflow` expects `release.yml`. |
| | `publish` | Calls the shared `publish-release.yml`: checks the file set is exactly the seven expected, writes `SHA256SUMS`, confirms the tag still points at the built commit and creates the GitHub release. Only this job can write to the repository. |
| | `pypi` | After the release exists and the `pypi` environment is approved, uploads the wheel and sdist that `build` made (not rebuilt) by trusted publishing. |
| `build.yml` | `dist` | Builds the sdist and wheel, installs each in a clean venv and runs `--version` and `--selftest`. |
| | `binary` | Calls the shared `build-binaries.yml`, which builds one binary per platform with PyInstaller (`scripts/build_binary.py`): Windows, Linux and macOS, x86_64 and arm64, each on its own kind of runner. Runs `--version`, `--selftest` and `--demo --headless-seconds 5` on the result, then names it `wikihose-<version>-<os>-<arch>` (`.exe` on Windows, `.tar.gz` elsewhere, so the executable bit survives). |
| `post-release.yml` | `published` | Calls the shared `verify-published.yml`: downloads every published file from its public URL, checks `SHA256SUMS` and each build attestation, then on a runner of each platform with nothing of ours installed runs the published binary (`--version`, `--selftest`). |
| | `pipx` | On Ubuntu and Windows runners, `pipx install wikihose==<version>` from PyPI, then `--version` and `--selftest`. |

`build.yml` also runs by hand (Actions tab, Run workflow) and on pull requests that touch the build, which is how the
build is proved without a release. It publishes nothing and has no secrets. `post-release.yml` can be run by hand
against an existing tag (input `tag`).

The shared workflows live in [WPTK/release-workflows](https://github.com/WPTK/release-workflows) (also used by the other WPTK apps) and are pinned by commit SHA in `build.yml`, `release.yml` and `post-release.yml`; to take a change, move the SHA in all three together. All third-party actions are pinned to a commit SHA. Dependencies for the builds, including the
build backend (hatchling), are pinned by `.github/release-constraints.txt`, so a dependency release between two tags cannot change what ships.

## Build a binary locally

PyInstaller cannot cross-compile, so this builds for the machine you run it on.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -c .github\release-constraints.txt . tzdata "pyinstaller>=6.22,<7"
.venv\Scripts\python scripts\build_binary.py
```

On macOS and Linux use `.venv/bin/python` and forward slashes. The script runs PyInstaller (output in `dist/`, work files
in `.pyi-build/`, both gitignored), runs `--version`, `--selftest` and `--demo --headless-seconds 5` on the result, and
prints the SHA-256.

### Platforms and compatibility

| Asset | Built on | Runs on |
| --- | --- | --- |
| `windows-x86_64.exe` | `windows-latest` | Windows 10 and 11, Intel or AMD |
| `linux-x86_64.tar.gz` | `ubuntu-latest`, inside `python:3.12-bullseye` | glibc 2.31 or newer (Ubuntu 20.04, Debian 11) |
| `linux-arm64.tar.gz` | `ubuntu-24.04-arm`, inside `python:3.12-bullseye` | the same, on 64-bit ARM |
| `macos-arm64.tar.gz` | `macos-latest` | Apple silicon; tested only on the runner's macOS version |
| `macos-x86_64.tar.gz` | `macos-15-intel` | Intel Macs. GitHub's last Intel image; `macos-13` is retired. |

The Linux builds run in a Debian 11 container, pinned by digest in `build.yml`, because a PyInstaller binary needs at
least the glibc of the machine that built it. The runner's own glibc (2.35 on Ubuntu 22.04, 2.39 on 24.04) would shut
out older systems. Move the pin on purpose, and update the glibc line in the README with it. GitHub has said it will
retire Intel macOS runners; when `macos-15-intel` goes, drop that row (and the README's) and say so in the changelog.

macOS binaries are not signed or notarized; see "Apple signing and notarization" in [deferred.md](deferred.md).

## Checking a download

```powershell
(Get-FileHash .\wikihose-0.1.0-windows-x86_64.exe -Algorithm SHA256).Hash.ToLower()   # compare with SHA256SUMS
gh attestation verify .\wikihose-0.1.0-windows-x86_64.exe --repo WPTK/wikihose --signer-workflow WPTK/wikihose/.github/workflows/release.yml
```

On macOS and Linux, `sha256sum -c SHA256SUMS --ignore-missing` (`shasum -a 256 -c SHA256SUMS` on a Mac, which has no
`--ignore-missing`; delete the lines for files you did not download first) checks the files in the folder, and
`gh attestation verify` takes the archive the same way.
