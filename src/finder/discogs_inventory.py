"""Explicit, cohort-limited official seller inventory observations. Never sends or buys."""

import argparse
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from finder.discogs_listing import ListingReadError, ReadBudgetBusy, listing_id, normalize
from finder.discogs_listing_preview import evaluate_preview
from finder.discogs_read_guard import ReadSafetyHalt, guard_row
from finder.discogs_review_backend import (
    DeadlineTransport,
    PublicPreviewReader,
    ReviewReadBudget,
)
from finder.discogs_rules import CURRENCIES, GRADES, StrictInput, amount, release_id

PER_PAGE = 50
MAX_SEEN = 10000
STATE_TABLE = "discogs_seeded_inventory_state"
RESULT_TTL = 6 * 3600


class InventoryTarget(StrictInput):
    release_id: int
    currency: str
    under: str
    min_media: str
    min_sleeve: str

    _release = field_validator("release_id", mode="before")(release_id)
    _amount = field_validator("under", mode="before")(amount)

    @model_validator(mode="after")
    def criteria(self):
        if self.currency not in CURRENCIES or Decimal(self.under) <= 0:
            raise ValueError("Use an explicit supported currency and positive item-price limit.")
        if self.min_media not in GRADES or self.min_sleeve not in GRADES:
            raise ValueError("Use explicit supported media and sleeve grades.")
        return self


class InventorySeed(StrictInput):
    source: Literal["owner_supplied_known_listing"]
    seller: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")
    listing_id: int
    release_id: int

    _listing = field_validator("listing_id", mode="before")(listing_id)
    _release = field_validator("release_id", mode="before")(release_id)


class InventoryConfig(StrictInput):
    version: Literal[1]
    seed: InventorySeed
    targets: list[InventoryTarget] = Field(min_length=1, max_length=1)
    max_pages: int = Field(default=1, ge=1, le=3)
    poll_seconds: int = Field(default=600, ge=120, le=86400)
    max_detail_reads: int = Field(default=2, ge=1, le=2)

    @model_validator(mode="after")
    def bounded_cohort(self):
        releases = [target.release_id for target in self.targets]
        if len(set(releases)) != len(releases) or self.seed.release_id not in releases:
            raise ValueError("Use unique exact releases including the seed listing's release.")
        if len({target.currency for target in self.targets}) != 1:
            raise ValueError("A bounded inventory poll uses one explicit requested currency.")
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def read_json(path, limit):
    with Path(path).open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds the bounded size limit.")
    return json.loads(data, parse_float=Decimal)


def verify_seed(payload, seed):
    """Require the explicitly supplied listing to report the configured seller/release."""
    listing = normalize(payload, seed.listing_id)
    seller = payload.get("seller")
    username = seller.get("username") if isinstance(seller, dict) else None
    if (
        listing["release_id"] != seed.release_id
        or not isinstance(username, str)
        or username.casefold() != seed.seller.casefold()
    ):
        raise ListingReadError("Known listing does not verify the supplied seller and release.")


def parse_page(payload, page, seller):
    if not isinstance(payload, dict) or not isinstance(payload.get("pagination"), dict):
        raise ListingReadError("Inventory pagination is missing.")
    pagination = payload["pagination"]
    values = [pagination.get(key) for key in ("page", "pages", "per_page", "items")]
    if any(type(value) is not int for value in values):
        raise ListingReadError("Inventory pagination is invalid.")
    current, pages, per_page, items = values
    # Discogs may represent an empty inventory as either zero or one pages.
    expected_pages = (items + PER_PAGE - 1) // PER_PAGE
    if (
        current != page
        or per_page != PER_PAGE
        or not 0 <= items <= 10000000
        or pages not in ({0, 1} if items == 0 else {expected_pages})
        or page > max(1, pages)
    ):
        raise ListingReadError("Inventory pagination is inconsistent.")
    rows = payload.get("listings")
    if not isinstance(rows, list) or len(rows) != min(
        PER_PAGE, max(0, items - (page - 1) * PER_PAGE)
    ):
        raise ListingReadError("Inventory page length is incomplete or inconsistent.")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ListingReadError("Inventory listing identity is missing.")
        ident = listing_id(row["id"])
        normalize(row, ident)  # Invalid release IDs never become an empty successful page.
        owner = row.get("seller")
        if isinstance(owner, dict) and "username" in owner:
            username = owner["username"]
            if not isinstance(username, str) or username.casefold() != seller.casefold():
                raise ListingReadError("Inventory listing reports a different seller.")
        if ident in ids:
            raise ListingReadError("Inventory contains repeated listing identities.")
        ids.add(ident)
    # Never follow provider-supplied pagination/resource URLs.
    return rows, max(1, pages), items


