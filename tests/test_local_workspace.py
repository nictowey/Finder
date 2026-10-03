import copy
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from finder import local_workspace as local
from finder.errors import PersistenceError
from finder.local_workspace import (
    APPLICATION_ID,
    LOCAL_SOURCE,
    ImportBundle,
    LocalWorkspace,
    WorkspaceConflict,
    WorkspaceError,
    WorkspaceNotFound,
)
from finder.persistence import listing_observations

NOW = datetime(2026, 10, 3, 5, tzinfo=UTC)
OBSERVED = (NOW - timedelta(minutes=10)).isoformat()


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(local, "_utc_now", lambda: NOW)


@pytest.fixture
def bundle():
    return {
        "schema_version": 1,
        "source": "synthetic",
        "target": {
            "artist": "Example Ensemble",
            "album": "Offline Horizons",
            "colors": ["Blue"],
            "catalog_numbers": ["SYN-BLUE"],
        },
        "settings": {
            "maximum_subtotal": "30.00",
            "gamble_max": "15.00",
            "country": "US",
            "postal_code": "00000",
            "tells": [{"kind": "color", "value": "Blue", "required": True}],
        },
        "listings": [
            {
                "id": "blue",
                "title": "Example Ensemble Offline Horizons blue vinyl",
                "observed_at": OBSERVED,
                "details_observed_at": OBSERVED,
                "current_price": "10.00",
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
def workspace(tmp_path):
    with LocalWorkspace.create(tmp_path / "review.sqlite3") as workspace:
        yield workspace


def save(workspace, snapshot, verdict="mine"):
    row = snapshot["rows"][0]
    return workspace.set_verdict(
        row["id"],
        verdict,
        expected_revision=snapshot["revision"],
        review_fingerprint=row["review_fingerprint"],
    )


def test_empty_workspace_create_tag_permissions_and_reopen(tmp_path):
    path = tmp_path / "review.sqlite3"
    with LocalWorkspace.create(path) as workspace:
        initial = workspace.snapshot()
        assert initial["revision"] == 0
        assert initial["target"] is None
        assert initial["settings"] is None
        assert initial["rows"] == []
        assert "not provider verified" in initial["notice"]
        with workspace.engine.connect() as conn:
            assert set(workspace.engine.dialect.get_table_names(conn)) == {
                "finder_local_meta",
                "finder_local_targets",
                "finder_local_judgments",
                "listings",
                "listing_observations",
            }
    assert path.stat().st_mode & 0o777 == 0o600
    assert int.from_bytes(path.read_bytes()[68:72], "big") == APPLICATION_ID
    assert all(
        not path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
    )
    with LocalWorkspace.open(path) as reopened:
        assert reopened.snapshot() == initial


def test_import_review_verdict_reopen_and_export(workspace, bundle):
    snapshot = workspace.import_bundle(json.dumps(bundle))
    assert snapshot["revision"] == 1
    row = snapshot["rows"][0]
    assert row["listing"]["marketplace"] == LOCAL_SOURCE
    assert row["listing"]["source_metadata"]["observation_provenance"] == "user_supplied_unverified"
    assert row["review"]["status"] == "family_review"
    assert row["review"]["alternatives_checked"] is None
    assert "catalog_alternatives_not_checked" in row["review"]["verify"]
    assert row["review"]["subtotal"] == "14.00"
    assert row["review"]["budget"] == "within_ceiling"
    judged = save(workspace, snapshot)
    assert judged["rows"][0]["verdict"] == "mine"
    assert not judged["rows"][0]["judgment_needs_review"]
    assert not judged["rows"][0]["judgment"]["provenance_changed"]
    exported = workspace.export_bundle()
    assert exported["export_version"] == 1
    assert len(exported["observations"]) == 1
    assert len(exported["target_history"]) == 1
    assert exported["judgments"][0]["data"]["first_prediction"] == row["review"]
    workspace.close()
    with LocalWorkspace.open(workspace.path) as reopened:
        assert reopened.snapshot() == judged
        assert reopened.export_bundle() == exported
        with pytest.raises(WorkspaceError):
            reopened.import_bundle(exported)


def test_first_judgment_survives_edits_target_changes_and_observations(workspace, bundle):
    snapshot = workspace.import_bundle(bundle)
    first = save(workspace, snapshot)["rows"][0]["judgment"]
    edit = save(workspace, workspace.snapshot(), "other")
    assert edit["rows"][0]["judgment"]["first_verdict"] == "mine"
    assert edit["rows"][0]["judgment"]["first_prediction"] == first["first_prediction"]
    assert edit["rows"][0]["judgment"]["first_judged_at"] == first["first_judged_at"]
    changed_target = copy.deepcopy(bundle)
    changed_target["target"]["colors"] = ["Black"]
    changed_target["listings"] = []
    changed = workspace.import_bundle(changed_target)
    assert changed["revision"] == 2
    assert changed["rows"][0]["verdict"] == "other"
    assert changed["rows"][0]["judgment_needs_review"]
    assert changed["rows"][0]["judgment"]["provenance_changed"]
    reviewed = save(workspace, changed, "unsure")
    assert not reviewed["rows"][0]["judgment_needs_review"]
    assert reviewed["rows"][0]["judgment"]["provenance_changed"]
    later = copy.deepcopy(changed_target)
    later["listings"] = copy.deepcopy(bundle["listings"])
    later["listings"][0]["observed_at"] = (NOW - timedelta(minutes=5)).isoformat()
    later["listings"][0]["current_price"] = "12.00"
    newer = workspace.import_bundle(later)
    assert newer["revision"] == 2
    assert newer["rows"][0]["judgment_needs_review"]
    assert newer["rows"][0]["judgment"]["first_revision"] == 1
    assert newer["rows"][0]["judgment"]["first_prediction"] == first["first_prediction"]
    assert len(workspace.export_bundle()["observations"]) == 2


def test_read_does_not_persist_prediction(workspace, bundle):
    workspace.import_bundle(bundle)
    before = workspace.export_bundle()
    workspace.snapshot()
    workspace.snapshot()
    assert workspace.export_bundle() == before
    assert not before["judgments"]


def test_stale_tab_and_repeated_save_conflict(workspace, bundle):
    snapshot = workspace.import_bundle(bundle)
    save(workspace, snapshot, "mine")
    with pytest.raises(WorkspaceConflict):
        save(workspace, snapshot, "other")
    with pytest.raises(WorkspaceConflict):
        save(workspace, snapshot, "mine")
    assert workspace.snapshot()["rows"][0]["verdict"] == "mine"


def test_independent_connections_cannot_overwrite_concurrent_verdict(workspace, bundle):
    snapshot = workspace.import_bundle(bundle)
    barrier = threading.Barrier(2)
    with LocalWorkspace.open(workspace.path) as second:

        def attempt(handle, verdict):
            barrier.wait(timeout=5)
            try:
                save(handle, snapshot, verdict)
                return verdict
            except WorkspaceConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(attempt, workspace, "mine"),
                executor.submit(attempt, second, "other"),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
    assert outcomes.count("conflict") == 1
    assert workspace.snapshot()["rows"][0]["verdict"] == next(
        value for value in outcomes if value != "conflict"
    )


def test_stale_review_fingerprint_rejected_at_real_assessment_time(workspace, bundle, monkeypatch):
    snapshot = workspace.import_bundle(bundle)
    monkeypatch.setattr(local, "_utc_now", lambda: NOW + timedelta(hours=2))
    with pytest.raises(WorkspaceConflict):
        save(workspace, snapshot)
    current = workspace.snapshot()
    assert "listing_stale" in current["rows"][0]["review"]["verify"]
    assert "details_need_refresh" in current["rows"][0]["review"]["verify"]
    assert not current["rows"][0]["review"]["notify"]
    assert not workspace.export_bundle()["judgments"]
    assert datetime.fromisoformat(
        current["rows"][0]["listing"]["last_observed_at"]
    ) == datetime.fromisoformat(OBSERVED)


def test_changed_target_or_observation_rejects_old_card(workspace, bundle):
    original = workspace.import_bundle(bundle)
    bundle["settings"]["maximum_subtotal"] = "20.00"
    workspace.import_bundle(bundle)
    with pytest.raises(WorkspaceConflict):
        save(workspace, original)
    original = workspace.snapshot()
    bundle["listings"][0]["observed_at"] = (NOW - timedelta(minutes=5)).isoformat()
    workspace.import_bundle(bundle)
    with pytest.raises(WorkspaceConflict):
        save(workspace, original)


def test_unknown_shipping_remains_unknown_and_details_not_fabricated(workspace, bundle):
    row = bundle["listings"][0]
    row.pop("shipping_cost")
    row.pop("details_observed_at")
    imported = workspace.import_bundle(bundle)["rows"][0]
    assert imported["review"]["subtotal"] is None
    assert imported["review"]["budget"] == "unknown"
    assert not imported["review"]["notify"]
    assert imported["listing"]["details_observed_at"] is None
    assert imported["listing"]["shipping_cost"] is None
    assert "shipping_or_price_unknown" in imported["review"]["verify"]
    assert "details_need_refresh" in imported["review"]["verify"]


def test_identical_import_idempotent_and_older_observation_cannot_replace_latest(workspace, bundle):
    first = workspace.import_bundle(bundle)
    assert workspace.import_bundle(bundle) == first
    assert len(workspace.export_bundle()["observations"]) == 1
    older = copy.deepcopy(bundle)
    older["listings"][0]["observed_at"] = (NOW - timedelta(days=1)).isoformat()
    older["listings"][0]["details_observed_at"] = older["listings"][0]["observed_at"]
    older["listings"][0]["current_price"] = "1.00"
    assert workspace.import_bundle(older) == first
    assert len(workspace.export_bundle()["observations"]) == 2


def test_money_formatting_does_not_change_revision_or_observation(workspace, bundle):
    first = workspace.import_bundle(bundle)
    bundle["settings"]["maximum_subtotal"] = "30"
    bundle["listings"][0]["current_price"] = "10.0"
    assert workspace.import_bundle(bundle) == first


def test_same_timestamp_conflict_rejects_whole_bundle(workspace, bundle):
    workspace.import_bundle(bundle)
    before = workspace.export_bundle()
    bundle["target"]["album"] = "Changed Album"
    bundle["listings"][0]["current_price"] = "12.00"
    bundle["listings"].insert(0, {"id": "new", "title": "New local case", "observed_at": OBSERVED})
    with pytest.raises(WorkspaceConflict):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


def test_conflicting_duplicates_rejected_before_first_import(workspace, bundle):
    second = copy.deepcopy(bundle["listings"][0])
    second["current_price"] = "20.00"
    bundle["listings"].append(second)
    with pytest.raises(WorkspaceConflict):
        workspace.import_bundle(bundle)
    assert workspace.snapshot()["revision"] == 0
    assert workspace.export_bundle()["observations"] == []


def test_import_transaction_rolls_back_target_and_listing_history(workspace, bundle, monkeypatch):
    before = workspace.export_bundle()
    original = workspace.repository.upsert

    def write_then_fail(listing, *, connection):
        original(listing, connection=connection)
        raise PersistenceError("synthetic write failure")

    monkeypatch.setattr(workspace.repository, "upsert", write_then_fail)
    with pytest.raises(WorkspaceError, match="no partial import"):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


def test_invalid_whole_bundle_validated_before_any_write(workspace, bundle):
    before = workspace.export_bundle()
    bundle["listings"].append({"id": "invalid", "title": "Incomplete"})
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)
    assert workspace.export_bundle() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_metadata", {"secret": "test-value"}),
        ("listing_url", "https://example.invalid"),
        ("primary_image", "https://example.invalid/a.jpg"),
        ("seller_id", "external-seller"),
        ("api_key", "test-value"),
        ("title", "https://example.invalid"),
        ("title", "password=test-value"),
        ("current_price", "-1"),
        ("shipping_cost", "-1"),
        ("current_price", "NaN"),
        ("current_price", "1000001"),
        ("current_price", "1.001"),
        ("current_price", 25.0),
        ("shipping_cost", 1),
        ("current_price", True),
        ("observed_at", "2026-10-03T05:00:00"),
        ("observed_at", (NOW + timedelta(seconds=1)).isoformat()),
        ("details_observed_at", NOW.isoformat()),
    ],
)
def test_reject_unbounded_or_external_claim_fields(workspace, bundle, field, value):
    bundle["listings"][0][field] = value
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)
    assert workspace.snapshot()["rows"] == []


