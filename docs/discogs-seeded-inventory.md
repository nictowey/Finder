# Explicit seeded seller inventory observations

This standalone, default-off supplement observes **one explicitly supplied seller and one
exact release**, using official public GETs only. It is not a market-wide scanner and never
places orders, creates carts, sends notifications, changes aggregate watches, or reads email.
No seller or target is bundled, activated, or discovered by guessing names.

Implementation and tests are offline. No live validation, deployment, state initialization,
timer or notification binding is claimed by this document. A prior observation of a small
inventory is not proof of its current size or API availability.

## Explicit private configuration

The values below are **synthetic examples**, not a usable production seed. Keep real values
in an operator-managed private JSON file outside the repository and public logs. Existing
owner-supplied known-listing evidence must identify the seller and exact release. The program
verifies that association with the actual known-listing response before inventory access.

```json
{
  "version": 1,
  "seed": {
    "source": "owner_supplied_known_listing",
    "seller": "fixture-seller",
    "listing_id": 100,
    "release_id": 123
  },
  "targets": [{
    "release_id": 123,
    "currency": "USD",
    "under": "100.00",
    "min_media": "NM",
    "min_sleeve": "NM"
  }],
  "max_pages": 1,
  "max_detail_reads": 2,
  "poll_seconds": 600
}
```

All unknown fields are rejected. One seller, one target, exact release IDs, an explicit
positive item-price limit and explicit grades are required. Accepted currencies are USD,
EUR, GBP, CAD and AUD. The maximum is 1–3 inventory pages of 50 items, 1–2 detail reads per
invocation, and a durable polling interval of 120–86400 seconds. Default bounds are one
inventory page, two detail reads and 600 seconds. No criteria are imported from or written
back to aggregate rules. A different config cannot silently reuse/reset existing state.

## Executable first-proof path

Use the existing review environment and existing tagged aggregate rules database, under
its authorized operator/service identity. That database must already contain a valid shared
API guard and enabled aggregate control. This module never creates a replacement workspace,
initializes/repairs the shared guard, enables the aggregate control, or loads credentials.

From the checkout (or use the equivalent installed `finder-inventory` entry point):

```sh
# Validate config only. No database changes and no HTTP.
PYTHONPATH=src python -m finder.discogs_inventory --config /private/inventory.json

# Separate, explicit operator provisioning. No HTTP or activation.
PYTHONPATH=src python -m finder.discogs_inventory \
  --config /private/inventory.json --database /private/existing-rules.db --init-state

# First live proof, only after review and authorization.
PYTHONPATH=src python -m finder.discogs_inventory \
  --config /private/inventory.json --database /private/existing-rules.db --poll --snapshot

# Inspect retained private results and pending work, without HTTP.
PYTHONPATH=src python -m finder.discogs_inventory \
  --config /private/inventory.json --database /private/existing-rules.db --results

# A later explicit ordinary observation establishes a silent baseline first.
PYTHONPATH=src python -m finder.discogs_inventory \
  --config /private/inventory.json --database /private/existing-rules.db --poll
```

The first snapshot does at most one seed verification, the configured number of inventory
pages, and the configured number of detail reads. With the default one-page/two-detail
bounds that is at most four GETs; one matching listing needs three. Later invocations reuse
the persisted seed verification and do at most three GETs with the defaults. Only one
bounded wait of up to three seconds may precede each request; longer shared cooldowns stop
work for later. Requests use the existing eight-second transport timeout plus the existing
bounded streaming-deadline wrapper and 256 KiB response limit.

Snapshot results are `origin: snapshot`, never new-listing alerts. Snapshot mode does not
establish a baseline. It can queue more matches than the detail cap; unfinished work remains
durable and is resumed by later due invocations. Another explicit snapshot may refresh an
existing snapshot result using the same event key. `--results` shows the retained result
when the next poll is not yet due. Never pipe the output to public CI or public logs: it
contains listing URLs, prices and seller comments intended for private review.

The first live gate must verify actual endpoint access, seed association, reported inventory
size/completeness and detail currency/criteria. A successful synthetic test is not that gate.
No installed service, timer, new credential, security permission, or web-broker capability
is required or added by this artifact. A later authorized scheduler can invoke this bounded
command. Notification integration is a separate reviewed change using the existing private
sender, not a second delivery stack.

## Supported source and qualification

- Verify `GET /marketplace/listings/{supplied_id}` once. Require the reported exact release
  and seller username to match configuration. Persist verification before scanning. A sold
  or later-disappearing seed therefore does not disable the seller cohort. A different seed
  or criteria config requires explicit operator review, not an automatic state reset.
- Read only `GET /users/{validated_username}/inventory` with `status=For Sale`, `sort=listed`,
  `sort_order=desc`, `per_page=50` and a bounded integer page. Never follow provider pagination,
  resource or seller URLs. No inventory currency-conversion parameter is assumed.
- Match only exact numeric `release.id`, never titles, artist words, masters, barcode guesses
  or seller descriptions. Inventory currency is not assumed to match the target currency.
