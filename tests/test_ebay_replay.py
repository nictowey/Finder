from copy import deepcopy

import pytest

from finder.adapters.ebay.replay import EbayReplayAdapter, sanitize_fixture
from finder.errors import ConfigurationError
from finder.service import run_scan


def _bundle(search_payload, detail_payload):
    return {
        "schema_version": 1,
        "captured_at": "2026-09-11T00:00:00Z",
        "search_responses": [deepcopy(search_payload)],
        "detail_responses": {detail_payload["itemId"]: deepcopy(detail_payload)},
    }


def test_sanitize_and_replay_ebay_bundle(
    repository, monitor, search_payload, detail_payload, observed_at
):
    raw = _bundle(search_payload, detail_payload)
    clean = sanitize_fixture(raw)
    serialized = str(clean)
    assert clean["sanitized"] is True
    assert "captured_at" not in clean
    assert "123456789012" not in serialized
    assert "fixture-records" not in serialized
    assert "ebay.com/itm" not in serialized
    summary = run_scan(monitor, EbayReplayAdapter(clean, observed_at=observed_at), repository)
    assert summary.status == "completed"
    assert summary.fetched == 2
    assert summary.new == 2
    stored = repository.get("ebay", "v1|fixture000001|0")
    assert stored.item_specifics["Release Year"] == ["2018"]
    assert stored.source_metadata["environment"] == "sanitized_fixture"


def test_replay_refuses_unsanitized_bundle(search_payload, detail_payload):
    with pytest.raises(ConfigurationError):
        EbayReplayAdapter(_bundle(search_payload, detail_payload))
