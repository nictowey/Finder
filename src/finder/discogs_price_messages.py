"""Bounded, canonical aggregate notifications; never individual-listing evidence."""

import json
import re
import unicodedata
from decimal import Decimal

from finder.discogs_rules import CURRENCIES, release_id

TITLE_BYTES = 180
PAYLOAD_BYTES = 4000  # Includes JSON escaping; leaves room for the bounded ntfy topic.
_MONEY = r"(\d{1,7}\.\d{2})"
_ERROR = "Only the fixed public price-lead message is supported."


def _shorten(text, limit):
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore") + "..."


def _public_title(title, release):
    if not isinstance(title, str):
        raise ValueError(_ERROR)
    # Provider/user titles are inert single-line text, including Unicode controls.
    title = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} else char
        for char in title
    )
    title = " ".join(title.split()) or f"Release {release}"
    return title if len(title) <= 120 else title[:117] + "..."


def _watch(currency, ceiling, any_price):
    if ceiling is not None:
        return f"Watch: item price at or below {currency} {ceiling}.\n"
    if any_price:
        return f"Watch: any price ({currency}).\n"
    return ""  # Old helper callers have not supplied a threshold; never invent one.


def _message(title, release, summary, observation, watch="", *, change=False):
    title = _public_title(title, release)
    suffix = " | " + summary
    link = f"https://www.discogs.com/sell/release/{release}"
    comparison = (
        "Compared with earlier observations in this watch period. "
        "Aggregate changes do not identify individual new listings. "
        if change
        else "This is aggregate availability, not an individual new-listing alert. "
    )
    payload = {
        "title": _shorten(title, TITLE_BYTES - len(suffix.encode("utf-8"))) + suffix,
        "message": (
            f"{title}{suffix}\nRelease {release}: {observation}.\n{watch}"
            f"{comparison}"
            "Condition and shipping are unknown; delivered total is unverified. "
            "This is a manual-review lead, not a qualified purchase.\n"
            f"{link}\nData provided by Discogs."
        ),
        "click": link,
    }
    if len(json.dumps(payload).encode("utf-8")) > PAYLOAD_BYTES:
        raise ValueError(_ERROR)
    return payload


def lead_message(title, release, currency, price, *, item_price_ceiling=None):
    """Known minimum item price; optional exact saved inclusive cap, never inferred."""
    return _message(
        title,
        release,
        f"{currency} {price} min",
        f"reported minimum item price {currency} {price}",
        _watch(currency, item_price_ceiling, False),
    )


def availability_message(title, release, *, currency=None, price=None):
    """Aggregate stock only; show a minimum only when the caller has retained it."""
    summary = f"{currency} {price} min" if price not in (None, "") else "available"
    observation = "copies reported for sale (any price)"
    if price not in (None, ""):
        observation += f"; reported minimum item price {currency} {price}"
    return _message(
        title,
        release,
        f"{summary}; any price",
        observation,
        _watch(currency, None, currency is not None),
    )


def change_message(
    title,
    release,
    currency,
    price,
    previous_price,
    count,
    previous_count,
    *,
    item_price_ceiling=None,
    any_price=False,
):
    """Observed price/count changes, not evidence that a particular copy is new."""
    changes, summary = [], []
    if price not in (None, ""):
        summary.append(f"{currency} {price} min")
    if previous_price is not None:
        if not summary:
            raise ValueError("A price change requires a current minimum item price.")
        summary[-1] += f" (was {currency} {previous_price})"
        changes.append(
            f"reported minimum item price decreased from {currency} {previous_price} "
            f"to {currency} {price}"
        )
    if previous_count is not None:
        summary.append(f"offers {previous_count} -> {count}")
        changes.append(f"reported aggregate offers increased from {previous_count} to {count}")
    if not changes:
        raise ValueError("An aggregate change requires a supported signal.")
    if previous_price is None and price not in (None, ""):
        changes.append(f"reported minimum item price {currency} {price}")
    return _message(
        title,
        release,
        "; ".join(summary),
        "; ".join(changes),
        _watch(currency, item_price_ceiling, any_price),
        change=True,
    )


