"""Synthetic socket/DB tests. No browser, cart mutation or order is contacted."""

import io
import json
import socket
from dataclasses import replace

import pytest
from test_discogs_candidate_pipeline import KEY, flow
from test_discogs_candidate_pipeline import setup as source_setup

from finder.discogs_hosted_checkout import (
    CartEvidence,
    CheckoutStopped,
    HostedCartReadOnlyReader,
    HostedCheckoutPreflight,
)


@pytest.fixture
def candidate_state(tmp_path):
    return source_setup.__wrapped__(tmp_path)


def cart_reply(*, tax_hidden=True, item="199.99", shipping="5.85", total="205.84"):
    return {
        "status": "blocked",
        "code": "final_quote_unverified",
        "source": "hosted_browser_read_only",
        "stop_required": True,
        "purchase_allowed": False,
        "observations": [
            "expected_listing_link_observed",
            "single_cart_row_observed",
            *(["tax_hidden_unknown"] if tax_hidden else []),
        ],
        "observed_quote": {
            "source": "visible_cart",
            "final_quote_verified": False,
            "subtotal": {"amount": item, "currency": "USD"},
            "shipping": {"amount": shipping, "currency": "USD"},
            "tax": None,
            "fees": None,
            "total": {"amount": total, "currency": "USD"},
        },
        "observed_shipping_method": {
            "source": "visible_cart",
            "selected_label": f"Standard - USPS - ${shipping}",
            "selection_verified": False,
        },
    }


def fake_socket(monkeypatch, reply, *, record=None):
    """Exercise the fixed wire request without sandbox-dependent AF_UNIX binding."""

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, _path):
            pass

        def sendall(self, payload):
            if record is not None:
                record.append(json.loads(payload))

        def makefile(self, _mode):
            return io.BytesIO((json.dumps(reply) + "\n").encode())

    monkeypatch.setattr(socket, "socket", lambda *_: Connection())
    monkeypatch.setattr(HostedCartReadOnlyReader, "_private", lambda self: True)


def test_read_existing_cart_only_and_never_promote_displayed_total(monkeypatch):
    requests = []
    fake_socket(monkeypatch, cart_reply(), record=requests)
    evidence = HostedCartReadOnlyReader()("1002")
    assert requests == [{"action": "current_cart", "listing_id": "1002"}]
    assert evidence.displayed_total == ("205.84", "USD")
    assert evidence.tax is None and evidence.fees is None
    assert evidence.tax_hidden_unknown
    assert not evidence.final_quote_verified and not evidence.purchase_allowed
    assert not evidence.destination_verified


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"purchase_allowed": True}, "cart_response_unverified"),
        ({"status": "observed"}, "cart_response_unverified"),
        ({"code": "access_restricted"}, "cart_read_stopped"),
        ({"observations": []}, "cart_identity_unverified"),
        ({"observed_quote": {"source": "visible_cart"}}, "cart_quote_unverified"),
    ],
)
def test_unverified_socket_responses_stop(monkeypatch, change, code):
    reply = {**cart_reply(), **change}
    fake_socket(monkeypatch, reply)
    with pytest.raises(CheckoutStopped, match=f"^{code}$"):
        HostedCartReadOnlyReader()("1002")


def test_read_requires_private_socket(tmp_path):
    with pytest.raises(CheckoutStopped, match="private_cart_reader_unavailable"):
        HostedCartReadOnlyReader(tmp_path / "not-a-private-socket")("1002")


def test_integrated_candidate_claim_and_cart_remain_no_order(candidate_state, monkeypatch):
    database, now, claims = candidate_state
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    fake_socket(monkeypatch, cart_reply(item="40.00", shipping="5.00", total="45.00"))
    result = HostedCheckoutPreflight(candidates, HostedCartReadOnlyReader()).inspect(
        123, "1002", KEY
    )
    assert result["code"] == "final_payable_quote_unverified"
    assert result["claim_state"] == "cart_observed"
    assert result["evidence"].tax_hidden_unknown
    assert result["order_submission"] == "unavailable"
    assert result["purchase_allowed"] is False


def test_observed_cart_above_rule_ceiling_stops(candidate_state, monkeypatch):
    database, now, claims = candidate_state
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    fake_socket(monkeypatch, cart_reply())
    with pytest.raises(CheckoutStopped, match="observed_total_above_ceiling"):
        HostedCheckoutPreflight(candidates, HostedCartReadOnlyReader()).inspect(123, "1002", KEY)


def test_stale_or_unreconciled_claim_stops_before_browser_read(candidate_state):
    database, now, claims = candidate_state
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)

    def reader(_listing):
        pytest.fail("cart read should not start")

    preflight = HostedCheckoutPreflight(candidates, reader)
    with pytest.raises(CheckoutStopped, match="cart_outcome_requires_reconciliation"):
        preflight.inspect(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    scheduler.set_paused(True, expected_revision=1)
    with pytest.raises(CheckoutStopped, match="candidate_or_claim_unavailable"):
        preflight.inspect(123, "1002", KEY)


def test_revision_change_during_read_invalidates_evidence(candidate_state):
    database, now, claims = candidate_state
    candidates, scheduler = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")

    def change_revision(ident):
        assert ident == "1002"
        scheduler.set_paused(True, expected_revision=1)
        return CartEvidence("1002", None, None, None, None, None, None, True)

    with pytest.raises(CheckoutStopped, match="candidate_changed_during_cart_read"):
        HostedCheckoutPreflight(candidates, change_revision).inspect(123, "1002", KEY)


def test_injected_reader_cannot_promote_unverified_cart(candidate_state):
    database, now, claims = candidate_state
    candidates, _ = flow(database, now, claims)
    assert candidates.review_one()["status"] == "review_only"
    candidates.reserve_rehearsal(123, "1002", KEY)
    claims.transition(KEY, "attempting")
    claims.transition(KEY, "cart_observed")
    evidence = CartEvidence(
        listing_id="1002",
        subtotal=("40.00", "USD"),
        shipping=("5.00", "USD"),
        tax=None,
        fees=None,
        displayed_total=("45.00", "USD"),
        shipping_method_label="Standard - USPS - $5.00",
        tax_hidden_unknown=True,
    )
    for invalid in (
        replace(evidence, listing_id="1003"),
        replace(evidence, final_quote_verified=True),
        replace(evidence, purchase_allowed=True),
    ):
        with pytest.raises(CheckoutStopped, match="cart_evidence_unverified"):
            HostedCheckoutPreflight(candidates, lambda _listing, value=invalid: value).inspect(
                123, "1002", KEY
            )
