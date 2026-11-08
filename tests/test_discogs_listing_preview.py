import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from finder.discogs_listing import ListingReadError, money
from finder.discogs_listing_preview import PreviewReader, evaluate_preview, main, preview_price


def payload():
    return {
        "id": 123,
        "release": {"id": 456},
        "condition": "Near Mint (NM or M-)",
        "sleeve_condition": "Mint (M)",
        "price": {"currency": "USD", "value": Decimal("166.66666666666666")},
        "original_price": {"curr_abbr": "GBP", "value": Decimal("125.0")},
        "shipping_price": {},
        "shipping_is_blocked": True,
        "comments": "unplayed copy",
        "status": "For Sale",
    }


def test_converted_preview_does_not_weaken_purchase_money():
    data = payload()
    result = evaluate_preview(data, 123, 456)
    assert money(data["price"]) == (None, "USD")
    assert result["price"] == {
        "currency": "USD",
        "raw_value": "166.66666666666666",
        "display_value": "166.67",
    }
    assert result["original_price"]["display_value"] == "125.00"
    assert result["item_criteria_match"] is True
    assert result["purchase_qualified"] is result["purchased"] is False
    assert result["media_completeness"]["status"] == "unknown"
    assert result["shipping"]["reported"] is None
    assert result["shipping"]["blocked_or_unknown"] is True
    assert result["url"] == "https://www.discogs.com/sell/item/123"


@pytest.mark.parametrize(
    "amount,passes",
    [
        ("174.99", True),
        ("174.999", False),
        ("175", False),
        ("175.001", False),
        ("166.66666666666666", True),
    ],
)
def test_conservative_round_up_strict_boundary(amount, passes):
    data = payload()
    data["price"]["value"] = amount
    assert evaluate_preview(data, 123, 456)["checks"]["item_strictly_under"] is passes


@pytest.mark.parametrize(
    "amount",
    [True, None, [], {}, "NaN", "Infinity", "-1", "1e-999999999", "1e999999999", "9" * 101],
)
def test_invalid_amounts(amount):
    assert preview_price({"currency": "USD", "value": amount}) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("condition", []),
        ("condition", "NM"),
        ("sleeve_condition", None),
        ("comments", None),
        ("comments", "<script>complete set</script>"),
        ("comments", "missing disc 2"),
        ("comments", "disc 1 only"),
        ("comments", "sleeve only"),
        ("comments", "record is scratched"),
        ("status", "Sold"),
    ],
)
def test_missing_malicious_fields_and_description_exclusions(field, value):
    data = payload()
    data[field] = value
    assert not evaluate_preview(data, 123, 456)["item_criteria_match"]


def test_identity_and_currency_fail_closed():
    with pytest.raises(ListingReadError):
        evaluate_preview(payload(), 124, 456)
    assert not evaluate_preview(payload(), 123, 457)["item_criteria_match"]
    assert not evaluate_preview(payload(), 123, 456, currency="GBP")["item_criteria_match"]
    data = payload()
    data.update(uri="https://evil.example", url="https://evil.example")
    assert evaluate_preview(data, 123, 456)["url"].endswith("/123")


def test_metadata_does_not_infer_seller_confirmed_contents():
    now = datetime.now(UTC)
    result = evaluate_preview(
        payload(),
        123,
        456,
        release_metadata={"id": 456, "formats": [{"name": "Vinyl", "qty": "2"}]},
        release_provenance={
            "source": "discogs_release_api",
            "release_id": 456,
            "fetched_at": now.isoformat(),
        },
        now=now,
    )
    assert result["media_completeness"]["expected_media_count"] == 2
    assert result["media_completeness"]["status"] == "unknown"


def test_one_public_get_no_credentials_or_redirects():
    requests = []

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        data = payload()
        data["price"]["value"] = "166.66666666666666"
        data["original_price"]["value"] = "125.0"
        return httpx.Response(200, json=data)

    result = PreviewReader(transport=httpx.MockTransport(handler)).read_preview(123, 456)
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.discogs.com/marketplace/listings/123"
    assert requests[0].method == "GET"
    assert result["observed_at"]


@pytest.mark.parametrize("status", [301, 401, 403, 429])
def test_no_retry_or_fallback(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example"})

    with pytest.raises(ListingReadError):
        PreviewReader(transport=httpx.MockTransport(handler)).read_preview(123, 456)
    assert len(requests) == 1


def test_fixture_cli_does_not_claim_freshness(tmp_path, capsys):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload(), default=str))
    assert main(["123", "--release", "456", "--fixture", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["freshness"] == "unknown"
    assert result["observed_at"] is None


@pytest.mark.parametrize(
    "provenance",
    [
        {
            "source": "discogs_release_api",
            "release_id": 457,
            "fetched_at": "2026-11-08T00:00:00+00:00",
        },
        {
            "source": "discogs_release_api",
            "release_id": 456,
            "fetched_at": "2026-01-01T00:00:00+00:00",
        },
    ],
)
def test_untrusted_or_stale_metadata_cannot_establish_quantity(provenance):
    result = evaluate_preview(
        payload(),
        123,
        456,
        release_metadata={"id": 456, "formats": [{"name": "Vinyl", "qty": "2"}]},
        release_provenance=provenance,
        now=datetime(2026, 11, 8, 1, tzinfo=UTC),
    )
    assert result["media_completeness"]["expected_media_count"] is None
    assert result["media_completeness"]["status"] == "unknown"


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"rate_limited": True, "retry_after": 120}, "retry after 120 seconds"),
        ({"access_restricted": True}, "access restricted"),
    ],
)
def test_cli_sanitized_access_and_rate_failures(monkeypatch, capsys, flags, expected):
    def fail(*args, **kwargs):
        raise ListingReadError("untrusted secret text", **flags)

    monkeypatch.setattr(PreviewReader, "read_preview", fail)
    with pytest.raises(SystemExit) as exc:
        main(["123", "--release", "456", "--live"])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert expected in error
    assert "secret" not in error
