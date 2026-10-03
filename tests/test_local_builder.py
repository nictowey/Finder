"""Invented cases cover granular local edits and immutable observation history."""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from finder import local_workspace as local
from finder.errors import PersistenceError
from finder.local_workspace import (
    LocalWorkspace,
    WorkspaceConflict,
    WorkspaceError,
    WorkspaceObservationConflict,
)

NOW = datetime(2026, 10, 3, 5, tzinfo=UTC)
OBSERVED = (NOW - timedelta(minutes=10)).isoformat()
LATER = (NOW - timedelta(minutes=5)).isoformat()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "_utc_now", lambda: NOW)
    with LocalWorkspace.create(tmp_path / "builder.sqlite3") as workspace:
        yield workspace


@pytest.fixture
def profile():
    return {
        "schema_version": 2,
        "source": "synthetic",
        "target": {
            "artist": "Invented Ensemble",
            "album": "Paper Satellites",
            "colors": ["Blue"],
            "formats": [],
            "country": "US",
            "release_year": 2024,
            "editions": ["Limited Edition"],
            "required_components": ["signed_insert"],
        },
        "settings": {
            "maximum_subtotal": "30.00",
            "gamble_max": "15.00",
            "country": "US",
            "postal_code": "00000",
            "tells": [{"kind": "color", "value": "Blue", "required": True}],
        },
        "alternatives": [
            {
                "id": "black-edition",
                "artist": "Invented Ensemble",
                "album": "Paper Satellites",
                "colors": ["Black"],
                "formats": [],
                "release_year": 2023,
            }
        ],
    }


@pytest.fixture
def observation():
    return {
        "id": "local-blue",
        "title": "Invented Ensemble Paper Satellites blue vinyl",
        "observed_at": OBSERVED,
        "details_observed_at": OBSERVED,
        "artist": "Invented Ensemble",
        "album": "Paper Satellites",
        "colors": ["Blue"],
        "country": "US",
        "release_year": 2024,
        "editions": ["Limited Edition"],
        "current_price": "23.00",
        "currency": "USD",
        "shipping_cost": "4.00",
        "shipping_currency": "USD",
        "price_kind": "fixed_price",
        "delivery_country": "US",
        "delivery_postal_code": "00000",
    }


def create_case(workspace, profile, observation):
    snapshot = workspace.save_profile(profile, expected_revision=0)
    return workspace.save_candidate(
        observation, expected_revision=snapshot["revision"], expected_current=None
    )


def as_profile(snapshot):
    keys = ("schema_version", "source", "target", "settings", "alternatives")
    return {key: copy.deepcopy(snapshot[key]) for key in keys if key in snapshot}


def test_profile_only_price_change_updates_review_without_observations(
    workspace, profile, observation
):
    before = create_case(workspace, profile, observation)
    export = workspace.export_bundle()
    assert before["rows"][0]["review"]["budget"] == "within_ceiling"
    edited = as_profile(before)
    edited["settings"]["maximum_subtotal"] = "25"
    after = workspace.save_profile(edited, expected_revision=before["revision"])
    assert after["revision"] == 2
    assert after["settings"]["maximum_subtotal"] == "25.00"
    assert after["target"] == before["target"]
    assert after["alternatives"] == before["alternatives"]
    assert after["rows"][0]["review"]["budget"] == "over_ceiling"
    assert workspace.export_bundle()["observations"] == export["observations"]


def test_profile_only_comparison_add_edit_remove_and_invalid_rollback(workspace, profile):
    initial = workspace.save_profile(profile, expected_revision=0)
    edited = as_profile(initial)
    edited["alternatives"].append(
        {**edited["alternatives"][0], "id": "red-edition", "colors": ["Red"]}
    )
    added = workspace.save_profile(edited, expected_revision=1)
    assert len(added["alternatives"]) == 2
    edited["alternatives"][1]["country"] = "UK"
    changed = workspace.save_profile(edited, expected_revision=2)
    assert changed["alternatives"][1]["country"] == "UK"
    export = workspace.export_bundle()
    invalid = copy.deepcopy(edited)
    invalid["settings"]["maximum_subtotal"] = "25"
    invalid["alternatives"][1]["artist"] = "Different Invented Ensemble"
    with pytest.raises(WorkspaceError):
        workspace.save_profile(invalid, expected_revision=3)
    assert workspace.export_bundle() == export
    edited["alternatives"] = []
    removed = workspace.save_profile(edited, expected_revision=3)
    assert removed["alternatives"] is None
    assert workspace.export_bundle()["observations"] == []


