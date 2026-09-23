from scripts.measure_vinyl_identity import ALBUMS, SCENARIOS, _case, measure


def test_measurement_denominators_and_truth_are_independent_of_candidate_order():
    report = measure()
    assert report["scope"] == "synthetic_policy_only"
    assert report["cases"] == len(ALBUMS) * len(SCENARIOS)
    assert sum(report["decision_outcomes"].values()) == report["cases"]
    assert report["provisional_variant_calls"] == (
        report["provisional_variant_correct"] + report["provisional_variant_false"]
    )
    assert report["pressing_abstentions"] + report["provisional_variant_calls"] == (
        report["cases"] - report["exact_variant_calls"]
    )
    ids = []
    for album in ALBUMS:
        listing, variants, true_id = _case(album, "shared_barcode_other_color")
        ids.append(listing.marketplace_item_id)
        assert true_id == variants[1].catalog_variant_id
    assert len(set(ids)) == len(ALBUMS)
