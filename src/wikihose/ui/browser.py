"""Opening a page in the reader's web browser, and saying so plainly when that cannot work.

Textual's ``App.open_url`` hands the address to the driver, which calls ``webbrowser.open`` and ignores the answer. Over
SSH that opens a browser on the far machine or none at all, and a text browser such as lynx would take over this
terminal. So the check comes first, and when it fails the reader gets the address to copy instead.
"""

from __future__ import annotations

import os
import webbrowser
from typing import Any

from textual.app import App

SSH_VARIABLES = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
TEXT_BROWSERS = frozenset({"lynx", "w3m", "links", "elinks", "www-browser"})
NO_BROWSER_SECONDS = 15
OPENING_SECONDS = 3


def can_open_browser() -> bool:
    """Whether opening an address would show a window to the reader: not over SSH, not with a browser that runs in
    this terminal, and only when the system knows of a browser at all.
    """
    if any(os.environ.get(name) for name in SSH_VARIABLES):
        return False
    try:
        browser = webbrowser.get()
    except webbrowser.Error:
        return False
    if isinstance(browser, webbrowser.GenericBrowser | webbrowser.BackgroundBrowser):
        name = os.path.basename(str(getattr(browser, "name", ""))).lower()
        if name.removesuffix(".exe") in TEXT_BROWSERS:
            return False
    return True


def open_in_browser(app: App[Any], url: str | None, what: str) -> None:
    """Open ``url`` in the default browser and say so, or say why it could not be done. ``what`` names the thing."""
    if url is None:
        app.notify(f"There is no web address for {what}.", timeout=4, markup=False)
    elif not can_open_browser():
        app.notify(f"No browser can be opened here. The address is:\n{url}", timeout=NO_BROWSER_SECONDS, markup=False)
    else:
        app.open_url(url)
        app.notify(f"Opening {what} in your browser.", timeout=OPENING_SECONDS, markup=False)
