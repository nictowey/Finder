"""Offline, read-only evidence about aggregate polling and notification queues.

Never construct a store/worker, reconcile an outbox, probe a provider, or send.
All output is allowlisted: no paths, titles, credentials, payloads or destination IDs.
"""

import argparse
import json
import math
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from finder.discogs_app_auth import AppCredentials
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    MAX_ATTEMPTS,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
    POLL_SECONDS,
    PriceLeadRule,
)
from finder.discogs_price_worker import private_topic

_STATES = (
    "pending",
    "retry_wait",
    "leased",
    "accepted",
    "uncertain",
    "failed",
    "expired",
    "revoked",
)
_TABLES = {
    "discogs_price_control",
    "discogs_price_rules",
    "discogs_price_samples",
    "discogs_price_outbox",
    "discogs_monitor_control",
}


class HealthUnavailable(ValueError):
    """Fixed diagnostics only; never wrap a database or configuration exception."""


def _number(value, *, optional=False, integer=False):
    if value is None and optional:
        return None
    if (
        type(value) not in ((int,) if integer else (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise HealthUnavailable("invalid_persisted_data")
    return value


def _flag(value):
    if type(value) is not int or value not in (0, 1):
        raise HealthUnavailable("invalid_persisted_data")
    return bool(value)


def _counts(values):
    counts = Counter(values)
    return {state: counts[state] for state in _STATES}


def _configuration(app_file, ntfy_config_file, topic_file):
    if ntfy_config_file is not None and topic_file is not None:
        raise HealthUnavailable("invalid_configuration_arguments")
    app_status = "not_checked"
    if app_file is not None:
        try:
            AppCredentials(app_file)
            app_status = "valid"
        except (OSError, ValueError, RecursionError):
            app_status = "unavailable"
    binding = None
    destination = {"status": "not_checked", "kind": "not_specified"}
    if ntfy_config_file is not None or topic_file is not None:
        destination["kind"] = "private_ntfy" if ntfy_config_file is not None else "public_ntfy"
        try:
            sender = (
                NtfySender(private_config=PrivateNtfyConfig(ntfy_config_file), enabled=True)
                if ntfy_config_file is not None
                else NtfySender(private_topic(topic_file), enabled=True)
            )
            binding = sender.binding
            destination["status"] = "valid"
        except (OSError, ValueError, RecursionError):
            destination["status"] = "unavailable"
    return {
        "discogs_app": {"status": app_status},
        "destination": destination,
        "network_checked": False,
        "running_worker_configuration_verified": False,
    }, binding


def _snapshot(database):
    """A consistent SQLite read transaction, including committed WAL data.

    mode=ro prevents creation/writes; query_only is defense in depth. Do not use
    immutable=1: ignoring live journal coordination can return incorrect evidence.
    Missing/foreign/old schemas are reported, never created or migrated.
    """
    path = Path(database).absolute()
    if path.is_symlink() or path.name.startswith(".env") or not path.is_file():
        raise HealthUnavailable("database_unavailable")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            raise HealthUnavailable("wrong_workspace")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not _TABLES <= tables:
            raise HealthUnavailable("aggregate_schema_unavailable")
        controls = db.execute("SELECT * FROM discogs_price_control WHERE id=1").fetchall()
        pacing = db.execute(
            "SELECT next_request FROM discogs_monitor_control WHERE id=1"
        ).fetchall()
        if len(controls) != 1 or len(pacing) != 1:
            raise HealthUnavailable("aggregate_control_unavailable")
        cloud = None
        if "cloud_control" in tables:
            row = db.execute("SELECT killed FROM cloud_control WHERE id=1").fetchone()
            if row is None:
                raise HealthUnavailable("invalid_persisted_data")
            cloud = _flag(row[0])
        return (
            dict(controls[0]),
            _number(pacing[0][0]),
            cloud,
            [
                dict(row)
                for row in db.execute("SELECT * FROM discogs_price_rules ORDER BY release_id")
            ],
            {
                row["release_id"]: dict(row)
                for row in db.execute("SELECT * FROM discogs_price_samples")
            },
            [dict(row) for row in db.execute("SELECT * FROM discogs_price_outbox")],
        )


def _rule_report(row, sample, now, poll_seconds):
    rule = PriceLeadRule.model_validate_json(row["data"])
    if rule.release_id != row["release_id"]:
        raise HealthUnavailable("invalid_persisted_data")
    revision = _number(row["revision"], integer=True)
    result = {
        "release_id": rule.release_id,
        "revision": revision,
        "enabled": rule.enabled,
        "mode": "any_price" if rule.any_price else "item_price_ceiling",
        "sample_status": "missing",
        "freshness": "never_succeeded",
        "last_success_at": None,
        "last_success_age_seconds": None,
        "last_observed_at": None,
        "consecutive_failed_polls": 0,
        "next_check_at": None,
        "poll_overdue_seconds": None,
        "poll_claim": "none",
        "issues": [],
    }
    if sample is None:
        result["issues"].append("sample_missing")
    else:
        status = sample["status"]
        if status not in ("waiting", "ok", "unknown", "error"):
            raise HealthUnavailable("invalid_persisted_data")
        success = _number(sample["last_success"], optional=True)
        observed = _number(sample["last_observed"], optional=True)
        due = _number(sample["next_check"])
        failures = _number(sample["failures"], integer=True)
        _number(sample["episode"], integer=True)
        _flag(sample["low"])
        _flag(sample["seen"])
        age = now - success if success is not None else None
        freshness = (
            "never_succeeded"
            if age is None
            else "future_timestamp"
            if age < 0
            else "fresh"
            if age < FRESH_SECONDS
            else "stale"
        )
        claim = "none"
        if sample["claim"] is not None:
            until = _number(sample["claim_until"])
            claim = "active" if until > now else "expired"
        result.update(
            sample_status=status,
            freshness=freshness,
            last_success_at=success,
            last_success_age_seconds=age,
            last_observed_at=observed,
            consecutive_failed_polls=failures,
            next_check_at=due,
            poll_overdue_seconds=max(0, now - due),
            poll_claim=claim,
        )
        if status in ("error", "unknown"):
            result["issues"].append("poll_error" if status == "error" else "sample_unknown")
        if failures:
            result["issues"].append("failed_polls")
        if claim == "expired":
            result["issues"].append("poll_claim_expired")
        if now - due >= poll_seconds and claim != "active":
            result["issues"].append("poll_overdue")
        if observed is not None and observed > now:
            result["issues"].append("observation_in_future")
    if result["freshness"] != "fresh":
        result["issues"].append(result["freshness"])
    return rule, result


def _queue_report(rows, rules, samples, control, binding, now):
    stored, effective, blocks = [], [], Counter()
    per_rule = {}
    waiting_ages, retry_times = [], []
    due = eligible = expired_leases = destination_mismatch = 0
    for row in rows:
        state = row["state"]
        if state not in _STATES:
            raise HealthUnavailable("invalid_persisted_data")
        release = _number(row["release_id"], integer=True)
        _number(row["rule_revision"], integer=True)
        _number(row["episode"], integer=True)
        observed = _number(row["observed_at"])
        attempts = _number(row["attempts"], integer=True)
        next_attempt = _number(row["next_attempt"])
        stored.append(state)
        if state == "leased" and _number(row["lease_until"]) <= now:
            state = "uncertain"
            expired_leases += 1
        elif state in ("pending", "retry_wait") and observed <= now - FRESH_SECONDS:
            state = "expired"
        effective.append(state)
        per_rule.setdefault(release, []).append(state)
        if state not in ("pending", "retry_wait"):
            continue
        waiting_ages.append(max(0, now - observed))
        if state == "retry_wait":
            retry_times.append(next_attempt)
        reasons = []
        if observed > now:
            reasons.append("observation_in_future")
        if next_attempt > now:
            reasons.append("retry_or_send_delay")
        else:
            due += 1
        if not control["enabled"]:
            reasons.append("alerts_disabled")
        if control["send_cooldown_seconds"] > 0:
            reasons.append("send_cooldown")
        if control["daily_cap_reached"]:
            reasons.append("daily_cap")
        if attempts >= MAX_ATTEMPTS:
            reasons.append("attempt_limit")
        if binding is None:
            reasons.append("destination_not_verified")
        elif row["destination_ref"] is not None and (
            row["destination_ref"] != binding.destination_ref or row["provider_id"] != "ntfy"
        ):
            destination_mismatch += 1
            reasons.append("destination_mismatch")
        rule_pair, sample = rules.get(release), samples.get(release)
        if rule_pair is None or sample is None:
            reasons.append("rule_or_sample_missing")
        else:
            rule, report = rule_pair
            if not rule.enabled:
                reasons.append("rule_disabled")
            if row["rule_revision"] != report["revision"] or row["episode"] != sample["episode"]:
                reasons.append("rule_or_episode_changed")
            if (
                not sample["low"]
                or sample["status"] != "ok"
                or sample["price"] != row["price"]
                or report["freshness"] != "fresh"
            ):
                reasons.append("sample_not_current")
        blocks.update(reasons)
        eligible += not reasons
    return {
        "stored_states": _counts(stored),
        "effective_states": _counts(effective),
        "projection_only": True,
        "expired_leases_projected_uncertain": expired_leases,
        "waiting_total": len(waiting_ages),
        "due_waiting": due,
        "eligible_now": eligible,
        "oldest_waiting_age_seconds": max(waiting_ages, default=None),
        "next_retry_at": min(retry_times, default=None),
        "destination_mismatch": destination_mismatch,
        "blocked_reasons": dict(sorted(blocks.items())),
    }, per_rule


def health_report(
    database,
    *,
    now=None,
    poll_seconds=POLL_SECONDS,
    discogs_app_file=None,
    ntfy_config_file=None,
    topic_file=None,
):
    """Return safe evidence, not a service/process/device liveness assertion."""
    try:
        now = _number(time.time() if now is None else now)
        if (
            type(poll_seconds) is not int
            or not MIN_POLL_SECONDS <= poll_seconds <= MAX_POLL_SECONDS
        ):
            raise HealthUnavailable("invalid_poll_interval")
        configuration, binding = _configuration(discogs_app_file, ntfy_config_file, topic_file)
        control, pacing, killed, rows, samples, outbox = _snapshot(database)
        sends_today = (
            _number(control["sends"], integer=True)
            if _number(control["send_day"], integer=True) == int(now // 86400)
            else 0
        )
        controls = {
            "enabled": _flag(control["enabled"]),
            "revision": _number(control["revision"], integer=True),
            "request_cooldown_seconds": max(0, pacing - now),
            "send_cooldown_seconds": max(0, _number(control["next_send"]) - now),
            "send_attempts_today": sends_today,
            "daily_send_cap": DAILY_SEND_CAP,
            "daily_cap_reached": sends_today >= DAILY_SEND_CAP,
            "cloud_kill_switch": killed,
            "cloud_kill_switch_controls_aggregate_alerts": False,
        }
        rules = {
            row["release_id"]: _rule_report(row, samples.get(row["release_id"]), now, poll_seconds)
            for row in rows
        }
        queue, by_rule = _queue_report(outbox, rules, samples, controls, binding, now)
        reports = [pair[1] for pair in rules.values()]
        for report in reports:
            report["queue_effective_states"] = _counts(by_rule.get(report["release_id"], []))
        enabled = [report for report in reports if report["enabled"]]
        issues = []
        if not controls["enabled"]:
            issues.append("alerts_disabled")
        if not reports:
            issues.append("no_rules")
        elif not enabled:
            issues.append("no_enabled_rules")
        if any(report["issues"] for report in enabled):
            issues.append("polling_needs_attention")
        for key in ("discogs_app", "destination"):
            if configuration[key]["status"] != "valid":
                issues.append(f"{key}_{configuration[key]['status']}")
        for state in ("retry_wait", "uncertain", "failed", "expired"):
            if queue["effective_states"][state]:
                issues.append(f"queue_{state}")
        if queue["destination_mismatch"]:
            issues.append("destination_mismatch")
        if any(
            reason not in {"retry_or_send_delay", "send_cooldown"}
            for reason in queue["blocked_reasons"]
        ):
            issues.append("queue_blocked")
        if queue["oldest_waiting_age_seconds"] is not None and (
            queue["oldest_waiting_age_seconds"] >= poll_seconds
        ):
            issues.append("queue_backlog")
        if controls["daily_cap_reached"]:
            issues.append("daily_send_cap_reached")
        status = (
            "empty"
            if not reports
            else "disabled"
            if not controls["enabled"] or not enabled
            else "attention"
            if issues
            else "healthy"
        )
        return {
            "schema_version": 1,
            "status": status,
            "snapshot_at": now,
            "read_only": True,
            "purchase_allowed": False,
            "issues": issues,
            "expected_poll_seconds": poll_seconds,
            "sample_fresh_seconds": FRESH_SECONDS,
            "controls": controls,
            "configuration": configuration,
            "rules_total": len(reports),
            "rules_enabled": len(enabled),
            "rules": reports,
            "queue": queue,
            "delivery": {
                "provider_accepted_total": queue["stored_states"]["accepted"],
                "phone_delivery": "unverified",
                "provider_acceptance_is_delivery": False,
            },
            "limitations": [
                "Stored evidence does not verify the worker is running or using these files.",
                "Freshness is local observation age; source inventory lag is unknown.",
                "Queue expiry and lease uncertainty are projected without changing stored rows.",
                "Failed, expired and uncertain totals include historical events.",
                "Provider acceptance is not proof of phone notification delivery.",
            ],
        }
    except HealthUnavailable as error:
        reason = str(error)
        if reason not in {
            "invalid_persisted_data",
            "invalid_configuration_arguments",
            "database_unavailable",
            "wrong_workspace",
            "aggregate_schema_unavailable",
            "aggregate_control_unavailable",
            "invalid_poll_interval",
        }:
            reason = "health_evidence_unavailable"
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        reason = "health_evidence_unavailable"
    return {
        "schema_version": 1,
        "status": "unavailable",
        "read_only": True,
        "purchase_allowed": False,
        "issues": [reason],
    }


def format_text(report):
    lines = [f"Aggregate monitor: {report['status']}"]
    if report["status"] == "unavailable":
        return "\n".join(lines + ["Issues: " + ", ".join(report["issues"])])
    lines.append(f"Rules: {report['rules_enabled']} enabled / {report['rules_total']} saved")
    control = report["controls"]
    lines.append(
        f"Stored alert control: {'enabled' if control['enabled'] else 'disabled'}; "
        f"send attempts today: {control['send_attempts_today']}/{control['daily_send_cap']}"
    )
    for rule in report["rules"]:
        age = rule["last_success_age_seconds"]
        age_text = "never" if age is None else f"{age:.0f}s ago"
        lines.append(
            f"Release {rule['release_id']}: {'enabled' if rule['enabled'] else 'disabled'}, "
            f"{rule['sample_status']}, {rule['freshness']}; last success {age_text}; "
            f"failed polls {rule['consecutive_failed_polls']}; "
            f"issues: {', '.join(rule['issues']) or 'none'}"
        )
    lines.append(
        "Queue (effective): "
        + ", ".join(f"{key}={value}" for key, value in report["queue"]["effective_states"].items())
    )
    lines.append(
        f"Waiting eligible now: {report['queue']['eligible_now']}; "
        f"destination mismatches: {report['queue']['destination_mismatch']}"
    )
    lines.append(
        "Configuration: Discogs app="
        + report["configuration"]["discogs_app"]["status"]
        + "; destination="
        + report["configuration"]["destination"]["status"]
    )
    lines.append("Issues: " + (", ".join(report["issues"]) or "none"))
    lines.extend(report["limitations"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only aggregate monitor health; no network")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    parser.add_argument("--discogs-app-file", type=Path)
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument("--ntfy-config-file", type=Path)
    destinations.add_argument("--topic-file", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    report = health_report(
        args.database,
        poll_seconds=args.poll_seconds,
        discogs_app_file=args.discogs_app_file,
        ntfy_config_file=args.ntfy_config_file,
        topic_file=args.topic_file,
    )
    print(
        json.dumps(report, sort_keys=True, allow_nan=False)
        if args.format == "json"
        else format_text(report)
    )
    return 2 if report["status"] == "unavailable" else 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
