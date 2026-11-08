"""Which key bindings wikihose itself declares, for the help screen and for the input-inventory meta-test."""

from __future__ import annotations

from textual.binding import Binding


def declared_bindings(cls: type) -> list[Binding]:
    """The bindings ``cls`` and its wikihose base classes declare in ``BINDINGS``, one per key, in order.

    Textual's own bindings (such as Ctrl+Q on every App) are left out, so the help screen lists only our keys.
    """
    found: list[Binding] = []
    for klass in reversed(cls.__mro__):
        if not klass.__module__.startswith("wikihose"):
            continue
        declared = klass.__dict__.get("BINDINGS")
        if declared:
            found.extend(Binding.make_bindings(declared))
    return found


def help_text(binding: Binding) -> str:
    """The sentence the help screen shows: the tooltip when there is one, otherwise the footer label."""
    return binding.tooltip or binding.description


def key_label(binding: Binding) -> str:
    return binding.key_display or binding.key
