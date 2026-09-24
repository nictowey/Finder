"""Private pilot storage, with versioned, additive schema and deletion cascades."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
    text,
    update,
)

from finder.categories.vinyl_clues import Clue
from finder.persistence import listings

# The Browse request budget, not storage, bounds this; cadence stretches as watches grow.
MAX_WATCHES = 20


class SavedWatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    release_id: int = Field(gt=0)
    label: str = Field(default="", max_length=120)
    maximum_subtotal: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    condition_ids: list[str] = Field(default_factory=list, max_length=20)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    postal_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 -]{1,16}$")
    enabled: bool = True
    alert_mode: Literal["review_leads", "strict"] = "review_leads"
    # The most the owner would pay when the pressing is unclear; unset means no alert.
    gamble_max: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Minutes before an auction ends when a qualifying bid alerts; 0 disables.
    auction_alert_minutes: int = Field(default=120, ge=0, le=1440)
    tells: list[Clue] = Field(default_factory=list, max_length=12)
    anti_tells: list[Clue] = Field(default_factory=list, max_length=12)
    extra_queries: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def valid_destination(self):
        if bool(self.country) != bool(self.postal_code):
            raise ValueError("Provide both country and postal code")
        if any(not value.isdigit() for value in self.condition_ids):
            raise ValueError("Condition IDs must be numeric")
        if any(not query.strip() or len(query) > 100 for query in self.extra_queries):
            raise ValueError("Extra searches must be nonblank and at most 100 characters")
        return self


schema = MetaData()
listings.to_metadata(schema)
migrations = Table("finder_schema_versions", schema, Column("version", Integer, primary_key=True))
watches = Table(
    "finder_watches",
    schema,
    Column("id", String(36), primary_key=True),
    Column("slot", Integer, nullable=False, unique=True),
    CheckConstraint(f"slot >= 1 AND slot <= {MAX_WATCHES}", name="finder_watches_slot_range"),
    Column("config", JSON, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("next_scan_at", String(40), nullable=False),
    Column("lease_until", String(40)),
    Column("lease_token", String(36)),
    Column("last_started_at", String(40)),
    Column("last_success_at", String(40)),
    Column("status", String(32), nullable=False, default="pending"),
    Column("summary", JSON),
    Column("catalog", JSON),
    Column("catalog_observed_at", String(40)),
)
inbox = Table(
    "finder_inbox",
    schema,
    Column("watch_id", String(36), primary_key=True),
    Column("marketplace", String(64), primary_key=True),
    Column("marketplace_item_id", String(255), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("first_seen_at", String(40), nullable=False),
    Column("last_seen_at", String(40), nullable=False),
    Column("dismissed", Boolean, nullable=False, default=False),
    Column("alerted", Boolean, nullable=False, default=False),
    ForeignKeyConstraint(["watch_id"], ["finder_watches.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(
        ["marketplace", "marketplace_item_id"],
        ["listings.marketplace", "listings.marketplace_item_id"],
        ondelete="CASCADE",
    ),
)
outbox = Table(
    "finder_outbox",
    schema,
    Column("id", String(36), primary_key=True),
    Column("watch_id", String(36), nullable=False),
    Column("marketplace", String(64), nullable=False),
    Column("marketplace_item_id", String(255), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("status", String(32), nullable=False, default="pending"),
    Column("attempts", Integer, nullable=False, default=0),
    ForeignKeyConstraint(
        ["watch_id", "marketplace", "marketplace_item_id"],
        ["finder_inbox.watch_id", "finder_inbox.marketplace", "finder_inbox.marketplace_item_id"],
        ondelete="CASCADE",
    ),
)
settings = Table(
    "finder_private_settings",
    schema,
    Column("key", String(64), primary_key=True),
    Column("data", JSON, nullable=False),
)
subscriptions = Table(
    "finder_push_subscriptions",
    schema,
    Column("id", String(64), primary_key=True),
    Column("data", JSON, nullable=False),
)
scan_attempts = Table(
    "finder_scan_attempts",
    schema,
    Column("id", String(36), primary_key=True),
    Column("watch_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("due_at", String(40), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("lease_until", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("previous_success_at", String(40)),
    Column("status", String(32), nullable=False),
    Column("run_id", String(32)),
    Column("source", String(32), nullable=False),
    Column("dispatch_id", String(36)),
    Column("metrics", JSON, nullable=False),
    ForeignKeyConstraint(["watch_id"], ["finder_watches.id"], ondelete="CASCADE"),
    Index("finder_scan_attempts_started", "started_at"),
)
dispatch_attempts = Table(
    "finder_dispatch_attempts",
    schema,
    Column("id", String(36), primary_key=True),
    Column("scheduled_at", String(40), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("status", String(32), nullable=False),
    Column("http_status", Integer),
    Index("finder_dispatch_attempts_started", "started_at"),
)
profiles = Table(
    "finder_watch_profiles",
    schema,
    Column("watch_id", String(36), primary_key=True),
    Column("release_id", Integer, nullable=False),
    Column("observed_at", String(40), nullable=False),
    Column("data", JSON, nullable=False),
    ForeignKeyConstraint(["watch_id"], ["finder_watches.id"], ondelete="CASCADE"),
)
verdicts = Table(
    "finder_verdicts",
    schema,
    Column("watch_id", String(36), primary_key=True),
    Column("marketplace", String(64), primary_key=True),
    Column("marketplace_item_id", String(255), primary_key=True),
    Column("verdict", String(16), nullable=False),
    Column("tier", String(32), nullable=False),
    Column("decided_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["watch_id", "marketplace", "marketplace_item_id"],
        ["finder_inbox.watch_id", "finder_inbox.marketplace", "finder_inbox.marketplace_item_id"],
        ondelete="CASCADE",
    ),
)
NEW_TABLES = [
    migrations,
    watches,
    inbox,
    outbox,
    settings,
    subscriptions,
    scan_attempts,
    dispatch_attempts,
    profiles,
    verdicts,
]


def _widen_watch_slots(conn):
    """Replace the three-slot check on existing PostgreSQL tables; data is unchanged."""
    if conn.dialect.name != "postgresql":
        return
    wanted = f"slot <= {MAX_WATCHES}"
    rows = conn.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'finder_watches'::regclass AND contype = 'c'"
        )
    ).all()
    checks = [(name, definition) for name, definition in rows if "slot" in definition]
    if len(checks) == 1 and wanted in checks[0][1]:
        return
    for name, _ in checks:
        conn.execute(text(f'ALTER TABLE finder_watches DROP CONSTRAINT "{name}"'))
    conn.execute(
        text(
            "ALTER TABLE finder_watches ADD CONSTRAINT finder_watches_slot_range "
            f"CHECK (slot >= 1 AND slot <= {MAX_WATCHES})"
        )
    )


def migrate(engine):
    """Additive pilot (1), operations (2), discovery (3), cheat sheets and verdicts (4)."""
    from finder.discovery_store import progress, work

    for table in (progress, work):
        if table not in NEW_TABLES:
            NEW_TABLES.append(table)
    with engine.begin() as conn:
        schema.create_all(conn, tables=NEW_TABLES)
        if not conn.execute(select(migrations).where(migrations.c.version == 1)).first():
            conn.execute(insert(migrations).values(version=1))
        if not conn.execute(select(migrations).where(migrations.c.version == 2)).first():
            conn.execute(insert(migrations).values(version=2))
        if not conn.execute(select(migrations).where(migrations.c.version == 3)).first():
            conn.execute(insert(migrations).values(version=3))
        _widen_watch_slots(conn)
        if not conn.execute(select(migrations).where(migrations.c.version == 4)).first():
            conn.execute(insert(migrations).values(version=4))
        if not conn.execute(select(settings).where(settings.c.key == "operations_since")).first():
            conn.execute(
                insert(settings).values(
                    key="operations_since", data={"at": datetime.now(UTC).isoformat()}
                )
            )


def rollback_pilot_schema(engine):
    """Explicit operator-only rollback; destroys pilot state, preserves ingestion."""
    with engine.begin() as conn:
        schema.drop_all(conn, tables=list(reversed(NEW_TABLES)))


class WatchStore:
    def __init__(self, engine):
        self.engine = engine

    def add(self, watch: SavedWatch, *, watch_id=None, now=None):
        now = now or datetime.now(UTC)
        watch_id = watch_id or str(uuid4())
        with self.engine.begin() as conn:
            if not conn.execute(select(watches.c.id).where(watches.c.id == watch_id)).first():
                used = set(conn.execute(select(watches.c.slot)).scalars())
                slot = next((n for n in range(1, MAX_WATCHES + 1) if n not in used), None)
                if slot is None:
                    raise ValueError("Pilot watch limit reached")
                conn.execute(
                    insert(watches).values(
                        slot=slot,
                        id=watch_id,
                        config=watch.model_dump(mode="json"),
                        enabled=watch.enabled,
                        revision=1,
                        next_scan_at=now.isoformat(),
                        status="pending",
                    )
                )
        return watch_id

    def claim(self, *, now=None, context=None):
        now = now or datetime.now(UTC)
        stamp = now.isoformat()
        with self.engine.begin() as conn:
            # A process killed before finish must remain visible after its lease expires.
            conn.execute(
                update(scan_attempts)
                .where(scan_attempts.c.status == "started", scan_attempts.c.lease_until <= stamp)
                .values(status="abandoned")
            )
            cutoff = (now - timedelta(days=30)).isoformat()
            conn.execute(delete(scan_attempts).where(scan_attempts.c.started_at < cutoff))
            conn.execute(delete(dispatch_attempts).where(dispatch_attempts.c.started_at < cutoff))
            query = (
                select(watches)
                .where(
                    watches.c.enabled.is_(True),
                    watches.c.next_scan_at <= stamp,
                    (watches.c.lease_until.is_(None)) | (watches.c.lease_until <= stamp),
                )
                .order_by(watches.c.next_scan_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = conn.execute(query).mappings().first()
            if row is None:
                return None
            token = str(uuid4())
            lease_until = (now + timedelta(minutes=12)).isoformat()
            conn.execute(
                update(watches)
                .where(watches.c.id == row["id"])
                .values(
                    lease_token=token,
                    lease_until=lease_until,
                    last_started_at=stamp,
                    status="scanning",
                )
            )
            context = context or {}
            source = context.get("source", "local")
            if source not in ("local", "manual", "github_schedule", "deployment", "neon_catchup"):
                source = "manual"
            dispatch_id = context.get("dispatch_id")
            if source == "neon_catchup":
                known = conn.execute(
                    select(dispatch_attempts.c.id).where(
                        dispatch_attempts.c.id == dispatch_id,
                        dispatch_attempts.c.status.in_(("reserved", "accepted", "unknown")),
                        dispatch_attempts.c.started_at >= (now - timedelta(hours=1)).isoformat(),
                    )
                ).first()
                if not known:
                    source, dispatch_id = "manual", None
            run_id = str(context.get("run_id", ""))
            conn.execute(
                insert(scan_attempts).values(
                    id=token,
                    watch_id=row["id"],
                    revision=row["revision"],
                    due_at=row["next_scan_at"],
                    started_at=stamp,
                    lease_until=lease_until,
                    previous_success_at=row["last_success_at"],
                    status="started",
                    source=source,
                    dispatch_id=dispatch_id,
                    run_id=run_id if run_id.isdigit() and len(run_id) <= 32 else None,
                    metrics={},
                )
            )
            return {**dict(row), "lease_token": token, "attempt_started_at": stamp}

    def pause_for_quota(self, claim, *, reason, remaining=None, required=None, now=None):
        """Release the lease without advancing the scan or inventory cursor."""
        with self.engine.begin() as conn:
            prior = claim["summary"] if isinstance(claim["summary"], dict) else {}
            result = conn.execute(
                update(watches)
                .where(
                    watches.c.id == claim["id"],
                    watches.c.revision == claim["revision"],
                    watches.c.lease_token == claim["lease_token"],
                    watches.c.lease_until > (now or datetime.now(UTC)).isoformat(),
                )
                .values(
                    lease_token=None,
                    lease_until=None,
                    last_started_at=claim["last_started_at"],
                    status="quota_paused",
                    summary={
                        **prior,
                        "quota_pause": {
                            "reason": reason,
                            "remaining": remaining,
                            "required": required,
                        },
                    },
                )
            )
            conn.execute(
                update(scan_attempts)
                .where(
                    scan_attempts.c.id == claim["lease_token"], scan_attempts.c.status == "started"
                )
                .values(
                    status="quota_paused" if result.rowcount else "superseded",
                    finished_at=(now or datetime.now(UTC)).isoformat(),
                    metrics={
                        "quota_remaining": remaining,
                        "quota_required": required,
                        "reason": reason,
                    },
                )
            )

    def finish(
        self,
        claim,
        reviews,
        *,
        catalog=None,
        summary=None,
        success=True,
        unavailable_item_ids=(),
        now=None,
        connection=None,
        checkpoint=False,
        next_delay_minutes=30,
    ):
        now = now or datetime.now(UTC)
        stamp = now.isoformat()
        with nullcontext(connection) if connection is not None else self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(watches)
                    .where(
                        watches.c.id == claim["id"],
                        watches.c.lease_token == claim["lease_token"],
                        watches.c.revision == claim["revision"],
                        watches.c.enabled.is_(True),
                        watches.c.lease_until > stamp,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not row:
                conn.execute(
                    update(scan_attempts)
                    .where(
                        scan_attempts.c.id == claim["lease_token"],
                        scan_attempts.c.status == "started",
                    )
                    .values(status="superseded", finished_at=stamp)
                )
                return None
            added = 0
            for listing, data in reviews:
                data = {**data, "watch_revision": claim["revision"]}
                # Lock the listing against concurrent seller deletion. The FK prevents
                # recreating evidence if the deletion already won the race.
                listing_exists = conn.execute(
                    select(listings.c.marketplace_item_id)
                    .where(
                        listings.c.marketplace == listing.marketplace,
                        listings.c.marketplace_item_id == listing.marketplace_item_id,
                    )
                    .with_for_update()
                ).first()
                if not listing_exists:
                    continue
                key = (
                    (inbox.c.watch_id == claim["id"])
                    & (inbox.c.marketplace == listing.marketplace)
                    & (inbox.c.marketplace_item_id == listing.marketplace_item_id)
                )
                old = conn.execute(select(inbox).where(key)).mappings().first()
                already_judged = conn.execute(
                    select(verdicts.c.verdict).where(
                        verdicts.c.watch_id == claim["id"],
                        verdicts.c.marketplace == listing.marketplace,
                        verdicts.c.marketplace_item_id == listing.marketplace_item_id,
                    )
                ).first()
                eligible = success and data.get("notify", False) and not already_judged
                values = dict(data=data, last_seen_at=stamp)
                if old:
                    conn.execute(update(inbox).where(key).values(**values))
                else:
                    conn.execute(
                        insert(inbox).values(
                            watch_id=claim["id"],
                            marketplace=listing.marketplace,
                            marketplace_item_id=listing.marketplace_item_id,
                            first_seen_at=stamp,
                            dismissed=False,
                            alerted=False,
                            **values,
                        )
                    )
                    added += 1
                if (
                    eligible
                    and not (old and (old["alerted"] or old["dismissed"]))
                    and not data.get("digest_covered")
                ):
                    conn.execute(
                        insert(outbox).values(
                            id=str(uuid4()),
                            watch_id=claim["id"],
                            marketplace=listing.marketplace,
                            marketplace_item_id=listing.marketplace_item_id,
                            created_at=stamp,
                            status="pending",
                            attempts=0,
                        )
                    )
                    conn.execute(update(inbox).where(key).values(alerted=True))
                if eligible and data.get("digest_covered"):
                    conn.execute(update(inbox).where(key).values(alerted=True))
                if not eligible:
                    conn.execute(
                        delete(outbox).where(
                            outbox.c.watch_id == claim["id"],
                            outbox.c.marketplace == listing.marketplace,
                            outbox.c.marketplace_item_id == listing.marketplace_item_id,
                            outbox.c.status == "pending",
                        )
                    )
            if success:
                for item_id in unavailable_item_ids:
                    key = (
                        (inbox.c.watch_id == claim["id"])
                        & (inbox.c.marketplace == "ebay")
                        & (inbox.c.marketplace_item_id == item_id)
                    )
                    previous = conn.execute(select(inbox.c.data).where(key)).scalar()
                    if previous is None:
                        continue
                    conn.execute(
                        update(inbox)
                        .where(key)
                        .values(
                            data={
                                **previous,
                                "availability": "unavailable_on_recheck",
                                "notify": False,
                            },
                            last_seen_at=stamp,
                        )
                    )
                    conn.execute(
                        delete(outbox).where(
                            outbox.c.watch_id == claim["id"],
                            outbox.c.marketplace == "ebay",
                            outbox.c.marketplace_item_id == item_id,
                            outbox.c.status == "pending",
                        )
                    )
            if checkpoint:
                return added
            if (
                not success
                and isinstance(row["summary"], dict)
                and row["summary"].get("inventory") is not None
                and not (summary or {}).get("inventory")
            ):
                summary = {**(summary or {}), "inventory": row["summary"]["inventory"]}
            values = dict(
                lease_token=None,
                lease_until=None,
                # Bound daily calls even when GitHub and Neon both schedule workers.
                # Ten-minute catch-up ticks keep a due watch's added wait below ten minutes.
                next_scan_at=(now + timedelta(minutes=next_delay_minutes)).isoformat(),
                status=(
                    "quota_paused"
                    if (summary or {}).get("coverage", {}).get("partial_reason")
                    == "quota_or_attempt_budget"
                    else "healthy"
                    if success
                    else "failed"
                ),
                summary=summary or {},
            )
            if catalog is not None:
                values.update(catalog=catalog.model_dump(mode="json"), catalog_observed_at=stamp)
            if success:
                values["last_success_at"] = stamp
            conn.execute(update(watches).where(watches.c.id == claim["id"]).values(**values))
            from finder.operations import scan_metrics

            conn.execute(
                update(scan_attempts)
                .where(
                    scan_attempts.c.id == claim["lease_token"], scan_attempts.c.status == "started"
                )
                .values(
                    status=(
                        "quota_paused"
                        if (summary or {}).get("coverage", {}).get("partial_reason")
                        == "quota_or_attempt_budget"
                        else "completed"
                        if success
                        else "failed"
                    ),
                    finished_at=stamp,
                    metrics=scan_metrics(summary or {}, added),
                )
            )
            return added
