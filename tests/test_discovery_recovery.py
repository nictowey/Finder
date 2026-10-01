"""Barcode discovery must recover from temporary provider failures."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from finder import discovery_worker as worker
from finder.adapters.discogs.adapter import AlternativeRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.client import EbayClient
from finder.discovery_store import progress
from finder.watch_store import SavedWatch, WatchStore, inbox, migrate, watches

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "failure",
    [
        "transport",
        "server",
        "invalid_json",
        "authentication",
        "timeout",
        "invalid_total",
        "invalid_page",
    ],
)
def test_barcode_only_listing_is_discovered_after_temporary_outage(
    repository, settings, discogs_release, detail_payload, monkeypatch, failure
):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=111), now=NOW)
    variant = normalize_release(discogs_release, NOW)
    monkeypatch.setattr(
        worker, "DiscogsClient", lambda _: nullcontext(SimpleNamespace(requests=1, retries=0))
    )
    monkeypatch.setattr(
        worker,
        "DiscogsCatalogProvider",
        lambda _: SimpleNamespace(
            get_release=lambda _: variant,
            search_alternatives=lambda _: AlternativeRetrieval([], False),
        ),
    )
    monkeypatch.setattr(worker, "watch_queries", lambda *args: ["gtin:0123456789012", "Album"])
    available = False
    barcode_reads = []
    details = []
    item = {
        **detail_payload,
        "itemId": "v1|900001|0",
        "itemOriginDate": (NOW - timedelta(hours=1)).isoformat(),
        "itemEndDate": None,
        "seller": {"userId": "synthetic-seller"},
    }

    def handle(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
        if "analytics" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiContext": "buy",
                            "apiName": "browse",
                            "resources": [
                                {
                                    "name": "buy.browse",
                                    "rates": [
                                        {
                                            "remaining": 5000,
                                            "limit": 5000,
                                            "timeWindow": 86400,
                                            "reset": (NOW + timedelta(days=1)).isoformat(),
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if "item_summary/search" in request.url.path:
            barcode = "gtin" in request.url.params
            if barcode:
                barcode_reads.append(available)
                if not available:
                    if failure == "transport":
                        raise httpx.ReadTimeout("synthetic outage", request=request)
                    if failure == "invalid_json":
                        return httpx.Response(200, content="synthetic invalid response")
                    if failure == "authentication":
                        return httpx.Response(403)
                    if failure == "timeout":
                        return httpx.Response(408)
                    if failure == "invalid_total":
                        return httpx.Response(200, json={"total": "1", "itemSummaries": [item]})
                    if failure == "invalid_page":
                        return httpx.Response(200, json={"total": 1, "itemSummaries": "invalid"})
                    return httpx.Response(503)
            items = [item] if barcode else []
            return httpx.Response(200, json={"total": len(items), "itemSummaries": items})
        details.append(request.url.path)
        return httpx.Response(200, json=item)

    monkeypatch.setattr(
        worker,
        "EbayClient",
        lambda s: EbayClient(
            s, transport=httpx.MockTransport(handle), max_retries=1, sleep=lambda _: None
        ),
    )
    claim = store.claim(now=NOW)
    initial = worker.run_chunk(repository, settings, None, claim, now_fn=lambda: NOW)
    assert initial["failed"] == 1
    assert barcode_reads and not any(barcode_reads)
    assert not details
    with repository.engine.connect() as conn:
        state = conn.execute(select(progress.c.data)).scalar_one()
        retry_at = conn.execute(select(watches.c.next_scan_at)).scalar_one()
    assert state["queries"][0]["incremental"]["status"] == "interrupted"
    assert state["queries"][0]["incremental"]["frontier"][0]["offset"] == 0
    assert state["queries"][0]["watermark"] is None
    assert datetime.fromisoformat(retry_at) == NOW + timedelta(minutes=30)
    available = True
    later = NOW + timedelta(hours=1)
    claim = store.claim(now=later)
    result = worker.run_chunk(repository, settings, None, claim, now_fn=lambda: later)
    assert any(barcode_reads), "A transient outage must not disable future barcode discovery"
    assert result["failed"] == 0
    assert len(details) == 1
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox.c.marketplace_item_id)).scalar_one() == item["itemId"]
        state = conn.execute(select(progress.c.data)).scalar_one()
    assert all(
        state["queries"][0][lane]["status"] == "search_exhausted"
        for lane in ("baseline", "incremental")
    )
