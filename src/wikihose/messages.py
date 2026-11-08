"""Plain-words descriptions of failures, for screens to show."""

from __future__ import annotations

import math

from wikihose.net import FetchError


def describe_fetch_error(error: FetchError) -> str:
    match error.kind:
        case "down":
            return "Wikipedia could not be reached. Check your connection and try again."
        case "rate_limited":
            wait = f" in about {math.ceil(error.retry_after)} seconds" if error.retry_after else " in a moment"
            return f"Wikipedia asked wikihose to slow down. Try again{wait}."
        case "bad_data":
            return "Wikipedia sent an answer wikihose could not read."
