"""Only aggregate, application-level eBay Browse quota data."""

from __future__ import annotations

import re

# A whole watch can use four searches with ten details each on its first pass.
# Allow every Browse request to retry three times and leave room for other jobs.
WATCH_BROWSE_REQUEST_ALLOWANCE = 4 * (1 + 10) * 4
OTHER_BROWSE_REQUEST_RESERVE = 100


def summarize_browse_quota(payload: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("rateLimits"), list):
        raise ValueError("Missing application rate limits")
    resources = []
    for api in payload["rateLimits"]:
        if not isinstance(api, dict) or (
            str(api.get("apiContext", "")).lower(),
            str(api.get("apiName", "")).lower(),
        ) != ("buy", "browse"):
            continue
        if not isinstance(api.get("resources"), list):
            raise ValueError("Missing Browse resources")
        for index, resource in enumerate(api["resources"], 1):
            if not isinstance(resource, dict) or not isinstance(resource.get("rates"), list):
                raise ValueError("Malformed Browse resource")
            name = resource.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("Malformed Browse resource name")
            # API resource paths may contain slashes, hyphens and digits. Never emit an
            # unexpected arbitrary string in a public workflow log.
            public_name = (
                name if re.fullmatch(r"[A-Za-z0-9_./ -]{1,96}", name) else f"resource_{index}"
            )
            rates = []
            for rate in resource["rates"]:
                if not isinstance(rate, dict):
                    raise ValueError("Malformed Browse rate")
                limit, remaining, window = (
                    rate.get("limit"),
                    rate.get("remaining"),
                    rate.get("timeWindow"),
                )
                if (
                    any(type(n) is not int for n in (limit, remaining, window))
                    or not 0 <= remaining <= limit
                    or window <= 0
                ):
                    raise ValueError("Invalid Browse rate numbers")
                rates.append(
                    {"limit": limit, "remaining": remaining, "time_window_seconds": window}
                )
            if not rates:
                raise ValueError("Missing Browse rate windows")
            resources.append({"name": public_name, "rates": rates})
    if not resources:
        raise ValueError("No Browse quota returned")
    return {"status": "available", "api": "buy.browse", "resources": resources}


def watch_scan_quota(quota: dict) -> dict:
    """Fail closed unless the shared Browse pool covers one whole watch and a reserve."""
    shared = [r for r in quota["resources"] if r["name"] == "buy.browse"]
    if len(shared) != 1:
        raise ValueError("Shared Browse quota missing")
    remaining = min(rate["remaining"] for rate in shared[0]["rates"])
    required = WATCH_BROWSE_REQUEST_ALLOWANCE + OTHER_BROWSE_REQUEST_RESERVE
    return {"remaining": remaining, "required": required, "allowed": remaining >= required}
