"""Offline intercepted cart preparation; no browser, site request or order."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_candidate_pipeline import make_observation
from test_discogs_hosted_scheduler import MIN_INTERVAL, page
from test_discogs_rules import target

from finder.cloud_app import CloudApp
from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import CandidatePipeline
from finder.discogs_cart_claims import CartClaimBlocked, CartClaimStore
from finder.discogs_hosted_cartprep import prepare_hosted_cart
from finder.discogs_hosted_scheduler import HostedReleaseScheduler
from finder.discogs_release_snapshots import RenderedReleaseSnapshot
from finder.discogs_rules import Observation, RulesStore


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


def reviewed_case(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    CloudApp(database, ORIGIN, verifier())
    store = RulesStore(database)
    store.save(target().model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    now = [1792887301.0]
    claims = CartClaimStore(tmp_path / "claims.sqlite", create=True)
    candidates = CandidatePipeline(
        database,
        reader=lambda _release, ident: make_observation(now[0], ident),
        claims=claims,
        clock=lambda: now[0],
    )
    pages = [(1001,), (1001, 1002)]

    def read(_release):
        observed = page(now[0], pages.pop(0))
        for row in observed["rows"]:
            row["fields"]["seller_handle"] = "Synthetic_1"
        return observed

    scheduler = HostedReleaseScheduler(database, read, clock=lambda: now[0], candidates=candidates)
    scheduler.set_paused(False, expected_revision=0)
    assert scheduler.tick()["status"] == "baseline"
    now[0] += MIN_INTERVAL + 1
    assert scheduler.tick()["status"] == "changed"
    assert candidates.review_one()["status"] == "review_only"
    observed = page(now[0], (1001, 1002))
    for row in observed["rows"]:
        row["fields"]["seller_handle"] = "Synthetic_1"
    snapshot = RenderedReleaseSnapshot.model_validate(observed)
    _, detail = evidence(now[0])
    detail = replace(detail, observation=make_observation(now[0]))
    return now[0], claims, candidates, snapshot, detail


class InterceptedAdapter:
    def __init__(self, outcome="cart_observed"):
        self.calls = []
        self.outcome = outcome

    def prepare(self, *, release_id, listing_id):
        self.calls.append((release_id, listing_id))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def run(claims, candidates, snapshot, detail, adapter, now, key, **changes):
    return prepare_hosted_cart(
        release_id=changes.get("release_id", 123),
        listing_id=changes.get("listing_id", "1002"),
        rules_revision=1,
        idempotency_key=key,
        snapshot=snapshot,
        detail=detail,
        claims=claims,
        candidates=changes.get("candidates", candidates),
        adapter=adapter,
        check_current=changes.get(
            "check_current",
            lambda: {
                "release_id": 123,
                "listing_id": "1002",
                "rules_revision": 1,
                "seller_handle": "Synthetic_1",
                "allowed": True,
            },
        ),
        allow_cart_write=changes.get("allow_cart_write", True),
        clock=lambda: now,
    )


def live_shaped(detail):
    comments = "surface scratches, plays nice, no skips, all vinyl is graded by my standards"
    data = detail.observation.model_dump()
    data.update(
        source="discogs_rendered_listing",
        media="P",
        sleeve="Generic",
        currency=None,
        shipping=None,
        tax=None,
        fees=None,
        pressing_evidence="unknown",
        seller_percent=None,
        feedback=None,
        seller_comments=comments,
        release_media_metadata=None,
    )
    return replace(detail, observation=Observation.model_validate(data))


@pytest.mark.parametrize("source", ["synthetic", "discogs_public_listing"])
def test_nonhosted_review_cannot_be_wrapped_as_hosted_cart_evidence(tmp_path, source):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    detail = replace(
        detail,
        observation=Observation.model_validate(
            {**detail.observation.model_dump(), "source": source}
        ),
    )
    path = claims.path
    adapter, key = InterceptedAdapter(), str(uuid4())
    result = run(claims, candidates, snapshot, detail, adapter, now, key)
    assert result["code"] == "hosted_detail_provenance_unverified"
    assert result["claim_state"] is None
    assert claims.get(key) is None
    assert adapter.calls == []
    assert (
        run(CartClaimStore(path), candidates, snapshot, detail, adapter, now, key)["code"]
        == "hosted_detail_provenance_unverified"
    )
    assert adapter.calls == []


def test_malformed_hosted_wrapper_fails_closed(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    adapter, key = InterceptedAdapter(), str(uuid4())
    malformed = replace(detail, observation=object())
    assert run(claims, candidates, snapshot, malformed, adapter, now, key)["code"] == (
        "hosted_detail_unavailable"
    )
    assert claims.get(key) is None
    assert adapter.calls == []


def test_seller_and_identity_mismatch_or_disabled_write_never_claim(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
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
            candidates,
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


def test_adapter_exception_is_unreachable_from_synthetic_wrapper(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    path = claims.path
    adapter, key = InterceptedAdapter(RuntimeError("uncertain click")), str(uuid4())
    result = run(claims, candidates, snapshot, detail, adapter, now, key)
    assert result["code"] == "hosted_detail_provenance_unverified"
    assert CartClaimStore(path).get(key) is None
    assert (
        run(CartClaimStore(path), candidates, snapshot, detail, adapter, now, key)["code"]
        == "hosted_detail_provenance_unverified"
    )
    assert adapter.calls == []


def test_callback_is_not_queried_for_synthetic_wrapper(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    adapter, key = InterceptedAdapter(), str(uuid4())
    reads = [0]

    def check_current():
        reads[0] += 1
        return {
            "release_id": 123,
            "listing_id": "1002",
            "rules_revision": 1,
            "seller_handle": "Synthetic_1",
            "allowed": reads[0] == 1,
        }

    result = run(
        claims, candidates, snapshot, detail, adapter, now, key, check_current=check_current
    )
    assert result["code"] == "hosted_detail_provenance_unverified"
    assert claims.get(key) is None
    assert adapter.calls == []
    assert reads == [0]


def test_allowed_callback_cannot_bypass_blocked_candidate(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    store = RulesStore(candidates.database)
    with store.db:
        store.db.execute(
            "UPDATE discogs_candidates SET state='blocked' "
            "WHERE release_id=123 AND listing_id='1002'"
        )
    store.close()
    adapter, key = InterceptedAdapter(), str(uuid4())
    result = run(claims, candidates, snapshot, detail, adapter, now, key)
    assert result["code"] == "hosted_detail_provenance_unverified"
    assert claims.get(key) is None
    assert adapter.calls == []


def test_live_shaped_unknown_item_cannot_claim_or_prepare(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    detail = live_shaped(detail)
    adapter, key = InterceptedAdapter(), str(uuid4())
    result = run(claims, candidates, snapshot, detail, adapter, now, key)
    assert result["code"] == "hosted_pre_cart_qualification_unavailable"
    assert claims.get(key) is None
    assert adapter.calls == []


def test_default_disabled_and_preexisting_release_claim_stay_fenced(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    detail = live_shaped(detail)
    adapter, first, other = InterceptedAdapter(), str(uuid4()), str(uuid4())
    assert (
        run(
            claims,
            candidates,
            snapshot,
            detail,
            adapter,
            now,
            other,
            allow_cart_write=False,
        )["code"]
        == "cart_write_disabled"
    )
    assert claims.get(other) is None
    assert adapter.calls == []

    claims.reserve(123, "1002", 1, first)
    assert run(claims, candidates, snapshot, detail, adapter, now, other)["code"] == (
        "hosted_pre_cart_qualification_unavailable"
    )
    assert CartClaimStore(claims.path).get(first).state == "reserved"
    assert claims.get(other) is None
    with pytest.raises(CartClaimBlocked):
        CartClaimStore(claims.path).reserve(123, "1002", 1, other)
    assert adapter.calls == []


def test_stale_rule_does_not_override_live_qualification_block(tmp_path):
    now, claims, candidates, snapshot, detail = reviewed_case(tmp_path)
    detail = live_shaped(detail)
    store = RulesStore(candidates.database)
    saved = store.snapshot()
    store.save(saved["targets"][0], saved["revision"])
    store.close()
    adapter, key = InterceptedAdapter(), str(uuid4())
    assert run(claims, candidates, snapshot, detail, adapter, now, key)["code"] == (
        "hosted_pre_cart_qualification_unavailable"
    )
    assert claims.get(key) is None
    assert adapter.calls == []