@pytest.mark.parametrize(
    "settings",
    [
        {"maximum_subtotal": "-1"},
        {"maximum_subtotal": "0"},
        {"maximum_subtotal": 25.0},
        {"gamble_max": "31.00"},
        {"postal_code": None},
        {"condition_ids": ["not-numeric"]},
        {"auction_alert_minutes": True},
        {"tells": [{"kind": "color", "value": "Blue", "required": "yes"}]},
    ],
)
def test_validate_settings(workspace, bundle, settings):
    bundle["settings"].update(settings)
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)


def test_limits_across_imports_and_bounded_schema(workspace, bundle):
    bundle["listings"] = [
        {"id": f"case-{i}", "title": f"Synthetic case {i}", "observed_at": OBSERVED}
        for i in range(100)
    ]
    assert len(workspace.import_bundle(bundle)["rows"]) == 100
    bundle["listings"] = [{"id": "one-too-many", "title": "Synthetic", "observed_at": OBSERVED}]
    with pytest.raises(WorkspaceError, match="100"):
        workspace.import_bundle(bundle)
    schema = ImportBundle.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["ListingInput"]["additionalProperties"] is False


def test_json_limits_duplicates_and_no_client_prediction(workspace, bundle):
    for value in [
        " " * (local.MAX_IMPORT_BYTES + 1),
        '{"schema_version":1,"schema_version":1}',
        "[[]]",
        '{"x":NaN}',
    ]:
        with pytest.raises(WorkspaceError):
            workspace.import_bundle(value)
    for value in (True, "1", 1.0):
        invalid_version = {**bundle, "schema_version": value}
        with pytest.raises(WorkspaceError):
            workspace.import_bundle(invalid_version)
    bundle["target"]["discogs_release_id"] = 1234
    with pytest.raises(WorkspaceError):
        workspace.import_bundle(bundle)
    with pytest.raises(TypeError):
        workspace.set_verdict(
            "blue", "mine", expected_revision=1, review_fingerprint="x", prediction={}
        )
    with pytest.raises(WorkspaceNotFound):
        workspace.set_verdict("missing", "mine", expected_revision=1, review_fingerprint="x")
    with pytest.raises(WorkspaceError):
        workspace.set_verdict("blue", "purchased", expected_revision=1, review_fingerprint="x")


