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
flags; the known item identity is not printed. Its job summary includes an anonymous target-first
comparison table of same-family alternatives, seller/catalog disagreements, missing evidence,
and unscored runout presence. Scores are heuristics, and a missing seller field is a review gap,
not a requirement for an eBay description. This is diagnostic, not an exact pressing claim.

| UTC date | Run | Aggregate result | Interpretation |
| --- | --- | --- | --- |
| 2026-09-23 | [Target match check #1](https://github.com/nictowey/Finder/actions/runs/35811037387) | Completed; pinned target retrieved and scored 25 (`rejected`). Matched color and edition; artist, format, and title comparisons conflicted. One release evaluated; target absent from seller-text search, so retrieval incomplete. No structured pressing identifier. Decision `insufficient_data`. | The user identifies the supplied listing as the numbered release, but Finder did not recognize it. This is a known false negative in this private example. Do not promote the pinned target merely because it was directly fetched or because color and edition overlap. Review artist/title extraction and aliases, seller specifics, and additional independent copy evidence before changing the decision policy. Subject to the provider-use review, measure effects on a labeled held-out set rather than tuning to one listing. |
| 2026-09-23 | [Target match check #2](https://github.com/nictowey/Finder/actions/runs/35811657267) | After the general artist-suffix and full-title fixes, target scored 60 (`candidate`), with artist, title, color, and edition matched. Decision `family_only`, with the target in decision candidates. Seller-text catalog search still missed the pinned release; one release evaluated and retrieval incomplete. No structured pressing identifier. | The bounded check now recovers the album family without asking the seller for a Discogs release ID, barcode, or runout. It does not identify the numbered copy conclusively or establish competitor coverage. The format text's fuzzy nonmatch was displayed as a conflict in this run; the next policy version reports such misses separately. |
| 2026-09-23 | [Target match check #3](https://github.com/nictowey/Finder/actions/runs/35811827748) | Passed with the revised decision report: target still scored 60 (`candidate`), decision `family_only`, no contradictory fields, and format as an unmatched soft field. Search incomplete and pressing identifier missing. | Review the target pressing manually; no exact match or valuation claim. The title, artist, color, and edition are useful clues but are not independent confirmation that this individual copy is numbered. |
| 2026-09-23 | [Target match check #4](https://github.com/nictowey/Finder/actions/runs/35812298469) | Passed after adding one bounded target-catalog family query. Ten releases were evaluated, including five other releases from the same catalog family. The target remained a candidate; decision `family_only`. The family query hit the ten-result cap, seller-text search still missed the target, and no pressing identifier was supplied. | At least five alternatives now enter review, but neither family coverage nor the individual numbered copy is proved. The decision-level edition conflict comes from a representative candidate; the target itself has no contradictory fields. [Discogs smoke #22](https://github.com/nictowey/Finder/actions/runs/35812283987) also passed on this code. |
| 2026-09-23 | [Target match check #5](https://github.com/nictowey/Finder/actions/runs/35813229053) | Passed with a rendered anonymous candidate comparison. The seller has both a title and structured numbered claim. Of ten evaluated releases, five alternatives share the target family; within these six, the pinned target is the only catalog-marked numbered release. It matched artist, title, color and edition, with no seller/catalog disagreements in scored fields. Barcode, catalog number and runout are absent from seller specifics. Decision remains `family_only`; search truncated. | Stronger evidence for human review, not proof that this individual copy is numbered. The table preserves per-candidate disagreements and missing fields without listing or release identities. [Discogs smoke #25](https://github.com/nictowey/Finder/actions/runs/35813265426) passed. |

The Discogs release number is a database identity, not a manufacturing identifier expected in an
eBay listing. The collector supplied catalog barcodes and side-specific runouts for this release;
their presence on the catalog page does not mean the seller supplied or photographed them for
this individual copy. Missing seller identifiers lead to review, while explicit conflicting
manufacturing evidence can exclude a variant. Compare individual-copy numbering or other clear
seller evidence when available; do not infer it from the catalog entry.
