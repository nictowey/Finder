"""Isolated item socket plus mocked public catalog; no Discogs requests."""

import socket
from datetime import UTC, datetime

import httpx
import pytest
from test_discogs_candidate_pipeline import hosted_reply, one_private_socket

from finder.discogs_candidate_detail import HostedListingCandidateReader
from finder.discogs_completeness import assess_media_completeness
from finder.discogs_listing import ListingReader, ListingReadError


@pytest.fixture(autouse=True)
def local_socket_only(monkeypatch):
    def connect(sock, address):
        assert sock.family == socket.AF_UNIX
        if error := sock.connect_ex(address):
            raise OSError(error, "Isolated Unix socket connect failed")

    monkeypatch.setattr(socket.socket, "connect", connect)


def reader_case(payload, *, status=200, enabled=True):
    now = [datetime(2026, 10, 26, tzinfo=UTC).timestamp()]
    calls = []

    def handle(request):
        calls.append(str(request.url))
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(status, json=payload)

    catalog = ListingReader(transport=httpx.MockTransport(handle), wall_clock=lambda: now[0])
    path, requests, thread = one_private_socket(
        lambda: hosted_reply(now, cart_state="addable", seller_comments="Clean copy.")
    )
    reader = HostedListingCandidateReader(
        path, clock=lambda: now[0], catalog=catalog if enabled else None
    )
    return reader, calls, requests, thread


def test_hosted_detail_attaches_only_exact_authoritative_catalog_quantity():
    reader, calls, requests, thread = reader_case(
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "1"}]}
    )
    detail = reader(123, "1002")
    thread.join(2)
    assert calls == ["https://api.discogs.com/releases/123"]
    assert requests == [{"action": "observe_listing", "release_id": "123", "listing_id": "1002"}]
    item = detail.observation
    assert item.release_media_metadata == {
        "id": 123,
        "formats": [{"name": "Vinyl", "qty": "1"}],
    }
    assert item.release_media_provenance["source"] == "discogs_release_api"
    result = assess_media_completeness(123, item.seller_comments, item.release_media_metadata)
    assert result["expected_media_count"] == 1
    assert result["status"] == "no_disclosed_media_omission"
    assert item.currency is None and item.pressing_evidence == "unknown"
    assert all(getattr(item, field) is None for field in ("shipping", "tax", "fees"))


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"id": 124, "formats": [{"name": "Vinyl", "qty": "1"}]}, 200),
        ({"id": 123, "formats": [{"name": "Vinyl"}]}, 200),
        ({"id": 123, "formats": [{"name": "CD", "qty": "1"}]}, 200),
        ({}, 403),
        ({}, 429),
    ],
)
def test_unknown_or_denied_catalog_never_infers_quantity(payload, status):
    reader, calls, _, thread = reader_case(payload, status=status)
    detail = reader(123, "1002")
    thread.join(2)
    assert calls == ["https://api.discogs.com/releases/123"]
    assert detail.observation.release_media_metadata is None
    assert detail.observation.release_media_provenance is None
    assert detail.observation.currency is None


def test_catalog_enrichment_is_explicit_and_does_not_add_a_marketplace_read():
    reader, calls, requests, thread = reader_case({}, enabled=False)
    detail = reader(123, "1002")
    thread.join(2)
    assert calls == [] and len(requests) == 1
    assert detail.observation.release_media_metadata is None


def test_unverified_item_stops_before_catalog():
    now = [datetime(2026, 10, 26, tzinfo=UTC).timestamp()]
    calls = []
    catalog = ListingReader(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        wall_clock=lambda: now[0],
    )
    path, _, thread = one_private_socket(lambda: hosted_reply(now, release=124))
    with pytest.raises(ListingReadError, match="unverified"):
        HostedListingCandidateReader(path, clock=lambda: now[0], catalog=catalog)(123, "1002")
    thread.join(2)
    assert calls == []


def test_arbitrary_catalog_callback_is_not_a_provenance_source():
    with pytest.raises(ValueError, match="fixed public release catalog"):
        HostedListingCandidateReader(catalog=lambda _: ({}, {}))
