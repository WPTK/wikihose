from __future__ import annotations

import colorsys
from pathlib import Path

import pytest
from colour import is_orange, is_orange_hls, orange_backgrounds, orange_rect_fills

from wikihose.ui.theme import THEMES

ROOT = Path(__file__).resolve().parent.parent


def hex_of(hue: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


# --- the predicate ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("colour", ["#ffa62b", "#fca834"])
def test_known_oranges_are_orange(colour: str) -> None:
    assert is_orange(colour)


@pytest.mark.parametrize("colour", ["#000000", "#808080", "#ffffff", "#e3e8ee", "#cccccc"])
def test_greys_are_not_orange(colour: str) -> None:
    assert not is_orange(colour)


@pytest.mark.parametrize(
    ("hue", "lightness", "saturation", "expected"),
    [
        (15, 0.5, 0.8, True),
        (45, 0.5, 0.8, True),
        (14, 0.5, 0.8, False),
        (46, 0.5, 0.8, False),
        (30, 0.5, 0.35, False),
        (30, 0.5, 0.36, True),
        (30, 0.20, 0.8, True),
        (30, 0.85, 0.8, True),
        (30, 0.19, 0.8, False),
        (30, 0.86, 0.8, False),
    ],
)
def test_orange_boundaries(hue: float, lightness: float, saturation: float, expected: bool) -> None:
    assert is_orange_hls(hue, lightness, saturation) is expected


@pytest.mark.parametrize(("hue", "expected"), [(30, True), (10, False), (60, False)])
def test_orange_through_hex_for_colours_built_with_colorsys(hue: float, expected: bool) -> None:
    assert is_orange(hex_of(hue, 0.5, 0.8)) is expected


@pytest.mark.parametrize("theme", THEMES, ids=lambda t: t.name)
@pytest.mark.parametrize("slot", ["primary", "background", "surface", "panel", "warning", "accent"])
def test_theme_slots_are_not_orange(theme, slot: str) -> None:
    assert not is_orange(getattr(theme, slot)), f"{theme.name}.{slot}"


# --- SVG scan --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "svg",
    [
        '<rect fill="#ffa62b" x="0"/>',
        "<rect fill='#fa0' x='0'/>",
        '<rect style="fill:#ffa62b" x="0"/>',
        '<rect style="stroke:none; fill: #ffa62b;"/>',
    ],
)
def test_svg_scan_flags_orange_rects(svg: str, tmp_path: Path) -> None:
    path = tmp_path / "orange.svg"
    path.write_text(f"<svg>{svg}</svg>", "utf-8")
    assert orange_rect_fills(path.read_text("utf-8")), svg


@pytest.mark.parametrize(
    "svg",
    [
        '<circle fill="#ffa62b" r="5"/>',
        "<style>.r1 { fill: #ffa62b }</style><text class='r1'>hi</text>",
        '<rect data-fill="#ffa62b" fill="#161b21"/>',
        '<rect fill="#161b21"/>',
    ],
)
def test_svg_scan_ignores_everything_else(svg: str) -> None:
    assert orange_rect_fills(f"<svg>{svg}</svg>") == []


def svg_files() -> list[Path]:
    found = list((ROOT / "docs").rglob("*.svg"))
    found += list((ROOT / "tests").glob("**/__snapshots__/**/*.svg"))
    return sorted(found)


def test_no_svg_has_an_orange_rect() -> None:
    bad = {str(p.relative_to(ROOT)): fills for p in svg_files() if (fills := orange_rect_fills(p.read_text("utf-8")))}
    assert not bad


# --- source scan -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Screen { background: orange; }", "tcss"),
        ("Screen { background: #fa0; }", "tcss"),
        ("Screen { background-color: rgb(255, 166, 43); }", "tcss"),
        ("Screen { background: #ffa62b 50%; }", "tcss"),
        ('DEFAULT_CSS = "Button { background: sandy_brown; }"', "py"),
        ("text = '[bold white on dark_orange]x[/]'", "py"),
        ("text = '[on orange_red1]x[/]'", "py"),
        ("text = '[on #fca834]x[/]'", "py"),
        ("text = '[on rgb(255,166,43)]x[/]'", "py"),
        ('style = "white on orange"', "py"),
        ('Static(bgcolor="orange")', "py"),
    ],
)
def test_source_scan_catches_orange_backgrounds(text: str, kind: str) -> None:
    assert orange_backgrounds(text, kind=kind), text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Screen { background: $panel; }", "tcss"),
        ("Screen { background: #161b21; }", "tcss"),
        ("Screen { background: nonsense; }", "tcss"),
        ("/* background: orange */", "tcss"),
        ("# turn on orange for this one", "py"),
        ("text = '[orange]text only[/]'", "py"),
        ("text = '[on $panel]x[/]'", "py"),
        ("text = '[bold on blue]x[/]'", "py"),
        ("x = foo.on orange", "py"),
    ],
)
def test_source_scan_leaves_the_rest_alone(text: str, kind: str) -> None:
    assert orange_backgrounds(text, kind=kind) == [], text


def test_no_source_puts_orange_behind_text() -> None:
    problems = []
    for path in sorted((ROOT / "src").rglob("*")):
        if path.suffix in {".py", ".tcss"}:
            kind = "tcss" if path.suffix == ".tcss" else "py"
            problems += [
                f"{path.relative_to(ROOT)}: {v}" for v in orange_backgrounds(path.read_text("utf-8"), kind=kind)
            ]
    assert not problems, "\n".join(problems)
