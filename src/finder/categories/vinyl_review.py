"""Value-free, bounded comparison of a saved pressing and same-family alternatives.

This projection is safe for public Actions output: it contains only controlled field codes,
booleans, heuristic scores, and anonymous candidate positions. Seller and catalog values stay
in memory and must not be interpolated into this report.
"""

from finder.categories.vinyl import from_listing, from_variant, has_numbered_claim
from finder.domain import Listing, ListingVariantCandidate, Variant

EVIDENCE_FIELDS = frozenset(
    {
        "artist",
        "barcode",
        "catalog_number",
        "color",
        "country",
        "edition",
        "format",
        "release_year",
        "title",
    }
)
SELLER_CATALOG_DISAGREEMENTS = frozenset(
    {"artist", "barcode", "catalog_number", "color", "country", "edition", "release_year"}
)
SOFT_TEXT_FIELDS = frozenset({"title", "format"})


def compare_pressings(
    listing: Listing,
    variants: list[Variant],
    ranked: list[ListingVariantCandidate],
    target_id: int,
) -> dict:
    """Describe up to the retrieved same-family releases without any item or release IDs."""
    by_id = {item.catalog_variant_id: item for item in variants}
    target = by_id.get(str(target_id))
    if target is None:
        return {
            "target_available": False,
            "same_family_competitors": 0,
            "other_family_releases": len(variants),
            "candidates": [],
        }

    seller = from_listing(listing)
    seller_numbered = has_numbered_claim(seller.editions)
    seller_title_numbered = has_numbered_claim([listing.title])
    candidates = {item.catalog_variant_id: item for item in ranked}
    same_family = [
        item
        for item in ranked
        if item.catalog_variant_id != str(target_id)
        and by_id[item.catalog_variant_id].catalog_product_id == target.catalog_product_id
    ]

    def row(variant: Variant, role: str) -> dict:
        item = candidates[variant.catalog_variant_id]
        fingerprint = from_variant(variant)
        catalog_numbered = has_numbered_claim(fingerprint.editions)
        missing = [
            field
            for field, catalog_values, seller_values in (
                ("barcode", fingerprint.barcodes, seller.barcodes),
                ("catalog_number", fingerprint.catalog_numbers, seller.catalog_numbers),
                ("matrix_runout", fingerprint.matrix_runouts, seller.matrix_runouts),
                ("color", fingerprint.colors, seller.colors),
                ("edition", fingerprint.editions, seller.editions),
            )
            if catalog_values and not seller_values
        ]
        if catalog_numbered and not seller_numbered:
            missing.append("structured_numbered_claim")
        return {
            "role": role,
            "heuristic_score": item.score,
            "score_band": item.status,
            "catalog_numbered": catalog_numbered,
            "matched_fields": sorted(
                {field.field for field in item.evidence if field.matched} & EVIDENCE_FIELDS
            ),
            "seller_catalog_disagreements": sorted(
                {field.field for field in item.evidence if not field.matched}
                & SELLER_CATALOG_DISAGREEMENTS
            ),
            "unmatched_soft_text": sorted(
                {field.field for field in item.evidence if not field.matched} & SOFT_TEXT_FIELDS
            ),
            "missing_seller_evidence": sorted(missing),
            "unscored_present_fields": ["matrix_runout"]
            if fingerprint.matrix_runouts and seller.matrix_runouts
            else [],
        }

    return {
        "target_available": True,
        "seller_structured_numbered_claim": seller_numbered,
        "seller_title_numbered_claim": seller_title_numbered,
        "same_family_competitors": len(same_family),
        "other_family_releases": len(variants) - len(same_family) - 1,
        "candidates": [
            row(target, "target"),
            *(
                row(by_id[item.catalog_variant_id], f"same_family_{index}")
                for index, item in enumerate(same_family, 1)
            ),
        ],
    }
