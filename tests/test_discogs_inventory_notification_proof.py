"""Explicit TEST proof uses snapshot evidence without relabelling or source writes."""

import hashlib
import json
import sqlite3

import httpx
import pytest
from test_discogs_inventory_notifications import mutate, rig, workspace  # noqa: F401

from finder.discogs_inventory import STATE_TABLE
from finder.discogs_inventory_notifications import InventoryNotifications, inventory_message
from finder.discogs_notification_lock import notification_lock, recover_abandoned


def source(rig):  # noqa: F811
    with sqlite3.connect(rig[0]) as db:
        return (
            db.execute(f"SELECT * FROM {STATE_TABLE}").fetchall(),
            db.execute("SELECT * FROM discogs_price_rules").fetchall(),
        )


def snapshot(rig):  # noqa: F811
    def change(state):
        state["baseline"] = False
        state["results"][0].update(
            origin="snapshot",
            event_key=hashlib.sha256(f"{rig[2].fingerprint}:snapshot:100".encode()).hexdigest(),
        )

    mutate(rig, change)


def proof(rig, **kw):  # noqa: F811
    return InventoryNotifications(
        rig[0],
        rig[2],
        clock=rig[1],
        enabled=True,
        test_listing=100,
        test_key="synthetic-proof-01",
        **kw,
    )


def test_snapshot_proof_is_labelled_and_source_unchanged(rig):  # noqa: F811
    snapshot(rig)
    before = source(rig)
    obj = proof(rig)
    assert obj.deliver(rig[3]) == {"state": "accepted", "delivered": False}
    body = json.loads(rig[4][0].content)
    assert body["title"].startswith("TEST:")
    assert "TEST ONLY: Existing snapshot listing" in body["message"]
    assert "not a newly observed listing alert" in body["message"]
    assert body["click"] == "https://www.discogs.com/sell/item/100"
    assert "Newly observed in your monitored" not in body["message"]
    assert source(rig) == before
    with sqlite3.connect(rig[0]) as db:
        key, state = db.execute(
            "SELECT event_key,state FROM discogs_inventory_notification_outbox"
        ).fetchone()
        assert key.startswith("test:") and state == "accepted"
    rig[1].now += 91
    assert proof(rig).deliver(rig[3])["state"] == "idle"
    assert len(rig[4]) == 1
    # Normal discovery dispatch remains silent for the unchanged snapshot.
    ordinary = InventoryNotifications(rig[0], rig[2], enabled=True, clock=rig[1])
    assert ordinary.deliver(rig[3])["state"] == "idle"


def test_proof_rejects_non_snapshot_and_stale_detail(rig):  # noqa: F811
    assert proof(rig).deliver(rig[3])["state"] == "idle"
    snapshot(rig)
    rig[1].now += 120
    assert proof(rig).deliver(rig[3])["state"] == "idle"
    assert not rig[4]


def test_unknown_proof_never_replays_or_consumes_real_event(rig):  # noqa: F811
    snapshot(rig)
    before = source(rig)
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic unknown")

    rig[3]._transport = httpx.MockTransport(fail)
    assert proof(rig).deliver(rig[3])["state"] == "uncertain"
    rig[1].now += 91
    assert proof(rig).deliver(rig[3])["state"] == "idle"
    assert len(calls) == 1 and source(rig) == before


def test_crashed_proof_is_recovered_by_shared_protocol(rig):  # noqa: F811
    snapshot(rig)
    before = source(rig)
    with notification_lock(rig[0]):
        claim = proof(rig).claim(rig[3])
        assert claim["origin"] == "snapshot"
        assert claim["source_event_key"] != claim["event_key"]
        assert claim["event_key"].startswith("test:")
    with notification_lock(rig[0]):
        assert recover_abandoned(rig[0], rig[1]()) == 1
    assert source(rig) == before and not rig[4]


@pytest.mark.parametrize(
    "options",
    [
        {"test_listing": 100},
        {"test_key": "synthetic-proof-01"},
        {"test_listing": True, "test_key": "synthetic-proof-01"},
        {"test_listing": 100, "test_key": "short"},
    ],
)
def test_proof_requires_explicit_paired_selection(rig, options):  # noqa: F811
    with pytest.raises(ValueError):
        InventoryNotifications(rig[0], rig[2], **options)
    assert not rig[4]


def test_proof_does_not_implicitly_enable_delivery(rig):  # noqa: F811
    snapshot(rig)
    obj = InventoryNotifications(
        rig[0], rig[2], clock=rig[1], test_listing=100, test_key="synthetic-proof-01"
    )
    assert obj.deliver(rig[3])["state"] == "disabled"
    assert not rig[4]


def test_canonical_test_switch_is_strict():
    with pytest.raises(ValueError):
        inventory_message(100, 123, 1800000000, test="false")
