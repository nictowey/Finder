# Resumable private inventory discovery

Implementation revision: September 23, 2026. Rollout evidence is recorded separately below;
offline tests are not a claim about production coverage or pressing accuracy.

## Scope and completion

The configured broad artist/album queries, spelling aliases and existing file-configured title
aliases define the search scope. Up to three queries per watch, three watches, eBay US vinyl
category, worldwide item locations, fixed-price/auction/best-offer, all conditions. A saved
buyer destination adds delivery-country filtering and contextual shipping headers; an unset
destination means shipping estimates and ship-eligibility coverage remain uncertain. Buyer
ceilings and accepted conditions control notification eligibility, not discovery.

Migration 3 adds query progress and an identity-only work ledger. Historical sampled success
never initializes an exhausted pass. Existing watches, subscriptions, revisions, dismissals,
alert history, and operational history are preserved. A worker can finish a chunk while search
is partial or evaluations remain queued. The private summary distinguishes not started, in
progress, search exhausted, exhausted with pending evaluation, budget partial, provider-limit
partial and interrupted. Counts are unique **retained references since this discovery baseline**,
not summed query totals, a marketplace denominator, or a recall measurement. Deletion cascades
can reduce this count. Provider totals are per-query hints that can change.

Every returned stable item ID is transactionally queued before page progress advances. Invalid
IDs or unverifiable start windows fail the page without checkpointing past it. No raw search
payload is stored. Sparse summaries are conservatively treated as uncertain and hydrated;
missing pressing attributes do not screen them out. Unchanged summary fingerprints do not
requeue fresh details. A detail failure is error/pending work with bounded exponential delay,
not a negative match. Successful hydration and review/inbox/outbox disposition share a fenced
transaction. Existing snapshots can be reused only within one hour with the same buyer context
and no stale-detail flags; summary observation never changes the detail evidence timestamp.

## Pagination, time windows, and moving inventory

Search pages request 200 items and offsets in multiples of 200 below 10,000. A query reporting
10,000 or more results splits its time range into inclusive, overlapping halves; stable REST
item IDs (including variation IDs) deduplicate boundaries, pages and aliases. Equal timestamps
are paginated rather than treated as a cursor. A sub-millisecond range or 128-partition frontier
that still hits the ceiling remains `partial_provider_limit`, never exhausted. It requires an
operator to assess a further supported partition or scope; the scanner does not invent one.

The baseline upper bound is fixed when the new plan starts. Independent per-query incremental
windows start 24 hours before that anchor, then resume from the last exhausted upper bound
minus 24 hours, including after outages. Each selected window processes every page before its
watermark advances; its detail work is already durable and pending counts stay visible. New
windows open no more frequently than every 30 minutes. Baseline and incremental lanes rotate
across queries during backfill. The API's `itemOriginDate` must fall within the requested start
window on returned summaries; absence or contradiction interrupts rather than certifying the
filter. No manually supplied listing is counted as independent discovery.

Every time partition receives a second complete traversal. This replay recovers some movement
and delayed indexing (covered by synthetic insertion/removal tests); it is not a marketplace
snapshot. A repeated identical noninitial page interrupts rather than loops or pretends to
finish. A 24-hour overlap cannot catch arbitrarily delayed indexing. Daily reconciliation also
revisits older inventory and changes that a start-date filter cannot report. Rapid listing
churn, ranking instability, indexing omissions, inaccessible offers and items starting/ending
between requests remain limitations. Search absence never implies sold or unavailable.

## Freshness, retention, and alerts

`FINDER_RECONCILIATION_HOURS` defaults provisionally to 24 (minimum 6). Final cadence should be
reviewed against live request counts. Four detail slots per chunk are reserved for the oldest
known due leads; remaining slots process pending work. Plausible/uncertain items become due
in four hours, rejected items in 24. Changed summaries or catalog/policy evidence requeue
previous evaluations. Confirmed 404/410, ended time or explicit out-of-stock response marks
unavailable and invalidates pending alerts; API failures stay separate. No public certainty or
valuation claims change.

The six-hour provider-content display limit remains. Older references stay in the paginated
inbox with content and prices withheld and availability explicitly unverified; owners can
request a fresh check. Fresh content exposes its detail timestamp. The inbox has server-side
50-row keyset pages and no hidden global 300-row cap. Dismissals and historical alerts survive.
Alert creation and dispatch both require sufficiently fresh detail evidence. Initial inventory
uses one generic review notification per watch; later eligible initial rows join that digest
and remain visible in the inbox, rather than sending per-item pushes. Existing alerted or
dismissed rows do not consume the initial digest. Truly newly listed eligible offers retain
normal per-watch deduplication. Push payloads remain generic and link to the authenticated inbox.
Push-service acceptance is distinct from owner-observed display.

The existing provider-use decision remains open and unchanged. This implementation adds no
real fixtures, public item evaluations or longer display permission. Pending records contain
item identity, dates, hashes and disposition, not raw seller content. Hydrated work has listing
foreign keys and seller-deletion cascades; rehydration still checks stable seller tombstones.
The scanner updates current snapshots without adding full observation snapshots on every
refresh. A 30,000-reference per-watch ceiling bounds new ledger growth and pauses before
committing an over-capacity page; it is an explicit storage constraint, not claimed coverage.
No existing observations, dismissals or notification history are erased to make room.

