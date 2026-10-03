"""Synthetic behavior checks; never evidence of live pressing accuracy."""

import copy
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finder import local_evaluation as evaluation
from finder import local_workspace as local

CLOCK = "2026-10-03T05:00:00Z"


@pytest.fixture
def bundle():
    return {
        "schema_version": 2,
        "source": "synthetic",
        "target": {"artist": "Example Ensemble", "album": "Offline Horizons", "colors": ["Blue"]},
        "alternatives": [
            {
                "id": "black-profile",
                "artist": "Example Ensemble",
                "album": "Offline Horizons",
                "colors": ["Black"],
            }
        ],
        "settings": {
            "maximum_subtotal": "30.00",
            "gamble_max": "15.00",
            "country": "US",
            "postal_code": "00000",
            "tells": [{"kind": "color", "value": "Blue", "required": True}],
        },
        "listings": [
            {
                "id": "blue-case",
                "title": "Example Ensemble Offline Horizons blue vinyl",
                "observed_at": "2026-10-03T04:50:00Z",
                "details_observed_at": "2026-10-03T04:50:00Z",
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


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def freeze(tmp_path, bundle, name="prediction.json"):
    source = write(tmp_path / "input.json", bundle)
    output = tmp_path / name
    prediction = evaluation.freeze_predictions(source, output, assessed_at=CLOCK)
    return output, prediction


def answer(case_id="blue-case", identity="target", **expectations):
    return {
        "id": case_id,
        "declared_identity": identity,
        "provenance": {
            "kind": "synthetic_case_design",
            "detail": "Invented blue and black disc scenario.",
        },
        "rationale": "Case design declares the blue disc to be the target.",
        **expectations,
    }


def manifest(prediction, answers=None, coverage="complete"):
    return {
        "schema_version": 1,
        "kind": "finder-local-answers",
        "answer_version": "draft-1",
        "input_digest": prediction["input_digest"],
        "coverage": coverage,
        "answers": answers if answers is not None else [answer()],
    }


def score(tmp_path, prediction_path, data, name="report.json"):
    answers = write(tmp_path / "answers.json", data)
    return evaluation.score_predictions(prediction_path, answers, tmp_path / name)


def test_freeze_uses_same_stateless_matcher_and_explicit_clock(tmp_path, bundle, monkeypatch):
    monkeypatch.setattr(
        local, "_utc_now", lambda: pytest.fail("Never use wall clock in evaluation")
    )
    path, result = freeze(tmp_path, bundle)
    validated = local.validate_bundle(bundle, now=datetime(2026, 10, 3, 5, tzinfo=UTC))
    reference = local.review_local_case(
        validated, validated.listings[0], now=datetime(2026, 10, 3, 5, tzinfo=UTC)
    )
    assert result["canonical_input"] == validated.model_dump(mode="json")
    assert result["cases"][0]["review"] == reference["review"]
    assert result["cases"][0]["comparison_evidence"] == reference["comparison_evidence"]
    assert result["clock_semantics"] == "fixed_simulation_clock"
    assert result["assessed_at"] == "2026-10-03T05:00:00+00:00"
    assert path.stat().st_mode & 0o777 == 0o400
    assert result["cases"][0]["comparison_scope"] == {
        "kind": "supplied_profiles_only",
        "supplied_profile_ids": ["black-profile"],
        "supplied_profile_count": 1,
        "complete_catalog": False,
    }
    assert result["implementation"]["finder_source_digest"] == evaluation._digest(
        result["implementation"]["finder_source_files"]
    )
    assert "watch_worker.py" in result["implementation"]["finder_source_files"]
    assert "local_evaluation.py" in result["implementation"]["finder_source_files"]


def test_normalized_digest_reproducible_and_labels_never_read(tmp_path, bundle, monkeypatch):
    source = write(tmp_path / "input.json", bundle)
    secret_answers = write(tmp_path / "not-read.json", {"impossible": "labels"})
    first = evaluation.freeze_predictions(source, tmp_path / "first.json", assessed_at=CLOCK)
    bundle["listings"][0]["current_price"] = "10"
    bundle["listings"][0]["observed_at"] = "2026-10-03T05:50:00+01:00"
    write(source, dict(reversed(list(bundle.items()))))
    second = evaluation.freeze_predictions(
        source, tmp_path / "second.json", assessed_at="2026-10-03T06:00:00+01:00"
    )
    assert first == second
    original = local.review_local_case

    def reads_undeclared(*args, **kwargs):
        secret_answers.read_text()
        return original(*args, **kwargs)

    monkeypatch.setattr(local, "review_local_case", reads_undeclared)
    with pytest.raises(evaluation.EvaluationError, match="declared input"):
        evaluation.freeze_predictions(source, tmp_path / "third.json", assessed_at=CLOCK)
    assert not (tmp_path / "third.json").exists()


def test_score_never_recomputes_predictions_and_answer_edits_change_only_report(
    tmp_path, bundle, monkeypatch
):
    path, prediction = freeze(tmp_path, bundle)
    frozen = path.read_bytes()
    monkeypatch.setattr(
        local, "review_local_case", lambda *a, **k: pytest.fail("Score must not rerun policy")
    )
    monkeypatch.setattr(
        local, "validate_bundle", lambda *a, **k: pytest.fail("Score must not reinterpret inputs")
    )
    good = manifest(
        prediction,
        [
            answer(
                expected_status="possible_pressing",
                expected_eligible=True,
                required_evidence_codes=["seller_color_claim"],
                forbidden_evidence_codes=["color_conflict"],
            )
        ],
    )
    first = score(tmp_path, path, good)
    good["answer_version"] = "draft-2"
    good["answers"][0]["expected_status"] = "conflicting"
    second = score(tmp_path, path, good, "report-edited.json")
    assert path.read_bytes() == frozen
    assert first["expectation_outcome"] == "passed"
    assert second["expectation_outcome"] == "failed"
    assert second["expectation_mismatch_count"] == 1
    assert first["answer_digest"] != second["answer_digest"]
    assert first["prediction_digest"] == second["prediction_digest"]
    assert second["cases"][0]["mismatches"] == [
        {
            "field": "status",
            "expected": "conflicting",
            "actual": "possible_pressing",
            "passed": False,
        }
    ]


def test_full_matrix_abstentions_identity_errors_and_qualification_are_separate(tmp_path, bundle):
    base = bundle["listings"][0]
    inputs = [
        ("supported-target", "blue", "target", "99.00"),
        ("supported-other", "blue", "other", "10.00"),
        ("abstain-target", "", "target", "10.00"),
        ("exclude-target", "black", "target", "10.00"),
        ("exclude-other", "black", "other", "10.00"),
        ("unrelated", "Other Artist Other Album", "other", "10.00"),
        ("unresolved", "blue", "unresolved", "10.00"),
        ("unlabeled", "blue", "unlabeled", "10.00"),
    ]
    bundle["listings"] = [
        {
            **base,
            "id": case_id,
            "title": (
                color
                if case_id == "unrelated"
                else f"Example Ensemble Offline Horizons {color} vinyl"
            ),
            "current_price": price,
        }
        for case_id, color, _, price in inputs
    ]
    path, prediction = freeze(tmp_path, bundle)
    report = score(
        tmp_path,
        path,
        manifest(prediction, [answer(case_id, identity) for case_id, _, identity, _ in inputs]),
    )
    matrix = report["tier_by_declared_identity"]
    assert set(matrix) == set(evaluation.TIERS)
    assert all(set(row) == set(evaluation.IDENTITIES) for row in matrix.values())
    assert sum(sum(row.values()) for row in matrix.values()) == 8
    assert matrix["possible_pressing"] == {"target": 1, "other": 1, "unresolved": 1, "unlabeled": 1}
    assert matrix["family_review"]["target"] == 1
    assert matrix["conflicting"]["target"] == 1
    assert matrix["unrelated"]["other"] == 1
    metrics = report["metrics"]
    assert metrics["supported_positive_agreement"] == {
        "numerator": 1,
        "denominator": 2,
        "ratio": 0.5,
    }
    assert metrics["declared_target_recognition"] == {
        "numerator": 1,
        "denominator": 3,
        "ratio": 1 / 3,
    }
    assert metrics["supported_positive_undecided_cases"] == 2
    assert metrics["false_review_leads"] == {"numerator": 1, "denominator": 3, "ratio": 1 / 3}
    assert metrics["excluded_declared_positives"] == {
        "numerator": 1,
        "denominator": 3,
        "ratio": 1 / 3,
    }
    assert metrics["abstained_declared_positives"] == 1
    assert metrics["abstentions"] == {"numerator": 1, "denominator": 8, "ratio": 1 / 8}
    assert report["simulated_qualification"]["nonqualifying_declared_target_cases"] == 2
    assert report["expectation_outcome"] == "not_evaluated"
    assert report["expectations_passed"] is None


@pytest.mark.parametrize("identity", ["unresolved", "unlabeled"])
def test_undecided_labels_never_become_perfect_identity_agreement(tmp_path, bundle, identity):
    path, prediction = freeze(tmp_path, bundle)
    report = score(tmp_path, path, manifest(prediction, [answer(identity=identity)]))
    for metric in (
        "supported_positive_agreement",
        "declared_target_recognition",
        "false_review_leads",
        "excluded_declared_positives",
        "expectation_agreement",
    ):
        assert report["metrics"][metric]["denominator"] == 0
        assert report["metrics"][metric]["ratio"] is None
    assert report["coverage"]["decided_identity_cases"] == 0
    assert report["identity_outcome"] == "not_evaluated"
    assert report["expectation_outcome"] == "not_evaluated"


def test_partial_answers_keep_missing_ids_in_unlabeled_denominator(tmp_path, bundle):
    bundle["listings"].append({**bundle["listings"][0], "id": "missing"})
    path, prediction = freeze(tmp_path, bundle)
    report = score(tmp_path, path, manifest(prediction, coverage="partial"))
    assert report["coverage"]["missing_answer_ids"] == ["missing"]
    assert report["coverage"]["total_cases"] == 2
    assert report["coverage"]["identities"]["unlabeled"] == 1
    assert report["cases"][1]["answer"] is None
    assert report["cases"][1]["missing_answer"]
    assert report["metrics"]["supported_positive_undecided_cases"] == 1
    with pytest.raises(evaluation.EvaluationError, match="every frozen case"):
        score(tmp_path, path, manifest(prediction), "bad-report.json")


def test_ui_assisted_labels_are_explicit_and_not_imported(tmp_path, bundle):
    path, prediction = freeze(tmp_path, bundle)
    data = manifest(prediction)
    data["answers"][0]["provenance"]["kind"] = "ui_assisted_judgment"
    result = score(tmp_path, path, data)
    assert result["coverage"]["ui_assisted_answer_count"] == 1
    assert result["cases"][0]["answer"]["provenance"]["kind"] == "ui_assisted_judgment"
    data["judgments"] = [{"verdict": "mine"}]
    with pytest.raises(evaluation.EvaluationError, match="Invalid answer"):
        score(tmp_path, path, data, "no-import.json")


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "unknown",
        "digest",
        "version",
        "boolean_version",
        "missing_provenance",
        "missing_rationale",
        "url",
        "credential",
        "oversize",
        "ambiguous_evidence",
        "coerced_bool",
    ],
)
def test_bad_answers_fail_closed(tmp_path, bundle, mutation):
    path, prediction = freeze(tmp_path, bundle)
    data = manifest(prediction)
    if mutation == "duplicate":
        data["answers"].append(copy.deepcopy(data["answers"][0]))
    elif mutation == "unknown":
        data["answers"][0]["id"] = "unknown"
    elif mutation == "digest":
        data["input_digest"] = "0" * 64
    elif mutation == "version":
        data["schema_version"] = 2
    elif mutation == "boolean_version":
        data["schema_version"] = True
    elif mutation in {"missing_provenance", "missing_rationale"}:
        del data["answers"][0][mutation.removeprefix("missing_")]
    elif mutation == "url":
        data["answers"][0]["rationale"] = "https://example.invalid/answer"
    elif mutation == "credential":
        data["answers"][0]["rationale"] = "password=not-a-real-secret"
    elif mutation == "oversize":
        data["answers"][0]["rationale"] = "X" * 501
    elif mutation == "ambiguous_evidence":
        data["answers"][0]["required_evidence_codes"] = ["artist_and_album"]
        data["answers"][0]["forbidden_evidence_codes"] = ["artist_and_album"]
    else:
        data["answers"][0]["expected_eligible"] = "true"
    with pytest.raises(evaluation.EvaluationError):
        score(tmp_path, path, data)
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize(
    "mutation", ["duplicate_id", "empty", "labels", "future_observation", "verdict_export", "url"]
)
def test_bad_prediction_inputs_fail_before_output(tmp_path, bundle, mutation):
    if mutation == "duplicate_id":
        bundle["listings"].append(copy.deepcopy(bundle["listings"][0]))
    elif mutation == "empty":
        bundle["listings"] = []
    elif mutation == "labels":
        bundle["listings"][0]["expected_status"] = "possible_pressing"
    elif mutation == "future_observation":
        bundle["listings"][0]["observed_at"] = "2026-10-03T05:01:00Z"
    elif mutation == "verdict_export":
        bundle = {"export_version": 1, "kind": "finder-local-manual", "judgments": []}
    else:
        bundle["listings"][0]["title"] = "https://example.invalid/listing"
    with pytest.raises(ValueError):
        freeze(tmp_path, bundle)
    assert not (tmp_path / "prediction.json").exists()


@pytest.mark.parametrize("mutation", ["review", "input", "source"])
def test_tampering_frozen_artifact_fails(tmp_path, bundle, mutation):
    _, prediction = freeze(tmp_path, bundle)
    original = copy.deepcopy(prediction)
    if mutation == "review":
        prediction["cases"][0]["review"]["status"] = "conflicting"
    elif mutation == "input":
        prediction["canonical_input"]["settings"]["maximum_subtotal"] = "999.00"
    else:
        prediction["implementation"]["finder_source_digest"] = "0" * 64
    tampered = write(tmp_path / "tampered.json", prediction)
    with pytest.raises(evaluation.EvaluationError, match="digest mismatch"):
        score(tmp_path, tampered, manifest(original))


@pytest.mark.parametrize(
    "operation",
    ["socket", "dns", "subprocess", "database", "dotenv", "undeclared_file", "other_write"],
)
def test_accidental_io_is_blocked_and_guard_restores(tmp_path, bundle, monkeypatch, operation):
    source = write(tmp_path / "input.json", bundle)
    dotenv = tmp_path / ".env"
    dotenv.write_text("FINDER_DATABASE_URL=never-read\n")
    other = tmp_path / "owner.sqlite3"

    def accidental(*args, **kwargs):
        if operation == "socket":
            socket.socket()
        elif operation == "dns":
            socket.getaddrinfo("never-resolve.invalid", 443)
        elif operation == "subprocess":
            subprocess.run([sys.executable, "-c", "pass"], check=True)
        elif operation == "database":
            sqlite3.connect(other)
        elif operation == "dotenv":
            dotenv.read_text()
        elif operation == "undeclared_file":
            (tmp_path / "expected.json").read_text()
        else:
            (tmp_path / "unrequested.txt").write_text("no")
        pytest.fail("Forbidden I/O should not run")

    monkeypatch.setattr(local, "review_local_case", accidental)
    with pytest.raises(evaluation.EvaluationError):
        evaluation.freeze_predictions(source, tmp_path / "prediction.json", assessed_at=CLOCK)
    assert not other.exists()
    assert not (tmp_path / "prediction.json").exists()
    assert dotenv.read_text() == "FINDER_DATABASE_URL=never-read\n"
    assert evaluation._active_boundary is None


@pytest.mark.parametrize(
    "name",
    [
        "FINDER_DATABASE_URL",
        "DATABASE_URL",
        "DISCOGS_TOKEN",
        "EBAY_CLIENT_ID",
        "OPENAI_API_KEY",
        "ACCESS_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
    ],
)
def test_live_or_credential_environment_rejected_without_leaking_value(
    tmp_path, bundle, monkeypatch, name, capsys
):
    source = write(tmp_path / "input.json", bundle)
    monkeypatch.setenv(name, "never-print-this-synthetic-value")
    assert (
        evaluation.main(
            [
                "predict",
                "--input",
                str(source),
                "--assessed-at",
                CLOCK,
                "--output",
                str(tmp_path / "result.json"),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert name in error
    assert "never-print-this-synthetic-value" not in error
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "path", [".env", ".env.test", "https://example.invalid/out.json", "postgresql://local/db"]
)
def test_url_dotenv_paths_rejected(tmp_path, bundle, path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = write(tmp_path / "input.json", bundle)
    with pytest.raises(evaluation.EvaluationError):
        evaluation.freeze_predictions(source, path, assessed_at=CLOCK)


def test_no_overwrite_input_output_or_symlinks(tmp_path, bundle):
    source = write(tmp_path / "input.json", bundle)
    original = source.read_bytes()
    with pytest.raises(evaluation.EvaluationError, match="never overwrites"):
        evaluation.freeze_predictions(source, source, assessed_at=CLOCK)
    assert source.read_bytes() == original
    output, _ = freeze(tmp_path, bundle)
    original_output = output.read_bytes()
    with pytest.raises(evaluation.EvaluationError, match="never overwrites"):
        evaluation.freeze_predictions(source, output, assessed_at=CLOCK)
    assert output.read_bytes() == original_output
    link = tmp_path / "linked.json"
    link.symlink_to(source)
    with pytest.raises(evaluation.EvaluationError, match="symlink"):
        evaluation.freeze_predictions(link, tmp_path / "other.json", assessed_at=CLOCK)


def test_duplicate_json_keys_nonfinite_and_oversize_rejected(tmp_path):
    for name, content in [
        ("duplicate", '{"a":1,"a":2}'),
        ("nonfinite", '{"a":NaN}'),
        ("oversize", " " * (256 * 1024 + 1)),
    ]:
        path = tmp_path / (name + ".json")
        path.write_text(content)
        assert (
            evaluation.main(
                [
                    "predict",
                    "--input",
                    str(path),
                    "--assessed-at",
                    CLOCK,
                    "--output",
                    str(tmp_path / (name + "-out.json")),
                ]
            )
            == 2
        )


@pytest.mark.parametrize("clock", ["2026-10-03T05:00:00", "today", ""])
def test_explicit_clock_required(tmp_path, bundle, clock):
    source = write(tmp_path / "input.json", bundle)
    with pytest.raises(evaluation.EvaluationError, match="timestamp"):
        evaluation.freeze_predictions(source, tmp_path / "result.json", assessed_at=clock)


def test_v1_unknown_alternatives_and_price_basis_remain_unknown(tmp_path, bundle):
    bundle["schema_version"] = 1
    del bundle["alternatives"]
    del bundle["listings"][0]["shipping_cost"]
    del bundle["listings"][0]["shipping_currency"]
    _, result = freeze(tmp_path, bundle)
    row = result["cases"][0]
    assert row["review"]["status"] == "family_review"
    assert row["price_basis"]["shipping_cost"] is None
    assert row["price_basis"]["delivered_subtotal"] is None
    assert row["comparison_scope"]["kind"] == "alternatives_unavailable"
    assert "catalog_alternatives_not_checked" in row["unknowns_and_verification"]
    assert "shipping_or_price_unknown" in row["unknowns_and_verification"]


def test_cli_wrong_expectation_exits_one_and_saved_report_preserves_evidence(
    tmp_path, bundle, capsys
):
    path, prediction = freeze(tmp_path, bundle)
    answers = write(
        tmp_path / "answers.json",
        manifest(
            prediction,
            [
                answer(
                    expected_status="conflicting", forbidden_evidence_codes=["seller_color_claim"]
                )
            ],
        ),
    )
    report_path = tmp_path / "report.json"
    assert (
        evaluation.main(
            [
                "score",
                "--predictions",
                str(path),
                "--answers",
                str(answers),
                "--output",
                str(report_path),
            ]
        )
        == 1
    )
    text = capsys.readouterr().out
    assert "failed" in text and "mismatches: 2" in text
    assert "accuracy" not in text
    report = json.loads(report_path.read_text())
    assert report["cases"][0]["prediction"] == prediction["cases"][0]
    assert report["expectation_mismatch_count"] == 2


def test_cli_subprocess_does_not_load_live_cli_or_open_database(tmp_path, bundle):
    source = write(tmp_path / "input.json", bundle)
    code = (
        "import sys; from finder.local_evaluation import main; "
        "result = main(sys.argv[1:]); assert 'finder.cli' not in sys.modules; "
        "assert 'finder.config' not in sys.modules; raise SystemExit(result)"
    )
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "predict",
            "--input",
            str(source),
            "--assessed-at",
            CLOCK,
            "--output",
            str(tmp_path / "result.json"),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == ["input.json", "result.json"]
