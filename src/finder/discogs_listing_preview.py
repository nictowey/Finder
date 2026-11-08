"""Explicit one-listing review only; never a purchase observation or saved watch."""

import argparse
import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext
from pathlib import Path

from finder.discogs_listing import ListingReader, ListingReadError, listing_id, normalize
from finder.discogs_media_defects import assess_media_defects
from finder.discogs_rules import CURRENCIES, release_id


def preview_price(value, *, original=False):
    """Keep provider precision separate from the strict purchase-money parser.

    Round UP for display and threshold comparison. Never perform currency conversion.
    Bounded decimal inputs prevent untrusted enormous exponents/precision allocations.
    """
    if not isinstance(value, dict):
        return None
    currency = value.get("curr_abbr") if original else value.get("currency")
    raw = value.get("value")
    if currency not in CURRENCIES or type(raw) not in (str, int, float, Decimal):
        return None
    text = str(raw)
    if len(text) > 100:
        return None
    try:
        amount = Decimal(text)
        if not amount.is_finite() or not 0 <= amount <= Decimal("9999999.99"):
            return None
        if amount.as_tuple().exponent < -50 or amount.as_tuple().exponent > 10:
            return None
        with localcontext() as context:
            context.prec = 110
            display = amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        return {"currency": currency, "raw_value": text, "display_value": format(display, ".2f")}
    except (InvalidOperation, ValueError):
        return None


def evaluate_preview(
    payload,
    requested_id,
    target_release,
    *,
    under="175",
    currency="USD",
    release_metadata=None,
    release_provenance=None,
    now=None,
):
    """Untrusted payload -> review data. Never claims freshness or all-in qualification."""
    ident, target = listing_id(requested_id), release_id(target_release)
    limit = preview_price({"value": under, "currency": currency})
    if limit is None or Decimal(limit["raw_value"]) <= 0:
        raise ListingReadError("Use a positive supported item-price threshold.")
    listing = normalize(
        payload,
        ident,
        release_metadata=release_metadata,
        release_provenance=release_provenance,
        now=now,
    )
    price = preview_price(payload.get("price"))
    original = preview_price(payload.get("original_price"), original=True)
    completeness = listing["media_completeness"]
    if completeness["status"] == "no_disclosed_media_omission":
        completeness = dict(completeness, status="unknown")
        completeness["reasons"] = [
            *completeness["reasons"],
            "Seller has not explicitly confirmed all expected records are included.",
        ]
    defects = assess_media_defects(
        listing["seller_comments"], comments_supported=listing["seller_comments_supported"]
    )
    price_pass = bool(
        price
        and price["currency"] == currency
        and Decimal(price["display_value"]) < Decimal(limit["raw_value"])
    )
    checks = {
        "release_matches": listing["release_id"] == target,
        "media_at_least_nm": listing["media"] in {"NM", "M"},
        "sleeve_at_least_nm": listing["sleeve"] in {"NM", "M"},
        "item_strictly_under": price_pass,
        "reported_for_sale": listing["available"],
        "comments_supported": listing["seller_comments_supported"],
        "no_explicit_media_omission": completeness["status"] != "incomplete",
        "no_disclosed_media_defect": defects["status"] == "no_disclosed_media_defect",
    }
    return {
        "kind": "manual_known_listing_preview",
        "listing_id": str(ident),
        "url": listing["url"],
        "release_id": listing["release_id"],
        "target_release_id": target,
        "media": listing["media"],
        "sleeve": listing["sleeve"],
        "price": price,
        "original_price": original,
        "threshold": {"currency": currency, "strictly_under": limit["raw_value"]},
        "threshold_policy": (
            "Compare rounded-up item price strictly below threshold; "
            "excludes shipping, tax and fees."
        ),
        "checks": checks,
        "item_criteria_match": all(checks.values()),
        "media_completeness": completeness,
        "media_defects": defects,
        "shipping": {
            "reported": preview_price(payload.get("shipping_price")),
            "blocked_or_unknown": listing["shipping_blocked"],
            "destination_verified": False,
        },
        "purchase_qualified": False,
        "purchased": False,
        "warnings": [
            "Manual review only; seller claims and physical contents are not verified.",
            "Physical completeness is unverified; review the completeness evidence separately.",
            "Shipping eligibility, destination, tax, fees and delivered total are unverified.",
            "Original and reported prices are separate provider values; no conversion performed.",
            "No cart, checkout, order, payment, notification or persistent watch was created.",
        ],
        "attribution": "Data provided by Discogs",
    }


class PreviewReader(ListingReader):
    def read_preview(self, value, target_release, *, under="175", currency="USD"):
        ident, target = listing_id(value), release_id(target_release)
        # Validate caller settings before requesting the sole fixed official endpoint.
        limit = preview_price({"value": under, "currency": currency})
        if limit is None or Decimal(limit["raw_value"]) <= 0:
            raise ListingReadError("Use a positive supported item-price threshold.")
        payload = self._request(f"/marketplace/listings/{ident}")
        result = evaluate_preview(payload, ident, target, under=under, currency=currency)
        result["source"] = "official_public_listing_get"
        result["observed_at"] = datetime.now(UTC).isoformat()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing")
    parser.add_argument("--release", required=True)
    parser.add_argument("--under", default="175")
    parser.add_argument("--currency", default="USD", choices=sorted(CURRENCIES))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="Offline JSON; freshness is unknown")
    source.add_argument(
        "--live", action="store_true", help="One official public GET; no credentials"
    )
    args = parser.parse_args(argv)
    try:
        if args.fixture:
            with args.fixture.open("rb") as stream:
                data = stream.read(256 * 1024 + 1)
            if len(data) > 256 * 1024:
                raise ListingReadError("Fixture exceeded the safe size limit.")
            payload = json.loads(data, parse_float=Decimal)
            result = evaluate_preview(
                payload, args.listing, args.release, under=args.under, currency=args.currency
            )
            result.update(source="offline_fixture", observed_at=None, freshness="unknown")
        else:
            result = PreviewReader().read_preview(
                args.listing, args.release, under=args.under, currency=args.currency
            )
    except ListingReadError as error:
        if error.rate_limited:
            parser.exit(2, f"Public read rate limited; retry after {error.retry_after} seconds.\n")
        if error.access_restricted:
            parser.exit(2, "Public read access restricted; stopped without retry or fallback.\n")
        parser.exit(2, "Unable to preview listing; invalid input or public read unavailable.\n")
    except (ValueError, OSError, RecursionError):
        # Neither provider text nor filesystem paths/credentials belong in errors.
        parser.exit(2, "Unable to preview listing; invalid input or public read unavailable.\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
