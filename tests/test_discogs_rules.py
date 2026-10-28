import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from finder.discogs_rules import Observation, RulesStore, Target, evaluate, release_id

NOW = datetime(2026, 10, 24, 15, tzinfo=UTC)


def target(**changes):
    data = dict(
        release_id="https://www.discogs.com/release/123-Example",
        label="Example",
        min_media="VG+",
        min_sleeve="VG",
        min_seller_percent=99.0,
        min_feedback=100,
        max_total="50",
        currency="USD",
        destination_country="US",
        destination_postal="03106",
    )
    data.update(changes)
    return Target.model_validate(data)


def observation(**changes):
    data = dict(
        source="synthetic",
        listing_id="example-1",
        release_id=123,
        media="NM",
        sleeve="VG+",
        seller_percent=100.0,
        feedback=200,
        item="40",
        shipping="5",
        tax="3",
        fees="2",
        currency="USD",
        destination_country="US",
        destination_postal="03106",
        observed_at=NOW.isoformat(),
        available=True,
        pressing_evidence="confirmed",
        seller_comments="Includes both discs.",
        release_media_metadata={"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}]},
    )
    data.update(changes)
    return Observation.model_validate(data)


def test_threshold_inclusive_but_never_purchase():
    result = evaluate(target(), observation(), now=NOW)
    assert result["eligible_for_review"]
    assert result["total"] == "50.00"
    assert result["purchase_allowed"] is False


def test_paused_draft_can_omit_destination_but_cannot_qualify():
    draft = target(enabled=False, destination_country=None, destination_postal=None)
    assert draft.destination_postal is None
    result = evaluate(draft, observation(), now=NOW)
    assert not result["eligible_for_review"]
    assert "Target is paused." in result["reasons"]
    with pytest.raises(ValidationError):
        target(enabled=True, destination_country=None, destination_postal=None)
    with pytest.raises(ValidationError):
        target(enabled=False, destination_country="US", destination_postal=None)


def test_zero_seller_threshold_means_no_rating_or_feedback_gate():
    unrestricted = target(min_seller_percent=0, min_feedback=0)
    result = evaluate(unrestricted, observation(seller_percent=None, feedback=None), now=NOW)
    assert result["eligible_for_review"]
    assert result["purchase_allowed"] is False
    unknown_total = evaluate(
        unrestricted, observation(seller_percent=None, feedback=None, tax=None), now=NOW
    )
    assert not unknown_total["eligible_for_review"]
    assert "Item, shipping, tax and mandatory fees must all be known." in unknown_total["reasons"]


def test_generic_sleeve_is_a_separate_opt_in_not_a_grade():
    generic = observation(sleeve="Generic")
    assert not evaluate(target(min_sleeve="P"), generic, now=NOW)["eligible_for_review"]
    allowed = target(min_sleeve="VG+", allow_generic_sleeve=True)
    accepted = evaluate(allowed, generic, now=NOW)
    assert accepted["eligible_for_review"]
    assert accepted["purchase_allowed"] is False
    assert not evaluate(allowed, observation(media="Generic"), now=NOW)["eligible_for_review"]
    assert not evaluate(allowed, observation(sleeve="No Cover"), now=NOW)["eligible_for_review"]
    assert not evaluate(allowed, observation(sleeve="Generic", tax=None), now=NOW)[
        "eligible_for_review"
    ]
    with pytest.raises(ValidationError):
        target(allow_generic_sleeve="true")


@pytest.mark.parametrize(
    "changes",
    [
        {"shipping": None},
        {"tax": None},
        {"fees": None},
        {"item": None},
        {"fees": "2.01"},
        {"currency": "EUR"},
        {"destination_postal": None},
        {"destination_country": "CA"},
        {"release_id": 124},
        {"media": "VG"},
        {"sleeve": "Generic"},
        {"seller_percent": None},
        {"seller_percent": 98.9},
        {"feedback": None},
        {"feedback": 99},
        {"available": False},
        {"pressing_evidence": "unknown"},
        {"pressing_evidence": "conflicting"},
        {"observed_at": (NOW - timedelta(minutes=6)).isoformat()},
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"observed_at": "2026-10-24T15:00:00"},
        {"observed_at": "bad"},
    ],
)
def test_fail_closed(changes):
    result = evaluate(target(), observation(**changes), now=NOW)
    assert result["reasons"] and not result["eligible_for_review"]
    assert not result["purchase_allowed"]