def candidate(row, target):
    """Reuse known-listing normalization, conservative prices and comment defect checks."""
    result = evaluate_preview(
        row, row["id"], target.release_id, under=target.under, currency=target.currency
    )
    checks = result["checks"]
    checks.pop("media_at_least_nm")
    checks.pop("sleeve_at_least_nm")
    for name in ("media", "sleeve"):
        grade = result[name]
        checks[name + "_at_least_minimum"] = grade in GRADES and GRADES.index(
            grade
        ) >= GRADES.index(getattr(target, "min_" + name))
    result["item_criteria_match"] = all(checks.values())
    result["kind"] = "seeded_inventory_review_candidate"
    result["warnings"][-1] = (
        "No notification, saved aggregate watch, cart, order or payment changed."
    )
    result["detail_recheck_required"] = True
    result["freshness"] = "inventory_observation_only"
    # Only a fresh standalone listing check can qualify a downstream detail candidate.
    result["eligible_for_notification"] = False
    return result


class InventoryReader(PublicPreviewReader):
    def seed_payload(self, config, *, budget):
        return self._request(
            f"/marketplace/listings/{config.seed.listing_id}", budget=budget, wait=True
        )

    def detail_payload(self, ident, currency, *, budget):
        return self._request(
            f"/marketplace/listings/{listing_id(ident)}",
            params={"curr_abbr": currency},
            budget=budget,
            wait=True,
        )

    def inventory_page(self, config, page, *, budget):
        if type(page) is not int or not 1 <= page <= config.max_pages:
            raise ValueError("Page exceeds the explicit inventory request bound.")
        return self._request(
            f"/users/{config.seed.seller}/inventory",
            params={
                "sort": "listed",
                "sort_order": "desc",
                "status": "For Sale",
                "per_page": PER_PAGE,
                "page": page,
            },
            budget=budget,
            wait=True,
        )


def collect(config, seed_payload, fetch_page):
    """Bounded observation, with explicit pagination rather than full-market claims."""
    verify_seed(seed_payload, config.seed)
    return scan_inventory(config, fetch_page)


def scan_inventory(config, fetch_page):
    seen, rows = set(), []
    expected = None
    complete = False
    reason = "page_cap"
    page = 0
    for page in range(1, config.max_pages + 1):
        values, pages, items = parse_page(fetch_page(page), page, config.seed.seller)
        if expected is None:
            expected = (pages, items)
        elif expected != (pages, items):
            reason = "pagination_changed"
            break
        ids = {value["id"] for value in values}
        if seen & ids:
            reason = "pagination_shifted"
            break
        seen.update(ids)
        rows.extend(values)
        if page == pages:
            complete, reason = len(seen) == items, "reported_pages_observed"
            break
    targets = {target.release_id: target for target in config.targets}
    candidates = [
        candidate(row, targets[row["release"]["id"]])
        for row in rows
        if row["release"]["id"] in targets
    ]
    return {
        "coverage": "explicit_seed_seller_only",
        "whole_market": False,
        "pagination": {
            "complete": complete,
            "reason": reason,
            "pages_fetched": page,
            "reported_pages": expected[0],
            "reported_items": expected[1],
            "observed_unique_listings": len(seen),
            "atomic_snapshot": False,
        },
        "candidates": candidates,
        "observed_ids": sorted(seen),
        "purchase_allowed": False,
        "notifications_sent": 0,
        "attribution": "Data provided by Discogs",
    }


def _connect(database):
    path = Path(database).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
    db.execute("PRAGMA synchronous=FULL")
    if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
        db.close()
        raise ReadSafetyHalt("inventory_wrong_workspace")
    try:
        guard_row(db)  # No initialization or guard recovery from this adapter.
    except BaseException:
        db.close()
        raise
    return db


def _control(db):
    rows = db.execute("SELECT enabled,revision FROM discogs_price_control").fetchall()
    if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int:
        raise ReadSafetyHalt("inventory_aggregate_control_stopped")
    return tuple(rows[0])


