"""Opt-in durable candidate dry run from complete release snapshots.

No provider, browser, cart mutation, order or background worker is configured here.
The injected reader must provide one already authorized exact-listing observation.
Uncertain reads are never retried automatically. A review is never purchase permission.
"""

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from finder.discogs_candidate_detail import VerifiedHostedDetail, VerifiedPublicDetail
from finder.discogs_cart_claims import CartClaimBlocked
from finder.discogs_rules import Observation, RulesStore, Target, evaluate

MAX_AGE = 300
MAX_ROWS = 500
MAX_ID = 9007199254740991


class CandidateStopped(RuntimeError):
    """Sanitized stop code, never a marketplace or credential payload."""


def observation_fingerprint(observation: Observation):
    """Bind a review to the complete normalized observation without storing it."""
    value = json.dumps(observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


class CandidatePipeline:
    """Persist one candidate per exact release/listing, with revisioned reviews."""

    def __init__(self, database, *, reader=None, claims=None, clock=time.time):
        self.database, self.reader, self.claims, self.clock = database, reader, claims, clock
        store = RulesStore(database)
        try:
            with store.db:
                store.db.execute("""
                    CREATE TABLE IF NOT EXISTS discogs_candidates (
                        release_id INTEGER NOT NULL, listing_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL, baseline_revision INTEGER NOT NULL,
                        rules_revision INTEGER NOT NULL, control_revision INTEGER NOT NULL,
                        schedule_revision INTEGER NOT NULL, observed_at REAL NOT NULL,
                        state TEXT NOT NULL CHECK(state IN
                            ('pending','reading','review_only','blocked','uncertain')),
                        token TEXT, reviewed_at REAL, review TEXT,
                        PRIMARY KEY(release_id,listing_id))
                """)
        finally:
            store.close()

    def _guard(self, store, release, *, rules=None, control=None, schedule=None, token=None):
        saved = store.snapshot()
        global_control = (
            store.db.execute("SELECT killed,revision FROM cloud_control WHERE id=1").fetchone()
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_control'").fetchone()
            else None
        )
        running = (
            store.db.execute(
                "SELECT paused,revision,lease_token FROM discogs_hosted_schedule WHERE id=1"
            ).fetchone()
            if store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='discogs_hosted_schedule'"
            ).fetchone()
            else None
        )
        baseline = (
            store.db.execute(
                "SELECT revision,baseline_at,baseline,status FROM discogs_release_baselines "
                "WHERE release_id=?",
                (release,),
            ).fetchone()
            if store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='discogs_release_baselines'"
            ).fetchone()
            else None
        )
        target = next((item for item in saved["targets"] if item["release_id"] == release), None)
        now = self.clock()
        if (
            not global_control
            or global_control[0]
            or not running
            or running[0]
            or not target
            or not target["enabled"]
            or release in saved["owned"]
            or not baseline
            or baseline[3] != "observed"
            or baseline[1] is None
            or baseline[2] is None
            or not 0 <= now - baseline[1] <= MAX_AGE
            or rules is not None
            and saved["revision"] != rules
            or control is not None
            and global_control[1] != control
            or schedule is not None
            and running[1] != schedule
            or token is not None
            and running[2] != token
        ):
            raise CandidateStopped("candidate_state_changed_or_unavailable")
        page = json.loads(baseline[2])
        if page.get("complete") is not True or page.get("release_id") != release:
            raise CandidateStopped("candidate_baseline_unverified")
        return saved, global_control, running, baseline, target, page

    def capture(self, release, *, rules_revision, control_revision, schedule_revision, lease_token):
        """Consume retained new/reappeared/changed events while the snapshot lease holds."""
        if type(release) is not int or not 0 < release <= MAX_ID or not lease_token:
            raise CandidateStopped("invalid_candidate_capture")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                _, _, _, baseline, _, page = self._guard(
                    store,
                    release,
                    rules=rules_revision,
                    control=control_revision,
                    schedule=schedule_revision,
                    token=lease_token,
                )
                present = {row["listing_id"] for row in page["rows"]}
                events = store.db.execute(
                    "SELECT sequence,listing_id FROM discogs_release_changes "
                    "WHERE release_id=? AND kind IN ('new','reappeared','changed') "
                    "ORDER BY sequence",
                    (release,),
                ).fetchall()
                latest = {ident: sequence for sequence, ident in events if ident in present}
                for ident, sequence in latest.items():
                    store.db.execute(
                        """
                        INSERT INTO discogs_candidates VALUES (?,?,?,?,?,?,?,?,
                            'pending',NULL,NULL,NULL)
                        ON CONFLICT(release_id,listing_id) DO UPDATE SET
                            sequence=excluded.sequence,
                            baseline_revision=excluded.baseline_revision,
                            rules_revision=excluded.rules_revision,
                            control_revision=excluded.control_revision,
                            schedule_revision=excluded.schedule_revision,
                            observed_at=excluded.observed_at,
                            state=CASE WHEN excluded.sequence>discogs_candidates.sequence
                                OR excluded.rules_revision!=discogs_candidates.rules_revision
                                OR excluded.control_revision!=discogs_candidates.control_revision
                                OR excluded.schedule_revision!=discogs_candidates.schedule_revision
                                THEN 'pending' ELSE discogs_candidates.state END,
                            token=CASE WHEN excluded.sequence>discogs_candidates.sequence
                                OR excluded.rules_revision!=discogs_candidates.rules_revision
                                OR excluded.control_revision!=discogs_candidates.control_revision
                                OR excluded.schedule_revision!=discogs_candidates.schedule_revision
                                THEN NULL ELSE discogs_candidates.token END,
                            reviewed_at=CASE WHEN excluded.sequence>discogs_candidates.sequence
                                OR excluded.rules_revision!=discogs_candidates.rules_revision
                                OR excluded.control_revision!=discogs_candidates.control_revision
                                OR excluded.schedule_revision!=discogs_candidates.schedule_revision
                                THEN NULL ELSE discogs_candidates.reviewed_at END,
                            review=CASE WHEN excluded.sequence>discogs_candidates.sequence
                                OR excluded.rules_revision!=discogs_candidates.rules_revision
                                OR excluded.control_revision!=discogs_candidates.control_revision
                                OR excluded.schedule_revision!=discogs_candidates.schedule_revision
                                THEN NULL ELSE discogs_candidates.review END
                    """,
                        (
                            release,
                            ident,
                            sequence,
                            baseline[0],
                            rules_revision,
                            control_revision,
                            schedule_revision,
                            baseline[1],
                        ),
                    )
                count = store.db.execute("SELECT count(*) FROM discogs_candidates").fetchone()[0]
                if count > MAX_ROWS:
                    raise CandidateStopped("candidate_limit_reached")
                return {"captured": len(latest), "purchase_allowed": False}
        finally:
            store.close()

    def review_one(self):
        """Review one pending candidate using only an injected fixed-ID data reader."""
        if self.reader is None:
            return {"status": "disabled", "purchase_allowed": False}
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                rows = store.db.execute(
                    "SELECT release_id,listing_id,sequence,baseline_revision,rules_revision,"
                    "control_revision,schedule_revision FROM discogs_candidates "
                    "WHERE state='pending' ORDER BY sequence LIMIT ?",
                    (MAX_ROWS,),
                ).fetchall()
                chosen = None
                for row in rows:
                    try:
                        _, _, _, baseline, _, page = self._guard(
                            store, row[0], rules=row[4], control=row[5], schedule=row[6]
                        )
                    except CandidateStopped:
                        continue
                    if baseline[0] == row[3] and any(
                        item["listing_id"] == row[1] for item in page["rows"]
                    ):
                        chosen = row
                        break
                if chosen is None:
                    return {"status": "idle", "purchase_allowed": False}
                lease = uuid.uuid4().hex
                store.db.execute(
                    "UPDATE discogs_candidates SET state='reading',token=? "
                    "WHERE release_id=? AND listing_id=? AND state='pending'",
                    (lease, chosen[0], chosen[1]),
                )
        finally:
            store.close()
        release, ident, sequence, baseline_revision, rules, control, schedule = chosen
        try:
            detail = self.reader(release, ident)
            if isinstance(detail, (VerifiedPublicDetail, VerifiedHostedDetail)):
                observation = Observation.model_validate(detail.observation)
                read_at = datetime.fromisoformat(
                    observation.observed_at.replace("Z", "+00:00")
                ).timestamp()
                hosted = isinstance(detail, VerifiedHostedDetail)
                source = "discogs_rendered_listing" if hosted else "discogs_public_listing"
                route = f"/sell/item/{ident}" if hosted else f"/marketplace/listings/{ident}"
                if (
                    observation.source != source
                    or detail.requested_release != release
                    or detail.requested_listing != ident
                    or detail.route != route
                    or not 0 <= detail.completed_at - detail.started_at <= MAX_AGE
                    or not 0 <= self.clock() - detail.completed_at <= MAX_AGE
                    or not detail.started_at <= read_at <= detail.completed_at + 1
                    or any(
                        getattr(observation, field) is not None
                        for field in (
                            "shipping",
                            "tax",
                            "fees",
                            "destination_country",
                            "destination_postal",
                        )
                    )
                    or observation.pressing_evidence != "unknown"
                ):
                    raise CandidateStopped("candidate_live_detail_provenance_unverified")
                detail_evidence = {
                    "source": source,
                    "fixed_route": detail.route,
                    "reported_shipping": (
                        detail.reported_shipping_text if hosted else detail.reported_shipping
                    ),
                    "shipping_blocked": None if hosted else detail.shipping_blocked,
                    "cart_state": detail.cart_state if hosted else "unknown",
                    "seller_handle": detail.seller_handle if hosted else None,
                    "delivered_total_verified": False,
                }
            else:
                observation = Observation.model_validate(detail)
                if observation.source != "synthetic":
                    raise CandidateStopped("candidate_identity_or_source_unverified")
                detail_evidence = {"source": "synthetic", "delivered_total_verified": True}
            if observation.release_id != release or observation.listing_id != ident:
                raise CandidateStopped("candidate_identity_or_source_unverified")
            store = RulesStore(self.database)
            try:
                with store.db:
                    store.db.execute("BEGIN IMMEDIATE")
                    row = store.db.execute(
                        "SELECT state,token,sequence,baseline_revision FROM discogs_candidates "
                        "WHERE release_id=? AND listing_id=?",
                        (release, ident),
                    ).fetchone()
                    if tuple(row) != ("reading", lease, sequence, baseline_revision):
                        raise CandidateStopped("candidate_claim_changed")
                    saved, _, _, baseline, target, _ = self._guard(
                        store, release, rules=rules, control=control, schedule=schedule
                    )
                    if baseline[0] != baseline_revision:
                        raise CandidateStopped("candidate_baseline_changed")
                    result = evaluate(
                        Target.model_validate(target),
                        observation,
                        now=datetime.fromtimestamp(self.clock(), UTC),
                        already_owned=release in saved["owned"],
                    )
                    if isinstance(detail, VerifiedHostedDetail) and detail.cart_state != "addable":
                        result["eligible_for_review"] = False
                        result["reasons"].append(
                            "Listing is already in cart or add state is unknown."
                        )
                    projection = {
                        "listing": {
                            "listing_id": ident,
                            "release_id": release,
                            "available": observation.available,
                            "media": observation.media,
                            "sleeve": observation.sleeve,
                            "seller_percent": observation.seller_percent,
                            "feedback": observation.feedback,
                            "item": observation.item,
                            "currency": observation.currency,
                            "shipping_blocked": observation.shipping is None,
                        },
                        "evaluation": {
                            **result,
                            "rules_revision": rules,
                        },
                        "detail_evidence": detail_evidence,
                        "observation_fingerprint": observation_fingerprint(observation),
                    }
                    state = "review_only" if result["eligible_for_review"] else "blocked"
                    store.db.execute(
                        "UPDATE discogs_candidates SET state=?,token=NULL,reviewed_at=?,"
                        "review=? WHERE release_id=? AND listing_id=? AND token=?",
                        (state, self.clock(), json.dumps(projection), release, ident, lease),
                    )
                    return {
                        "status": state,
                        "release_id": release,
                        "listing_id": ident,
                        "purchase_allowed": False,
                    }
            finally:
                store.close()
        except Exception:
            store = RulesStore(self.database)
            try:
                with store.db:
                    store.db.execute(
                        "UPDATE discogs_candidates SET state='uncertain',token=NULL,"
                        "review=NULL WHERE release_id=? AND listing_id=? AND token=?",
                        (release, ident, lease),
                    )
            finally:
                store.close()
            return {
                "status": "uncertain",
                "release_id": release,
                "listing_id": ident,
                "purchase_allowed": False,
            }

    def review_bundle(self, release, ident, key):
        """Read-only snapshot/listing/claim port data for a no-order cart rehearsal."""
        if self.claims is None:
            raise CandidateStopped("claim_store_unconfigured")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                saved, global_control, running, baseline, _, _ = self._guard(store, release)
                row = store.db.execute(
                    "SELECT sequence,baseline_revision,rules_revision,control_revision,"
                    "schedule_revision,state,reviewed_at,review FROM discogs_candidates "
                    "WHERE release_id=? AND listing_id=?",
                    (release, ident),
                ).fetchone()
                if (
                    not row
                    or row[5] != "review_only"
                    or row[1] != baseline[0]
                    or row[2] != saved["revision"]
                    or row[3] != global_control[1]
                    or row[4] != running[1]
                    or row[7] is None
                    or not 0 <= self.clock() - row[6] <= MAX_AGE
                ):
                    raise CandidateStopped("candidate_review_stale_or_blocked")
                claim = self.claims.get(key)
                if (
                    not claim
                    or claim.release_id != release
                    or claim.listing_id != ident
                    or claim.rules_revision != saved["revision"]
                ):
                    raise CandidateStopped("one_copy_claim_unverified")
                return {
                    "snapshot": {
                        **saved,
                        "kill_switch": False,
                        "control_revision": global_control[1],
                    },
                    "listing": json.loads(row[7]),
                    "claim": claim.response(),
                    "candidate_sequence": row[0],
                    "purchase_allowed": False,
                }
        finally:
            store.close()

    def reserve_rehearsal(self, release, ident, key):
        """Reserve one copy only after a fresh dry-run review; never add to a cart."""
        if self.claims is None:
            raise CandidateStopped("claim_store_unconfigured")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                saved, global_control, running, baseline, _, _ = self._guard(store, release)
                row = store.db.execute(
                    "SELECT state,baseline_revision,rules_revision,reviewed_at,"
                    "control_revision,schedule_revision "
                    "FROM discogs_candidates WHERE release_id=? AND listing_id=?",
                    (release, ident),
                ).fetchone()
                if (
                    not row
                    or row[0] != "review_only"
                    or row[1] != baseline[0]
                    or row[2] != saved["revision"]
                    or row[4] != global_control[1]
                    or row[5] != running[1]
                    or row[3] is None
                    or not 0 <= self.clock() - row[3] <= MAX_AGE
                ):
                    raise CandidateStopped("candidate_review_stale_or_blocked")
                revision = saved["revision"]
        finally:
            store.close()
        try:
            claim = self.claims.reserve(release, ident, revision, key)
        except CartClaimBlocked as error:
            raise CandidateStopped(str(error)) from None
        # A concurrent rule/pause change can leave a conservative reserved slot,
        # but it can never turn a stale review into a cart or order action.
        self.review_bundle(release, ident, key)
        return claim.response()
