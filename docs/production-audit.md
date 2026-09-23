# Bounded Production data audit

The `Production bounded scan` workflow is a **manual** check against the ten-item
`rap-vinyl-validation` monitor. Run it from `main` on seven different UTC dates. The first
scan and a repeat on 2026-09-22 passed, but **together count as one date** toward this gate.
Record the GitHub run URL and aggregate JSON results only; do not copy listing IDs, seller
names, titles, full responses, or raw postal codes into public issues or commits.

For each date, check:

1. `status: passed`, `environment: production`, and the intended `monitor`.
2. `browse.fetched` is at most ten; `unprocessed` and persistence missing/drift are zero.
   Investigate `skipped`, `suppressed_deleted_sellers`, `detail_enrichment_failures`, and
   `browse_retries`, including a passing run with partial details.
3. `normalization.field_coverage` and `cost_readiness`: unknown shipping, fixed-price delivered
   subtotal, auction bid, condition, specifics, and detail age. Only counts are public.
   If `destination_context` is not `country_and_postal`, do not infer that shipping coverage
   reflects the eventual buyer destination.
4. `browse.limit_reached` can be true by design. A bounded ten-item sample cannot prove search
   recall, that previously seen listings remain buyable, or that a disappeared listing sold.
5. `browse.browse_requests` counts HTTP GET attempts including retries; it excludes OAuth.
   Compare request cost to stored listings before expanding scan breadth.

| UTC date | Run URL | Fetched / new / updated | Invalid / suppressed / unprocessed | Unknown shipping / fixed delivered subtotal known / auctions | Detail failures / Browse requests / retries | Outcome or issue |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-22 | [Successful repeat scan #4](https://github.com/nictowey/Finder/actions/runs/35786205343) | 10 / 0 / 10 | 0 / 0 / 0 | 0 / 10 / 0 | 0 / not instrumented / not instrumented | Passed with no persistence drift; bounded limit reached. Earlier scans on this date count as the same day. |

The September 22 report predates the new request counters and destination-context field.
Those values are unknown for this baseline; do not infer a destination from shipping coverage.

Do not mark the seven-day gate complete until seven distinct dates have verified results,
including inspection of failures and repeat identities. Do not collect real labeled fixtures
until the retention/evaluation decision in
[`0001-provider-use.md`](decisions/0001-provider-use.md) is resolved. For target-level discovery
coverage, compare a future private watchlist against a manually checked eBay sample; the
ten-item broad scan cannot measure that outcome.

## Exact-target discovery sample

The manual `Production target scan` workflow runs from `main` with the existing Production
credentials and shared deletion-aware Neon database. Select `initial` once to sample existing
active listings ranked by eBay best match; `refresh` samples the newest listings. It executes
the two configured Future DS2 alias queries, one page of ten items each. The public run log
contains only the plan and aggregate outcomes; it must never print listing IDs, seller data,
titles, or listing URLs. Check `complete`, `coverage_truncated`, fetched/new/updated counts,
`skip_reasons`, and whether both query results completed. A successful bounded run does not
establish recall of the user-supplied listing or a reliable pressing match.

For a privately known listing, `finder scan-target --target future-ds2-7609839 --mode initial
--probe-legacy-id <numeric eBay item ID> --json` reports `probe_found_in_this_run` without
printing the ID. Run this only in a private environment with Production credentials and the
shared database; do not paste the ID into a public workflow input or log. The probe checks
items normalized during **this scan**, not items already stored by an earlier broad scan.
An absent result identifies a miss within this bounded sample, not a permanent search miss.
Record counts of found/missed and reasons privately, subject to the provider-use review; do
not copy real item identities or labeled examples into the repository or public workflow logs.
The manual workflow also accepts an optional `TARGET_PROBE_LEGACY_ID` **Production environment
secret** for a private yes/no check. The workflow never puts the ID in a dispatch input, command
literal, or report. Keep it scoped to this environment while repeating this target check;
remove or replace it when that check is no longer needed.

| UTC date | Run | Result | Target discovery |
| --- | --- | --- | --- |
| 2026-09-23 | [Initial scan #1](https://github.com/nictowey/Finder/actions/runs/35809931034) | Passed, both queries complete. First query fetched 10 and hit the page cap; second fetched 4, with 2 overlaps. Twelve distinct listings were normalized, with no partial details or unprocessed items. | No item-level probe in this run. |
| 2026-09-23 | [Private probe scan #2](https://github.com/nictowey/Finder/actions/runs/35810295262) | Passed. The same 10 + 4 summaries and 2 overlaps were returned; all 12 distinct items updated. First query again hit the page cap. | `probe_found_in_this_run: true` for the user-supplied DS2 example. This verifies discovery in this run, not the numbered pressing identity. |

The two runs share one date and do not count as another day of the seven-day **broad-monitor**
audit. The first query's ten-item cap means target recall remains unmeasured. An item outside
the first page, a differently worded listing, or a new listing between samples may still be
missed. A positive probe does not establish a catalog match, condition, or market value.

## Private target pressing check

The manual `Production target match check` workflow reads the newest 100 stored eBay snapshots,
locates exactly one item using the Production environment's private probe secret, and retrieves
at most ten Discogs release details including the pinned target. It writes no listing labels or
candidate records. Public output is restricted to decision codes, field names, and retrieval
flags; the known item identity is not printed. This is diagnostic, not an exact pressing claim.

| UTC date | Run | Aggregate result | Interpretation |
| --- | --- | --- | --- |
| 2026-09-23 | [Target match check #1](https://github.com/nictowey/Finder/actions/runs/35811037387) | Completed; pinned target retrieved and scored 25 (`rejected`). Matched color and edition; artist, format, and title comparisons conflicted. One release evaluated; target absent from seller-text search, so retrieval incomplete. No structured pressing identifier. Decision `insufficient_data`. | The user identifies the supplied listing as the numbered release, but Finder did not recognize it. This is a known false negative in this private example. Do not promote the pinned target merely because it was directly fetched or because color and edition overlap. Review artist/title extraction and aliases, seller specifics, and additional independent copy evidence before changing the decision policy. Subject to the provider-use review, measure effects on a labeled held-out set rather than tuning to one listing. |
