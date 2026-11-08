"""Opt-in ntfy sender with fixed public and explicitly configured private roots."""

import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from finder.discogs_health_messages import health_message
from finder.discogs_notification_lock import notification_lock, recover_abandoned
from finder.discogs_notification_outbox import NotificationBinding
from finder.discogs_price_messages import validate_price_message

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
    manual_attention: bool = False
    provider_deadline: float | None = None


def provider_retry_after(raw, *, now=None):
    """Honor seconds and HTTP dates without clipping an explicit provider deadline.

    Python/SQLite finite timestamps through year 9999 are supported. A nonempty
    unparseable/out-of-range header requires a durable manual-attention hold.
    """
    if not raw:
        return 0, False
    now = time.time() if now is None else now
    try:
        if not math.isfinite(now) or now < 0:
            return 0, True
        if re.fullmatch(r"[0-9]{1,12}", raw):
            delay = int(raw)
        else:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                return 0, True
            delay = max(0, math.ceil(date.timestamp() - now))
        if now + delay > 253402300799:
            return 0, True
        return delay, False
    except (ValueError, TypeError, OverflowError):
        return 0, True


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
        validate_price_message(payload)
        return self._publish(payload)

    def send_health(self, kind, reasons=(), *, test=False):
        """Private operational messages only; caller must reserve the shared budget."""
        if not self.enabled:
            return DeliveryResult("disabled")
        if self._token is None or self._origin != PRIVATE_NTFY_ORIGIN:
            raise ValueError("Health notifications require the existing private configuration.")
        return self._publish(health_message(kind, reasons, test=test))

    def send_inventory(self, listing, release, checked_at):
        """Canonical private inventory copy; caller reserves the shared budget."""
        if not self.enabled:
            return DeliveryResult("disabled")
        if self._token is None or self._origin != PRIVATE_NTFY_ORIGIN:
            raise ValueError("Inventory notifications require existing private configuration.")
        from finder.discogs_inventory_notifications import inventory_message

        return self._publish(inventory_message(listing, release, checked_at))

    def _publish(self, payload):
        # Validated message paths share this transport and outcome classification.
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
                    raw_retry = response.headers.get("Retry-After", "")
                    response_time = time.time()
                    delay, manual = provider_retry_after(raw_retry, now=response_time)
                    deadline = response_time + delay if raw_retry and not manual else None
                    if 200 <= response.status_code < 300:
                        return DeliveryResult("accepted", delay, manual, deadline)
                    if response.status_code == 429:
                        return (
                            DeliveryResult("failed", 0, True)
                            if manual
                            else DeliveryResult("rejected", max(900, delay), False, deadline)
                        )
                    if 300 <= response.status_code < 500:
                        return DeliveryResult("failed", delay, manual, deadline)
                    return DeliveryResult("uncertain", delay, manual, deadline)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return DeliveryResult("rejected", 900)
        except httpx.HTTPError:
            return DeliveryResult("uncertain")


def deliver_one(alerts, sender=None):
    """One reserved attempt; neither default construction nor polling sends."""
    if not isinstance(sender, NtfySender) or not sender.enabled:
        return {"state": "disabled", "delivered": False}
    with notification_lock(alerts.database) as acquired:
        if not acquired:
            return {"state": "busy", "delivered": False}
        if recover_abandoned(alerts.database, alerts.clock()):
            return {"state": "uncertain_recovery", "delivered": False}
        reservation = alerts.claim(sender.binding)
        if reservation is None:
            return {"state": "idle", "delivered": False}
        if not alerts.delivery_current(reservation):
            return alerts.finish(reservation, "failed")
        # A crash releases the lock but leaves a quarantined, never-replayed lease.
        result = sender.send(reservation["payload"])
        return alerts.finish(
            reservation,
            result.outcome,
            retry_after=result.retry_after,
            manual_attention=result.manual_attention,
            provider_deadline=result.provider_deadline,
        )
