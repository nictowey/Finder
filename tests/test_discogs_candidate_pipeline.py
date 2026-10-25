"""One disposable synthetic flow; no Discogs access, real cart or order path."""

import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_hosted_scheduler import page
from test_discogs_rules import observation, target

from finder.cloud_app import CloudApp
from finder.discogs_candidate_pipeline import CandidatePipeline, CandidateStopped
from finder.discogs_cart_claims import CartClaimStore
from finder.discogs_hosted_scheduler import MIN_INTERVAL, HostedReleaseScheduler
from finder.discogs_rules import RulesStore

KEY = "123e4567-e89b-42d3-a456-426614174000"
OTHER = "123e4567-e89b-42d3-a456-426614174001"


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