## Resource accounting and scheduling

A chunk allows eight page operations, 16 detail operations and a hard 40 Browse **attempt** cap
including retries/401 renewals. It stops issuing new work after 150 seconds; a bounded in-flight
HTTP request may finish later. Existing workflow and lease timeouts remain final recovery
bounds. Pending chunks become due in one minute and normally catch the next ten-minute Neon
tick; completed work returns to the ordinary 30-minute interval. Actual scheduler delays remain
measured, not guaranteed. Earliest due watch selection, per-watch chunks, query rotation and
reserved old-lead work prevent a large baseline from monopolizing one invocation.

All live Python Production Browse consumers now debit a shared PostgreSQL row **before each
attempt**, including retries. Official Developer Analytics readings refresh at most every five
minutes in that guard, under the same row lock. Lower live balances are honored; balances are
never increased within an unexpired provider reset window. Missing/malformed telemetry fails
closed. A 200-call reserve remains. The worker additionally obtains a live preflight reading.
Other jobs using these credentials outside this repository cannot be atomically reserved;
telemetry lag and the time between readings remain uncertainty, covered only partly by the
reserve. OAuth, catalog, and Analytics have their own limits, not the Browse pool. Catalog
alternatives are fetched once per watch chunk, not once per listing; attempts and retries are
counted. `getItems` is restricted to selected partners, and COMPACT lacks the full pressing and
stable-seller evidence this path requires; neither is assumed available as a batch shortcut.

Estimated steady-state Browse cost (before retries): twice the page count of each incremental
window, plus twice the page count of each daily full reconciliation, plus approximately six
refreshes per plausible lead/day and one per rejected listing/day. Initial hydration adds one
request per unique uncached candidate. For illustration, six one-page queries across three
watches cost `6 * 2 * 48 = 576` incremental search calls/day, plus 12 daily reconciliation pages.
100 plausible leads and 200 rejected references add roughly 800 detail requests/day, yielding
about 1,388/day before retries, newly discovered items, and other consumers. This is a scenario,
not measured production demand. Continuous backlogs can exceed the daily quota; the guard
pauses work with visible partial coverage rather than reducing depth or incurring costs.

## Rollout and rollback

Deploy through **Deploy private watchlist** after the normal Python, Node, lint, format, type,
and isolated PostgreSQL gates. `discovery_slots=1` enables one existing slot; `1,2,3` expands
only after bounded live validation. `unchanged` preserves the rollout. An explicit slot selection makes those unleased watches
due for one bounded validation chunk without changing revisions or prior successes. `off` restores the old
scanner for rollback without dropping data. Old and new scanners never run for the same watch
in one invocation, and both use the shared budget guard. Rolling back code leaves additive
tables intact. Never invoke destructive pilot-schema rollback on production.

The isolated PostgreSQL rehearsal covers migration/backup/restore, discovery transaction
writes, seller-deletion cascades and three concurrent quota contenders competing for two calls
above the safety reserve. Public outputs contain only aggregate counters. Rollout must report
pages, unique references, pending/evaluated counts, quotas, resume evidence and scheduled-run
evidence separately. Do not reset the existing observation window.

## Official references checked

- [Search pagination, filters, sort and fields](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [Buy filters: itemStartDate, buyingOptions, worldwide location and destination](https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html)
- [Origin timestamp release note](https://developer.ebay.com/api-docs/buy/browse/static/release-notes.html)
- [Discovery and refresh: COMPACT, stable seller IDs and restricted getItems](https://developer.ebay.com/develop/guides/buy/inventory-discovery-and-refresh-guide)
- [Application quota telemetry](https://developer.ebay.com/api-docs/developer/analytics/resources/rate_limit/methods/getRateLimits)

The current documentation site's search page sometimes resolves to its API overview; indexed
official search documentation still states the 200-item/10,000-result bounds. Live window
validation is required rather than relying on documentation alone.

## Controlled production evidence, September 23

Deployment #14 installed migration 3 and the paginated dashboard, but no watch was due;
that successful deployment did not validate retrieval. Deployment #15 recorded one quota
pause before any Browse request: its new shared parser rejected differently capitalized API
metadata that the preflight parser accepted. The reserve was not bypassed. PR #63 aligns the
parsers and adds synthetic regression coverage and fixed, content-free diagnostics.

Deployment #16 then retrieved 276 unique references for one existing watch: eight search
requests (three committed initial pages plus five incremental pages), 16 detail requests,
16 evaluations and 260 durable pending evaluations. The preflight reading was 4,230 remaining.
All returned origin timestamps passed the requested start-window validation on EBAY_US.
The initial pass was still partial (zero of three queries exhausted), accurately displayed.
This is independently searched inventory, not manually supplied item URLs; no pressing
accuracy or relevant-lead recovery claim is inferred from the larger count. Existing device
registration and the operational observation start remained intact, including the quota pause.

316 Python tests and 32 Node tests pass, together with Ruff/formatting/TypeScript. The deployment
also passed isolated PostgreSQL migration, deletion, shared concurrent budgeting and synthetic
backup/restore checks. Authenticated Discogs validation passed in smoke run #43. Further
resume, full rollout and unattended invocation evidence must be assessed separately.
