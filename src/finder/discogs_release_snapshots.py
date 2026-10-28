"""Bounded imports of already observed exact-release DOM, not a discovery transport.

Nothing here fetches, navigates, authenticates, schedules or notifies. Source assertions
are supplied evidence, not proof of a live or authenticated Discogs session. Listing
IDs identify observations, never a physical pressing, sale or destination quote.
"""

import json
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from finder.discogs_rules import RulesStore, StrictInput

MAX_ROWS = 250
MAX_BYTES = 256 * 1024
MAX_RELEASES = 100
MAX_CHANGES = 500
MAX_SEEN = 1000
RETENTION_SECONDS = 30 * 86400
MAX_AGE_SECONDS = 300
MAX_ID = 9007199254740991
FIELD_NAMES = (
    "price",
    "media",
    "sleeve",
    "seller",
    "seller_handle",
    "seller_rating",
    "seller_feedback",
    "shipping",
    "seller_description",
)
WARNING = (
    "Imported displayed observations, not verified live/authenticated data. Disappearance "
    "is not a sale; a new listing ID can be a relisting. Physical pressing, availability "
    "and destination-confirmed shipping, tax and fees remain unverified."
)


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise ValueError("Observation time must be an ISO UTC timestamp.") from None


def _iso(value):
    return None if value is None else datetime.fromtimestamp(value, UTC).isoformat()


class DisplayedFields(StrictInput):
    price: str | None = Field(max_length=160)
    media: str | None = Field(max_length=160)
    sleeve: str | None = Field(max_length=160)
    seller: str | None = Field(max_length=120)
    seller_handle: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    seller_rating: str | None = Field(max_length=160)
    seller_feedback: str | None = Field(max_length=160)
    shipping: str | None = Field(max_length=240)
    seller_description: str | None = Field(default=None, max_length=2000)

    @field_validator(*FIELD_NAMES)
    @classmethod
    def displayed_text(cls, value):
        if value is None:
            return None
        if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            raise ValueError("Displayed fields cannot contain control characters.")
        return " ".join(value.split()) or None


class RenderedListing(StrictInput):
    listing_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    url: str = Field(max_length=120)
    fields: DisplayedFields

    @model_validator(mode="after")
    def identity(self):
        if int(self.listing_id) > MAX_ID:
            raise ValueError("Listing ID is outside the supported range.")
        if self.url != f"https://www.discogs.com/sell/item/{self.listing_id}":
            raise ValueError("Listing link must match its canonical listing ID.")
        return self


class DOMAssertions(StrictInput):
    release_identity: bool
    listing_container: bool
    pagination: bool
    stable: bool


