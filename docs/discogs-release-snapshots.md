# Selected-release snapshot and diff core

This is a bounded import engine for an **already observed** selected Discogs release
marketplace page, not another discovery transport, polling worker or notification sender.
It extends the existing `ReleaseMonitor` as `release_snapshots` and uses the same tagged
rules SQLite database and cloud stop/control revisions. Public API release statistics stay
aggregate availability and are never converted to individual listing IDs. Imports never
create targets or add IDs to known-listing polling.

## Input and source boundary

The strict `RenderedReleaseSnapshot` schema accepts `source: discogs_rendered_release`,
an integer exact release ID, that release's canonical `https://www.discogs.com/sell/release/ID`
URL (an optional trailing slash only), a timezone-qualified UTC observation timestamp,
`status: observed | incomplete | error`, `complete`, `range_start`, `range_end`, `total`,
`page`, `has_next`, `rows` and the four boolean DOM assertions `release_identity`,
`listing_container`, `pagination`, `stable`. Optional `error_code` is a bounded lowercase
machine code, not raw HTML or error text. Optional `purchase_allowed` can only be false.

Each row has a decimal-string listing ID, its exact canonical item URL, and a `fields`
object containing `price`, `media`, `sleeve`, `seller`, `seller_rating`, `seller_feedback`
and `shipping`. These are displayed strings or null, not normalized prices, conditions,
ratings, eligibility or a delivered quote. Unknown fields remain null, never zero.

A complete observation requires all assertions true, observed status, page 1, no next
page, consistent start/end/total and exactly that many unique rows. A complete positive
page starts at 1 and ends at total. An explicitly asserted empty page must have all three
counts 0 and no rows. No page stitching or navigation is implemented. The separate DOM
collector must establish its own verified complete/empty rendered shape; merely providing
total 0 is not enough. Incomplete/error inputs may have null count and pagination fields.
Duplicate IDs, mismatched URLs, unsupported extra fields, contradictory completeness or
invalid types are rejected, never silently deduplicated, coerced or treated as empty.

Importing JSON is not authentication or proof of a live observation. The schema's source
label and DOM assertions are supplied evidence only. Results explicitly return
`source_verified: false`, `pressing_verified: false`, `qualifies_all_in: false` and
`purchase_allowed: false`. There is no purchase switch, verified physical-copy identity,
sale inference, final shipping quote, tax/fee calculation or notification transport here.

## Revision and failure contract

Capture rules, cloud-control and per-release baseline revisions **before** observing.
Pass them unchanged to `ReleaseSnapshots.ingest`; never refresh revisions to force a stale
observation through. Import requires an already configured, enabled, unowned target and a
released shared cloud kill switch. All three revisions are checked under one immediate
SQLite transaction. A competing complete import advances the baseline revision and wins
once; stale edits, stop/restart cycles and concurrent imports fail closed.

Observations must be at most five minutes old and no later than the injected current clock.
Each release's accepted attempt timestamp is strictly increasing. Invalid, stale,
future-dated, out-of-order, unconfigured or unauthorized imports cannot poison the watermark,
create a baseline or overwrite a success. A valid recent incomplete/error attempt updates
only attempt status/time, preserving the last successful complete snapshot and baseline
revision. The newer attempt watermark prevents a delayed earlier observation from replacing
that success. No failed/loading/racing observation becomes zero inventory.

## Changes and separate acknowledgment

The first successful complete snapshot establishes a baseline without changes. Later
complete observations compare listing identities, not just counts:

- `new`: an ID absent from the baseline and retained identity history.
- `disappeared`: a prior ID is absent from a complete observation. This does not mean sold.
- `reappeared`: a previously seen retained ID returns after absence.
- `changed`: one or more non-null displayed fields differ for the same ID.

Same-count substitutions produce both new/reappeared and disappeared changes. A different
ID with identical displayed fields remains different; a relisting cannot be confidently
associated with the old physical copy. New means new to this retained observation history,
not necessarily newly offered on Discogs. Whitespace and row order do not produce changes.
Unknown-to-known and known-to-unknown optional fields update the baseline without creating
field-change events. This avoids noise, but can miss a price/condition change across a gap
in field evidence. It does not establish that seller data or a quote was fully loaded.

Every successful complete observation advances its baseline revision, including unchanged
ones. Changes have durable, non-reused sequence IDs. A separate acknowledgment table records
private in-app review/delivery-dedup state without changing the baseline. Unacknowledged
changes remain pending across later observations and reopenings. Repeated acknowledgments
are idempotent, expired/unknown IDs fail, and a batch is atomic. Acknowledging sends nothing
and is not proof an external notification was delivered. Review acknowledgment can occur
while stopped but still requires current rules and control revisions.

## Bounds and additive storage

