# Smoke test after a release

What the owner runs by hand after `release.yml` and `post-release.yml` are green. It checks the files a user would
actually get, in a folder that has nothing of ours in it. Windows PowerShell or PowerShell 7; every block can be pasted
as it is. Change `$v` to the version just released.

If a step fails, stop and tell the maintainer which step and what it printed. Do not delete anything on PyPI.

## 1. The Windows executable

```powershell
$v = "0.1.0"
$ProgressPreference = "SilentlyContinue"   # makes downloads fast in Windows PowerShell 5.1
$dir = Join-Path $env:TEMP "wikihose-smoke"
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir | Out-Null
Set-Location $dir
$base = "https://github.com/WPTK/wikihose/releases/download/v$v"
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS
Invoke-WebRequest "$base/wikihose-$v-windows-x86_64.exe" -OutFile wikihose.exe
```

Check the checksum. Expected output: `True`.

```powershell
$want = ((Get-Content SHA256SUMS | Select-String -SimpleMatch "-windows-x86_64.exe").Line -split "\s+")[0]
$got = (Get-FileHash .\wikihose.exe -Algorithm SHA256).Hash.ToLower()
$want -eq $got
```

Check the build attestation (needs the GitHub CLI, `gh`, signed in with `gh auth login`; an authentication error here is not a release defect). Expected: `Verification succeeded!`.

```powershell
gh attestation verify .\wikihose.exe --repo WPTK/wikihose
```

Run it. Expected: `wikihose 0.1.0` (your version), then `selftest: ok`.

```powershell
.\wikihose.exe --version
.\wikihose.exe --selftest
echo "exit code: $LASTEXITCODE"
```

The exit code line should say `0`. Windows may show a SmartScreen "unrecognized app" warning the first time, because
the exe is not code-signed; choose More info, then Run anyway. That is expected for 0.1.0.

## 2. Install from PyPI with pipx

```powershell
$v = "0.1.0"   # set again on purpose: a new window forgets it
pipx install wikihose==$v
wikihose --version
wikihose --selftest
```

Expected: the same two results as above. If `pipx` is not installed, run `python -m pip install --user pipx` and
`python -m pipx ensurepath`, then open a new window.

## 3. Open the app

Turn Wi-Fi off first for the demo. In Windows Terminal, in a plain folder:

```powershell
wikihose --demo
```

- [ ] The app opens with the network off and shows the Live tab with edits scrolling.
- [ ] Keys `1` to `5` change tab, `?` opens help, `q` quits.
- [ ] After quitting, the terminal is back to normal (you can type, and the colors are restored).

Turn Wi-Fi back on, then the live feed:

```powershell
wikihose
```

- [ ] Edits start to scroll within about ten seconds. Press `q` to quit.

## 4. The release page

Open https://github.com/WPTK/wikihose/releases and check:

- [ ] The release notes match the `CHANGELOG.md` section for this version.
- [ ] The files are the wheel, the sdist, the exe and `SHA256SUMS`, and nothing else.
- [ ] https://pypi.org/project/wikihose/ shows this version and a "Verified details" link to the repository.

## 5. Clean up

```powershell
pipx uninstall wikihose
Set-Location $env:TEMP
Remove-Item (Join-Path $env:TEMP "wikihose-smoke") -Recurse -Force
```
