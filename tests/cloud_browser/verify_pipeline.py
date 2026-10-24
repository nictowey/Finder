"""Verify real snapshot persistence/rules using only a disposable synthetic workspace.

The browser harness passes four unchanged collector outputs as a JSON array on stdin.
This bridge never opens a browser, reads deployment configuration, authenticates against
a service, starts a monitor, or constructs a provider reader. It does not establish live
Discogs access or verify a physical copy, destination quote, or purchase capability.
"""

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from finder.cloud_app import CloudApp
from finder.discogs_completeness import assess_media_completeness
from finder.discogs_listing import GRADE_NAMES
from finder.discogs_release_snapshots import MAX_BYTES, ReleaseSnapshots, RenderedReleaseSnapshot
from finder.discogs_rules import Observation, RulesStore, Target, evaluate

RELEASE_ID = 111
ORIGIN = "https://synthetic-finder.invalid"
COOKIE = "synthetic-pipeline-only"
MAX_INPUT_BYTES = 4 * MAX_BYTES


class PipelineVerificationError(ValueError):
    """Only fixed, non-sensitive failure codes may cross the CLI boundary."""


def _require(condition, code):
    if not condition:
        raise PipelineVerificationError(code)


class _SyntheticVerifier:
    def verify(self, cookie):
        return cookie == COOKIE


def _post(app, path, value):
    """Exercise the real WSGI control endpoints without HTTP or an auth service."""
    raw = json.dumps(value).encode()
    statuses = []
    env = {
        "HTTP_HOST": app.host,
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "HTTP_COOKIE": COOKIE,
        "HTTP_ORIGIN": ORIGIN,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "HTTP_X_FINDER_CSRF": app.token(COOKIE),
        "wsgi.input": io.BytesIO(raw),
    }
    response = b"".join(app(env, lambda status, headers: statuses.append(status)))
    _require(statuses == ["200 OK"], "synthetic_control_setup_failed")
    return json.loads(response)


def _target():
    return Target(
        release_id=RELEASE_ID,
        label="Synthetic browser pipeline fixture",
        min_media="VG+",
        min_sleeve="VG+",
        min_seller_percent=99.0,
        min_feedback=100,
        max_total="50.00",
        currency="USD",
        destination_country="US",
        destination_postal="00000",
    )


def _metadata(quantity):
    return {"id": RELEASE_ID, "formats": [{"name": "Vinyl", "qty": str(quantity)}]}


def _evaluate_row(row, now):
    fields = row["fields"]
    # Only comments and grades originate in the rendered row. Every other value below
    # is fabricated test evidence, never a conversion of displayed shipping into a quote.
    observation = Observation(
        source="synthetic",
        listing_id=row["listing_id"],
        release_id=RELEASE_ID,
        media=GRADE_NAMES.get(fields["media"]),
        sleeve=GRADE_NAMES.get(fields["sleeve"]),
        seller_percent=100.0,
        feedback=100,
        item="20.00",
        shipping="5.00",
        tax="0.00",
        fees="0.00",
        currency="USD",
        destination_country="US",
        destination_postal="00000",
        observed_at=now.isoformat(),
        available=True,
        pressing_evidence="confirmed",
        seller_comments=fields["seller_description"],
        release_media_metadata=_metadata(2),
    )
    assessment = assess_media_completeness(RELEASE_ID, fields["seller_description"], _metadata(2))
    result = evaluate(_target(), observation, now=now)
    _require(result["media_completeness"] == assessment, "assessment_rules_disagree")
    _require(assessment["expected_media_count"] == 2, "exact_quantity_not_used")
    _require(assessment["verified"] is False, "contents_cannot_be_verified")
    _require(result["purchase_allowed"] is False, "purchase_must_stay_disabled")
    return result


def _verify_quantities():
    for quantity, comments, status in (
        (1, "Record only.", "reported_complete"),
        (3, "Includes two records.", "incomplete"),
        (3, "Complete set of three records.", "reported_complete"),
    ):
        result = assess_media_completeness(RELEASE_ID, comments, _metadata(quantity))
        _require(result["expected_media_count"] == quantity, "quantity_was_assumed")
        _require(result["status"] == status, "quantity_check_failed")
        _require(result["verified"] is False, "contents_cannot_be_verified")


