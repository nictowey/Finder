"""Canonical operational notifications; no record interests, paths or secrets."""

REASONS = {
    "monitor_disabled": "the aggregate monitor is stopped",
    "no_enabled_rules": "there are no enabled aggregate rules",
    "polling_problem": "one or more enabled rules have failed, missing or stale checks",
    "configuration_problem": "the supplied monitor configuration is unavailable",
    "notification_backlog": "notification work is delayed or blocked",
    "new_delivery_problem": "a new notification attempt failed or has an uncertain outcome",
}


def health_message(kind, reasons=(), *, test=False):
    if kind not in ("degraded", "recovered") or type(test) is not bool:
        raise ValueError("Use a fixed health notification kind.")
    if not isinstance(reasons, (tuple, list)) or any(reason not in REASONS for reason in reasons):
        raise ValueError("Use fixed health reason codes.")
    reasons = sorted(set(reasons))
    if kind == "degraded" and not reasons or kind == "recovered" and reasons:
        raise ValueError("Use reasons only for degradation.")
    if test:
        return {
            "title": "TEST: Sniper health alert"
            if kind == "degraded"
            else "TEST: Sniper recovery alert",
            "message": "This is a test, not an outage. Your record rules are unchanged."
            if kind == "degraded"
            else "This is a recovery test. Your record rules are unchanged.",
        }
    return {
        "title": "Sniper needs attention" if kind == "degraded" else "Sniper recovered",
        "message": "Repeated checks found: "
        + "; ".join(REASONS[reason] for reason in reasons)
        + "."
        if kind == "degraded"
        else "Two consecutive checks are healthy again. "
        "Missed or uncertain notifications have not been resent.",
    }
