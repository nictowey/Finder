"""One disposable synthetic flow; no Discogs access, real cart or order path."""

import json
import shutil
import socket
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_hosted_scheduler import page
from test_discogs_rules import observation, target

from finder.cloud_app import CloudApp
from finder.discogs_candidate_detail import (
    HostedListingCandidateReader,
    PublicKnownListingCandidateReader,
)
from finder.discogs_candidate_pipeline import CandidatePipeline, CandidateStopped
from finder.discogs_cart_claims import CartClaimStore
from finder.discogs_hosted_scheduler import MIN_INTERVAL, HostedReleaseScheduler
from finder.discogs_listing import ListingReader, ListingReadError
from finder.discogs_order_safety import (
    OrderSafetyStore,
    OrderStopped,
    SimulatedCrash,
    SyntheticFinalQuote,
    SyntheticOrderAdapter,
    SyntheticReceipt,
    _fingerprint,
)
from finder.discogs_purchase_pipeline import OfflinePurchasePipeline
from finder.discogs_rules import RulesStore

KEY = "123e4567-e89b-42d3-a456-426614174000"
OTHER = "123e4567-e89b-42d3-a456-426614174001"
GRANT = "123e4567-e89b-42d3-a456-426614174010"
DESTINATION = "123e4567-e89b-42d3-a456-426614174011"
PAYMENT = "123e4567-e89b-42d3-a456-426614174012"
ORDER = "123e4567-e89b-42d3-a456-426614174013"


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    CloudApp(database, ORIGIN, verifier())
    store = RulesStore(database)
    store.save(target().model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    now = [datetime(2026, 10, 25, tzinfo=UTC).timestamp()]
    claims = CartClaimStore(tmp_path / "claims.db", create=True)
    return database, now, claims


def make_observation(now, ident="1002", **changes):
    data = {
        "listing_id": ident,
        "observed_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "tax": "5",
        "fees": "0",
    }
    data.update(changes)
    return observation(**data)


def count(database):
    store = RulesStore(database)
    try:
        return store.db.execute("SELECT count(*) FROM discogs_candidates").fetchone()[0]
    finally:
        store.close()


def flow(database, now, claims, *, candidate_reader=None):
    pages = [(1001,), (1001, 1002)]
    candidates = CandidatePipeline(
        database,
        reader=candidate_reader or (lambda _release, ident: make_observation(now[0], ident)),
        claims=claims,
        clock=lambda: now[0],
    )
    scheduler = HostedReleaseScheduler(
        database,
        lambda _: page(now[0], pages.pop(0)),
        clock=lambda: now[0],
        candidates=candidates,
    )
    scheduler.set_paused(False, expected_revision=0)
    assert scheduler.tick()["status"] == "baseline"
    assert count(database) == 0
    now[0] += MIN_INTERVAL + 1
    assert scheduler.tick()["status"] == "changed"
    assert count(database) == 1
    return candidates, scheduler


def synthetic_order_flow(setup, tmp_path, *, executor=None):
    database, now, claims = setup
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    assert candidates.reserve_rehearsal(123, "1002", KEY)["state"] == "reserved"
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    observation = make_observation(now[0])
    quote = SyntheticFinalQuote.model_validate(
        {
            "mode": "synthetic_final_quote",
            "release_id": 123,
            "listing_id": "1002",
            "rules_revision": 1,
            "item": "40",
            "shipping": "5",
            "tax": "5",
            "fees": "0",
            "total": "50",
            "currency": "USD",
            "destination_country": "US",
            "destination_postal": "03106",
            "destination_ref": DESTINATION,
            "payment_ref": PAYMENT,
            "observed_at": now[0],
            "expires_at": now[0] + 120,
            "quantity": 1,
            "final_quote_verified": True,
        }
    )
    path = tmp_path / "orders.db"
    orders = OrderSafetyStore(path, create=True)
    pipeline = OfflinePurchasePipeline(candidates, orders, executor=executor, clock=lambda: now[0])
    pipeline.issue_simulated_grant(
        GRANT, KEY, 123, "1002", observation, "50", DESTINATION, PAYMENT, now[0] + 3600
    )
    return database, now, claims, scheduler, path, pipeline, observation, quote


def synthetic_receipt(quote, **changes):
    data = dict(
        intent_id=KEY,
        provider_order_id=ORDER,
        release_id=123,
        listing_id="1002",
        total="50.00",
        currency="USD",
        quote_fingerprint=_fingerprint(quote.model_dump()),
        destination_ref=DESTINATION,
        payment_ref=PAYMENT,
    )
    data.update(changes)
    return SyntheticReceipt(**data)


def test_full_synthetic_candidate_to_order_fence_default_executor_is_unavailable(setup, tmp_path):
    database, now, claims, _, path, pipeline, observation, quote = synthetic_order_flow(
        setup, tmp_path
    )
    assert pipeline.readiness()["executor"] == "unavailable"
    assert pipeline.readiness()["live_order_submission_enabled"] is False
    with pytest.raises(ValueError, match="synthetic order stores"):
        OfflinePurchasePipeline(pipeline.candidates, pipeline.orders, executor=object())
    assert pipeline.prepare(GRANT, KEY, 123, "1002", observation, quote)["state"] == "prepared"
    with pytest.raises(OrderStopped, match="executor_unavailable"):
        pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)
    assert OrderSafetyStore(path).status(KEY)["state"] == "prepared"
    assert claims.get(KEY).state == "cart_observed"


