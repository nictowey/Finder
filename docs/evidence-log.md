# Finder evidence log

Dated operational and validation history, moved verbatim from `AGENTS.md` on
September 24, 2026 when Finder became a personal pressing hunter. Newer entries go at the top.

## September 24, 2026 — first owner verdicts exposed poor precision

The owner judged the initial listings and reported that most were wrong: some were different
pressings, and Nas “Rare” included other records altogether. Counts and specific false
positive examples were not available to the developer from the private dashboard. The review
policy now treats “Rare” as an ambiguous adjective unless the album is explicitly claimed,
rejects a contradictory structured release title, and demotes “Likely yours” to “Unclear” when
another retrieved pressing fits the same evidence. Judged listings leave the default review
queue and cannot cause another notification for that item; per-watch verdict counts expose
the measured outcome privately. The new policy is re-applied to stored listings without
refetching every old item. These changes reduce known false positives, but have not yet been
validated against the owner’s specific judged listings or measured for recall loss.

## September 24, 2026 — review inbox visibility investigation

The public scan log for run #83 reported 731 unique references across five processed watch
chunks, 701 evaluated outcomes, 29 pending, and one new inbox row in that run. These are
aggregate counts, not proof that the Nas or Larry June / 2 Chainz examples were retrieved or
that any particular row met its price ceiling. The dashboard opened on `possible_pressing`
only, while seller titles that establish artist and album without pressing evidence are
classified `family_review`. The default inbox now includes both reviewable tiers, and each
watch shows aggregate counts by classification. The saved price and six-hour evidence
freshness filters remain in force; `All prices` can expose older references with withheld
seller detail. Production classification for the named examples still needs an authenticated
inbox check after deployment.

## September 24, 2026 — personal pressing hunter

Implemented, not yet deployed: legacy sampled scan path removed; up to 20 watches with an
adaptive new-listing cadence; Discogs master-version comparison and cheat-sheet suggestions;
likely / unclear / likely-other tiers; a separate price for unclear listings; auction alerts
near the end; barcode and owner-defined extra searches; listing photos, verdicts and a seller
question in the inbox. Paid photo reading was declined to keep add-ons free. See
[the plan](personal-pressing-hunter-plan.md). Live request use, alert timing and tier accuracy
are not yet observed.

## September 24, 2026 — first scans after deployment

