"""Isolated real-SQLite metadata tests; no browser or payment network."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/discogs_order_ledger_port.py"


def _call(path, request):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--test-ledger", str(path)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "SNIPER_LEDGER_FIXTURE_TEST": "1"},
        timeout=10,
    )
    assert result.stderr == ""
    return json.loads(result.stdout)


def _expected():
    return {
        "intent_id": str(uuid4()),
        "approval_ref": str(uuid4()),
        "release_id": 3422100,
        "listing_id": "1366950910",
        "seller": "Mailman21842",
        "quoted_total": "5.05",
        "currency": "USD",
        "quote_fingerprint": "a" * 64,
        "rules_revision": 1,
        "destination_ref": str(uuid4()),
        "payment_ref": str(uuid4()),
        "quote_expires_at": time.time() + 120,
    }


def test_explicit_initialize_prepare_fence_restart_and_no_rearm(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "real-orders.sqlite3"
    expected = _expected()
    assert _call(path, {"op": "status", "release_id": 3422100})["status"] == "blocked"
    assert not path.exists()  # Reads are noncreating.
    assert _call(path, {"op": "initialize"})["status"] == "initialized"
    first = _call(path, {"op": "prepare", "expected": expected})
    assert first["ledger"]["state"] == "prepared"
    assert _call(path, {"op": "prepare", "expected": expected})["ledger"]["state"] == "prepared"
    fenced = _call(path, {"op": "fence", "expected": expected})
    assert fenced["durable"] is True
    assert fenced["ledger"]["state"] == "submission_unknown"
    assert fenced["ledger"]["may_submit_again"] is False
    assert _call(path, {"op": "fence", "expected": expected})["status"] == "blocked"
    assert (
        _call(path, {"op": "status", "release_id": 3422100})["ledger"]["state"]
        == "submission_unknown"
    )
    assert (
        _call(path, {"op": "reconcile", "intent_id": expected["intent_id"], "evidence": None})[
            "ledger"
        ]["state"]
        == "submission_unknown"
    )


def test_full_identity_idempotency_and_unqualified_lookup_preserve_unknown(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "orders.sqlite3"
    expected = _expected()
    _call(path, {"op": "initialize"})
    assert _call(path, {"op": "prepare", "expected": expected})["ledger"]["state"] == "prepared"
    changed = {**expected, "payment_ref": str(uuid4())}
    assert _call(path, {"op": "fence", "expected": changed})["status"] == "blocked"
    assert _call(path, {"op": "status", "release_id": 3422100})["ledger"]["state"] == "prepared"
    assert (
        _call(path, {"op": "fence", "expected": expected})["ledger"]["state"]
        == "submission_unknown"
    )
    unqualified = {
        "order_id": "123-456",
        "release_id": 3422100,
        "listing_id": "1366950910",
        "seller": "Mailman21842",
        "total": "5.06",
        "currency": "USD",
        "displayed_status": "Payment Received",
        "observed_at": time.time(),
        "source": "normal_order_page",
    }
    assert (
        _call(
            path, {"op": "reconcile", "intent_id": expected["intent_id"], "evidence": unqualified}
        )["status"]
        == "blocked"
    )
    status = _call(path, {"op": "status", "release_id": 3422100})["ledger"]
    assert status["state"] == "submission_unknown"
    assert status["payment_state"] == "unknown"


def test_competing_processes_get_only_one_durable_fence(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "orders.sqlite3"
    expected = _expected()
    _call(path, {"op": "initialize"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(
            pool.map(lambda _: _call(path, {"op": "fence", "expected": expected}), range(4))
        )
    assert sum(value["status"] == "observed" for value in outcomes) == 1
    assert (
        _call(path, {"op": "status", "release_id": 3422100})["ledger"]["state"]
        == "submission_unknown"
    )


def test_reconcile_only_normalized_order_evidence_and_never_releases_slot(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "orders.sqlite3"
    expected = _expected()
    _call(path, {"op": "initialize"})
    assert _call(path, {"op": "fence", "expected": expected})["status"] == "observed"
    evidence = {
        "order_id": "123-456",
        "release_id": 3422100,
        "listing_id": "1366950910",
        "seller": "Mailman21842",
        "total": "5.05",
        "currency": "USD",
        "displayed_status": "Invoice Sent",
        "observed_at": time.time(),
        "source": "normal_order_page",
    }
    reconciled = _call(
        path, {"op": "reconcile", "intent_id": expected["intent_id"], "evidence": evidence}
    )
    assert reconciled["ledger"]["state"] == "order_seen"
    assert reconciled["ledger"]["payment_state"] == "unpaid"
    assert reconciled["ledger"]["may_submit_again"] is False
    assert _call(path, {"op": "fence", "expected": expected})["status"] == "blocked"