def validate_price_message(payload):
    """Reconstruct the entire canonical payload before any sender network call."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"title", "message", "click"}
        or not all(isinstance(value, str) for value in payload.values())
    ):
        raise ValueError(_ERROR)
    try:
        if (
            len(payload["title"].encode("utf-8")) > TITLE_BYTES
            or len(json.dumps(payload).encode("utf-8")) > PAYLOAD_BYTES
        ):
            raise ValueError(_ERROR)
        lines = payload["message"].split("\n")
        title, separator, _ = lines[0].rpartition(" | ")
        link = re.fullmatch(
            r"https://www\.discogs\.com/sell/release/([1-9]\d{0,15})", payload["click"]
        )
        if not separator or not link or not lines[1].startswith(f"Release {link[1]}: "):
            raise ValueError(_ERROR)
        release = release_id(int(link[1]))
        observation = lines[1].removeprefix(f"Release {release}: ")
        ceiling, watch_currency, any_price = None, None, False
        if lines[2].startswith("Watch:"):
            cap = re.fullmatch(
                r"Watch: item price at or below ([A-Z]{3}) " + _MONEY + r"\.", lines[2]
            )
            unlimited = re.fullmatch(r"Watch: any price \(([A-Z]{3})\)\.", lines[2])
            if cap:
                watch_currency, ceiling = cap[1], cap[2]
                if Decimal(ceiling) <= 0:
                    raise ValueError(_ERROR)
            elif unlimited:
                watch_currency, any_price = unlimited[1], True
            else:
                raise ValueError(_ERROR)
        low = re.fullmatch(r"reported minimum item price ([A-Z]{3}) " + _MONEY + r"\.", observation)
        available = re.fullmatch(
            r"copies reported for sale \(any price\)"
            r"(?:; reported minimum item price ([A-Z]{3}) " + _MONEY + r")?\.",
            observation,
        )
        change = re.fullmatch(
            r"(?:reported minimum item price decreased from ([A-Z]{3}) "
            + _MONEY
            + r" to ([A-Z]{3}) "
            + _MONEY
            + r"(?:; )?)?"
            r"(?:reported aggregate offers increased from (\d{1,8}) to (\d{1,8}))?"
            r"(?:; reported minimum item price ([A-Z]{3}) " + _MONEY + r")?\.",
            observation,
        )
        currency = (
            (
                low[1]
                if low
                else (change[1] or change[7])
                if change
                else available[1]
                if available
                else None
            )
            or watch_currency
            or "USD"
        )
        if currency not in CURRENCIES or watch_currency not in (None, currency):
            raise ValueError(_ERROR)
        if low and not any_price:
            price = low[2]
            expected = lead_message(title, release, currency, price, item_price_ceiling=ceiling)
        elif available and ceiling is None:
            price = available[2]
            expected = availability_message(title, release, currency=watch_currency, price=price)
        elif change:
            old_price, price = change[2], change[4] or change[8]
            old_count = int(change[5]) if change[5] is not None else None
            count = int(change[6]) if change[6] is not None else None
            if (
                old_price is None
                and old_count is None
                or old_price is not None
                and (change[1] != change[3] or Decimal(price) >= Decimal(old_price))
                or old_count is not None
                and not 0 < old_count < count <= 10000000
            ):
                raise ValueError(_ERROR)
            expected = change_message(
                title,
                release,
                currency,
                price,
                old_price,
                count,
                old_count,
                item_price_ceiling=ceiling,
                any_price=any_price,
            )
        else:
            raise ValueError(_ERROR)
        if ceiling is not None and price is not None and Decimal(price) > Decimal(ceiling):
            raise ValueError(_ERROR)
        if payload != expected:
            raise ValueError(_ERROR)
    except (IndexError, TypeError, UnicodeError):
        raise ValueError(_ERROR) from None
