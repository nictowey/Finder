"""Durable, opt-in cloud polling. Aggregate availability is never listing discovery.

Only public API GETs are supported. No browser/session, email, cart or order code.
"""

import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from finder.discogs_listing import ListingReadError, listing_id
from finder.discogs_rules import GRADES, RulesStore, Target

RELEASE_INTERVAL = 600
LISTING_INTERVAL = 300
CACHE_TTL = 6 * 3600
MAX_TRACKED = 100
DISCOVERY_BLOCKER = (
    "Automatic release-listing discovery is not connected: no permitted enumeration "
    "transport has been verified. Release counts do not identify new listings."
)


def listing_review(target, listing, *, fresh, owned=False):
    """Triage a reported listing; never invent a delivered quote or verify its pressing."""
    reasons = []
    if not fresh:
        reasons.append("Listing snapshot is stale; wait for a successful refresh.")
    if not target.enabled or owned:
        reasons.append("Target is paused or already marked owned.")
    if listing["release_id"] != target.release_id:
        reasons.append("Reported release ID differs from the target.")
    if listing["available"] is not True:
        reasons.append("Listing is not reported available.")
    for field in ("media", "sleeve"):
        grade = listing[field]
        if grade not in GRADES or GRADES.index(grade) < GRADES.index(
            getattr(target, "min_" + field)
        ):
            reasons.append(f"{field.capitalize()} condition is unknown or below minimum.")
    if listing["seller_percent"] is None or listing["seller_percent"] < target.min_seller_percent:
        reasons.append("Seller rating is unknown or below minimum.")
    if listing["feedback"] is None or listing["feedback"] < target.min_feedback:
        reasons.append("Seller feedback count is unknown or below minimum.")
    if listing["currency"] != target.currency:
        reasons.append("Currency is unknown or differs; no conversion.")
    if listing["item"] is None:
        reasons.append("Item price is unknown.")
    elif Decimal(listing["item"]) > Decimal(target.max_total):
        reasons.append("Item price alone exceeds the all-in ceiling.")
    if listing["shipping_blocked"]:
        reasons.append("Shipping availability is blocked or unverified.")
    return {
        "status": "blocked" if reasons else "needs_total",
        "reasons": reasons
        + [
            "Destination-confirmed shipping, tax and mandatory fees are unknown.",
            "Physical pressing still needs owner review.",
        ],
        "total": None,
        "qualifies_all_in": False,
        "purchase_allowed": False,
    }


