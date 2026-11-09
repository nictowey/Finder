"""Read-only operational observations, never checkout authority or live evidence.

The optional owner-only file records the last operator observation. Its enums
cannot verify a quote, clear a security stop, activate a helper, authorize an
order or select a checkout route. An old observation remains historical evidence;
the five-minute age label changes presentation only.
"""

import json
import os
import stat
import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal

MAX_RECORD_BYTES = 1024
OLD_RECORD_SECONDS = 300
CheckoutKind = Literal["unknown", "legacy_v1", "enhanced_v2"]
PaymentAccess = Literal["unknown", "blocked_browser_security_challenge"]


def _epoch(value):
    try:
        return type(value) in (int, float) and value >= 0 and isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class RecordedCheckoutStatus:
    """Bounded operator-recorded facts; no free text, URL or account fields."""

    schema: Literal["sniper_checkout_status_v1"]
    checkout_kind: CheckoutKind
    payment_access: PaymentAccess
    recorded_at: float

    def __post_init__(self):
        if (
            self.schema != "sniper_checkout_status_v1"
            or type(self.checkout_kind) is not str
            or self.checkout_kind not in {"unknown", "legacy_v1", "enhanced_v2"}
            or type(self.payment_access) is not str
            or self.payment_access not in {"unknown", "blocked_browser_security_challenge"}
            or not _epoch(self.recorded_at)
        ):
            raise ValueError("Invalid recorded checkout status.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate status field.")
        result[key] = value
    return result


def _private_regular(info):
    return (
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_uid == os.geteuid()
        and 0 < info.st_size <= MAX_RECORD_BYTES
    )


def _file_identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_status(path, now):
    if not isinstance(path, Path):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            if not _private_regular(opened):
                return None
            payload = os.read(descriptor, MAX_RECORD_BYTES + 1)
            final = os.fstat(descriptor)
            named = path.lstat()
            if (
                len(payload) != opened.st_size
                or len(payload) > MAX_RECORD_BYTES
                or _file_identity(final) != _file_identity(opened)
                or _file_identity(named) != _file_identity(opened)
            ):
                return None
        finally:
            os.close(descriptor)
        values = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        if type(values) is not dict or set(values) != {
            "schema",
            "checkout_kind",
            "payment_access",
            "recorded_at",
        }:
            return None
        record = RecordedCheckoutStatus(**values)
        return record if record.recorded_at <= now else None
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return None


def checkout_readiness(path=None, *, now=None):
    """Return generic disabled capabilities plus an optional recorded observation.

    The helper has offline tests and remains disabled for every checkout kind.
    Enhanced checkout never routes to legacy controls. An unavailable or malformed
    file reveals no payload, exception, pathname or account information.
    """
    result = {
        "schema": "sniper_checkout_readiness_v1",
        "legacy_helper": "offline_tested_disabled",
        "observed_checkout_kind": "unknown",
        "payment_access": "unknown",
        "observation_recorded_at": None,
        "observation_age_seconds": None,
        "observation_age": "unknown",
        "status_record": "unconfigured" if path is None else "unavailable",
        "manual_unpaid_order_creation": "unverified",
        "enhanced_routes_to_legacy": False,
        "live_status_checked": False,
        "order_submission_enabled": False,
        "purchase_allowed": False,
    }
    now = time.time() if now is None else now
    if not _epoch(now):
        return {**result, "status_record": "unavailable"}
    if path is None:
        return result
    record = _read_status(path, now)
    if record is None:
        return result
    age = float(now - record.recorded_at)
    return {
        **result,
        "status_record": "valid",
        "observed_checkout_kind": record.checkout_kind,
        "payment_access": record.payment_access,
        "observation_recorded_at": float(record.recorded_at),
        "observation_age_seconds": age,
        "observation_age": "old" if age >= OLD_RECORD_SECONDS else "recent",
    }
