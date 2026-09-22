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
