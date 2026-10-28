"""Intercepted hosted cart transport; no browser connection or cart mutation."""

import io
import json
import os
import stat
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import observation_fingerprint
from finder.discogs_hosted_cart_adapter import (
    CART_SOCKET,
    HostedCartPrepareAdapter,
    visible_item_digest,
)
from finder.discogs_hosted_cartprep import CartInspectionAuthorization
from finder.discogs_rules import Observation


def example_detail(now):
    return VerifiedHostedDetail(
        observation=Observation(
            source="discogs_rendered_listing",
            release_id=123,
            listing_id="1002",
            observed_at=datetime.fromtimestamp(now, UTC).isoformat(),
            available=True,
            pressing_evidence="unknown",
            media="VG+",
            sleeve="VG+",
            seller_percent=99.5,
            feedback=10,
            item="5.00",
            currency=None,
            seller_comments="Clean copy, plays great",
            seller_comments_supported=True,
        ),
        requested_release=123,
        requested_listing="1002",
        route="/sell/item/1002",
        started_at=now,
        completed_at=now,
        reported_shipping_text="$4.95 shipping",
        cart_state="addable",
        seller_handle="Seller_1",
    )


def grant(now, detail=None):
    detail = detail or example_detail(now)
    return CartInspectionAuthorization(
        action="cart_quote_inspection",
        release_id=123,
        listing_id="1002",
        seller_handle="Seller_1",
        rules_revision=7,
        idempotency_key=str(uuid4()),
        observation_fingerprint=observation_fingerprint(detail.observation),
        issued_at=now - 1,
        expires_at=now + 120,
    )


def reply(authorization):
    return {
        "schema": "discogs_hosted_cart_prepare_v1",
        "status": "observed",
        "code": "cart_observed_after_add",
        "scope": authorization.model_dump(),
        "item_digest": visible_item_digest(example_detail(0)),
        "stop_required": True,
        "purchase_allowed": False,
        "order_submission": "disabled",
        "cart": {
            "status": "observed",
            "item_row_count": 1,
            "listing_id": "1002",
            "single_item_single_seller_summary_observed": True,
            "identity_provenance": "verified_pre_click_item_plus_post_add_cart_link",
            "unrelated_changes": False,
        },
    }


class InterceptedSocket:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.timeout = None
        self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def settimeout(self, seconds):
        self.timeout = seconds

    def connect(self, path):
        self.path = path
        if isinstance(self.response, Exception):
            raise self.response

    def sendall(self, data):
        self.requests.append(data)

    def makefile(self, _mode):
        return io.BytesIO(self.response)


def make_adapter(now, response, *, enabled=True):
    detail = example_detail(now[0])
    authorization = grant(now[0], detail)
    fake = InterceptedSocket(response)
    adapter = HostedCartPrepareAdapter(
        authorization,
        detail=detail,
        enabled=enabled,
        clock=lambda: now[0],
        socket_factory=lambda *_: fake,
    )
    adapter._private = lambda: True  # Transport tests only; ownership check tested separately.
    return adapter, fake, authorization


def wire(value):
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def test_default_unavailable_never_connects():
    now = [1000.0]
    adapter, socket, authorization = make_adapter(now, b"", enabled=False)
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"
    assert socket.requests == [] and socket.path is None
    assert authorization.action == "cart_quote_inspection"


def test_other_socket_path_cannot_be_selected(tmp_path):
    with pytest.raises(ValueError, match="dedicated fixed cart socket"):
        HostedCartPrepareAdapter(grant(1000.0), path=tmp_path / "other.sock", enabled=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("item", "6.00"),
        ("seller_comments", "Scratched copy"),
        ("media", "NM"),
        ("seller_percent", 98.0),
    ],
)
def test_changed_visible_item_cannot_use_old_candidate_authorization(field, value):
    detail = example_detail(1000.0)
    authorization = grant(1000.0, detail)
    changed = replace(detail, observation=detail.observation.model_copy(update={field: value}))
    with pytest.raises(ValueError, match="does not match"):
        HostedCartPrepareAdapter(authorization, detail=changed, enabled=True)


def test_detail_is_required_for_transport_even_if_enabled():
    adapter = HostedCartPrepareAdapter(grant(1000.0), enabled=True)
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"


def test_python_and_browser_visible_item_digests_agree():
    detail = example_detail(1000.0)
    observation = detail.observation
    rendered = {
        "source": observation.source,
        "release_id": observation.release_id,
        "listing_id": observation.listing_id,
        "seller_handle": detail.seller_handle,
        "media": observation.media,
        "sleeve": observation.sleeve,
        "seller_percent": observation.seller_percent,
        "feedback": observation.feedback,
        "item": observation.item,
        "currency": observation.currency,
        "seller_comments": observation.seller_comments,
        "seller_comments_supported": observation.seller_comments_supported,
        "available": observation.available,
        "cart_state": detail.cart_state,
        "reported_shipping_text": detail.reported_shipping_text,
    }
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "import {visibleItemDigest} from './scripts/discogs_hosted_cart_helper.mjs'; "
            "console.log(visibleItemDigest(JSON.parse(readFileSync(0,'utf8'))));",
        ],
        input=json.dumps(rendered),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == visible_item_digest(detail)


