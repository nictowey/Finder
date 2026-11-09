"""Offline tests for the synthetic real-browser bridge; Playwright is not required."""

import copy
import importlib.util
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

BRIDGE = Path(__file__).parent / "cloud_browser" / "verify_pipeline.py"
SPEC = importlib.util.spec_from_file_location("cloud_browser_verify_pipeline", BRIDGE)
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def _snapshot(second, ident, description):
    return {
        "source": "discogs_rendered_release",
        "purchase_allowed": False,
        "release_id": 111,
        "url": "https://www.discogs.com/sell/release/111",
        "observed_at": (datetime(2026, 10, 24, tzinfo=UTC) + timedelta(seconds=second)).isoformat(),
        "status": "observed",
        "complete": True,
        "range_start": 1,
        "range_end": 1,
        "total": 1,
        "page": 1,
        "has_next": False,
        "rows": [
            {
                "listing_id": ident,
                "url": f"https://www.discogs.com/sell/item/{ident}",
                "fields": {
                    "price": "$20.00 USD",
                    "media": "Very Good Plus (VG+)",
                    "sleeve": "Very Good Plus (VG+)",
                    "seller": "Synthetic seller",
                    "seller_rating": "100.0%",
                    "seller_feedback": "100 ratings",
                    "shipping": "+$5.00 shipping",
                    "seller_description": description,
                },
            }
        ],
        "assertions": {
            "release_identity": True,
            "listing_container": True,
            "pagination": True,
            "stable": True,
        },
    }


@pytest.fixture
def snapshots():
    first = _snapshot(0, "222", "Clean copy, plays well with light sleeve wear.")
    changed = _snapshot(1, "333", "NO DISC 1, disc 2 only.")
    failed = _snapshot(2, "333", "unused")
    failed.update(
        status="incomplete",
        complete=False,
        range_start=None,
        range_end=None,
        total=None,
        has_next=None,
        rows=[],
        assertions={key: False for key in failed["assertions"]},
        error_code="partial_page_unverified",
    )
    return [first, changed, failed, _snapshot(3, "333", "NO DISC 1, disc 2 only.")]


def test_pipeline_real_storage_rules_and_reopen_without_network(snapshots, monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Synthetic bridge must not use network or an auth service")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    original = copy.deepcopy(snapshots)
    result = pipeline.verify_pipeline(snapshots)
    assert snapshots == original
    assert result == {
        "ok": True,
        "mode": "synthetic_cloud_browser_pipeline",
        "snapshots": 4,
        "baseline_events": 0,
        "substitution_events": 2,
        "failed_baseline_preserved": True,
        "restart_dedupe": True,
        "unchanged_events": 0,
        "revision_guard_rejections": 4,
        "duplicate_rejected": True,
        "ordinary_omission_check": "no_disclosed_media_omission",
        "incomplete_rejected_despite_vg_plus": True,
        "exact_quantities_checked": [1, 2, 3],
        "purchase_allowed": False,
        "notifications_sent": 0,
    }


@pytest.mark.parametrize(
    "mode", ["missing", "wrong_release", "purchase", "repeated_time", "not_failed"]
)
def test_bridge_rejects_wrong_or_incomplete_sequence(snapshots, mode):
    if mode == "missing":
        snapshots.pop()
    elif mode == "wrong_release":
        snapshots[0]["release_id"] = 112
    elif mode == "purchase":
        snapshots[0]["purchase_allowed"] = True
    elif mode == "repeated_time":
        snapshots[3]["observed_at"] = snapshots[2]["observed_at"]
    else:
        snapshots[2] = copy.deepcopy(snapshots[1])
    with pytest.raises(ValueError):
        pipeline.verify_pipeline(snapshots)


@pytest.mark.parametrize("comments", [None, "Includes both discs.", "Missing disc one."])
def test_ordinary_fixture_must_reach_omission_status_not_a_complete_claim(snapshots, comments):
    snapshots[0]["rows"][0]["fields"]["seller_description"] = comments
    with pytest.raises(pipeline.PipelineVerificationError):
        pipeline.verify_pipeline(snapshots)


def test_bridge_cli_emits_compact_safe_summary(snapshots):
    result = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(snapshots),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["ok"] is True
    assert len(result.stdout.splitlines()) == 1 and result.stderr == ""
    assert "Synthetic seller" not in result.stdout and "discogs.com" not in result.stdout


def test_bridge_cli_failure_never_echoes_input(snapshots):
    snapshots[0]["private_unexpected_field"] = "PRIVATE_SENTINEL_DO_NOT_PRINT"
    result = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(snapshots),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": "pipeline_verification_failed",
        "purchase_allowed": False,
    }
    assert "PRIVATE_SENTINEL_DO_NOT_PRINT" not in result.stdout + result.stderr
    assert result.stderr == ""
