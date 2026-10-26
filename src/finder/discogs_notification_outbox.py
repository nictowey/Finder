"""Durable notification handoff contract for retained Sniper change events.

This module has no network transport, provider credentials, or send method. An
accepted provider result means provider acceptance only, never device delivery.
The installed/default provider is unavailable, so nothing can be claimed by
the deployed application until a separate provider integration is reviewed.
"""

import hashlib
import re
import time
import uuid
from dataclasses import dataclass

from finder.discogs_change_outbox import InAppChangeOutbox
from finder.discogs_rules import RulesStore

MAX_ATTEMPTS = 5
BASE_BACKOFF = 15 * 60
MAX_BACKOFF = 24 * 60 * 60
LEASE_SECONDS = 120
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_EVENTS = 500
OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass(frozen=True)
class NotificationBinding:
    provider_id: str
    destination_ref: str

    def __post_init__(self):
        if (
            not isinstance(self.provider_id, str)
            or not PROVIDER_ID.fullmatch(self.provider_id)
            or self.provider_id == "unavailable"
            or not isinstance(self.destination_ref, str)
            or not OPAQUE_REF.fullmatch(self.destination_ref)
        ):
            raise ValueError("Use a named provider and opaque destination reference, not a secret.")


@dataclass(frozen=True)
class ProviderCapability:
    provider_id: str = "unavailable"
    available: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.provider_id, str)
            or not PROVIDER_ID.fullmatch(self.provider_id)
            or type(self.available) is not bool
        ):
            raise ValueError("Invalid provider capability.")


UNAVAILABLE_PROVIDER = ProviderCapability()


def _event_key(release_id, revision, kind, listing_id):
    source = f"sniper-change-v1\0{release_id}\0{revision}\0{kind}\0{listing_id}"
    return hashlib.sha256(source.encode("ascii")).hexdigest()


