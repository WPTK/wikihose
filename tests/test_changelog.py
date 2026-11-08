"""The changelog follows Common Changelog with a gitmoji prefix on each entry, and versions follow ZeroVer."""

import re
from pathlib import Path

import wikihose

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

# The one list of allowed gitmoji. CONTRIBUTING.md's table must match it.
ALLOWED_GITMOJI = (
    "\u2728",  # new feature
    "\U0001f41b",  # bug fix
    "\u26a1\ufe0f",  # performance
    "\U0001f512\ufe0f",  # security
    "\U0001f484",  # UI or style
    "\u267f\ufe0f",  # accessibility
    "\U0001f691\ufe0f",  # critical hotfix
    "\U0001f4a5",  # breaking change
    "\U0001f525",  # removal
    "\U0001f5d1\ufe0f",  # deprecation
    "\U0001f310",  # localization
    "\U0001f6b8",  # usability
    "\U0001f4e6\ufe0f",  # packaging or install change users can see
    "\u2b06\ufe0f",  # user-visible dependency upgrade
)
SECTION_ORDER = ("Changed", "Added", "Removed", "Fixed")
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\S.*)$")
PR_REFERENCE = re.compile(r"\(#\d+(?:, #\d+)*\)$")


def _versions() -> list[tuple[str, list[tuple[str, list[str]]]]]:
    """Return [(version, [(section, [bullet, ...]), ...]), ...] in file order."""
    versions: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if match := VERSION_HEADING.match(line):
            versions.append((match.group(1), []))
        elif line.startswith("## "):
            raise AssertionError(f"malformed version heading: {line!r}")
        elif line.startswith("### "):
            assert versions, f"section before any version: {line!r}"
            versions[-1][1].append((line[4:].strip(), []))
        elif line.startswith("- "):
            assert versions and versions[-1][1], f"bullet outside a section: {line!r}"
            versions[-1][1][-1][1].append(line)
    return versions


def test_changelog_has_a_version() -> None:
    assert _versions()


def test_top_version_matches_dunder_version() -> None:
    assert _versions()[0][0] == wikihose.__version__


def test_version_is_zerover() -> None:
    assert wikihose.__version__.split(".")[0] == "0"
    assert all(version.split(".")[0] == "0" for version, _ in _versions())


def test_sections_are_in_order() -> None:
    for version, sections in _versions():
        names = [name for name, _ in sections]
        assert all(name in SECTION_ORDER for name in names), f"{version}: unknown section in {names}"
        assert names == sorted(names, key=SECTION_ORDER.index), f"{version}: sections out of order: {names}"
        assert len(set(names)) == len(names), f"{version}: repeated section"


def test_every_entry_has_a_gitmoji_and_a_pr_reference() -> None:
    count = 0
    for version, sections in _versions():
        for _, bullets in sections:
            for bullet in bullets:
                count += 1
                text = bullet[2:]
                assert any(text.startswith(f"{emoji} ") for emoji in ALLOWED_GITMOJI), (
                    f"{version}: entry must start with an allowed gitmoji and a space: {bullet!r}"
                )
                assert PR_REFERENCE.search(text), f"{version}: entry must end with (#N): {bullet!r}"
    assert count


def test_contributing_table_lists_the_allowed_gitmoji() -> None:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    section = text.split("## Versions and the changelog", 1)[1].split("\n## ", 1)[0]
    listed = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] not in ("Emoji", "---"):
            listed.append(cells[0])
    assert listed == list(ALLOWED_GITMOJI)
