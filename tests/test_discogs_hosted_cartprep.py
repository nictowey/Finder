"""Offline pre-cart quote inspection; no site, browser, cart, or order access."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_hosted_scheduler import MIN_INTERVAL, page
from test_discogs_rules import target

from finder.cloud_app import CloudApp
from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import (
    CandidatePipeline,
    CandidateStopped,
    observation_fingerprint,
)
from finder.discogs_cart_claims import CartClaimStore
from finder.discogs_hosted_cartprep import CartInspectionAuthorization, prepare_hosted_cart
from finder.discogs_hosted_checkout import CheckoutStopped, HostedCheckoutPreflight
from finder.discogs_hosted_scheduler import HostedReleaseScheduler
from finder.discogs_release_snapshots import RenderedReleaseSnapshot
from finder.discogs_rules import Observation, RulesStore


class InterceptedAdapter:
    def __init__(self, outcome="cart_observed"):
        self.calls = []
        self.outcome = outcome

    def prepare(self, *, release_id, listing_id):
        self.calls.append((release_id, listing_id))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def setup(
    tmp_path,
    *,
    comments="Clean copy, plays great",
    item="5.00",
    currency=None,
    read_delay=0,
    catalog_age=0,
):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    CloudApp(database, ORIGIN, verifier())
    store = RulesStore(database)
    store.save(target(min_feedback=0).model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    now = [1792887301.0]
    claims = CartClaimStore(tmp_path / "claims.sqlite", create=True)

    def make_detail(_release, ident):
        observation = Observation(
            source="discogs_rendered_listing",
            listing_id=ident,
            release_id=123,
            observed_at=datetime.fromtimestamp(now[0] - read_delay, UTC).isoformat(),
            available=True,
            pressing_evidence="unknown",
            media="VG+",
            sleeve="VG+",
            seller_percent=100.0,
            feedback=10,
            item=item,
            currency=currency,
            seller_comments=comments,
            release_media_metadata={"id": 123, "formats": [{"name": "Vinyl", "qty": "1"}]},
            release_media_provenance={
                "source": "discogs_release_api",
                "release_id": 123,
                "fetched_at": datetime.fromtimestamp(now[0] - catalog_age, UTC).isoformat(),
            },
        )
        return VerifiedHostedDetail(
            observation=observation,
            requested_release=123,
            requested_listing=ident,
            route=f"/sell/item/{ident}",
            started_at=now[0] - read_delay,
            completed_at=now[0],
            reported_shipping_text="$4.95 shipping",
            cart_state="addable",
            seller_handle="Synthetic_1",
        )

    candidates = CandidatePipeline(
        database, reader=make_detail, claims=claims, clock=lambda: now[0]
    )
    pages = [(1001,), (1001, 1002)]

    def read(_release):
        result = page(now[0], pages.pop(0))
        for row in result["rows"]:
            row["fields"]["seller_handle"] = "Synthetic_1"
        return result

    scheduler = HostedReleaseScheduler(database, read, clock=lambda: now[0], candidates=candidates)
    scheduler.set_paused(False, expected_revision=0)
    assert scheduler.tick()["status"] == "baseline"
    now[0] += MIN_INTERVAL + 1
    assert scheduler.tick()["status"] == "changed"
    assert candidates.review_one()["status"] == "blocked"  # Final quote remains unknown.
    snapshot = page(now[0], (1001, 1002))
    for row in snapshot["rows"]:
        row["fields"]["seller_handle"] = "Synthetic_1"
    return (
        now,
        claims,
        candidates,
        RenderedReleaseSnapshot.model_validate(snapshot),
        make_detail(123, "1002"),
    )


def authorization(now, detail, key, **changes):
    data = dict(
        action="cart_quote_inspection",
        release_id=123,
        listing_id="1002",
        seller_handle="Synthetic_1",
        rules_revision=1,
        idempotency_key=key,
        observation_fingerprint=observation_fingerprint(detail.observation),
        issued_at=now[0] - 1,
        expires_at=now[0] + 120,
    )
    data.update(changes)
    return CartInspectionAuthorization.model_validate(data)


def run(now, claims, candidates, snapshot, detail, adapter, key, **changes):
    return prepare_hosted_cart(
        release_id=changes.get("release_id", 123),
        listing_id=changes.get("listing_id", "1002"),
        rules_revision=1,
        idempotency_key=key,
        snapshot=changes.get("snapshot", snapshot),
        detail=changes.get("detail", detail),
        claims=claims,
        candidates=candidates,
        adapter=adapter,
        authorization=changes.get("authorization", authorization(now, detail, key)),
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
        clock=lambda: now[0],
    )


def test_pre_cart_passes_while_final_review_is_blocked_and_claim_is_single_use(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key, adapter = str(uuid4()), InterceptedAdapter()
    with pytest.raises(CandidateStopped, match="candidate_review_stale_or_blocked"):
        candidates.reserve_rehearsal(123, "1002", str(uuid4()))
    result = run(now, claims, candidates, snapshot, detail, adapter, key)
    assert result == {
        "status": "blocked",
        "code": "cart_observed_no_order",
        "claim_state": "cart_observed",
        "purchase_allowed": False,
    }
    assert adapter.calls == [(123, "1002")]
    assert (
        run(now, claims, candidates, snapshot, detail, adapter, key)["code"]
        == "cart_claim_already_used"
    )
    assert adapter.calls == [(123, "1002")]
    review = candidates.cart_review_bundle(123, "1002", key)["listing"]
    assert review["evaluation"]["eligible_for_review"] is False
    assert review["cart_inspection_evaluation"]["eligible_for_cart_inspection"] is True
    assert review["cart_inspection_evaluation"]["quote_unresolved"]
    reads = []
    with pytest.raises(CheckoutStopped, match="candidate_or_claim_unavailable"):
        HostedCheckoutPreflight(candidates, lambda item: reads.append(item)).inspect(
            123, "1002", key
        )
    assert reads == []


def test_caller_flag_or_callback_cannot_replace_scoped_authorization(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key, adapter = str(uuid4()), InterceptedAdapter()
    for auth in (
        None,
        authorization(now, detail, key, seller_handle="Other"),
        authorization(now, detail, key, observation_fingerprint="0" * 64),
        authorization(now, detail, key, expires_at=now[0]),
    ):
        assert (
            run(now, claims, candidates, snapshot, detail, adapter, key, authorization=auth)["code"]
            == "cart_inspection_authorization_unverified"
        )
    assert claims.get(key) is None and not adapter.calls


@pytest.mark.parametrize("source", ["synthetic", "discogs_public_listing"])
def test_wrapped_nonhosted_detail_never_promotes(source, tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    changed = replace(detail, observation=detail.observation.model_copy(update={"source": source}))
    key, adapter = str(uuid4()), InterceptedAdapter()
    assert run(now, claims, candidates, snapshot, changed, adapter, key)["code"] == (
        "hosted_detail_provenance_unverified"
    )
    assert claims.get(key) is None and not adapter.calls


@pytest.mark.parametrize(
    "comments,item,currency",
    [
        ("no disc", "5.00", None),
        ("surface scratches, plays nice", "5.00", None),
        ("Clean copy", "55.00", "USD"),
    ],
)
def test_blocked_detail_never_reserves(comments, item, currency, tmp_path):
    now, claims, candidates, snapshot, detail = setup(
        tmp_path, comments=comments, item=item, currency=currency
    )
    key, adapter = str(uuid4()), InterceptedAdapter()
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_current_assessment_blocked"
    )
    assert claims.get(key) is None and not adapter.calls


def test_rule_revision_and_snapshot_changes_stop_before_adapter(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    store = RulesStore(candidates.database)
    saved = store.snapshot()
    store.save(saved["targets"][0], saved["revision"])
    store.close()
    key, adapter = str(uuid4()), InterceptedAdapter()
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_current_assessment_blocked"
    )
    assert not adapter.calls


def test_changed_item_price_or_seller_after_review_cannot_reserve(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key, adapter = str(uuid4()), InterceptedAdapter()
    changed = replace(detail, observation=detail.observation.model_copy(update={"item": "6.00"}))
    assert run(now, claims, candidates, snapshot, changed, adapter, key)["code"] == (
        "cart_claim_unavailable"
    )
    assert claims.get(key) is None
    assert not adapter.calls


def test_competing_claim_blocks_a_second_cart_inspection(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    first, second, adapter = str(uuid4()), str(uuid4()), InterceptedAdapter()
    claims.reserve(123, "1002", 1, first)
    assert run(now, claims, candidates, snapshot, detail, adapter, second)["code"] == (
        "cart_claim_unavailable"
    )
    assert claims.get(second) is None and not adapter.calls


def test_revision_change_during_adapter_is_uncertain_not_observed(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key = str(uuid4())

    class ConcurrentRevision:
        calls = []

        def prepare(self, *, release_id, listing_id):
            self.calls.append((release_id, listing_id))
            store = RulesStore(candidates.database)
            saved = store.snapshot()
            store.save(saved["targets"][0], saved["revision"])
            store.close()
            return "cart_observed"

    adapter = ConcurrentRevision()
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_outcome_uncertain"
    )
    assert claims.get(key).state == "uncertain"
    assert adapter.calls == [(123, "1002")]


def test_authorization_expires_during_adapter_is_uncertain(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key = str(uuid4())

    class SlowAdapter:
        calls = []

        def prepare(self, *, release_id, listing_id):
            self.calls.append((release_id, listing_id))
            now[0] += 130
            return "cart_observed"

    adapter = SlowAdapter()
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_outcome_uncertain"
    )
    assert claims.get(key).state == "uncertain"
    assert adapter.calls == [(123, "1002")]


def test_unknown_adapter_outcome_is_durable_and_not_replayed(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key, adapter = str(uuid4()), InterceptedAdapter(RuntimeError("uncertain"))
    result = run(now, claims, candidates, snapshot, detail, adapter, key)
    assert result["code"] == "cart_outcome_uncertain"
    assert claims.get(key).state == "uncertain"
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_claim_already_used"
    )
    assert adapter.calls == [(123, "1002")]


def test_observation_freshness_is_recomputed_after_a_slow_detail_read(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path, read_delay=30)
    now[0] += 290  # Completion/review remain fresh; the underlying observation does not.
    key, adapter = str(uuid4()), InterceptedAdapter()
    result = run(now, claims, candidates, snapshot, detail, adapter, key)
    assert result["code"] == "cart_current_assessment_blocked"
    assert claims.get(key) is None and adapter.calls == []


def test_catalog_expiry_during_adapter_keeps_outcome_uncertain(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path, catalog_age=21590)
    key = str(uuid4())

    class DelayedAdapter:
        calls = []

        def prepare(self, *, release_id, listing_id):
            self.calls.append((release_id, listing_id))
            now[0] += 20
            return "cart_observed"

    adapter = DelayedAdapter()
    assert run(now, claims, candidates, snapshot, detail, adapter, key)["code"] == (
        "cart_outcome_uncertain"
    )
    assert claims.get(key).state == "uncertain"
    assert adapter.calls == [(123, "1002")]


def test_simultaneous_workers_cannot_prepare_the_same_claim_twice(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key, adapter, start = str(uuid4()), InterceptedAdapter(), Barrier(2)

    def attempt(_):
        start.wait(timeout=5)
        return run(now, claims, candidates, snapshot, detail, adapter, key)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, range(2)))
    assert sum(result["code"] == "cart_observed_no_order" for result in results) == 1
    assert adapter.calls == [(123, "1002")]
    assert CartClaimStore(claims.path).get(key).state == "cart_observed"
