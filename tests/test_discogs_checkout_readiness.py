"""Read-only tests of operator-recorded enums; no browser or account access."""

import json
import os
from dataclasses import FrozenInstanceError

import pytest

from finder.discogs_checkout_readiness import RecordedCheckoutStatus, checkout_readiness

NOW = 1000.0


def values(**changes):
    return {
        "schema": "sniper_checkout_status_v1",
        "checkout_kind": "enhanced_v2",
        "payment_access": "blocked_browser_security_challenge",
        "recorded_at": NOW - 10,
        **changes,
    }


def record(tmp_path, *, payload=None, mode=0o600):
    path = tmp_path / "checkout-status.json"
    path.write_bytes(json.dumps(values()).encode() if payload is None else payload)
    path.chmod(mode)
    return path


def assert_disabled(result):
    assert result["legacy_helper"] == "offline_tested_disabled"
    assert result["manual_unpaid_order_creation"] == "unverified"
    for field in (
        "enhanced_routes_to_legacy",
        "live_status_checked",
        "order_submission_enabled",
        "purchase_allowed",
    ):
        assert result[field] is False


def assert_unavailable(result):
    assert result["status_record"] == "unavailable"
    assert result["observed_checkout_kind"] == result["payment_access"] == "unknown"
    assert result["observation_recorded_at"] is None
    assert result["observation_age"] == "unknown"
    assert_disabled(result)


def test_default_has_generic_unknown_observations_and_disabled_helpers():
    result = checkout_readiness(now=NOW)
    assert result["schema"] == "sniper_checkout_readiness_v1"
    assert result["status_record"] == "unconfigured"
    assert result["observed_checkout_kind"] == result["payment_access"] == "unknown"
    assert result["observation_recorded_at"] is None
    assert_disabled(result)


@pytest.mark.parametrize("kind", ["unknown", "legacy_v1", "enhanced_v2"])
@pytest.mark.parametrize("access", ["unknown", "blocked_browser_security_challenge"])
def test_checkout_and_payment_enums_are_independent_recorded_facts(tmp_path, kind, access):
    path = record(
        tmp_path, payload=json.dumps(values(checkout_kind=kind, payment_access=access)).encode()
    )
    before = path.read_bytes()
    result = checkout_readiness(path, now=NOW)
    assert result["status_record"] == "valid"
    assert result["observed_checkout_kind"] == kind
    assert result["payment_access"] == access
    assert result["observation_recorded_at"] == NOW - 10
    assert result["observation_age_seconds"] == 10.0
    assert result["observation_age"] == "recent"
    assert_disabled(result)
    assert path.read_bytes() == before


def test_old_record_is_labeled_historical_and_never_clears_block(tmp_path):
    path = record(tmp_path, payload=json.dumps(values(recorded_at=0)).encode())
    result = checkout_readiness(path, now=NOW)
    assert result["status_record"] == "valid"
    assert result["payment_access"] == "blocked_browser_security_challenge"
    assert result["observed_checkout_kind"] == "enhanced_v2"
    assert result["observation_recorded_at"] == 0.0
    assert result["observation_age"] == "old"
    assert_disabled(result)


def test_age_boundary_is_a_label_only_and_default_clock_is_used(tmp_path, monkeypatch):
    from finder import discogs_checkout_readiness as module

    path = record(tmp_path, payload=json.dumps(values(recorded_at=NOW - 300)).encode())
    monkeypatch.setattr(module.time, "time", lambda: NOW)
    assert checkout_readiness(path)["observation_age"] == "old"
    assert checkout_readiness(path, now=NOW - 0.1)["observation_age"] == "recent"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o400, 0o700])
def test_non_owner_only_permissions_are_unavailable(tmp_path, mode):
    assert_unavailable(checkout_readiness(record(tmp_path, mode=mode), now=NOW))


def test_missing_symlink_directory_and_foreign_owner_are_unavailable(tmp_path, monkeypatch):
    from finder import discogs_checkout_readiness as module

    path = record(tmp_path)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    for unsupported in (tmp_path / "missing.json", link, tmp_path, str(path)):
        assert_unavailable(checkout_readiness(unsupported, now=NOW))
    current_uid = os.geteuid()
    monkeypatch.setattr(module.os, "geteuid", lambda: current_uid + 1)
    assert_unavailable(checkout_readiness(path, now=NOW))


def test_fifo_cannot_block_or_supply_a_status(tmp_path):
    path = tmp_path / "fifo"
    os.mkfifo(path, 0o600)
    assert_unavailable(checkout_readiness(path, now=NOW))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "other"},
        {"checkout_kind": "enhanced_v3"},
        {"payment_access": "available"},
        {"recorded_at": True},
        {"recorded_at": -1},
        {"recorded_at": NOW + 1},
        {"recorded_at": "990"},
        {"recorded_at": None},
        {"recorded_at": float("nan")},
        {"recorded_at": float("inf")},
        {"recorded_at": 10**400},
        {"recorded_at": 10**1000},
        {"purchase_allowed": True},
        {"order_url": "private-sentinel"},
        {"account": "private-sentinel"},
    ],
)
def test_malformed_and_extra_fields_are_unavailable_without_echoing_values(tmp_path, changes):
    path = record(tmp_path, payload=json.dumps(values(**changes)).encode())
    result = checkout_readiness(path, now=NOW)
    assert_unavailable(result)
    assert "private-sentinel" not in json.dumps(result)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b"[]",
        b"null",
        b"\xff",
        b"{}",
        b" " * 1025,
        b'{"schema":"sniper_checkout_status_v1","checkout_kind":"legacy_v1",'
        b'"checkout_kind":"enhanced_v2","payment_access":"unknown","recorded_at":990}',
    ],
)
def test_invalid_oversized_or_ambiguous_json_is_unavailable(tmp_path, payload):
    assert_unavailable(checkout_readiness(record(tmp_path, payload=payload), now=NOW))


def test_byte_limit_accepts_a_bounded_complete_record_and_rejects_one_extra_byte(tmp_path):
    payload = json.dumps(values()).encode()
    path = record(tmp_path, payload=payload + b" " * (1024 - len(payload)))
    assert checkout_readiness(path, now=NOW)["status_record"] == "valid"
    path.write_bytes(payload + b" " * (1025 - len(payload)))
    assert_unavailable(checkout_readiness(path, now=NOW))


@pytest.mark.parametrize("now", [True, -1, "1000", float("nan"), float("inf"), 10**1000])
def test_invalid_clock_values_never_raise_or_expose_the_record(tmp_path, now):
    assert_unavailable(checkout_readiness(record(tmp_path), now=now))


def test_record_fields_are_frozen_and_enum_only():
    status = RecordedCheckoutStatus(**values())
    with pytest.raises(FrozenInstanceError):
        status.checkout_kind = "legacy_v1"
    with pytest.raises(ValueError):
        RecordedCheckoutStatus(**values(payment_access="private account data"))


def test_record_replaced_after_open_is_unavailable(tmp_path, monkeypatch):
    from finder import discogs_checkout_readiness as module

    path = record(tmp_path)
    actual_read = module.os.read

    def replaced(descriptor, count):
        payload = actual_read(descriptor, count)
        path.unlink()
        record(tmp_path)
        return payload

    monkeypatch.setattr(module.os, "read", replaced)
    assert_unavailable(checkout_readiness(path, now=NOW))
