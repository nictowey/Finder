"""Bounded watch IPC schemas; only the DB-owning broker holds WatchBackend."""

import math
import re
import secrets

from finder.discogs_price_alerts import PriceLeadRule
from finder.discogs_read_guard import ReadSafetyHalt
from finder.discogs_review_broker import BrokerClient, ProtocolError, encode
from finder.discogs_rules import release_id

WATCH_REQUEST_LIMIT = 4096
WATCH_RESPONSE_LIMIT = 1048576
SAVE_FIELDS = {"release", "title", "currency", "price", "any_price", "enabled", "revision"}
PREFERENCE_FIELDS = {"action", "release", "revision", "alert_price_drops", "alert_count_increases"}
RULE_FIELDS = set(PriceLeadRule.model_fields)
SAMPLE_FIELDS = {"revision", "last_success", "lowest_item_price", "count", "status", "removed"}
REMOVAL_FIELDS = {"action", "release", "revision"}
NOTIFICATION_FIELDS = {"accepted", "accepted_current_revision", "pending", "in_flight", "uncertain"}


def save_fields(fields):
    if type(fields) is not dict or set(fields) != SAVE_FIELDS:
        raise ProtocolError("invalid_watch_fields")
    if (
        type(fields["release"]) is not str
        or len(fields["release"]) > 500
        or type(fields["title"]) is not str
        or len(fields["title"]) > 120
        or type(fields["currency"]) is not str
        or type(fields["any_price"]) is not bool
        or type(fields["enabled"]) is not bool
        or type(fields["revision"]) is not int
        or not 0 <= fields["revision"] < 2**63
        or (
            fields["price"] is not None
            and (
                type(fields["price"]) is not str
                or not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", fields["price"])
            )
        )
    ):
        raise ProtocolError("invalid_watch_fields")
    PriceLeadRule(
        release_id=release_id(fields["release"]),
        public_title=fields["title"] or f"Discogs release {release_id(fields['release'])}",
        currency=fields["currency"],
        item_price_ceiling=fields["price"],
        any_price=fields["any_price"],
        enabled=fields["enabled"],
    )
    return dict(fields)


def mutation_fields(fields):
    """Existing exact save schema or one exact, bounded lifecycle operation."""
    if type(fields) is dict and set(fields) == REMOVAL_FIELDS:
        if (
            type(fields["action"]) is not str
            or fields["action"] not in ("remove", "restore")
            or type(fields["release"]) is not str
            or not re.fullmatch(r"[1-9][0-9]{0,15}", fields["release"])
            or type(fields["revision"]) is not int
            or not 0 < fields["revision"] < 2**63 - 1
        ):
            raise ProtocolError("invalid_watch_action")
        release_id(fields["release"])
        return dict(fields)
    if type(fields) is dict and set(fields) == PREFERENCE_FIELDS:
        if (
            fields["action"] != "preferences"
            or type(fields["release"]) is not str
            or not re.fullmatch(r"[1-9][0-9]{0,15}", fields["release"])
            or type(fields["revision"]) is not int
            or not 0 < fields["revision"] < 2**63 - 1
            or type(fields["alert_price_drops"]) is not bool
            or type(fields["alert_count_increases"]) is not bool
        ):
            raise ProtocolError("invalid_watch_preferences")
        release_id(fields["release"])
        return dict(fields)
    return save_fields(fields)


def project_snapshot(data):
    """Explicit allowlist, bounded legacy stores, no internal DB/control/outbox data."""
    if (
        type(data) is not dict
        or type(data.get("monitor_enabled")) is not bool
        or type(data.get("watches")) is not list
        or len(data["watches"]) > 1000
    ):
        raise ProtocolError("invalid_watch_state")
    watches = []
    for row in data["watches"]:
        rule = PriceLeadRule.model_validate({key: row[key] for key in RULE_FIELDS})
        sample = {key: row[key] for key in SAMPLE_FIELDS}
        notifications = row["notifications"]
        if (
            type(notifications) is not dict
            or set(notifications) != NOTIFICATION_FIELDS
            or any(
                type(value) is not int or not 0 <= value < 2**63 for value in notifications.values()
            )
            or notifications["accepted_current_revision"] > notifications["accepted"]
        ):
            raise ProtocolError("invalid_notification_counts")
        if (
            type(sample["removed"]) is not bool
            or sample["removed"]
            and rule.enabled
            or type(sample["revision"]) is not int
            or not 0 < sample["revision"] < 2**63
            or (
                sample["count"] is not None
                and (type(sample["count"]) is not int or not 0 <= sample["count"] <= 10000000)
            )
            or (
                sample["last_success"] is not None
                and (
                    type(sample["last_success"]) not in (int, float)
                    or not math.isfinite(sample["last_success"])
                    or sample["last_success"] < 0
                )
            )
            or (
                sample["lowest_item_price"] is not None
                and (
                    type(sample["lowest_item_price"]) is not str
                    or len(sample["lowest_item_price"]) > 32
                    or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", sample["lowest_item_price"])
                )
            )
            or (
                sample["status"] is not None
                and (type(sample["status"]) is not str or len(sample["status"]) > 64)
            )
        ):
            raise ProtocolError("invalid_watch_sample")
        watches.append({**rule.model_dump(), **sample, "notifications": dict(notifications)})
    result = {
        "watches": watches,
        "monitor_enabled": data["monitor_enabled"],
        "purchase_available": False,
        "aggregate_only": True,
    }
    encode(result, WATCH_RESPONSE_LIMIT - 128)
    return result


class WatchBrokerClient(BrokerClient):
    request_limit = WATCH_REQUEST_LIMIT
    response_limit = WATCH_RESPONSE_LIMIT

    def _watch_call(self, request):
        try:
            return self._call(request)
        except (ReadSafetyHalt, OSError, ValueError, TypeError, KeyError):
            # Never reconnect/retry an uncertain mutation. The UI requires Refresh.
            raise RuntimeError("Watch broker unavailable; refresh before retrying") from None

    def snapshot(self):
        response = self._watch_call({"v": 1, "op": "watch_snapshot"})
        try:
            if set(response) != {"v", "ok", "snapshot"}:
                raise ProtocolError("invalid_watch_response")
            result = project_snapshot(response["snapshot"])
            if result != response["snapshot"]:
                raise ProtocolError("invalid_watch_projection")
            return result
        except (ValueError, TypeError, KeyError):
            raise RuntimeError("Watch response unavailable") from None

    def save(self, fields):
        fields = mutation_fields({key: value for key, value in fields.items() if key != "csrf"})
        request_id = secrets.token_hex(16)
        response = self._watch_call(
            {"v": 1, "op": "watch_save", "request_id": request_id, "fields": fields}
        )
        if (
            set(response) != {"v", "ok", "request_id", "revision"}
            or response["request_id"] != request_id
            or type(response["revision"]) is not int
            or not 0 < response["revision"] < 2**63
        ):
            raise RuntimeError("Watch save response unknown; refresh before retrying")
        return response["revision"]