def test_fixed_scope_request_and_exact_single_row_response():
    now = [1000.0]
    authorization = grant(now[0])
    socket = InterceptedSocket(wire(reply(authorization)))
    adapter = HostedCartPrepareAdapter(
        authorization,
        detail=example_detail(now[0]),
        enabled=True,
        clock=lambda: now[0],
        socket_factory=lambda *_: socket,
    )
    adapter._private = lambda: True
    assert adapter.prepare(release_id=123, listing_id="1002") == "cart_observed"
    assert socket.timeout == 15
    sent = json.loads(socket.requests[0])
    assert sent == {
        "schema": "discogs_hosted_cart_prepare_v1",
        "action": "prepare_cart_quote_only",
        "scope": authorization.model_dump(),
        "item_digest": visible_item_digest(example_detail(now[0])),
        "purchase_allowed": False,
    }
    assert "order" not in sent and "url" not in sent


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(purchase_allowed=True),
        lambda data: data.update(order_submission="enabled"),
        lambda data: data.update(order_id="oops"),
        lambda data: data.update(item_digest="f" * 64),
        lambda data: data["scope"].update(listing_id="1003"),
        lambda data: data["cart"].update(item_row_count=2),
        lambda data: data["cart"].update(unrelated_changes=True),
        lambda data: data["cart"].update(listing_id="1003"),
        lambda data: data["cart"].update(single_item_single_seller_summary_observed=False),
        lambda data: data["cart"].update(identity_provenance="unverified"),
        lambda data: data.update(code="cart_already_contains_expected_listing"),
    ],
)
def test_unknown_or_changed_cart_evidence_cannot_report_success(change):
    now = [1000.0]
    authorization = grant(now[0])
    result = reply(authorization)
    change(result)
    fake = InterceptedSocket(wire(result))
    adapter = HostedCartPrepareAdapter(
        authorization,
        detail=example_detail(now[0]),
        enabled=True,
        clock=lambda: now[0],
        socket_factory=lambda *_: fake,
    )
    adapter._private = lambda: True
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"
    assert len(fake.requests) == 1


def test_wrong_request_identity_expiry_and_transport_failures_stop():
    now = [1000.0]
    adapter, fake, _ = make_adapter(now, b"")
    assert adapter.prepare(release_id=124, listing_id="1002") == "uncertain"
    assert adapter.prepare(release_id=123, listing_id="1003") == "uncertain"
    assert not fake.requests
    now[0] += 121
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"
    assert not fake.requests
    now = [1000.0]
    adapter, fake, _ = make_adapter(now, OSError("unavailable"))
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"
    assert not fake.requests


@pytest.mark.parametrize("body", [b"", b"{}", b"not-json\n", b"x" * 4098 + b"\n"])
def test_incomplete_or_malformed_response_is_uncertain(body):
    now = [1000.0]
    adapter, _, _ = make_adapter(now, body)
    assert adapter.prepare(release_id=123, listing_id="1002") == "uncertain"


def test_socket_requires_owner_only_directory_and_socket(monkeypatch, tmp_path):
    now = [1000.0]
    adapter, _, _ = make_adapter(now, b"")
    adapter.path = tmp_path / "prepare.sock"
    monkeypatch.setattr(adapter, "_private", HostedCartPrepareAdapter._private.__get__(adapter))
    original = type(adapter.path).lstat
    modes = {tmp_path: stat.S_IFDIR | 0o700, adapter.path: stat.S_IFSOCK | 0o600}

    def fake_lstat(path):
        if path in modes:
            return SimpleNamespace(st_mode=modes[path], st_uid=os.getuid())
        return original(path)

    monkeypatch.setattr(type(adapter.path), "lstat", fake_lstat)
    assert adapter._private() is True
    modes[tmp_path] = stat.S_IFDIR | 0o710
    assert adapter._private() is False
    modes[tmp_path] = stat.S_IFDIR | 0o700
    modes[adapter.path] = stat.S_IFSOCK | 0o660
    assert adapter._private() is False


def test_fixed_production_socket_requires_existing_browser_group(monkeypatch):
    now = [1000.0]
    adapter, _, _ = make_adapter(now, b"")
    monkeypatch.setattr(adapter, "_private", HostedCartPrepareAdapter._private.__get__(adapter))
    original = type(CART_SOCKET).lstat
    modes = {
        CART_SOCKET.parent: stat.S_IFDIR | 0o2710,
        CART_SOCKET: stat.S_IFSOCK | 0o660,
    }

    def fake_lstat(path):
        if path in modes:
            return SimpleNamespace(st_mode=modes[path], st_uid=10001, st_gid=113)
        return original(path)

    monkeypatch.setattr(type(CART_SOCKET), "lstat", fake_lstat)
    monkeypatch.setattr("finder.discogs_hosted_cart_adapter.os.getgid", lambda: 113)
    assert adapter._private() is True
    modes[CART_SOCKET] = stat.S_IFSOCK | 0o666
    assert adapter._private() is False
