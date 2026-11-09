# Bounded aggregate polling canary

`scripts/sniper_polling_canary.py` is an offline-reviewed candidate helper, not a
service cutover or authorization to perform live reads. Its tests use synthetic
SQLite databases, intercepted HTTP and simulated clocks. Subprocess supervision,
SQLite locks, termination and cleanup tests also use real local processes/clocks.

## Preconditions for a separately authorized live proof

- Review the exact application and helper revisions and pass full combined CI.
- Coordinate a maintenance pause of **all** old aggregate, legacy and manual API
  readers sharing the source database, plus the health timer. Old code ignores the
  guard and must not run concurrently with this helper.
- Verify the tagged source database and existing protected app-credential file.
  Preserve enabled flags, rule revisions/data, samples and notification history.
- Separately initialize only the reviewed additive guard schema during that pause.
  The helper requires the schema to exist and never migrates or repairs its source.
- Confirm source guard state and all existing provider cooldowns. Pending/unknown
  outcomes require the separately reviewed recovery process, not a restart.

The explicit live-read CLI flag alone does not verify these external preconditions.
The helper does not pause services, change credentials, start a sender/watcher,
modify real rule/sample/outbox/send-budget data, or authorize purchases.

## Isolation and request lifecycle

The supervisor creates a private 0700 directory and owns its cleanup. The child
backs up the source with SQLite into a 0600 database, accepts exactly 19 enabled
rules plus disabled release 11398560 at revision 2, and seeds only the copy's enabled
`next_check` fields. Active rule IDs and user choices are read from the snapshot,
not hardcoded. The copied aggregate control enabled/revision and every rule's
ID/revision/data are fingerprinted.

The actual candidate `AggregatePriceAlerts.poll_once` runs with a requested
120-second interval. Each actual HTTP request retains the production copy guard
and a source guard. The local reader budget is checked before committing a copy
pending token. The copy commits first, followed by source, with no second wait.
The source fingerprint is checked inside the source reservation transaction.
Neither transaction spans HTTP. These two independent commits are not atomic
together: a rejected second reservation stops the helper and retains the already
committed token; it never compensates by clearing a token or retries HTTP.

At every response's headers, the underlying reader retains its local cooldown.
The helper checkpoints a sanitized known provider deadline before potentially
blocking writes, and attempts **both** guards' normal header finalization even if
one fails. The reviewed guard limits each persistence retry to five seconds. A
successful finalization takes the maximum known deadline and clears only its own
token. Pre-header failure, process death or failed finalization can leave pending
state indefinitely. No helper path clears unknown or stale pending state.

A SQLite authorizer permits source UPDATEs only to:

- `discogs_monitor_control.next_request`
- `discogs_api_read_guard.pending_token`
- `discogs_api_read_guard.pending_since`
- `discogs_api_read_guard.known_deadline`

Guard metadata is read-only. Required durability PRAGMAs are read-only; temporary
`busy_timeout` changes are connection-local and restored by the reviewed guard.

## Results and stopping

The helper seeks three successful observations for each of the 19 active rules.
Reports contain anonymous per-rule actual timestamps and request intervals,
sanitized HTTP/quota status and guard/hold diagnostics, never tokens, credentials,
URLs, release IDs, titles or provider bodies. Production execution uses real
system clocks and worker-equivalent latency-adjusted three-second sleeps.

A separate parent process enforces a 480-second run deadline, then TERM/KILL and
join. Repeated SIGINT/SIGTERM signals are ignored during bounded shutdown and the
previous handlers are restored afterward. Parent cleanup removes the private copy
and all WAL/SHM/lock sidecars after the child stops.

Final source inspection runs in a separate read-only child with a two-second
deadline and the same TERM/KILL/join protection. The parent never opens or queries
SQLite itself, so blocked native source-file I/O cannot bypass this bound. If final
inspection cannot finish, source state is reported unavailable/manual-check while
preserving checkpoint deadlines and pending-hold diagnostics. This inspection and
bounded shutdown may add time after the 480-second live-read window; neither can
send HTTP. Diagnostic checkpoints do not replace durable source state.

Reports from forced termination, missing final reports or interrupted execution
mark request counters incomplete and lower bounds. A checkpoint containing zero
recorded attempts does not prove that no HTTP request was issued. A pending guard
still requires manual investigation regardless of the reported request count.

No report grants permission to resume old code. An operator must verify no pending
request or unknown outcome remains and every known cooldown is respected before
any resumption. Otherwise leave readers paused and use the separately reviewed
[manual recovery procedure](discogs-api-read-guard.md#operator-recovery-requirements).
Never reset a gate, discard pending state, or guess a cooldown to accelerate a run.

Three observed cycles demonstrate measured candidate scheduling only. A requested
120-second interval is not a guaranteed complete sweep, upstream refresh rate,
listing-discovery capability or production deployment result.
