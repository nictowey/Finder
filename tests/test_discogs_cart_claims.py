"""Synthetic durable claim tests; no Discogs, browser, or order connection."""

import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from finder.discogs_cart_claims import CartClaimBlocked, CartClaimStore

KEY = "123e4567-e89b-42d3-a456-426614174000"
OTHER = "123e4567-e89b-42d3-a456-426614174001"


def test_one_copy_and_idempotency_survive_restart(tmp_path):
    path = tmp_path / "claims.sqlite"
    store = CartClaimStore(path, create=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    claim = store.reserve(111, "222", 3, KEY)
    assert claim.response()["purchase_allowed"] is False
    assert store.reserve(111, "222", 3, KEY) == claim
    with pytest.raises(CartClaimBlocked, match="idempotency_conflict"):
        store.reserve(111, "223", 3, KEY)
    with pytest.raises(CartClaimBlocked, match="one_copy_reserved"):
        store.reserve(111, "223", 3, OTHER)
    reopened = CartClaimStore(path)
    assert reopened.get(KEY) == claim
    assert reopened.transition(KEY, "attempting").state == "attempting"
    with pytest.raises(CartClaimBlocked, match="claim_already_used_or_uncertain"):
        reopened.transition(KEY, "attempting")
    assert CartClaimStore(path).get(KEY).state == "attempting"
    assert reopened.transition(KEY, "uncertain").state == "uncertain"
    with pytest.raises(CartClaimBlocked, match="claim_already_used_or_uncertain"):
        reopened.transition(KEY, "cart_observed")
    with pytest.raises(CartClaimBlocked, match="one_copy_reserved"):
        reopened.reserve(111, "222", 4, OTHER)


def test_observed_cart_is_terminal_and_never_order_permission(tmp_path):
    store = CartClaimStore(tmp_path / "claims.sqlite", create=True)
    store.reserve(111, "222", 3, KEY)
    store.transition(KEY, "attempting")
    observed = store.transition(KEY, "cart_observed")
    assert observed.response() == {
        "release_id": 111,
        "listing_id": "222",
        "rules_revision": 3,
        "idempotency_key": KEY,
        "state": "cart_observed",
        "mode": "durable_cart_rehearsal",
        "purchase_allowed": False,
    }
    with pytest.raises(CartClaimBlocked, match="claim_already_used_or_uncertain"):
        store.transition(KEY, "uncertain")


def test_competing_callers_only_reserve_one_release(tmp_path):
    path = tmp_path / "claims.sqlite"
    CartClaimStore(path, create=True)

    def reserve(key):
        try:
            return CartClaimStore(path).reserve(111, "222", 3, key).state
        except CartClaimBlocked as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, [KEY, OTHER]))
    assert sorted(outcomes) == ["one_copy_reserved", "reserved"]


def test_invalid_identity_and_unknown_file_never_reserve(tmp_path):
    path = tmp_path / "claims.sqlite"
    with pytest.raises(ValueError, match="explicitly initialize"):
        CartClaimStore(path)
    store = CartClaimStore(path, create=True)
    for release, listing, revision, key in [
        (0, "222", 3, KEY),
        (True, "222", 3, KEY),
        (111, "0222", 3, KEY),
        (111, "222", -1, KEY),
        (111, "222", 3, "not-a-uuid"),
    ]:
        with pytest.raises(CartClaimBlocked):
            store.reserve(release, listing, revision, key)
    assert store.get(KEY) is None