- At most 250 rows and 256 KiB per validated encoded snapshot.
- At most 100 selected releases with stored snapshots.
- Fields: seller 120 characters, shipping 240, other displayed strings 160.
- Latest successful baseline is kept durably for each configured release, even when stale
  or following failure. Freshness is exposed separately; there is no false fresh claim.
- At most 1,000 recently seen IDs per release; absent IDs expire after 30 days. Current
  baseline identities are retained. After expiry an ID may be classified new, not reappeared.
- At most 500 changes globally, for 30 days. Old acknowledgments are removed with their
  changes. Events lost to bounded retention are not a notification-delivery guarantee.

The additive tables are `discogs_release_baselines`, `discogs_release_seen`,
`discogs_release_changes` and `discogs_release_acknowledged`. Existing targets, ownership,
aggregate polls and known-listing polls are unchanged. This private SQLite schema is not a
migration of the legacy PostgreSQL watch schema and does not require a production DB run.

`tests/test_discogs_release_snapshots.py` uses synthetic IDs, source assertions and clocks.
It tests completeness and strict bounds, same-count swaps, field changes, missing fields,
empty/partial/error/racing input, preservation, stop/edit/concurrency/revision races,
future-time poisoning, restart, retention, separate acknowledgment and unchanged suppression.
These tests establish application behavior, not live authentication or reliable ongoing
Discogs access. No real observation, account, seller identity or source HTML is committed.

## Bounded rendered-page collector

`scripts/discogs_release_snapshot.mjs` exports the self-contained
`collectReleaseMarketplaceDom(expectedReleaseId)` for one already-loaded, authorized page.
It has no navigation, request, browser setup, credentials, schedule or purchase methods.
Only the literal selected `/sell/release/<id>` URL (optional trailing slash) is supported;
filtered/query URLs and multi-page results stop as incomplete. It requires the marketplace
header/table, two agreeing pagination ranges with at least one visible, matching release
form actions, named `limit` selects with equal page sizes, and no pagination actions.
Only the verified responsive pagination labels and the exact per-row `item_release_link`
href may be structurally read while hidden. The latter is reported release identity, not
physical pressing proof; listing fields and the listing row/title must be visible. A complete empty result requires the independently observed 0–0 of 0
shape, zero rows and the same structural checks; missing content never means zero.

Nonempty results require the exact row count, unique listing IDs, matching per-row release
links and loaded core displayed fields. Numeric release links may carry a safe title slug;
the slug is never used to select a release. Optional shipping remains null when unavailable.
Item price and displayed shipping are not a final destination-confirmed acquisition quote.
The final check rejects URL/count/row-identity changes during observation. Unrecognized
layout, loading, missing fields or errors return an incomplete bounded diagnostic and no
rows, never an empty-success observation.

The DOM fixtures test the observed responsive forms, zero and nonempty shapes, wrong
releases, duplicate/missing rows, partial pages, unknown fields and same-count row races.
Real-page collector verification is separate from durable-engine tests. No automatic page
collection has been enabled: permitted ongoing source acquisition, hosted browser/session
access and multi-page enumeration remain explicit integration gates. A DOM observation
can be submitted to the engine only for an already saved target with revisions captured
before the observation; this module creates no targets or live read transport.

On October 24, 2026, the exact final collector (SHA256
`c1b00901e243b6d2c769a9e898c154ba471affdae88ea243d104ff9ea786da0d`, with only the
module export removed for tool execution) passed a bounded authorized local-browser read
of a seven-row page: complete range/count agreement, seven unique listing IDs and all
structural assertions, with purchase unavailable. The same final collector also passed a bounded authorized read of the strict empty-page
shape at 22:11 UTC: matching zero ranges, zero rows and all four assertions. No targets were created and no cart was changed.
This validates these observed single-page shapes, not unattended collection, all Discogs
layouts, continuous access, authenticated import transport or physical pressing identity.

### Seller descriptions
The optional `fields.seller_description` is bounded to 2,000 characters. The collector reads
only the observed seller-comment paragraph, not grade help or whole-row text. Missing,
hidden, duplicate, expandable, clipped or oversized text becomes null. It remains untrusted
display data, never instructions or proof of a complete set. Existing baselines without the
field remain compatible. Known-to-known changes use the existing diff semantics; unknown
transitions are suppressed. A complete inventory snapshot does not mean each record set is
complete: that separate qualification gate requires exact-release format quantity and an
unambiguous supported seller report, and blocks unknown evidence.

On October 24, 2026 at approximately 22:21 UTC, the updated collector was exercised in the
approved local browser on a seven-row marketplace. All completeness assertions passed;
a VG+/VG+ row's seller paragraph explicitly reporting missing disc 1 and disc 2 only was
captured. The cart was unchanged. The child reported running the supplied logic but did
not independently verify its artifact hash. This confirms description capture in that
observed layout, not physical contents, classifier accuracy for arbitrary prose, automatic
purchase eligibility or hosted execution.