class ReleaseMonitor:
    """One bounded request per tick, with persistent rate/cooldown and work reservations.

    Must share the cloud rules database and kill switch. Multiple accidental workers
    cannot claim the same work concurrently or reset the request budget on restart.
    """

    def __init__(self, database, *, stats_reader=None, listing_reader=None, clock=time.time):
        self.database = database
        self.stats_reader, self.listing_reader, self.clock = stats_reader, listing_reader, clock
        store = RulesStore(database)
        try:
            with store.db:
                store.db.executescript("""
                    CREATE TABLE IF NOT EXISTS discogs_monitor_control (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        revision INTEGER NOT NULL, next_request REAL NOT NULL);
                    INSERT OR IGNORE INTO discogs_monitor_control VALUES(1,0,0);
                    CREATE TABLE IF NOT EXISTS discogs_poll (
                        release_id INTEGER NOT NULL,
                        kind TEXT NOT NULL CHECK(kind IN('release','listing')),
                        reference TEXT NOT NULL,
                        next_check REAL NOT NULL DEFAULT 0,
                        last_attempt REAL, last_success REAL,
                        failures INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'waiting',
                        payload TEXT, claim TEXT,
                        PRIMARY KEY(release_id,kind,reference));
                    CREATE TABLE IF NOT EXISTS discogs_monitor_events (
                        sequence INTEGER PRIMARY KEY,
                        at REAL NOT NULL, release_id INTEGER NOT NULL,
                        kind TEXT NOT NULL, reference TEXT NOT NULL);
                """)
        finally:
            store.close()

    @property
    def enabled(self):
        return self.stats_reader is not None

    def read_known(self, value):
        """Manual cloud reads share the worker's durable request budget."""
        ident = listing_id(value)
        if self.listing_reader is None:
            raise ListingReadError("Listing reads are unavailable.")
        store = RulesStore(self.database)
        try:
            now = self.clock()
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                killed = store.db.execute("SELECT killed FROM cloud_control WHERE id=1").fetchone()[
                    0
                ]
                due = store.db.execute(
                    "SELECT next_request FROM discogs_monitor_control WHERE id=1"
                ).fetchone()[0]
                if killed or now < due:
                    raise ListingReadError("Cloud reads are stopped or cooling down.")
                store.db.execute("UPDATE discogs_monitor_control SET next_request=?", (now + 3,))
            try:
                return self.listing_reader.read(ident)
            except ListingReadError as error:
                if error.rate_limited:
                    with store.db:
                        store.db.execute(
                            "UPDATE discogs_monitor_control SET next_request=max(next_request,?)",
                            (self.clock() + min(86400, error.retry_after),),
                        )
                raise
        finally:
            store.close()

    def track(self, store, *, release_id, listing, remove, expected_revision, monitor_revision):
        try:
            ident = str(listing_id(listing))
        except ListingReadError:
            raise ValueError("Use a known Discogs listing ID or canonical item link.") from None
        if type(release_id) is not int or type(remove) is not bool:
            raise ValueError("A saved target and explicit tracking action are required.")
        if type(expected_revision) is not int or type(monitor_revision) is not int:
            raise ValueError("Current rules and monitor revisions are required.")
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            current = store.snapshot()
            revision = store.db.execute(
                "SELECT revision FROM discogs_monitor_control WHERE id=1"
            ).fetchone()[0]
            if current["revision"] != expected_revision or revision != monitor_revision:
                raise RuntimeError("Saved state changed.")
            if not any(t["release_id"] == release_id for t in current["targets"]):
                raise ValueError("Save the release target first.")
            if remove:
                changed = store.db.execute(
                    "DELETE FROM discogs_poll WHERE release_id=? AND kind='listing' "
                    "AND reference=?",
                    (release_id, ident),
                ).rowcount
            else:
                existing = store.db.execute(
                    "SELECT 1 FROM discogs_poll WHERE kind='listing' AND reference=?",
                    (ident,),
                ).fetchone()
                if existing:
                    raise ValueError("That listing is already tracked; remove it before moving it.")
                count = store.db.execute(
                    "SELECT count(*) FROM discogs_poll WHERE kind='listing'"
                ).fetchone()[0]
                if count >= MAX_TRACKED:
                    raise ValueError("Known-listing tracking limit reached.")
                changed = store.db.execute(
                    "INSERT INTO discogs_poll(release_id,kind,reference) VALUES(?,'listing',?)",
                    (release_id, ident),
                ).rowcount
            if changed:
                store.db.execute("UPDATE discogs_monitor_control SET revision=revision+1")
        return self.snapshot(store)

    def _prune(self, store, now):
        store.db.execute(
            "UPDATE discogs_poll SET payload=NULL WHERE last_success < ?", (now - CACHE_TTL,)
        )
        store.db.execute("DELETE FROM discogs_monitor_events WHERE at < ?", (now - CACHE_TTL,))
        store.db.execute(
            "DELETE FROM discogs_monitor_events WHERE sequence NOT IN "
            "(SELECT sequence FROM discogs_monitor_events ORDER BY sequence DESC LIMIT 100)"
        )

    def snapshot(self, store):
        now = self.clock()
        with store.db:
            # Reads also expire old payloads; an idle/stopped monitor cannot serve old data.
            store.db.execute("BEGIN IMMEDIATE")
            self._prune(store, now)
            current = store.snapshot()
            targets = {t["release_id"]: Target.model_validate(t) for t in current["targets"]}
            revision, next_request = store.db.execute(
                "SELECT revision,next_request FROM discogs_monitor_control WHERE id=1"
            ).fetchone()
            rows = []
            for row in store.db.execute(
                "SELECT release_id,kind,reference,last_attempt,last_success,next_check,"
                "status,payload FROM discogs_poll ORDER BY release_id,kind,reference"
            ):
                release, kind, reference, attempted, success, due, status, payload = row
                target = targets.get(release)
                if target is None:
                    continue
                interval = RELEASE_INTERVAL if kind == "release" else LISTING_INTERVAL
                fresh = success is not None and 0 <= now - success <= interval
                data = json.loads(payload) if payload else None
                item = dict(
                    release_id=release,
                    kind=kind,
                    reference=reference,
                    last_attempt=attempted,
                    last_success=success,
                    next_check=due,
                    status=status,
                    fresh=fresh and status == "ok",
                    data=data,
                    paused=not target.enabled or release in current["owned"],
                    rules_revision=current["revision"],
                )
                if kind == "listing" and data:
                    item["review"] = listing_review(
                        target,
                        data,
                        fresh=item["fresh"],
                        owned=release in current["owned"],
                    )
                if kind == "release" and data and data["currency"] != target.currency:
                    item["data"], item["fresh"] = None, False
                rows.append(item)
            events = [
                dict(sequence=r[0], at=r[1], release_id=r[2], kind=r[3], reference=r[4])
                for r in store.db.execute(
                    "SELECT sequence,at,release_id,kind,reference FROM discogs_monitor_events "
                    "ORDER BY sequence DESC LIMIT 30"
                )
                if r[2] in targets
            ]
        return dict(
            configured=self.enabled,
            revision=revision,
            next_request=next_request,
            release_interval_seconds=RELEASE_INTERVAL,
            known_listing_interval_seconds=LISTING_INTERVAL,
            automatic_listing_discovery=False,
            discovery_blocker=DISCOVERY_BLOCKER,
            checkout_available=False,
            email_monitoring=False,
            rows=rows,
            events=events,
        )

    def run_once(self):
        """Return only sanitized operational status. Never retry in the same tick."""
        if not self.enabled:
            return {"status": "not_configured", "requests": 0}
        store = RulesStore(self.database)
        try:
            return self._run(store)
        finally:
            store.close()

    @staticmethod
    def _audit(store, now, outcome):
        store.db.execute(
            "INSERT INTO cloud_audit(at,operation,outcome) VALUES(?,?,?)",
            (datetime.fromtimestamp(now, UTC).isoformat(), "/monitor/poll", outcome),
        )

    def _run(self, store):
        now = self.clock()
        claim = uuid.uuid4().hex
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            killed, control_revision = store.db.execute(
                "SELECT killed,revision FROM cloud_control WHERE id=1"
            ).fetchone()
            self._prune(store, now)
            if killed:
                return {"status": "stopped", "requests": 0}
            current = store.snapshot()
            active = [
                t
                for t in current["targets"]
                if t["enabled"] and t["release_id"] not in current["owned"]
            ]
            for target in active:
                store.db.execute(
                    "INSERT OR IGNORE INTO discogs_poll(release_id,kind,reference) "
                    "VALUES(?,'release',?)",
                    (target["release_id"], str(target["release_id"])),
                )
            next_request = store.db.execute(
                "SELECT next_request FROM discogs_monitor_control WHERE id=1"
            ).fetchone()[0]
            if now < next_request:
                return {"status": "cooldown", "requests": 0}
            ids = {t["release_id"]: Target.model_validate(t) for t in active}
            selected = next(
                (
                    row
                    for row in store.db.execute(
                        "SELECT release_id,kind,reference,failures FROM discogs_poll "
                        "WHERE next_check<=? ORDER BY next_check,release_id,kind,reference",
                        (now,),
                    )
                    if row[0] in ids and (row[1] == "release" or self.listing_reader is not None)
                ),
                None,
            )
            if selected is None:
                return {"status": "idle", "requests": 0}
            release, kind, reference, failures = selected
            key = (release, kind, reference)
            interval = RELEASE_INTERVAL if kind == "release" else LISTING_INTERVAL
            store.db.execute(
                "UPDATE discogs_poll SET last_attempt=?,next_check=?,claim=?,status='checking' "
                "WHERE release_id=? AND kind=? AND reference=?",
                (now, now + interval, claim, *key),
            )
            store.db.execute("UPDATE discogs_monitor_control SET next_request=?", (now + 3,))
            self._audit(store, now, "intent")
        # No SQLite transaction spans network I/O. Stop/pause/edit remain available.
        payload, retry_after, global_pause = None, 0, 0
        try:
            payload = (
                self.stats_reader.read(release, ids[release].currency)
                if kind == "release"
                else self.listing_reader.read(reference)
            )
        except ListingReadError as error:
            retry_after = max(interval, min(86400, error.retry_after), 60 * 2 ** min(failures, 6))
            if error.rate_limited:
                global_pause = min(86400, error.retry_after)
        now = self.clock()
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            # A provider rate limit outlives rule/stop changes, even when its result
            # must be discarded. Releasing a stop must not reset provider cooldown.
            if global_pause:
                store.db.execute(
                    "UPDATE discogs_monitor_control SET next_request=max(next_request,?)",
                    (now + global_pause,),
                )
            control = store.db.execute(
                "SELECT killed,revision FROM cloud_control WHERE id=1"
            ).fetchone()
            previous = store.db.execute(
                "SELECT payload,claim FROM discogs_poll WHERE release_id=? "
                "AND kind=? AND reference=?",
                key,
            ).fetchone()
            unchanged = store.snapshot()["revision"] == current["revision"]
            if not previous or previous[1] != claim:
                self._audit(store, now, "discarded")
                return {"status": "discarded", "requests": 1}
            if control != (0, control_revision) or not unchanged:
                store.db.execute(
                    "UPDATE discogs_poll SET status='discarded',claim=NULL "
                    "WHERE release_id=? AND kind=? AND reference=?",
                    key,
                )
                self._audit(store, now, "discarded")
                return {"status": "discarded", "requests": 1}
            if payload is None:
                store.db.execute(
                    "UPDATE discogs_poll SET status='unavailable',failures=failures+1,"
                    "next_check=?,claim=NULL WHERE release_id=? AND kind=? AND reference=?",
                    (now + retry_after, *key),
                )
                outcome = "unavailable"
            else:
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
                # No timestamp in the fingerprint: unchanged observations never spam the inbox.
                if previous[0] != encoded:
                    store.db.execute(
                        "INSERT INTO discogs_monitor_events(at,release_id,kind,reference) "
                        "VALUES(?,?,?,?)",
                        (now, *key),
                    )
                store.db.execute(
                    "UPDATE discogs_poll SET status='ok',failures=0,payload=?,last_success=?,"
                    "next_check=?,claim=NULL WHERE release_id=? AND kind=? AND reference=?",
                    (encoded, now, now + interval, *key),
                )
                outcome = "ok"
            self._audit(store, now, outcome)
        return {"status": outcome, "requests": 1}


class MonitorLoop:
    """Optional daemon for one cloud process; no browser or external scheduler needed."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="discogs-monitor", daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                self.monitor.run_once()
            except (sqlite3.Error, ValueError, RuntimeError):
                # Keep raw provider/DB payloads out of cloud logs. Pending claims expire;
                # a failed job is not evidence that a release has no copies for sale.
                pass
            self.stop.wait(3)