def test_stale_profile_and_candidate_cannot_restore_old_cap(workspace, profile, observation):
    initial = create_case(workspace, profile, observation)
    edited = as_profile(initial)
    edited["settings"]["maximum_subtotal"] = "25.00"
    workspace.save_profile(edited, expected_revision=1)
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceConflict):
        workspace.save_profile(profile, expected_revision=1)
    with pytest.raises(WorkspaceConflict):
        workspace.save_candidate(
            {**observation, "id": "new-local"}, expected_revision=1, expected_current=None
        )
    with pytest.raises(WorkspaceConflict):
        workspace.import_bundle({**profile, "listings": []}, expected_revision=1)
    assert workspace.export_bundle() == before
    assert workspace.snapshot()["settings"]["maximum_subtotal"] == "25.00"


def test_candidate_uses_server_profile_source_and_does_not_change_history(
    workspace, profile, observation
):
    initial = workspace.save_profile(profile, expected_revision=0)
    before = workspace.export_bundle()["target_history"]
    after = workspace.save_candidate(observation, expected_revision=1, expected_current=None)
    assert as_profile(after) == as_profile(initial)
    assert workspace.export_bundle()["target_history"] == before
    assert after["rows"][0]["listing"]["source_metadata"]["local_source"] == "synthetic"
    invalid = {**observation, "id": "second", "settings": {"maximum_subtotal": "999"}}
    with pytest.raises(WorkspaceError):
        workspace.save_candidate(invalid, expected_revision=1, expected_current=None)
    assert workspace.snapshot() == after


def test_same_time_edit_is_distinct_collision_and_preserves_everything(
    workspace, profile, observation
):
    initial = create_case(workspace, profile, observation)
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceObservationConflict, match="actual later observation time"):
        workspace.save_candidate(
            {**observation, "current_price": "20.00"},
            expected_revision=1,
            expected_current=initial["rows"][0]["current_token"],
        )
    assert workspace.export_bundle() == before


def test_later_observation_preserves_first_judgment_and_immutable_observation(
    workspace, profile, observation
):
    initial = create_case(workspace, profile, observation)
    first_row = initial["rows"][0]
    judged = workspace.set_verdict(
        first_row["id"],
        "mine",
        expected_revision=1,
        review_fingerprint=first_row["review_fingerprint"],
    )
    before = workspace.export_bundle()
    assert judged["rows"][0]["current_token"] == first_row["current_token"]
    later = {**observation, "observed_at": LATER, "current_price": "26.00"}
    after = workspace.save_candidate(
        later, expected_revision=1, expected_current=first_row["current_token"]
    )
    row = after["rows"][0]
    assert row["current_token"] != first_row["current_token"]
    assert row["judgment_needs_review"]
    assert row["judgment"]["first_prediction"] == first_row["review"]
    assert row["judgment"]["first_observed_at"] == first_row["listing"]["last_observed_at"]
    assert row["listing"]["first_observed_at"] == first_row["listing"]["first_observed_at"]
    assert datetime.fromisoformat(row["listing"]["last_observed_at"]) == datetime.fromisoformat(
        LATER
    )
    assert row["listing"]["source_metadata"]["manual_input"]["country"] == "US"
    exported = workspace.export_bundle()
    assert exported["observations"][0] == before["observations"][0]
    assert exported["judgments"] == before["judgments"]
    assert len(exported["observations"]) == 2


def test_repeated_exact_candidate_and_profile_requests_do_not_duplicate(
    workspace, profile, observation
):
    initial = create_case(workspace, profile, observation)
    before = workspace.export_bundle()
    assert (
        workspace.save_candidate(observation, expected_revision=1, expected_current=None) == initial
    )
    assert workspace.save_profile(profile, expected_revision=1) == initial
    assert workspace.export_bundle() == before
    later = {**observation, "observed_at": LATER}
    current = workspace.save_candidate(
        later, expected_revision=1, expected_current=initial["rows"][0]["current_token"]
    )
    assert (
        workspace.save_candidate(
            later, expected_revision=1, expected_current=initial["rows"][0]["current_token"]
        )
        == current
    )
    assert (
        workspace.save_candidate(observation, expected_revision=1, expected_current=None) == current
    )
    assert len(workspace.export_bundle()["observations"]) == 2