def _ingest(engine, snapshot, *, rules=1, control=1, baseline=0):
    result = engine.ingest(
        snapshot,
        expected_rules_revision=rules,
        expected_control_revision=control,
        expected_baseline_revision=baseline,
    )
    _require(result["purchase_allowed"] is False, "purchase_must_stay_disabled")
    _require(result["notifications_sent"] == 0, "notification_must_stay_disabled")
    return result


def _reject_without_writes(engine, snapshot, **revisions):
    before = engine.snapshot()
    try:
        _ingest(engine, snapshot, **revisions)
    except RuntimeError:
        pass
    else:
        raise PipelineVerificationError("guard_did_not_reject")
    _require(engine.snapshot() == before, "rejected_import_changed_state")


def _seen(database):
    store = RulesStore(database)
    try:
        return store.db.execute(
            "SELECT listing_id,first_seen,last_seen,present FROM discogs_release_seen "
            "WHERE release_id=? ORDER BY listing_id",
            (RELEASE_ID,),
        ).fetchall()
    finally:
        store.close()


def _validate_sequence(snapshots):
    _require(isinstance(snapshots, list) and len(snapshots) == 4, "four_snapshots_required")
    parsed = [RenderedReleaseSnapshot.model_validate(value) for value in snapshots]
    _require(all(value.release_id == RELEASE_ID for value in parsed), "synthetic_release_required")
    for index, ident in ((0, "222"), (1, "333"), (3, "333")):
        value = parsed[index]
        _require(value.complete and value.total == 1, "single_complete_row_required")
        _require([row.listing_id for row in value.rows] == [ident], "synthetic_row_required")
    _require(not parsed[2].complete and not parsed[2].rows, "failed_snapshot_required")
    _require(parsed[2].error_code is not None, "collector_failure_code_required")
    _require(parsed[1].rows == parsed[3].rows, "final_rows_must_be_unchanged")
    _require(
        parsed[1].rows[0].fields.seller_description == "NO DISC 1, disc 2 only.",
        "missing_record_description_required",
    )
    _require(
        all(parsed[index].rows[0].fields.media == "Very Good Plus (VG+)" for index in (0, 1, 3)),
        "vg_plus_fixture_required",
    )
    times = [datetime.fromisoformat(value.observed_at.replace("Z", "+00:00")) for value in parsed]
    _require(all(a < b for a, b in zip(times, times[1:], strict=False)), "times_must_increase")
    _require((times[-1] - times[0]).total_seconds() < 300, "bounded_sequence_required")
    return times


