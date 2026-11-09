# Shared API write-ahead safety guard

This is an offline candidate. Nothing here deploys, restarts, enables, or recovers
an existing service. The normal poll interval remains operator-configured.

## Durable request lifecycle

The additive `discogs_api_read_guard` and `discogs_api_guard_metadata` tables live
inside the existing tagged rules database. Initialization preserves all rules,
samples, delivery history, controls and existing API cooldowns. It does not enable
polling or notifications. Missing/corrupt rows after initialization are not repaired
or reset. A read-only health inspection never initializes these tables.

Every shared actual HTTP request first atomically commits a pending token and its
normal three-second API reservation. No HTTP follows a failed reservation commit.
All shared API readers check this row. A concurrent in-flight request is a cooldown,
not a failed poll; its selected rule retains its prior due time and failure count.

At **every response's headers**, including ordinary HTTP 200 with no quota headers,
the local reader cooldown is retained first. A short transaction then takes the
maximum of the existing API deadline and the response hold, and clears only the
matching pending token. Header persistence retries busy/locked SQLite writes for
at most five seconds using one absolute deadline. Its temporary nonblocking SQLite
setting is restored before unrelated work. No transaction spans network I/O.

SQLite FULL durability (or stronger) and a durable journal are required. Storage
errors are not treated as successful commits. A missing header response, crash,
failed finalization, or uncertain outcome can leave the precommitted token pending.
It never expires automatically. The 30-second age threshold changes diagnostics
only, not permission to send another request. This intentionally reduces availability:
an ordinary pre-header network error may require operator investigation.

Coverage is the aggregate stats worker (public and existing app-authenticated),
legacy scheduled stats/listing reads, and manual known-listing reads with each
catalog fallback separately guarded. Cache hits create no additional HTTP or token.
Standalone readers with no shared database, unrelated applications, and processes
running older code do not honor this guard. Do not claim global credential safety.

## Health and operational halt

Read-only health exposes `controls.api_guard.state`, `pending_since`,
`known_deadline`, and `manual_recovery_required`; it never publishes the token.
Pending/corrupt/unavailable guard state requiring intervention is a polling problem
for the existing debounced health watcher. Existing 300-second watcher scheduling,
1200-second sample freshness, and notification caps/gates remain unchanged. There
is no extra test notification on a worker error.

The aggregate worker emits `api_safety_halt` and exits **78**. Install the reviewed
`RestartPreventExitStatus=78` drop-in as part of an explicitly authorized cutover,
so a supervisor does not repeatedly restart a safety halt. The legacy monitor loop
stops itself on the same safety exception. Restarts still consult the persistent
pending token, so restarting alone cannot release it.

## Operator recovery requirements

There is deliberately no automatic clear command, timeout-based reset, or recovery
button in this candidate. Recovery is a separate, explicitly reviewed operation.

1. Pause every aggregate, legacy and manual API entry point sharing this database.
   Verify the exact database, code revision, interpreter and service identities.
   Preserve enabled flags, rule data/revisions and delivery history.
2. Read health and inspect the guard schema/version, pending timestamp, matching
   request token, known deadline and `discogs_monitor_control.next_request` using
   existing authorized local database access. Inspect fixed-status worker logs and
   the source of any known response deadline. Do not expose credentials or payloads.
3. Verify the original request process and any other in-flight request are actually
   terminated. A stale timestamp, expired rule claim, elapsed 30 seconds or successful
   service restart is not evidence that a provider hold has expired.
4. Establish the provider restriction and its expiry from verified evidence. If the
   outcome/hold remains unknown, leave the guard pending and keep API reads halted.
   Do not replace an unknown deadline with a guessed 60/120/300-second wait.
5. Only after an approved recovery decision, use a separately reviewed atomic
   compare-and-set against the exact pending token, preserving the maximum known
   API deadline. Never reset notification gates, rule histories or another token.
   Re-read guard state and cooldown before resuming any service.

These controls cannot promise durability if the underlying storage lies about
commits or the database is externally destroyed/replaced. Treat storage integrity
failures and restored backups as operational incidents, not permission to recreate
an empty guard and resume traffic.

## Proof, deployment and rollback

All API entry points sharing the database must be upgraded together. An old worker
that ignores pending state must not run concurrently with a guarded proof. A bounded
live canary therefore needs a separately authorized maintenance pause of the old
aggregate poller and health timer, preserving database enabled flags and rules.
No automatic purchasing/browser service is enabled by this operation.

Use the exact reviewed application revision, explicit additive schema initialization,
full checks and a bounded isolated proof. Keep delivery changes separate. A 120-second
requested interval is not a guarantee of complete sweeps or upstream freshness.
If safe measured performance is slower, retain the 300-second configuration.

Rollback to older code is allowed only after checking that no request is pending,
no provider outcome is unknown, and every known cooldown remains enforced. Older
code ignores the new guard. Never use rollback to bypass pending state or clear a
provider hold. If recovery cannot be verified, leave polling paused and report the
blocker rather than silently restarting the old worker.
