# Private Discogs-only Finder

October 24, 2026: the owner approved replacing Finder's local primary interface with a
private Discogs-only target tool. This branch implements the **rules preparation and
synthetic dry-run slice**, not a functioning marketplace sniper.

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

## Implemented

- Exact Discogs release ID or canonical HTTPS release link as the target key.
- Separate minimum media/sleeve grades; numeric seller percentage and feedback minimum.
- Positive all-in ceiling, explicit currency and shipping country/postal destination.
- Durable local SQLite settings with optimistic revision checks and one target per release.
- Owner-entered already-owned marker blocks repeated eligibility for that release.
- Synthetic evaluator with explicit rejection reasons. Missing shipping, tax, fees, condition,
  reputation, matching destination, recent observation or pressing evidence fails closed.
- Decimal arithmetic, inclusive maximum, no currency conversion, no secrets or external calls.
- Loopback-only listener, host validation, CSRF/origin checks, bounded JSON requests and CSP.

A synthetic 'confirmed' evidence value is a test input, not verified real seller identity.
A matching release ID alone cannot prove the physical copy is correctly listed. Passing
rules means eligible for review only; every result says `purchase_allowed: false`.

## Deliberately not implemented

Live listing ingestion, scraping, real-time monitoring, automatic cart/order/payment,
credentials, notifications, deployment and a real-purchase ledger are absent. There is no
hidden enable-purchases switch. Owned markers are manual bookkeeping, not orders or receipts.
The evaluator accepts synthetic observations only and does not persist them.

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
No network capability or purchasing is enabled by saving settings on this page.

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
- No live provider, checkout, purchase or production deployment verification was attempted.
