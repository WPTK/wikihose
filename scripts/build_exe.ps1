# Builds the one-file Windows executable with PyInstaller, then runs --version and --selftest on it.
# Usage (from the repository root, in an environment with the package and PyInstaller installed):
#   .venv\Scripts\python -m pip install -c .github\release-constraints.txt . "pyinstaller>=6.22,<7"
#   scripts\build_exe.ps1 [-Python .venv\Scripts\python.exe]
# Output: dist\wikihose.exe (work files in .pyi-build\, both gitignored).
param([string]$Python = "python")
$ErrorActionPreference = "Stop"

& $Python -m PyInstaller --noconfirm --clean --onefile --console `
    --name wikihose `
    --paths src `
    --workpath .pyi-build `
    --distpath dist `
    --specpath .pyi-build `
    --collect-data textual `
    --collect-data tzdata `
    --collect-data wikihose `
    --collect-submodules wikihose `
    --collect-submodules textual.widgets `
    --exclude-module tkinter `
    --exclude-module pytest `
    --exclude-module mypy `
    src/wikihose/__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path (Get-Location) "dist\wikihose.exe"
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "--version failed" }
& $exe --selftest
if ($LASTEXITCODE -ne 0) { throw "--selftest failed: the frozen app is missing something" }
(Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() + "  wikihose.exe"
