"""Portable SQLAlchemy repository with a database-enforced composite identity."""

from typing import Literal, Protocol

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from finder.domain import Listing
from finder.errors import PersistenceError

UpsertResult = Literal["new", "updated"]


class ListingRepository(Protocol):
    def upsert(self, listing: Listing) -> UpsertResult: ...
    def count(self) -> int: ...


metadata = MetaData()
listings = Table(
    "listings",
    metadata,
    Column("marketplace", String(64), primary_key=True),
    Column("marketplace_item_id", String(255), primary_key=True),
    # UTC ISO strings are sortable and avoid SQLite's timezone/float coercion.
    Column("first_observed_at", String(40), nullable=False),
    Column("last_observed_at", String(40), nullable=False, index=True),
    # Pydantic encodes Decimals as strings; no binary floating-point money in SQLite.
    Column("data", JSON, nullable=False),
)


class SqlAlchemyListingRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> "SqlAlchemyListingRepository":
        try:
            engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
            metadata.create_all(engine)
            return cls(engine)
        except (SQLAlchemyError, ImportError, ValueError):
            raise PersistenceError(
                "Cannot initialize database; check its URL, driver and path."
            ) from None

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _key(marketplace: str, item_id: str):
        return (listings.c.marketplace == marketplace) & (listings.c.marketplace_item_id == item_id)

    def upsert(self, listing: Listing) -> UpsertResult:
        key = self._key(listing.marketplace, listing.marketplace_item_id)
        # Retry a concurrent insert once; the composite PK remains the authority.
        for attempt in range(2):
            try:
                with self.engine.begin() as conn:
                    row = (
                        conn.execute(select(listings).where(key).with_for_update())
                        .mappings()
                        .first()
                    )
                    data = listing.model_dump(mode="json")
                    first = listing.first_observed_at.isoformat()
                    last = listing.last_observed_at.isoformat()
                    if row is None:
                        conn.execute(
                            listings.insert().values(
                                marketplace=listing.marketplace,
                                marketplace_item_id=listing.marketplace_item_id,
                                first_observed_at=first,
                                last_observed_at=last,
                                data=data,
                            )
                        )
                        return "new"
                    # Older concurrent observations never overwrite newer data.
                    if last < row["last_observed_at"]:
                        return "updated"
                    data["first_observed_at"] = row["first_observed_at"]
                    # Preserve detail-only aspects after enrichment failures, marked stale.
                    if listing.details_observed_at is None and row["data"].get(
                        "details_observed_at"
                    ):
                        data["item_specifics"] = row["data"].get("item_specifics", {})
                        data["details_observed_at"] = row["data"]["details_observed_at"]
                        data["quality_flags"] = sorted(
                            set(data["quality_flags"] + ["item_specifics_stale"])
                        )
                    conn.execute(
                        update(listings)
                        .where(key & (listings.c.last_observed_at <= last))
                        .values(last_observed_at=last, data=data)
                    )
                    return "updated"
            except IntegrityError:
                if attempt:
                    raise PersistenceError(
                        "Cannot upsert listing after concurrent insert."
                    ) from None
            except SQLAlchemyError:
                raise PersistenceError(
                    "Database write failed; previously committed listings remain."
                ) from None
        raise AssertionError("unreachable")

    def get(self, marketplace: str, item_id: str) -> Listing | None:
        try:
            with self.engine.connect() as conn:
                data = conn.execute(
                    select(listings.c.data).where(self._key(marketplace, item_id))
                ).scalar()
            if data is None:
                return None
            # Recompute derived fields when loading, while keeping them queryable in stored JSON.
            data.pop("total_acquisition_cost", None)
            return Listing.model_validate(data)
        except SQLAlchemyError:
            raise PersistenceError("Database read failed.") from None

    def count(self) -> int:
        try:
            with self.engine.connect() as conn:
                return conn.execute(select(func.count()).select_from(listings)).scalar_one()
        except SQLAlchemyError:
            raise PersistenceError("Cannot count stored listings.") from None