def test_candidate_revision_and_token_guard_independent_connections(
    workspace, profile, observation
):
    initial = create_case(workspace, profile, observation)
    barrier = threading.Barrier(2)
    with LocalWorkspace.open(workspace.path) as other:

        def save(handle, price, minute):
            barrier.wait(timeout=5)
            try:
                handle.save_candidate(
                    {
                        **observation,
                        "current_price": price,
                        "observed_at": (NOW - timedelta(minutes=minute)).isoformat(),
                    },
                    expected_revision=1,
                    expected_current=initial["rows"][0]["current_token"],
                )
                return "saved"
            except WorkspaceConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(save, workspace, "20.00", 4),
                executor.submit(save, other, "21.00", 5),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == ["conflict", "saved"]
    assert len(workspace.export_bundle()["observations"]) == 2


def test_profile_revision_guard_independent_connections(workspace, profile):
    workspace.save_profile(profile, expected_revision=0)
    barrier = threading.Barrier(2)
    with LocalWorkspace.open(workspace.path) as other:

        def save(handle, cap):
            update = copy.deepcopy(profile)
            update["settings"]["maximum_subtotal"] = cap
            barrier.wait(timeout=5)
            try:
                handle.save_profile(update, expected_revision=1)
                return "saved"
            except WorkspaceConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(save, workspace, "25.00"),
                executor.submit(save, other, "26.00"),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == ["conflict", "saved"]
    assert len(workspace.export_bundle()["target_history"]) == 2


def test_later_explicit_unknowns_do_not_borrow_old_details(workspace, profile, observation):
    initial = create_case(workspace, profile, observation)
    unknown = {"id": observation["id"], "title": "Unclear invented case", "observed_at": LATER}
    current = workspace.save_candidate(
        unknown, expected_revision=1, expected_current=initial["rows"][0]["current_token"]
    )["rows"][0]["listing"]
    assert current["details_observed_at"] is None
    assert current["item_specifics"] == {}
    assert current["price_kind"] == "unknown"
    assert current["current_price"] is None
    assert current["currency"] is None
    assert current["shipping_cost"] is None
    assert current["shipping_currency"] is None
    assert current["source_metadata"]["manual_input"]["formats"] == []
    assert current["source_metadata"]["manual_input"]["country"] is None
    observations = workspace.export_bundle()["observations"]
    assert len(observations) == 2
    assert observations[0]["details_observed_at"] is not None
    assert observations[0]["item_specifics"]
    assert observations[1]["details_observed_at"] is None
    assert observations[1]["item_specifics"] == {}
    assert (
        observations[1]["source_metadata"]["manual_input"]
        == current["source_metadata"]["manual_input"]
    )
    assert current["first_observed_at"] == initial["rows"][0]["listing"]["first_observed_at"]


def test_profile_builder_never_infers_lp_or_required_color(workspace):
    profile = {
        "schema_version": 2,
        "source": "manual",
        "target": {"artist": "Invented Ensemble", "album": "Paper Satellites", "colors": ["Blue"]},
        "settings": {},
        "alternatives": [
            {"id": "unknown", "artist": "Invented Ensemble", "album": "Paper Satellites"}
        ],
    }
    result = workspace.save_profile(profile, expected_revision=0)
    assert result["target"]["formats"] == []
    assert result["settings"]["tells"] == []
    assert result["alternatives"][0]["formats"] == []


