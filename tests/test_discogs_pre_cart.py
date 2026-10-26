"""Quote-inspection eligibility is distinct from purchase qualification."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_discogs_rules import NOW, observation, target

from finder.discogs_rules import evaluate, evaluate_pre_cart


def hosted(**changes):
    fields = {
        "source": "discogs_rendered_listing",
        "listing_id": "1002",
        "pressing_evidence": "unknown",
        "item": "20.00",
        "currency": None,
        "shipping": None,
        "tax": None,
        "fees": None,
        "destination_country": None,
        "destination_postal": None,
        "seller_comments": "Clean copy.",
        "release_media_metadata": {"id": 123, "formats": [{"name": "Vinyl", "qty": "1"}]},
        "release_media_provenance": {
            "source": "discogs_release_api",
            "release_id": 123,
            "fetched_at": NOW.isoformat(),
        },
    }
    fields.update(changes)
    return observation(**fields)


def test_unknown_quote_allows_only_cart_inspection_without_promoting_fields():
    item = hosted()
    before = deepcopy(item.model_dump())
    result = evaluate_pre_cart(target(), item, now=NOW)
    assert result["eligible_for_cart_inspection"] is True
    assert result["mode"] == "hosted_cart_inspection_only"
    assert result["final_quote_verified"] is False and result["purchase_allowed"] is False
    assert set(result["quote_unresolved"]) == {
        "currency",
        "shipping",
        "tax",
        "fees",
        "physical_pressing",
        "destination",
        "final_payable_total",
    }
    final = evaluate(target(), item, now=NOW)
    assert final["eligible_for_review"] is False and final["total"] is None
    assert final["purchase_allowed"] is False
    assert item.model_dump() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "synthetic"},
        {"source": "discogs_public_listing"},
        {"release_id": 124},
        {"available": False},
        {"media": "P"},
        {"sleeve": "Generic"},
        {"seller_percent": 98.0},
        {"feedback": None},
        {"item": None},
        {"pressing_evidence": "conflicting"},
        {"observed_at": (NOW - timedelta(minutes=6)).isoformat()},
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"release_media_provenance": None},
        {"seller_comments": "No disc 1."},
        {"seller_comments": "Vinyl is warped."},
        {"seller_comments": "surface scratches, plays nice, no skips"},
        {"seller_comments_supported": False},
        {"currency": "EUR"},
        {"currency": "USD", "item": "50.01"},
        {"currency": "USD", "item": "49.00", "shipping": "2.00"},
        {"destination_country": "CA"},
        {"destination_country": "US", "destination_postal": "90210"},
    ],
)
def test_item_safety_filters_still_block_before_cart_inspection(changes):
    result = evaluate_pre_cart(target(), hosted(**changes), now=NOW)
    assert result["eligible_for_cart_inspection"] is False
    assert result["reasons"]
    assert result["purchase_allowed"] is False


def test_owned_or_paused_rules_block_and_accepted_generic_remains_explicit():
    assert not evaluate_pre_cart(target(), hosted(), now=NOW, already_owned=True)[
        "eligible_for_cart_inspection"
    ]
    assert not evaluate_pre_cart(target(enabled=False), hosted(), now=NOW)[
        "eligible_for_cart_inspection"
    ]
    result = evaluate_pre_cart(
        target(allow_generic_sleeve=True, min_seller_percent=0, min_feedback=0),
        hosted(sleeve="Generic", seller_percent=None, feedback=None),
        now=NOW,
    )
    assert result["eligible_for_cart_inspection"] is True


def test_known_price_limit_is_inclusive_but_does_not_establish_a_final_quote():
    result = evaluate_pre_cart(target(), hosted(currency="USD", item="50.00"), now=NOW)
    assert result["eligible_for_cart_inspection"] is True
    assert "currency" not in result["quote_unresolved"]
    assert "final_payable_total" in result["quote_unresolved"]
    assert result["final_quote_verified"] is False
