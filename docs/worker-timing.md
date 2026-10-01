# Anonymous worker timing

The scheduled worker's existing JSON run report adds `timing_total_ms` and, for each
fixed stage below, `timing_<stage>_ms`, `timing_<stage>_calls` and
`timing_<stage>_completed`. No seller/catalog payloads, item/watch identities, titles,
sellers, URLs, arbitrary field names, credentials, owner labels or price values enter
these metrics. No extra database write or provider call is made.

## Stages

- `setup`: chunk initialization, target/query preparation, queue loading and initial
  checkpoint, plus otherwise unattributed chunk/control overhead
- `catalog`: catalog client lifecycle, target hydration and cached/fallback sibling profile
- `search`: marketplace client lifecycle, quota reading, search pages and their checkpoints,
  plus shared loop/control overhead; excludes the nested cached/detail stages below
- `cached_queue`: load the pending batch eligible for cached reassessment
- `cached_read`: each stored source read attempted after eligibility and deadline checks;
  a normal return may contain no usable source
- `cached_review`: each cached `assess_review` call, excluding reads and persistence;
  this is wall time in the CPU review path, not process CPU time
- `cached_disposition`: each cached disposition transaction, including its existing
  lease checks, snapshot/inbox work and storage operations
- `detail_queue`: select the pending/due detail batch
- `detail`: each admitted detail-row iteration, including stored-source checks, reuse or
  provider refresh, assessment, disposition and handled-error storage
- `finalize`: final checkpoint, coverage/delay calculation and finish, or existing
  failure/lost-lease cleanup; a failed finalization followed by cleanup can enter twice

## Accounting and limits

Spans nest, but their elapsed totals are **exclusive**: child elapsed time is subtracted
from its parent. Each measured nanosecond belongs to only the innermost stage. The outer
setup span covers the chunk, so the sum of unrounded stage durations equals its duration.
Milliseconds are truncated only after accumulating each stage's nanoseconds per chunk.
`timing_total_ms` truncates the unrounded chunk total independently, so it can exceed the
sum of stage milliseconds by small rounding remainders. The run sums each chunk's emitted
integers; it does not time claim acquisition, migration, notification delivery or CLI setup.
Runs with no chunks return zero timing fields.

`calls` counts entered spans; `completed` counts spans that exit normally. Failed spans
still add elapsed time and re-raise the original exception. Normal return is not a claim
of business success: a missing stored source, an internally handled detail failure or a
suppressed disposition can return normally. Catalog/search/finalize spans are broad phases,
not counts of provider requests or SQL statements. Existing request counters remain the
source for provider counts. Cached review and disposition counts distinguish actual calls
in this run from the stored coverage snapshots.

The existing `pending`, `evaluated`, `unique_retrieved`, initial-page and exhausted-query
run counters are sums of per-chunk stored snapshots. They can count the same retained
work repeatedly; they are **not unique newly completed work**. A completed chunk may stop
on an execution, chunk or storage budget with work still pending. Existing private coverage
summaries retain the partial reason; completed span counts do not replace those semantics.

The measurements use `perf_counter_ns`, independently of all work/lease/overall budget
clocks. Deadline reads, limits, exception handling, matching/scoring, prices, alerts,
provider request limits and policy versions are unchanged. No extra interruption point is
introduced: an in-flight provider or database operation can already exceed a deadline.

These are observations, not proof of a production bottleneck. Compare matched workloads
and repeated ordinary runs before diagnosing storage, catalog, CPU or network delays.
Differences in siblings, cached/detail mix, provider/cache state and pending work can change
both timing and throughput. Local deterministic tests establish accounting and unchanged
control flow; they do not establish production latency, precision or recall.
