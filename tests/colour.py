"""Colour helpers for the orange-background tests.

Adapted from scripts/svgcolour.py in WPTK/wxbrief at commit 8562fc72e02f8730b7a1d9039448a137fe29e551 (the hex-in-SVG
regex idea); the orange predicate is new here.
"""

from __future__ import annotations

import colorsys
import re

HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
SVG_RECT = re.compile(r"<rect\b[^>]*>")
RECT_FILL = re.compile(r"\bfill=\"#([0-9a-fA-F]{6})\"")


def hls(digits: str) -> tuple[float, float, float]:
    """Hue in degrees, lightness and saturation (0 to 1) of ``rrggbb``."""
    r, g, b = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, lightness, s


def is_orange(colour: str) -> bool:
    """Orange means hue 15-45 degrees, saturation above 35% and lightness 20%-85%."""
    h, lightness, s = hls(colour.lstrip("#"))
    return 15 <= h <= 45 and s > 0.35 and 0.20 <= lightness <= 0.85


def orange_rect_fills(svg: str) -> list[str]:
    """Orange fills on ``<rect>`` elements; text ``fill:`` classes and ``<circle>`` dots are not looked at."""
    found = []
    for rect in SVG_RECT.findall(svg):
        for digits in RECT_FILL.findall(rect):
            if is_orange(digits):
                found.append("#" + digits.lower())
    return found