def test_synthetic_order_handoff_requires_observed_claim(setup, tmp_path):
    database, now, claims = setup
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    orders = OrderSafetyStore(tmp_path / "orders.db", create=True)
    pipeline = OfflinePurchasePipeline(candidates, orders, clock=lambda: now[0])
    with pytest.raises(OrderStopped, match="candidate_or_observed_cart_unverified"):
        pipeline.issue_simulated_grant(
            GRANT,
            KEY,
            123,
            "1002",
            make_observation(now[0]),
            "50",
            DESTINATION,
            PAYMENT,
            now[0] + 3600,
        )
    assert candidates.reserve_rehearsal(123, "1002", KEY)["state"] == "reserved"
    with pytest.raises(OrderStopped, match="candidate_or_observed_cart_unverified"):
        pipeline.issue_simulated_grant(
            GRANT,
            KEY,
            123,
            "1002",
            make_observation(now[0]),
            "50",
            DESTINATION,
            PAYMENT,
            now[0] + 3600,
        )
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    pipeline.issue_simulated_grant(
        GRANT,
        KEY,
        123,
        "1002",
        make_observation(now[0]),
        "50",
        DESTINATION,
        PAYMENT,
        now[0] + 3600,
    )


def test_synthetic_order_handoff_rejects_changed_quote(setup, tmp_path):
    _, _, _, _, _, ready, observation, quote = synthetic_order_flow(setup, tmp_path)
    with pytest.raises(OrderStopped, match="quote_identity_destination_or_freshness_unverified"):
        ready.prepare(
            GRANT, KEY, 123, "1002", observation, quote.model_copy(update={"tax": "6.00"})
        )
    assert ready.orders.status(KEY) is None


@pytest.mark.parametrize("change", ["rules", "pause", "owned", "observation", "quote"])
def test_full_synthetic_handoff_rejects_changed_state_before_executor(setup, tmp_path, change):
    database, now, _, scheduler, path, pipeline, observation, quote = synthetic_order_flow(
        setup, tmp_path
    )
    pipeline.prepare(GRANT, KEY, 123, "1002", observation, quote)
    adapter = SyntheticOrderAdapter(submit_result=synthetic_receipt(quote))
    pipeline.executor = adapter
    if change == "rules":
        store = RulesStore(database)
        updated = store.snapshot()["targets"][0]
        updated["min_media"] = "M"
        store.save(updated, 1)
        store.close()
    elif change == "pause":
        scheduler.set_paused(True, expected_revision=1)
    elif change == "owned":
        store = RulesStore(database)
        store.mark_owned(123, True, 1)
        store.close()
    elif change == "observation":
        observation = observation.model_copy(update={"seller_comments": "Another complete copy."})
    else:
        quote = quote.model_copy(update={"total": "49.00"})
    with pytest.raises(OrderStopped):
        pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)
    assert adapter.submit_calls == 0
    assert OrderSafetyStore(path).status(KEY)["state"] == "prepared"