@pytest.mark.parametrize(
    "value",
    [
        True,
        0,
        -1,
        "123.0",
        "https://evil.com/release/123",
        "https://www.discogs.com/master/123",
        "https://discogs.com@evil.com/release/123",
        "https://www.discogs.com/sell/item/123",
        "9007199254740992",
    ],
)
def test_bad_release(value):
    with pytest.raises(ValueError):
        release_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "123",
        123,
        "https://discogs.com/release/123",
        "https://www.discogs.com/en/release/123-Example?x=1",
    ],
)
def test_release(value):
    assert release_id(value) == 123


@pytest.mark.parametrize("value", [1.0, True, "-1", "NaN", "1.001", "Infinity", "1e3"])
def test_invalid_money(value):
    with pytest.raises(ValidationError):
        observation(shipping=value)


def test_never_accept_live_or_enable_purchase():
    with pytest.raises(ValidationError):
        observation(source="discogs")
    with pytest.raises(ValidationError):
        Target.model_validate({**target().model_dump(), "purchasing_enabled": True})


def test_store_revision_reopen_one_copy(tmp_path):
    path = tmp_path / "rules.sqlite3"
    store = RulesStore(path, create=True)
    data = target().model_dump()
    assert store.save(data, 0)["revision"] == 1
    with pytest.raises(RuntimeError):
        store.save(data, 0)
    assert len(store.snapshot()["targets"]) == 1
    store.save(data, 1)
    assert len(store.snapshot()["targets"]) == 1
    store.mark_owned(123, True, 2)
    store.close()
    store = RulesStore(path)
    assert store.snapshot()["owned"] == [123]
    result = evaluate(target(), observation(), now=NOW, already_owned=True)
    assert not result["eligible_for_review"]
    assert any("One-copy" in reason for reason in result["reasons"])
    store.mark_owned(123, False, 3)
    assert store.snapshot()["owned"] == []
    store.close()


def test_paused_destination_free_rule_round_trips_and_stays_paused(tmp_path):
    path = tmp_path / "rules.sqlite3"
    store = RulesStore(path, create=True)
    draft = target(
        enabled=False,
        destination_country=None,
        destination_postal=None,
        min_seller_percent=0,
        min_feedback=0,
        max_total="10",
        min_sleeve="VG+",
    )
    assert store.save(draft.model_dump(), 0)["revision"] == 1
    store.close()
    reopened = RulesStore(path)
    saved = Target.model_validate(reopened.snapshot()["targets"][0])
    reopened.close()
    assert saved == draft
    assert not evaluate(saved, observation(seller_percent=None, feedback=None), now=NOW)[
        "eligible_for_review"
    ]


def test_no_overwrite_or_legacy_migration(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    path.write_bytes(b"not a rules database")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        RulesStore(path, create=True)
    with pytest.raises(sqlite3.DatabaseError):
        RulesStore(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("seller_percent", float("nan")),
        ("seller_percent", float("inf")),
        ("seller_percent", True),
        ("feedback", True),
        ("feedback", 2.5),
        ("feedback", -1),
        ("seller_percent", "99"),
    ],
)
def test_invalid_reputation(field, value):
    with pytest.raises(ValidationError):
        observation(**{field: value})


def test_paused_target_and_small_decimal_costs():
    paused = target().model_copy(update={"enabled": False})
    assert not evaluate(paused, observation(), now=NOW)["eligible_for_review"]
    row = observation(item="0.10", shipping="0.20", tax="0.00", fees="0.00")
    cap = target().model_copy(update={"max_total": "0.30"})
    assert evaluate(cap, row, now=NOW)["total"] == "0.30"
    assert evaluate(cap, row, now=NOW)["eligible_for_review"]


def test_edited_settings_persist(tmp_path):
    path = tmp_path / "rules.sqlite3"
    store = RulesStore(path, create=True)
    store.save(target().model_dump(), 0)
    update = {**target().model_dump(), "max_total": "45.00", "enabled": False}
    store.save(update, 1)
    store.close()
    store = RulesStore(path)
    assert store.snapshot()["targets"][0]["max_total"] == "45.00"
    assert store.snapshot()["targets"][0]["enabled"] is False
    store.close()


def test_review_reports_saved_rules_revision(tmp_path):
    store = RulesStore(tmp_path / "rules.sqlite3", create=True)
    store.save(target().model_dump(), 0)
    result = store.review(123, observation(observed_at=datetime.now(UTC).isoformat()).model_dump())
    assert result["rules_revision"] == 1
    assert result["eligible_for_review"]
    store.mark_owned(123, True, 1)
    result = store.review(123, observation(observed_at=datetime.now(UTC).isoformat()).model_dump())
    assert result["rules_revision"] == 2
    assert not result["eligible_for_review"]
    store.close()
