"""Build the one-file binary with PyInstaller, run it, and print its SHA-256.

Works the same on Windows, macOS and Linux, so one list of PyInstaller flags serves every platform.

Usage, from the repository root, in an environment with the package, tzdata and PyInstaller installed:

    python -m pip install -c .github/release-constraints.txt . tzdata "pyinstaller>=6.22,<7"
    python scripts/build_binary.py

Output: dist/wikihose (dist/wikihose.exe on Windows); work files in .pyi-build/. Both are gitignored.
The binary must pass --version, --selftest and a five-second headless demo before the script succeeds.

CI runs the two halves apart, because the shared build workflow (WPTK/release-workflows) builds, then smoke-tests:
    python scripts/build_binary.py --no-smoke     only build
    python scripts/build_binary.py --smoke-only   only run the built binary through SMOKE_TESTS
"""

import argparse
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


def smoke() -> int:
    for args in SMOKE_TESTS:
        print(f"$ {EXE.name} {' '.join(args)}", flush=True)
        if subprocess.run([str(EXE), *args], cwd=ROOT).returncode != 0:
            print(f"{' '.join(args)} failed: the frozen app is missing something", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-smoke", action="store_true", help="build only")
    group.add_argument("--smoke-only", action="store_true", help="run the already built binary through the smoke tests")
    args = parser.parse_args()
    if not args.smoke_only:
        subprocess.run(PYINSTALLER, cwd=ROOT, check=True)
    if not args.no_smoke and smoke() != 0:
        return 1
    if not args.smoke_only:
        print(f"{hashlib.sha256(EXE.read_bytes()).hexdigest()}  {EXE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
