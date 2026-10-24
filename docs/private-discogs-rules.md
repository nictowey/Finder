# Private Discogs-only Finder

October 24, 2026: the owner approved replacing Finder's local primary interface with a
private Discogs-only target tool. This branch implements the **rules preparation and
synthetic dry-run slice plus explicit known-listing read**, not a functioning marketplace sniper.

The subsequent cloud continuation adds optional aggregate release availability and scheduled
rechecks of supplied listing IDs; see [cloud monitoring](discogs-monitor.md). The loopback
development fixture remains manual. Automatic release-listing discovery remains unimplemented.

## Run locally

Install the project with its development dependencies in an isolated Python environment.

```sh
python -m finder.discogs_web --database /tmp/finder-discogs.sqlite3 --init
# Later, reopen without --init:
python -m finder.discogs_web --database /tmp/finder-discogs.sqlite3
```

The installed `finder-local` command now opens this page. The prior review workspace is
retained as `finder-review` (or `python -m finder.local_web`). Use a new database filename;
legacy databases are rejected rather than migrated, overwritten or emptied. Production,
existing watch data, hosted workflows, provider credentials and the old eBay code are unchanged.

## Explicit known-listing read (opt-in)

Start with `--live-listing-read` to enable a manual public API read from an exact listing
ID or canonical Discogs sell/item URL. The default remains network-disabled. This opt-in
mode uses a fixed official API endpoint with timeout, bounded response, no redirects, no
credentials, at most 20 attempts/minute and a rate-limit cooldown. It does not enumerate
listings or run in the background. Environments using SOCKS proxies need `httpx[socks]`.
The loopback app retains host/origin/CSRF guards; the offline audit network guard is not
used in this explicit network-enabled mode. No other provider operation is implemented.

Data provided by Discogs: https://www.discogs.com/developers#page:marketplace

API-reported shipping is shown separately, never accepted as a final destination quote.
Shipping-blocked/unknown status is disclosed. Tax, fees, destination and physical pressing
evidence remain unverified; no live read can authorize a purchase or meet all-in eligibility.
A real public adapter read succeeded on October 24; no cart/order/payment was attempted.
Only normalized bounded fields are returned, including bounded top-level listing comments as
untrusted text data. Raw provider payloads, images and credentials are not retained.

## Implemented

- Exact Discogs release ID or canonical HTTPS release link as the target key.
- Separate minimum media/sleeve grades; numeric seller percentage and feedback minimum.
- Positive all-in ceiling, explicit currency and shipping country/postal destination.
- Durable local SQLite settings with optimistic revision checks and one target per release.
- Owner-entered already-owned marker blocks repeated eligibility for that release.
- Synthetic evaluator with explicit rejection reasons. Missing shipping, tax, fees, condition,
  reputation, matching destination, recent observation or pressing evidence fails closed.
- Decimal arithmetic, inclusive maximum, no currency conversion or secrets. External calls
  require the explicit known-listing startup option.
- Loopback-only listener, host validation, CSRF/origin checks, bounded JSON requests and CSP.

A synthetic 'confirmed' evidence value is a test input, not verified real seller identity.
A matching release ID alone cannot prove the physical copy is correctly listed. Passing
rules means eligible for review only; every result says `purchase_allowed: false`.

## Deliberately not implemented

Automatic listing discovery, scraping, real-time individual-listing monitoring, automatic cart/order/payment,
credentials, external notifications, deployment and a real-purchase ledger are absent. There is no
hidden enable-purchases switch. Owned markers are manual bookkeeping, not orders or receipts.
Synthetic JSON input accepts only synthetic observations. API observations are constructed server-side
and cannot be supplied through the synthetic input endpoint. Neither is persisted.

Native Discogs saved searches are a potential permitted discovery source, not an integrated
source here. Current official documentation describes exact Wantlist release, condition,
seller, shipping and price filters with Realtime/Daily Digest notifications:
https://support.discogs.com/hc/en-us/articles/26355982507149-Using-Filters-Saved-Searches

Before any live integration, verify a documented permitted transport and purchase route.
An unsupported API is not proof that every browser purchase is forbidden, but it is not
permission to evade access/security restrictions either. Personal use does not remove
Discogs' automated site data-collection restrictions:
https://support.discogs.com/hc/en-us/articles/360009334333-Terms-of-Service
https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use