def test_no_open_or_overwrite_of_unrecognized_database(tmp_path, monkeypatch):
    path = tmp_path / "ordinary.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE ordinary (value TEXT)")
    before = path.read_bytes()
    monkeypatch.setattr(
        local,
        "create_engine",
        lambda *args, **kwargs: pytest.fail("Must reject before SQLite connection"),
    )
    with pytest.raises(WorkspaceError, match="recognized"):
        LocalWorkspace.open(path)
    with pytest.raises(WorkspaceError, match="new workspace"):
        LocalWorkspace.create(path)
    assert path.read_bytes() == before
    assert not (tmp_path / "absent.sqlite3").exists()
    with pytest.raises(WorkspaceError):
        LocalWorkspace.open(tmp_path / "absent.sqlite3")
    assert not (tmp_path / "absent.sqlite3").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecars_rejected_for_create_and_reopen(tmp_path, suffix):
    path = tmp_path / "new.sqlite3"
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_bytes(b"unrelated")
    with pytest.raises(WorkspaceError, match="sidecar"):
        LocalWorkspace.create(path)
    assert not path.exists()
    assert sidecar.read_bytes() == b"unrelated"
    sidecar.unlink()
    LocalWorkspace.create(path).close()
    before = path.read_bytes()
    sidecar.symlink_to(tmp_path / "nonexistent")
    with pytest.raises(WorkspaceError, match="sidecar"):
        LocalWorkspace.open(path)
    assert path.read_bytes() == before
    assert sidecar.is_symlink()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_recognized_workspace_and_regular_sidecars_preserved_on_reopen(tmp_path, suffix):
    path = tmp_path / "review.sqlite3"
    LocalWorkspace.create(path).close()
    original_database = path.read_bytes()
    sidecar = path.with_name(path.name + suffix)
    original_sidecar = b"Synthetic sidecar preservation probe"
    sidecar.write_bytes(original_sidecar)
    with pytest.raises(WorkspaceError, match="automatic crash recovery is not supported"):
        LocalWorkspace.open(path)
    assert path.read_bytes() == original_database
    assert sidecar.read_bytes() == original_sidecar


