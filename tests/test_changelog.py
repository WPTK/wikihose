"""The changelog follows Common Changelog with a gitmoji prefix on each entry, and versions follow ZeroVer."""

import re
from pathlib import Path

import pytest

import wikihose

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

# The one list of allowed gitmoji. CONTRIBUTING.md's table must match it.
ALLOWED_GITMOJI = (
    "✨",  # new feature
    "\U0001f41b",  # bug fix
    "⚡️",  # performance
    "\U0001f512️",  # security
    "\U0001f484",  # UI or style
    "♿️",  # accessibility
    "\U0001f691️",  # critical hotfix
    "\U0001f4a5",  # breaking change
    "\U0001f525",  # removal
    "\U0001f5d1️",  # deprecation
    "\U0001f310",  # localization
    "\U0001f6b8",  # usability
    "\U0001f4e6️",  # packaging or install change users can see
    "⬆️",  # user-visible dependency upgrade
)
SECTION_ORDER = ("Changed", "Added", "Removed", "Fixed")
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\S.*)$")
UNRELEASED = "Unreleased"
UNRELEASED_HEADING = "## [Unreleased]"
PR_REFERENCE = re.compile(r"\(#\d+(?:, #\d+)*\)$")

Versions = list[tuple[str, list[tuple[str, list[str]]]]]


def parse(text: str) -> Versions:
    """Return [(version, [(section, [bullet, ...]), ...]), ...] in file order.

    One ``## [Unreleased]`` heading is allowed, and only as the first heading: it holds the entries merged since the
    last release, and the release PR renames it to the new version and date.
    """
    versions: Versions = []
    for line in text.splitlines():
        if match := VERSION_HEADING.match(line):
            versions.append((match.group(1), []))
        elif line == UNRELEASED_HEADING:
            assert not versions, f"{UNRELEASED_HEADING} is only allowed as the first heading"
            versions.append((UNRELEASED, []))
        elif line.startswith("## "):
            raise AssertionError(f"malformed version heading: {line!r}")
        elif line.startswith("### "):
            assert versions, f"section before any version: {line!r}"
            versions[-1][1].append((line[4:].strip(), []))
        elif line.startswith("- "):
            assert versions and versions[-1][1], f"bullet outside a section: {line!r}"
            versions[-1][1][-1][1].append(line)
    return versions


def _versions() -> Versions:
    return parse(CHANGELOG.read_text(encoding="utf-8"))


def _released() -> Versions:
    return [entry for entry in _versions() if entry[0] != UNRELEASED]


def test_changelog_has_a_version() -> None:
    assert _released()


def test_top_numbered_version_matches_dunder_version() -> None:
    assert _released()[0][0] == wikihose.__version__


def test_version_is_zerover() -> None:
    assert wikihose.__version__.split(".")[0] == "0"
    assert all(version.split(".")[0] == "0" for version, _ in _released())


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


RELEASED = "## [0.2.0] - 2026-12-01\n\n### Added\n\n- ✨ Add a thing (#1)\n"
PENDING = f"{UNRELEASED_HEADING}\n\n### Added\n\n- ✨ Add another thing (#2)\n"


def test_an_unreleased_section_is_allowed_first() -> None:
    parsed = parse(f"# Changelog\n\n{PENDING}\n{RELEASED}")
    assert [name for name, _ in parsed] == [UNRELEASED, "0.2.0"]
    assert parsed[0][1] == [("Added", ["- ✨ Add another thing (#2)"])]


def test_an_unreleased_section_anywhere_but_first_is_refused() -> None:
    with pytest.raises(AssertionError, match="only allowed as the first heading"):
        parse(f"{RELEASED}\n{PENDING}")
    with pytest.raises(AssertionError, match="only allowed as the first heading"):
        parse(f"{PENDING}\n{PENDING}")


@pytest.mark.parametrize(
    "heading", ["## Unreleased", "## [Unreleased] - 2026-12-01", "## [0.2] - 2026-12-01", "## [Next]", "## [0.2.0]"]
)
def test_other_headings_are_still_refused(heading: str) -> None:
    with pytest.raises(AssertionError, match="malformed version heading"):
        parse(f"{heading}\n")


def test_contributing_table_lists_the_allowed_gitmoji() -> None:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    section = text.split("## Versions and the changelog", 1)[1].split("\n## ", 1)[0]
    listed = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] not in ("Emoji", "---"):
            listed.append(cells[0])
    assert listed == list(ALLOWED_GITMOJI)
