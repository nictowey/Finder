import pytest

from finder.adapters.ebay.quota import summarize_browse_quota, watch_scan_quota


def test_browse_quota_only_includes_aggregate_filtered_resources():
    payload = {
        "rateLimits": [
            {"apiContext": "sell", "apiName": "inventory", "resources": []},
            {
                "apiContext": "buy",
                "apiName": "browse",
                "resources": [
                    {
                        "name": "item_summary",
                        "rates": [
                            {
                                "limit": 5000,
                                "remaining": 4321,
                                "timeWindow": 86400,
                                "reset": "2026-09-24T00:00:00Z",
                                "count": 679,
                            }
                        ],
                    }
                ],
            },
        ]
    }
    assert summarize_browse_quota(payload) == {
        "status": "available",
        "api": "buy.browse",
        "resources": [
            {
                "name": "item_summary",
                "rates": [{"limit": 5000, "remaining": 4321, "time_window_seconds": 86400}],
            }
        ],
    }


def test_watch_budget_uses_shared_pool_and_safely_stops_at_reserve():
    quota = {
        "resources": [
            {"name": "buy.browse", "rates": [{"remaining": 276}, {"remaining": 300}]},
            {"name": "buy.browse.item.bulk", "rates": [{"remaining": 5000}]},
        ]
    }
    assert watch_scan_quota(quota) == {"remaining": 276, "required": 276, "allowed": True}
    quota["resources"][0]["rates"][0]["remaining"] = 275
    assert watch_scan_quota(quota)["allowed"] is False
    quota["resources"].pop(0)
    with pytest.raises(ValueError, match="Shared Browse quota missing"):
        watch_scan_quota(quota)


def test_resource_path_is_accepted_but_unexpected_text_is_not_logged():
    def quota(name):
        return {
            "rateLimits": [
                {
                    "apiContext": "buy",
                    "apiName": "browse",
                    "resources": [
                        {
                            "name": name,
                            "rates": [{"limit": 5000, "remaining": 4000, "timeWindow": 86400}],
                        }
                    ],
                }
            ]
        }

    assert summarize_browse_quota(quota("item_summary/search"))["resources"][0]["name"] == (
        "item_summary/search"
    )
    assert summarize_browse_quota(quota("secret@example.com"))["resources"][0]["name"] == (
        "resource_1"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rateLimits": []},
        {"rateLimits": [{"apiContext": "buy", "apiName": "browse", "resources": []}]},
        {
            "rateLimits": [
                {
                    "apiContext": "buy",
                    "apiName": "browse",
                    "resources": [
                        {
                            "name": "item",
                            "rates": [{"limit": True, "remaining": 1, "timeWindow": 1}],
                        }
                    ],
                }
            ]
        },
    ],
)
def test_quota_probe_cannot_present_unknown_or_malformed_limits_as_available(payload):
    with pytest.raises(ValueError):
        summarize_browse_quota(payload)