Any future purchasing needs explicit concrete owner limits and purchase authorization,
validated checkout totals for the actual destination, fresh availability, transactional
one-copy protection, reconciliation after uncertain outcomes, and no blind retry of orders.
No network capability or purchasing is enabled by saving settings on this page. Explicit known-listing
reads require the separate server startup opt-in described above.

## Local verification (October 24, 2026)

- Python full suite: 2,901 passed, 41 subtests passed in the initial validation.
  The pre-push rerun passed 2,900 with one unchanged PGlite subprocess SIGKILL;
  that projection test file passed both tests immediately when rerun in isolation.
- New focused rules/server/DOM coverage: 61 passed.
- Node suite with `--test-concurrency=1`: 110 passed. The default parallel run and overlapping
  suites experienced SIGKILL in resource-heavy unchanged PGlite tests; isolated serial execution passed.
- `ruff check src tests scripts`, `ruff format --check src tests scripts`, `npm run typecheck`
  and `git diff --check` passed.
- Offline DOM tests cover edits/reloads, paused and owned targets, repeated clicks, stale
  revisions, unknown save outcomes, missing/mismatched evaluated rule revisions and escaping.
- Chromium screenshot verification could not run: sandbox socket access was denied and the
  permitted escalation failed during environment setup. No visual screenshot claim is made.
- Actual loopback HTTP smoke flow passed: page/snapshot, target save, stale duplicate rejection,
  inclusive-total evaluation, owned blocking, and absent checkout route.
- CI reruns the new rules/server/DOM suite after Node/jsdom installation so browser-state
  checks are exercised rather than skipped for missing dependencies.
- Known-listing public API read verified; no checkout, purchase or production deployment attempted.


### Known-listing update verification

- 94 focused adapter/rules/server/DOM tests passed, including malformed fields, unsupported
  currencies/precision, unknown shipping/tax, fixed endpoint, timeouts, no redirects and cooldowns.
- Aggregate Python rerun: 2,930 passed, four unchanged Node/PGlite subprocesses were SIGKILLed;
  all four passed the isolated retry. Ruff, format and typecheck passed.
- Direct public adapter read succeeded. A later end-to-end server live read returned a
  sanitized 502, so live transport reliability is not established. It did not produce an
  eligibility result or trigger a retry, cart, order or payment.


## Complete playable record sets

Media grade is separate from completeness: a VG+ copy can still be missing a record.
Both synthetic evaluation and known-listing monitoring now fail closed on `unknown` or
`incomplete` playable-vinyl completeness, independently of matching release ID, grades and price.
The response exposes `media_completeness` with `status`, `release_id`, `expected_media_count`,
`reasons`, `packaging_uncertain`, and `verified: false`. Status is `reported_complete`,
`incomplete`, or `unknown`; it never means the actual contents have been independently verified.

The pure assessor accepts only separately supplied authoritative exact-release catalog metadata:
integer `id` matching the listing release, and a bounded `formats` array with `name: "Vinyl"`
and valid `qty` values totaling 1–100 playable records. It does not infer quantity from titles,
barcodes or an embedded seller/listing release summary. Mixed or unsupported formats, malformed
quantities and mismatched metadata remain unknown. This change adds **no catalog request or
new route**. Current live listing reads therefore remain unknown unless the application is
separately integrated with authorized authoritative catalog metadata.

Only the official listing response's top-level `comments` string supplies seller-text evidence.
The projection retains at most 4,096 characters as inert text, no raw response. Truncation, HTML,
controls, non-ASCII text, unrecognized language/prose and ambiguous or contradictory quantities
cannot produce a positive assessment. Clear missing-media reports in the bounded portion still
reject. Explicit missing-record reports take precedence over complete-set claims. A narrow
recognized statement such as "Includes both discs" can produce `reported_complete` only when
the authoritative expected quantity is exactly two. Absence of missing-media keywords does not
establish completeness; this is a conservative English-phrase checker, not a semantic guarantee.

Missing inserts, posters and other optional packaging are reported separately as packaging
uncertainty, not automatically counted as missing playable media. No existing saved target or
listing data is rewritten as complete. Earlier synthetic positive test cases supply explicit
made-up catalog quantities and complete-set comments. Every evaluation and monitor review
still returns `purchase_allowed: false`.