def verify_pipeline(snapshots):
    """Return only safe summary metrics; caller-owned snapshots remain unchanged."""
    times = _validate_sequence(snapshots)
    ordinary = _evaluate_row(snapshots[0]["rows"][0], times[0])
    incomplete = _evaluate_row(snapshots[1]["rows"][0], times[1])
    _require(ordinary["eligible_for_review"] is True, "ordinary_prose_must_pass_omission_check")
    _require(
        ordinary["media_completeness"]["status"] == "no_disclosed_media_omission",
        "ordinary_prose_is_not_completeness_proof",
    )
    _require(incomplete["eligible_for_review"] is False, "missing_media_must_block_review")
    _require(incomplete["media_completeness"]["status"] == "incomplete", "missing_media_not_found")
    _require(
        incomplete["reasons"]
        == ["Playable vinyl set is incomplete; missing records are not acceptable."],
        "incomplete_must_fail_independently_of_grade",
    )
    _verify_quantities()

    with TemporaryDirectory(prefix="finder-synthetic-browser-") as directory:
        database = Path(directory) / "rules.sqlite3"
        RulesStore(database, create=True).close()
        app = CloudApp(database, ORIGIN, _SyntheticVerifier())
        _require(
            app.reader is None
            and app.monitor_loop is None
            and app.monitor.listing_reader is None
            and app.monitor.stats_reader is None,
            "network_readers_must_not_exist",
        )
        saved = _post(
            app, "/api/target", {"target": _target().model_dump(), "expected_revision": 0}
        )
        _require(saved["revision"] == 1 and not saved["purchasing_enabled"], "target_setup_failed")
        clock = [times[0].timestamp()]
        engine = ReleaseSnapshots(database, clock=lambda: clock[0])
        _reject_without_writes(engine, snapshots[0], control=0)
        control = _post(app, "/api/kill", {"killed": False, "expected_revision": 0})
        _require(
            control["control_revision"] == 1 and not control["kill_switch"], "kill_setup_failed"
        )
        for revisions in ({"rules": 0}, {"control": 0}, {"baseline": 1}):
            _reject_without_writes(engine, snapshots[0], **revisions)

        first = _ingest(engine, snapshots[0])
        _require(first["status"] == "baseline" and first["change_ids"] == [], "baseline_not_silent")
        _require(first["baseline_revision"] == 1, "baseline_revision_not_advanced")
        _require(engine.snapshot()["changes"] == [], "baseline_created_events")

        clock[0] = times[1].timestamp()
        replacement = _ingest(engine, snapshots[1], baseline=1)
        changed = engine.snapshot()
        _require(replacement["status"] == "changed", "same_count_substitution_not_detected")
        _require(replacement["baseline_revision"] == 2, "replacement_revision_not_advanced")
        _require(len(replacement["change_ids"]) == 2, "substitution_event_count_incorrect")
        _require(
            {(row["kind"], row["listing_id"]) for row in changed["changes"]}
            == {("new", "333"), ("disappeared", "222")},
            "substitution_events_incorrect",
        )
        seen = _seen(database)
        _require(
            [(row[0], row[3]) for row in seen] == [("222", 0), ("333", 1)], "seen_ids_incorrect"
        )

        clock[0] = times[2].timestamp()
        failed = _ingest(engine, snapshots[2], baseline=2)
        preserved = engine.snapshot()
        before_release, after_release = changed["releases"][0], preserved["releases"][0]
        _require(
            failed["status"] == "preserved" and failed["change_ids"] == [], "failure_not_silent"
        )
        for key in ("baseline", "baseline_revision", "baseline_observed_at"):
            _require(after_release[key] == before_release[key], "failure_replaced_baseline")
        _require(after_release["last_status"] == snapshots[2]["status"], "failure_not_recorded")
        _require(after_release["fresh"] is False, "failed_read_claimed_fresh")
        _require(
            after_release["last_attempt_observed_at"] == times[2].astimezone(UTC).isoformat(),
            "failed_attempt_watermark_missing",
        )
        _require(preserved["changes"] == changed["changes"], "failure_changed_events")
        _require(_seen(database) == seen, "failure_changed_seen_ledger")

        # A fresh object has no in-memory baseline or dedupe cache to rely on.
        reopened = ReleaseSnapshots(database, clock=lambda: clock[0])
        _require(reopened.snapshot() == preserved, "restart_lost_persisted_state")
        clock[0] = times[3].timestamp()
        final = _ingest(reopened, snapshots[3], baseline=2)
        state = reopened.snapshot()
        _require(
            final["status"] == "unchanged" and final["change_ids"] == [],
            "restart_duplicated_events",
        )
        _require(final["baseline_revision"] == 3, "final_revision_not_advanced")
        _require(state["changes"] == changed["changes"], "restart_changed_event_ledger")
        _require(
            state["pending_change_ids"] == changed["pending_change_ids"], "restart_lost_dedupe"
        )
        _require(state["releases"][0]["fresh"] is True, "complete_read_not_fresh")
        _require(state["purchase_allowed"] is False, "purchase_must_stay_disabled")
        _require(state["notifications_enabled"] is False, "notification_must_stay_disabled")
        _reject_without_writes(reopened, snapshots[3], baseline=3)

    return {
        "ok": True,
        "mode": "synthetic_cloud_browser_pipeline",
        "snapshots": 4,
        "baseline_events": 0,
        "substitution_events": 2,
        "failed_baseline_preserved": True,
        "restart_dedupe": True,
        "unchanged_events": 0,
        "revision_guard_rejections": 4,
        "duplicate_rejected": True,
        "ordinary_omission_check": "no_disclosed_media_omission",
        "incomplete_rejected_despite_vg_plus": True,
        "exact_quantities_checked": [1, 2, 3],
        "purchase_allowed": False,
        "notifications_sent": 0,
    }


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        _require(len(raw) <= MAX_INPUT_BYTES, "input_too_large")
        result = verify_pipeline(json.loads(raw))
    except PipelineVerificationError as error:
        result = {"ok": False, "error": str(error), "purchase_allowed": False}
    except Exception:
        # Validation errors can contain input text; never print raw data or tracebacks.
        result = {"ok": False, "error": "pipeline_verification_failed", "purchase_allowed": False}
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
