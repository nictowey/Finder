# Bounded Monitoring history transfer

The Monitoring history query retains the existing lexical 14-day text cutoff and
independent 10,001-row scan/dispatch caps. It aggregates those bounded inputs in
PostgreSQL and returns one row containing totals, dispatch correlation and the 12
recent scan projections. The 10,001st row still contributes to totals and sets
`history_truncated`; the original counts are not silently expanded or reduced.
No schema, index, persistent snapshot, provider request, worker cadence or budget
changes are required. `summarizeOperations` remains the original reference oracle.

`readOperationsHealth` reads only the existing three settings and device count,
using the original JavaScript current-watch, lease, due-time and heartbeat rules.
Its result omits history totals and recent records, so a caller cannot mistake
unloaded history for zero scans. API/UI integration can use this for ordinary
page loads and fetch full history only for Monitoring.

## Compatibility and bounds

Ordinary integer counters aggregate exactly when every partial sum is guaranteed
to fit JavaScript's safe integer range. Other numeric encodings, decimals and
malformed values retain their original ordered JavaScript addition through
projected scalar arrays, with at most 10,001 entries per counter. Latest quota
still uses JavaScript `Number.isInteger`, including its original rounding behavior.
SQL uses JSON rather than JSONB, avoiding new numeric-range restrictions.

Canonical UTC timestamp text is parsed through guarded calendar arithmetic with
millisecond truncation. Noncanonical or malformed timestamps use narrow JavaScript
fallback projections. Invalid delay timestamps still invalidate the overall max;
invalid lease timestamps still do not expire a lease. Calendar overflow such as
February 30 retains JavaScript's normalization. No unchecked timestamp/numeric
casts, PostgreSQL 16-only input-validation helpers, or installed functions are used.

PostgreSQL JSON extraction rejects escaped NUL/unpaired surrogates even in ignored
properties. Documents containing NUL/surrogate escapes (including valid pairs as a
conservative fallback) are returned as raw text and decoded in JavaScript. These
exceptional documents are bounded by the same 10,001-row cap, but individual JSON
values have no byte cap in the existing schema. Therefore malformed history can
reduce the transfer savings; there is no universal fixed response-byte guarantee.
Normal records never return unrelated metrics or full ledger rows. Timestamp ties
retain the existing unspecified ordering; no new ID tie-breaker is introduced.

## Offline evidence

The actual SQL runs in PGlite and is compared field-for-field with the original
JavaScript summary over separately selected original rows. Cases cover missing and
malformed metrics, numeric overflow/underflow and coercions, escaped Unicode,
noncanonical/invalid timestamps, empty and mixed histories, current-watch health,
lease boundaries, notification/device state, independent dispatch correlation and
9,999/10,000/10,001/10,002-row cap boundaries.

A synthetic fixture with 10,002 attempts and 10,002 dispatches, each limited to
10,001, returned:

| History query result | Rows | JSON bytes |
| --- | ---: | ---: |
| Original full rows | 20,002 | 8,427,530 |
| Aggregate and recent projection | 1 | 3,966 |

This is a 99.95% reduction for the synthetic normal fixture. Bytes measure the
JSON serialization of returned history rows only, excluding unchanged settings and
device reads, PostgreSQL framing, TLS, connection setup and HTTP. This is not
billing telemetry, a claim about current provider usage, or production latency.

The same local PGlite/WASM run measured 270 ms for the original two history
queries and 1,942 ms for the aggregate query at the cap. Aggregation trades more
database computation for far less returned data; these local measurements do not
predict hosted PostgreSQL latency or billing. Loading history only for Monitoring
is therefore part of the mitigation, and isolated PostgreSQL CI should report its
own timings before an approved release. Both paths cap the history rows included
in the summary at 10,001 per ledger; filtering/sorting can examine additional
underlying rows before applying those limits.

## Real PostgreSQL gate and release

`node --import tsx scripts/check_operations.ts` runs the same bounded synthetic
fixture on real PostgreSQL. It requires `FINDER_OPERATIONS_FIXTURE_URL` to identify
only the existing disposable `finder_ci` service at `127.0.0.1` with an explicit
port and the fixture credentials. All other destinations, URL options and fragments
are rejected before connection. The gate uses one connection, temporary tables, a
transaction rolled back at completion, a 5-second connection timeout and 30-second
statement timeout. Errors do not print URLs, credentials or query parameters.
The pull-request and deployment workflows both run this gate against their own
disposable PostgreSQL service, never a production-hosted synthetic schema.

Before an approved release, run the exact integrated candidate's ordinary offline
checks and this gate inside the isolated CI service. This validation generates no
Neon or provider traffic. The deployment path includes `operations.ts` and
`operations-query.ts`, runs the existing migration/restore rehearsal locally, then
applies actual production migrations with `--migrate-only` before deploying the API.
It retains the bounded anonymous/auth rejection smoke checks and performs no optional
seed or provider scan. No production ledger sweep is needed. Deployment/upload
overhead is not estimated from the synthetic byte measurement. Publication,
deployment and any authenticated production check require the applicable authority.