def test_full_synthetic_crash_restart_reconciles_without_resubmit(setup, tmp_path):
    database, now, claims, _, path, pipeline, observation, quote = synthetic_order_flow(
        setup, tmp_path, executor=SyntheticOrderAdapter(submit_result=SimulatedCrash())
    )
    pipeline.prepare(GRANT, KEY, 123, "1002", observation, quote)
    with pytest.raises(SimulatedCrash):
        pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)
    assert OrderSafetyStore(path).status(KEY)["state"] == "submitting"
    lookup = SyntheticOrderAdapter(lookup_result=synthetic_receipt(quote))
    reopened = OfflinePurchasePipeline(
        CandidatePipeline(database, claims=CartClaimStore(claims.path), clock=lambda: now[0]),
        OrderSafetyStore(path),
        executor=lookup,
        clock=lambda: now[0],
    )
    with pytest.raises(OrderStopped, match="attempt_already_started_or_missing"):
        reopened.simulate_attempt(KEY, 123, "1002", observation, quote)
    assert lookup.submit_calls == 0
    assert reopened.reconcile(KEY)["state"] == "confirmed"
    assert lookup.lookup_calls == 1


def test_full_synthetic_unknown_outcome_stays_fenced_until_lookup(setup, tmp_path):
    _, _, _, _, path, pipeline, observation, quote = synthetic_order_flow(setup, tmp_path)
    adapter = SyntheticOrderAdapter(submit_result=synthetic_receipt(quote, total="51.00"))
    pipeline.executor = adapter
    pipeline.prepare(GRANT, KEY, 123, "1002", observation, quote)
    assert pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)["state"] == "uncertain"
    assert OrderSafetyStore(path).status(KEY)["requires_reconciliation"] is True
    with pytest.raises(OrderStopped, match="attempt_already_started_or_missing"):
        pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)
    adapter.lookup_result = synthetic_receipt(quote)
    assert pipeline.reconcile(KEY)["state"] == "confirmed"
    assert adapter.submit_calls == 1 and adapter.lookup_calls == 1


def test_link_to_snapshot_review_one_copy_and_no_order_rehearsal(setup, tmp_path):
    database, now, claims = setup
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    assert candidates.review_one()["status"] == "idle"
    assert candidates.reserve_rehearsal(123, "1002", KEY)["state"] == "reserved"
    with pytest.raises(CandidateStopped, match="one_copy_reserved"):
        candidates.reserve_rehearsal(123, "1002", OTHER)
    # Cart-observed is fixture state, not a browser mutation or a live assertion.
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    bundle = candidates.review_bundle(123, "1002", KEY)
    assert bundle["purchase_allowed"] is False
    assert bundle["listing"]["evaluation"]["eligible_for_review"] is True
    if shutil.which("node"):
        path = tmp_path / "synthetic-bundle.json"
        path.write_text(json.dumps(bundle))
        result = subprocess.run(
            ["node", "tests/verify_discogs_candidate_rehearsal.cjs", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    reopened = CandidatePipeline(database, claims=CartClaimStore(claims.path), clock=lambda: now[0])
    assert reopened.review_one()["status"] == "disabled"
    assert count(database) == 1
    assert (
        reopened.review_bundle(123, "1002", KEY)["candidate_sequence"]
        == bundle["candidate_sequence"]
    )


def test_changed_price_reappearance_and_restart_keep_one_candidate(setup):
    database, now, claims = setup
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    first = candidates.reserve_rehearsal(123, "1002", KEY)
    assert first["purchase_allowed"] is False
    # A fresh changed price creates a new generation of the same durable candidate.
    now[0] += MIN_INTERVAL + 1
    altered = page(now[0], (1001, 1002))
    altered["rows"][1]["fields"]["price"] = "$8.00"
    scheduler.reader = lambda _: altered
    assert scheduler.tick()["status"] == "changed"
    restarted = CandidatePipeline(
        database,
        reader=lambda _, ident: make_observation(now[0], ident, item="8"),
        claims=claims,
        clock=lambda: now[0],
    )
    assert count(database) == 1
    assert restarted.review_one()["status"] == "review_only"
    assert restarted.review_bundle(123, "1002", KEY)["listing"]["evaluation"]["total"] == "18.00"
    with pytest.raises(CandidateStopped, match="one_copy_reserved"):
        restarted.reserve_rehearsal(123, "1002", OTHER)
    now[0] += MIN_INTERVAL + 1
    scheduler.reader = lambda _: page(now[0], (1001,))
    assert scheduler.tick()["status"] == "changed"
    now[0] += MIN_INTERVAL + 1
    scheduler.reader = lambda _: page(now[0], (1001, 1002))
    assert scheduler.tick()["status"] == "changed"
    assert count(database) == 1
    assert restarted.review_one()["status"] == "review_only"
    with pytest.raises(CandidateStopped, match="one_copy_reserved"):
        restarted.reserve_rehearsal(123, "1002", OTHER)
    assert restarted.review_one()["status"] == "idle"


@pytest.mark.parametrize("change", ["pause", "rules", "owned", "control"])
def test_inflight_state_change_invalidates_review_and_claim(setup, change):
    database, now, claims = setup
    scheduler = None

    def read(_release, ident):
        store = RulesStore(database)
        try:
            if change == "pause":
                scheduler.set_paused(True, expected_revision=scheduler.health()["revision"])
            elif change == "rules":
                store.save(target(max_total="49").model_dump(), store.snapshot()["revision"])
            elif change == "owned":
                store.mark_owned(123, True, store.snapshot()["revision"])
            else:
                with store.db:
                    store.db.execute("UPDATE cloud_control SET revision=revision+1")
        finally:
            store.close()
        return make_observation(now[0], ident)

    candidates, scheduler = flow(database, now, claims, candidate_reader=read)
    assert candidates.review_one()["status"] == "uncertain"
    assert candidates.review_one()["status"] == "idle"
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"shipping": None}, "blocked"),
        ({"tax": None}, "blocked"),
        ({"seller_comments": "Vinyl is warped."}, "blocked"),
        ({"seller_comments": "No disc 1."}, "blocked"),
        ({"seller_comments": "Might skip."}, "blocked"),
        ({"seller_percent": 90.0}, "blocked"),
        ({"media": "VG"}, "blocked"),
        ({"source": "discogs_public_listing"}, "uncertain"),
    ],
)
def test_unknown_total_damage_condition_and_unverified_source_never_claim(setup, changes, expected):
    database, now, claims = setup
    candidates, _ = flow(
        database,
        now,
        claims,
        candidate_reader=lambda _, ident: make_observation(now[0], ident, **changes),
    )
    assert candidates.review_one()["status"] == expected
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


