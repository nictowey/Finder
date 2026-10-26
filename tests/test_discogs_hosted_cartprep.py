"""Offline intercepted cart preparation; no browser, site request or order."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_cart_claims import CartClaimStore
from finder.discogs_hosted_cartprep import prepare_hosted_cart
from finder.discogs_release_snapshots import RenderedReleaseSnapshot
from finder.discogs_rules import Observation


def evidence(now, seller="Synthetic_1"):
    observed_at = datetime.fromtimestamp(now, UTC).isoformat()
    snapshot = RenderedReleaseSnapshot.model_validate(
        {
            "source": "discogs_rendered_release",
            "purchase_allowed": False,
            "release_id": 123,
            "url": "https://www.discogs.com/sell/release/123",
            "observed_at": observed_at,
            "status": "observed",
            "complete": True,
            "range_start": 1,
            "range_end": 1,
            "total": 1,
            "page": 1,
            "has_next": False,
            "rows": [
                {
                    "listing_id": "1002",
                    "url": "https://www.discogs.com/sell/item/1002",
                    "fields": {
                        "price": "$5.00 USD",
                        "media": "Very Good Plus (VG+)",
                        "sleeve": "Very Good Plus (VG+)",
                        "seller": "Display name",
                        "seller_handle": seller,
                        "seller_rating": "100%",
                        "seller_feedback": "10 reviews",
                        "shipping": None,
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
    )
    detail = VerifiedHostedDetail(
        observation=Observation(
            source="discogs_rendered_listing",
            listing_id="1002",
            release_id=123,
            observed_at=observed_at,
            available=True,
            pressing_evidence="unknown",
            media="VG+",
            sleeve="VG+",
        ),
        requested_release=123,
        requested_listing="1002",
        route="/sell/item/1002",
        started_at=now,
        completed_at=now,
        reported_shipping_text=None,
        cart_state="addable",
        seller_handle="Synthetic_1",
    )
    return snapshot, detail


class InterceptedAdapter:
    def __init__(self, outcome="cart_observed"):
        self.calls = []
        self.outcome = outcome

    def prepare(self, *, release_id, listing_id):
        self.calls.append((release_id, listing_id))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def run(claims, snapshot, detail, adapter, now, key, **changes):
    return prepare_hosted_cart(
        release_id=changes.get("release_id", 123),
        listing_id=changes.get("listing_id", "1002"),
        rules_revision=3,
        idempotency_key=key,
        snapshot=snapshot,
        detail=detail,
        claims=claims,
        adapter=adapter,
        check_current=changes.get(
            "check_current",
            lambda: {
                "release_id": 123,
                "listing_id": "1002",
                "rules_revision": 3,
                "seller_handle": "Synthetic_1",
                "allowed": True,
            },
        ),
        allow_cart_write=changes.get("allow_cart_write", True),
        clock=lambda: now,
    )


def test_intercepted_add_claims_once_and_replay_after_restart_stays_blocked(tmp_path):
    now = 1792887301.0
    snapshot, detail = evidence(now)
    path = tmp_path / "claims.sqlite"
    claims = CartClaimStore(path, create=True)
    adapter, key = InterceptedAdapter(), str(uuid4())
    result = run(claims, snapshot, detail, adapter, now, key)
    assert result == {
        "status": "blocked",
        "code": "cart_observed_no_order",
        "claim_state": "cart_observed",
        "purchase_allowed": False,
    }
    assert adapter.calls == [(123, "1002")]
    assert (
        run(CartClaimStore(path), snapshot, detail, adapter, now, key)["code"]
        == "cart_claim_already_used"
    )
    assert adapter.calls == [(123, "1002")]


def test_seller_and_identity_mismatch_or_disabled_write_never_claim(tmp_path):
    now = 1792887301.0
    snapshot, detail = evidence(now)
    claims = CartClaimStore(tmp_path / "claims.sqlite", create=True)
    adapter, key = InterceptedAdapter(), str(uuid4())
    for changed in [
        dict(snapshot=evidence(now, "Other")[0]),
        dict(detail=replace(detail, requested_release=124)),
        dict(detail=replace(detail, seller_handle=None)),
        dict(allow_cart_write=False),
        dict(listing_id="1003"),
    ]:
        values = {
            "snapshot": snapshot,
            "detail": detail,
            "allow_cart_write": True,
            "listing_id": "1002",
            **changed,
        }
        result = run(
            claims,
            values["snapshot"],
            values["detail"],
            adapter,
            now,
            key,
            allow_cart_write=values["allow_cart_write"],
            listing_id=values["listing_id"],
        )
        assert result["status"] == "blocked"
        assert result["claim_state"] is None
    assert claims.get(key) is None
    assert adapter.calls == []


def test_unknown_result_is_durable_and_cannot_be_retried(tmp_path):
    now = 1792887301.0
    snapshot, detail = evidence(now)
    path = tmp_path / "claims.sqlite"
    claims = CartClaimStore(path, create=True)
    adapter, key = InterceptedAdapter(RuntimeError("uncertain click")), str(uuid4())
    result = run(claims, snapshot, detail, adapter, now, key)
    assert result["code"] == "cart_outcome_uncertain"
    assert CartClaimStore(path).get(key).state == "uncertain"
    assert (
        run(CartClaimStore(path), snapshot, detail, adapter, now, key)["code"]
        == "cart_claim_already_used"
    )
    assert len(adapter.calls) == 1


def test_state_change_at_click_gate_consumes_claim_without_click(tmp_path):
    now = 1792887301.0
    snapshot, detail = evidence(now)
    claims = CartClaimStore(tmp_path / "claims.sqlite", create=True)
    adapter, key = InterceptedAdapter(), str(uuid4())
    reads = [0]

    def check_current():
        reads[0] += 1
        return {
            "release_id": 123,
            "listing_id": "1002",
            "rules_revision": 3,
            "seller_handle": "Synthetic_1",
            "allowed": reads[0] == 1,
        }

    result = run(claims, snapshot, detail, adapter, now, key, check_current=check_current)
    assert result["code"] == "cart_outcome_uncertain"
    assert claims.get(key).state == "uncertain"
    assert adapter.calls == []
