"""Build the one-file binary with PyInstaller, run it, and print its SHA-256.

Works the same on Windows, macOS and Linux, so one list of PyInstaller flags serves every platform.

Usage, from the repository root, in an environment with the package, tzdata and PyInstaller installed:

    python -m pip install -c .github/release-constraints.txt . tzdata "pyinstaller>=6.22,<7"
    python scripts/build_binary.py

Output: dist/wikihose (dist/wikihose.exe on Windows); work files in .pyi-build/. Both are gitignored.
The binary must pass --version, --selftest and a five-second headless demo before the script succeeds.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / ("wikihose.exe" if sys.platform == "win32" else "wikihose")

PYINSTALLER = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--name",
    "wikihose",
    "--paths",
    "src",
    "--workpath",
    ".pyi-build",
    "--distpath",
    "dist",
    "--specpath",
    ".pyi-build",
    "--collect-data",
    "textual",
    "--collect-data",
    "tzdata",
    "--collect-data",
    "wikihose",
    "--collect-submodules",
    "wikihose",
    "--collect-submodules",
    "textual.widgets",
    "--exclude-module",
    "tkinter",
    "--exclude-module",
    "pytest",
    "--exclude-module",
    "mypy",
    "src/wikihose/__main__.py",
]

# What every binary must do before it is named and uploaded. The demo plays a recording with no screen and no network.
SMOKE_TESTS = [
    ["--version"],
    ["--selftest"],
    ["--demo", "--headless-seconds", "5"],
]


def main() -> int:
    subprocess.run(PYINSTALLER, cwd=ROOT, check=True)
    for args in SMOKE_TESTS:
        print(f"$ {EXE.name} {' '.join(args)}", flush=True)
        if subprocess.run([str(EXE), *args], cwd=ROOT).returncode != 0:
            print(f"{' '.join(args)} failed: the frozen app is missing something", file=sys.stderr)
            return 1
    print(f"{hashlib.sha256(EXE.read_bytes()).hexdigest()}  {EXE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
