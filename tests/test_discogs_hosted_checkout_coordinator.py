"""Offline only: no site, browser, cart mutation, or order submission."""

from dataclasses import replace

import pytest
from test_discogs_candidate_pipeline import (
    DESTINATION,
    GRANT,
    KEY,
    OTHER,
    PAYMENT,
    flow,
    synthetic_order_flow,
)
from test_discogs_candidate_pipeline import setup as source_setup

from finder.discogs_hosted_checkout import CartEvidence, CheckoutStopped, HostedCheckoutPreflight
from finder.discogs_hosted_checkout_coordinator import (
    CheckoutBindings,
    FixtureFinalQuoteEvidence,
    FixtureQuoteAdapter,
    HostedNoOrderCoordinator,
)
from finder.discogs_order_safety import OrderSafetyStore, SyntheticOrderAdapter
from finder.discogs_rules import RulesStore


@pytest.fixture
def ready(tmp_path):
    database, now, claims = source_setup.__wrapped__(tmp_path)
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    reads = []
    evidence = CartEvidence(
        "1002",
        ("40.00", "USD"),
        ("5.00", "USD"),
        ("5.00", "USD"),
        None,
        ("50.00", "USD"),
        "Standard",
        False,
    )

    def reader(ident):
        reads.append(ident)
        return evidence

    preflight = HostedCheckoutPreflight(candidates, reader)
    orders = OrderSafetyStore(tmp_path / "hosted-orders.db", create=True)
    return candidates, preflight, orders, reads, evidence


def bindings(revision=1):
    return CheckoutBindings(
        rules_revision=revision, destination_ref=DESTINATION, payment_ref=PAYMENT
    )


def fixture_quote(**changes):
    values = {
        "source": "offline_checkout_fixture",
        "release_id": 123,
        "listing_id": "1002",
        "rules_revision": 1,
        "item": "40.00",
        "shipping": "5.00",
        "tax": "5.00",
        "fees": "0.00",
        "total": "50.00",
        "currency": "USD",
        "destination_ref": DESTINATION,
        "payment_ref": PAYMENT,
    }
    values.update(changes)
    return FixtureFinalQuoteEvidence.model_validate(values)


def test_default_unavailable_and_submit_is_uncallable(ready):
    candidates, preflight, orders, reads, _ = ready
    coordinator = HostedNoOrderCoordinator(candidates, preflight, orders=orders)
    assert coordinator.readiness()["final_quote_adapter"] == "unavailable"
    assert coordinator.readiness()["live_order_submission_enabled"] is False
    result = coordinator.inspect(123, "1002", KEY)
    assert result["code"] == "destination_payment_binding_unavailable"
    assert result["final_payable_quote_verified"] is False
    assert reads == ["1002"]
    assert orders.status(KEY) is None
    with pytest.raises(CheckoutStopped, match="live_order_submission_unavailable"):
        coordinator.submit()
    assert orders.status(KEY) is None


def test_exact_bindings_and_offline_fixture_never_promote_to_live_quote(ready):
    candidates, preflight, orders, reads, _ = ready
    no_adapter = HostedNoOrderCoordinator(candidates, preflight, orders=orders)
    assert no_adapter.inspect(123, "1002", KEY, bindings=bindings())["code"] == (
        "final_quote_adapter_unavailable"
    )
    coordinator = HostedNoOrderCoordinator(
        candidates,
        preflight,
        orders=orders,
        quote_adapter=FixtureQuoteAdapter(fixture_quote()),
    )
    for _ in range(2):
        result = coordinator.inspect(123, "1002", KEY, bindings=bindings())
        assert result["code"] == "offline_quote_cannot_authorize_purchase"
        assert result["purchase_allowed"] is False
        assert orders.status(KEY) is None
    assert reads == ["1002", "1002", "1002"]


def test_stale_refs_or_changed_quote_stop(ready):
    candidates, preflight, orders, _, _ = ready
    coordinator = HostedNoOrderCoordinator(
        candidates,
        preflight,
        orders=orders,
        quote_adapter=FixtureQuoteAdapter(fixture_quote()),
    )
    with pytest.raises(CheckoutStopped, match="checkout_bindings_stale"):
        coordinator.inspect(123, "1002", KEY, bindings=bindings(0))
    with pytest.raises(ValueError, match="canonical opaque reference"):
        CheckoutBindings(rules_revision=1, destination_ref="street address", payment_ref=PAYMENT)
    for changed in (
        fixture_quote(release_id=124),
        fixture_quote(payment_ref=GRANT),
        fixture_quote(item="39.00", total="49.00"),
    ):
        variant = HostedNoOrderCoordinator(
            candidates,
            preflight,
            orders=orders,
            quote_adapter=FixtureQuoteAdapter(changed),
        )
        with pytest.raises(CheckoutStopped, match="fixture_quote_"):
            variant.inspect(123, "1002", KEY, bindings=bindings())
    assert orders.status(KEY) is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"tax": None, "tax_hidden_unknown": True}, "cart_tax_unverified"),
        ({"displayed_total": None}, "cart_total_unverified"),
    ],
)
def test_unknown_cart_tax_or_total_stops_even_with_a_fixture_quote(ready, change, reason):
    candidates, _, orders, _, evidence = ready
    preflight = HostedCheckoutPreflight(candidates, lambda _listing: replace(evidence, **change))
    coordinator = HostedNoOrderCoordinator(
        candidates,
        preflight,
        orders=orders,
        quote_adapter=FixtureQuoteAdapter(fixture_quote()),
    )
    assert coordinator.inspect(123, "1002", KEY, bindings=bindings())["code"] == reason
    assert orders.status(KEY) is None


def test_rule_change_during_fixture_quote_read_invalidates_handoff(ready, monkeypatch):
    candidates, preflight, orders, _, _ = ready
    adapter = FixtureQuoteAdapter(fixture_quote())

    def change_rule(_self):
        store = RulesStore(candidates.database)
        saved = store.snapshot()
        store.save(saved["targets"][0], saved["revision"])
        store.close()
        return adapter.quote

    monkeypatch.setattr(FixtureQuoteAdapter, "read", change_rule)
    coordinator = HostedNoOrderCoordinator(
        candidates, preflight, orders=orders, quote_adapter=adapter
    )
    with pytest.raises(CheckoutStopped, match="candidate_changed_after_quote_read"):
        coordinator.inspect(123, "1002", KEY, bindings=bindings())
    assert orders.status_for_release(123) is None


def test_existing_uncertain_intent_stops_before_a_cart_read(tmp_path):
    state = source_setup.__wrapped__(tmp_path)
    _, _, claims, _, path, pipeline, observation, quote = synthetic_order_flow(state, tmp_path)
    pipeline.prepare(GRANT, KEY, 123, "1002", observation, quote)
    pipeline.executor = SyntheticOrderAdapter(submit_result=None)
    assert pipeline.simulate_attempt(KEY, 123, "1002", observation, quote)["state"] == "uncertain"
    candidates = pipeline.candidates
    reads = []
    coordinator = HostedNoOrderCoordinator(
        candidates,
        HostedCheckoutPreflight(candidates, lambda ident: reads.append(ident)),
        orders=OrderSafetyStore(path),
    )
    with pytest.raises(CheckoutStopped, match="existing_order_intent_blocks_hosted_checkout"):
        coordinator.inspect(123, "1002", OTHER, bindings=bindings())
    assert reads == []
    assert coordinator.orders.status_for_release(123)["state"] == "uncertain"


def test_fixture_quote_requires_explicit_all_in_arithmetic():
    with pytest.raises(ValueError):
        fixture_quote(tax="6.00")
