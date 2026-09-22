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
| 2026-09-22 | Prior first scan and repeat; see Actions history | 10 fetched in each | Check logged results | Check logged results | Check logged results | Passed as ingestion/persistence checks; one distinct day |

Do not mark the seven-day gate complete until seven distinct dates have verified results,
including inspection of failures and repeat identities. Do not collect real labeled fixtures
until the retention/evaluation decision in
[`0001-provider-use.md`](decisions/0001-provider-use.md) is resolved. For target-level discovery
coverage, compare a future private watchlist against a manually checked eBay sample; the
ten-item broad scan cannot measure that outcome.
