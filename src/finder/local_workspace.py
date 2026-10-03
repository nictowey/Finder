"""Bounded manual/synthetic review storage, isolated from the live watch loop.

This adapter reuses the review policy but never fetches data or sends alerts. Its
tagged SQLite files are deliberately incompatible with demo and live databases.
Existing sidecars, including legitimate crash journals, fail closed. Preserve all
files for explicit recovery; this milestone does not recover or delete sidecars.
"""

import hashlib
import json
import os
import re
import stat
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from finder.categories.vinyl import from_variant
from finder.categories.vinyl_clues import Clue
from finder.categories.vinyl_covers import cover_name
from finder.domain import Listing, Variant
from finder.errors import PersistenceError
from finder.matching import _normalized, _palette, score_variant
from finder.persistence import SqlAlchemyListingRepository, listing_observations, listings
from finder.watch_worker import assess_review

MAX_IMPORT_BYTES = 256 * 1024
MAX_LISTINGS = 100
MAX_ALTERNATIVES = 20
SCHEMA_VERSION = 1  # SQLite storage version; observation input versions are separate.
APPLICATION_ID = 0x46494E4C  # FINL; checked before SQLite opens an existing file.
LOCAL_SOURCE = "finder-local-manual"
NOTICE = (
    "Local manual or synthetic cases only. Observation times and delivery quotes are "
    "user claims, not provider verified. Authored comparison profiles are incomplete; "
    "a possible result supports only the supplied profiles, not market or physical identity. "
    "Review and price gates are simulations; this workspace sends no alerts."
)
UNSUPPORTED_COMPARISON = (
    "Authored comparison is unsupported when an artist or album has no recognized identity words."
)


class WorkspaceError(ValueError):
    """A sanitized input, file-safety, or storage error safe to show locally."""


class WorkspaceConflict(WorkspaceError):
    """The displayed review no longer represents the current workspace."""


class WorkspaceObservationConflict(WorkspaceConflict):
    """An immutable observation already has different claims at the supplied time."""


class WorkspaceNotFound(WorkspaceError):
    """The requested local case does not exist."""


def _plain_text(value: str) -> str:
    if re.search(r"[\x00-\x1f\x7f]|://|\b(?:www\.|data:|javascript:|mailto:|file:)", value, re.I):
        raise ValueError("Use plain metadata, without URLs or control characters")
    if re.search(
        r"\bBearer\s|-----BEGIN|\b(?:api[_ -]?key|password|access[_ -]?token)\s*[:=]", value, re.I
    ):
        raise ValueError("Credentials are not allowed in local metadata")
    return value


Text = Annotated[str, Field(min_length=1, max_length=160), AfterValidator(_plain_text)]
Title = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_plain_text)]


def _money_string(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value) is None:
        raise ValueError("Money must be a nonnegative decimal string with at most two places")
    return value


def _two_places(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


Money = Annotated[
    Decimal,
    Field(ge=0, le=1000000, decimal_places=2, allow_inf_nan=False),
    BeforeValidator(_money_string),
    AfterValidator(_two_places),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^[0-9]{1,7}(?:\.[0-9]{1,2})?$",
            "description": "Decimal amount from 0.00 to 1000000.00",
        }
    ),
]
Cap = Annotated[
    Decimal,
    Field(gt=0, le=1000000, decimal_places=2, allow_inf_nan=False),
    BeforeValidator(_money_string),
    AfterValidator(_two_places),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^[0-9]{1,7}(?:\.[0-9]{1,2})?$",
            "description": "Positive decimal amount at most 1000000.00",
        }
    ),
]
Values = Annotated[list[Text], Field(max_length=12)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
PostalCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9 -]{1,16}$")]
CaseId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class LocalClue(Clue):
    value: Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_plain_text)]
    required: bool = Field(default=False, strict=True)


class TargetInput(_Input):
    artist: Text
    album: Title
    colors: Values = Field(default_factory=list)
    catalog_numbers: Values = Field(default_factory=list)
    barcodes: Values = Field(default_factory=list)
    formats: Values = Field(default_factory=lambda: ["LP"])


