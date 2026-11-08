"""Offline inventory dispatch contract. All HTTP is MockTransport."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from test_discogs_inventory import config, listing, workspace  # noqa: F401

from finder.discogs_inventory import STATE_TABLE, candidate
from finder.discogs_inventory_notifications import (
    FRESH_SECONDS,
    InventoryNotifications,
    initialize_notifications,
    inventory_message,
)
from finder.discogs_notification_lock import notification_lock, recover_abandoned
from finder.discogs_ntfy import PRIVATE_NTFY_ORIGIN, DeliveryResult, NtfySender, PrivateNtfyConfig


@pytest.fixture
def rig(workspace, tmp_path):  # noqa: F811
    path, clock = workspace
    cfg = config()
    initialize_notifications(path, cfg)
    detail = candidate(listing(), cfg.targets[0])
    detail["checks"]["seed_seller_matches"] = True
    detail.update(
        source="official_public_listing_get",
        detail_recheck_required=False,
        freshness="detail_observation_only",
        observed_at=datetime.fromtimestamp(clock(), UTC).isoformat(),
    )
    event = dict(
        listing_id=100,
        release_id=123,
        origin="newly_observed",
        discovered_at=clock(),
        checked_at=clock(),
        state="review_ready",
        result=detail,
        event_key=hashlib.sha256(f"{cfg.fingerprint}:newly_observed:100".encode()).hexdigest(),
    )
    with sqlite3.connect(path) as db:
        state = json.loads(db.execute(f"SELECT data FROM {STATE_TABLE}").fetchone()[0])
        state.update(baseline=True, seed_verified_at=clock(), results=[event])
        db.execute(f"UPDATE {STATE_TABLE} SET data=?", (json.dumps(state),))
    private = tmp_path / "notify.json"
    private.write_text(
        json.dumps(dict(origin=PRIVATE_NTFY_ORIGIN, topic="s" * 30, token="tk_" + "X" * 20))
    )
    private.chmod(0o600)
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200)

    sender = NtfySender(
        enabled=True,
        private_config=PrivateNtfyConfig(private),
        transport=httpx.MockTransport(response),
    )
    return path, clock, cfg, sender, calls


def mutate(rig, change):
    with sqlite3.connect(rig[0]) as db:
        state = json.loads(db.execute(f"SELECT data FROM {STATE_TABLE}").fetchone()[0])
        change(state)
        db.execute(f"UPDATE {STATE_TABLE} SET data=?", (json.dumps(state),))


def bridge(rig, **kw):
    return InventoryNotifications(rig[0], rig[2], clock=rig[1], **kw)


def test_default_off_and_public_sender(rig):
    assert bridge(rig).deliver(rig[3])["state"] == "disabled"
    public = NtfySender("s" * 30, enabled=True)
    assert bridge(rig, enabled=True).deliver(public)["state"] == "disabled"
    assert not rig[4]
    with pytest.raises(ValueError):
        public.send_inventory(100, 123, rig[1]())


def test_accepted_once_preserves_rules_and_copy(rig):
    with sqlite3.connect(rig[0]) as db:
        before = db.execute("SELECT * FROM discogs_price_rules").fetchall()
    assert bridge(rig, enabled=True).deliver(rig[3]) == {"state": "accepted", "delivered": False}
    rig[1].now += 91
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "idle"
    assert len(rig[4]) == 1
    body = json.loads(rig[4][0].content)
    assert body["click"] == "https://www.discogs.com/sell/item/100"
    assert "not proven newly listed" in body["message"]
    assert "one explicitly monitored seller" in body["message"]
    assert "Both records included" not in body["message"]
    with sqlite3.connect(rig[0]) as db:
        assert db.execute("SELECT * FROM discogs_price_rules").fetchall() == before
        assert len(before) == 19
        assert db.execute("SELECT sends FROM discogs_price_control").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(baseline=False),
        lambda s: s.update(denied=True),
        lambda s: s.update(in_progress=True),
        lambda s: s["results"][0].update(origin="snapshot"),
        lambda s: s["results"][0].update(state="criteria_blocked"),
        lambda s: s["results"][0].update(event_key="0" * 64),
        lambda s: s["results"][0].update(checked_at=1),
        lambda s: s["results"][0].update(checked_at=2000000000),
        lambda s: s["results"][0]["result"].update(source="inventory"),
        lambda s: s["results"][0]["result"].update(listing_id="999"),
        lambda s: s["results"][0]["result"].update(release_id=999),
        lambda s: s["results"][0]["result"].update(detail_recheck_required=True),
        lambda s: s["results"][0]["result"].update(purchase_qualified=True),
        lambda s: s["results"][0]["result"].update(media="VG"),
        lambda s: s["results"][0]["result"]["price"].update(currency="GBP"),
        lambda s: s["results"][0]["result"]["price"].update(display_value="100.00"),
        lambda s: s["results"][0]["result"]["checks"].update(reported_for_sale=False),
        lambda s: s["results"][0]["result"].update(observed_at="2020-01-01T00:00:00+00:00"),
    ],
)
def test_ineligible_is_silent(rig, change):
    mutate(rig, change)
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "idle"
    assert not rig[4]


@pytest.mark.parametrize(
    "code,state", [(200, "accepted"), (429, "rejected"), (403, "failed"), (500, "uncertain")]
)
def test_no_outcome_replays_and_retry_after_shared(rig, code, state):
    rig[3]._transport = httpx.MockTransport(
        lambda r: httpx.Response(code, headers={"Retry-After": "1000"})
    )
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == state
    with sqlite3.connect(rig[0]) as db:
        assert (
            db.execute("SELECT next_send FROM discogs_price_control").fetchone()[0]
            >= rig[1]() + 1000
        )
    # Reimporting the same result cannot reset a terminal event, even if evidence is refreshed.
    rig[1].now += 1001
    mutate(rig, lambda s: s["results"][0].update(checked_at=rig[1]()))
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "idle"


def test_crash_recovery_and_lock(rig):
    obj = bridge(rig, enabled=True)
    with notification_lock(rig[0]) as acquired:
        assert acquired
        assert obj.deliver(rig[3])["state"] == "busy"
        reservation = obj.claim(rig[3])
    with notification_lock(rig[0]):
        assert recover_abandoned(rig[0], rig[1]()) == 1
        assert recover_abandoned(rig[0], rig[1]()) == 0
    assert obj.finish(reservation, DeliveryResult("accepted"))["state"] == "uncertain"
    assert not rig[4]


def test_expiry_between_claim_and_dispatch(rig):
    obj = bridge(rig, enabled=True)
    with notification_lock(rig[0]):
        reservation = obj.claim(rig[3])
        rig[1].now += FRESH_SECONDS
        assert not obj.delivery_current(reservation)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_control SET sends=200,send_day=20833",
        "UPDATE discogs_price_control SET next_send=2000000000",
    ],
)
def test_shared_budget(rig, sql):
    with sqlite3.connect(rig[0]) as db:
        db.execute(sql)
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "idle"
    assert not rig[4]


def test_control_change_and_destination_mutation(rig):
    obj = bridge(rig, enabled=True)
    with notification_lock(rig[0]):
        reservation = obj.claim(rig[3])
        assert obj.delivery_current(reservation)
        changed = dict(reservation, destination_ref="wrong")
        assert not obj.delivery_current(changed)
        with sqlite3.connect(rig[0]) as db:
            db.execute("UPDATE discogs_price_control SET revision=revision+1")
        assert not obj.delivery_current(reservation)


def test_canonical_message_rejects_arbitrary_input():
    with pytest.raises(ValueError):
        inventory_message("https://evil.invalid", 123, 1000)


def test_aggregate_publisher_recovers_inventory_lease(rig):
    from finder.discogs_ntfy import deliver_one
    from finder.discogs_price_alerts import AggregatePriceAlerts

    obj = bridge(rig, enabled=True)
    with notification_lock(rig[0]):
        obj.claim(rig[3])
    aggregate = AggregatePriceAlerts(rig[0], clock=rig[1])
    assert deliver_one(aggregate, rig[3])["state"] == "uncertain_recovery"
    assert not rig[4]


def test_response_timeout_is_terminal(rig):
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic private text must never escape")

    rig[3]._transport = httpx.MockTransport(fail)
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "uncertain"
    rig[1].now += 91
    assert bridge(rig, enabled=True).deliver(rig[3])["state"] == "idle"
    assert len(calls) == 1


def test_manual_hold_and_idempotent_finish(rig):
    obj = bridge(rig, enabled=True)
    with notification_lock(rig[0]):
        claim = obj.claim(rig[3])
        obj.finish(claim, DeliveryResult("failed", manual_attention=True))
        obj.finish(claim, DeliveryResult("accepted"))
    with sqlite3.connect(rig[0]) as db:
        assert db.execute("SELECT next_send FROM discogs_price_control").fetchone()[0] == float(
            "inf"
        )
        assert (
            db.execute("SELECT state FROM discogs_inventory_notification_outbox").fetchone()[0]
            == "failed"
        )


def test_default_cli_has_no_delivery(rig, tmp_path, capsys):
    from finder.discogs_inventory_notifications import main

    cfg = tmp_path / "inventory.json"
    cfg.write_text(rig[2].model_dump_json())
    assert main(["--database", str(rig[0]), "--config", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    assert not rig[4]
