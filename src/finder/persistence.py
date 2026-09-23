"""Portable SQLAlchemy repository with a database-enforced composite identity."""

from typing import Literal, Protocol

from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    func,
    select,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from finder.domain import Listing, ListingVariantCandidate, Product, Variant
from finder.errors import PersistenceError

UpsertResult = Literal["new", "updated", "suppressed"]


class ListingRepository(Protocol):
    def upsert(self, listing: Listing) -> UpsertResult: ...
    def count(self) -> int: ...
    def get_observations(self, marketplace: str, item_id: str) -> list[Listing]: ...
    def list_recent(self, marketplace: str, limit: int) -> list[Listing]: ...
    def delete_ebay_seller(self, seller_id: str) -> int: ...


class CatalogRepository(Protocol):
    def upsert_product(self, product: Product) -> UpsertResult: ...
    def upsert_variant(self, variant: Variant) -> UpsertResult: ...
    def replace_candidates(
        self,
        marketplace: str,
        item_id: str,
        catalog_source: str,
        candidates: list[ListingVariantCandidate],
    ) -> None: ...


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
listing_observations = Table(
    "listing_observations",
    metadata,
    Column("marketplace", String(64), primary_key=True),
    Column("marketplace_item_id", String(255), primary_key=True),
    Column("observed_at", String(40), primary_key=True),
    Column("data", JSON, nullable=False),
)
products = Table(
    "products",
    metadata,
    Column("catalog_source", String(64), primary_key=True),
    Column("catalog_product_id", String(255), primary_key=True),
    Column("observed_at", String(40), nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
variants = Table(
    "variants",
    metadata,
    Column("catalog_source", String(64), primary_key=True),
    Column("catalog_variant_id", String(255), primary_key=True),
    Column("catalog_product_id", String(255), nullable=False, index=True),
    Column("observed_at", String(40), nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
listing_variant_candidates = Table(
    "listing_variant_candidates",
    metadata,
    Column("marketplace", String(64), primary_key=True),
    Column("marketplace_item_id", String(255), primary_key=True),
    Column("catalog_source", String(64), primary_key=True),
    Column("catalog_variant_id", String(255), primary_key=True),
    Column("observed_at", String(40), nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
ebay_deleted_users = Table(
    "ebay_deleted_users",
    metadata,
    Column("seller_id", String(255), primary_key=True),
)


class SqlAlchemyListingRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> "SqlAlchemyListingRepository":
        try:
            parsed = make_url(url)
            if parsed.drivername in ("postgres", "postgresql"):
                parsed = parsed.set(drivername="postgresql+psycopg")
            engine = create_engine(parsed, pool_pre_ping=True, hide_parameters=True)
            if parsed.get_backend_name() == "sqlite":

                @event.listens_for(engine, "connect")
                def enable_foreign_keys(connection, _):
                    connection.execute("PRAGMA foreign_keys=ON")

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
                    if listing.marketplace == "ebay" and listing.seller_id:
                        if conn.dialect.name == "postgresql":
                            # The deletion endpoint takes the same lock before tombstoning.
                            conn.execute(
                                select(func.pg_advisory_xact_lock(func.hashtext(listing.seller_id)))
                            )
                        if conn.execute(
                            select(ebay_deleted_users.c.seller_id).where(
                                ebay_deleted_users.c.seller_id == listing.seller_id
                            )
                        ).first():
                            return "suppressed"
                    observation_key = (
                        (listing_observations.c.marketplace == listing.marketplace)
                        & (
                            listing_observations.c.marketplace_item_id
                            == listing.marketplace_item_id
                        )
                        & (
                            listing_observations.c.observed_at
                            == listing.last_observed_at.isoformat()
                        )
                    )
                    observation_exists = conn.execute(
                        select(listing_observations.c.observed_at).where(observation_key)
                    ).first()
                    if observation_exists is None:
                        conn.execute(
                            listing_observations.insert().values(
                                marketplace=listing.marketplace,
                                marketplace_item_id=listing.marketplace_item_id,
                                observed_at=listing.last_observed_at.isoformat(),
                                data=listing.model_dump(mode="json"),
                            )
                        )
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

    def list_recent(self, marketplace: str, limit: int) -> list[Listing]:
        """Return bounded current snapshots, newest first; history remains untouched."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        select(listings.c.data)
                        .where(listings.c.marketplace == marketplace)
                        .order_by(
                            listings.c.last_observed_at.desc(),
                            listings.c.marketplace_item_id.asc(),
                        )
                        .limit(limit)
                    )
                    .scalars()
                    .all()
                )
            result = []
            for data in rows:
                data.pop("total_acquisition_cost", None)
                result.append(Listing.model_validate(data))
            return result
        except SQLAlchemyError:
            raise PersistenceError("Cannot list stored listings.") from None

    def count(self) -> int:
        try:
            with self.engine.connect() as conn:
                return conn.execute(select(func.count()).select_from(listings)).scalar_one()
        except SQLAlchemyError:
            raise PersistenceError("Cannot count stored listings.") from None

    def delete_ebay_seller(self, seller_id: str) -> int:
        """Remove all stored eBay records for a notified user in one transaction."""
        if not seller_id:
            raise PersistenceError("Cannot delete listings without a seller ID.")
        try:
            with self.engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(select(func.pg_advisory_xact_lock(func.hashtext(seller_id))))
                if not conn.execute(
                    select(ebay_deleted_users.c.seller_id).where(
                        ebay_deleted_users.c.seller_id == seller_id
                    )
                ).first():
                    conn.execute(ebay_deleted_users.insert().values(seller_id=seller_id))
                item_ids = (
                    conn.execute(
                        select(listings.c.marketplace_item_id).where(
                            (listings.c.marketplace == "ebay")
                            & (listings.c.data["seller_id"].as_string() == seller_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not item_ids:
                    return 0
                scope = (listings.c.marketplace == "ebay") & (
                    listings.c.marketplace_item_id.in_(item_ids)
                )
                for table in (listing_variant_candidates, listing_observations):
                    conn.execute(
                        delete(table).where(
                            (table.c.marketplace == "ebay")
                            & (table.c.marketplace_item_id.in_(item_ids))
                        )
                    )
                conn.execute(delete(listings).where(scope))
                return len(item_ids)
        except SQLAlchemyError:
            raise PersistenceError(
                "Cannot delete eBay seller data; notification must be retried."
            ) from None

    def get_observations(self, marketplace: str, item_id: str) -> list[Listing]:
        scope = (listing_observations.c.marketplace == marketplace) & (
            listing_observations.c.marketplace_item_id == item_id
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(listing_observations.c.data)
                    .where(scope)
                    .order_by(listing_observations.c.observed_at)
                ).scalars()
                observations = []
                for row in rows:
                    row.pop("total_acquisition_cost", None)
                    observations.append(Listing.model_validate(row))
                return observations
        except SQLAlchemyError:
            raise PersistenceError("Cannot load listing observation history.") from None

    @staticmethod
    def _catalog_key(table: Table, source: str, identifier_column: str, identifier: str):
        return (table.c.catalog_source == source) & (
            getattr(table.c, identifier_column) == identifier
        )

    def upsert_product(self, product: Product) -> UpsertResult:
        key = self._catalog_key(
            products, product.catalog_source, "catalog_product_id", product.catalog_product_id
        )
        return self._upsert_catalog_document(
            products,
            key,
            {
                "catalog_source": product.catalog_source,
                "catalog_product_id": product.catalog_product_id,
                "observed_at": product.observed_at.isoformat(),
                "data": product.model_dump(mode="json"),
            },
        )

    def upsert_variant(self, variant: Variant) -> UpsertResult:
        key = self._catalog_key(
            variants, variant.catalog_source, "catalog_variant_id", variant.catalog_variant_id
        )
        return self._upsert_catalog_document(
            variants,
            key,
            {
                "catalog_source": variant.catalog_source,
                "catalog_variant_id": variant.catalog_variant_id,
                "catalog_product_id": variant.catalog_product_id,
                "observed_at": variant.observed_at.isoformat(),
                "data": variant.model_dump(mode="json"),
            },
        )

    def _upsert_catalog_document(self, table: Table, key, values: dict) -> UpsertResult:
        try:
            with self.engine.begin() as conn:
                exists = conn.execute(select(table).where(key).with_for_update()).first()
                if exists is None:
                    conn.execute(table.insert().values(**values))
                    return "new"
                conn.execute(update(table).where(key).values(**values))
                return "updated"
        except SQLAlchemyError:
            raise PersistenceError("Catalog database write failed.") from None

    def replace_candidates(
        self,
        marketplace: str,
        item_id: str,
        catalog_source: str,
        candidates: list[ListingVariantCandidate],
    ) -> None:
        scope = (
            (listing_variant_candidates.c.marketplace == marketplace)
            & (listing_variant_candidates.c.marketplace_item_id == item_id)
            & (listing_variant_candidates.c.catalog_source == catalog_source)
        )
        if any(
            candidate.marketplace != marketplace
            or candidate.marketplace_item_id != item_id
            or candidate.catalog_source != catalog_source
            for candidate in candidates
        ):
            raise PersistenceError("Candidate identities do not match the replacement scope.")
        try:
            with self.engine.begin() as conn:
                listing_key = self._key(marketplace, item_id)
                current = conn.execute(select(listings.c.data).where(listing_key)).scalar()
                if current is None:
                    raise PersistenceError("Cannot attach candidates to a missing listing.")
                seller_id = current.get("seller_id") if marketplace == "ebay" else None
                if seller_id and conn.dialect.name == "postgresql":
                    # Lock in the same order as the deletion endpoint: seller, then row.
                    # A deletion that wins first leaves no listing to attach evidence to.
                    conn.execute(select(func.pg_advisory_xact_lock(func.hashtext(seller_id))))
                current = conn.execute(
                    select(listings.c.data).where(listing_key).with_for_update()
                ).scalar()
                if current is None or (seller_id and current.get("seller_id") != seller_id):
                    raise PersistenceError("Listing changed during candidate replacement.")
                if (
                    seller_id
                    and conn.execute(
                        select(ebay_deleted_users.c.seller_id).where(
                            ebay_deleted_users.c.seller_id == seller_id
                        )
                    ).first()
                ):
                    raise PersistenceError("Cannot attach candidates to a deleted seller.")
                conn.execute(delete(listing_variant_candidates).where(scope))
                if candidates:
                    conn.execute(
                        listing_variant_candidates.insert(),
                        [
                            {
                                "marketplace": candidate.marketplace,
                                "marketplace_item_id": candidate.marketplace_item_id,
                                "catalog_source": candidate.catalog_source,
                                "catalog_variant_id": candidate.catalog_variant_id,
                                "observed_at": candidate.observed_at.isoformat(),
                                "data": candidate.model_dump(mode="json"),
                            }
                            for candidate in candidates
                        ],
                    )
        except SQLAlchemyError:
            raise PersistenceError("Candidate database write failed.") from None

    def get_variant(self, source: str, variant_id: str) -> Variant | None:
        key = self._catalog_key(variants, source, "catalog_variant_id", variant_id)
        try:
            with self.engine.connect() as conn:
                data = conn.execute(select(variants.c.data).where(key)).scalar()
            return Variant.model_validate(data) if data else None
        except SQLAlchemyError:
            raise PersistenceError("Catalog database read failed.") from None

    def get_candidates(
        self, marketplace: str, item_id: str, source: str
    ) -> list[ListingVariantCandidate]:
        scope = (
            (listing_variant_candidates.c.marketplace == marketplace)
            & (listing_variant_candidates.c.marketplace_item_id == item_id)
            & (listing_variant_candidates.c.catalog_source == source)
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(listing_variant_candidates.c.data).where(scope)
                ).scalars()
                return [ListingVariantCandidate.model_validate(row) for row in rows]
        except SQLAlchemyError:
            raise PersistenceError("Candidate database read failed.") from None


# Backwards-compatible name while the repository expands beyond listings.
SqlAlchemyRepository = SqlAlchemyListingRepository
