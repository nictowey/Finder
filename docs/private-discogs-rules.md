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
credentials, at most 20 attempts/minute and a rate-limit cooldown. An explicit review may
also read the exact saved release catalog entry, as described below. It does not enumerate
listings or start background work. Environments using SOCKS proxies need `httpx[socks]`.
The loopback app retains host/origin/CSRF guards; the offline audit network guard is not
used in this explicit network-enabled mode. Cart, checkout and order operations are absent.

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


## Playable record sets and disclosed omissions

Media grade is separate from completeness: a VG+ copy can still be missing a record. Expected
record quantity comes from each exact release, never a blanket two-disc assumption. The owner
clarified that normal seller descriptions need not state the quantity or affirm a full set.
Fully read supported comments with no disclosed omission produce `no_disclosed_media_omission`,
not verified completeness. An explicitly present empty API `comments` string means no seller
comment was provided; absent/null/unreadable comments remain unknown.

The response exposes `media_completeness` with `status`, `release_id`, `expected_media_count`,
`reasons`, `packaging_uncertain`, and `verified: false`. The four statuses are:

- `incomplete`: clear missing/partial playable media, a hard rejection despite matching ID/grades.
- `unknown`: unavailable metadata/text, unsupported text, or an ambiguous omission warning.
- `no_disclosed_media_omission`: no omission disclosed in available comments, not proof of contents.
- `reported_complete`: an actual unambiguous seller claim consistent with the catalog quantity,
  still not independently verified. No positive full-set phrase is required for ordinary review.

Both evaluators block incomplete and unknown results. They do not guess quantity from titles,
barcodes, tracks, sides or a listing's embedded release summary. Authoritative metadata requires
an exact matching integer release ID and a bounded `formats` array containing only Vinyl
entries with valid `qty` values totaling 1–100 playable records. Mixed/unsupported formats and
malformed or mismatched quantities remain unknown.

Only the official listing response's top-level `comments` string supplies API seller-text
information. At most 4,096 characters are retained as inert text, no raw provider payload.
Truncation, markup, controls, non-ASCII text and recognized unsupported-language hints cannot
pass an omission check. Ordinary English prose is not restricted to a vocabulary or complete-set
phrase whitelist. Language/omission heuristics cannot guarantee translation, discover every
possible disclosure, or verify actual physical contents. Ambiguous warnings such as "might be
missing" or "not sure if all records" stop review. Explicit omissions override seller completeness
claims. Routine format/playback text such as "2LP" or "disc1 plays well" is not itself an omission.

Missing inserts, posters and other optional packaging are flagged separately, not automatically
counted as missing playable media. No existing saved data is rewritten as complete. Synthetic
fixtures include explicit made-up release quantities, including single-record releases. Every
evaluation and monitor review still returns `purchase_allowed: false`.

### Explicit catalog metadata integration

The existing explicit known-listing endpoints now call `read_for_release(listing, saved_release)`:

1. Read the requested public listing using the existing bounded, credential-free transport.
2. If its reported release differs from the saved target, do not fetch any catalog release.
3. Otherwise use fresh cached catalog formats or make one public `GET /releases/{saved_release}`.
   No alternate endpoint, redirects, retries, credentials or browser fallback is introduced.
4. Reserve the same request budget for each GET. Only the second request may wait once, at most
   three seconds, for a slot. Contention or a longer cooldown leaves metadata unknown rather than
   retrying. Each HTTP request retains its existing eight-second timeout and 256-KiB response cap.

The default budget is lock-protected and local to its instance/process, not a global guarantee
across independent processes. Cloud explicit reads reserve the existing durable monitor budget
for each GET, shared with scheduled listing/statistics work. No transaction or application lock
is held while waiting or performing the explicit read. Stop/control/rules changes are checked
before each slot and after the operation; changed controls/rules fail closed. Rate-limit cooldowns
persist even if Stop is pressed during the response. The UI allows 25 seconds for this explicit
operation; a client timeout does not prove the server request was cancelled.

The in-memory metadata cache holds at most 100 exact-release entries for less than six hours.
Only normalized ID/formats and `source: discogs_release_api`, release ID and UTC ISO `fetched_at`
provenance are retained. No database migration or raw catalog payload is needed. Expired, future,
mismatched or malformed metadata cannot qualify, including when a previously normalized listing
is evaluated again. A failed refresh never reuses stale metadata. A cache hit needs only the
listing GET. Scheduled `read(listing)` remains exactly one GET and does not fetch or silently
attach catalog metadata; no new watch, poller or background discovery is created.

Catalog failure preserves the normalized listing with unknown quantity/omission assessment,
unless its comments already clearly disclose incomplete media. All-in totals and the physical
pressing remain unverified regardless of the metadata result.
