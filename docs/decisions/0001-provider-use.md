# Decision record 0001: buyer-facing eBay deal discovery

Status: **open; price-based deal output blocked**

Opened: 2026-09-22

Owner: Finder product owner

## Proposed product flow to review with providers

1. A collector selects a vinyl release or pressing, acceptable copy condition, shipping
   destination, and maximum delivered price. Finder searches official eBay Browse listings on
   a bounded schedule and displays a link with pressing evidence, condition and cost caveats.
2. If separately licensed sold transactions are available, Finder would match the listing and
   sold records to the same pressing, exclude incompatible conditions, calculate a conservative
   value range, and flag listings priced below that range. A notification could link a collector
   to the original eBay listing. Finder would not purchase automatically or reprice sellers.
3. A private evaluation would check real listing identity against Discogs catalog releases and
   manually review false leads. Listing snapshots and observations currently persist in Neon
   to support repeat scans and eBay account-deletion notices. Proposed evaluation may require
   human labels, retention of derived features, and comparison with later outcomes.
4. The initial pilot is free and private. A future subscription or affiliate program is a
   separate proposed use, not an approved feature.

This description is intentionally specific: approval for a generic API application or access to
Browse does not answer whether the proposed comparison, ranking, alert, retention, and paid use
are allowed. Do not implement steps 2 or a paid version until the relevant written decisions.

## Questions requiring written answers

| Provider or source | Exact question | Required decision/evidence |
| --- | --- | --- |
| eBay developer/business contact | May Finder show a buyer-facing value range, rank or label an eBay listing as underpriced using external sold data, and send a price-based link alert? Does the answer change for a private pilot, subscription, affiliate revenue, or buyers who resell? | Written answer for each proposed display and business model; API agreement exception or qualifying terms if needed. |
| eBay developer/business contact | May Finder keep current listing snapshots and observation history in Neon as designed? For how long? May a private human label real listings, keep derived labels/features, and use those records to measure or tune a deterministic matcher? Can sanitized real fixtures be committed publicly? | Document retention limits, permitted evaluation uses, deletion handling, and whether any algorithm-training restriction applies. Do not publish captured real fixtures before review. |
| Sold-transaction rights holder | Can Finder access sold price, date, shipping, condition, identifiers and offer-price certainty for commercial valuation and cross-source comparisons? | Agreement covering API access, combination with eBay content, display, derived values, retention, attribution, fees, and deletion. Check pressing-level pilot coverage before integration. |
| Discogs | May a paid Finder display CC0 catalog data obtained through its API, linked to the Discogs release and refreshed within its current terms? Is a separate written permission needed? | Written paid-use and display decision. Keep marketplace sales history and prices out of current integration. |

Current public documentation points to substantive restrictions, not approval: the
[eBay API License Agreement](https://developer.ebay.com/join/api-license-agreement) includes
limits on pricing models, intermediate copies and algorithm training;
[Marketplace Insights](https://developer.ebay.com/api-docs/buy/static/ref-marketplace-supported.html)
is restricted;
[Discogs API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
put conditions on paid use and display freshness. Terms and provider answers must be checked
again at decision time. This record is a product decision request, not a legal determination.

## Decision branches

- **Price-based use explicitly permitted, sold source licensed:** qualify coverage and cost on
  the pilot pressing set; build valuation only after documented accuracy and display gates.
- **Discovery/watchlist permitted, comparison unavailable:** ship the user-set price-ceiling
  watchlist without fair-value or undervaluation claims; do not hide a deal score in its ranking.
- **Required eBay retention or matching use denied:** remove or redesign dependent persistence
  and evaluation before wider scanning; qualify a different licensed marketplace.

## Follow-up evidence to attach

- Date, contact, agreement version, exact example outputs and answers for each provider.
- Sold-source contract and measured pressing/condition coverage from a licensed sample.
- Data inventory and retention/deletion implementation review for the current Neon tables.
- Explicit go/no-go for the private watchlist, real-label evaluation, valuation, alerts,
  subscription, and any affiliate use separately.

No provider outreach or approval is recorded yet. A future decision must record its evidence
and update `ROADMAP.md` and `AGENTS.md` before crossing a gate.

## Current data inventory for the retention question

This is the implementation as inspected on 2026-09-23, not a provider-approved retention
schedule. Production scans write to the shared Neon PostgreSQL database. Local Sandbox databases
and developer CLI copies require a separate cleanup procedure before retaining real data there.

| Storage | Marketplace-derived content | Current lifecycle and deletion |
| --- | --- | --- |
| `listings` | Current item ID, title, prices, shipping, seller ID and username, condition, image/listing URLs, specifics, selected source metadata | Updated on repeat observation. Seller-deletion handler removes eBay rows for the notified stable seller ID. No age-based expiry exists. |
| `listing_observations` | Full normalized snapshots for each observed timestamp, including seller and item values | Append-only until deletion of that seller's matched listing IDs. No age-based expiry exists. |
| `listing_variant_candidates` | Listing and catalog IDs, candidate score, and evidence values from seller listing and Discogs catalog | Replaced by an explicit match run and deleted with that seller's listing IDs. Candidate replacement now checks that the listing still exists and locks the seller on PostgreSQL before writing. |
| `ebay_deleted_users` | Stable seller ID tombstone | Retained to prevent reimport after a deletion notice. No expiry is defined; confirm required retention or minimization with eBay. |
| `products`, `variants` | Discogs catalog identity and normalized release metadata | Upserted catalog data without eBay seller values. Display freshness and paid-use conditions require separate review. No age-based expiry exists. |
| GitHub Actions output | Aggregate scan field counts, request totals, failures and synthetic catalog results; no listing IDs, titles, sellers or URLs in intended live logs | GitHub log retention is a separate account setting and has not been verified in this review. Do not upload real replay fixtures. |

Open design questions: permitted maximum retention for current snapshots and observations,
whether manual real-listing labels or tuned matching rules count as restricted algorithm use,
whether anonymizing a fixture suffices, treatment of database backups and local copies after a
seller notice, and the retention period for seller tombstones. The deletion path has unit tests
and a successful eBay test notification, but the candidate/deletion concurrency guard has not
been exercised against concurrent transactions on the hosted PostgreSQL database.
