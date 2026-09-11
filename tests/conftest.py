import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finder.config import Settings
from finder.domain import Monitor
from finder.persistence import SqlAlchemyListingRepository


@pytest.fixture
def search_payload():
    return json.loads((Path(__file__).parent / "fixtures/ebay_search.json").read_text())


@pytest.fixture
def detail_payload():
    return json.loads((Path(__file__).parent / "fixtures/ebay_detail.json").read_text())


@pytest.fixture
def observed_at():
    return datetime(2026, 9, 11, 12, tzinfo=UTC)


@pytest.fixture
def settings():
    return Settings(ebay_client_id="test-client", ebay_client_secret="test-secret")


@pytest.fixture
def monitor():
    return Monitor(
        id="test",
        name="Test",
        marketplace="ebay",
        query="vinyl (rap,hip-hop)",
        source_options={"fetch_details": False},
    )


@pytest.fixture
def repository(tmp_path):
    repo = SqlAlchemyListingRepository.from_url(f"sqlite:///{tmp_path / 'test.db'}")
    yield repo
    repo.close()