def test_dotenv_urls_symlinks_and_unknown_tagged_schema_rejected(tmp_path):
    for value in [
        "sqlite:///other.sqlite3",
        "postgresql://example/db",
        tmp_path / ".env",
        tmp_path / ".env.local",
    ]:
        with pytest.raises(WorkspaceError):
            LocalWorkspace.create(value)
    original = tmp_path / "original.sqlite3"
    LocalWorkspace.create(original).close()
    alias = tmp_path / "symlink.sqlite3"
    alias.symlink_to(original)
    with pytest.raises(WorkspaceError, match="symlink"):
        LocalWorkspace.open(alias)
    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(original, hardlink)
    with pytest.raises(WorkspaceError, match="unlinked"):
        LocalWorkspace.open(hardlink)
    hardlink.unlink()
    with sqlite3.connect(original) as conn:
        conn.execute("CREATE TABLE finder_watches (id TEXT)")
    with pytest.raises(WorkspaceError, match="schema"):
        LocalWorkspace.open(original)


@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE VIEW unknown_view AS SELECT 1",
        "ALTER TABLE finder_local_targets ADD COLUMN unknown_column TEXT",
        "CREATE TRIGGER unknown_trigger AFTER INSERT ON finder_local_targets BEGIN SELECT 1; END",
    ],
)
def test_reject_modified_tagged_schema(tmp_path, mutation):
    path = tmp_path / "review.sqlite3"
    LocalWorkspace.create(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute(mutation)
    with pytest.raises(WorkspaceError, match="schema"):
        LocalWorkspace.open(path)


def test_canonical_observations_remain_immutable(workspace, bundle):
    workspace.import_bundle(bundle)
    with workspace.engine.connect() as conn:
        original = conn.execute(select(listing_observations.c.data)).scalar_one()
    bundle["listings"][0]["observed_at"] = (NOW - timedelta(minutes=1)).isoformat()
    bundle["listings"][0]["current_price"] = "9.00"
    workspace.import_bundle(bundle)
    with workspace.engine.connect() as conn:
        stored = conn.execute(
            select(listing_observations.c.data).where(
                listing_observations.c.observed_at == OBSERVED
            )
        ).scalar_one()
    assert stored == original
