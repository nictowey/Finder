"""Invented profiles exercise local input plumbing, never market accuracy."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from finder import local_workspace as local
from finder.local_workspace import LocalWorkspace, WorkspaceConflict, WorkspaceError

NOW = datetime(2026, 10, 3, 5, tzinfo=UTC)
OBSERVED = (NOW - timedelta(minutes=10)).isoformat()


@pytest.fixture
def bundle():
    return {
        "schema_version": 2,
        "source": "synthetic",
        "target": {
            "artist": "Invented Ensemble",
            "album": "Paper Satellites",
            "colors": ["Blue"],
            "barcodes": ["000000000001"],
            "country": "US",
            "release_year": 2024,
            "editions": ["Limited Edition"],
            "required_components": [],
        },
        "alternatives": [
            {
                "id": "invented-black",
                "artist": "Invented Ensemble",
                "album": "Paper Satellites",
                "colors": ["Black"],
                "barcodes": ["000000000002"],
                "country": "US",
                "release_year": 2023,
            }
        ],
        "settings": {
            "maximum_subtotal": "30",
            "gamble_max": "15.0",
            "country": "US",
            "postal_code": "00000",
            "tells": [{"kind": "color", "value": "Blue", "required": True}],
        },
        "listings": [
            {
                "id": "blue",
                "title": "Invented Ensemble Paper Satellites blue vinyl",
                "observed_at": OBSERVED,
                "details_observed_at": OBSERVED,
                "artist": "Invented Ensemble",
                "album": "Paper Satellites",
                "colors": ["Blue"],
                "barcodes": ["000000000001"],
                "country": "US",
                "release_year": 2024,
                "editions": ["Limited Edition"],
                "current_price": "20.00",
                "currency": "USD",
                "shipping_cost": "4.00",
                "shipping_currency": "USD",
                "price_kind": "fixed_price",
                "delivery_country": "US",
                "delivery_postal_code": "00000",
            }
        ],
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "_utc_now", lambda: NOW)
    with LocalWorkspace.create(tmp_path / "profiles.sqlite3") as workspace:
        yield workspace


def assess(value, *, now=NOW):
    validated = local.validate_bundle(value, now=now)
    return local.review_local_case(validated, validated.listings[0], now=now)


def test_authored_comparison_exercises_main_cap_with_incomplete_coverage(bundle, workspace):
    prediction = assess(bundle)
    review = prediction["review"]
    assert review["status"] == "possible_pressing"
    assert review["subtotal"] == "24.00"
    assert review["alert_budget"] == "within_ceiling"
    assert review["notify"]
    assert review["alternatives_checked"] == 1
    assert review["alternatives_not_ruled_out"] == 0
    assert "catalog_alternative_search_incomplete" in review["verify"]
    saved = workspace.import_bundle(bundle)["rows"][0]
    assert saved["review"] == review
    assert saved["comparison_evidence"] == prediction["comparison_evidence"]


@pytest.mark.parametrize("profiles", [None, []])
def test_no_authored_alternatives_never_pretends_successful_comparison(bundle, profiles):
    bundle["alternatives"] = profiles
    review = assess(bundle)["review"]
    assert review["status"] == "family_review"
    assert review["alternatives_checked"] is None
    assert not review["notify"]
    assert "catalog_alternatives_not_checked" in review["verify"]


def test_shared_evidence_remains_unclear(bundle):
    # A distinct local pressing may share every supplied target fact.
    bundle["alternatives"] = [{"id": "shared-blue", **bundle["target"]}]
    review = assess(bundle)["review"]
    assert review["status"] == "family_review"
    assert review["alternatives_not_ruled_out"] == 1
    assert review["alert_budget"] == "over_ceiling"
    assert not review["notify"]
    assert "shared_pressing_evidence" in review["verify"]


def test_country_year_editions_reach_existing_normalizer_without_new_rejection(bundle):
    bundle["listings"][0].update(country="UK", release_year=2023, editions=["Reissue"])
    prediction = assess(bundle)
    evidence = {row["field"]: row for row in prediction["comparison_evidence"]}
    assert evidence["country"]["listing_values"] == ["UK"]
    assert evidence["release_year"]["listing_values"] == ["2023"]
    assert evidence["edition"]["variant_values"] == ["Limited Edition"]
    assert all(not evidence[field]["matched"] for field in ("country", "release_year", "edition"))
    assert prediction["review"]["status"] == "possible_pressing"
    assert "edition_needs_review" in prediction["review"]["verify"]


def test_explicit_signed_insert_component_activates_only_existing_guard(bundle):
    bundle["target"]["required_components"] = ["signed_insert"]
    bundle["listings"][0]["title"] = (
        "Invented Ensemble Paper Satellites blue vinyl no signed insert"
    )
    review = assess(bundle)["review"]
    assert review["status"] == "conflicting"
    assert "catalog_signed_insert_missing" in review["verify"]
    assert not review["notify"]
    # The original v1-style description never asserted a package component.
    bundle["target"]["required_components"] = []
    bundle["target"]["formats"] = ["LP", "Signed Insert"]
    assert "catalog_signed_insert_missing" not in assess(bundle)["review"]["verify"]


@pytest.mark.parametrize(
    "claim",
    [
        "blue vinyl",
        "blue vinyl signed insert not included in photos",
        "blue vinyl maybe no signed insert",
        "blue vinyl signed delivery required",
        "blue vinyl unsigned sleeve",
    ],
)
def test_missing_or_ambiguous_package_claims_do_not_create_denial(bundle, claim):
    bundle["listings"][0]["title"] = "Invented Ensemble Paper Satellites " + claim
    baseline = assess(bundle)["review"]
    bundle["target"]["required_components"] = ["signed_insert"]
    review = assess(bundle)["review"]
    assert "catalog_signed_insert_missing" not in review["verify"]
    assert review == baseline
    # Existing barcode/color support can still qualify without any insert claim.
    # The package field activates denial checks, not a positive-inclusion gate.
    assert review["status"] == "possible_pressing"


@pytest.mark.parametrize(
    ("settings", "listing", "code"),
    [
        ({}, {"shipping_cost": None}, "shipping_or_price_unknown"),
        ({}, {"shipping_currency": "EUR"}, "shipping_or_price_unknown"),
        ({"currency": "EUR"}, {}, "currency_mismatch"),
        ({"country": None, "postal_code": None}, {}, "destination_not_set"),
        (
            {},
            {"delivery_country": None, "delivery_postal_code": None},
            "destination_quote_unconfirmed",
        ),
        ({}, {"details_observed_at": None}, "details_need_refresh"),
        ({"alert_mode": "strict"}, {}, "catalog_alternative_search_incomplete"),
        ({"condition_ids": ["1000"]}, {}, "condition_not_accepted"),
        ({}, {"listing_ends_at": OBSERVED}, "listing_ended"),
    ],
)
def test_existing_gates_still_block_v2_main_cap(bundle, settings, listing, code):
    bundle["settings"].update(settings)
    bundle["listings"][0].update(listing)
    review = assess(bundle)["review"]
    assert review["status"] == "possible_pressing"
    assert code in review["verify"]
    assert not review["notify"]


def test_explicit_clock_controls_freshness_without_reading_wall_time(bundle, monkeypatch):
    def forbidden_clock():
        raise AssertionError("Stateless evaluation must not read wall time")

    monkeypatch.setattr(local, "_utc_now", forbidden_clock)
    first = assess(bundle)
    assert assess(bundle) == first
    stale = assess(bundle, now=NOW + timedelta(hours=2))["review"]
    assert not stale["notify"]
    assert {"listing_stale", "details_need_refresh"} <= set(stale["verify"])
    with pytest.raises(WorkspaceError, match="timezone-aware"):
        assess(bundle, now=NOW.replace(tzinfo=None))


def test_v1_contract_and_stored_history_are_not_reinterpreted(bundle, workspace):
    bundle["schema_version"] = 1
    bundle.pop("alternatives")
    for field in ("country", "release_year", "editions", "required_components"):
        bundle["target"].pop(field)
        bundle["listings"][0].pop(field, None)
    snapshot = workspace.import_bundle(bundle)
    assert snapshot["schema_version"] == 1
    assert snapshot["rows"][0]["review"]["status"] == "family_review"
    record = workspace.export_bundle()
    assert "schema_version" not in record["target_history"][0]["data"]["input"]
    workspace.close()
    with LocalWorkspace.open(workspace.path) as reopened:
        assert reopened.snapshot() == snapshot
        assert reopened.export_bundle() == record
        for field, value in (
            ("country", "US"),
            ("release_year", 2024),
            ("editions", []),
            ("cover_edition", "Alpha"),
        ):
            bad = copy.deepcopy(bundle)
            bad["target"][field] = value
            with pytest.raises(WorkspaceError):
                reopened.import_bundle(bad)


def test_v2_roundtrip_canonical_import_and_judgment_history(bundle, workspace):
    first = workspace.import_bundle(bundle)
    canonical = local.validate_bundle(bundle, now=NOW).model_dump(mode="json")
    assert workspace.import_bundle(canonical) == first
    row = first["rows"][0]
    judged = workspace.set_verdict(
        row["id"],
        "mine",
        expected_revision=first["revision"],
        review_fingerprint=row["review_fingerprint"],
    )
    original = copy.deepcopy(judged["rows"][0]["judgment"])
    before = workspace.export_bundle()
    assert before["observations"][0]["source_metadata"]["manual_input"] == canonical["listings"][0]
    assert before["target_history"][0]["data"]["input"] == {
        key: value for key, value in canonical.items() if key != "listings"
    }
    updated = copy.deepcopy(canonical)
    updated["listings"] = []
    updated["alternatives"][0]["country"] = "UK"
    changed = workspace.import_bundle(updated)
    assert changed["revision"] == 2
    assert changed["rows"][0]["judgment_needs_review"]
    for key in original:
        if key.startswith("first_"):
            assert changed["rows"][0]["judgment"][key] == original[key]
    with pytest.raises(WorkspaceConflict):
        workspace.set_verdict(
            row["id"],
            "other",
            expected_revision=first["revision"],
            review_fingerprint=row["review_fingerprint"],
        )
    assert workspace.export_bundle()["target_history"][0] == before["target_history"][0]


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate_id",
        "duplicate_facts",
        "family",
        "component",
        "year",
        "coverage",
        "provider_id",
        "count",
        "version",
    ],
)
def test_invalid_profiles_fail_before_any_write(bundle, workspace, invalid):
    workspace.import_bundle(bundle)
    before = workspace.export_bundle()
    bad = copy.deepcopy(bundle)
    bad["target"]["country"] = "UK"
    profile = bad["alternatives"][0]
    if invalid == "duplicate_id":
        bad["alternatives"].append({**profile, "id": profile["id"].upper(), "colors": ["White"]})
    elif invalid == "duplicate_facts":
        bad["alternatives"].append({**profile, "id": "duplicate-facts"})
    elif invalid == "family":
        profile["album"] = "A Different Album"
    elif invalid == "component":
        bad["target"]["required_components"] = ["signed_sleeve"]
    elif invalid == "year":
        profile["release_year"] = "2024"
    elif invalid == "coverage":
        bad["search_incomplete"] = False
    elif invalid == "provider_id":
        profile["discogs_release_id"] = 1234
    elif invalid == "count":
        bad["alternatives"] = [
            {**profile, "id": f"profile-{i}", "release_year": 2000 + i} for i in range(21)
        ]
    else:
        bad["schema_version"] = 2.0
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bad)
    assert workspace.export_bundle() == before


def test_v2_limits_duplicate_keys_and_untrusted_answers_remain_rejected(bundle):
    for field in ("expected", "prediction", "verdict"):
        with pytest.raises(WorkspaceError):
            assess({**bundle, field: "mine"})
    with pytest.raises(WorkspaceError):
        assess(
            json.dumps(bundle).replace(
                '"schema_version": 2', '"schema_version": 2, "schema_version": 2'
            )
        )
    with pytest.raises(WorkspaceError, match="256 KiB"):
        assess(" " * (local.MAX_IMPORT_BYTES + 1))
    bundle["listings"] *= 101
    with pytest.raises(WorkspaceError):
        assess(bundle)


def test_canonical_comparator_order_and_empty_list_equivalence(bundle):
    bundle["alternatives"].append({**bundle["alternatives"][0], "id": "alpha", "colors": ["White"]})
    first = local.validate_bundle(bundle, now=NOW).model_dump(mode="json")
    bundle["alternatives"].reverse()
    assert local.validate_bundle(bundle, now=NOW).model_dump(mode="json") == first
    bundle["alternatives"] = []
    empty = local.validate_bundle(bundle, now=NOW).model_dump(mode="json")
    bundle["alternatives"] = None
    assert local.validate_bundle(bundle, now=NOW).model_dump(mode="json") == empty


def test_duplicate_profile_facts_ignore_case_and_value_order(bundle):
    profile = bundle["alternatives"][0]
    profile["colors"] = ["Black", "White"]
    bundle["alternatives"].append(
        {**profile, "id": "same-facts", "colors": ["white", "BLACK"], "country": "us"}
    )
    with pytest.raises(WorkspaceError, match="bundle"):
        local.validate_bundle(bundle, now=NOW)


def test_duplicate_profile_facts_ignore_repeated_normalized_values(bundle, workspace):
    workspace.import_bundle(bundle)
    before = workspace.export_bundle()
    profile = bundle["alternatives"][0]
    bundle["alternatives"].append({**profile, "id": "repeated-black", "colors": ["black", "Black"]})
    with pytest.raises(WorkspaceError, match="bundle"):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


@pytest.mark.parametrize("field", ["artist", "album"])
@pytest.mark.parametrize(("target_value", "other_value"), [("音楽", "別物"), ("...", "?!")])
def test_empty_normalized_family_cannot_authorize_comparison(
    bundle, workspace, field, target_value, other_value
):
    before = workspace.export_bundle()
    bundle["target"][field] = target_value
    bundle["alternatives"][0][field] = other_value
    with pytest.raises(WorkspaceError, match="comparison.*unsupported"):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


@pytest.mark.parametrize("version", [1, 2])
def test_empty_normalized_family_without_alternatives_keeps_existing_acceptance(version):
    bundle = {
        "schema_version": version,
        "source": "synthetic",
        "target": {"artist": "音楽", "album": "..."},
        "settings": {},
        "listings": [],
    }
    assert local.validate_bundle(bundle, now=NOW).schema_version == version


def test_v2_profiles_share_one_local_work_identity_but_v1_keeps_old_behavior(bundle):
    validated = local.validate_bundle(bundle, now=NOW)
    target = local._variant(validated.target, "local-target", NOW)
    other = local._variant(validated.alternatives[0], "local-alternative:invented-black", NOW)
    assert target.catalog_source == other.catalog_source == local.LOCAL_SOURCE
    assert target.catalog_product_id.startswith("local-work:")
    assert target.catalog_product_id == other.catalog_product_id
    assert target.catalog_product_id not in {target.catalog_variant_id, other.catalog_variant_id}
    legacy = local.TargetInput(artist="Invented Ensemble", album="Paper Satellites")
    old = local._variant(legacy, "legacy-identity", NOW)
    assert old.catalog_product_id == old.catalog_variant_id == "legacy-identity"


def cover_bundle(bundle, *, other_name="River Scene"):
    bundle["target"].update(colors=[], barcodes=[], editions=[], cover_edition="Northern Lights")
    bundle["alternatives"][0].update(colors=[], barcodes=[], cover_edition=other_name)
    bundle["settings"]["tells"] = []
    bundle["listings"][0].update(colors=[], barcodes=[], editions=[])
    return bundle


def test_explicit_named_competing_cover_reaches_existing_family_guard(bundle, workspace):
    cover_bundle(bundle)
    bundle["listings"][0]["title"] = "Invented Ensemble Paper Satellites River Scene cover vinyl LP"
    review = assess(bundle)["review"]
    assert review["status"] == "conflicting"
    assert "catalog_cover_conflict" in review["verify"]
    assert "catalog_alternative_search_incomplete" in review["verify"]
    assert not review["notify"]
    snapshot = workspace.import_bundle(bundle)
    assert snapshot["target"]["cover_edition"] == "Northern Lights"
    assert snapshot["alternatives"][0]["cover_edition"] == "River Scene"
    assert snapshot["rows"][0]["review"] == review
    history = workspace.export_bundle()["target_history"][0]["data"]["input"]
    assert history["target"]["cover_edition"] == "Northern Lights"
    assert history["alternatives"][0]["cover_edition"] == "River Scene"


def test_alpha_beta_cover_names_never_manufacture_disc_color(bundle):
    from finder.categories.vinyl import from_variant

    cover_bundle(bundle, other_name="Beta")
    bundle["target"]["cover_edition"] = "Alpha"
    bundle["listings"][0]["title"] = "Invented Ensemble Paper Satellites Beta cover vinyl LP"
    validated = local.validate_bundle(bundle, now=NOW)
    target = local._variant(validated.target, "local-target", NOW)
    assert not local._palette(from_variant(target).colors)
    assert not from_variant(target).editions
    other = local._variant(validated.alternatives[0], "local-alternative:beta", NOW)
    assert not from_variant(other).editions
    review = assess(bundle)["review"]
    assert review["status"] == "conflicting"
    assert "catalog_cover_conflict" in review["verify"]


@pytest.mark.parametrize("name", ["Promo", "Unofficial Skyline", "Test Pressing"])
@pytest.mark.parametrize("profile_key", ["target", "alternative"])
def test_cover_name_cannot_manufacture_edition_evidence(bundle, workspace, name, profile_key):
    before = workspace.export_bundle()
    cover_bundle(bundle)
    profile = bundle["target"] if profile_key == "target" else bundle["alternatives"][0]
    profile["cover_edition"] = name
    assert profile.get("editions", []) == []
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


@pytest.mark.parametrize("edition", ["Promo", "Unofficial Skyline", "Test Pressing"])
def test_explicit_edition_facts_remain_supported_alongside_safe_cover(bundle, edition):
    from finder.categories.vinyl import from_variant

    cover_bundle(bundle, other_name="Beta")
    bundle["target"].update(cover_edition="Alpha", editions=[edition])
    validated = local.validate_bundle(bundle, now=NOW)
    target = local._variant(validated.target, "local-target", NOW)
    assert from_variant(target).editions == [edition]


@pytest.mark.parametrize(
    "claim",
    [
        "vinyl LP",
        "Unknown Design vinyl LP",
        "Northern Lights vinyl LP",
        "not River Scene vinyl LP",
        "River Scene or Northern Lights vinyl LP",
        "River Scene signed insert vinyl LP",
        "River Scene cover photograph vinyl LP",
        "River Scene cover sold separately vinyl LP",
    ],
)
def test_cover_missing_own_unknown_ambiguous_and_accessory_claims_abstain(bundle, claim):
    cover_bundle(bundle)
    bundle["listings"][0]["title"] = "Invented Ensemble Paper Satellites " + claim
    review = assess(bundle)["review"]
    assert review["status"] == "family_review"
    assert "catalog_cover_conflict" not in review["verify"]


@pytest.mark.parametrize(
    "claim",
    [
        "Universal Music Group vinyl LP",
        "Group Records vinyl LP",
        "record label Group vinyl LP",
        "music by Group vinyl LP",
        "Group band vinyl LP",
    ],
)
def test_catalog_cover_name_in_company_label_or_artist_role_is_not_offer_claim(bundle, claim):
    cover_bundle(bundle, other_name="Group")
    bundle["listings"][0]["title"] = "Invented Ensemble Paper Satellites " + claim
    review = assess(bundle)["review"]
    assert review["status"] == "family_review"
    assert "catalog_cover_conflict" not in review["verify"]


def test_independent_named_cover_claim_survives_separate_credit_role(bundle):
    cover_bundle(bundle, other_name="Group")
    bundle["listings"][0]["title"] = (
        "Invented Ensemble Paper Satellites Group cover vinyl LP Universal Music Group"
    )
    assert "catalog_cover_conflict" in assess(bundle)["review"]["verify"]


def test_target_identifier_retains_existing_cover_contradiction_behavior(bundle):
    cover_bundle(bundle)
    bundle["target"]["barcodes"] = ["000000000001"]
    bundle["alternatives"][0]["barcodes"] = ["000000000002"]
    bundle["listings"][0].update(
        title="Invented Ensemble Paper Satellites River Scene cover vinyl LP",
        barcodes=["000000000001"],
    )
    review = assess(bundle)["review"]
    assert review["status"] == "possible_pressing"
    assert "catalog_cover_conflict" not in review["verify"]


@pytest.mark.parametrize("shared", [True, False])
def test_named_cover_uses_existing_shared_palette_guard(bundle, shared):
    cover_bundle(bundle)
    bundle["target"]["colors"] = ["White"]
    bundle["alternatives"][0]["colors"] = ["White"] if shared else ["Blue"]
    bundle["listings"][0].update(
        title="Invented Ensemble Paper Satellites River Scene cover vinyl LP", colors=["White"]
    )
    assert ("catalog_cover_conflict" in assess(bundle)["review"]["verify"]) is shared


@pytest.mark.parametrize(
    "name",
    [
        "Unknown",
        "Limited Edition",
        "River/Scene",
        "River, Scene",
        "River or Moon",
        "2026",
        "One Two Three Four Five Six",
        "Mint",
        "Teal",
        "Cream",
        "Mint Window",
        "Teal Mountain",
        "Cream Horizon",
    ],
)
def test_unsupported_cover_names_reject_before_writes(bundle, workspace, name):
    before = workspace.export_bundle()
    bundle["target"]["cover_edition"] = name
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


def test_explicit_version_upgrade_preserves_immutable_v1_observations(bundle, workspace):
    v1 = copy.deepcopy(bundle)
    v1["schema_version"] = 1
    v1.pop("alternatives")
    for field in ("country", "release_year", "editions", "required_components"):
        v1["target"].pop(field)
        v1["listings"][0].pop(field, None)
    workspace.import_bundle(v1)
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceConflict):
        workspace.import_bundle({**v1, "schema_version": 2})
    assert workspace.export_bundle() == before
    upgraded = workspace.import_bundle({**bundle, "listings": []})
    assert upgraded["schema_version"] == 2
    assert workspace.export_bundle()["observations"] == before["observations"]