def _state(db, config):
    rows = db.execute(f"SELECT config_hash,data FROM {STATE_TABLE} WHERE id=1").fetchall()
    if len(rows) != 1 or rows[0][0] != config.fingerprint:
        raise ReadSafetyHalt("inventory_state_missing_or_configuration_changed")
    value = json.loads(rows[0][1])
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "seen",
            "baseline",
            "next_poll",
            "denied",
            "in_progress",
            "seed_verified_at",
            "pending",
            "results",
        }
        or any(type(value[key]) is not bool for key in ("baseline", "denied", "in_progress"))
        or type(value["next_poll"]) not in (int, float)
        or not 0 <= value["next_poll"] < 1e12
        or not isinstance(value["seen"], list)
        or len(value["seen"]) > MAX_SEEN
        or any(
            type(ident) is not int or not 0 < ident <= 9007199254740991 for ident in value["seen"]
        )
        or len(set(value["seen"])) != len(value["seen"])
    ):
        raise ReadSafetyHalt("inventory_state_invalid")
    verified = value["seed_verified_at"]
    if verified is not None and (type(verified) not in (int, float) or not 0 <= verified < 1e12):
        raise ReadSafetyHalt("inventory_seed_evidence_invalid")
    references = []
    targets = {target.release_id for target in config.targets}
    for key in ("pending", "results"):
        if not isinstance(value[key], list) or len(value[key]) > MAX_SEEN:
            raise ReadSafetyHalt("inventory_queue_invalid")
        for row in value[key]:
            if (
                not isinstance(row, dict)
                or type(row.get("listing_id")) is not int
                or not 0 < row["listing_id"] <= 9007199254740991
                or type(row.get("release_id")) is not int
                or row["release_id"] not in targets
                or row.get("origin") not in {"snapshot", "newly_observed"}
                or type(row.get("discovered_at")) not in (int, float)
                or not 0 <= row["discovered_at"] < 1e12
            ):
                raise ReadSafetyHalt("inventory_queue_invalid")
            references.append((row["origin"], row["listing_id"]))
            if key == "results" and (
                type(row.get("checked_at")) not in (int, float)
                or not 0 <= row["checked_at"] < 1e12
                or row.get("state") not in {"review_ready", "criteria_blocked", "expired"}
                or (row.get("state") != "expired" and not isinstance(row.get("result"), dict))
            ):
                raise ReadSafetyHalt("inventory_result_invalid")
    if len(set(references)) != len(references):
        raise ReadSafetyHalt("inventory_queue_duplicate")
    return value


def _prune_results(state, now):
    for row in state["results"]:
        if now - row["checked_at"] > RESULT_TTL or now < row["checked_at"]:
            row.update(state="expired", result=None)


def _save(db, value):
    db.execute(f"UPDATE {STATE_TABLE} SET data=? WHERE id=1", (json.dumps(value),))


def initialize_inventory(database, config):
    """Explicit additive operator provisioning. Never reset baselines or a denial latch."""
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (STATE_TABLE,)
        ).fetchone()
        if not exists:
            db.execute(
                f"CREATE TABLE {STATE_TABLE} ("
                "id INTEGER PRIMARY KEY CHECK(id=1), config_hash TEXT NOT NULL, data TEXT NOT NULL)"
            )
            db.execute(
                f"INSERT INTO {STATE_TABLE} VALUES(1,?,?)",
                (
                    config.fingerprint,
                    json.dumps(
                        dict(
                            seen=[],
                            baseline=False,
                            next_poll=0,
                            denied=False,
                            in_progress=False,
                            seed_verified_at=None,
                            pending=[],
                            results=[],
                        )
                    ),
                ),
            )
        _state(db, config)


