"""Only aggregate, application-level eBay Browse quota data."""

from __future__ import annotations

import re


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
        for resource in api["resources"]:
            if not isinstance(resource, dict) or not isinstance(resource.get("rates"), list):
                raise ValueError("Malformed Browse resource")
            name = resource.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_]{1,64}", name):
                raise ValueError("Malformed Browse resource name")
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
            resources.append({"name": name, "rates": rates})
    if not resources:
        raise ValueError("No Browse quota returned")
    return {"status": "available", "api": "buy.browse", "resources": resources}