class ReviewSettings(_Input):
    maximum_subtotal: Cap | None = None
    gamble_max: Cap | None = None
    currency: Currency = "USD"
    country: Country | None = None
    postal_code: PostalCode | None = None
    condition_ids: Annotated[
        list[Annotated[str, Field(pattern=r"^[0-9]{1,12}$")]], Field(max_length=20)
    ] = Field(default_factory=list)
    alert_mode: Literal["review_leads", "strict"] = "review_leads"
    auction_alert_minutes: int = Field(default=120, ge=0, le=1440, strict=True)
    tells: list[LocalClue] = Field(default_factory=list, max_length=12)
    anti_tells: list[LocalClue] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_settings(self):
        if bool(self.country) != bool(self.postal_code):
            raise ValueError("Provide both country and postal code")
        if self.maximum_subtotal is not None and self.gamble_max is not None:
            if self.gamble_max > self.maximum_subtotal:
                raise ValueError("The unclear price cap cannot exceed the main price cap")
        return self


class ListingInput(_Input):
    id: CaseId
    title: Title
    observed_at: AwareDatetime
    details_observed_at: AwareDatetime | None = None
    artist: Text | None = None
    album: Title | None = None
    colors: Values = Field(default_factory=list)
    catalog_numbers: Values = Field(default_factory=list)
    barcodes: Values = Field(default_factory=list)
    formats: Values = Field(default_factory=list)
    current_price: Money | None = None
    currency: Currency | None = None
    shipping_cost: Money | None = None
    shipping_currency: Currency | None = None
    price_kind: Literal["fixed_price", "current_bid", "unknown"] = "unknown"
    condition_id: Annotated[str, Field(pattern=r"^[0-9]{1,12}$")] | None = None
    listing_ends_at: AwareDatetime | None = None
    delivery_country: Country | None = None
    delivery_postal_code: PostalCode | None = None

    @field_validator("observed_at", "details_observed_at", "listing_ends_at")
    @classmethod
    def utc_times(cls, value):
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def valid_claims(self):
        if self.details_observed_at and self.details_observed_at > self.observed_at:
            raise ValueError("Detail observation cannot be newer than the case observation")
        if bool(self.delivery_country) != bool(self.delivery_postal_code):
            raise ValueError("Provide both claimed delivery country and postal code")
        if self.current_price is not None and self.currency is None:
            raise ValueError("A stated price needs its currency")
        if self.shipping_cost is not None and self.shipping_currency is None:
            raise ValueError("A stated shipping cost needs its currency")
        return self


class ImportBundle(_Input):
    schema_version: Literal[1]
    source: Literal["manual", "synthetic"]
    target: TargetInput
    settings: ReviewSettings
    listings: list[ListingInput] = Field(max_length=MAX_LISTINGS)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Schema version must be an integer")
        return value


class TargetInputV2(TargetInput):
    country: Text | None = None
    release_year: int | None = Field(default=None, ge=1900, le=2099, strict=True)
    editions: Values = Field(default_factory=list)
    required_components: list[Literal["signed_insert"]] = Field(default_factory=list, max_length=1)
    cover_edition: (
        Annotated[str, Field(min_length=3, max_length=60), AfterValidator(_plain_text)] | None
    ) = None

    @model_validator(mode="after")
    def supported_cover_name(self):
        if self.cover_edition is not None:
            # Validate an explicit name with the existing parser, not a new seller
            # grammar. The fixed timestamp is irrelevant to this metadata-only check.
            variant = _variant(self, "local-cover-validation", datetime(2000, 1, 1, tzinfo=UTC))
            isolated_cover = variant.model_copy(
                update={"formats": [_cover_component(self.cover_edition)]}
            )
            if (
                re.search(r"[,/&+;?]", self.cover_edition)
                or _palette([self.cover_edition])
                or from_variant(isolated_cover).editions
                or cover_name(variant) != _normalized(self.cover_edition).removeprefix("the ")
            ):
                raise ValueError("Use one named cover recognized by the existing format parser")
        return self


class AlternativeInput(TargetInputV2):
    id: CaseId


class ListingInputV2(ListingInput):
    country: Text | None = None
    release_year: int | None = Field(default=None, ge=1900, le=2099, strict=True)
    editions: Values = Field(default_factory=list)


