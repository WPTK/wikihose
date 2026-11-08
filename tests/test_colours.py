from __future__ import annotations

import re
from pathlib import Path

import pytest
from colour import HEX, is_orange, orange_rect_fills

from wikihose.ui.theme import THEMES

ROOT = Path(__file__).resolve().parent.parent
NAMED_ORANGE = ("orange", "dark_orange", "orange1", "orange3", "dark_orange3", "orange4", "darkorange")
ON_NAMED = re.compile(r"\bon\s+(" + "|".join(NAMED_ORANGE) + r")\b")
ON_HEX = re.compile(r"\bon\s+(#[0-9a-fA-F]{6})\b")
TCSS_BACKGROUND = re.compile(r"background(?:-color)?\s*:\s*([^;}\n]+)")


@pytest.mark.parametrize("colour", ["#ffa62b", "#fca834"])
def test_known_oranges_are_orange(colour: str) -> None:
    assert is_orange(colour)


@pytest.mark.parametrize("colour", ["#000000", "#808080", "#ffffff", "#e3e8ee", "#cccccc"])
def test_greys_are_not_orange(colour: str) -> None:
    assert not is_orange(colour)


def test_theme_colours_are_not_orange_by_the_predicate_itself() -> None:
    for theme in THEMES:
        for value in (theme.primary, theme.warning, theme.accent, theme.error, theme.success):
            assert not is_orange(value)


@pytest.mark.parametrize("theme", THEMES, ids=lambda t: t.name)
@pytest.mark.parametrize("slot", ["primary", "background", "surface", "panel", "warning", "accent"])
def test_theme_slots_are_not_orange(theme, slot: str) -> None:
    assert not is_orange(getattr(theme, slot)), f"{theme.name}.{slot}"


def test_svg_check_finds_an_orange_rect_and_ignores_text_and_circles() -> None:
    bad = '<rect fill="#fca834" x="0"/>'
    ok = '<circle fill="#ffa62b" r="5"/><style>.r1 { fill: #ffa62b }</style><rect fill="#161b21"/>'
    assert orange_rect_fills(bad) == ["#fca834"]
    assert orange_rect_fills(ok) == []


def svg_files() -> list[Path]:
    found = list((ROOT / "docs").rglob("*.svg"))
    found += list((ROOT / "tests").glob("**/__snapshots__/**/*.svg"))
    return sorted(found)


def test_no_svg_has_an_orange_rect() -> None:
    bad = {str(p.relative_to(ROOT)): fills for p in svg_files() if (fills := orange_rect_fills(p.read_text("utf-8")))}
    assert not bad


def source_files(*suffixes: str) -> list[Path]:
    return sorted(p for p in (ROOT / "src").rglob("*") if p.is_file() and p.suffix in suffixes)


def test_no_markup_puts_orange_behind_text() -> None:
    problems = []
    for path in source_files(".py", ".tcss"):
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            hit = ON_NAMED.search(line)
            if hit is None:
                hit = next((m for m in ON_HEX.finditer(line) if is_orange(m.group(1))), None)
            if hit is None and path.suffix == ".tcss":
                for m in TCSS_BACKGROUND.finditer(line):
                    value = m.group(1).strip()
                    if value.split()[0] in NAMED_ORANGE or any(is_orange(h) for h in HEX.findall(value)):
                        hit = m
            if hit is not None:
                problems.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not problems, "\n".join(problems)


def test_source_grep_catches_what_it_should() -> None:
    assert ON_NAMED.search("[bold on dark_orange]x[/]")
    assert ON_NAMED.search("style='white on orange'")
    assert not ON_NAMED.search("[orange]text only[/]")
