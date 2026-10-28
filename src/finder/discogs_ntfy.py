"""Disabled-by-default ntfy.sh sender. No accounts, paid tiers or topic creation UI."""

import hashlib
import re
from dataclasses import dataclass

import httpx

from finder.discogs_notification_outbox import NotificationBinding
from finder.discogs_price_alerts import PriceLeadRule, lead_message


@dataclass(frozen=True)
class DeliveryResult:
    outcome: str
    retry_after: int = 0


class NtfySender:
    def __init__(self, topic=None, *, enabled=False, transport=None):
        if type(enabled) is not bool:
            raise ValueError("An explicit delivery opt-in is required.")
        if enabled and (
            not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", topic)
        ):
            raise ValueError("Provide a private high-entropy topic through secure configuration.")
        self.enabled, self._topic, self._transport = enabled, topic, transport
        self.binding = (
            NotificationBinding("ntfy", hashlib.sha256(("ntfy:" + topic).encode()).hexdigest())
            if enabled
            else None
        )

    def __repr__(self):
        return f"NtfySender(enabled={self.enabled}, topic=<redacted>)"

    def send(self, payload):
        if not self.enabled:
            return DeliveryResult("disabled")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"title", "message", "click"}
            or not all(isinstance(v, str) for v in payload.values())
        ):
            raise ValueError("Only the fixed public price-lead message is supported.")
        match = re.fullmatch(
            r"https://www\.discogs\.com/sell/release/([1-9]\d{0,15})", payload["click"]
        )
        details = re.match(
            r"([^\x00-\x1f\x7f]{1,120})\nRelease ([1-9]\d{0,15}): reported minimum item price "
            r"([A-Z]{3}) (\d{1,7}\.\d{2})\. ",
            payload["message"],
        )
        if not match or not details or match[1] != details[2]:
            raise ValueError("Only the fixed public price-lead message is supported.")
        # Reuse the strict public-title/currency/amount validation, including zero prices.
        rule = PriceLeadRule(
            release_id=int(match[1]),
            public_title=details[1],
            currency=details[3],
            item_price_ceiling="9999999.99",
        )
        if payload != lead_message(rule.public_title, rule.release_id, rule.currency, details[4]):
            raise ValueError("Only the fixed public price-lead message is supported.")
        # JSON publishing to the fixed root keeps the topic out of HTTP URL logs.
        # Never log request bodies, response bodies or exception strings.
        try:
            with httpx.Client(
                transport=self._transport, timeout=8, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    "POST", "https://ntfy.sh/", json={"topic": self._topic, **payload}
                ) as response:
                    if 200 <= response.status_code < 300:
                        return DeliveryResult("accepted")
                    if response.status_code == 429:
                        raw = response.headers.get("Retry-After", "")
                        delay = min(86400, int(raw)) if re.fullmatch(r"[0-9]{1,6}", raw) else 900
                        return DeliveryResult("rejected", max(900, delay))
                    if 300 <= response.status_code < 500:
                        return DeliveryResult("failed")
                    return DeliveryResult("uncertain")  # A server error may follow acceptance.
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return DeliveryResult("rejected", 900)
        except httpx.HTTPError:
            return DeliveryResult("uncertain")


def deliver_one(alerts, sender=None):
    """One reserved attempt; neither default construction nor polling sends."""
    if not isinstance(sender, NtfySender) or not sender.enabled:
        return {"state": "disabled", "delivered": False}
    reservation = alerts.claim(sender.binding)
    if reservation is None:
        return {"state": "idle", "delivered": False}
    if not alerts.delivery_current(reservation):
        return alerts.finish(reservation, "failed")
    # A crash from here leaves a durable lease that expires to uncertain, never auto-retry.
    result = sender.send(reservation["payload"])
    return alerts.finish(reservation, result.outcome, retry_after=result.retry_after)