def test_v1_stays_v1_until_explicit_profile_upgrade_and_v2_cannot_downgrade(
    workspace, profile, observation
):
    v1 = {
        "schema_version": 1,
        "source": "synthetic",
        "target": {"artist": "Invented Ensemble", "album": "Paper Satellites", "formats": []},
        "settings": {},
    }
    workspace.save_profile(v1, expected_revision=0)
    minimal = {key: observation[key] for key in ("id", "title", "observed_at")}
    first = workspace.save_candidate(minimal, expected_revision=1, expected_current=None)
    assert first["schema_version"] == 1
    assert "country" not in first["target"]
    assert "schema_version" not in workspace.export_bundle()["target_history"][0]["data"]["input"]
    with pytest.raises(WorkspaceError):
        workspace.save_candidate(
            {**minimal, "id": "rich-too-early", "country": "US"},
            expected_revision=1,
            expected_current=None,
        )
    upgraded = workspace.save_profile(profile, expected_revision=1)
    assert upgraded["schema_version"] == 2
    assert len(workspace.export_bundle()["observations"]) == 1
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceError, match="preserve rich fields"):
        workspace.save_profile(v1, expected_revision=2)
    assert workspace.export_bundle() == before


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": None},
        {"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
        {"observed_at": "2026-10-03T04:00:00"},
        {"current_price": "-1"},
        {"current_price": "10", "currency": None},
        {"shipping_cost": "1", "shipping_currency": None},
        {"price_kind": "guessed"},
        {"id": "https://example.invalid"},
    ],
)
def test_invalid_candidate_rolls_back_all_changes(workspace, profile, observation, change):
    workspace.save_profile(profile, expected_revision=0)
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceError):
        workspace.save_candidate(
            {**observation, **change}, expected_revision=1, expected_current=None
        )
    assert workspace.export_bundle() == before


def test_new_candidate_requires_absence_and_existing_requires_current_token(
    workspace, profile, observation
):
    initial = create_case(workspace, profile, observation)
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceConflict):
        workspace.save_candidate(
            {**observation, "id": "second"},
            expected_revision=1,
            expected_current=initial["rows"][0]["current_token"],
        )
    with pytest.raises(WorkspaceConflict):
        workspace.save_candidate(
            {**observation, "observed_at": LATER}, expected_revision=1, expected_current=None
        )
    assert workspace.export_bundle() == before


def test_older_observation_requires_advanced_import(workspace, profile, observation):
    initial = create_case(workspace, profile, observation)
    older = {
        **observation,
        "observed_at": (NOW - timedelta(days=1)).isoformat(),
        "details_observed_at": None,
    }
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceError, match="actual time later"):
        workspace.save_candidate(
            older, expected_revision=1, expected_current=initial["rows"][0]["current_token"]
        )
    assert workspace.export_bundle() == before
    assert workspace.import_bundle({**profile, "listings": [older]}) == initial
    assert len(workspace.export_bundle()["observations"]) == 2


def test_profile_cannot_smuggle_observations_and_candidate_requires_profile(
    workspace, profile, observation
):
    with pytest.raises(WorkspaceError, match="without observations"):
        workspace.save_profile({**profile, "listings": [observation]}, expected_revision=0)
    with pytest.raises(WorkspaceError, match="Save a profile"):
        workspace.save_candidate(observation, expected_revision=0, expected_current=None)
    assert workspace.snapshot()["revision"] == 0
    assert workspace.export_bundle()["observations"] == []


@pytest.mark.parametrize("revision", [None, True, -1, 1.0, "1"])
def test_revision_must_be_explicit_nonnegative_integer(workspace, profile, observation, revision):
    before = workspace.export_bundle()
    with pytest.raises(WorkspaceError):
        workspace.save_profile(profile, expected_revision=revision)
    with pytest.raises(WorkspaceError):
        workspace.save_candidate(observation, expected_revision=revision, expected_current=None)
    assert workspace.export_bundle() == before


def test_storage_error_rolls_back_observation_and_current_snapshot(
    workspace, profile, observation, monkeypatch
):
    initial = create_case(workspace, profile, observation)
    before = workspace.export_bundle()
    original = workspace.repository.upsert

    def fail_after_append(*args, **kwargs):
        original(*args, **kwargs)
        raise PersistenceError("Synthetic rollback failure")

    monkeypatch.setattr(workspace.repository, "upsert", fail_after_append)
    with pytest.raises(WorkspaceError, match="no changes were saved"):
        workspace.save_candidate(
            {**observation, "observed_at": LATER},
            expected_revision=1,
            expected_current=initial["rows"][0]["current_token"],
        )
    assert workspace.export_bundle() == before
    assert workspace.snapshot() == initial