class ImportBundleV2(ImportBundle):
    schema_version: Literal[2]
    target: TargetInputV2
    listings: list[ListingInputV2] = Field(max_length=MAX_LISTINGS)
    alternatives: list[AlternativeInput] | None = Field(default=None, max_length=MAX_ALTERNATIVES)

    @model_validator(mode="after")
    def valid_alternatives(self):
        family = (_normalized(self.target.artist), _normalized(self.target.album))
        identities = set()
        profiles = set()
        for profile in self.alternatives or []:
            other_family = (_normalized(profile.artist), _normalized(profile.album))
            if not all((*family, *other_family)):
                raise PydanticCustomError("unsupported_comparison_family", UNSUPPORTED_COMPARISON)
            if other_family != family:
                raise ValueError("Comparison profiles must have the target artist and album")
            identity = profile.id.casefold()
            facts = profile.model_dump(mode="json", exclude={"id"})
            facts = {
                key: sorted({_normalized(item) for item in value})
                if isinstance(value, list)
                else _normalized(value)
                if isinstance(value, str)
                else value
                for key, value in facts.items()
            }
            fingerprint = _json(facts)
            if identity in identities or fingerprint in profiles:
                raise ValueError("Comparison profile IDs and facts must be distinct")
            identities.add(identity)
            profiles.add(fingerprint)
        return self

    @field_validator("alternatives")
    @classmethod
    def canonical_alternatives(cls, value):
        # Absent and empty both mean no comparison. Supplied order has no semantics.
        return sorted(value, key=lambda row: row.id.casefold()) if value else None


LocalBundle = ImportBundle | ImportBundleV2


_schema = MetaData()
_listings = listings.to_metadata(_schema)
_observations = listing_observations.to_metadata(_schema)
_meta = Table(
    "finder_local_meta",
    _schema,
    Column("id", Integer, primary_key=True),
    Column("data", JSON, nullable=False),
)
_targets = Table(
    "finder_local_targets",
    _schema,
    Column("revision", Integer, primary_key=True),
    Column("data", JSON, nullable=False),
)
_judgments = Table(
    "finder_local_judgments",
    _schema,
    Column("listing_id", String(64), primary_key=True),
    Column("data", JSON, nullable=False),
)


