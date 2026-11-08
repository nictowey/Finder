"""Conservative response-derived pacing, without retaining provider payloads."""

import math
import sys
from datetime import UTC
from email.utils import parsedate_to_datetime


def retry_seconds(value, now):
    """Honor delta-seconds or HTTP-date, never shorten a valid provider deadline."""
    try:
        if value.isascii() and value.isdigit():
            # Unrepresentably large holds fail closed, never fall back to one minute.
            stripped = value.lstrip("0") or "0"
            return max(60, float(stripped)) if len(stripped) < 309 else sys.float_info.max
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(60, math.ceil(date.timestamp() - now))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 60


def quota_delay(headers, now):
    """Keep 20% reserve, cap at the existing 20/minute local rate.

    Discogs normally reports limit/used/remaining, not reset. With no valid reset,
    conservatively spread remaining capacity over a fresh 60-second window. A
    supplied reset may be epoch seconds or seconds from now; never assume an
    unknown/malformed header means extra capacity.
    """

    def integer(name):
        value = headers.get(name, "")
        return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None

    limit = integer("X-Discogs-Ratelimit")
    remaining = integer("X-Discogs-Ratelimit-Remaining")
    used = integer("X-Discogs-Ratelimit-Used")
    if limit is None or not 0 < limit <= 100000:
        return 60.0 if remaining == 0 else 3.0
    if used is not None:
        remaining = (
            min(remaining, max(0, limit - used)) if remaining is not None else max(0, limit - used)
        )
    if remaining is None or not 0 <= remaining <= limit:
        return 60.0  # Known quota with incomplete/contradictory usage: fail closed.
    reset = integer("X-Discogs-Ratelimit-Reset")
    window = max(60, reset - now if reset and reset >= 1000000000 else reset or 60)
    available = remaining - math.ceil(limit * 0.2)
    return max(3.0, window / available) if available > 0 else float(window)


def defer_shared_read(db, clock, seconds):
    """Persist a response hold before reading its body, without holding I/O locks."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE discogs_monitor_control SET next_request=max(next_request,?) WHERE id=1",
            (clock() + seconds,),
        )