Deployment [#21](https://github.com/nictowey/Finder/actions/runs/35982251931) passed all gates.
Its first scan completed one watch (22 references, 8 searches, 16 detail reads). Two of the
next three scheduled runs completed two watches and reported one watch failed. PR #70 contained
barcode-search failures and added error-type counters; run
[#67](https://github.com/nictowey/Finder/actions/runs/35986120957) then showed the failing
search was a keyword search (`ResponseError`), not the barcode search. The search validator
treated two routine eBay behaviors as corruption: an empty or summary-less page before eBay's
estimated total, and a listing dated just outside the requested window. Both are now tolerated
(the latter counted as `window_mismatches`), and failures report a fixed code.

## Resumable discovery revision — September 23, 2026

The new implementation replaces scheduled samples with durable full search passes, overlapping
incremental windows, periodic reconciliation, a deletion-aware evaluation queue, shared
request debits and a paginated inbox. See [the discovery contract](resumable-discovery.md) for scope,
completion states, failure behavior, estimates and rollout/rollback controls. Earlier sampled
scan descriptions below are historical and remain applicable only while rollout is disabled.
Validation: 317 Python and 32 Node tests, Ruff and TypeScript. Deployment #19 at f9ab586 passed
all gates. All six current queries exhausted their initial passes: 434 retained references,
175 evaluated and 259 pending at the last observation. A scheduled Neon catch-up at 22:15 UTC
resumed evaluation; the notification step had no eligible alerts. See the discovery contract
for request counts and the earlier recorded quota-parser pause. Daily reconciliation and
real-listing phone delivery remain unobserved. Existing operational history
is retained. No marketplace recall or pressing-accuracy claim follows from this change.



## Project state before September 24, 2026

## Current project state

### Current owner decisions and operational delivery

The owner reconfirmed on September 23: build the private pressing watchlist first, keep
hosting free, and send clearly labeled **review leads** even when retrieved pressings remain
ambiguous or catalog search is capped. Policy v4 defaults to `review_leads`; `strict` is a
per-watch option. Failed alternative retrieval, explicit conflicts, stale details, and invalid
ceiling/destination/condition evidence still block alerts. Never promote a review alert to an
exact pressing, bargain, or valuation claim. This supersedes the v3 incomplete-search alert
hold for review mode only.

Operational history is now implemented in additive migration 2: due/start/finish timestamps,
expired and superseded attempts, quota pauses, bounded counters and dispatch correlations.
The protected dashboard exposes current overdue/stale state and a 14-day history view.
It never certifies reliability from elapsed days or successful workflow counts. A Home Screen
manifest and a rate-limited, owner-only test notification support device activation. Public
notification logs report push-service acceptance, not device receipt.

Read `docs/pilot-acceptance.md` for the next evidence and usability gates. Current checks:
292 Python and 29 Node tests, Ruff and TypeScript pass locally. Production deployment #13 passed the additive upgrade, isolated synthetic backup/restore
rehearsal, and private-access checks. The owner subsequently confirmed visible test delivery; unattended real-listing device delivery remains unverified. These changes
add no paid services and do not raise the three-watch cap.


The accuracy-gated development sequence and product decisions are maintained in `ROADMAP.md`.
Read it before proposing or implementing a new phase.
The active delivery order is `docs/identification-roadmap.md`: identify competing pressings,
account for search gaps, add resumable discovery, collect permitted manual adjudications, and
measure on a holdout. Prioritize these over interface polish or more categories.

The current product blockers are permission for a buyer-facing eBay deal signal, a lawful,
commercially usable source of sold transactions, and a decision on using Discogs API-derived
catalog data in a public view linking to eBay. Production Browse access by itself does not
authorize price modeling or prove that an undervalued-listing product is viable. The collector
watchlist without a fair-value claim can be developed and tested internally while these are
resolved; public display requires the outbound-use decision.
The open provider-use decision and bounded Production audit are documented in
`docs/decisions/0001-provider-use.md` and `docs/production-audit.md`.

### Completed

1. Phase 1 — eBay ingestion foundation
   - Configurable eBay monitor and official Browse API client
   - Listing normalization, total acquisition cost, structured logging, retries, and errors
   - Marketplace/item-ID upserts with first/latest observation timestamps
   - Mocked API, normalization, configuration, persistence, and scan tests
2. Discogs catalog identity foundation
   - Authenticated catalog search and release normalization
   - Generic Product and Variant persistence
   - Deterministic listing-to-variant candidate scoring
   - Secret-backed GitHub Actions smoke test
3. Vinyl identity evaluation foundation
   - Vinyl-specific metadata extraction outside the generic domain
   - Barcode, catalog number, artist, title, year, format, color, edition, and country evidence
   - Deterministic ranking with explicit ambiguity and conflict handling
   - Twenty-case hip-hop vinyl policy evaluation set using synthetic identifiers
   - Append-only listing observation history
   - Sanitized eBay response replay tooling
4. Live eBay Sandbox validation
   - Environment-scoped keysets (`EBAY_SANDBOX_*`, `EBAY_PRODUCTION_*`) that are never mixed
   - Bounded OAuth → Browse search → normalization → persistence round-trip script
   - **eBay smoke test** workflow: Sandbox on push, Production on manual dispatch only
5. Production deletion-compliance foundation
   - Stable eBay seller IDs on Production listings
   - Shared PostgreSQL storage and signed deletion endpoint on Neon Functions
   - Seller tombstones to prevent reimport after deletion
6. Provisional pressing decision contracts
   - VinylFingerprint and copy-level CollectibleAttribute models
   - Versioned MatchDecision with source EvidenceRecords, conflicts, and missing evidence
   - Separate family and probable-variant decisions in the match CLI; exact outcome withheld
7. Bounded multi-query Discogs candidate retrieval
   - Up to one valid seller barcode, one catalog number, and one title search; at most 25
     release detail lookups (default ten), with duplicate releases removed
   - Incomplete search coverage prevents a provisional probable-variant decision
   - Authenticated synthetic-listing validation passed on September 23, 2026; ten releases
     included four strong candidates, so live catalog ambiguity is real
8. Internal exact-target eBay discovery prototype
   - Versioned, configurable, at most three queries of ten items, with initial and refresh modes
   - DS2 title-alias plan and cross-query item deduplication; a bounded Production scan found
     the user-supplied example on September 23, 2026, but target recall is unmeasured
9. Internal target-family candidate retrieval
   - A saved release is hydrated directly and a bounded catalog-derived artist/title query
     adds potential competing releases alongside seller-derived searches
   - The catalog query does not count as seller-text recall or prove complete family coverage
10. Anonymous pressing comparison prototype
   - Manual target check reports target and same-family candidate positions, field-level
     agreement, seller/catalog disagreements, missing evidence and unscored runouts
   - Public job summary contains no listing, seller or release identities or evidence values
11. Cross-genre measurement baseline
   - A synthetic policy panel spans jazz, rock, classical, electronic, and folk with paired
     candidate pressings and explicit abstentions; it does not estimate real-listing precision
   - A bounded Discogs title probe checks catalog-derived seller-like titles without pinning
     the selected release. See `docs/vinyl-measurement-2026-09-23.md` for its limits.

12. Private collector watchlist loop
   - Persistent targets, bounded 30-minute schedule, leases, review inbox and browser push outbox
   - Owner-only verified-email login is configured and signed-in dashboard access passed
   - Live migration/deletion checks and two-watch scheduled scan passed; the inbox shows
     provisional leads for both saved targets, including the known DS2 listing
   - Watch creation and inbox filters passed in the hosted dashboard; push delivery on the
     owner's own device is still unverified
   - Scheduled policy v3 retrieves up to five alternatives once per watch; unresolved
     competing pressings and explicitly incomplete or failed alternative searches remain
     visible but suppress notifications. Catalog coverage is still bounded.
13. Bounded inventory reconciliation and known-lead refresh
   - Per-query caps, request counts and cursor progress persist in the private watch summary
   - Eight newest items per query; alternating six-item older samples and one direct known-lead
     recheck; stale results still need independent coverage and availability measurement
   - A disappeared/ended direct recheck is marked unavailable, never inferred sold
   - Developer Analytics now gates each due watch against the shared Browse quota with a
     worst-case request allowance and a reserve; there is still no atomic reservation across
     independent workflows or guarantee of future capacity
14. Scheduler catch-up activation
   - GitHub's twice-hourly cron delivered only two scheduled watch runs on September 23;
     sustained timeliness is unproven
   - The independent Neon trigger is deployed for due, unleased watches using a
     repository-scoped Actions-write credential. A due-time `workflow_dispatch` at 20:05:03 UTC
     completed three scans; longer-term delivery latency remains unmeasured
   - Each watch is due at least 30 minutes after completion, bounding daily scan volume

### Verified baseline

- Latest verified implementation baseline: current `main` after required checks
- Offline suite: 292 Python and 29 Node tests passing; rerun required checks before commit
- Ruff lint and formatting checks passing
- Live Discogs validation passing through GitHub Actions
- Multi-query Discogs smoke passed on the `discogs-candidate-retrieval-2026` branch; this uses
  a synthetic seller listing derived from a real catalog release, not a labeled eBay listing
- Live validation: five releases persisted, one unambiguous strong candidate scored 100,
  and a conflicting barcode was rejected
- Live eBay Sandbox validation passed through GitHub Actions: application OAuth token issued,
  `vinyl` search returned 10 items, 9 normalized and round-tripped through persistence
  (1 skipped as ended). Sandbox inventory is test data, not vinyl market data.
- Live inventory deployment #6 passed; two saved watches completed, one inbox row was added,
  and the dashboard showed six sampled older results per target. Subsequent private scan #4
  directly refreshed the known DS2 lead into the active inbox, with four of five retrieved
  alternatives still compatible and its alert withheld. This did not measure target recall.
- Read-only Developer Analytics quota check #3 reported a 5,000-call daily `buy.browse` limit
  with 4,940 remaining at approximately 11:54 UTC on September 23. The nominal three-watch
  daily refresh maximum of 4,464 calls does not reserve capacity for other work or retries.
- Third live watch first scan completed all three watches but exposed a false artist conflict
  on an inverted seller name. The correction for exact comma inversion and competing album
  title claims is merged and deployed; the fresh live third-watch scan showed eleven visible
  rows with identifier conflicts, none with the false artist-conflict reason. No positive
  exact-pressing call was validated. Scheduled workflow runs were roughly five hours apart
  on September 23, so scan freshness is unproven.
- A non-retaining panel sampled all eleven owner-supplied targets across three successful
  batches. Ten of twelve five-result query pages were capped; its possible/family/conflicting
  counts are unverified policy output, not measured pressing accuracy or discovery recall.
- [Production deployment #11](https://github.com/nictowey/Finder/actions/runs/35905240731)
  passed all tests, isolated PostgreSQL checks, private-access checks, and three live due
  watch scans under the quota guard. Zero scans failed or paused; eight new inbox rows appeared.
  This is one successful scan, not a scheduler reliability measurement.
- [Production deployment #12](https://github.com/nictowey/Finder/actions/runs/35910197545)
  passed the same gates with the independent Neon trigger configured. Three due watches
  completed without failures or quota pauses, adding five new inbox rows. The repository-scoped
  dispatch token is stored as a production environment secret and expires October 23, 2026;
  a due-time `workflow_dispatch` at 20:05:03 UTC subsequently completed three watches with
  zero failures or quota pauses and ten new inbox rows. The signed-in dashboard showed fresh
  last-success times; sustained scheduler reliability remains unmeasured.

- [Production deployment #13](https://github.com/nictowey/Finder/actions/runs/35920314329)
  deployed PR #59 at `16c82ba8d0da2e7bcc2ec10bea2e43b0bb71b370`. All required checks,
  isolated PostgreSQL migration/deletion and synthetic restore/upgrade checks, and anonymous
  access/CSRF checks passed. Production history began at 21:07:25 UTC on September 23.
  No watches were due during deployment: zero scans attempted, not three successful scans.
  The signed-in dashboard shows the health/history panel, review-mode settings and zero
  registered notification devices. The first new correlated unattended dispatch remains
  pending, as do sustained timing, discovery accuracy, and phone delivery evidence.

### Production status

The free Neon Production branch and deletion Function are deployed with Production credentials.
Candidate persistence now guards against recreating eBay-derived evidence after a seller
deletion. Isolated PostgreSQL migration, lease, deduplication and deletion-cascade checks now pass.
Simultaneous seller-deletion races remain unmeasured; see the data
inventory in `docs/decisions/0001-provider-use.md` before expanding retention.
eBay accepted the endpoint and sent test notifications; new deletion tombstones appeared in the
shared PostgreSQL database. The September 22, 2026 Production smoke workflow fetched,
normalized, stored, and read back 10 live listings. This validates ingestion, persistence, and
the signed deletion test path. It does not prove exact vinyl matching or deletion of a real
seller's data. Keep the Production and Sandbox keysets separate.
The September 23 manual DS2 target scan completed two bounded queries, with 12 distinct
normalized results and two overlaps. A private probe found the known listing in that run.
The first query reached its ten-item cap; exact pressing identity and broader recall remain
unverified. See `docs/production-audit.md` for aggregate run records.
The private DS2 match check now identifies the album family and a bounded catalog query surfaced
five other same-family releases among ten evaluated. The catalog query hit its cap; the specific
numbered copy, catalog coverage, and any real-listing precision remain unverified.
The September 23 anonymous pressing comparison rendered successfully in the manual workflow.
Seller title and item specifics claimed numbering, and the pinned target alone was catalog-marked
numbered among six sampled same-family releases. No individual-copy verification was performed.
The September 23 broad Production scan fetched and persisted ten new listings without skips,
enrichment failures, or persistence drift. It is the second distinct successful UTC day of the
seven-day audit; the sample hit its page cap and had no verified buyer destination context.


## Near-term priorities before September 24, 2026


Follow the ordered deliverables in `docs/identification-roadmap.md`. Bounded alternative
comparison, the first inventory-reconciliation pass, a direct known-lead refresh, and an
application quota reading are observed; next independently audit capped-search misses and
account for shared request use, then add permitted manual review.
The dependencies below still govern the corresponding real-data and release decisions.

1. Resolve and record eBay's intended-use, retention, and evaluation rights and qualify a
   permitted sold-comparables source before treating price-based deal detection as buildable.
2. Audit bounded Production scans with aggregate statistics; collect real fixtures and manually
   label listings only after the relevant data-use decision.
3. Measure `vinyl-decision-v2` on a development and held-out evaluation set; report precision,
   abstention, and catalog candidate-retrieval failures separately.
4. Expand bounded Discogs candidate retrieval, including variants sharing identifiers.
5. Complete seven bounded Production scans and field-quality measurements.
6. Build an internal exact-item watchlist prototype with synthetic listings; involve outside
   collectors only after ingestion, identity, listing-use, and Discogs outbound-use gates.
   Withhold fair-value or "steal" claims.
7. Compare target search results with a permitted manual sample to measure misses, including
   sellers that omit the artist, title alias, or edition, before suggesting coverage.

The eBay deletion endpoint and manual bounded cloud scans support Production data validation.
The owner authorized the private watchlist loop on September 23, 2026: recurring bounded
scans, an authenticated review inbox, and notifications for unverified possible pressings.
Implement that pilot without claiming exact identity, complete recall, or undervaluation.
See `docs/private-watchlist.md` for scope, deployment and acceptance checks. The broader public
launch and valuation gates remain unchanged.
