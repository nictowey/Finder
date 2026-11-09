"""Private in-app change review, backed by the durable release-change ledger.

No transport or browser access lives here. The caller must enforce the private
loopback boundary before returning ``pending`` or accepting an acknowledgment.
Materialization is repeatable
after a crash because change sequences, not listing IDs, are the outbox keys.
"""

import time
from datetime import UTC, datetime

from finder.discogs_release_snapshots import MAX_CHANGES, ReleaseSnapshots
from finder.discogs_rules import RulesStore


class InAppChangeOutbox:
    def __init__(self, database, *, clock=time.time):
        self.database, self.clock = database, clock
        ReleaseSnapshots(database, clock=clock)
        store = RulesStore(database)
        try:
            with store.db:
                store.db.execute(
                    "CREATE TABLE IF NOT EXISTS discogs_change_outbox ("
                    "change_sequence INTEGER PRIMARY KEY, enqueued_at REAL NOT NULL)"
                )
        finally:
            store.close()

    @staticmethod
    def _check_revisions(store, rules, control):
        if (
            type(rules) is not int
            or rules < 0
            or (control is not None and (type(control) is not int or control < 0))
        ):
            raise ValueError("Explicit current rules and control revisions are required.")
        saved = store.db.execute("SELECT revision FROM state WHERE id=1").fetchone()
        has_control = store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_control'"
        ).fetchone()
        gate = (
            store.db.execute("SELECT revision FROM cloud_control WHERE id=1").fetchone()
            if has_control
            else None
        )
        control_matches = (not has_control and control is None) or (
            gate is not None and type(control) is int and gate[0] == control
        )
        if not saved or saved[0] != rules or not control_matches:
            raise RuntimeError("Owner view changed; refresh before reviewing alerts.")

    def materialize(self, *, expected_rules_revision, expected_control_revision):
        """Queue each retained complete-snapshot change once; never send a message."""
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                self._check_revisions(store, expected_rules_revision, expected_control_revision)
                store.db.execute(
                    "DELETE FROM discogs_change_outbox WHERE change_sequence NOT IN "
                    "(SELECT sequence FROM discogs_release_changes)"
                )
                store.db.execute(
                    "INSERT OR IGNORE INTO discogs_change_outbox(change_sequence,enqueued_at) "
                    "SELECT sequence,? FROM discogs_release_changes",
                    (self.clock(),),
                )
                return store.db.execute(
                    "SELECT count(*) FROM discogs_change_outbox o "
                    "LEFT JOIN discogs_release_acknowledged a "
                    "ON a.sequence=o.change_sequence WHERE a.sequence IS NULL"
                ).fetchone()[0]
        finally:
            store.close()

    def pending(self):
        """Private review payload; listing changes are never purchase decisions."""
        store = RulesStore(self.database)
        try:
            return [
                {
                    "change_sequence": row[0],
                    "release_id": row[1],
                    "listing_id": row[2],
                    "kind": row[3],
                    "observed_at": datetime.fromtimestamp(row[4], UTC).isoformat(),
                    "url": f"https://www.discogs.com/sell/item/{row[2]}",
                    "status": "needs_review",
                    "delivery": "in_app_only",
                    "purchase_allowed": False,
                    "qualifies_all_in": False,
                }
                for row in store.db.execute(
                    "SELECT o.change_sequence,c.release_id,c.listing_id,c.kind,c.observed_at "
                    "FROM discogs_change_outbox o JOIN discogs_release_changes c "
                    "ON c.sequence=o.change_sequence "
                    "LEFT JOIN discogs_release_acknowledged a ON a.sequence=o.change_sequence "
                    "WHERE a.sequence IS NULL "
                    "ORDER BY o.change_sequence DESC LIMIT ?",
                    (MAX_CHANGES,),
                )
            ]
        finally:
            store.close()

    def acknowledge(self, change_sequences, *, expected_rules_revision, expected_control_revision):
        """Acknowledge only retained queued changes; duplicate acknowledgment is safe."""
        if (
            not isinstance(change_sequences, list)
            or not 1 <= len(change_sequences) <= MAX_CHANGES
            or any(type(sequence) is not int or sequence <= 0 for sequence in change_sequences)
            or len(set(change_sequences)) != len(change_sequences)
        ):
            raise ValueError("Use a bounded list of distinct change sequences.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                self._check_revisions(store, expected_rules_revision, expected_control_revision)
                for sequence in change_sequences:
                    exists = store.db.execute(
                        "SELECT 1 FROM discogs_change_outbox o "
                        "JOIN discogs_release_changes c ON c.sequence=o.change_sequence "
                        "WHERE o.change_sequence=?",
                        (sequence,),
                    ).fetchone()
                    if not exists:
                        raise ValueError("A change is unavailable or expired. Refresh the review.")
                    store.db.execute(
                        "INSERT OR IGNORE INTO discogs_release_acknowledged "
                        "(sequence,acknowledged_at) VALUES(?,?)",
                        (sequence, self.clock()),
                    )
                return {"acknowledged_change_sequences": change_sequences, "messages_sent": 0}
        finally:
            store.close()