@pytest.mark.parametrize("change", ["pause", "rules", "owned", "control"])
def test_review_is_invalidated_before_one_copy_reservation(setup, change):
    database, now, claims = setup
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    store = RulesStore(database)
    try:
        if change == "pause":
            scheduler.set_paused(True, expected_revision=scheduler.health()["revision"])
        elif change == "rules":
            changed_target = target().model_dump()
            changed_target["max_total"] = "49"
            store.save(changed_target, store.snapshot()["revision"])
        elif change == "owned":
            store.mark_owned(123, True, store.snapshot()["revision"])
        else:
            with store.db:
                store.db.execute("UPDATE cloud_control SET revision=revision+1")
    finally:
        store.close()
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


def test_competing_workers_claim_candidate_once(setup):
    database, now, claims = setup
    entered, release = threading.Event(), threading.Event()

    def read(_release, ident):
        entered.set()
        assert release.wait(2)
        return make_observation(now[0], ident)

    candidates, _ = flow(database, now, claims, candidate_reader=read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(candidates.review_one)
        assert entered.wait(2)
        second = pool.submit(
            CandidatePipeline(database, reader=read, claims=claims, clock=lambda: now[0]).review_one
        )
        assert second.result(timeout=2)["status"] == "idle"
        release.set()
        assert first.result(timeout=2)["status"] == "review_only"
    assert count(database) == 1


def test_interrupted_or_failed_candidate_read_never_retries_itself(setup):
    database, now, claims = setup

    def failed(_release, _ident):
        raise TimeoutError("synthetic uncertain read")

    candidates, _ = flow(database, now, claims, candidate_reader=failed)
    assert candidates.review_one()["status"] == "uncertain"
    reopened = CandidatePipeline(
        database,
        reader=lambda _, ident: make_observation(now[0], ident),
        claims=claims,
        clock=lambda: now[0],
    )
    assert reopened.review_one()["status"] == "idle"
    assert count(database) == 1
    with pytest.raises(CandidateStopped):
        reopened.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


def test_pause_between_review_and_claim_commit_stops_with_reserved_slot(setup):
    database, now, claims = setup
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    original_reserve = claims.reserve

    def racing_reserve(*args):
        result = original_reserve(*args)
        scheduler.set_paused(True, expected_revision=scheduler.health()["revision"])
        return result

    claims.reserve = racing_reserve
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY).state == "reserved"
    assert claims.get(KEY).purchase_allowed is False


