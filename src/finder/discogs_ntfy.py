"""Opt-in ntfy sender with fixed public and explicitly configured private roots."""

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from decimal import Decimal

import httpx

from finder.discogs_aggregate_signals import change_message
from finder.discogs_notification_outbox import NotificationBinding
from finder.discogs_price_alerts import PriceLeadRule, availability_message, lead_message

PRIVATE_NTFY_ORIGIN = "https://vps-a0c5b746.vps.ovh.us"


class PrivateNtfyConfig:
    """Existing owner-only configuration, never created or repaired by the worker."""

    def __init__(self, path):
        error = "Private notification configuration unavailable."
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                ):
                    raise ValueError(error)
                raw = source.read(4097)
            if len(raw) > 4096:
                raise ValueError(error)
            config = json.loads(raw)
            if (
                not isinstance(config, dict)
                or set(config) != {"origin", "topic", "token"}
                or config["origin"] != PRIVATE_NTFY_ORIGIN
                or not isinstance(config["topic"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", config["topic"])
                or not isinstance(config["token"], str)
                or not re.fullmatch(r"tk_[A-Za-z0-9]{16,128}", config["token"])
            ):
                raise ValueError(error)
            self._origin, self._topic, self._token = (
                config["origin"],
                config["topic"],
                config["token"],
            )
        except (OSError, ValueError, UnicodeError):
            raise ValueError(error) from None

    def __repr__(self):
        return "PrivateNtfyConfig(<redacted>)"


@dataclass(frozen=True)
class DeliveryResult:
    outcome: str
    retry_after: int = 0


class NtfySender:
    def __init__(self, topic=None, *, enabled=False, transport=None, private_config=None):
        if type(enabled) is not bool:
            raise ValueError("An explicit delivery opt-in is required.")
        if private_config is not None:
            if not isinstance(private_config, PrivateNtfyConfig) or topic is not None:
                raise ValueError("Use one notification destination configuration.")
            topic = private_config._topic
        self._origin = private_config._origin if private_config else "https://ntfy.sh"
        self._token = private_config._token if private_config else None
        if enabled and (
            not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", topic)
        ):
            raise ValueError("Provide a private high-entropy topic through secure configuration.")
        self.enabled, self._topic, self._transport = enabled, topic, transport
        # Preserve the legacy binding; private credentials conservatively define identity.
        # A token rotation requires explicit destination rebinding, never implicit replay.
        identity = (
            json.dumps([self._origin, topic, self._token], separators=(",", ":"))
            if private_config
            else "ntfy:" + topic
            if enabled
            else ""
        )
        self.binding = (
            NotificationBinding("ntfy", hashlib.sha256(identity.encode()).hexdigest())
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
        availability = re.match(
            r"([^\x00-\x1f\x7f]{1,120})\nRelease ([1-9]\d{0,15}): "
            r"copies reported for sale \(any price\)\. ",
            payload["message"],
        )
        change = re.match(
            r"([^\x00-\x1f\x7f]{1,120})\nRelease ([1-9]\d{0,15}): "
            r"(?:reported minimum item price decreased from ([A-Z]{3}) (\d{1,7}\.\d{2}) "
            r"to ([A-Z]{3}) (\d{1,7}\.\d{2})(?:; )?)?"
            r"(?:reported aggregate offers increased from (\d{1,8}) to (\d{1,8}))?\. ",
            payload["message"],
        )
        if not match or not (details or availability or change):
            raise ValueError("Only the fixed public price-lead message is supported.")
        fields = details or availability or change
        if match[1] != fields[2]:
            raise ValueError("Only the fixed public price-lead message is supported.")
        # Strict public-title/currency validation; no arbitrary price ceiling.
        rule = PriceLeadRule(
            release_id=int(match[1]),
            public_title=fields[1],
            currency=details[3] if details else change[3] if change and change[3] else "USD",
            any_price=True,
        )
        expected = (
            lead_message(rule.public_title, rule.release_id, rule.currency, details[4])
            if details
            else availability_message(rule.public_title, rule.release_id)
        )
        if change:
            previous_price, price = change[4], change[6]
            previous_count = int(change[7]) if change[7] is not None else None
            count = int(change[8]) if change[8] is not None else None
            if (
                previous_price is None
                and previous_count is None
                or previous_price is not None
                and (change[3] != change[5] or Decimal(price) >= Decimal(previous_price))
                or previous_count is not None
                and not 0 < previous_count < count <= 10000000
            ):
                raise ValueError("Only the fixed public price-lead message is supported.")
            expected = change_message(
                rule.public_title,
                rule.release_id,
                rule.currency,
                price,
                previous_price,
                count,
                previous_count,
            )
        if payload != expected:
            raise ValueError("Only the fixed public price-lead message is supported.")
        # JSON publishing to the fixed root keeps the topic out of HTTP URL logs.
        # Never log request bodies, response bodies or exception strings.
        try:
            with httpx.Client(
                transport=self._transport, timeout=8, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    "POST",
                    self._origin + "/",
                    json={"topic": self._topic, **payload},
                    headers={"Authorization": "Bearer " + self._token} if self._token else {},
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
