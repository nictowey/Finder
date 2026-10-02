# Narrow discovery worker reads

This local mitigation reduces returned database payloads without changing review
policy v19, search/detail budgets, queue eligibility or ordering, cadence rules,
coverage, notification decisions, lease fences, lock strength/order, or transaction
boundaries. It requires no schema changes.

The worker now reads:

- Query-array lengths for enabled-watch cadence instead of complete progress
  documents. Empty/missing arrays retain the original three-query default;
  legacy nonarray values retain Python's original length/truthiness behavior
- The previous evaluation signature at checkpoint instead of the full progress
- Only the inbox invalidation marker alongside each due work row. The existing
  `review_data` access stays at the same conditional points in the worker
- One `EXISTS` boolean at final scheduling using the same due-row predicate
- Watch ID for checkpoint lease checks, or current summary for final completion;
  listing invalidation marker and type guards under the original listing lock

Full inbox reads remain where the saved review must be merged. Listing repository
upsert still reads and merges its full current snapshot and retains the original
observation-history contract. Matching profiles remain full because assessment
needs their content. This change reduces returned data, not ingress writes.

## Compatibility and exceptional reads

PostgreSQL JSON extraction can reject escaped NUL or unpaired surrogates even in
ignored fields. SQLite can truncate NUL or emit invalid UTF-8 from an unpaired
surrogate. A raw-text guard returns the full original JSON only for these documents
and malformed nonobject roots, preserving Python decoding and mapping behavior.
Valid escaped surrogate pairs conservatively take the same fallback. Normal
objects return only the projected fields. There is no universal response-byte cap
because existing JSON documents have no byte limit.

Missing rows, SQL NULL, JSON null, missing keys, falsey values, malformed metadata,
and timestamp boundaries remain distinct where the previous consumer distinguished
them. In particular, missing source metadata is allowed, explicit nonobject
metadata fails closed at the original read point, and a malformed review is only
accessed after the worker's existing refresh/failure short-circuits. The existing
nonpending due predicate is unchanged; this patch does not repair corrupt data or
expand the predicate's existing JSON-operator compatibility.

## Synthetic evidence and release gate

The offline queue fixture selects the same ordered 300 rows at the cached-re-sort
cap from 301 eligible identities, with a 12,000-character synthetic field in each
existing review. JSON serialization of the actual returned SQL rows is 3,413,473
bytes before projection and 146,101 bytes after, a 95.72% reduction. The projected
measurement includes exceptional-fallback columns. Missing reviews in the fixture
remain eligible. These are synthetic result bytes, excluding unchanged queries,
protocol framing, TLS, connection setup and writes; they are not Neon billing,
production payload measurements, or latency estimates.

Failure-first tests cover projection shape alongside the unchanged behavior oracle.
Additional tests cover exact eligible work/order at limits 0/1/12/16/300, reserved
old-work detail slots, refresh requests, stale leases, source invalidation, event
resumption, supplied-transaction rollback and malformed/Unicode JSON. The actual
SQLAlchemy JSON expressions execute in both SQLite and local PostgreSQL/WASM.

The existing localhost-only isolated PostgreSQL rehearsal now invokes
`scripts/check_worker_projections.py` after remigration, using its disposable schema
and already validated connection. It executes the actual worker paths including
escaped Unicode, cadence, signatures, refresh selection, existence checks and stale
listing invalidation. The existing migration, restore, locking, refresh and evidence
checks remain required. Run this real PostgreSQL gate in approved CI before any
release; no production/provider call is needed for validation.

Rollback is a code revert to the preceding worker version. No migration, data
rewrite, forced reassessment or provider replay is needed.
