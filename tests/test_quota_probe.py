import pytest

from finder.adapters.ebay.quota import summarize_browse_quota


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
