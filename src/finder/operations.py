"""Allow-listed operational counters; never retain provider evidence in the run ledger."""


def scan_metrics(summary, added):
    discovery = summary.get("discovery") or {}
    pages = discovery.get("newest") or []
    initial = discovery.get("initial_relevance") or []
    inventory = discovery.get("inventory_sample")
    all_pages = pages + initial + ([inventory] if inventory else [])
    result = {
        "new_inbox_rows": summary.get("new_inbox_rows", added),
        "catalog_check_failed": summary.get("catalog_check_failed") is True,
        "catalog_search_incomplete": summary.get("catalog_search_incomplete") is True,
        "capped_pages": sum(bool(row.get("cap_reached")) for row in all_pages),
        "partial_details": sum(row.get("partial_details", 0) for row in all_pages),
    }
    for name in (
        "browse_requests",
        "browse_retries",
        "quota_remaining",
        "quota_required",
        "search_requests",
        "detail_requests",
        "quota_requests",
        "catalog_requests",
        "catalog_retries",
    ):
        value = discovery.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[name] = value
    for name in ("observed", "ambiguous_leads"):
        value = summary.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[name] = value
    if summary.get("error") in ("scan_failed", "catalog_unavailable", "discovery_failed"):
        result["reason"] = summary["error"]
    return result
