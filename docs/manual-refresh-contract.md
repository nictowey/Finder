# Explicit fresh checks

The owner endpoint stores a request token and a separate retry time on the existing
identity-only discovery work row. Classification-policy re-sorts and ordinary search
checkpoints preserve those fields. Both worker cache paths yield to a pending request.
The classifier remains v19; this change does not alter matching policy.

A paused watch rejects new refresh requests with a clear 409 response; unknown work
references return 404. The endpoint never resumes a watch. Requests accepted before a
later pause retain their token for normal owner-initiated resumption. The success notice
promises queued work when the watch can run, without promising a particular worker.

Only a provider detail read that succeeds, or confirms the listing is unavailable, can
acknowledge the selected token. A transient error keeps the token and advances its retry
time using the normal backoff. Quota or execution-budget pauses leave it pending. Repeated
requests replace the token but preserve an existing retry delay. Completion from an older
snapshot cannot acknowledge or postpone a newer token.

The endpoint and disposition serialize on the existing watch fence, before work writes.
There is no new work-row lock ahead of the seller/listing locks. Seller deletion still
removes the work row by the existing cascade and prevents its recreation from old details.
Owner identity labels, purchases, first-judgment provenance and notification suppression
are unchanged. Requesting a read does not itself assert that seller evidence changed or
invalidate it; existing freshness, invalidation and alert rules continue to apply.

Coverage counts each outstanding token once as pending, even after an older worker changes
legacy status. Requested work uses its own retry time for eligibility and queue ordering,
within the existing pending detail allocation and shared provider budget. Legacy workers
ignore these additive fields and cannot erase the request. An endpoint request racing
with final watch scheduling can still wait until the normal watch poll, in either worker
version; this is a bounded scheduling delay, not acknowledgement of the request.

## Deployment and rollback

1. Run required checks on the exact candidate commit, including the isolated PostgreSQL PR
   job (`feedback-postgres`). Its endpoint/worker, row-lock, migration and restore gates
   are required before deployment. PGlite and SQLite tests do not prove PostgreSQL races.
2. Apply additive migration 6 before deploying the new API or worker. The existing
   deployment workflow migrates in `run_watchlist.py --migrate-only` before deploying the dashboard.
   Do not expose the new endpoint against a schema without both nullable columns.
3. Drain old running and queued worker jobs during the controlled rollout for prompt
   pickup. The token survives old-worker overlap, but an old worker does not fulfill it.
4. Code rollback must retain migration 6 and its nullable fields. Existing v19 code can
   read/write the old columns, while outstanding tokens remain available for a corrected
   worker. Do not remove pending tokens as part of rollback or claim that legacy cached
   completion satisfied them. Restore the new worker to process them under normal budgets.

No historical requests are reconstructed or replayed. The offline reproduction establishes
that accepted requests can be consumed by cached reassessment in v19. It does not identify
the cause of any particular live listing's stale evidence.

## Local checks

Install Python development dependencies and run `npm ci` before the cross-runtime tests.
`tests/test_manual_refresh.py` executes the real owner endpoint in PGlite and transfers its
committed queue fields into the real Python worker's SQLite fixture; all provider responses
are synthetic. Python-only provider workflows explicitly skip this cross-runtime suite.
The isolated PostgreSQL PR job installs both runtimes and sets
`FINDER_REQUIRE_REFRESH_INTEGRATION=1`: missing Node, tsx or PGlite then fails collection
instead of accepting a skipped suite. It requires the suite, then runs
`scripts/check_manual_refresh.py` through the existing disposable-schema rehearsal.
