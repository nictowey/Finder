"""Default-off, single-step seeded inventory observation and private review delivery.

Uses existing provisioned state only. No snapshot, test, reset or credential discovery.
"""

import argparse
import json
import sqlite3
import time
from contextlib import closing

from finder.discogs_inventory import (
    InventoryConfig,
    InventoryObserver,
    _connect,
    _control,
    read_json,
)
from finder.discogs_inventory_notifications import TABLE, InventoryNotifications
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig
from finder.discogs_read_guard import ReadSafetyHalt

SOURCE_STATUSES = {
    "baseline",
    "observed",
    "pending_details",
    "incomplete_no_new_candidates",
    "not_due",
    "no_pending_details",
    "access_denied",
    "cooldown",
    "unavailable",
}
DELIVERY_STATUSES = {
    "disabled",
    "busy",
    "uncertain_recovery",
    "idle",
    "accepted",
    "rejected",
    "failed",
    "uncertain",
}


def run_step(
    database,
    config,
    sender=None,
    *,
    enabled=False,
    transport=None,
    clock=time.time,
    sleep=time.sleep,
):
    """Perform at most one bounded observation and one normal delivery attempt.

    Never return observer payloads or exception text to scheduler logs. Exceptions
    abort this step before delivery; durable reservations remain owned by the existing
    observer/bridge recovery policies, not reset by this orchestrator.
    """
    if type(enabled) is not bool:
        raise ValueError("Explicit step opt-in required.")
    result = {
        "state": "disabled",
        "source": "not_run",
        "delivery": "not_run",
        "requests": 0,
        "delivered": False,
        "purchase_allowed": False,
    }
    if not enabled:
        return result
    if not isinstance(sender, NtfySender) or not sender.enabled or sender._token is None:
        raise ValueError("Existing private sender required.")
    with closing(_connect(database)) as db:
        _control(db)
        # Missing provisioning must fail before consuming any source evidence.
        db.execute(
            f"SELECT event_key,config_hash,control_revision,listing_id,release_id,checked_at,"
            f"state,token,destination_ref,lease_until,outcome FROM {TABLE} LIMIT 0"
        )
    bridge = InventoryNotifications(database, config, enabled=True, clock=clock)
    admission = bridge.prepare_observation()
    if admission["state"] != "ready":
        result["state"] = "deferred"
        result["source"] = "not_due" if admission["state"] == "not_due" else "not_run"
        result["delivery"] = admission["state"]
        return result
    pending = admission["pending"]
    result["operation"] = "drain_pending" if pending else "poll"
    observation = InventoryObserver(
        database, config, transport=transport, clock=clock, sleep=sleep
    ).poll(drain_pending=pending, detail_limit=1)
    status = observation.get("status")
    result["source"] = status if status in SOURCE_STATUSES else "unavailable"
    requests = observation.get("requests")
    if type(requests) is int and 0 <= requests <= 6:
        result["requests"] = requests
    result["state"] = "complete"
    # A failed, stopped, incomplete or not-due source must not trigger delivery.
    # Ordinary bridge freshness/control checks still run again at claim/dispatch.
    if status in {"baseline", "observed", "pending_details"}:
        delivery = bridge.deliver(sender)
        state = delivery.get("state")
        result["delivery"] = state if state in DELIVERY_STATUSES else "unavailable"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--database")
    parser.add_argument("--private-notify-config")
    parser.add_argument("--run-step", action="store_true")
    args = parser.parse_args(argv)
    if args.run_step and (not args.database or not args.private_notify_config):
        parser.error("--run-step requires --database and --private-notify-config")
    try:
        config = InventoryConfig.model_validate(read_json(args.config, 16384))
        sender = None
        if args.run_step:
            sender = NtfySender(
                enabled=True, private_config=PrivateNtfyConfig(args.private_notify_config)
            )
        result = run_step(args.database, config, sender, enabled=args.run_step)
    except ReadSafetyHalt:
        print('{"state":"safety_halt","manual_recovery_required":true,"delivered":false}')
        return 78
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        KeyError,
        TypeError,
        OverflowError,
        IndexError,
        RecursionError,
    ):
        print('{"state":"unavailable_manual_review","delivered":false}')
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
