"""Private Discogs rule preparation. No provider transport or purchasing capability."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from finder.discogs_completeness import assess_media_completeness, current_release_media
from finder.discogs_media_defects import assess_media_defects

GRADES = ("P", "F", "G", "G+", "VG", "VG+", "NM", "M")
CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD")


def release_id(value):
    if type(value) is int and 0 < value <= 9007199254740991:
        return value
    if isinstance(value, str):
        match = re.fullmatch(
            r"(?:https://(?:www\.)?discogs\.com/(?:[a-z]{2}/)?release/)?"
            r"([1-9]\d*)(?:-[^?#\s]*)?(?:[?#][^\s]*)?",
            value.strip(),
        )
        if match and int(match[1]) <= 9007199254740991:
            return int(match[1])
    raise ValueError("Use a Discogs release ID or https://www.discogs.com/release/ link.")


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", value):
        raise ValueError("Amounts must be non-negative decimal strings with at most two decimals.")
    return format(Decimal(value), ".2f")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Target(StrictInput):
    release_id: int
    enabled: bool = True
    label: str = Field(default="", max_length=120)
    min_media: Literal["P", "F", "G", "G+", "VG", "VG+", "NM", "M"]
    min_sleeve: Literal["P", "F", "G", "G+", "VG", "VG+", "NM", "M"]
    allow_generic_sleeve: bool = False
    min_seller_percent: float = Field(ge=0, le=100)
    min_feedback: int = Field(ge=0, le=10000000)
    max_total: str
    currency: Literal["USD", "EUR", "GBP", "CAD", "AUD"]
    destination_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    destination_postal: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 -]{1,16}$")

    _release = field_validator("release_id", mode="before")(release_id)
    _amount = field_validator("max_total", mode="before")(amount)

    @field_validator("max_total")
    @classmethod
    def positive(cls, value):
        if Decimal(value) <= 0:
            raise ValueError("Maximum total must be positive.")
        return value

    @field_validator("destination_postal")
    @classmethod
    def postal(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Destination postal code is required.")
        return value.strip().upper()

    @model_validator(mode="after")
    def destination_for_active_target(self):
        if (self.destination_country is None) != (self.destination_postal is None):
            raise ValueError("Destination country and postal code must be supplied together.")
        if self.enabled and self.destination_postal is None:
            raise ValueError("An active target requires a shipping destination.")
        return self


class Observation(StrictInput):
    source: Literal["synthetic", "discogs_public_listing", "discogs_rendered_listing"]
    listing_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    release_id: int = Field(gt=0)
    media: str | None = None
    sleeve: str | None = None
    seller_percent: float | None = Field(default=None, ge=0, le=100)
    feedback: int | None = Field(default=None, ge=0)
    item: str | None = None
    shipping: str | None = None
    tax: str | None = None
    fees: str | None = None
    currency: str | None = None
    destination_country: str | None = None
    destination_postal: str | None = None
    observed_at: str
    available: bool
    pressing_evidence: Literal["confirmed", "unknown", "conflicting"]
    seller_comments: str | None = Field(default=None, max_length=4096)
    seller_comments_supported: bool = True
    release_media_metadata: dict | None = None
    release_media_provenance: dict | None = None

    @field_validator("item", "shipping", "tax", "fees", mode="before")
    @classmethod
    def money(cls, value):
        return None if value is None else amount(value)


def _listing_assessment(target, observation, *, now, already_owned, confirmed_pressing):
    """Shared item filters; quote and action scope are assessed by the caller."""
    reasons = []
    metadata = observation.release_media_metadata
    if observation.source in {"discogs_public_listing", "discogs_rendered_listing"}:
        metadata = current_release_media(
            metadata, observation.release_media_provenance, observation.release_id, now=now
        )
    completeness = assess_media_completeness(
        observation.release_id,
        observation.seller_comments,
        metadata,
        comments_supported=observation.seller_comments_supported,
    )
    defects = assess_media_defects(
        observation.seller_comments, comments_supported=observation.seller_comments_supported
    )
    if completeness["status"] == "incomplete":
        reasons.append("Playable vinyl set is incomplete; missing records are not acceptable.")
    elif completeness["status"] not in {"reported_complete", "no_disclosed_media_omission"}:
        reasons.append("Playable-media omission check is unknown; a full record set is required.")
    if defects["status"] == "defective":
        reasons.append("Seller comments disclose playable-media damage.")
    elif defects["status"] != "no_disclosed_media_defect":
        reasons.append("Playable-media damage check is unknown.")
    if now.tzinfo is None:
        raise ValueError("Evaluation clock must include timezone.")
    try:
        observed = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None or not timedelta(0) <= now - observed <= timedelta(minutes=5):
            reasons.append("Observation is stale, future-dated, or lacks timezone.")
    except ValueError:
        reasons.append("Observation time is invalid.")
    if not target.enabled:
        reasons.append("Target is paused.")
    if already_owned:
        reasons.append("One-copy limit: this release is already marked owned.")
    if not observation.available:
        reasons.append("Listing is not available.")
    if observation.release_id != target.release_id:
        reasons.append("Discogs release ID differs.")
    if confirmed_pressing and observation.pressing_evidence != "confirmed":
        reasons.append(
            "Pressing evidence is unknown or conflicting; release ID alone is insufficient."
        )
    elif observation.pressing_evidence == "conflicting":
        reasons.append("Pressing evidence conflicts with the selected release.")
    for field in ("media", "sleeve"):
        value = getattr(observation, field)
        if field == "sleeve" and value == "Generic" and target.allow_generic_sleeve:
            continue
        if value not in GRADES or GRADES.index(value) < GRADES.index(
            getattr(target, "min_" + field)
        ):
            reasons.append(f"{field.capitalize()} condition is unknown or below minimum.")
    if target.min_seller_percent > 0 and (
        observation.seller_percent is None or observation.seller_percent < target.min_seller_percent
    ):
        reasons.append("Seller rating is unknown or below minimum.")
    if target.min_feedback > 0 and (
        observation.feedback is None or observation.feedback < target.min_feedback
    ):
        reasons.append("Seller feedback count is unknown or below minimum.")
    return reasons, completeness, defects


def evaluate_pre_cart(target: Target, observation: Observation, *, now, already_owned=False):
    """Assess a reversible quote inspection, never purchase qualification.

    The fixed hosted reader has already checked the item's marketplace release
    association. Unknown physical pressing evidence and destination charges may
    be investigated in the cart; conflicting evidence and disclosed item defects
    still block. An explicit scoped cart authorization is required separately.
    """
    reasons, completeness, defects = _listing_assessment(
        target, observation, now=now, already_owned=already_owned, confirmed_pressing=False
    )
    if observation.source != "discogs_rendered_listing":
        reasons.append("Cart inspection requires a verified hosted item observation.")
    if observation.item is None:
        reasons.append("Current item price is unknown.")
    if observation.currency is not None and observation.currency != target.currency:
        reasons.append("Known item currency differs; no automatic conversion.")
    if any(
        getattr(observation, field) is not None
        and getattr(observation, field) != getattr(target, field)
        for field in ("destination_country", "destination_postal")
    ):
        reasons.append("Known shipping destination differs.")
    if (
        observation.currency == target.currency
        and observation.item is not None
        and Decimal(observation.item) > Decimal(target.max_total)
    ):
        reasons.append("Item price alone exceeds your all-in maximum.")
    elif observation.currency == target.currency and sum(
        (
            Decimal(value)
            for field in ("item", "shipping", "tax", "fees")
            if (value := getattr(observation, field)) is not None
        ),
        Decimal(0),
    ) > Decimal(target.max_total):
        reasons.append("Known cost components already exceed your all-in maximum.")
    unresolved = [
        field
        for field in ("currency", "shipping", "tax", "fees")
        if getattr(observation, field) is None
    ]
    if observation.pressing_evidence != "confirmed":
        unresolved.append("physical_pressing")
    if (
        observation.destination_country != target.destination_country
        or observation.destination_postal != target.destination_postal
    ):
        unresolved.append("destination")
    return {
        "eligible_for_cart_inspection": not reasons,
        "media_completeness": completeness,
        "media_defects": defects,
        "reasons": reasons,
        "quote_unresolved": [*unresolved, "final_payable_total"],
        "final_quote_verified": False,
        "purchase_allowed": False,
        "mode": "hosted_cart_inspection_only",
    }


def evaluate(target: Target, observation: Observation, *, now: datetime, already_owned=False):
    """Eligibility is a synthetic rule result, never permission to place an order."""
    reasons, completeness, defects = _listing_assessment(
        target, observation, now=now, already_owned=already_owned, confirmed_pressing=True
    )
    if (
        observation.destination_country != target.destination_country
        or observation.destination_postal != target.destination_postal
    ):
        reasons.append("Shipping destination is unverified or differs.")
    if observation.currency != target.currency:
        reasons.append("Currency is unknown or differs; no automatic conversion.")
    costs = [getattr(observation, name) for name in ("item", "shipping", "tax", "fees")]
    total = None
    if any(value is None for value in costs):
        reasons.append("Item, shipping, tax and mandatory fees must all be known.")
    else:
        total = sum((Decimal(value) for value in costs), Decimal(0))
        if total > Decimal(target.max_total):
            reasons.append("All-in total exceeds your maximum.")
    return {
        "eligible_for_review": not reasons,
        "media_completeness": completeness,
        "media_defects": defects,
        "reasons": reasons,
        "total": format(total, ".2f") if total is not None else None,
        "purchase_allowed": False,
        "mode": "synthetic_dry_run" if observation.source == "synthetic" else "live_read_only",
    }


class RulesStore:
    """Separate tagged local file: never migrates or modifies legacy owner data."""

    def __init__(self, path: Path, *, create=False):
        if path.is_symlink() or path.name.startswith(".env"):
            raise ValueError("Use a regular local workspace file.")
        if create:
            with path.open("x"):
                pass
        elif not path.is_file():
            raise ValueError("Workspace does not exist; use --init for a new file.")
        self.db = sqlite3.connect(str(path))
        if create:
            self.db.executescript("""
                PRAGMA application_id=1178882643;
                CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT INTO state VALUES (1,0);
                CREATE TABLE targets (release_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE owned (release_id INTEGER PRIMARY KEY);
            """)
        if self.db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            self.db.close()
            raise ValueError("Not a Discogs rules workspace. Existing data was not changed.")

    def close(self):
        self.db.close()

    def snapshot(self):
        return {
            "revision": self.db.execute("SELECT revision FROM state").fetchone()[0],
            "targets": [
                json.loads(row[0])
                for row in self.db.execute("SELECT data FROM targets ORDER BY release_id")
            ],
            "owned": [row[0] for row in self.db.execute("SELECT release_id FROM owned")],
            "mode": "dry_run",
            "purchasing_enabled": False,
        }

    def save(self, value, expected_revision):
        target = Target.model_validate(value)
        if type(expected_revision) is not int:
            raise ValueError("Expected revision must be an integer.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.snapshot()["revision"] != expected_revision:
                raise RuntimeError("Saved state changed. Reload before explicitly saving again.")
            self.db.execute(
                "INSERT INTO targets VALUES (?,?) ON CONFLICT(release_id) DO UPDATE "
                "SET data=excluded.data",
                (target.release_id, target.model_dump_json()),
            )
            self.db.execute("UPDATE state SET revision=revision+1")
        return self.snapshot()

    def mark_owned(self, release_id, owned, expected_revision):
        if (
            type(release_id) is not int
            or type(owned) is not bool
            or type(expected_revision) is not int
        ):
            raise ValueError("Invalid ownership update.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.snapshot()["revision"] != expected_revision:
                raise RuntimeError("Saved state changed.")
            if not self.db.execute(
                "SELECT 1 FROM targets WHERE release_id=?", (release_id,)
            ).fetchone():
                raise ValueError("Target not found.")
            if owned:
                self.db.execute("INSERT OR IGNORE INTO owned VALUES (?)", (release_id,))
            else:
                self.db.execute("DELETE FROM owned WHERE release_id=?", (release_id,))
            self.db.execute("UPDATE state SET revision=revision+1")
        return self.snapshot()

    def review(self, release, observation):
        if type(release) is not int:
            raise ValueError("Release ID must be an integer.")
        with self.db:
            self.db.execute("BEGIN")
            revision = self.db.execute("SELECT revision FROM state").fetchone()[0]
            row = self.db.execute(
                "SELECT data FROM targets WHERE release_id=?", (release,)
            ).fetchone()
            if row is None:
                raise ValueError("Save this target before evaluating a synthetic observation.")
            owned = self.db.execute("SELECT 1 FROM owned WHERE release_id=?", (release,)).fetchone()
            result = evaluate(
                Target.model_validate_json(row[0]),
                Observation.model_validate(observation),
                now=datetime.now(UTC),
                already_owned=bool(owned),
            )
            return {**result, "rules_revision": revision}
