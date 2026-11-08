"""The app's two Textual themes. None of their main colours is orange; see docs/design.md, "Colors"."""

from __future__ import annotations

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
