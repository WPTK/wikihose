# Vendored from https://github.com/WPTK/wxbrief, tests/uat_kit/__init__.py at commit 8562fc72e02f8730b7a1d9039448a137fe29e551.
"""uat_kit: a self-contained, vendorable real-terminal test kit (pywinpty/ConPTY or stdlib pty, plus pyte)."""

from .terminal import Screen, Terminal, unavailable_reason

__all__ = ["Screen", "Terminal", "unavailable_reason"]