def _json(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceError("Import JSON must not contain duplicate object keys.")
        result[key] = value
    return result


def _review_clock(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise WorkspaceError("Review time must be a timezone-aware datetime.")
    return now.astimezone(UTC)


def validate_bundle(value, *, now: datetime) -> LocalBundle:
    """Validate bounded observation input at an explicit clock, without I/O or labels."""
    now = _review_clock(now)
    try:
        if isinstance(value, (str, bytes)):
            raw = value.encode() if isinstance(value, str) else value
            if len(raw) > MAX_IMPORT_BYTES:
                raise WorkspaceError("Import exceeds the 256 KiB limit.")
            value = json.loads(raw, object_pairs_hook=_pairs)
        elif isinstance(value, dict):
            if len(_json(value).encode()) > MAX_IMPORT_BYTES:
                raise WorkspaceError("Import exceeds the 256 KiB limit.")
        else:
            raise WorkspaceError("Import must be a JSON object.")
        model = (
            ImportBundleV2
            if isinstance(value, dict) and value.get("schema_version") == 2
            else ImportBundle
        )
        bundle = model.model_validate(value)
    except ValidationError as exc:
        if any(error["type"] == "unsupported_comparison_family" for error in exc.errors()):
            raise WorkspaceError(UNSUPPORTED_COMPARISON) from None
        paths = sorted({".".join(map(str, error["loc"])) or "bundle" for error in exc.errors()})
        raise WorkspaceError("Invalid local import fields: " + ", ".join(paths[:12])) from None
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, WorkspaceError):
            raise
        raise WorkspaceError("Import must contain valid, bounded JSON metadata.") from None
    if any(row.observed_at > now for row in bundle.listings):
        raise WorkspaceError("Claimed observations cannot be in the future.")
    return bundle


def _validate_bundle(value) -> LocalBundle:
    return validate_bundle(value, now=_utc_now())


def _path(value: str | Path) -> Path:
    text = str(value)
    if not text or ":" in text or "\\" in text or text.startswith("//"):
        raise WorkspaceError("Workspace must be a local file path, not a URL or URI.")
    path = Path(text).expanduser()
    if path.name == ".env" or path.name.startswith(".env."):
        raise WorkspaceError("Workspace cannot use a dotenv file.")
    if path.is_symlink():
        raise WorkspaceError("Workspace cannot use a symlink.")
    path = path.resolve()
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise WorkspaceError("Cannot safely inspect workspace sidecar paths.") from None
        raise WorkspaceError(
            "Workspace cannot use an existing SQLite sidecar; automatic crash recovery "
            "is not supported. Preserve the database and all sidecars."
        )
    return path


def _listing(row: ListingInput, source: str) -> Listing:
    specifics = {
        "Artist": [row.artist] if row.artist else [],
        "Release Title": [row.album] if row.album else [],
        "Color": row.colors,
        "Catalog Number": row.catalog_numbers,
        "Barcode": row.barcodes,
        "Format": row.formats,
    }
    if isinstance(row, ListingInputV2):
        specifics.update(
            Country=[row.country] if row.country else [],
            **{"Release Year": [str(row.release_year)] if row.release_year else []},
            Edition=row.editions,
        )
    return Listing(
        marketplace=LOCAL_SOURCE,
        marketplace_item_id=row.id,
        title=row.title,
        first_observed_at=row.observed_at,
        last_observed_at=row.observed_at,
        details_observed_at=row.details_observed_at,
        current_price=row.current_price,
        currency=row.currency,
        shipping_cost=row.shipping_cost,
        shipping_currency=row.shipping_currency,
        price_kind=row.price_kind,
        condition_id=row.condition_id,
        listing_ends_at=row.listing_ends_at,
        item_specifics={key: values for key, values in specifics.items() if values},
        source_metadata={
            "local_source": source,
            "observation_provenance": "user_supplied_unverified",
            "delivery_country": row.delivery_country,
            "delivery_postal_code": row.delivery_postal_code,
            "manual_input": row.model_dump(mode="json"),
        },
    )


def _cover_component(name: str) -> dict:
    return {"name": "All Media", "text": f"{name} Alternative Cover"}


def _variant(target: TargetInput, target_id: str, observed_at: datetime) -> Variant:
    formats = [{"name": "Vinyl", "descriptions": target.formats, "text": " ".join(target.colors)}]
    extra = {}
    work_id = target_id
    if isinstance(target, TargetInputV2):
        work_id = "local-work:" + _digest(
            {"artist": _normalized(target.artist), "album": _normalized(target.album)}
        )
        formats[0]["descriptions"] = [*target.formats, *target.editions]
        extra = {"country": target.country, "release_year": target.release_year}
        if "signed_insert" in target.required_components:
            formats.append({"name": "All Media", "text": "Signed Insert"})
        if target.cover_edition:
            formats.append(_cover_component(target.cover_edition))
    return Variant(
        catalog_source=LOCAL_SOURCE,
        catalog_variant_id=target_id,
        catalog_product_id=work_id,
        title=target.album,
        artists=[target.artist],
        formats=formats,
        labels=[{"catno": value} for value in target.catalog_numbers],
        identifiers={"Barcode": target.barcodes},
        observed_at=observed_at,
        source_metadata={"local_manual_target": True, "provider_verified": False},
        **extra,
    )


def review_local_case(bundle: LocalBundle, row: ListingInput | Listing, *, now: datetime) -> dict:
    """Assess validated local inputs without storage, expected answers, or a live clock.

    Canonical input is ``bundle.model_dump(mode="json")``. This adapter shares the
    UI's exact conversions and policy; profile evidence is diagnostic, not a new
    classification rule. All authored comparison coverage remains incomplete.
    """
    now = _review_clock(now)
    listing = row if isinstance(row, Listing) else _listing(row, bundle.source)
    target = _variant(bundle.target, "local-target", now)
    profiles = bundle.alternatives if isinstance(bundle, ImportBundleV2) else None
    alternatives = (
        [_variant(profile, f"local-alternative:{profile.id}", now) for profile in profiles]
        if profiles
        else None
    )
    review = assess_review(
        bundle.settings, listing, target, now=now, alternatives=alternatives, search_incomplete=True
    )
    return {
        "id": listing.marketplace_item_id,
        "listing": listing.model_dump(mode="json"),
        "review": review,
        "comparison_evidence": [
            item.model_dump(mode="json") for item in score_variant(listing, target, now).evidence
        ],
    }


def _load_listing(data) -> Listing:
    return Listing.model_validate(
        {key: value for key, value in data.items() if key != "total_acquisition_cost"}
    )


class LocalWorkspace:
    """One local target, append-only observations, and independent user verdicts."""

    def __init__(self, path: Path, engine):
        self.path = path
        self.engine = engine
        self.repository = SqlAlchemyListingRepository(engine)
        self._lock = threading.RLock()

    @classmethod
    def create(cls, value: str | Path):
        path = _path(value)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except OSError:
            raise WorkspaceError(
                "Choose a new workspace file in an existing writable directory."
            ) from None
        engine = create_engine(URL.create("sqlite", database=str(path)), hide_parameters=True)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
                conn.exec_driver_sql(f"PRAGMA application_id={APPLICATION_ID}")
                conn.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
                _schema.create_all(conn)
                conn.execute(
                    _meta.insert().values(
                        id=1,
                        data={
                            "schema_version": SCHEMA_VERSION,
                            "kind": LOCAL_SOURCE,
                            "target_id": str(uuid4()),
                            "created_at": _utc_now().isoformat(),
                        },
                    )
                )
        except SQLAlchemyError:
            engine.dispose()
            raise WorkspaceError("Could not initialize the new local workspace.") from None
        return cls(path, engine)

    @classmethod
    def open(cls, value: str | Path):
        path = _path(value)
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkspaceError("Workspace must be a regular, unlinked local file.")
            with path.open("rb") as stream:
                header = stream.read(100)
        except OSError:
            raise WorkspaceError("Cannot read the existing local workspace.") from None
        if (
            len(header) != 100
            or header[:16] != b"SQLite format 3\x00"
            or int.from_bytes(header[68:72], "big") != APPLICATION_ID
            or int.from_bytes(header[60:64], "big") != SCHEMA_VERSION
        ):
            raise WorkspaceError("File is not a recognized local review workspace.")
        engine = create_engine(URL.create("sqlite", database=str(path)), hide_parameters=True)
        try:
            with engine.connect() as conn:
                names = set(
                    conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).scalars()
                )
                if names != set(_schema.tables):
                    raise WorkspaceError("Local workspace schema is not recognized.")
                if conn.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'trigger') LIMIT 1"
                ).first():
                    raise WorkspaceError("Local workspace schema is not recognized.")
                for table in _schema.sorted_tables:
                    columns = {
                        row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')
                    }
                    if columns != set(table.c.keys()):
                        raise WorkspaceError("Local workspace schema is not recognized.")
                row = conn.execute(select(_meta.c.data).where(_meta.c.id == 1)).scalar_one()
                if row.get("kind") != LOCAL_SOURCE or row.get("schema_version") != SCHEMA_VERSION:
                    raise WorkspaceError("Local workspace metadata is not recognized.")
        except (SQLAlchemyError, ValueError, TypeError, AttributeError):
            engine.dispose()
            raise WorkspaceError("Local workspace schema is not recognized.") from None
        return cls(path, engine)

    def close(self):
        self.engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _current_profile(conn):
        return (
            conn.execute(select(_targets).order_by(_targets.c.revision.desc()).limit(1))
            .mappings()
            .first()
        )

    @staticmethod
    def _check_revision(current, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise WorkspaceError("Saving requires the displayed nonnegative profile revision.")
        if expected_revision != (current["revision"] if current else 0):
            raise WorkspaceConflict("The saved profile changed; reload it before saving.")

    @staticmethod
    def _profile_data(bundle):
        excluded = {"listings", "schema_version"} if bundle.schema_version == 1 else {"listings"}
        return bundle.model_dump(mode="json", exclude=excluded)

    @staticmethod
    def _observation_data(conn, row):
        return conn.execute(
            select(_observations.c.data).where(
                (_observations.c.marketplace == LOCAL_SOURCE)
                & (_observations.c.marketplace_item_id == row.marketplace_item_id)
                & (_observations.c.observed_at == row.last_observed_at.isoformat())
            )
        ).scalar()

    def _apply_bundle(self, conn, bundle, current, *, explicit_claims=False):
        """Apply validated input inside the caller's single locked transaction."""
        incoming = [_listing(row, bundle.source) for row in bundle.listings]
        target_data = self._profile_data(bundle)
        # Validate every collision before writing any profile or observation.
        known = set(conn.execute(select(_listings.c.marketplace_item_id)).scalars())
        if len(known | {row.marketplace_item_id for row in incoming}) > MAX_LISTINGS:
            raise WorkspaceError("A workspace supports at most 100 distinct local cases.")
        pending = {}
        new_observations = {}
        for row in incoming:
            key = (row.marketplace_item_id, row.last_observed_at.isoformat())
            data = row.model_dump(mode="json")
            existing = self._observation_data(conn, row)
            previous = pending.get(key, existing)
            if previous is not None and previous != data:
                raise WorkspaceObservationConflict(
                    "Different case data uses an existing observation time. "
                    "Keep the saved observation or supply an actual later observation time."
                )
            pending[key] = data
            if existing is None:
                new_observations[key] = row
        if current is None or current["data"]["input"] != target_data:
            revision = current["revision"] + 1 if current else 1
            conn.execute(
                _targets.insert().values(
                    revision=revision,
                    data={"input": target_data, "changed_at": _utc_now().isoformat()},
                )
            )
        for row in new_observations.values():
            self.repository.upsert(row, connection=conn)
            if explicit_claims:
                # Live upsert preserves old details after an enrichment failure. A manual
                # observation is a complete set of explicit claims, including unknowns.
                # Keep first-seen history, but do not silently borrow previous details.
                key = (_listings.c.marketplace == LOCAL_SOURCE) & (
                    _listings.c.marketplace_item_id == row.marketplace_item_id
                )
                first = conn.execute(select(_listings.c.first_observed_at).where(key)).scalar_one()
                data = row.model_dump(mode="json")
                data["first_observed_at"] = first
                conn.execute(_listings.update().where(key).values(data=data))

    def import_bundle(self, value, *, expected_revision=None):
        """Atomically append validated input, optionally guarding the displayed profile."""
        bundle = _validate_bundle(value)
        try:
            with self._lock, self.engine.begin() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                current = self._current_profile(conn)
                if expected_revision is not None:
                    self._check_revision(current, expected_revision)
                self._apply_bundle(conn, bundle, current)
        except (SQLAlchemyError, PersistenceError):
            raise WorkspaceError("Local import failed; no partial import was saved.") from None
        return self.snapshot()

    def save_profile(self, value, *, expected_revision):
        """Save a complete profile without adding or rewriting any observations."""
        if not isinstance(value, dict) or "listings" in value:
            raise WorkspaceError("Profile input must be an object without observations.")
        # The old import schema defaulted omitted target formats to LP. The builder
        # must leave omitted target/comparison formats unknown instead.
        value = dict(value)
        if isinstance(value.get("target"), dict):
            value["target"] = {"formats": [], **value["target"]}
        if isinstance(value.get("alternatives"), list):
            value["alternatives"] = [
                {"formats": [], **row} if isinstance(row, dict) else row
                for row in value["alternatives"]
            ]
        bundle = _validate_bundle({**value, "listings": []})
        try:
            with self._lock, self.engine.begin() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                current = self._current_profile(conn)
                self._check_revision(current, expected_revision)
                if current and bundle.schema_version < current["data"]["input"].get(
                    "schema_version", 1
                ):
                    raise WorkspaceError("Keep the saved profile version to preserve rich fields.")
                self._apply_bundle(conn, bundle, current)
        except (SQLAlchemyError, PersistenceError):
            raise WorkspaceError(
                "Could not save the local profile; no changes were saved."
            ) from None
        return self.snapshot()

    def save_candidate(self, value, *, expected_revision, expected_current):
        """Append one explicit observation using only the server's saved profile.

        ``expected_current`` is the displayed row's ``current_token``, or None for
        a new local ID. Changed existing cases require an actual later observation;
        historical input remains available through the advanced import operation.
        """
        if expected_current is not None and (
            not isinstance(expected_current, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_current) is None
        ):
            raise WorkspaceError("Candidate input requires its displayed observation token.")
        try:
            with self._lock, self.engine.begin() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                current = self._current_profile(conn)
                self._check_revision(current, expected_revision)
                if current is None:
                    raise WorkspaceError("Save a profile before adding a local candidate.")
                bundle = _validate_bundle(
                    {"schema_version": 1, **current["data"]["input"], "listings": [value]}
                )
                incoming = _listing(bundle.listings[0], bundle.source)
                existing_observation = self._observation_data(conn, incoming)
                if existing_observation is not None:
                    if existing_observation != incoming.model_dump(mode="json"):
                        raise WorkspaceObservationConflict(
                            "Different case data uses an existing observation time. "
                            "Keep the saved observation or supply an actual later observation time."
                        )
                    # Retrying exact claims is harmless, even if a newer observation exists.
                    return self._snapshot(conn, _utc_now())
                saved = conn.execute(
                    select(_listings.c.data).where(
                        (_listings.c.marketplace == LOCAL_SOURCE)
                        & (_listings.c.marketplace_item_id == incoming.marketplace_item_id)
                    )
                ).scalar()
                actual_token = (
                    _digest(_load_listing(saved).model_dump(mode="json")) if saved else None
                )
                if expected_current != actual_token:
                    raise WorkspaceConflict("This candidate changed; reload it before saving.")
                if saved and incoming.last_observed_at <= _load_listing(saved).last_observed_at:
                    raise WorkspaceError(
                        "Another observation needs an actual time later than the saved one. "
                        "Use advanced import for historical observations."
                    )
                self._apply_bundle(conn, bundle, current, explicit_claims=True)
        except (SQLAlchemyError, PersistenceError):
            raise WorkspaceError(
                "Could not save the local candidate; no changes were saved."
            ) from None
        return self.snapshot()

    def _snapshot(self, conn, now):
        meta = conn.execute(select(_meta.c.data).where(_meta.c.id == 1)).scalar_one()
        current = (
            conn.execute(select(_targets).order_by(_targets.c.revision.desc()).limit(1))
            .mappings()
            .first()
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "notice": NOTICE,
            "target_id": meta["target_id"],
            "revision": current["revision"] if current else 0,
            "target": None,
            "settings": None,
            "source": None,
            "rows": [],
        }
        if current is None:
            return result
        inputs = current["data"]["input"]
        result.update(inputs)
        # Untagged stored inputs are v1 forever; reading never upgrades their meaning.
        model = ImportBundleV2 if inputs.get("schema_version", 1) == 2 else ImportBundle
        bundle = model.model_validate({"schema_version": 1, **inputs, "listings": []})
        judgments = {
            row.listing_id: row.data
            for row in conn.execute(select(_judgments.c.listing_id, _judgments.c.data))
        }
        rows = conn.execute(
            select(_listings.c.data).order_by(_listings.c.marketplace_item_id)
        ).scalars()
        for data in rows:
            listing = _load_listing(data)
            data = listing.model_dump(mode="json")
            assessment = review_local_case(bundle, listing, now=now)
            review = assessment["review"]
            evidence_fingerprint = _digest(
                {"revision": result["revision"], "listing": data, "review": review}
            )
            judgment = judgments.get(listing.marketplace_item_id)
            fingerprint = _digest(
                {
                    "evidence": evidence_fingerprint,
                    "decision_revision": judgment["decision_revision"] if judgment else 0,
                }
            )
            needs_review = False
            if judgment is not None:
                needs_review = judgment["judged_evidence_fingerprint"] != evidence_fingerprint
                judgment = {
                    **judgment,
                    "provenance_changed": judgment["first_evidence_fingerprint"]
                    != evidence_fingerprint,
                }
            result["rows"].append(
                {
                    "id": listing.marketplace_item_id,
                    "current_token": _digest(data),
                    "listing": data,
                    "review": review,
                    "comparison_evidence": assessment["comparison_evidence"],
                    "review_fingerprint": fingerprint,
                    "evidence_fingerprint": evidence_fingerprint,
                    "verdict": judgment["verdict"] if judgment else None,
                    "judgment": judgment,
                    "judgment_needs_review": needs_review,
                }
            )
        return result

    def snapshot(self):
        """Compute current UTC review without changing stored prediction or verdict history."""
        try:
            with self._lock, self.engine.connect() as conn:
                conn.exec_driver_sql("BEGIN")
                return self._snapshot(conn, _utc_now())
        except SQLAlchemyError:
            raise WorkspaceError("Cannot read the local workspace.") from None

    def set_verdict(self, listing_id, verdict, *, expected_revision, review_fingerprint):
        if verdict not in ("mine", "other", "unsure"):
            raise WorkspaceError("Verdict must be mine, other, or unsure.")
        if type(expected_revision) is not int or not isinstance(review_fingerprint, str):
            raise WorkspaceError("Verdict requires the displayed revision and review fingerprint.")
        try:
            with self._lock, self.engine.begin() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                now = _utc_now()
                snapshot = self._snapshot(conn, now)
                row = next((row for row in snapshot["rows"] if row["id"] == listing_id), None)
                if row is None:
                    raise WorkspaceNotFound("Local case was not found.")
                if (
                    expected_revision != snapshot["revision"]
                    or review_fingerprint != row["review_fingerprint"]
                ):
                    raise WorkspaceConflict(
                        "Review changed; refresh the case before saving a verdict."
                    )
                existing = conn.execute(
                    select(_judgments.c.data).where(_judgments.c.listing_id == listing_id)
                ).scalar()
                judgment = (
                    dict(existing)
                    if existing
                    else {
                        "first_verdict": verdict,
                        "first_prediction": row["review"],
                        "first_revision": snapshot["revision"],
                        "first_fingerprint": row["review_fingerprint"],
                        "first_evidence_fingerprint": row["evidence_fingerprint"],
                        "first_observed_at": row["listing"]["last_observed_at"],
                        "first_judged_at": now.isoformat(),
                    }
                )
                judgment.update(
                    verdict=verdict,
                    updated_at=now.isoformat(),
                    decision_revision=existing["decision_revision"] + 1 if existing else 1,
                    judged_revision=snapshot["revision"],
                    judged_observed_at=row["listing"]["last_observed_at"],
                    judged_evidence_fingerprint=row["evidence_fingerprint"],
                )
                if existing:
                    conn.execute(
                        _judgments.update()
                        .where(_judgments.c.listing_id == listing_id)
                        .values(data=judgment)
                    )
                else:
                    conn.execute(_judgments.insert().values(listing_id=listing_id, data=judgment))
        except SQLAlchemyError:
            raise WorkspaceError("Could not save the local verdict.") from None
        return self.snapshot()

    def export_bundle(self):
        """Versioned archival export; intentionally not the manual-import input format."""
        try:
            with self._lock, self.engine.connect() as conn:
                conn.exec_driver_sql("BEGIN")
                return {
                    "export_version": 1,
                    "kind": LOCAL_SOURCE,
                    "notice": NOTICE,
                    "workspace": conn.execute(select(_meta.c.data)).scalar_one(),
                    "target_history": [
                        dict(row)
                        for row in conn.execute(
                            select(_targets).order_by(_targets.c.revision)
                        ).mappings()
                    ],
                    "observations": list(
                        conn.execute(
                            select(_observations.c.data).order_by(
                                _observations.c.marketplace_item_id, _observations.c.observed_at
                            )
                        ).scalars()
                    ),
                    "judgments": [
                        dict(row)
                        for row in conn.execute(
                            select(_judgments).order_by(_judgments.c.listing_id)
                        ).mappings()
                    ],
                }
        except SQLAlchemyError:
            raise WorkspaceError("Cannot export the local workspace.") from None
