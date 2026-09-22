from datetime import timedelta

import httpx
import pytest

from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.errors import PersistenceError
from finder.service import run_scan


def test_mocked_scan_twice_updates_without_duplicates(
    settings,
    monitor,
    repository,
    search_payload,
    observed_at,
):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        assert request.url.path == "/buy/browse/v1/item_summary/search"
        assert request.url.params["q"] == monitor.query
        assert "AUCTION" in request.url.params["filter"]
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        return httpx.Response(200, json=search_payload)

    search_payload["itemSummaries"].append({"title": "invalid: no ID"})
    search_payload["total"] = 3
    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        first = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
        search_payload["itemSummaries"][0]["price"]["value"] = "20.00"
        second = run_scan(
            monitor, EbayAdapter(client, now=lambda: observed_at + timedelta(hours=1)), repository
        )
    assert (first.fetched, first.new, first.updated, first.skipped_invalid, first.total_stored) == (
        3,
        2,
        0,
        1,
        2,
    )
    assert (
        second.fetched,
        second.new,
        second.updated,
        second.skipped_invalid,
        second.total_stored,
    ) == (3, 0, 2, 1, 2)
    assert first.status == second.status == "completed"
    saved = repository.get("ebay", "v1|123456789012|0")
    assert saved.first_observed_at == observed_at
    assert saved.last_observed_at == observed_at + timedelta(hours=1)


def test_details_and_context(
    settings, monitor, repository, search_payload, detail_payload, observed_at
):
    settings = settings.model_copy(
        update={"delivery_country": "US", "delivery_postal_code": "03106"}
    )
    monitor = monitor.model_copy(update={"source_options": {"fetch_details": True}})

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        assert (
            request.headers["X-EBAY-C-ENDUSERCTX"]
            == "contextualLocation=country%3DUS%2Czip%3D03106"
        )
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=search_payload)
        if "123456789012" in request.url.path:
            return httpx.Response(200, json=detail_payload)
        return httpx.Response(404)

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        summary = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
    assert summary.new == 1
    assert summary.skip_reasons == {"item_unavailable": 1}
    listing = repository.get("ebay", detail_payload["itemId"])
    assert listing.item_specifics["Release Year"] == ["2018"]
    assert listing.categories[0]["id"] == "176985"
    assert listing.details_observed_at == observed_at


@pytest.mark.parametrize("include_user_id", [True, False])
def test_production_requires_deletable_seller_id(
    settings, monitor, repository, search_payload, detail_payload, observed_at, include_user_id
):
    settings = settings.model_copy(update={"ebay_environment": "production"})
    monitor = monitor.model_copy(update={"source_options": {"fetch_details": True}})
    search_payload["itemSummaries"] = search_payload["itemSummaries"][:1]
    search_payload["total"] = 1
    detail_payload["seller"] = {"username": "fixture-records"}
    if include_user_id:
        detail_payload["seller"]["userId"] = "fixture-seller-id"

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=search_payload)
        assert request.url.params["fieldgroups"] == "ADDITIONAL_SELLER_DETAILS"
        return httpx.Response(200, json=detail_payload)

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        summary = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
    if include_user_id:
        assert summary.new == 1
        assert repository.get("ebay", detail_payload["itemId"]).seller_id == "fixture-seller-id"
    else:
        assert summary.new == 0
        assert summary.skip_reasons == {"missing_seller_id": 1}
        assert repository.count() == 0


def test_pagination_duplicate_cap_and_safe_urls(
    settings, monitor, repository, search_payload, observed_at
):
    item = search_payload["itemSummaries"][0]
    offsets = []
    monitor = monitor.model_copy(
        update={"source_options": {"fetch_details": False, "page_size": 1, "max_pages": 2}}
    )

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        assert request.url.host == "api.sandbox.ebay.com"
        offsets.append(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "total": 8,
                "itemSummaries": [item],
                "next": "https://untrusted.example/steal-token",
            },
        )

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        summary = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
    assert offsets == ["0", "1"]
    assert summary.limit_reached
    assert summary.skip_reasons == {"duplicate_in_scan": 1}
    assert summary.fetched == 2 and summary.new == 1


def test_later_page_failure_preserves_commits(
    settings, monitor, repository, search_payload, observed_at
):
    monitor = monitor.model_copy(
        update={"source_options": {"page_size": 1, "fetch_details": False}}
    )

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.params["offset"] == "0":
            return httpx.Response(
                200, json={"total": 2, "itemSummaries": [search_payload["itemSummaries"][0]]}
            )
        return httpx.Response(503)

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        summary = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
    assert summary.status == "failed"
    assert summary.new == summary.total_stored == 1
    assert summary.error


@pytest.mark.parametrize("status,expected", [(500, "completed"), (429, "failed"), (403, "failed")])
def test_detail_failure_policy(
    settings, monitor, repository, search_payload, observed_at, status, expected
):
    monitor = monitor.model_copy(update={"source_options": {"fetch_details": True}})

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=search_payload)
        return httpx.Response(status)

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        summary = run_scan(monitor, EbayAdapter(client, now=lambda: observed_at), repository)
    assert summary.status == expected
    assert summary.fetched == 2
    if expected == "completed":
        assert summary.partial_details == 2
        assert summary.total_stored == 2
    else:
        assert summary.unprocessed == 2


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"total": 0}, "completed"),
        ({"total": 1}, "failed"),
        ({"total": 1, "itemSummaries": []}, "failed"),
        ({"total": "2"}, "failed"),
        ({}, "failed"),
    ],
)
def test_empty_and_malformed_pages(settings, monitor, repository, payload, expected):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                {"access_token": "token", "expires_in": 7200}
                if request.method == "POST"
                else payload
            ),
        )

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        summary = run_scan(monitor, EbayAdapter(client), repository)
    assert summary.status == expected
    assert summary.total_stored == 0


def test_storage_failure_is_reported(settings, monitor, search_payload, observed_at):
    class BrokenRepository:
        def upsert(self, listing):
            raise PersistenceError("Database write failed")

        def count(self):
            return 0

    def handler(request):
        return httpx.Response(
            200,
            json=(
                {"access_token": "token", "expires_in": 7200}
                if request.method == "POST"
                else search_payload
            ),
        )

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        summary = run_scan(
            monitor, EbayAdapter(client, now=lambda: observed_at), BrokenRepository()
        )
    assert summary.status == "failed"
    assert summary.new == 0 and summary.unprocessed == 2
