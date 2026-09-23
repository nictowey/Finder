"""Read the Production application's official quota without revealing credentials or IDs."""

import json
import logging

from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.quota import summarize_browse_quota
from finder.config import load_settings


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        settings = load_settings()
        if settings.ebay_environment != "production":
            raise ValueError("Production keyset required")
        with EbayClient(settings) as client:
            response = client.get(
                "/developer/analytics/v1_beta/rate_limit/",
                headers={},
                params={"api_context": "buy", "api_name": "browse"},
            )
        print(json.dumps(summarize_browse_quota(response), sort_keys=True))
        return 0
    except Exception:
        # Public Actions logs must not contain provider payloads, keys or account identifiers.
        print('{"status":"unavailable"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
