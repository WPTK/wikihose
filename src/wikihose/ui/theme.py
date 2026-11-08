"""The app's two Textual themes. None of their main colours is orange; see docs/design.md, "Colors"."""

from __future__ import annotations

from textual.color import Color
from textual.theme import Theme

DARK = Theme(
    name="wikihose-dark",
    primary="#6b9eff",
    secondary="#8fb8ff",
    accent="#5fd7c4",
    warning="#e8d44d",
    error="#ff6b6b",
    success="#5fd38d",
    background="#0f1317",
    surface="#161b21",
    panel="#1e252d",
    foreground="#e3e8ee",
    dark=True,
)

LIGHT = Theme(
    name="wikihose-light",
    primary="#2a5bd7",
    secondary="#3366cc",
    accent="#00796b",
    warning="#8a7a00",
    error="#c62828",
    success="#2e7d32",
    background="#f6f7f9",
    surface="#ffffff",
    panel="#e6eaef",
    foreground="#1b2430",
    dark=False,
)

THEMES = (DARK, LIGHT)


def is_orange(colour: str) -> bool:
    """True for orange: hue 15 to 45 degrees, saturation above 35% and lightness 20% to 85% (see docs/design.md)."""
    hue, saturation, lightness = Color.parse(colour).hsl
    return 15 <= hue * 360 <= 45 and saturation > 0.35 and 0.2 <= lightness <= 0.85


def orange_roles(theme: Theme) -> list[str]:
    """The roles among primary, background, surface, panel, warning and accent that are orange (should be none)."""
    roles = ("primary", "background", "surface", "panel", "warning", "accent")
    return [role for role in roles if (value := getattr(theme, role)) and is_orange(value)]
