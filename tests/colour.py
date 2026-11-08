"""Colour helpers for the orange-background tests.

Adapted from scripts/svgcolour.py in WPTK/wxbrief at commit 8562fc72e02f8730b7a1d9039448a137fe29e551 (the hex-in-SVG
regex idea); the orange predicate and the source scanner are new here.
"""

from __future__ import annotations

import colorsys
import re

from rich.color import Color as RichColor
from rich.color import ColorParseError as RichColorParseError
from textual.color import Color, ColorParseError

SVG_RECT = re.compile(r"<rect\b[^>]*>", re.IGNORECASE)
# `fill=` as its own attribute, not `data-fill=`; values in single or double quotes.
RECT_FILL_ATTR = re.compile(r"""(?<![\w-])fill\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)
RECT_STYLE_ATTR = re.compile(r"""(?<![\w-])style\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
STYLE_FILL = re.compile(r"(?<![\w-])fill\s*:\s*([^;]+)", re.IGNORECASE)

TOKEN = r"(?:rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}\b|[A-Za-z_][\w]*)"
ON_COLOUR = re.compile(r"\bon\s+(" + TOKEN + ")")
BRACKETS = re.compile(r"\[([^\[\]]*)\]")
QUOTED = re.compile(r"""(["'])((?:(?!\1).)*)\1""")
BACKGROUND = re.compile(r"background(?:-color)?\s*:\s*([^;}\n\"']+)")
BGCOLOR = re.compile(r"""bgcolor\s*=\s*(["'])(.*?)\1""")


def is_orange_hls(hue_degrees: float, lightness: float, saturation: float) -> bool:
    """Orange means hue 15-45 degrees, saturation above 35% and lightness 20%-85%."""
    return 15 <= hue_degrees <= 45 and saturation > 0.35 and 0.20 <= lightness <= 0.85


def parse_colour(text: str) -> tuple[int, int, int] | None:
    """The RGB of a CSS or Rich colour, or None for a ``$variable`` or anything unparseable."""
    text = text.strip().removesuffix("!important").strip()
    if not text or text.startswith("$"):
        return None
    try:
        c = Color.parse(text)
        return c.r, c.g, c.b
    except (ColorParseError, ValueError):
        pass
    try:
        r, g, b = RichColor.parse(text).get_truecolor()
        return r, g, b
    except (RichColorParseError, ValueError):
        return None


def rgb_is_orange(rgb: tuple[int, int, int]) -> bool:
    h, lightness, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    return is_orange_hls(h * 360, lightness, s)


def is_orange(colour: str) -> bool:
    """True when ``colour`` (any CSS or Rich spelling) parses and is orange."""
    rgb = parse_colour(colour)
    return rgb is not None and rgb_is_orange(rgb)


def orange_rect_fills(svg: str) -> list[str]:
    """Orange fills on ``<rect>`` elements, from ``fill=`` or ``style="fill:..."``.

    Text ``fill:`` classes in a ``<style>`` block and ``<circle>`` elements are not looked at.
    """
    found = []
    for rect in SVG_RECT.findall(svg):
        values = [m.group(2) for m in RECT_FILL_ATTR.finditer(rect)]
        for style in RECT_STYLE_ATTR.finditer(rect):
            values += [m.group(1) for m in STYLE_FILL.finditer(style.group(2))]
        found += [v.strip() for v in values if is_orange(v)]
    return found


def orange_backgrounds(text: str, *, kind: str) -> list[str]:
    """Orange colours used as backgrounds in source text.

    Finds ``background:`` and ``background-color:`` values, Rich ``on <colour>`` inside ``[...]`` markup or a quoted
    style string, and ``bgcolor="..."`` arguments. ``$variables`` and unparseable tokens are skipped; comment lines are
    ignored. ``kind`` is ``"py"`` or ``"tcss"``; in TCSS ``//``-free comments are ``/* */`` and are stripped.
    """
    if kind == "tcss":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    found = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        candidates = [m.group(1) for m in BACKGROUND.finditer(line)]
        candidates += [m.group(2) for m in BGCOLOR.finditer(line)]
        for bracket in BRACKETS.finditer(line):
            candidates += ON_COLOUR.findall(bracket.group(1))
        for quoted in QUOTED.finditer(line):
            candidates += ON_COLOUR.findall(quoted.group(2))
        for value in candidates:
            value = value.strip()
            if is_orange(value) or is_orange(value.split()[0] if value.split() else ""):
                found.append(value)
    return found