class NotificationOutbox:
    """Transactional state only. The caller is responsible for a future sender."""

    def __init__(self, database, *, clock=time.time):
        self.database, self.clock = database, clock
        self.in_app = InAppChangeOutbox(database, clock=clock)
        store = RulesStore(database)
        try:
            with store.db:
                store.db.execute(
                    "CREATE TABLE IF NOT EXISTS discogs_notification_outbox ("
                    "event_key TEXT PRIMARY KEY, change_sequence INTEGER NOT NULL UNIQUE, "
                    "release_id INTEGER NOT NULL, baseline_revision INTEGER NOT NULL, "
                    "kind TEXT NOT NULL, listing_id TEXT NOT NULL, "
                    "provider_id TEXT NOT NULL, destination_ref TEXT NOT NULL, "
                    "state TEXT NOT NULL CHECK(state IN "
                    "('pending','leased','retry_wait','uncertain',"
                    "'accepted','failed','expired','revoked')), "
                    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL, "
                    "lease_token TEXT, lease_until REAL, last_error TEXT, "
                    "enqueued_at REAL NOT NULL, updated_at REAL NOT NULL)"
                )
        finally:
            store.close()

    def enqueue(self, binding, *, expected_rules_revision, expected_control_revision):
        """Bind retained in-app changes once; never reroute an existing event."""
        if not isinstance(binding, NotificationBinding):
            raise ValueError("An explicit opaque notification binding is required.")
        self.in_app.materialize(
            expected_rules_revision=expected_rules_revision,
            expected_control_revision=expected_control_revision,
        )
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                self.in_app._check_revisions(
                    store, expected_rules_revision, expected_control_revision
                )
                now = self.clock()
                rows = store.db.execute(
                    "SELECT c.sequence,c.release_id,c.revision,c.kind,c.listing_id "
                    "FROM discogs_release_changes c JOIN discogs_change_outbox a "
                    "ON a.change_sequence=c.sequence "
                    "LEFT JOIN discogs_release_acknowledged reviewed "
                    "ON reviewed.sequence=c.sequence "
                    "WHERE reviewed.sequence IS NULL ORDER BY c.sequence DESC LIMIT ?",
                    (MAX_EVENTS,),
                ).fetchall()
                for sequence, release, revision, kind, listing in rows:
                    key = _event_key(release, revision, kind, listing)
                    old = store.db.execute(
                        "SELECT provider_id,destination_ref,event_key,state "
                        "FROM discogs_notification_outbox WHERE change_sequence=?",
                        (sequence,),
                    ).fetchone()
                    if old and old[:3] != (binding.provider_id, binding.destination_ref, key):
                        if old[3] not in {"accepted", "uncertain", "failed", "expired", "revoked"}:
                            raise RuntimeError("An event is already bound to another destination.")
                        continue
                    store.db.execute(
                        "INSERT OR IGNORE INTO discogs_notification_outbox "
                        "(event_key,change_sequence,release_id,baseline_revision,kind,listing_id,"
                        "provider_id,destination_ref,state,next_attempt,enqueued_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?)",
                        (
                            key,
                            sequence,
                            release,
                            revision,
                            kind,
                            listing,
                            binding.provider_id,
                            binding.destination_ref,
                            now,
                            now,
                            now,
                        ),
                    )
                return store.db.execute(
                    "SELECT count(*) FROM discogs_notification_outbox "
                    "WHERE provider_id=? AND destination_ref=?",
                    (binding.provider_id, binding.destination_ref),
                ).fetchone()[0]
        finally:
            store.close()

    @staticmethod
    def _reconcile(store, now):
        saved = store.snapshot()
        active_releases = {
            target["release_id"]
            for target in saved["targets"]
            if target["enabled"] and target["release_id"] not in saved["owned"]
        }
        active_releases.update(
            release
            for (release,) in store.db.execute(
                "SELECT release_id FROM discogs_monitor_releases WHERE enabled=1"
            )
        )
        for (release,) in store.db.execute(
            "SELECT DISTINCT release_id FROM discogs_notification_outbox "
            "WHERE state IN ('pending','retry_wait','leased')"
        ):
            if release not in active_releases:
                store.db.execute(
                    "UPDATE discogs_notification_outbox SET state='uncertain',"
                    "last_error='target_paused',updated_at=? "
                    "WHERE release_id=? AND state='leased'",
                    (now, release),
                )
                store.db.execute(
                    "UPDATE discogs_notification_outbox SET state='revoked',updated_at=? "
                    "WHERE release_id=? AND state IN ('pending','retry_wait')",
                    (now, release),
                )
        store.db.execute(
            "UPDATE discogs_notification_outbox SET state='uncertain',lease_token=NULL,"
            "lease_until=NULL,last_error='lease_expired',updated_at=? "
            "WHERE state='leased' AND lease_until<=?",
            (now, now),
        )
        store.db.execute(
            "UPDATE discogs_notification_outbox SET state='uncertain',updated_at=?,"
            "last_error='reviewed_while_leased' WHERE state='leased' AND change_sequence IN "
            "(SELECT sequence FROM discogs_release_acknowledged)",
            (now,),
        )
        store.db.execute(
            "UPDATE discogs_notification_outbox SET state='revoked',updated_at=? "
            "WHERE state IN ('pending','retry_wait') AND change_sequence IN "
            "(SELECT sequence FROM discogs_release_acknowledged)",
            (now,),
        )
        store.db.execute(
            "UPDATE discogs_notification_outbox SET state='expired',updated_at=? "
            "WHERE state IN ('pending','retry_wait') AND change_sequence NOT IN "
            "(SELECT sequence FROM discogs_release_changes)",
            (now,),
        )
        store.db.execute(
            "DELETE FROM discogs_notification_outbox "
            "WHERE state IN ('accepted','expired','revoked') "
            "AND updated_at<? AND change_sequence NOT IN "
            "(SELECT sequence FROM discogs_release_changes)",
            (now - RETENTION_SECONDS,),
        )

    def reconcile(self):
        """Turn expired in-flight claims into manual-review uncertain outcomes."""
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                self._reconcile(store, self.clock())
        finally:
            store.close()

    def claim(self, binding, provider=UNAVAILABLE_PROVIDER):
        """Reserve a due event without sending. Unavailable provider claims nothing."""
        if not isinstance(binding, NotificationBinding) or not isinstance(
            provider, ProviderCapability
        ):
            raise ValueError("A verified provider capability and binding are required.")
        if not provider.available or provider.provider_id != binding.provider_id:
            return None
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                self._reconcile(store, now)
                control_table = store.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_control'"
                ).fetchone()
                control = (
                    store.db.execute("SELECT killed FROM cloud_control WHERE id=1").fetchone()
                    if control_table
                    else None
                )
                if control is None or control[0]:
                    return None
                row = store.db.execute(
                    "SELECT event_key,change_sequence,release_id,kind,attempts "
                    "FROM discogs_notification_outbox WHERE provider_id=? AND destination_ref=? "
                    "AND state IN ('pending','retry_wait') AND next_attempt<=? "
                    "AND attempts<? ORDER BY next_attempt,change_sequence LIMIT 1",
                    (binding.provider_id, binding.destination_ref, now, MAX_ATTEMPTS),
                ).fetchone()
                if row is None:
                    return None
                token = uuid.uuid4().hex
                store.db.execute(
                    "UPDATE discogs_notification_outbox SET state='leased',attempts=attempts+1,"
                    "lease_token=?,lease_until=?,updated_at=? WHERE event_key=?",
                    (token, now + LEASE_SECONDS, now, row[0]),
                )
                return {
                    "event_key": row[0],
                    "change_sequence": row[1],
                    "provider_id": binding.provider_id,
                    "destination_ref": binding.destination_ref,
                    "attempt_token": token,
                    "attempt": row[4] + 1,
                    "payload": {
                        "title": "Sniper listing update",
                        "body": "A monitored release changed. Open private Sniper to review.",
                        "event_key": row[0],
                        "purchase_allowed": False,
                    },
                    "sent": False,
                }
        finally:
            store.close()

    def _finish(self, claim, next_state, *, error_code=None, retry_after_seconds=0):
        if not isinstance(claim, dict) or set(claim) != {
            "event_key",
            "attempt_token",
            "provider_id",
            "destination_ref",
        }:
            raise ValueError("Use the exact reserved event and binding.")
        if (
            not isinstance(claim["event_key"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", claim["event_key"])
            or not isinstance(claim["attempt_token"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", claim["attempt_token"])
            or not isinstance(claim["provider_id"], str)
            or not isinstance(claim["destination_ref"], str)
            or type(retry_after_seconds) is not int
            or not 0 <= retry_after_seconds <= MAX_BACKOFF
        ):
            raise ValueError("Invalid reserved event result.")
        store = RulesStore(self.database)
        try:
            failure = None
            result = None
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                self._reconcile(store, now)
                row = store.db.execute(
                    "SELECT state,attempts,lease_token,lease_until "
                    "FROM discogs_notification_outbox "
                    "WHERE event_key=? AND provider_id=? AND destination_ref=?",
                    (claim["event_key"], claim["provider_id"], claim["destination_ref"]),
                ).fetchone()
                if row is None or row[2] != claim["attempt_token"]:
                    failure = "Claim changed; outcome is unknown. Reconcile before retrying."
                elif row[0] == next_state and next_state in ("accepted", "uncertain"):
                    result = {"state": next_state, "delivered": False}
                elif row[0] != "leased" or now >= row[3]:
                    failure = "Claim expired; outcome is uncertain. Do not retry automatically."
                elif next_state == "retry_wait":
                    if not isinstance(error_code, str) or not ERROR_CODE.fullmatch(error_code):
                        raise ValueError("Use a safe known-failure code.")
                    delay = max(
                        min(MAX_BACKOFF, BASE_BACKOFF * 2 ** (row[1] - 1)),
                        retry_after_seconds,
                    )
                    state = "failed" if row[1] >= MAX_ATTEMPTS else "retry_wait"
                    next_attempt = now + delay
                else:
                    state, next_attempt = next_state, now
                if failure is None and result is None:
                    store.db.execute(
                        "UPDATE discogs_notification_outbox SET state=?,next_attempt=?,"
                        "lease_until=NULL,last_error=?,updated_at=? WHERE event_key=?",
                        (state, next_attempt, error_code, now, claim["event_key"]),
                    )
                    result = {"state": state, "delivered": False, "next_attempt": next_attempt}
            if failure is not None:
                raise RuntimeError(failure)
            return result
        finally:
            store.close()

    @staticmethod
    def claim_reference(reservation):
        if not isinstance(reservation, dict) or not all(
            key in reservation
            for key in ("event_key", "attempt_token", "provider_id", "destination_ref")
        ):
            raise ValueError("Reservation required.")
        return {
            key: reservation[key]
            for key in ("event_key", "attempt_token", "provider_id", "destination_ref")
        }

    def record_accepted(self, reservation):
        """Provider acceptance only. The user's device delivery remains unknown."""
        return self._finish(self.claim_reference(reservation), "accepted")

    def record_known_failure(self, reservation, error_code, *, retry_after_seconds=0):
        """Retry only when the provider definitely did not accept the request."""
        return self._finish(
            self.claim_reference(reservation),
            "retry_wait",
            error_code=error_code,
            retry_after_seconds=retry_after_seconds,
        )

    def record_uncertain(self, reservation):
        return self._finish(
            self.claim_reference(reservation), "uncertain", error_code="unknown_outcome"
        )

    def revoke(self, binding):
        if not isinstance(binding, NotificationBinding):
            raise ValueError("Binding required.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                store.db.execute(
                    "UPDATE discogs_notification_outbox SET state='uncertain',updated_at=? "
                    "WHERE provider_id=? AND destination_ref=? AND state='leased'",
                    (now, binding.provider_id, binding.destination_ref),
                )
                store.db.execute(
                    "UPDATE discogs_notification_outbox SET state='revoked',updated_at=? "
                    "WHERE provider_id=? AND destination_ref=? "
                    "AND state IN ('pending','retry_wait')",
                    (now, binding.provider_id, binding.destination_ref),
                )
        finally:
            store.close()

    def status(self, binding=None, provider=UNAVAILABLE_PROVIDER):
        if binding is not None and not isinstance(binding, NotificationBinding):
            raise ValueError("Binding required.")
        if not isinstance(provider, ProviderCapability):
            raise ValueError("Provider capability required.")
        store = RulesStore(self.database)
        try:
            rows = store.db.execute(
                "SELECT state,count(*) FROM discogs_notification_outbox GROUP BY state"
            ).fetchall()
            return {
                "configured": binding is not None,
                "provider_available": bool(
                    binding and provider.available and provider.provider_id == binding.provider_id
                ),
                "sender_connected": False,
                "notifications_enabled": False,
                "provider_acceptance_is_delivery": False,
                "states": dict(rows),
                "purchase_allowed": False,
            }
        finally:
            store.close()