- Detail-read discovered IDs through `GET /marketplace/listings/{id}?curr_abbr={currency}`,
  under the same shared guard. Require exact requested listing ID, configured release,
  configured seller and reported `For Sale` status. Reuse known-listing normalization,
  decimal rounding-up, strict-under price, explicit grade and seller-comment checks.
  Missing evidence, wrong currency, unsupported comments, disclosed missing records or
  playback damage block an item-criteria match. Preserve reported and original prices
  separately. There is no conversion of local prices or fallback to the inventory price.
- A `review_ready` record means **reported item criteria match**, not a buyable item or a
  verified pressing. `purchase_qualified` and `eligible_for_notification` remain false.
  Shipping blocked/unknown is reported separately. Destination shipping, tax, fees, full
  playable contents and physical pressing remain unverified. No catalog or checkout reads
  are added; standalone media completeness can remain unknown.

Official route reference: https://www.discogs.com/developers/#page:marketplace, Inventory and
Listing resources. Data provided by Discogs. This application is not affiliated with Discogs.

## Baseline, queue and limits

One additive `discogs_seeded_inventory_state` table holds a configuration fingerprint,
verified-seed time, due time, persistent source-specific denial/unfinished-operation stops,
baseline IDs, pending detail references and retained results. No existing aggregate rule,
sample, outbox, target, or owned row is modified. Only existing shared request-guard and
request-budget fields are touched for transport coordination.

- The first complete ordinary traversal establishes a silent baseline, with no historical
  alert flood. Later complete traversals queue previously unseen matching IDs exactly once.
  Empty inventory is valid only with coherent pagination and no listings.
- Every page validates page number, total pages/items, fixed page size, length and listing
  identity. Count changes, duplicate IDs across pages, or a page cap mark coverage incomplete.
  Incomplete ordinary traversals do not establish a baseline or queue new candidates; already
  pending detail work may still resume. A seller exceeding the configured bound therefore
  needs review before this narrow version can provide continuing discovery.
- `pagination.complete` means all **reported** pages were observed consistently. The API is
  not an atomic snapshot. Coverage is always one seeded seller, never the whole market.
  A newly observed ID is not proof of a newly created listing. Copies listed and removed
  between polls can be missed, and absence does not establish a sale.
- Seen IDs and pending work are committed together before any detail requests. The two-detail
  cap, a restart or temporary detail failure cannot silently consume or lose candidates.
  Item-local failures rotate to the back of the pending queue for fairness on a later poll;
  they remain unresolved, never silently classified as nonmatches. Rate-limit/auth failures
  stop further requests for that invocation instead.
- Finished results retain deterministic event keys, provenance, timestamps, origin, item
  checks and unresolved shipping warnings. `--results` provides an executable handoff to a
  future private sender. There is no delivery/acknowledgment contract yet, so it must not be
  treated as an already-enabled notification outbox. Snapshot records are never new leads.
- Detailed payloads expire after six hours and are removed on a later poll/results read.
  Minimal expired/dedup references remain. Seen IDs and combined pending/results are each
  capped at 10,000; capacity exhaustion stops for operator review rather than evicting IDs
  and repeating old leads.
- Shared guard reservation commits before each HTTP request. 429/Retry-After/quota holds
  remain shared with aggregate and phone-review reads. 401/403 persist a source-specific
  stop before releasing the guard. No immediate retries, credentials, redirect following,
  browser fallback, proxy/netrc access or access-bypass paths exist.
- An unknown pre-header outcome or crash leaves durable unfinished state for manual
  reconciliation, not an automatically expiring permission to retry. `--init-state` cannot
  clear a denial, recover unfinished work, replace a missing existing row, reset a baseline
  or change its config. A stopped or changed aggregate control prevents requests.

## Offline replay and tests

`--fixture /path/to/synthetic.json` accepts a bounded object containing `seed_listing` and
`pages` with official-shaped synthetic responses. It runs seed verification, page validation,
exact release filtering and inventory preview with **zero network or database access**.
It labels freshness unknown. Requested-currency detail integration, restart/dedup and quota
behavior are tested with `httpx.MockTransport`, never a real service.

Run `python -m pytest -q tests/test_discogs_inventory.py`. Tests include complete/capped/
shifting pagination, strict identities, no arbitrary URL traversal, silent baseline, seed
retirement, currency precision, safe criteria, bounded detail resumption, denial persistence,
shared Retry-After, unknown request outcomes, queue fairness, result expiration, snapshot
refresh and preservation of 19 synthetic aggregate watches. Repository-wide required checks
remain those in `AGENTS.md`; restricted UNIX-socket tests are not live API validation.

### Local verification on November 8, 2026

- Final inventory suite: 57 passed, including both zero/one-page empty baselines, the
  120-second minimum and durable due time across an observer restart. Library defaults remain 600 seconds; tests are not a live
  polling-speed promise.
- Independent review: 320 inventory/shared-guard/known-listing/phone/aggregate regressions
  passed before the additional seven cadence/empty-baseline tests. Runtime module remained unchanged.
- Full Python: 4,792 passed, one skipped, 41 subtests passed; 18 existing UNIX-socket tests
  failed with sandbox `PermissionError`. This is not a full-suite pass. No permission bypass
  or live API call was attempted. All cadence and empty-baseline tests passed in this rerun.
- Ruff lint and 226-file format check passed. `npm test`: 110 passed. TypeScript typecheck
  and diff whitespace check passed. Exact remote-commit checks are a separate publication
  check, not implied by these local results.