class InventoryObserver:
    """One bounded poll. No background loop, delivery, target writes or checkout imports."""

    def __init__(self, database, config, *, transport=None, clock=time.time, sleep=time.sleep):
        self.database, self.config = database, config
        self.transport, self.clock, self.sleep = transport, clock, sleep

    def results(self):
        """Private review queue. Never returns stale payloads as current candidates."""
        with closing(_connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = _state(db, self.config)
            _prune_results(state, self.clock())
            _save(db, state)
            return {
                "status": "review_results",
                "results": state["results"],
                "pending": state["pending"],
                "access_denied": state["denied"],
                "in_progress": state["in_progress"],
                "purchase_allowed": False,
                "notifications_sent": 0,
            }

    def poll(self, *, snapshot=False):
        config = self.config
        with closing(_connect(self.database)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                control = _control(db)
                state = _state(db, config)
                if state["denied"] or state["in_progress"]:
                    raise ReadSafetyHalt("inventory_denied_or_manual_recovery_required")
                if self.clock() < state["next_poll"]:
                    return {"status": "not_due", "requests": 0, "purchase_allowed": False}
                state.update(in_progress=True, next_poll=self.clock() + config.poll_seconds)
                _prune_results(state, self.clock())
                _save(db, state)

            def check():
                current = _state(db, config)
                if current["denied"] or not current["in_progress"] or _control(db) != control:
                    raise ReadSafetyHalt("inventory_control_changed")

            budget = ReviewReadBudget(db, clock=self.clock, sleep=self.sleep, check=check)
            reader = InventoryReader(
                clock=self.clock,
                wall_clock=self.clock,
                sleep=self.sleep,
                transport=DeadlineTransport(self.transport),
            )
            requests = 0

            def request(read):
                nonlocal requests
                budget.response_delay = None
                try:
                    value = read()
                except ListingReadError as error:
                    if budget.response_delay is not None:
                        requests += 1
                    if error.access_restricted:
                        # Persist the source-specific stop before releasing the shared token.
                        with db:
                            db.execute("BEGIN IMMEDIATE")
                            current = _state(db, config)
                            current["denied"] = True
                            _save(db, current)
                    budget.finish()
                    raise
                requests += 1
                budget.finish()
                check()
                return value

            result = {"candidates": [], "purchase_allowed": False, "notifications_sent": 0}
            active_pending = None
            try:
                if state["seed_verified_at"] is None:
                    payload = request(lambda: reader.seed_payload(config, budget=budget))
                    verify_seed(payload, config.seed)
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        check()
                        state = _state(db, config)
                        state["seed_verified_at"] = self.clock()
                        _save(db, state)
                result = scan_inventory(
                    config,
                    lambda page: request(
                        lambda: reader.inventory_page(config, page, budget=budget)
                    ),
                )
                # Commit discovery and pending detail work atomically BEFORE detail requests.
                # A temporary detail error cannot consume a candidate or lose it on restart.
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    check()
                    state = _state(db, config)
                    ids = result.pop("observed_ids")
                    discovered = result.pop("candidates")
                    result["inventory_matches"] = len(discovered)
                    result["candidates"] = []
                    complete = result["pagination"]["complete"]
                    queued = []
                    if snapshot:
                        result["status"] = "snapshot_only"
                        result["candidate_semantics"] = "currently_observed_not_new_listing"
                        queued = discovered
                    elif not complete:
                        result["status"] = "incomplete_no_new_candidates"
                    elif not state["baseline"]:
                        state.update(seen=ids, baseline=True)
                        result["status"] = "baseline"
                        result["baseline_matches"] = len(discovered)
                    else:
                        previous = set(state["seen"])
                        queued = [v for v in discovered if int(v["listing_id"]) not in previous]
                        combined = previous | set(ids)
                        if len(combined) > MAX_SEEN:
                            raise ReadSafetyHalt("inventory_seen_capacity_manual_review_required")
                        state["seen"] = sorted(combined)
                        result["status"] = "observed"
                        result["candidate_semantics"] = "newly_observed_not_proven_newly_listed"
                    origin = "snapshot" if snapshot else "newly_observed"
                    existing = {
                        (v["origin"], v["listing_id"]) for v in state["pending"] + state["results"]
                    }
                    for value in queued:
                        ident = int(value["listing_id"])
                        # Explicit snapshots may refresh the same retained review event.
                        # Keep its deterministic event key, never turn it into a new lead.
                        if snapshot:
                            old = next(
                                (
                                    row
                                    for row in state["results"]
                                    if row["origin"] == origin and row["listing_id"] == ident
                                ),
                                None,
                            )
                            if old is not None:
                                state["results"].remove(old)
                                existing.discard((origin, ident))
                        if (origin, ident) not in existing:
                            state["pending"].append(
                                dict(
                                    listing_id=ident,
                                    release_id=value["target_release_id"],
                                    discovered_at=self.clock(),
                                    origin=origin,
                                )
                            )
                    if len(state["pending"]) + len(state["results"]) > MAX_SEEN:
                        raise ReadSafetyHalt("inventory_queue_capacity_manual_review_required")
                    _save(db, state)
                targets = {target.release_id: target for target in config.targets}
                for pending in state["pending"][: config.max_detail_reads]:
                    active_pending = pending
                    target = targets[pending["release_id"]]
                    payload = request(
                        lambda pending=pending, target=target: reader.detail_payload(
                            pending["listing_id"], target.currency, budget=budget
                        )
                    )
                    # The detail endpoint must still report the exact requested identity.
                    normalize(payload, pending["listing_id"])
                    detail = candidate(payload, target)
                    seller = payload.get("seller")
                    username = seller.get("username") if isinstance(seller, dict) else None
                    detail["checks"]["seed_seller_matches"] = (
                        isinstance(username, str)
                        and username.casefold() == config.seed.seller.casefold()
                    )
                    detail["item_criteria_match"] = all(detail["checks"].values())
                    detail.update(
                        source="official_public_listing_get",
                        detail_recheck_required=False,
                        freshness="detail_observation_only",
                        observed_at=datetime.fromtimestamp(self.clock(), UTC).isoformat(),
                    )
                    event = dict(
                        pending,
                        checked_at=self.clock(),
                        state="review_ready"
                        if detail["item_criteria_match"]
                        else "criteria_blocked",
                        result=detail,
                        event_key=hashlib.sha256(
                            f"{config.fingerprint}:{pending['origin']}:{pending['listing_id']}".encode()
                        ).hexdigest(),
                    )
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        check()
                        state = _state(db, config)
                        state["pending"].remove(pending)
                        state["results"].append(event)
                        _save(db, state)
                    result["candidates"].append(event)
                    active_pending = None
            except ListingReadError as error:
                # Any already-finished candidates remain durable and in this response.
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    state = _state(db, config)
                    state["next_poll"] = max(state["next_poll"], self.clock() + error.retry_after)
                    if (
                        active_pending is not None
                        and not error.access_restricted
                        and not error.rate_limited
                        and not isinstance(error, ReadBudgetBusy)
                    ):
                        # A vanished/malformed item cannot starve later candidates. Keep
                        # unresolved evidence pending, but try other IDs on the next poll.
                        state["pending"].remove(active_pending)
                        state["pending"].append(active_pending)
                    _save(db, state)
                result["status"] = (
                    "access_denied"
                    if error.access_restricted
                    else "cooldown"
                    if isinstance(error, ReadBudgetBusy) or error.rate_limited
                    else "unavailable"
                )
            with db:
                db.execute("BEGIN IMMEDIATE")
                state = _state(db, config)
                state["in_progress"] = False
                _save(db, state)
            result.update(
                requests=requests,
                source="official_public_inventory_get",
                pending_detail_count=len(state["pending"]),
                pending_candidates=state["pending"],
                retained_result_count=len(state["results"]),
                seed_verified=state["seed_verified_at"] is not None,
            )
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, required=True, help="Private explicit seed/criteria JSON"
    )
    parser.add_argument("--database", type=Path, help="Existing shared aggregate rules database")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--init-state", action="store_true", help="Provision stopped observer state only"
    )
    mode.add_argument("--poll", action="store_true", help="Permit one bounded public observation")
    mode.add_argument("--fixture", type=Path, help="Offline {seed_listing, pages} JSON snapshot")
    mode.add_argument("--results", action="store_true", help="Read retained private review results")
    parser.add_argument(
        "--snapshot", action="store_true", help="Report existing copies, no baseline update"
    )
    args = parser.parse_args(argv)
    if (args.poll or args.init_state or args.results) and args.database is None:
        parser.error("--database is required with --poll, --init-state or --results")
    if args.snapshot and not args.poll:
        parser.error("--snapshot requires --poll")
    try:
        config = InventoryConfig.model_validate(read_json(args.config, 16384))
        if args.init_state:
            initialize_inventory(args.database, config)
            result = {"status": "state_initialized_no_poll", "purchase_allowed": False}
        elif args.poll:
            result = InventoryObserver(args.database, config).poll(snapshot=args.snapshot)
        elif args.results:
            result = InventoryObserver(args.database, config).results()
        elif args.fixture:
            fixture = read_json(args.fixture, 1024 * 1024)
            result = collect(
                config, fixture["seed_listing"], lambda page: fixture["pages"][page - 1]
            )
            result.pop("observed_ids")
            result.update(status="offline_snapshot", source="offline_fixture", freshness="unknown")
        else:
            result = {"status": "validated_no_poll", "requests": 0, "purchase_allowed": False}
    except ReadSafetyHalt:
        print('{"status":"inventory_safety_halt","manual_recovery_required":true}')
        return 78
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, IndexError, RecursionError):
        print('{"status":"inventory_unavailable","purchase_allowed":false}')
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