class RenderedReleaseSnapshot(StrictInput):
    source: Literal["discogs_rendered_release"]
    purchase_allowed: Literal[False] = False
    release_id: int = Field(gt=0, le=MAX_ID)
    url: str = Field(max_length=120)
    observed_at: str = Field(min_length=20, max_length=40)
    status: Literal["observed", "incomplete", "error"]
    complete: bool
    range_start: int | None = Field(ge=0, le=10000000)
    range_end: int | None = Field(ge=0, le=10000000)
    total: int | None = Field(ge=0, le=10000000)
    page: int | None = Field(ge=1, le=10000000)
    coverage_pages: int = Field(default=1, ge=1, le=100)
    has_next: bool | None
    rows: list[RenderedListing] = Field(max_length=MAX_ROWS)
    assertions: DOMAssertions
    error_code: str | None = Field(default=None, pattern=r"^[a-z_]{1,48}$")

    @field_validator("purchase_allowed", mode="before")
    @classmethod
    def cannot_purchase(cls, value):
        if value is not False:
            raise ValueError("Purchasing is unavailable.")
        return value

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value):
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def identity_and_completeness(self):
        if self.url not in (
            f"https://www.discogs.com/sell/release/{self.release_id}",
            f"https://www.discogs.com/sell/release/{self.release_id}/",
        ):
            raise ValueError("Only the selected canonical release marketplace URL is supported.")
        ids = [row.listing_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate listing IDs make the observation ambiguous.")
        if self.complete:
            if (
                self.status != "observed"
                or self.error_code is not None
                or not all(self.assertions.model_dump().values())
                or self.page != 1
                or self.has_next is not False
                or self.total != len(self.rows)
                or self.range_start != (1 if self.rows else 0)
                or self.range_end != len(self.rows)
            ):
                raise ValueError(
                    "Complete observations require verified coverage of every displayed offer."
                )
        elif self.status == "observed":
            raise ValueError("Observed status requires an explicitly complete snapshot.")
        elif self.rows and (
            self.status != "incomplete"
            or self.error_code != "additional_page_required"
            or not all(self.assertions.model_dump().values())
            or self.page != 1
            or self.has_next is not True
            or self.range_start != 1
            or self.range_end != len(self.rows)
            or self.total is None
            or self.total <= len(self.rows)
        ):
            raise ValueError("Partial rows require a stable, verified first page with more offers.")
        return self


class RenderedReleasePage(StrictInput):
    """One validated page for offline assembly; this does not fetch another page."""

    source: Literal["discogs_rendered_release"]
    purchase_allowed: Literal[False]
    release_id: int = Field(gt=0, le=MAX_ID)
    url: str = Field(max_length=160)
    observed_at: str
    page: int = Field(ge=1, le=100)
    range_start: int = Field(ge=0, le=MAX_ROWS)
    range_end: int = Field(ge=0, le=MAX_ROWS)
    total: int = Field(ge=0, le=MAX_ROWS)
    has_next: bool
    rows: list[RenderedListing] = Field(max_length=MAX_ROWS)
    assertions: DOMAssertions

    @model_validator(mode="after")
    def verified(self):
        _timestamp(self.observed_at)
        expected = f"https://www.discogs.com/sell/release/{self.release_id}"
        if self.url not in (expected, expected + "/", expected + f"?page={self.page}"):
            raise ValueError("Page URL must identify the selected release and page.")
        if self.page > 1 and self.url != expected + f"?page={self.page}":
            raise ValueError("Later pages need an explicit page URL.")
        if not all(self.assertions.model_dump().values()):
            raise ValueError("Every page must have stable identity and pagination assertions.")
        if (self.total == 0 and (self.range_start, self.range_end, self.page) != (0, 0, 1)) or (
            self.total > 0 and self.range_start < 1
        ):
            raise ValueError("Invalid page range.")
        if self.range_end - self.range_start + (1 if self.total else 0) != len(self.rows):
            raise ValueError("Page row count does not match displayed range.")
        if self.range_end > self.total or self.has_next != (self.range_end < self.total):
            raise ValueError("Page coverage is inconsistent.")
        if len({row.listing_id for row in self.rows}) != len(self.rows):
            raise ValueError("Duplicate listing IDs on page.")
        return self


def assemble_release_pages(release_id: int, pages: list[dict]) -> dict:
    """Assemble already captured pages only when they cover one exact release without gaps."""
    if type(release_id) is not int or not 0 < release_id <= MAX_ID or not pages or len(pages) > 100:
        raise ValueError("A bounded exact-release page set is required.")
    parsed = [RenderedReleasePage.model_validate(page) for page in pages]
    first = parsed[0]
    if first.page != 1 or first.range_start != (1 if first.total else 0):
        raise ValueError("Page one must start the observation.")
    rows: list[RenderedListing] = []
    seen: set[str] = set()
    for index, page in enumerate(parsed, start=1):
        if (
            page.release_id != release_id
            or page.total != first.total
            or page.page != index
            or page.range_start != (len(rows) + 1 if first.total else 0)
            or page.has_next != (index < len(parsed))
        ):
            raise ValueError("Pages must be contiguous, from the same release and complete.")
        if abs(_timestamp(page.observed_at) - _timestamp(first.observed_at)) > MAX_AGE_SECONDS:
            raise ValueError("Pages were captured too far apart.")
        for row in page.rows:
            if row.listing_id in seen:
                raise ValueError("Listing repeated across pages.")
            seen.add(row.listing_id)
            rows.append(row)
    if len(rows) != first.total or len(rows) > MAX_ROWS:
        raise ValueError("Page set does not cover the displayed total within the safety cap.")
    return RenderedReleaseSnapshot.model_validate(
        {
            "source": "discogs_rendered_release",
            "purchase_allowed": False,
            "release_id": release_id,
            "url": f"https://www.discogs.com/sell/release/{release_id}",
            "observed_at": first.observed_at,
            "status": "observed",
            "complete": True,
            "range_start": first.range_start,
            "range_end": len(rows),
            "total": first.total,
            "page": 1,
            "coverage_pages": len(parsed),
            "has_next": False,
            "rows": [row.model_dump() for row in rows],
            "assertions": first.assertions.model_dump(),
        }
    ).model_dump()


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("Explicit nonnegative integer revisions are required.")


class ReleaseSnapshots:
    """Durable successful baselines and separate acknowledgment ledger in the rules DB."""

    def __init__(self, database, *, clock=time.time):
        self.database, self.clock = database, clock
        store = RulesStore(database)
        try:
            with store.db:
                store.db.executescript("""
                    CREATE TABLE IF NOT EXISTS discogs_release_baselines (
                        release_id INTEGER PRIMARY KEY,
                        revision INTEGER NOT NULL DEFAULT 0,
                        baseline_at REAL, baseline TEXT,
                        attempted_at REAL NOT NULL,
                        status TEXT NOT NULL, error_code TEXT);
                    CREATE TABLE IF NOT EXISTS discogs_release_seen (
                        release_id INTEGER NOT NULL, listing_id TEXT NOT NULL,
                        first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                        present INTEGER NOT NULL CHECK(present IN(0,1)),
                        PRIMARY KEY(release_id,listing_id));
                    CREATE TABLE IF NOT EXISTS discogs_release_changes (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL, revision INTEGER NOT NULL,
                        observed_at REAL NOT NULL, kind TEXT NOT NULL,
                        listing_id TEXT NOT NULL, fields TEXT NOT NULL,
                        UNIQUE(release_id,revision,kind,listing_id));
                    CREATE TABLE IF NOT EXISTS discogs_release_acknowledged (
                        sequence INTEGER PRIMARY KEY, acknowledged_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS discogs_monitor_releases (
                        release_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL
                        CHECK(enabled IN (0,1)));
                """)
        finally:
            store.close()

    @staticmethod
    def _guard(store, release, rules_revision, control_revision, baseline_revision):
        for revision in (rules_revision, control_revision, baseline_revision):
            _revision(revision)
        current = store.snapshot()
        control = store.db.execute(
            "SELECT killed,revision FROM cloud_control WHERE id=1"
        ).fetchone()
        if not control or control[0] or control[1] != control_revision:
            raise RuntimeError("Cloud reads stopped or control revision changed.")
        if current["revision"] != rules_revision:
            raise RuntimeError("Rules revision changed.")
        target = next((t for t in current["targets"] if t["release_id"] == release), None)
        monitor = store.db.execute(
            "SELECT enabled FROM discogs_monitor_releases WHERE release_id=?", (release,)
        ).fetchone()
        buying_target_active = (
            target is not None and target["enabled"] and release not in current["owned"]
        )
        if not buying_target_active and not (monitor and monitor[0]):
            raise ValueError("Save an active exact-release target before importing.")
        previous = store.db.execute(
            "SELECT revision,baseline_at,baseline,attempted_at FROM discogs_release_baselines "
            "WHERE release_id=?",
            (release,),
        ).fetchone()
        if (previous[0] if previous else 0) != baseline_revision:
            raise RuntimeError("Baseline revision changed. Re-observe before importing again.")
        return previous

    @staticmethod
    def _prune(store, now):
        # The latest complete baseline is durable, even after a failed or long-delayed read.
        # Historical IDs/events are bounded; expiry is not a claim about marketplace state.
        for table in (
            "discogs_release_baselines",
            "discogs_release_seen",
            "discogs_release_changes",
        ):
            store.db.execute(
                f"DELETE FROM {table} WHERE release_id NOT IN "
                "(SELECT release_id FROM targets UNION "
                "SELECT release_id FROM discogs_monitor_releases)"
            )
        store.db.execute(
            "DELETE FROM discogs_release_seen WHERE present=0 AND last_seen<?",
            (now - RETENTION_SECONDS,),
        )
        store.db.execute(
            "DELETE FROM discogs_release_changes WHERE observed_at<?",
            (now - RETENTION_SECONDS,),
        )
        store.db.execute(
            "DELETE FROM discogs_release_changes WHERE sequence NOT IN "
            "(SELECT sequence FROM discogs_release_changes ORDER BY sequence DESC LIMIT ?)",
            (MAX_CHANGES,),
        )
        store.db.execute(
            "DELETE FROM discogs_release_acknowledged WHERE sequence NOT IN "
            "(SELECT sequence FROM discogs_release_changes)"
        )

    def ingest(
        self,
        snapshot,
        *,
        expected_rules_revision,
        expected_control_revision,
        expected_baseline_revision,
        expected_schedule_revision=None,
        expected_schedule_token=None,
    ):
        """Import one observation with revisions captured before observation, never refreshed.

        Invalid input raises without writes. A valid incomplete/error attempt records only
        status and its monotonic observation watermark, never zero inventory or a new baseline.
        """
        parsed = RenderedReleaseSnapshot.model_validate(snapshot)
        encoded = parsed.model_dump_json()
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError("Snapshot exceeds the bounded import size.")
        observed = _timestamp(parsed.observed_at)
        now = self.clock()
        if not math.isfinite(now) or not 0 <= now - observed <= MAX_AGE_SECONDS:
            raise ValueError(
                "Observation is stale or future-dated; re-observe the selected release."
            )
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                previous = self._guard(
                    store,
                    parsed.release_id,
                    expected_rules_revision,
                    expected_control_revision,
                    expected_baseline_revision,
                )
                if expected_schedule_revision is not None:
                    _revision(expected_schedule_revision)
                    schedule = store.db.execute(
                        "SELECT paused,revision,lease_token FROM discogs_hosted_schedule WHERE id=1"
                    ).fetchone()
                    if schedule != (0, expected_schedule_revision, expected_schedule_token):
                        raise RuntimeError("Hosted schedule changed during observation.")
                if previous and observed <= previous[3]:
                    raise RuntimeError("Observation is duplicate or out of order.")
                if (
                    previous is None
                    and store.db.execute(
                        "SELECT count(*) FROM discogs_release_baselines"
                    ).fetchone()[0]
                    >= MAX_RELEASES
                ):
                    raise ValueError("Selected-release snapshot limit reached.")
                self._prune(store, now)
                if not parsed.complete:
                    store.db.execute(
                        "INSERT INTO discogs_release_baselines "
                        "(release_id,attempted_at,status,error_code) VALUES(?,?,?,?) "
                        "ON CONFLICT(release_id) DO UPDATE SET attempted_at=excluded.attempted_at,"
                        "status=excluded.status,error_code=excluded.error_code",
                        (parsed.release_id, observed, parsed.status, parsed.error_code),
                    )
                    return self._result(
                        parsed.release_id, expected_baseline_revision, "preserved", []
                    )
                old_rows = (
                    {row["listing_id"]: row for row in json.loads(previous[2])["rows"]}
                    if previous and previous[2]
                    else None
                )
                new_rows = {row.listing_id: row.model_dump() for row in parsed.rows}
                revision = expected_baseline_revision + 1
                changes = self._diff(store, parsed.release_id, old_rows, new_rows)
                sequences = []
                for kind, ident, fields in changes:
                    result = store.db.execute(
                        "INSERT INTO discogs_release_changes "
                        "(release_id,revision,observed_at,kind,listing_id,fields) "
                        "VALUES(?,?,?,?,?,?)",
                        (parsed.release_id, revision, observed, kind, ident, json.dumps(fields)),
                    )
                    sequences.append(result.lastrowid)
                store.db.execute(
                    "INSERT INTO discogs_release_baselines "
                    "(release_id,revision,baseline_at,baseline,attempted_at,status) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(release_id) DO UPDATE SET revision=excluded.revision,"
                    "baseline_at=excluded.baseline_at,baseline=excluded.baseline,"
                    "attempted_at=excluded.attempted_at,status=excluded.status,error_code=NULL",
                    (parsed.release_id, revision, observed, encoded, observed, "observed"),
                )
                self._remember_ids(store, parsed.release_id, new_rows, observed)
                self._prune(store, now)
                status = "baseline" if old_rows is None else "changed" if changes else "unchanged"
                return self._result(parsed.release_id, revision, status, sequences)
        finally:
            store.close()

    @staticmethod
    def _diff(store, release, old, new):
        if old is None:
            return []  # Initial baseline is not a new-listing alert.
        seen = {
            row[0]
            for row in store.db.execute(
                "SELECT listing_id FROM discogs_release_seen WHERE release_id=?", (release,)
            )
        }
        changes = []
        for ident in sorted(new.keys() - old.keys(), key=int):
            changes.append(("reappeared" if ident in seen else "new", ident, {}))
        for ident in sorted(old.keys() - new.keys(), key=int):
            changes.append(("disappeared", ident, {}))
        for ident in sorted(old.keys() & new.keys(), key=int):
            fields = {
                field: {
                    "before": old[ident]["fields"].get(field),
                    "after": new[ident]["fields"].get(field),
                }
                for field in FIELD_NAMES
                if old[ident]["fields"].get(field) is not None
                and new[ident]["fields"].get(field) is not None
                and old[ident]["fields"].get(field) != new[ident]["fields"].get(field)
            }
            if fields:
                changes.append(("changed", ident, fields))
        return changes

    @staticmethod
    def _remember_ids(store, release, rows, observed):
        store.db.execute("UPDATE discogs_release_seen SET present=0 WHERE release_id=?", (release,))
        for ident in rows:
            store.db.execute(
                "INSERT INTO discogs_release_seen VALUES(?,?,?,?,1) "
                "ON CONFLICT(release_id,listing_id) DO UPDATE "
                "SET last_seen=excluded.last_seen,present=1",
                (release, ident, observed, observed),
            )
        store.db.execute(
            "DELETE FROM discogs_release_seen WHERE release_id=? AND listing_id NOT IN "
            "(SELECT listing_id FROM discogs_release_seen WHERE release_id=? "
            "ORDER BY present DESC,last_seen DESC,listing_id DESC LIMIT ?)",
            (release, release, MAX_SEEN),
        )

    @staticmethod
    def _result(release, revision, status, changes):
        return dict(
            release_id=release,
            baseline_revision=revision,
            status=status,
            change_ids=changes,
            notifications_sent=0,
            purchase_allowed=False,
            qualifies_all_in=False,
            pressing_verified=False,
            source_verified=False,
        )

    def snapshot(self, store=None):
        own_store = store is None
        store = RulesStore(self.database) if own_store else store
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                self._prune(store, now)
                current = store.snapshot()
                control = store.db.execute(
                    "SELECT killed,revision FROM cloud_control WHERE id=1"
                ).fetchone()
                saved = {
                    row[0]: row
                    for row in store.db.execute(
                        "SELECT release_id,revision,baseline_at,baseline,attempted_at,"
                        "status,error_code "
                        "FROM discogs_release_baselines"
                    )
                }
                releases = []
                targets = {t["release_id"]: t for t in current["targets"]}
                monitor_enabled = {}
                for release, enabled in store.db.execute(
                    "SELECT release_id,enabled FROM discogs_monitor_releases"
                ):
                    monitor_enabled[release] = bool(enabled)
                    targets.setdefault(release, {"release_id": release, "enabled": bool(enabled)})
                for target in targets.values():
                    release = target["release_id"]
                    row = saved.get(release)
                    baseline = json.loads(row[3]) if row and row[3] else None
                    if baseline is not None:
                        for listing in baseline["rows"]:
                            listing["fields"].setdefault("seller_description", None)
                    releases.append(
                        dict(
                            release_id=release,
                            baseline_revision=row[1] if row else 0,
                            baseline=baseline,
                            baseline_observed_at=_iso(row[2]) if row else None,
                            last_attempt_observed_at=_iso(row[4]) if row else None,
                            last_status=row[5] if row else "waiting",
                            error_code=row[6] if row else None,
                            fresh=bool(
                                row
                                and row[2] is not None
                                and row[5] == "observed"
                                and 0 <= now - row[2] <= MAX_AGE_SECONDS
                            ),
                            import_allowed=bool(
                                control
                                and not control[0]
                                and (
                                    monitor_enabled.get(release, False)
                                    or (target["enabled"] and release not in current["owned"])
                                )
                            ),
                        )
                    )
                changes = [
                    dict(
                        sequence=row[0],
                        release_id=row[1],
                        baseline_revision=row[2],
                        observed_at=_iso(row[3]),
                        kind=row[4],
                        listing_id=row[5],
                        url=f"https://www.discogs.com/sell/item/{row[5]}",
                        fields=json.loads(row[6]),
                        acknowledged=row[7] is not None,
                        purchase_allowed=False,
                        qualifies_all_in=False,
                        pressing_verified=False,
                    )
                    for row in store.db.execute(
                        "SELECT c.sequence,c.release_id,c.revision,c.observed_at,"
                        "c.kind,c.listing_id,"
                        "c.fields,a.acknowledged_at FROM discogs_release_changes c "
                        "LEFT JOIN discogs_release_acknowledged a ON a.sequence=c.sequence "
                        "ORDER BY c.sequence DESC"
                    )
                ]
                return dict(
                    mode="imported_rendered_observations",
                    source_verified=False,
                    automatic_listing_discovery=False,
                    notifications_enabled=False,
                    purchase_allowed=False,
                    qualifies_all_in=False,
                    warning=WARNING,
                    rules_revision=current["revision"],
                    control_revision=control[1] if control else None,
                    releases=releases,
                    changes=changes,
                    pending_change_ids=[
                        row["sequence"] for row in changes if not row["acknowledged"]
                    ],
                    retention_days=30,
                    max_rows=MAX_ROWS,
                )
        finally:
            if own_store:
                store.close()

    def acknowledge(self, change_ids, *, expected_rules_revision, expected_control_revision):
        """Record private in-app review acknowledgment only; no notification is sent.

        This separate ledger never advances or clears a snapshot baseline. Repeated
        acknowledgments are idempotent. Missing/expired IDs fail rather than imply delivery.
        """
        for revision in (expected_rules_revision, expected_control_revision):
            _revision(revision)
        if (
            not isinstance(change_ids, list)
            or not 1 <= len(change_ids) <= MAX_CHANGES
            or any(type(value) is not int or value <= 0 for value in change_ids)
            or len(change_ids) != len(set(change_ids))
        ):
            raise ValueError("Use a bounded list of distinct retained change IDs.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                self._prune(store, self.clock())
                current = store.snapshot()
                control = store.db.execute(
                    "SELECT revision FROM cloud_control WHERE id=1"
                ).fetchone()
                if (
                    current["revision"] != expected_rules_revision
                    or not control
                    or control[0] != expected_control_revision
                ):
                    raise RuntimeError("Rules or control revision changed.")
                for sequence in change_ids:
                    if not store.db.execute(
                        "SELECT 1 FROM discogs_release_changes WHERE sequence=?", (sequence,)
                    ).fetchone():
                        raise ValueError(
                            "A change is unavailable or expired. Refresh before review."
                        )
                    store.db.execute(
                        "INSERT OR IGNORE INTO discogs_release_acknowledged VALUES(?,?)",
                        (sequence, self.clock()),
                    )
                return {"acknowledged_change_ids": change_ids, "notifications_sent": 0}
        finally:
            store.close()