def public_detail(now, *, reported_release=123, status=200, comments="All vinyl included."):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path == "/marketplace/listings/1002":
            return httpx.Response(
                status,
                headers={"Location": "https://elsewhere.test/"} if status == 302 else None,
                json={
                    "id": 1002,
                    "release": {"id": reported_release},
                    "status": "For Sale",
                    "condition": "Mint (M)",
                    "sleeve_condition": "Near Mint (NM or M-)",
                    "seller": {"stats": {"rating": "100", "total": 10020}},
                    "price": {"value": "20.00", "currency": "USD"},
                    "shipping_price": {"value": "5.00", "currency": "USD"},
                    "shipping_is_blocked": False,
                    "comments": comments,
                },
            )
        assert request.url.path == "/releases/123"
        return httpx.Response(200, json={"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}]})

    reader = ListingReader(transport=httpx.MockTransport(handle), wall_clock=lambda: now[0])
    return PublicKnownListingCandidateReader(reader=reader, clock=lambda: now[0]), calls


def test_fixed_public_detail_qualifies_only_as_blocked_live_review(setup):
    database, now, claims = setup
    reader, calls = public_detail(now)
    candidates, _ = flow(database, now, claims, candidate_reader=reader)
    result = candidates.review_one()
    assert result["status"] == "blocked" and result["purchase_allowed"] is False
    assert calls == [
        "https://api.discogs.com/marketplace/listings/1002",
        "https://api.discogs.com/releases/123",
    ]
    store = RulesStore(database)
    try:
        review = json.loads(store.db.execute("SELECT review FROM discogs_candidates").fetchone()[0])
    finally:
        store.close()
    assert review["detail_evidence"] == {
        "source": "discogs_public_listing",
        "fixed_route": "/marketplace/listings/1002",
        "reported_shipping": "5.00",
        "shipping_blocked": False,
        "cart_state": "unknown",
        "seller_handle": None,
        "delivered_total_verified": False,
    }
    assert review["evaluation"]["mode"] == "live_read_only"
    assert review["evaluation"]["total"] is None
    assert not review["evaluation"]["eligible_for_review"]
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


@pytest.mark.parametrize(
    "comments,reason",
    [
        ("No disc 1.", "Playable vinyl set is incomplete"),
        ("Vinyl is warped.", "Seller comments disclose playable-media damage"),
        ("No skips. All vinyl included.", "Item, shipping, tax and mandatory fees"),
    ],
)
def test_public_detail_preserves_completeness_damage_and_unknown_total(setup, comments, reason):
    database, now, claims = setup
    reader, _ = public_detail(now, comments=comments)
    candidates, _ = flow(database, now, claims, candidate_reader=reader)
    assert candidates.review_one()["status"] == "blocked"
    store = RulesStore(database)
    try:
        review = json.loads(store.db.execute("SELECT review FROM discogs_candidates").fetchone()[0])
    finally:
        store.close()
    assert any(reason in text for text in review["evaluation"]["reasons"])
    assert review["evaluation"]["total"] is None
    assert claims.get(KEY) is None


@pytest.mark.parametrize("failure", ["redirect", "wrong_release", "stale", "bad_route"])
def test_public_detail_provenance_failure_stops_without_claim(setup, failure):
    database, now, claims = setup
    reader, calls = public_detail(
        now,
        reported_release=124 if failure == "wrong_release" else 123,
        status=302 if failure == "redirect" else 200,
    )
    if failure in {"stale", "bad_route"}:
        original = reader

        def reader(release, ident):
            detail = original(release, ident)
            return replace(
                detail,
                completed_at=now[0] - 301 if failure == "stale" else detail.completed_at,
                route="/marketplace/listings/other" if failure == "bad_route" else detail.route,
            )

    candidates, _ = flow(database, now, claims, candidate_reader=reader)
    assert candidates.review_one()["status"] == "uncertain"
    assert candidates.review_one()["status"] == "idle"
    assert len(calls) == (2 if failure in {"stale", "bad_route"} else 1)
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


def hosted_reply(now, *, release=123, listing="1002", **changes):
    page = {
        "schema": "discogs_listing_detail_dom_v1",
        "source": "discogs_rendered_listing",
        "status": "observed",
        "complete": True,
        "release_id": release,
        "listing_id": listing,
        "url": f"https://www.discogs.com/shop/item/{listing}",
        "observed_at": datetime.fromtimestamp(now[0], UTC).isoformat(),
        "media": "M",
        "sleeve": "NM",
        "seller_percent": 100.0,
        "feedback": 10020,
        "seller_handle": "Synthetic_1",
        "item": "199.99",
        "currency": None,
        "reported_shipping_text": "$0.00 shipping",
        "seller_comments": "No skips. Both records included.",
        "seller_comments_supported": True,
        "available": True,
        "cart_state": "already_in_cart",
        "purchase_allowed": False,
    }
    page.update(changes)
    return {
        "status": "observed",
        "source": "hosted_browser_read_only",
        "observation": page,
        "stop_required": True,
        "purchase_allowed": False,
    }


def one_private_socket(reply):
    path = Path("/tmp") / f"sniper-listing-{uuid.uuid4().hex[:12]}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(1)
    requests = []

    def serve():
        try:
            with listener.accept()[0] as connection:
                with connection.makefile("rb") as stream:
                    requests.append(json.loads(stream.readline(1024)))
                connection.sendall((json.dumps(reply()) + "\n").encode())
        finally:
            listener.close()
            path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return path, requests, thread


@pytest.fixture
def unix_socket_only(monkeypatch):
    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            raise AssertionError("Offline tests must not open network connections")
        error = sock.connect_ex(address)
        if error:
            raise OSError(error, "Isolated Unix socket connect failed")

    monkeypatch.setattr(socket.socket, "connect", connect)


def test_hosted_item_reader_connects_to_candidate_review_but_never_claims_total(
    setup, unix_socket_only
):
    database, now, claims = setup
    path, requests, thread = one_private_socket(lambda: hosted_reply(now))
    reader = HostedListingCandidateReader(path, clock=lambda: now[0])
    candidates, _ = flow(database, now, claims, candidate_reader=reader)
    assert candidates.review_one()["status"] == "blocked"
    thread.join(2)
    assert requests == [{"action": "observe_listing", "release_id": "123", "listing_id": "1002"}]
    store = RulesStore(database)
    try:
        review = json.loads(store.db.execute("SELECT review FROM discogs_candidates").fetchone()[0])
    finally:
        store.close()
    assert review["detail_evidence"] == {
        "source": "discogs_rendered_listing",
        "fixed_route": "/sell/item/1002",
        "reported_shipping": "$0.00 shipping",
        "shipping_blocked": None,
        "cart_state": "already_in_cart",
        "seller_handle": "Synthetic_1",
        "delivered_total_verified": False,
    }
    assert review["evaluation"]["total"] is None
    assert review["evaluation"]["mode"] == "live_read_only"
    assert not review["evaluation"]["eligible_for_review"]
    with pytest.raises(CandidateStopped):
        candidates.reserve_rehearsal(123, "1002", KEY)
    assert claims.get(KEY) is None


@pytest.mark.parametrize("change", ["wrong_release", "stale", "extra_total", "blocked"])
def test_hosted_item_reader_rejects_unverified_or_blocked_response(setup, change, unix_socket_only):
    database, now, claims = setup

    def reply():
        if change == "blocked":
            return {
                "status": "blocked",
                "code": "access_restricted",
                "source": "hosted_browser_read_only",
                "stop_required": True,
                "purchase_allowed": False,
            }
        fields = {
            "release_id": 124 if change == "wrong_release" else 123,
            "observed_at": datetime.fromtimestamp(now[0] - 301, UTC).isoformat()
            if change == "stale"
            else datetime.fromtimestamp(now[0], UTC).isoformat(),
        }
        if change == "extra_total":
            fields["tax"] = "0.00"
        return hosted_reply(now, **fields)

    path, requests, thread = one_private_socket(reply)
    candidates, _ = flow(
        database,
        now,
        claims,
        candidate_reader=HostedListingCandidateReader(path, clock=lambda: now[0]),
    )
    assert candidates.review_one()["status"] == "uncertain"
    thread.join(2)
    assert len(requests) == 1
    assert candidates.review_one()["status"] == "idle"
    assert claims.get(KEY) is None


def test_hosted_item_reader_reports_only_known_safe_stop_code(setup, unix_socket_only):
    _, now, _ = setup

    def reply():
        return {
            "status": "blocked",
            "code": "listing_not_ready",
            "source": "hosted_browser_read_only",
            "stop_required": True,
            "purchase_allowed": False,
        }

    path, _, thread = one_private_socket(reply)
    with pytest.raises(ListingReadError, match="listing_not_ready"):
        HostedListingCandidateReader(path, clock=lambda: now[0])(123, "1002")
    thread.join(2)
