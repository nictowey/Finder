"""Cheat sheets, two-price alerts, auction timing, barcode searches and sibling profiles."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.discovery import search_page
from finder.adapters.ebay.normalize import normalize_listing
from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.categories.vinyl_clues import Clue, apply_cheat_sheet, suggest_clues
from finder.config import DiscogsSettings
from finder.discovery_worker import poll_minutes, watch_queries
from finder.errors import CatalogRequestError
from finder.watch_profile import load_profile, siblings_of
from finder.watch_store import SavedWatch, WatchStore, migrate, profiles
from finder.watch_worker import assess_review, refresh_hours

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def release(discogs_release, **changes):
    raw = {**discogs_release, **changes}
    return normalize_release(raw, NOW)


def vinyl(text, descriptions=("LP", "Album")):
    return [{"name": "Vinyl", "qty": "1", "descriptions": list(descriptions), "text": text}]


@pytest.fixture
def target(discogs_release):
    return release(
        discogs_release,
        formats=vinyl("Pink/Green", ("LP", "Album", "Limited Edition", "Numbered")),
    )


@pytest.fixture
def siblings(discogs_release):
    return [
        release(discogs_release, id=222, formats=vinyl("Black", ("LP", "Album", "Reissue"))),
        release(
            discogs_release,
            id=333,
            formats=vinyl("", ("LP", "Album", "Reissue", "180 Gram")),
            labels=[{"name": "Music On Vinyl", "catno": "MOVLP123"}],
        ),
    ]


def listing(search_payload, **changes):
    base = normalize_listing(search_payload["itemSummaries"][0], NOW).model_copy(
        update={
            "seller_id": "synthetic-seller",
            "details_observed_at": NOW,
            "quality_flags": [],
            "price_kind": "fixed_price",
            "listing_ends_at": None,
            "current_price": Decimal("40"),
            "shipping_cost": Decimal("5"),
            "currency": "USD",
            "shipping_currency": "USD",
            "title": "Example Artist Example Album LP",
            "item_specifics": {},
            "source_metadata": {"delivery_country": "US", "delivery_postal_code": "12345"},
        }
    )
    return base.model_copy(update=changes)


def test_suggestions_separate_the_target_from_its_siblings(target, siblings):
    proposals = suggest_clues(target, siblings, partial=True)
    tells = {(row["kind"], row["value"]): row for row in proposals["tells"]}
    assert tells[("color", "Pink/Green")]["required"]
    assert tells[("numbered", "numbered")]["required"]
    # The catalog number and label are shared with a sibling, so they prove nothing.
    assert ("catalog_number", "EX-101") not in tells
    anti = {(row["kind"], row["value"]): row["siblings_sharing"] for row in proposals["anti_tells"]}
    assert anti[("keyword", "reissue")] == 2
    assert anti[("keyword", "180 gram")] == 1
    assert anti[("label", "Music On Vinyl")] == 1
    assert proposals["partial"] and proposals["siblings_compared"] == 2


def test_cheat_sheet_promotes_required_signs_and_hides_common_versions(search_payload, target):
    tells = [
        Clue(kind="color", value="Pink/Green", required=True),
        Clue(kind="numbered", value="x"),
    ]
    anti = [Clue(kind="keyword", value="reissue"), Clue(kind="keyword", value="180 gram")]
    review = {"status": "family_review", "clues": ["artist_and_album"], "verify": []}

    def check(title, specifics=None):
        row = listing(search_payload, title=title, item_specifics=specifics or {})
        return apply_cheat_sheet(review, row, target, tells, anti)

    found = check("Example Artist Example Album pink green vinyl numbered")
    assert found["status"] == "possible_pressing"
    assert found["signs"] == ["color: Pink/Green", "numbered"]
    missing = check("Example Artist Example Album LP")
    assert missing["status"] == "family_review"
    assert missing["missing_signs"] == ["color: Pink/Green"]
    assert check("Example Artist Example Album 180g pink green")["status"] == "conflicting"
    assert check("Example Artist Example Album Reissue")["common_signs"] == ["keyword: reissue"]
    # Negated claims and sleeve colors in the title are not common-version signs.
    assert check("Example Artist Example Album pink green NOT a reissue")["status"] == (
        "possible_pressing"
    )
    black = [Clue(kind="color", value="Black")]
    row = listing(search_payload, title="Example Artist Example Album pink green black sleeve")
    assert apply_cheat_sheet(review, row, target, tells, black)["status"] == "possible_pressing"
    row = listing(search_payload, item_specifics={"Color": ["Black"]})
    assert apply_cheat_sheet(review, row, target, tells, black)["status"] == "conflicting"
    # Album words that happen to be colors never count as a color claim.
    purple = target.model_copy(update={"title": "Purple Rain"})
    row = listing(search_payload, title="Example Artist Purple Rain LP")
    rain = [Clue(kind="color", value="Purple", required=True)]
    assert apply_cheat_sheet(review, row, purple, rain, [])["status"] == "family_review"


def test_unclear_listings_alert_only_under_the_gamble_price(search_payload, target):
    watch = SavedWatch(
        release_id=111,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="12345",
        tells=[Clue(kind="color", value="Pink/Green", required=True)],
    )
    unclear = listing(search_payload)
    review = assess_review(
        watch, unclear, target, now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "family_review" and review["notify"]
    assert review["alert_budget"] == "within_ceiling"
    pricey = listing(search_payload, current_price=Decimal("60"))
    review = assess_review(watch, pricey, target, now=NOW, alternatives=[], search_incomplete=False)
    assert review["budget"] == "within_ceiling" and not review["notify"]
    likely = listing(
        search_payload, title="Example Artist Example Album pink green", current_price=Decimal("90")
    )
    review = assess_review(watch, likely, target, now=NOW, alternatives=[], search_incomplete=False)
    assert review["status"] == "possible_pressing" and review["notify"]
    no_gamble = watch.model_copy(update={"gamble_max": None})
    assert not assess_review(
        no_gamble, unclear, target, now=NOW, alternatives=[], search_incomplete=False
    )["notify"]
    strict = watch.model_copy(update={"alert_mode": "strict"})
    assert not assess_review(
        strict, unclear, target, now=NOW, alternatives=[], search_incomplete=False
    )["notify"]


def test_auctions_alert_near_their_end_and_are_rechecked_then(search_payload, target):
    watch = SavedWatch(
        release_id=111,
        maximum_subtotal=Decimal("100"),
        country="US",
        postal_code="12345",
        tells=[Clue(kind="color", value="Pink/Green", required=True)],
    )
    auction = listing(
        search_payload,
        title="Example Artist Example Album pink green",
        price_kind="current_bid",
        listing_ends_at=NOW + timedelta(hours=10),
    )
    early = assess_review(watch, auction, target, now=NOW, alternatives=[], search_incomplete=False)
    assert not early["notify"] and "auction_not_ending_soon" in early["verify"]
    assert refresh_hours(watch, auction, early, NOW) == 6
    # An unclear auction is otherwise read daily; here it is re-read as the window opens.
    unclear = {"status": "family_review"}
    assert refresh_hours(watch, auction, unclear, NOW) == pytest.approx(8.02)
    late = NOW + timedelta(hours=9)
    auction = auction.model_copy(update={"details_observed_at": late, "last_observed_at": late})
    ending = assess_review(
        watch, auction, target, now=late, alternatives=[], search_incomplete=False
    )
    assert ending["notify"] and "auction_current_bid_only" in ending["verify"]
    over = auction.model_copy(update={"current_price": Decimal("99")})
    assert not assess_review(
        watch, over, target, now=late, alternatives=[], search_incomplete=False
    )["notify"]
    off = watch.model_copy(update={"auction_alert_minutes": 0})
    assert not assess_review(
        off, auction, target, now=late, alternatives=[], search_incomplete=False
    )["notify"]
    fixed = listing(search_payload)
    assert refresh_hours(watch, fixed, {"status": "unrelated"}, NOW) == 168


def test_queries_add_owner_searches_and_one_barcode_within_the_cap(target):
    watch = SavedWatch(release_id=111, extra_queries=["Exampel Artist Album", "Example Album"])
    queries = watch_queries(["Example Artist Example Album", "example album"], target, watch)
    assert queries == [
        "Example Artist Example Album",
        "example album",
        "Exampel Artist Album",
        "gtin:0123456789012",
    ]
    many = watch_queries([f"q{i}" for i in range(6)], target, watch)
    assert len(many) == 6 and "gtin:0123456789012" not in many
    with pytest.raises(ValueError):
        SavedWatch(release_id=111, extra_queries=["   "])


def test_barcode_query_is_sent_as_gtin(settings):
    client = SimpleNamespace(settings=settings, get=Mock(return_value={"total": 0}))
    target = EbaySearchTarget(id="t", catalog_variant_id=1, queries=["Album", "gtin:0123456789012"])
    stamp = "2026-09-23T12:00:00.000Z"
    search_page(client, target, 1, lower="1990-01-01T00:00:00.000Z", upper=stamp, offset=0)
    params = client.get.call_args.kwargs["params"]
    assert params["gtin"] == "0123456789012" and "q" not in params
    bad = EbaySearchTarget(id="t", catalog_variant_id=1, queries=["gtin:12ab"])
    with pytest.raises(ValueError):
        search_page(client, bad, 0, lower="1990-01-01T00:00:00.000Z", upper=stamp, offset=0)


def test_discogs_versions_are_paged_filtered_and_allow_listed(discogs_release):
    pages = {
        1: {
            "pagination": {"pages": 2},
            "versions": [
                {"id": 111, "major_formats": ["Vinyl"]},
                {"id": 222, "format": "Vinyl, LP, Reissue"},
                {"id": 444, "major_formats": ["CD"], "format": "CD, Album"},
            ],
        },
        2: {"pagination": {"pages": 2}, "versions": [{"id": 555, "major_formats": ["Vinyl"]}]},
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=pages[int(request.url.params["page"])])

    settings = DiscogsSettings(token="synthetic", user_agent="Finder-test/1.0")
    client = DiscogsClient(settings, transport=httpx.MockTransport(handler))
    provider = DiscogsCatalogProvider(client)
    assert provider.vinyl_versions(99) == ([111, 222, 555], False)
    assert provider.vinyl_versions(99, max_pages=1) == ([111, 222], True)
    assert set(seen) == {"/masters/99/versions"}
    with pytest.raises(CatalogRequestError):
        client.get("/marketplace/stats/111")
    with pytest.raises(CatalogRequestError):
        client.get("/masters/99")


def test_profile_is_cached_weekly_and_survives_catalog_failures(repository, target, siblings):
    migrate(repository.engine)
    watch_id = WatchStore(repository.engine).add(SavedWatch(release_id=111))
    calls = []
    provider = SimpleNamespace(
        vinyl_versions=lambda master: calls.append(master) or ([111, 222, 333], False),
        get_releases=lambda ids: [s for s in siblings if int(s.catalog_variant_id) in ids],
    )
    first = load_profile(repository.engine, watch_id, provider, target, NOW)
    assert calls == [99] and first["vinyl_versions"] == 3 and not first["partial"]
    assert [v.catalog_variant_id for v in siblings_of(first)] == ["222", "333"]
    assert load_profile(repository.engine, watch_id, provider, target, NOW + timedelta(days=1))
    assert calls == [99]
    broken = SimpleNamespace(
        vinyl_versions=Mock(side_effect=RuntimeError("down")), get_releases=lambda ids: []
    )
    later = NOW + timedelta(days=8)
    assert load_profile(repository.engine, watch_id, broken, target, later) == first
    no_master = target.model_copy(update={"catalog_product_id": target.catalog_variant_id})
    alone = load_profile(repository.engine, watch_id, broken, no_master, later)
    assert alone["vinyl_versions"] == 1 and alone["siblings"] == []
    with repository.engine.connect() as conn:
        assert len(conn.execute(select(profiles)).all()) == 1


def test_poll_cadence_stretches_with_enabled_queries(repository):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    assert poll_minutes(repository.engine) == 10
    for release_id in range(1, 21):
        store.add(SavedWatch(release_id=release_id))
    # Twenty watches at an assumed three searches each: 1440 * 60 / 2000.
    assert poll_minutes(repository.engine) == 44


def test_additional_photos_are_kept_without_invalid_entries(search_payload):
    raw = {
        **search_payload["itemSummaries"][0],
        "additionalImages": [
            {"imageUrl": "https://i.ebayimg.com/images/g/a/s-l1600.jpg"},
            {"height": 1},
            "not-an-object",
        ],
    }
    assert normalize_listing(raw, NOW).additional_images == [
        "https://i.ebayimg.com/images/g/a/s-l1600.jpg"
    ]


def test_dashboard_and_worker_share_the_watch_limit():
    from pathlib import Path

    from finder.watch_store import MAX_WATCHES

    source = (Path(__file__).parents[1] / "functions/watchlist.ts").read_text()
    assert f"export const MAX_WATCHES = {MAX_WATCHES};" in source


def test_settings_edits_resort_known_listings_without_new_searches_or_reads(
    repository, settings, discogs_release, search_payload, monkeypatch
):
    from contextlib import nullcontext
    from urllib.parse import unquote

    from sqlalchemy import update

    from finder import discovery_worker as worker
    from finder.adapters.discogs.adapter import AlternativeRetrieval
    from finder.adapters.ebay.client import EbayClient
    from finder.discovery_store import SETTINGS_CHANGED, progress, work
    from finder.watch_store import inbox, watches

    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=111, country="US", postal_code="12345"), now=NOW)
    variant = normalize_release(discogs_release, NOW)
    provider = SimpleNamespace(
        get_release=lambda _: variant,
        search_alternatives=lambda _: AlternativeRetrieval(variants=[], search_incomplete=False),
    )
    monkeypatch.setattr(
        worker, "DiscogsClient", lambda _: nullcontext(SimpleNamespace(requests=1, retries=0))
    )
    monkeypatch.setattr(worker, "DiscogsCatalogProvider", lambda _: provider)
    items = [
        {"itemId": f"v1|{900 + i}|0", "itemOriginDate": "2026-09-23T12:00:00.000Z", "title": "x"}
        for i in range(3)
    ]
    calls = {"search": 0, "detail": 0}

    def handle(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "analytics" in request.url.path:
            reset = (NOW + timedelta(days=1)).isoformat()
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
                                            "reset": reset,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if "item_summary/search" in request.url.path:
            calls["search"] += 1
            return httpx.Response(200, json={"total": len(items), "itemSummaries": items})
        calls["detail"] += 1
        item_id = unquote(request.url.path.split("/item/")[1])
        return httpx.Response(
            200,
            json={
                **search_payload["itemSummaries"][0],
                "itemId": item_id,
                "title": "Example Artist Example Album LP",
                "itemEndDate": None,
                "seller": {"userId": "synthetic-seller"},
            },
        )

    monkeypatch.setattr(
        worker, "EbayClient", lambda s: EbayClient(s, transport=httpx.MockTransport(handle))
    )
    clock = [NOW]
    for _ in range(8):
        claim = store.claim(now=clock[0])
        if claim is None:
            break
        worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
        clock[0] += timedelta(minutes=2)
    assert calls["detail"] == 3
    with repository.engine.connect() as conn:
        before = conn.execute(select(progress.c.data)).scalar()
        watch_id, config, revision = conn.execute(
            select(watches.c.id, watches.c.config, watches.c.revision)
        ).one()
    assert all(q["baseline"]["status"] == "search_exhausted" for q in before["queries"])
    # The owner adds a required sign, as the dashboard's PUT does: new revision, due now.
    config = {**config, "tells": [{"kind": "color", "value": "Blue", "required": True}]}
    with repository.engine.begin() as conn:
        conn.execute(
            update(watches)
            .where(watches.c.id == watch_id)
            .values(config=config, revision=revision + 1, next_scan_at=clock[0].isoformat())
        )
    searches, details = calls["search"], calls["detail"]
    claim = store.claim(now=clock[0])
    worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
    with repository.engine.connect() as conn:
        after = conn.execute(select(progress.c.data)).scalar()
        rows = conn.execute(select(inbox.c.data)).scalars().all()
        reasons = conn.execute(select(work.c.reason)).scalars().all()
    assert calls["detail"] == details and calls["search"] == searches
    assert after["anchor"] == before["anchor"] and after["revision"] == revision + 1
    assert SETTINGS_CHANGED not in reasons
    assert {row["watch_revision"] for row in rows} == {revision + 1}
    assert all("required_sign_not_claimed" in row["verify"] for row in rows)


@pytest.mark.parametrize("barcode", [True, False])
def test_a_failing_barcode_search_is_switched_off_without_failing_the_watch(
    repository, settings, discogs_release, monkeypatch, barcode
):
    from contextlib import nullcontext

    from finder import discovery_worker as worker
    from finder.adapters.discogs.adapter import AlternativeRetrieval
    from finder.adapters.ebay.client import EbayClient
    from finder.discovery_store import progress

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
    monkeypatch.setattr(worker, "watch_queries", lambda q, v, w: ["gtin:0123456789012", "Album"])
    reset = (NOW + timedelta(days=1)).isoformat()
    quota = {
        "rateLimits": [
            {
                "apiContext": "buy",
                "apiName": "browse",
                "resources": [
                    {
                        "name": "buy.browse",
                        "rates": [
                            {"remaining": 5000, "limit": 5000, "timeWindow": 86400, "reset": reset}
                        ],
                    }
                ],
            }
        ]
    }
    # An item without a listing date cannot be placed in a window: a malformed response.
    outside = {"itemId": "v1|1|0"}

    def handle(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "analytics" in request.url.path:
            return httpx.Response(200, json=quota)
        failing = ("gtin" in request.url.params) == barcode
        items = [outside] if failing else []
        return httpx.Response(200, json={"total": len(items), "itemSummaries": items})

    monkeypatch.setattr(
        worker, "EbayClient", lambda s: EbayClient(s, transport=httpx.MockTransport(handle))
    )
    claim = store.claim(now=NOW)
    result = worker.run_chunk(repository, settings, None, claim, now_fn=lambda: NOW)
    kind = "barcode" if barcode else "keywords"
    assert result[f"search_failed_{kind}_SearchError_missing_start_date"] == 1
    with repository.engine.connect() as conn:
        state = conn.execute(select(progress.c.data)).scalar()
    if barcode:
        assert not result.get("failed")
        lanes = [state["queries"][0][lane] for lane in ("baseline", "incremental")]
        assert all(lane["reason"] == "barcode_search_unavailable" for lane in lanes)
        assert state["queries"][1]["baseline"]["status"] != "partial_provider_limit"
    else:
        assert result["failed"] == 1
