"""Durable, lease-fenced search checkpoints and identity-only pending work."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)

from finder.adapters.ebay.discovery import PAGE_SIZE, RESULT_CEILING, iso, summary_fingerprint
from finder.watch_store import WatchStore, inbox, outbox, schema, watches

EPOCH = "1990-01-01T00:00:00.000Z"
OVERLAP = timedelta(hours=24)
MAX_REFERENCES = 30_000
POLICY = "discovery-review-v1"

progress = Table(
    "finder_discovery",
    schema,
    Column("watch_id", String(36), primary_key=True),
    Column("data", JSON, nullable=False),
    ForeignKeyConstraint(["watch_id"], ["finder_watches.id"], ondelete="CASCADE"),
)
work = Table(
    "finder_discovery_work",
    schema,
    Column("watch_id", String(36), primary_key=True),
    Column("item_id", String(255), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("provider_started_at", String(40)),
    Column("first_seen_at", String(40), nullable=False),
    Column("last_search_at", String(40), nullable=False),
    Column("checked_at", String(40)),
    Column("next_check_at", String(40), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason", String(64)),
    Column("kind", String(32), nullable=False),
    Column("failures", Integer, nullable=False, default=0),
    # Null while identity-only work awaits details. Once hydrated, seller deletion
    # cascades all new derived data with the existing listing and its stable seller ID.
    Column("marketplace", String(64)),
    Column("listing_id", String(255)),
    ForeignKeyConstraint(["watch_id"], ["finder_watches.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(
        ["marketplace", "listing_id"],
        ["listings.marketplace", "listings.marketplace_item_id"],
        ondelete="CASCADE",
    ),
    Index("finder_discovery_due", "watch_id", "next_check_at"),
)


class LostLease(Exception):
    pass


class StorageBudget(Exception):
    pass


def fence(conn, claim, now):
    row = conn.execute(
        select(watches.c.id)
        .where(
            watches.c.id == claim["id"],
            watches.c.revision == claim["revision"],
            watches.c.lease_token == claim["lease_token"],
            watches.c.lease_until > now.isoformat(),
            watches.c.enabled.is_(True),
        )
        .with_for_update()
    ).first()
    if not row:
        raise LostLease("Worker no longer owns this watch")


def partition(lower, upper):
    return {"lower": lower, "upper": upper, "offset": 0, "replay": 0, "previous_page": None}


def new_pass(lower, upper):
    return {
        "status": "not_started",
        "frontier": [partition(lower, upper)],
        "pages": 0,
        "returned": 0,
        "started_at": upper,
        "finished_at": None,
        "last_total": None,
    }


def start_state(target, revision, now):
    anchor = iso(now)
    signature = hashlib.sha256(
        json.dumps([target.model_dump(), revision, POLICY], sort_keys=True).encode()
    ).hexdigest()
    return {
        "signature": signature,
        "revision": revision,
        "anchor": anchor,
        "queries": [
            {
                "baseline": new_pass(EPOCH, anchor),
                "incremental": new_pass(iso(now - OVERLAP), anchor),
                "watermark": None,
                "reconciliation": None,
            }
            for _ in target.queries
        ],
        "round_robin": 0,
        "initial_digest_sent": False,
        "last_activity_at": None,
    }


def advance_pass(current, page, now):
    """Two sweeps per bounded window, inclusive time partitions, no total denominator."""
    result = deepcopy(current)
    part = result["frontier"][0]
    result["pages"] += 1
    result["returned"] += len(page.items)
    result["last_total"] = page.total
    result["status"] = "in_progress"
    result.pop("reason", None)
    if page.total >= RESULT_CEILING:
        low = datetime.fromisoformat(part["lower"].replace("Z", "+00:00"))
        high = datetime.fromisoformat(part["upper"].replace("Z", "+00:00"))
        mid = low + (high - low) / 2
        boundary = iso(mid)
        if boundary in (part["lower"], part["upper"]) or len(result["frontier"]) >= 128:
            result["status"] = "partial_provider_limit"
            result["reason"] = "time_partition_cannot_reduce_result_ceiling"
            return result
        # Boundaries deliberately overlap; stable item identities deduplicate.
        result["frontier"][:1] = [
            partition(boundary, part["upper"]),
            partition(part["lower"], boundary),
        ]
        return result
    if page.items and part["offset"] > 0 and page.fingerprint == part["previous_page"]:
        result["status"] = "interrupted"
        result["reason"] = "repeated_page"
        return result
    part["previous_page"] = page.fingerprint
    if page.more:
        if part["offset"] + PAGE_SIZE >= RESULT_CEILING:
            result["status"] = "partial_provider_limit"
            result["reason"] = "provider_offset_ceiling"
        else:
            part["offset"] += PAGE_SIZE
    elif part["replay"] == 0:
        part.update(offset=0, replay=1, previous_page=None)
    else:
        result["frontier"].pop(0)
        if not result["frontier"]:
            result.update(status="search_exhausted", finished_at=iso(now))
    return result


class DiscoveryStore:
    def __init__(self, engine):
        self.engine = engine

    def load(self, claim, target, now):
        initial = start_state(target, claim["revision"], now)
        with self.engine.begin() as conn:
            fence(conn, claim, now)
            saved = conn.execute(
                select(progress.c.data).where(progress.c.watch_id == claim["id"])
            ).scalar()
            if saved and saved.get("signature") == initial["signature"]:
                return deepcopy(saved)
            if saved:
                # New search/policy pass, same inbox/dismissal/notification identities.
                conn.execute(
                    update(work)
                    .where(work.c.watch_id == claim["id"])
                    .values(status="pending", next_check_at=now.isoformat())
                )
                conn.execute(
                    update(progress).where(progress.c.watch_id == claim["id"]).values(data=initial)
                )
            else:
                conn.execute(insert(progress).values(watch_id=claim["id"], data=initial))
            return initial

    def checkpoint(self, claim, state, now, *, items=(), kind="existing_inventory"):
        with self.engine.begin() as conn:
            fence(conn, claim, now)
            prior = (
                conn.execute(
                    select(progress.c.data).where(progress.c.watch_id == claim["id"])
                ).scalar()
                or {}
            )
            if state.get("evaluation_signature") != prior.get("evaluation_signature"):
                conn.execute(
                    update(work)
                    .where(work.c.watch_id == claim["id"], work.c.status == "evaluated")
                    .values(status="pending", next_check_at=now.isoformat())
                )
            count = conn.execute(
                select(func.count()).select_from(work).where(work.c.watch_id == claim["id"])
            ).scalar_one()
            for raw in items:
                key = (work.c.watch_id == claim["id"]) & (work.c.item_id == raw["itemId"])
                old = conn.execute(select(work).where(key)).mappings().first()
                fingerprint = summary_fingerprint(raw)
                if old:
                    values = {"last_search_at": now.isoformat(), "fingerprint": fingerprint}
                    if old["fingerprint"] != fingerprint or old["status"] == "unavailable":
                        values.update(
                            status="pending",
                            next_check_at=now.isoformat(),
                            kind="existing_listing_updated",
                        )
                    conn.execute(update(work).where(key).values(**values))
                else:
                    if count >= MAX_REFERENCES:
                        raise StorageBudget("Reference capacity reached; page remains uncommitted")
                    count += 1
                    conn.execute(
                        insert(work).values(
                            watch_id=claim["id"],
                            item_id=raw["itemId"],
                            fingerprint=fingerprint,
                            provider_started_at=raw.get("itemOriginDate"),
                            first_seen_at=now.isoformat(),
                            last_search_at=now.isoformat(),
                            next_check_at=now.isoformat(),
                            status="pending",
                            kind=(
                                "new_to_finder"
                                if raw.get("itemOriginDate", "") <= state["anchor"]
                                else kind
                            ),
                            failures=0,
                        )
                    )
            # This update and all pending identities are one transaction. A lost worker
            # cannot advance either; a crash replays an idempotent page.
            conn.execute(
                update(progress).where(progress.c.watch_id == claim["id"]).values(data=state)
            )

    def due(self, claim, now, *, limit, pending):
        with self.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(work)
                    .where(
                        work.c.watch_id == claim["id"],
                        work.c.next_check_at <= now.isoformat(),
                        work.c.status.in_(("pending", "error"))
                        if pending
                        else work.c.status == "evaluated",
                    )
                    .order_by(work.c.next_check_at, work.c.first_seen_at, work.c.item_id)
                    .limit(limit)
                ).mappings()
            ]

    def disposition(
        self,
        claim,
        item,
        now,
        *,
        status,
        reason=None,
        listing=None,
        repository=None,
        refresh_hours=4,
        review=None,
        state=None,
    ):
        updated_state = None
        with self.engine.begin() as conn:
            fence(conn, claim, now)
            key = (work.c.watch_id == claim["id"]) & (work.c.item_id == item["item_id"])
            if not conn.execute(select(work.c.item_id).where(key)).first():
                return "suppressed", 0
            if listing is not None:
                saved = repository.upsert(listing, connection=conn, record_observation=False)
                if saved == "suppressed":
                    status, reason, listing = "suppressed", "seller_deletion", None
            values = {
                "status": status,
                "reason": reason,
                "checked_at": now.isoformat(),
                "next_check_at": (now + timedelta(hours=refresh_hours)).isoformat(),
                "failures": item["failures"] + 1 if status == "error" else 0,
            }
            if listing:
                values.update(
                    marketplace=listing.marketplace, listing_id=listing.marketplace_item_id
                )
            added = 0
            if listing and review is not None:
                prior_review = (
                    conn.execute(
                        select(inbox.c.alerted, inbox.c.dismissed).where(
                            inbox.c.watch_id == claim["id"],
                            inbox.c.marketplace == listing.marketplace,
                            inbox.c.marketplace_item_id == listing.marketplace_item_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    state is not None
                    and item["provider_started_at"] <= state["anchor"]
                    and review.get("notify")
                    and not (
                        prior_review and (prior_review["alerted"] or prior_review["dismissed"])
                    )
                ):
                    review = {**review, "digest_covered": state["initial_digest_sent"]}
                    updated_state = deepcopy(state)
                    updated_state["initial_digest_sent"] = True
                    conn.execute(
                        update(progress)
                        .where(progress.c.watch_id == claim["id"])
                        .values(data=updated_state)
                    )
                review = {
                    **review,
                    "discovery_kind": item["kind"],
                    "provider_started_at": item["provider_started_at"],
                    "details_observed_at": listing.details_observed_at.isoformat()
                    if listing.details_observed_at
                    else None,
                }
                added = WatchStore(self.engine).finish(
                    claim, [(listing, review)], now=now, connection=conn, checkpoint=True
                )
            elif status in ("unavailable", "error", "suppressed"):
                previous = conn.execute(
                    select(inbox.c.data).where(
                        inbox.c.watch_id == claim["id"],
                        inbox.c.marketplace_item_id == item["item_id"],
                    )
                ).scalar()
                if previous:
                    data = {**previous, "notify": False}
                    if status == "unavailable":
                        data["availability"] = "unavailable_on_recheck"
                    conn.execute(
                        update(inbox)
                        .where(
                            inbox.c.watch_id == claim["id"],
                            inbox.c.marketplace_item_id == item["item_id"],
                        )
                        .values(data=data)
                    )
                conn.execute(
                    delete(outbox).where(
                        outbox.c.watch_id == claim["id"],
                        outbox.c.marketplace_item_id == item["item_id"],
                        outbox.c.status.in_(("pending", "sending")),
                    )
                )
            if status == "suppressed":
                # A tombstone rejection must also erase the newly rediscovered identity.
                # Retain only an anonymous event count, never an item/seller association.
                conn.execute(delete(work).where(key))
                updated_state = (
                    deepcopy(state)
                    if state is not None
                    else dict(
                        conn.execute(
                            select(progress.c.data).where(progress.c.watch_id == claim["id"])
                        ).scalar_one()
                    )
                )
                updated_state["suppression_events"] = updated_state.get("suppression_events", 0) + 1
                conn.execute(
                    update(progress)
                    .where(progress.c.watch_id == claim["id"])
                    .values(data=updated_state)
                )
            else:
                conn.execute(update(work).where(key).values(**values))
        if updated_state is not None and state is not None:
            state.update(updated_state)
        return status, added

    def coverage(self, claim, state):
        with self.engine.connect() as conn:
            counts = dict(
                conn.execute(
                    select(work.c.status, func.count())
                    .where(work.c.watch_id == claim["id"])
                    .group_by(work.c.status)
                ).all()
            )
        pending = counts.get("pending", 0) + counts.get("error", 0)

        def describe(name):
            passes = [q.get(name) for q in state["queries"]]
            states = [p["status"] if p else "not_started" for p in passes]
            complete = all(s == "search_exhausted" for s in states)
            status = (
                "search_exhausted_evaluation_pending"
                if complete and pending
                else "search_exhausted"
                if complete
                else next(
                    (
                        s
                        for s in ("partial_provider_limit", "interrupted", "partial_budget")
                        if s in states
                    ),
                    "in_progress" if any(s != "not_started" for s in states) else "not_started",
                )
            )
            return {
                "status": status,
                "queries_exhausted": states.count("search_exhausted"),
                "queries": len(states),
                "query_passes": [
                    {
                        "position": i + 1,
                        "status": p["status"] if p else "not_started",
                        "pages": p["pages"] if p else 0,
                        "reported_total": p["last_total"] if p else None,
                        "reason": p.get("reason")
                        if p and p["status"] != "search_exhausted"
                        else None,
                        "next_offset": p["frontier"][0]["offset"] if p and p["frontier"] else None,
                    }
                    for i, p in enumerate(passes)
                ],
                "pages": sum(p["pages"] for p in passes if p),
                "finished_at": max(
                    (p["finished_at"] for p in passes if p and p["finished_at"]), default=None
                )
                if complete
                else None,
            }

        return {
            "version": 2,
            "initial": describe("baseline"),
            "incremental": describe("incremental"),
            "reconciliation": describe("reconciliation"),
            "unique_retrieved": sum(counts.values()),
            "pending": pending,
            "outcomes": counts,
            "suppression_events": state.get("suppression_events", 0),
            "watermarks": [q["watermark"] for q in state["queries"]],
            "last_activity_at": state["last_activity_at"],
            "last_reconciliation_at": state.get("last_reconciliation_at"),
            "scope": (
                "Configured queries · EBAY_US · worldwide locations · all conditions · "
                "fixed price, auction, best offer"
            ),
            "overlap_hours": 24,
            "snapshot_guaranteed": False,
        }
