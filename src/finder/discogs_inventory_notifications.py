"""Default-off private manual-review delivery. No discovery, refresh or purchases.

An attempt is at most once: even known rejection is terminal in this narrow bridge.
Provider acceptance is not device delivery. Provision explicitly before opting in.
"""

import hashlib
import math
import re
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal

from finder.discogs_inventory import GRADES, _connect, _control, _state
from finder.discogs_notification_lock import notification_lock, recover_abandoned
from finder.discogs_ntfy import NtfySender
from finder.discogs_price_alerts import DAILY_SEND_CAP

FRESH_SECONDS = 120
TABLE = "discogs_inventory_notification_outbox"
CHECKS = {
    "release_matches",
    "item_strictly_under",
    "reported_for_sale",
    "comments_supported",
    "no_explicit_media_omission",
    "no_disclosed_media_defect",
    "media_at_least_minimum",
    "sleeve_at_least_minimum",
    "seed_seller_matches",
}


def inventory_message(listing, release, checked_at, *, test=False):
    """Canonical inert copy; no provider text, arbitrary URLs or price promises."""
    if type(test) is not bool:
        raise ValueError("Use an explicit test mode.")
    if any(type(v) is not int or not 0 < v <= 9007199254740991 for v in (listing, release)):
        raise ValueError("Invalid inventory identity.")
    if type(checked_at) not in (int, float) or not math.isfinite(checked_at) or checked_at < 0:
        raise ValueError("Invalid observation time.")
    stamp = datetime.fromtimestamp(checked_at, UTC).isoformat()
    link = f"https://www.discogs.com/sell/item/{listing}"
    introduction = (
        "TEST ONLY: Existing snapshot listing; this is not a newly observed listing alert. "
        if test
        else "Newly observed in your monitored seller's inventory, not proven newly listed. "
    )
    return {
        "title": "TEST: snapshot listing review"
        if test
        else "Newly observed inventory review lead",
        "message": (
            f"Listing {listing}; release {release}. Reported item criteria matched at {stamp}.\n"
            f"{introduction}"
            "Coverage is one explicitly monitored seller only. Availability may change. "
            "Shipping, tax, fees, physical pressing and full contents remain unverified. "
            "Manual review required; not a qualified purchase.\n"
            f"{link}\nData provided by Discogs."
        ),
        "click": link,
    }


def initialize_notifications(database, config):
    """Explicit additive provisioning; never enables a sender or resets events."""
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _control(db)
        _state(db, config)
        db.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} ("
            "event_key TEXT PRIMARY KEY, config_hash TEXT NOT NULL, "
            "control_revision INTEGER NOT NULL, listing_id INTEGER NOT NULL, "
            "release_id INTEGER NOT NULL, checked_at REAL NOT NULL, "
            "state TEXT NOT NULL, token TEXT NOT NULL, destination_ref TEXT NOT NULL, "
            "lease_until REAL, outcome TEXT)"
        )


