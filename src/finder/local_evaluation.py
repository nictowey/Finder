"""Freeze local simulations first; score separately authored answers later.

This is a development regression boundary, not an untrusted-code sandbox or a
claim that case authors were blind to predictions. It never opens a database.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from finder.offline import OfflineSafetyError, _check_environment

VERSION = 1
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
TIERS = ("possible_pressing", "family_review", "conflicting", "unrelated")
IDENTITIES = ("target", "other", "unresolved", "unlabeled")
NOTICE = (
    "Synthetic/user-authored case agreement only. No physical identity, market precision, "
    "catalog completeness, time-saved, or independent held-out validation claim. "
    "Freshness and eligibility are simulations at the fixed assessment clock; no alerts are sent."
)
_active_boundary = None
_guard_installed = False


class EvaluationError(ValueError):
    """A bounded input or artifact error that is safe to show locally."""


def _plain(value: str) -> str:
    if re.search(
        r"[\x00-\x1f\x7f]|://|\b(?:www\.|data:|javascript:|mailto:|file:|Bearer\s)|"
        r"-----BEGIN|\b(?:api[_ -]?key|password|access[_ -]?token)\s*[:=]",
        value,
        re.I,
    ):
        raise ValueError("Use plain text without URLs or credentials")
    return value


Text = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_plain)]
CaseId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Provenance(_Model):
    kind: Literal["synthetic_case_design", "user_authored_case", "ui_assisted_judgment"]
    detail: Text


class Answer(_Model):
    id: CaseId
    declared_identity: Literal["target", "other", "unresolved", "unlabeled"]
    provenance: Provenance
    rationale: Text
    expected_status: (
        Literal["possible_pressing", "family_review", "conflicting", "unrelated"] | None
    ) = None
    expected_eligible: bool | None = None
    required_evidence_codes: list[Code] = Field(default_factory=list, max_length=100)
    forbidden_evidence_codes: list[Code] = Field(default_factory=list, max_length=100)


class AnswerManifest(_Model):
    schema_version: Literal[1]
    kind: Literal["finder-local-answers"]
    answer_version: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]
    input_digest: Digest
    coverage: Literal["complete", "partial"]
    answers: list[Answer] = Field(max_length=100)


def _canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvaluationError("JSON object keys must be unique.")
        value[key] = item
    return value


def _path(value: str | Path) -> Path:
    text = str(value)
    if not text or ":" in text or "\\" in text or text.startswith("//"):
        raise EvaluationError("Use a local file path, not a URL or URI.")
    path = Path(text).expanduser().absolute()
    if any(part == ".env" or part.startswith(".env.") for part in path.parts):
        raise EvaluationError("Evaluation cannot use dotenv paths.")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise EvaluationError("Evaluation cannot use symlink paths.")
    return path.resolve()


def _read(path: Path, *, limit=MAX_ARTIFACT_BYTES):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise EvaluationError("Input must be a bounded regular, unlinked local file.")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise EvaluationError("Input exceeds the bounded file size.")
        value = json.loads(raw, object_pairs_hook=_pairs)
        _canonical(value)  # Reject NaN/Infinity even in fields that are later ignored.
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError("Cannot read valid bounded local JSON.") from None


def _write_new(path: Path, value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise EvaluationError("Output exceeds the bounded artifact size.")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
    except FileExistsError:
        raise EvaluationError(
            "Evaluation never overwrites existing files; choose a fresh path."
        ) from None
    except OSError:
        raise EvaluationError(
            "Cannot save a fresh local artifact; preserve any partial file."
        ) from None


def _audit(event, args):
    if _active_boundary is None:
        return
    reads, output = _active_boundary
    if event.startswith("socket.") or event in {
        "sqlite3.connect",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.truncate",
    }:
        raise EvaluationError("Evaluation disables network, subprocesses, databases and deletion.")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).absolute()
        if any(part == ".env" or part.startswith(".env.") for part in path.parts):
            raise EvaluationError("Evaluation does not read dotenv files.")
        writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if writing:
            if path != output:
                raise EvaluationError("Evaluation only writes its designated fresh output.")
        elif path not in reads and path.suffix not in {".py", ".pyc", ".so"}:
            raise EvaluationError("Evaluation only reads its declared input artifacts and code.")


@contextmanager
def _boundary(reads, output):
    global _active_boundary, _guard_installed
    _check_environment(os.environ)
    blocked = sorted(
        key
        for key, value in os.environ.items()
        if value and re.search(r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)(?:_|$)", key)
    )
    if blocked:
        raise EvaluationError(
            "Evaluation requires a credential-free environment; unset: " + ", ".join(blocked)
        )
    if _active_boundary is not None:
        raise EvaluationError("An evaluation is already running.")
    if not _guard_installed:
        sys.addaudithook(_audit)
        _guard_installed = True
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    _active_boundary = (set(reads), output)
    try:
        yield
    finally:
        _active_boundary = None
        sys.dont_write_bytecode = previous


def _clock(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError(
            "Assessment time must be an explicit ISO timestamp with timezone."
        ) from None


def _implementation():
    import pydantic

    from finder.matching import MATCH_POLICY_VERSION
    from finder.watch_worker import POLICY

    root = Path(__file__).resolve().parent
    hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
    }
    return {
        "review_policy": POLICY,
        "matching_policy": MATCH_POLICY_VERSION,
        "finder_source_digest": _digest(hashes),
        "finder_source_files": hashes,
        "python_version": sys.version.split()[0],
        "pydantic_version": pydantic.__version__,
    }


def _predict(value, now):
    from finder.local_workspace import review_local_case, validate_bundle

    bundle = validate_bundle(value, now=now)
    canonical = bundle.model_dump(mode="json")
    ids = [row.id for row in bundle.listings]
    if len(ids) != len(set(ids)):
        raise EvaluationError(
            "Evaluation case IDs must be unique; observations are not separate cases."
        )
    if not ids:
        raise EvaluationError("Provide at least one local case to freeze.")
    implementation = _implementation()
    alternatives = canonical.get("alternatives") or []
    cases = []
    for row in bundle.listings:
        result = review_local_case(bundle, row, now=now)
        review = result["review"]
        cases.append(
            {
                **result,
                "evidence_codes": sorted(set(review["clues"] + review["verify"])),
                "unknowns_and_verification": review["verify"],
                "price_basis": {
                    "kind": row.price_kind,
                    "item_price": canonical["listings"][len(cases)]["current_price"],
                    "shipping_cost": canonical["listings"][len(cases)]["shipping_cost"],
                    "item_currency": row.currency,
                    "shipping_currency": row.shipping_currency,
                    "delivered_subtotal": review["subtotal"],
                    "excludes": ["tax", "duties", "fees"],
                    "maximum_subtotal": canonical["settings"]["maximum_subtotal"],
                    "gamble_max": canonical["settings"]["gamble_max"],
                    "quotes_and_thresholds": "user_supplied_unverified",
                },
                "comparison_scope": {
                    "kind": "supplied_profiles_only"
                    if alternatives
                    else "alternatives_unavailable",
                    "supplied_profile_ids": [item["id"] for item in alternatives],
                    "supplied_profile_count": len(alternatives),
                    "complete_catalog": False,
                },
            }
        )
    payload = {
        "schema_version": VERSION,
        "kind": "finder-local-predictions",
        "notice": NOTICE,
        "assessed_at": now.isoformat(),
        "clock_semantics": "fixed_simulation_clock",
        "input_digest": _digest(canonical),
        "canonical_input": canonical,
        "implementation": implementation,
        "cases": cases,
    }
    return {**payload, "prediction_digest": _digest(payload)}


def freeze_predictions(input_path, output_path, *, assessed_at):
    """Read only input metadata, run the shared matcher, and exclusively create a freeze."""
    source, output = _path(input_path), _path(output_path)
    now = _clock(assessed_at)
    with _boundary([source], output):
        prediction = _predict(_read(source, limit=256 * 1024), now)
        _write_new(output, prediction)
    return prediction


def _validate_prediction(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "notice",
        "assessed_at",
        "clock_semantics",
        "input_digest",
        "canonical_input",
        "implementation",
        "cases",
        "prediction_digest",
    }:
        raise EvaluationError("Unrecognized frozen prediction artifact.")
    payload = {key: item for key, item in value.items() if key != "prediction_digest"}
    if value["prediction_digest"] != _digest(payload):
        raise EvaluationError("Frozen prediction digest mismatch; preserve the original artifact.")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != VERSION
        or value["kind"] != "finder-local-predictions"
        or value["clock_semantics"] != "fixed_simulation_clock"
        or value["notice"] != NOTICE
        or value["input_digest"] != _digest(value["canonical_input"])
    ):
        raise EvaluationError("Frozen prediction schema or input digest mismatch.")
    _clock(value["assessed_at"])
    try:
        implementation = value["implementation"]
        if _digest(implementation["finder_source_files"]) != implementation["finder_source_digest"]:
            raise EvaluationError("Frozen source digest mismatch.")
        rows = value["cases"]
        expected_ids = [row["id"] for row in value["canonical_input"]["listings"]]
        actual_ids = [row["id"] for row in rows]
        if (
            not 1 <= len(rows) <= 100
            or actual_ids != expected_ids
            or len(set(actual_ids)) != len(rows)
        ):
            raise EvaluationError("Frozen case IDs must uniquely match every canonical input case.")
        for row in rows:
            review = row["review"]
            if (
                review["status"] not in TIERS
                or type(review["notify"]) is not bool
                or review["policy"] != implementation["review_policy"]
                or row["comparison_scope"]["complete_catalog"] is not False
                or row["evidence_codes"] != sorted(set(review["clues"] + review["verify"]))
                or row["unknowns_and_verification"] != review["verify"]
            ):
                raise EvaluationError("Frozen case evidence or policy is inconsistent.")
            for key in ("listing", "comparison_evidence", "price_basis", "comparison_scope"):
                if key not in row:
                    raise EvaluationError("Frozen case is missing evidence.")
    except (KeyError, TypeError, AttributeError):
        raise EvaluationError("Frozen prediction fields are invalid.") from None
    return value


def _metric(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else None,
    }


def _score(prediction, raw_answers):
    try:
        answers = AnswerManifest.model_validate(raw_answers)
        if type(raw_answers["schema_version"]) is not int:
            raise ValueError
    except (ValidationError, ValueError, TypeError, KeyError):
        raise EvaluationError(
            "Invalid answer manifest; use bounded, separately authored labels."
        ) from None
    if answers.input_digest != prediction["input_digest"]:
        raise EvaluationError("Answer input digest does not match the frozen input.")
    ids = [answer.id for answer in answers.answers]
    if len(set(ids)) != len(ids):
        raise EvaluationError("Answer IDs must be unique.")
    known = {row["id"] for row in prediction["cases"]}
    if set(ids) - known:
        raise EvaluationError("Answer manifest contains unknown case IDs.")
    missing = sorted(known - set(ids))
    if missing and answers.coverage == "complete":
        raise EvaluationError("Complete answer coverage requires an entry for every frozen case.")
    by_id = {answer.id: answer for answer in answers.answers}
    matrix = {tier: dict.fromkeys(IDENTITIES, 0) for tier in TIERS}
    cases = []
    for row in prediction["cases"]:
        answer = by_id.get(row["id"])
        identity = answer.declared_identity if answer else "unlabeled"
        matrix[row["review"]["status"]][identity] += 1
        checks = []
        if answer:
            required, forbidden = (
                set(answer.required_evidence_codes),
                set(answer.forbidden_evidence_codes),
            )
            if required & forbidden:
                raise EvaluationError("A code cannot be both required and forbidden.")
            if answer.expected_status is not None:
                checks.append(
                    {
                        "field": "status",
                        "expected": answer.expected_status,
                        "actual": row["review"]["status"],
                    }
                )
            if answer.expected_eligible is not None:
                checks.append(
                    {
                        "field": "eligible",
                        "expected": answer.expected_eligible,
                        "actual": row["review"]["notify"],
                    }
                )
            for code in sorted(required | forbidden):
                checks.append(
                    {
                        "field": "evidence_code",
                        "code": code,
                        "expected": code in required,
                        "actual": code in row["evidence_codes"],
                    }
                )
        for check in checks:
            check["passed"] = check["actual"] == check["expected"]
        cases.append(
            {
                "prediction": row,
                "answer": answer.model_dump(mode="json") if answer else None,
                "declared_identity": identity,
                "missing_answer": answer is None,
                "expectation_checks": checks,
                "mismatches": [check for check in checks if not check["passed"]],
            }
        )
    total = len(cases)
    identities = {
        label: sum(row["declared_identity"] == label for row in cases) for label in IDENTITIES
    }
    decided = [row for row in cases if row["declared_identity"] in {"target", "other"}]
    supported = [
        row for row in cases if row["prediction"]["review"]["status"] == "possible_pressing"
    ]
    supported_decided = [
        row for row in supported if row["declared_identity"] in {"target", "other"}
    ]
    leads = [
        row
        for row in cases
        if row["prediction"]["review"]["status"] in {"possible_pressing", "family_review"}
    ]
    qualifying = [row for row in cases if row["prediction"]["review"]["notify"]]
    decided_leads = [row for row in leads if row["declared_identity"] in {"target", "other"}]
    checks = [check for row in cases for check in row["expectation_checks"]]
    mismatch_count = sum(not check["passed"] for check in checks)
    payload = {
        "schema_version": VERSION,
        "kind": "finder-local-agreement-report",
        "notice": NOTICE,
        "input_digest": prediction["input_digest"],
        "prediction_digest": prediction["prediction_digest"],
        "answer_digest": _digest(answers.model_dump(mode="json")),
        "answer_version": answers.answer_version,
        "canonical_input": prediction["canonical_input"],
        "identity_outcome": "scored" if decided else "not_evaluated",
        "assessed_at": prediction["assessed_at"],
        "clock_semantics": prediction["clock_semantics"],
        "implementation": prediction["implementation"],
        "coverage": {
            "declared": answers.coverage,
            "total_cases": total,
            "answer_entries": len(ids),
            "missing_answer_ids": missing,
            "identities": identities,
            "decided_identity_cases": len(decided),
            "ui_assisted_answer_count": sum(
                a.provenance.kind == "ui_assisted_judgment" for a in answers.answers
            ),
        },
        "tier_by_declared_identity": matrix,
        "metrics": {
            "abstentions": _metric(sum(matrix["family_review"].values()), total),
            "supported_positive_agreement": _metric(
                sum(row["declared_identity"] == "target" for row in supported_decided),
                len(supported_decided),
            ),
            "declared_target_recognition": _metric(
                matrix["possible_pressing"]["target"], identities["target"]
            ),
            "supported_positive_cases": len(supported),
            "supported_positive_undecided_cases": len(supported) - len(supported_decided),
            "false_review_leads": _metric(
                sum(row["declared_identity"] == "other" for row in decided_leads),
                len(decided_leads),
            ),
            "review_lead_cases": len(leads),
            "review_lead_undecided_cases": len(leads) - len(decided_leads),
            "excluded_declared_positives": _metric(
                sum(
                    row["declared_identity"] == "target"
                    and row["prediction"]["review"]["status"] in {"conflicting", "unrelated"}
                    for row in cases
                ),
                identities["target"],
            ),
            "abstained_declared_positives": matrix["family_review"]["target"],
            "supported_declared_other_cases": matrix["possible_pressing"]["other"],
            "expectation_agreement": _metric(len(checks) - mismatch_count, len(checks)),
        },
        "simulated_qualification": {
            "qualifying_cases": len(qualifying),
            "total_cases": total,
            "qualifying_declared_other_cases": sum(
                row["declared_identity"] == "other" for row in qualifying
            ),
            "nonqualifying_declared_target_cases": sum(
                row["declared_identity"] == "target" and not row["prediction"]["review"]["notify"]
                for row in cases
            ),
        },
        "expectation_outcome": ("failed" if mismatch_count else "passed")
        if checks
        else "not_evaluated",
        "expectations_passed": mismatch_count == 0 if checks else None,
        "expectation_mismatch_count": mismatch_count,
        "cases": cases,
    }
    return {**payload, "report_digest": _digest(payload)}


def score_predictions(predictions_path, answers_path, output_path):
    """Score a verified freeze without importing or invoking the review adapter."""
    source, answers, output = _path(predictions_path), _path(answers_path), _path(output_path)
    with _boundary([source, answers], output):
        prediction = _validate_prediction(_read(source))
        report = _score(prediction, _read(answers, limit=256 * 1024))
        _write_new(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="finder-evaluate", description=NOTICE)
    commands = parser.add_subparsers(dest="command", required=True)
    predict = commands.add_parser(
        "predict", help="Freeze local simulations without reading answers"
    )
    predict.add_argument("--input", required=True)
    predict.add_argument(
        "--assessed-at", required=True, help="Explicit ISO replay/simulation clock"
    )
    predict.add_argument("--output", required=True, help="Fresh read-only local JSON artifact")
    score = commands.add_parser("score", help="Score a frozen artifact against separate answers")
    score.add_argument("--predictions", required=True)
    score.add_argument("--answers", required=True)
    score.add_argument("--output", required=True, help="Fresh read-only local JSON report")
    args = parser.parse_args(argv)
    try:
        if args.command == "predict":
            artifact = freeze_predictions(args.input, args.output, assessed_at=args.assessed_at)
            print("Frozen local predictions. Input digest: " + artifact["input_digest"])
        else:
            report = score_predictions(args.predictions, args.answers, args.output)
            metrics = report["metrics"]
            supported_other = metrics["supported_declared_other_cases"]
            excluded_target = metrics["excluded_declared_positives"]["numerator"]
            print(
                "Synthetic/user-authored case agreement report saved. "
                f"Expectations: {report['expectation_outcome']}; "
                f"mismatches: {report['expectation_mismatch_count']}. "
                f"Supported declared-other cases: {supported_other}; "
                f"excluded declared-target cases: {excluded_target}."
            )
            if report["expectations_passed"] is False:
                return 1
    except (EvaluationError, OfflineSafetyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError:
        # Shared adapter validation messages must never echo the raw input.
        print("Invalid local evaluation input; inspect the documented schema.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