class InventoryNotifications:
    def __init__(
        self, database, config, *, enabled=False, clock=time.time, test_listing=None, test_key=None
    ):
        if type(enabled) is not bool:
            raise ValueError("Explicit delivery opt-in required.")
        if (test_listing is None) != (test_key is None):
            raise ValueError("A test requires both an exact snapshot listing and a proof key.")
        if test_key is not None and (
            not isinstance(test_key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", test_key)
            or type(test_listing) is not int
            or not 0 < test_listing <= 9007199254740991
        ):
            raise ValueError("Use a bounded explicit proof key and exact snapshot listing.")
        self.test_listing, self.test_key = test_listing, test_key
        self.database, self.config, self.enabled, self.clock = database, config, enabled, clock

    def _eligible(self, db, now):
        _control(db)
        state = _state(db, self.config)
        if (
            state["denied"]
            or state["in_progress"]
            or (self.test_key is None and not state["baseline"])
            or state["seed_verified_at"] is None
            or state["seed_verified_at"] > now
        ):
            return []
        origin = "snapshot" if self.test_key is not None else "newly_observed"
        targets = {t.release_id: t for t in self.config.targets}
        valid = []
        for row in state["results"]:
            detail = row.get("result")
            target = targets[row["release_id"]]
            key = hashlib.sha256(
                f"{self.config.fingerprint}:{origin}:{row['listing_id']}".encode()
            ).hexdigest()
            if (
                row["origin"] != origin
                or (self.test_listing is not None and row["listing_id"] != self.test_listing)
                or row["state"] != "review_ready"
                or row.get("event_key") != key
                or not 0 <= now - row["checked_at"] < FRESH_SECONDS
                or not 0 <= row["discovered_at"] <= row["checked_at"]
                or not isinstance(detail, dict)
            ):
                continue
            checks = detail.get("checks", {})
            price = detail.get("price")
            if (
                detail.get("source") != "official_public_listing_get"
                or detail.get("freshness") != "detail_observation_only"
                or detail.get("detail_recheck_required") is not False
                or detail.get("item_criteria_match") is not True
                or detail.get("purchase_qualified") is not False
                or detail.get("eligible_for_notification") is not False
                or detail.get("listing_id") != str(row["listing_id"])
                or detail.get("release_id") != target.release_id
                or detail.get("target_release_id") != target.release_id
                or not isinstance(checks, dict)
                or set(checks) != CHECKS
                or any(v is not True for v in checks.values())
                or not isinstance(price, dict)
                or price.get("currency") != target.currency
            ):
                continue
            try:
                if not 0 <= Decimal(price["display_value"]) < Decimal(target.under):
                    continue
                if any(
                    detail.get(name) not in GRADES
                    or GRADES.index(detail[name]) < GRADES.index(getattr(target, "min_" + name))
                    for name in ("media", "sleeve")
                ):
                    continue
                stamp = datetime.fromisoformat(detail["observed_at"])
                if stamp.tzinfo is None or abs(stamp.timestamp() - row["checked_at"]) > 1:
                    continue
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
            if self.test_key is not None:
                proof_key = (
                    "test:"
                    + hashlib.sha256(
                        f"inventory-proof-v1:{self.config.fingerprint}:{self.test_key}".encode()
                    ).hexdigest()
                )
                # Preserve source origin and identity; only the separate delivery key changes.
                row = dict(row, event_key=proof_key, source_event_key=row["event_key"])
            valid.append(row)
        return valid

    def claim(self, sender):
        now = self.clock()
        with closing(_connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            candidates = self._eligible(db, now)
            day, used, next_send = db.execute(
                "SELECT send_day,sends,next_send FROM discogs_price_control WHERE id=1"
            ).fetchone()
            if (
                day > int(now // 86400)
                or next_send > now
                or day == int(now // 86400)
                and used >= DAILY_SEND_CAP
            ):
                return None
            for row in candidates:
                if db.execute(
                    f"SELECT 1 FROM {TABLE} WHERE event_key=?", (row["event_key"],)
                ).fetchone():
                    continue
                token = uuid.uuid4().hex
                db.execute(
                    f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,'leased',?,?,?,NULL)",
                    (
                        row["event_key"],
                        self.config.fingerprint,
                        _control(db)[1],
                        row["listing_id"],
                        row["release_id"],
                        row["checked_at"],
                        token,
                        sender.binding.destination_ref,
                        now + FRESH_SECONDS,
                    ),
                )
                db.execute(
                    "UPDATE discogs_price_control SET send_day=?,sends=?,next_send=? WHERE id=1",
                    (int(now // 86400), used + 1 if day == int(now // 86400) else 1, now + 90),
                )
                return dict(row, token=token, destination_ref=sender.binding.destination_ref)
        return None

    def delivery_current(self, claim):
        now = self.clock()
        with closing(_connect(self.database)) as db:
            row = db.execute(
                f"SELECT state,token,destination_ref,control_revision,lease_until "
                f"FROM {TABLE} WHERE event_key=?",
                (claim["event_key"],),
            ).fetchone()
            return bool(
                row
                and row[:3] == ("leased", claim["token"], claim["destination_ref"])
                and row[3] == _control(db)[1]
                and row[4] > now
                and db.execute("SELECT next_send FROM discogs_price_control WHERE id=1").fetchone()[
                    0
                ]
                <= row[4] - FRESH_SECONDS + 90
                and any(
                    r["event_key"] == claim["event_key"] and r["checked_at"] == claim["checked_at"]
                    for r in self._eligible(db, now)
                )
            )

    def finish(self, claim, result):
        if result.outcome not in {"accepted", "rejected", "failed", "uncertain"}:
            raise ValueError("Invalid delivery outcome.")
        now = self.clock()
        with closing(_connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT state,lease_until FROM {TABLE} WHERE event_key=? AND token=? "
                "AND destination_ref=?",
                (claim["event_key"], claim["token"], claim["destination_ref"]),
            ).fetchone()
            if row is None:
                raise ValueError("Reservation changed; never replay.")
            if row[1] is None:
                return {"state": row[0], "delivered": False}
            deadline = max(now + 90, now + result.retry_after, result.provider_deadline or 0)
            if result.manual_attention:
                deadline = math.inf
            db.execute(
                "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                (deadline,),
            )
            state = "uncertain" if row[0] == "uncertain" else result.outcome
            db.execute(
                f"UPDATE {TABLE} SET state=?,lease_until=NULL,outcome=? WHERE event_key=?",
                (state, result.outcome, claim["event_key"]),
            )
            return {"state": state, "delivered": False}

    def deliver(self, sender):
        if (
            not self.enabled
            or not isinstance(sender, NtfySender)
            or not sender.enabled
            or sender._token is None
        ):
            return {"state": "disabled", "delivered": False}
        with notification_lock(self.database) as acquired:
            if not acquired:
                return {"state": "busy", "delivered": False}
            if recover_abandoned(self.database, self.clock()):
                return {"state": "uncertain_recovery", "delivered": False}
            claim = self.claim(sender)
            if claim is None:
                return {"state": "idle", "delivered": False}
            if not self.delivery_current(claim):
                from finder.discogs_ntfy import DeliveryResult

                return self.finish(claim, DeliveryResult("failed"))
            result = sender.send_inventory(
                claim["listing_id"],
                claim["release_id"],
                claim["checked_at"],
                test=self.test_key is not None,
            )
            return self.finish(claim, result)


def main(argv=None):
    """Explicit operator command; normal invocation is read-only and default-off."""
    import argparse
    import json
    import sqlite3

    from finder.discogs_inventory import InventoryConfig, read_json
    from finder.discogs_ntfy import PrivateNtfyConfig
    from finder.discogs_read_guard import ReadSafetyHalt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--config", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--init-state", action="store_true")
    modes.add_argument("--deliver-one", action="store_true")
    parser.add_argument("--private-notify-config")
    parser.add_argument("--test-snapshot-listing", type=int)
    parser.add_argument("--test-key")
    args = parser.parse_args(argv)
    if args.deliver_one and not args.private_notify_config:
        parser.error("--deliver-one requires --private-notify-config")
    if (
        args.test_snapshot_listing is not None or args.test_key is not None
    ) and not args.deliver_one:
        parser.error("Test selection requires --deliver-one")
    try:
        config = InventoryConfig.model_validate(read_json(args.config, 16384))
        result = {"state": "disabled", "delivered": False}
        if args.init_state:
            initialize_notifications(args.database, config)
            result["state"] = "provisioned_disabled"
        elif args.deliver_one:
            sender = NtfySender(
                enabled=True, private_config=PrivateNtfyConfig(args.private_notify_config)
            )
            result = InventoryNotifications(
                args.database,
                config,
                enabled=True,
                test_listing=args.test_snapshot_listing,
                test_key=args.test_key,
            ).deliver(sender)
    except (
        ReadSafetyHalt,
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        KeyError,
        TypeError,
        OverflowError,
    ):
        print('{"state":"unavailable_manual_review","delivered":false}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
