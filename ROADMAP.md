# Finder Product Roadmap

Last updated: 2026-09-23

## What Finder should do for a collector

The first useful product is a small, trustworthy vinyl watchlist. A collector names an album or
exact pressing, the maximum delivered price and acceptable condition; Finder checks newly listed
eBay items, explains which pressing each might be, and links to the listing while it is still
available. If lawful sold transactions and permission for this use are secured, Finder can also
show a conservative value range and flag unusually cheap copies. It must never call a listing a
"steal" because its current asking price is below another seller's asking price.

Start with roughly 20 target pressings across genres and eras, including colored, limited,
test, and unofficial variants. Include difficult neighboring pressings and ordinary records,
then report discovery and identity results by genre and era. This is a pilot set for learning,
not a promise that every rare
record will have enough comparable sales. A user should be able to answer four questions quickly:

1. Is this the pressing I asked for, or is the identity uncertain?
2. What will I actually pay, including quoted shipping, and what costs remain unknown?
3. What independent, recent sold evidence supports any value claim?
4. Why did Finder show this listing now, and what should I verify before buying?

## Current state and the critical feasibility gate

**Working:** eBay Production OAuth/Browse ingestion, bounded scans, normalized listing snapshots
and observation history in the shared Neon database, deletion-notification handling, Discogs
catalog search, and an evidence-bearing provisional match decision. The first ten-item Production
scan and a repeat passed. `finder listings` and `finder match` support manual inspection.

**Unproven:** coverage of relevant new listings over time, real-world pressing-match precision,
any authorized sold-comparables feed, fair-value estimates, and alerts. The twenty synthetic
matching cases test policy mechanics, not market accuracy. No listing should be advertised as a
deal today.

The private target review now surfaces seller-claimed artist, album, and complete color cues
even without a barcode or runout. `possible_pressing` is a review lead, never an exact identity
or verified deal. Discogs marketplace inventory could add a second source of release-specific
offers, but its restricted API data requires a separate written use decision before integration.

**Before a public Discogs-backed watchlist or a price-based deal product, resolve these rights questions in writing:**

The proposed buyer flow and open provider questions are recorded in
[`docs/decisions/0001-provider-use.md`](docs/decisions/0001-provider-use.md). The bounded scan
measurement procedure and distinct-day log are in
[`docs/production-audit.md`](docs/production-audit.md).

| Question | Current evidence | Decision needed |
| --- | --- | --- |
| Can Finder compare an eBay listing with external sold prices and label it undervalued for a buyer? | eBay's API agreement restricts using eBay content with third-party information to suggest or model prices for items listed on eBay. | Obtain eBay's explicit permission or qualified review of the exact proposed display, alerts, data retention, and business model before implementing or launching deal scoring. |
| Where do actual sold transactions come from? | Browse exposes active offers; eBay Marketplace Insights is restricted and closed to new users. Discogs classifies marketplace prices and sales history as restricted data. | Secure a source with written commercial, retention, derived-data, and display rights; test its pressing and condition coverage. |
| May Finder retain and use real eBay listings for labeled evaluation and matcher development? | eBay's agreement limits intermediate copies and restricts use of eBay content to train algorithms. | Review the existing observation retention, sanitized fixtures, labeling, model/heuristic tuning, and deletion plan before building a permanent real-listing dataset. |
| May Finder show Discogs API catalog evidence beside outbound eBay listing links, even in a free watchlist? May it charge for that app? | Discogs identifies CC0 catalog fields, but its API terms also identify use intended to drive traffic to non-Discogs services as a prohibited commercial use and require written permission to charge for API-integrated access offered free by Discogs. Attribution, linking, and freshness have separate requirements. | Ask Discogs to review the exact free outbound-link journey and any paid model in writing before public display; keep restricted marketplace data outside Finder. |
| Can Finder watch listings for sale on Discogs itself? | Discogs classifies marketplace inventory and prices as Restricted Data, which its API terms prohibit using commercially. | Ask Discogs for a written decision covering private/free watchlists, display, notification, retention, and a future paid or price-comparison product before connecting marketplace endpoints. |

Ready-to-send questions and the official contact routes are in
[`docs/decisions/provider-outreach.md`](docs/decisions/provider-outreach.md). No provider approval
has been received.

This is a product gate, not just a compliance task. If price-based comparisons are not permitted,
ship only an expressly permitted exact-item discovery/watchlist workflow and assess another licensed
marketplace or data partnership. Do not disguise a valuation as an "interesting listing" score.
Do not use scraping, sold badges inferred from disappeared listings, or active asking prices as a
substitute for authorized sold transactions. The present assumption is buyer-side collecting,
not automated purchasing or seller repricing; a resale-focused business model needs its own
provider review.

## Release ladder

The estimates below are rough for one active builder and exclude provider approval and data
licensing delays. Exit gates control progression; calendar dates do not override failed gates.

| Milestone | Build and measure | Collector-visible result | Exit gate |
| --- | --- | --- | --- |
| A. Feasibility and data audit | Resolve the rights questions; complete seven bounded Production scans; measure field coverage, missing shipping, request cost, lifecycle, and failures. | A private, inspectable feed of real listings with honest freshness and cost flags. | Written rights decision for the intended next use; seven scans without silent loss or duplicate identities; permitted sanitized replay or synthetic contract fixtures. |
| B. Pressing identity | Broaden bounded Discogs retrieval beyond one title query; subject to data-use clearance, label at least 200 real listings across 40 families and difficult variants; measure family, exact-pressing, and abstention separately. | Correct pressing or explicit uncertainty, with the evidence to verify it. | At least 98% family and 99% exact-variant precision on a held-out labeled set; conflicts never silently promoted. |
| C. Personal watchlist | Store family/exact pressing targets, acceptable variants, excluded attributes, condition floor, delivered-price ceiling, shipping destination, and buying format. Map each target to bounded eBay searches. | In a cleared release, "show me this pressing under my own maximum price" without claiming market value. | Repeat scans do not lose targets or duplicate listings; missed-listing audits and manual buyer reviews show useful coverage; public Discogs-backed display and outbound links have a documented provider-use decision. |
| D. Sold data and valuation | Obtain permitted transactions; normalize variant, media/sleeve condition, shipping, currency, date, lots, and accepted-offer uncertainty. Backtest value ranges against later sales. | A dated range and comparable count only where evidence is strong enough. | Written data rights plus measured coverage on the pilot set; no value emitted for sparse, stale, or incompatible comps. |
| E. Opportunity pilot | If eBay permits it, compare fresh, buyable listing cost with conservative value; run shadow mode; manually review every candidate. | "Worth reviewing" leads with price, pressing, condition, and risk reasons. | False-positive causes and missed opportunities recorded; no alert without identity, sold-data, freshness, and delivered-cost gates. |
| F. Reliable alerts and private product | Add leased scheduled jobs, freshness monitoring, idempotent alerts, one delivery channel, and a minimal watchlist/evidence view. | A collector gets a timely, non-repeated lead and can open the listing. | Fourteen days of reliable shadow scans, backup/restore test, and a small pilot with collectors who use the results. |
| G. Next collectible | Qualify exactly one new category and its data providers. Reuse generic listing, observation, monitor, and opportunity contracts. | A second category with its own identity and condition rules. | Vinyl shows repeatable collector value and the new category independently passes source, rights, accuracy, and economics gates. |

Rough sequencing: A's technical audit and a synthetic labeling framework can run in the next
2–4 weeks. Real labeling waits for a data-use decision; B and an internal C prototype may then
take another 1–3 months. A public C release requires a Discogs outbound-use decision. D through
F have no credible delivery date until the rights and sold-data questions are answered. Do not
spend heavily on a public UI or subscription checkout while those gates are open.

### What "genuinely useful" means in the pilot

Track outcomes that reflect collector value, not just API calls or database rows:

- **Discovery coverage:** For each target, compare Finder's surfaced items with a manually
  checked eBay sample over the same window; record missed relevant listings and why.
- **Identity precision:** Count confirmed family and pressing matches, incorrect matches, and
  abstentions on separate development and holdout sets. Never report precision only on items
  Finder chose to inspect manually.
- **Cost accuracy:** Track known delivered subtotals, missing or location-dependent shipping,
  auction bids versus final purchase prices, and stale/unavailable items.
- **Lead quality and speed:** Have several collectors review a small shadow feed for four weeks;
  record how many listings they would seriously consider, how many were false leads, and whether
  Finder surfaced them before they became unavailable. A purchase is strong evidence of value,
  but not required for every rare target in a short pilot.
- **Economics:** Measure API and sold-data cost per inspected listing and per credible lead,
  including support and human review, before deciding on pricing or affiliate revenue.

Use the detailed engineering phases below to implement these milestones. A phase can progress
internally while a later release remains blocked by provider permission.

## North star

Finder should help a collector define an exact item they want, continuously inspect supported
marketplaces, distinguish the correct collectible variant, estimate a defensible acquisition
value from lawful comparable data, and surface only opportunities that meet explicit confidence
and price thresholds.

The first vertical is vinyl across genres and eras, using eBay for active listings and Discogs
for catalog identity. The first broad monitor and early evaluation examples focused on recent
hip-hop/rap; that sample alone cannot establish accuracy on all vinyl. Later categories and
marketplaces must fit the same
generic pipeline without weakening the accuracy standards established for vinyl.

Finder is not an automated buying system. It must show its evidence, uncertainty, data age, and
cost assumptions. When the evidence cannot identify an exact pressing, Finder should abstain or
label the result ambiguous rather than guess.

## Development doctrine

1. **Precision before recall.** Missing a possible deal is preferable to recommending the wrong
   pressing as a deal.
2. **Evidence before score.** Every match, valuation, and opportunity score must be reproducible
   from stored evidence. Scores may summarize evidence but never replace it.
3. **Abstention is a valid result.** `family_only`, `ambiguous`, and `insufficient_data` are normal
   outcomes, not errors to hide.
4. **Identity before valuation.** Finder must not value a listing against exact-variant
   comparables until the variant match meets its accuracy gate.
5. **Sold evidence before asking prices.** Active asking prices may describe supply but are not
   proof of market value.
6. **Provider boundaries stay strict.** Official APIs only. Marketplace logic, catalog logic,
   category logic, and valuation logic remain separate.
7. **Schema changes remain deliberate.** A hosted Neon database already exists; add migrations
   and backup/restore checks before the next production schema change.
8. **Build from observed failures.** Do not add matching heuristics merely because they sound
   useful; add them after labeled examples demonstrate the need.
9. **No silent degradation.** Stale feeds, partial details, failed enrichment, and missing
   comparables must be visible in status and logs.
10. **A feature is not complete until measured.** Passing unit tests is necessary but does not
    establish real-world accuracy.

## Identity model

The central modeling decision is to separate the musical work, manufactured pressing, and the
individual copy offered for sale.

| Concept | Meaning | Example |
| --- | --- | --- |
| Product | Release family or musical work | Travis Scott — *Rodeo* |
| Variant | A distinguishable manufactured release/pressing | US black 2LP, specific label and catalog number |
| Collectible attribute | Copy- or edition-level trait that can affect value | Artist signature, copy number, sealed condition |
| Listing | A marketplace offer and the seller's claims | One active eBay listing |
| Observation | Finder's immutable snapshot of a listing at a time | Price and shipping seen on a scan |
| Match candidate | A possible listing-to-variant relationship with evidence | Discogs release 123 scored against an eBay listing |
| Match decision | A calibrated outcome | Exact variant, family only, ambiguous, rejected |
| Comparable | A lawful historical transaction normalized to the same identity | Sold copy of the same pressing and comparable condition |
| Valuation | A time-stamped estimate with range and confidence | Estimated fair acquisition range |
| Opportunity | A listing evaluated against valuation and risk rules | Price sufficiently below the conservative range |
| Monitor | A user's target and thresholds | Exact colored pressing, maximum delivered price $80 |
| Alert | A delivered opportunity event with deduplication | One notification for a newly qualified listing |

The generic domain should keep `Product`, `Variant`, `Listing`, `Monitor`, `Comparable`,
`Valuation`, `Opportunity`, and `Alert`. Vinyl-specific fingerprints and collectible attributes
belong in the vinyl category package.

## Vinyl pressing and collectible distinctions

### Manufactured pressing identity

Potentially discriminating evidence includes:

- Discogs release ID and master-release relationship
- Artist and release title
- Label and catalog number
- Barcode, including normalization of spaces and check-digit formatting
- Country/market and release date
- Media format, disc count, size, speed, and RPM
- Intentional vinyl color or picture-disc designation
- Edition descriptors such as limited, deluxe, reissue, remaster, promo, test pressing,
  unofficial, or club edition
- Matrix/runout, pressing plant, mastering, and manufacturing identifiers
- Track-list or packaging differences when explicitly available
- Images that visibly prove a differentiating characteristic

No single field is universally unique. A barcode may be shared across multiple versions;
catalog numbers and matrix strings may also be reused or differ for reasons that do not create a
separate release. Finder must score combinations and retain conflicts.

### Signed and numbered copies

A signature is not automatically a new pressing:

- An officially manufactured and distributed signed edition may map to its own catalog variant
  when the catalog and evidence distinguish it.
- A retail copy signed after manufacture remains the same pressing plus an `autograph` claim.
- A seller writing “signed” is not proof of authenticity. Signature evidence must record the
  source, images, certification/provenance claims, and verification status.
- Individually numbered copies of one edition generally share a pressing identity. The copy
  number is a collectible attribute, not a new variant, unless reliable catalog evidence says
  otherwise.
- The DS2-style failure case is a numbered purple club edition beside other DS2 vinyl. A
  shared barcode or color is not proof of the numbered edition; the current matcher requires
  an explicit structured seller claim even for a *probable* numbered candidate. A title-only
  serial claim, a generic “first pressing” item specific, and an uninspected image do not
  verify the copy. An item-specific “Numbered” claim is still unverified seller evidence, not
  authentication of the serial or a route to `exact_variant`.
- Condition and sealed status describe the offered copy, not the pressing.

Valuation of a signed or numbered copy will eventually require comparable transactions with the
same attribute and an authenticity policy. Until then, Finder may identify the claim but must not
apply an unsupported premium.

### Match outcomes

The future matcher should return one of these outcomes rather than only a numeric score:

| Outcome | Meaning | Allowed downstream behavior |
| --- | --- | --- |
| `exact_variant` | One variant meets the calibrated auto-match gate with no material conflict | Exact-variant comparable search may proceed |
| `probable_variant` | One candidate leads, but evidence does not meet the exact gate | Show candidate; no exact-variant valuation claim |
| `family_only` | Album/work is clear but pressing is not | Family monitoring and broad supply context only |
| `ambiguous` | Two or more materially plausible variants remain | Preserve candidates and request more evidence |
| `rejected` | Evidence conflicts with the candidate family/variant | Exclude candidate |
| `insufficient_data` | Listing lacks enough evidence even for a useful family match | Retain listing without a product assertion |

## Evidence hierarchy

Evidence must retain its origin and observation time. Initial reliability ordering:

1. Provider-assigned exact release identifier, when genuinely supplied by the marketplace
2. Multiple compatible structured identifiers: barcode, catalog number, country, format, year
3. Matrix/runout or pressing-plant evidence from readable text or verified images
4. Explicit variant descriptors: color, edition, disc count, packaging, promo/test-pressing tags
5. Seller-supplied structured item specifics
6. Listing title and description text
7. Image-derived or model-derived evidence, added only after deterministic matching is measured

Contradictory higher-order evidence overrides several weaker similarities. Evidence extracted by
OCR or a model must be labeled as derived, keep the source image reference, and never overwrite
provider or human-confirmed values.

## Accuracy gates

These are initial release targets, not current achievements. They must be measured on labeled,
representative eBay listings and revised only with a written reason.

| Capability | Initial gate |
| --- | --- |
| Release-family auto-match | At least 98% precision on a labeled holdout set |
| Exact-variant auto-match | At least 99% precision; no minimum recall requirement |
| Strong-identifier conflict handling | 100% of curated conflicts rejected or marked ambiguous |
| Shared identifier handling | No silent arbitrary tie-breaking |
| Title-only listing | Never promoted to `exact_variant` |
| Missing shipping | Never treated as free shipping |
| Valuation eligibility | Exact identity or an explicitly compatible broader comparable policy |
| Alert eligibility | Identity, valuation, freshness, and delivered-cost gates all pass |

Before production alerts, the evaluation set should contain at least:

- 200 manually labeled real eBay listings across at least 40 release families
- 50 listings where two or more Discogs variants share a barcode or catalog number
- 30 colored, picture-disc, deluxe, promo, test-pressing, signed, or numbered cases
- 25 unofficial/counterfeit-risk cases
- 25 intentionally incomplete or misleading listings

Use separate development and holdout sets. Matching-weight changes may improve the development
set, but the holdout set determines whether the change is accepted. Report the number of
auto-match decisions and errors for each tier; a 200-listing corpus alone cannot substantiate
a 99% exact-pressing precision claim if only a few listings receive exact decisions. Grow the
holdout and test across families and variants before enabling exact-match deal alerts.

## Roadmap overview

| Phase | Status | Exit gate |
| --- | --- | --- |
| 0. Foundations | Complete | Offline tests and live Discogs smoke test pass |
| Rights and commercial feasibility | Blocking for price-based product | Written permission or reviewed approval for the intended buyer comparison/alert use, plus a permitted sold-data source |
| 1. Real eBay data validation | In progress | Bounded scans and sanitized replays pass |
| 2. Pressing identity engine | In progress | Labeled precision gates met |
| 3. User-defined vinyl monitors | Internal discovery prototype | Exact/family targets produce stable discovery plans and measured coverage |
| 4. Comparable-source qualification | Planned | Commercial rights and data quality documented |
| 5. Valuation and opportunity scoring | Planned | Backtests and uncertainty gates pass |
| 6. Scheduling, alerts, and hosting | Planned | Shadow-mode reliability and deduplication pass |
| 7. User product | Planned | Monitoring workflow is dependable before UI expansion |
| 8. Additional markets/categories | Planned | Each addition passes adapter/category qualification |

## Phase 0 — Foundations

Status: **Complete**

Delivered:

- Generic marketplace listing domain and eBay Browse adapter
- Configurable monitor and one-command bounded scan
- SQLAlchemy persistence with listing deduplication and immutable observations
- Discogs catalog provider restricted to approved catalog endpoints
- Generic Product/Variant models and auditable candidate evidence
- Vinyl-specific metadata extraction and deterministic ranking
- Twenty-case policy evaluation set with synthetic identifiers
- Sanitized eBay fixture replay support
- Structured logging, bounded retries, mocked tests, and a live Discogs workflow

This foundation proves mechanics. It does not prove matching accuracy on real eBay listings or
the availability of lawful sold comparables.

## Phase 1 — Real eBay data validation

Status: **In progress; Production credentials and deletion compliance are active**

The first Production OAuth → Browse → normalization smoke run passed on September 22, 2026.
On September 22, 2026, the first ten-item rap-vinyl scan and a repeat passed against the
shared Neon database. The repeat updated the same ten listing identities without duplicates;
all observations persisted. Continue bounded scans over time, then sanitize representative
responses for replay and measure field quality. The seven-scan exit gate below is still open.

### Deliverables

1. Run small production scans with strict request and result caps.
2. Record API response-field presence rates without logging credentials or prohibited payloads.
3. Sanitize representative responses and commit replay fixtures only after the retention and
   fixture-use rights are reviewed; use wholly synthetic contract fixtures in the meantime.
4. Measure availability and quality of:
   - UPC/barcode
   - artist and album
   - catalog number and label
   - release year, color, edition, format, and country
   - shipping, item group/variation, listing end time, and seller data
5. Verify auctions, fixed-price listings, best offers, item groups, missing shipping, ended items,
   and detail-enrichment failures.
6. Establish listing lifecycle states: active, ended, missing-from-bounded-scan, and unknown.
   Absence from one search is not proof of sale.
7. Add contract tests for every new real response shape before changing normalization.

### Exit gate

- At least seven bounded scans complete without duplicate identities or silent data loss.
- Representative sanitized fixtures replay deterministically.
- Rate limits and request budgets are measured and documented.
- Required and optional field-presence statistics are known.
- Failures preserve prior observations and expose degraded status.

Do not increase scan breadth until the bounded scans are reliable.

## Phase 2 — Pressing identity engine

Status: **In progress; eBay Production access is working, real labels are missing**

`vinyl-decision-v2` now implements the first provisional outcome contract. The match CLI reports
family-only, probable variant, ambiguity, rejection, or insufficient evidence with provenance,
conflicts, and missing evidence. It never emits `exact_variant`: the synthetic evaluation cases
are policy tests, not a measured live-listing precision gate. The match CLI now combines up to
three bounded Discogs release searches (one valid seller barcode, one catalog number, and the
listing title), deduplicates candidates, and marks truncated or omitted search coverage. A sole
candidate does not establish unique pressing identity; incomplete retrieval cannot produce even
a provisional probable-variant decision.
The September 23 authenticated smoke check returned ten releases for a synthetic listing and
four strong candidates. This validates the new request path, while real eBay precision and true
release recall remain unmeasured.
For a saved exact release, retrieval now includes one bounded catalog artist/title search in
addition to the seller-derived searches. It may reveal competing pressings under the existing
ten-detail default; a release found only by that catalog query is still a seller-search miss.
The September 23 private DS2 rerun evaluated ten releases, including five alternatives in the
same catalog family, and the first-page catalog search hit its cap. The listing remains
`family_only` with no structured pressing identifier. Next measure candidate coverage and
review evidence across diverse targets under permitted evaluation terms; do not infer that ten
releases exhaust the family or that this one example measures exact-pressing accuracy.

### 2.1 Target ontology

The initial contracts exist; complete their use in durable decisions without changing the
generic core:

- `VinylFingerprint`: normalized manufacturing evidence for a pressing
- `CollectibleAttribute`: autograph, number, sealed status, obi, insert, hype sticker, or other
  copy/packaging characteristic
- `EvidenceRecord`: value, source, extraction method, observed time, and reliability class
- `MatchDecision`: outcome, ranked candidates, conflicts, missing evidence, and policy version

### 2.2 Candidate retrieval

- Generate multiple Discogs queries from artist/title plus strong identifiers when present.
- For an exact target selected by a collector, reserve one bounded release-detail request for
  that release even if seller-text search misses it. Record the search miss, keep competing
  search results under the same limit, and send a no-identifier match to human review rather
  than silently dropping a known target or asserting an exact pressing.
- Search the target catalog artist/title once for bounded competing candidates when the seller
  text is too noisy; do not count that as discovery of the release from the seller listing.
- Measure whether these first-page searches actually return the true release on a permitted,
  labeled dataset; a seller's barcode or catalog number remains an unverified claim.
- Retrieve candidates at the master/family level before ranking exact releases.
- Normalize aliases, punctuation, featured artists, alternate catalog-number formatting, and
  barcode formatting without discarding original values.
- Bound candidate counts and API use; cache catalog releases with freshness metadata.
- Before any public display, recheck Discogs' current freshness and retention terms; do not show
  stale API-derived catalog content as live data. Its current terms prohibit displaying content
  more than six hours older than the information on Discogs.
- Never assume the first Discogs search result is the correct pressing.

### 2.3 Deterministic matching

- Separate family match evidence from pressing match evidence.
- Add explicit positive, negative, and missing evidence.
- The internal target check now produces a bounded anonymous comparison of same-family
  candidates in the manual workflow summary. Inspect candidate-level field agreement, seller
  and catalog disagreements, missing seller evidence, and unscored runout presence. This is a
  review aid, not a precision measure or an exact pressing assertion.
- Treat color, edition, country, disc count, and format conflicts as variant-level conflicts.
- Allow matrix/runout evidence to disambiguate only when its source is reliable.
- Version the matching policy and store the version with each decision.
- Calibrate thresholds from labeled data instead of hand-tuning indefinitely.

### 2.4 Images and descriptions

Defer OCR, computer vision, and LLM assistance until deterministic performance is measured.
When introduced:

- Use them to propose evidence, not to make opaque final decisions.
- Retain bounding boxes/text spans or equivalent provenance.
- Require a deterministic post-check against catalog candidates.
- Evaluate image/text extraction separately from match-decision accuracy.
- Do not use seller or catalog images outside provider terms and applicable rights.

### Exit gate

- The labeled dataset meets the family and exact-variant precision targets.
- Every decision exposes matched, conflicting, missing, and derived evidence.
- Ambiguous shared-barcode and shared-catalog-number cases abstain correctly.
- Signed-copy claims remain separate from pressing identity unless catalog evidence establishes an
  official signed edition.

## Phase 3 — User-defined vinyl monitors

Status: **Internal rule prototype in progress; storage, discovery plans, and release gated**

Users should be able to monitor at three levels:

1. **Release family:** any acceptable vinyl pressing of an album
2. **Exact variant:** one specific Discogs release/pressing
3. **Variant plus collectible attributes:** for example, exact red pressing plus verified signed
   claim

### Monitor definition

`finder.watchlist` currently implements a versioned family/exact-release target and pure
buyer-ceiling triage on supplied listing and match evidence. It abstains on an unverified
destination quote, unknown subtotal, auction final price, stale listing, incomplete catalog
retrieval, and probable identity for an exact-release target. This internal candidate is not a
live availability or valuation claim. Condition IDs are an explicit allow-list; record/media
grade translation, saved buyer targets, and a public view remain future work. A separate
versioned eBay query plan can scan one configured exact-release target with up to three searches
of ten items each. The DS2 plan uses two title aliases without mandatory edition words;
initial best-match and newest-first refresh samples dedupe overlapping items. This is a bounded
retrieval prototype, not measured recall or automated matching to the selected release. On
September 23, 2026, an initial Production scan found the known DS2 eBay example in its bounded
results. The first query hit its ten-item cap, so this is one positive discovery check rather
than a coverage estimate or confirmation of the numbered edition.

A monitor should eventually contain:

- Target identity and acceptable alternatives
- Required and excluded attributes
- Condition floor and grading vocabulary
- Maximum item price and maximum delivered acquisition cost
- Accepted currencies and shipping destination
- Buying formats: fixed price, auction, best offer
- Seller constraints, if any
- Match-confidence minimum
- Valuation discount or opportunity-score threshold
- Scan cadence and alert cooldown, added only when scheduling exists

### Discovery planning

One monitor may produce multiple provider-specific searches. Query planning should combine broad
recall searches with deterministic post-filtering. Store the query plan and version so a missed
or included listing can be explained.
The current `config/watch_targets.toml` prototype keeps its query wording and version alongside
the selected Discogs release. Its results are persisted by the normal scan path and may be
inspected privately with `finder match --target-release-id`; target-specific post-filtering,
missed-listing audits, and buyer target storage remain open.

### Exit gate

- Exact and family monitors serialize to a stable, versioned schema.
- Query plans remain provider-specific and bounded.
- A listing can match multiple monitors without duplicate ingestion.
- Changing thresholds does not erase historical observations or match evidence.

## Phase 4 — Comparable-source qualification

Status: **Planned; required before valuation**

The eBay Browse API supplies active listings, not a dependable general sold-comparables feed.
eBay currently states that Marketplace Insights is restricted and not open to new users. Discogs
catalog identifiers are useful for identity, but Discogs marketplace pricing and sales history
are restricted data and are outside Finder's current permitted scope. Qualifying a separate sold
feed does **not** by itself resolve eBay's restriction on using its content to suggest or model
prices for eBay items. Treat feed rights and the intended Finder output as separate approvals.

Before writing valuation code, document candidate sources against:

- Written commercial-use rights and retention rights
- Permission to combine the source with eBay Browse content in a buyer-facing valuation or lead
  (including display, alerts, and paid access)
- Sold versus merely listed status
- Exact item/variant identifiers
- Transaction date, sale price, shipping, currency, and condition
- Best-offer/accepted-price behavior
- Coverage, latency, pagination, and rate limits
- Ability to store derived statistics and source provenance
- Cost at MVP and scaled request volumes
- Deletion, attribution, and freshness obligations

If no lawful source meets the minimum requirements, pause automated valuation. Do not substitute
scraping or active asking prices and call the result fair market value.

### Exit gate

- A written data-source decision records terms, fields, cost, retention, and failure modes.
- eBay's intended-use restriction and the sold source's commercial use are resolved in writing
  for the actual product flow.
- A representative sample maps to Finder identities with measured coverage.

## Phase 5 — Valuation and opportunity scoring

Status: **Planned after Phase 4 and explicit eBay product-use clearance**

### Comparable normalization

- Match comparable transactions at the exact-variant level whenever possible.
- Normalize sale price plus shipping in one currency; keep tax and unknown fees explicit.
- Normalize media and sleeve condition separately.
- Separate signed/numbered/official-special-edition comparables from standard copies.
- Detect relists, duplicate transactions, lots, bundles, damaged copies, and obvious outliers.
- Apply transparent time weighting and record the valuation date.

### Valuation output

A valuation should include:

- Conservative low, central, and high estimates
- Comparable count and effective sample size
- Oldest/newest comparable dates
- Identity scope: exact variant, family-adjusted, or attribute-adjusted
- Condition and currency assumptions
- Confidence and reasons confidence is limited
- Source and policy versions

No valuation should be emitted when the comparable count, identity confidence, or freshness gate
fails.

### Opportunity scoring

Evaluate delivered acquisition cost against the conservative valuation range, then account for:

- Match uncertainty
- Auction status and time remaining
- Shipping uncertainty
- Condition uncertainty
- Seller quality and return terms when lawfully available
- Attribute authenticity risk
- Liquidity and comparable freshness

Finder should explain why an opportunity qualifies. A low asking price alone is not enough.

### Exit gate

- Backtests use time-separated data to avoid look-ahead bias.
- Errors are measured by category, condition, and match-confidence tier.
- No opportunity is labeled a deal when valuation eligibility fails.
- Thresholds favor false negatives over costly false positives.

## Phase 6 — Scheduling, alerts, and hosting

Status: **Planned after ingestion, identity, and valuation gates**

Introduce unattended product infrastructure in this order, after the bounded Production and
identity gates. The existing Neon Production database and deletion endpoint are already hosted;
they are not evidence that unattended deal alerts are ready:

1. Idempotent scheduled scans
2. Persistent job state and leases
3. Feed freshness and failure monitoring
4. Shadow-mode opportunity generation without notifications
5. Alert deduplication, cooldowns, and state transitions
6. One delivery channel
7. Production backup and restore drills (schema migrations must precede the next schema change)

Run shadow mode long enough to inspect false positives, missed updates, and duplicate alerts.
Hosting is not a milestone by itself; dependable unattended behavior is.

### Exit gate

- Fourteen consecutive days of scheduled scans without silent failures or duplicate observations
- Stale-provider and failed-scan alerts verified
- Opportunity decisions reproducible from stored inputs
- Alert retries cannot deliver unlimited duplicates
- Database backup and restore tested

## Phase 7 — User product

Status: **Planned**

Only after the monitoring pipeline is dependable:

- Authentication and user ownership
- Search/browse Discogs catalog targets
- Monitor creation for family, exact variant, and collectible attributes
- Evidence-first listing and opportunity views
- Manual correction/feedback flow for ambiguous matches
- Alert preferences and delivery history
- Privacy, retention, rate limiting, abuse controls, and account deletion
- A small collector pilot with repeated weekly use, manually checked leads, and measured time
  saved; then test subscription or affiliate economics only within provider permissions

Do not optimize onboarding or payments before users can trust the matches and valuations. A
subscription requires a separate Discogs API use decision; affiliate links, if used, require
the relevant eBay Partner Network setup and tracking rules. Neither is assumed to be approved.

## Phase 8 — Additional marketplaces and categories

Status: **Planned**

Expansion order is a hypothesis, not a promise: evaluate trading cards first because set,
number, parallel, and grading identifiers can support an explicit fingerprint; then compare
sneakers, whose size/colorway/condition matter; leave watches and autographs until provenance,
counterfeit risk, and sparse comparable sales can be handled. Do not commit to any category
until its official/licensed listing and sold-data routes are checked.

| Candidate | Identity to resolve | Major uncertainty to test |
| --- | --- | --- |
| Trading cards | Set, year, card number, parallel, serial number, grading company/grade | Licensed sold data, graded versus raw comparables, counterfeit and altered cards |
| Sneakers | Model SKU, colorway, size, release, condition, box and accessories | Size-specific sold data and authorized marketplace feeds |
| Watches | Reference, dial, year, completeness, service and provenance | Authenticity, condition, sparse comps, and high financial exposure |

Select a second category only after a short provider and label pilot demonstrates that one
category can pass the same identity, data-rights, delivered-cost, and opportunity gates as
vinyl. eBay's pricing-use restriction follows eBay content into every category; switching
collectibles does not solve it. Build one new `categories/<name>` package and adapters as
needed, preserving generic Listing/Variant/Monitor contracts and separate policy versions.

### Marketplace qualification

A marketplace adapter is eligible only if Finder has:

- An official or expressly licensed data-access method
- Stable listing identity and usable lifecycle fields
- Clear commercial-use and retention terms
- Sufficient price, shipping, condition, seller, and image metadata
- Rate limits compatible with bounded monitors
- Contract fixtures and failure tests

### Category qualification

A collectible category is eligible only after defining:

- Product-family and exact-variant identity
- Category-specific fingerprint fields
- Condition/grading semantics
- Counterfeit and authenticity risks
- Comparable-compatibility rules
- A labeled evaluation set and accuracy gate

Potential categories such as trading cards, sneakers, watches, memorabilia, or instruments should
not be prioritized until vinyl proves the generic architecture and economic value.

## Cross-cutting engineering work

### Data provenance

Every derived record should retain source, source identifier, observation time, ingestion run,
normalizer version, and policy version. Preserve raw values alongside normalized values when they
may be needed to explain a match.

### Database evolution

- A persistent Neon database is already deployed. Introduce versioned migrations before the
  next Production schema change, with a rollback plan and a verified backup/restore procedure.
- Require forward and rollback tests for schema migrations.
- Keep current snapshots separate from immutable observations.
- Add price-history queries only after observation semantics are stable.
- Do not delete disappeared listings merely because a bounded search omitted them.

### Observability

Track provider requests, rate-limit state, results fetched, normalization failures, missing-field
rates, match outcomes, ambiguity rates, stale data, and scan duration. Metrics must not include
credentials or unnecessary personal data.

### Test layers

1. Pure normalization and scoring unit tests
2. Repository and migration tests
3. Provider contract tests with synthetic and sanitized fixtures
4. Live credentialed smoke tests with strict request limits
5. Labeled real-world matching evaluations
6. Time-separated valuation backtests
7. Shadow-mode alert evaluations

### Security and privacy

- Keep credentials in secret managers or untracked local environment files.
- Minimize retention of seller/user data.
- Sanitize captured marketplace responses before committing fixtures.
- Add dependency and secret scanning before hosting.
- Define data deletion and incident-response procedures before user accounts.

## Next six concrete work packages

1. **Provider-rights decision record.** Send the concrete inquiries in
   [`docs/decisions/provider-outreach.md`](docs/decisions/provider-outreach.md) through the
   official support channels. Record eBay's position on buyer-set price ceilings, valuation,
   alerts, retention, and business model, and Discogs' position on free outbound eBay links,
   display, caching, and paid use. Record separate go/no-go decisions for each release.
2. **Production data audit.** Finish seven bounded rap-vinyl scans on different days, use
   synthetic contract fixtures until retention rights are reviewed, and publish aggregate
   field-presence, shipping, request-budget, and failure statistics. No listing identities in
   public CI logs.
3. **Real-label workflow.** After the listing-use decision, make a private labeling form and
   adjudication guide. Sample at least 200 real listings across at least 40 release families,
   including shared identifiers, unofficial copies, signed claims, misleading titles, lots,
   and unknown shipping. Separate development from holdout before tuning the matcher.
4. **Candidate-retrieval and identity fixes.** Measure how often the true Discogs release is
   absent from the bounded title search. Add identifier and family queries under strict budgets;
   test artist/title aliases, duplicate masters, color/edition conflicts, and refusal to guess.
5. **Watchlist pilot.** Build and test target rules internally with synthetic listings. After
   scan reliability, data-use clearance, and Discogs outbound-use review, let a small cohort
   specify target release/family, maximum delivered subtotal, condition floor, and exclusions.
   Show uncertain identity and unknown shipping plainly; do not label a price as undervalued.
6. **Sold-source qualification.** Approach rights holders or licensed vendors with a concrete
   schema and pilot set. Test transaction coverage, accepted-offer accuracy, pressing identity,
   condition, fees, currency, and cost. If no lawful source and eBay use clearance exist, keep
   valuation and price-based alerts blocked.

Do not add a public frontend, subscription billing, broad scheduling, scraping, automated
image/LLM matching, or deal scoring merely because Production ingestion works. The earlier
decision contracts are complete, but their real-world accuracy has not been established.

## Decisions that require explicit review

Do not make these decisions implicitly in a feature commit:

- Lowering an accuracy gate
- Treating a seller claim as authenticated
- Treating active asking prices as sold comparables
- Adding a provider endpoint or data field with new terms restrictions
- Combining eBay content with external sold data to score, rank, or alert on underpriced eBay
  listings without an explicit provider-use decision
- Charging for an application that displays Discogs API content without reviewing its paid-app
  terms and obtaining any needed permission
- Showing Discogs API-derived evidence in a public Finder view that sends buyers to eBay without
  resolving Discogs' non-Discogs traffic restriction for the exact journey
- Adding image/OCR/LLM evidence to automatic exact matching
- Persisting raw marketplace responses
- Replacing or expanding the existing hosted infrastructure
- Supporting a new marketplace or category

Record the evidence, tradeoffs, and approval in the pull request or a dedicated decision record.

## Official constraints informing this roadmap

- [eBay Browse API search](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [eBay Buy API support and Marketplace Insights restriction](https://developer.ebay.com/api-docs/buy/static/ref-marketplace-supported.html)
- [eBay API License Agreement, pricing-model and restricted-use clauses](https://developer.ebay.com/join/api-license-agreement)
- [eBay Buy API Production and business-model requirements](https://developer.ebay.com/api-docs/buy/buy-requirements.html)
- [Discogs API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
- [Discogs unique-release guidelines](https://support.discogs.com/hc/en-us/articles/360005006334-Database-Guidelines-1-General-Rules)
- [Discogs vinyl identification guide](https://support.discogs.com/hc/en-us/articles/360008602254-How-To-Find-Information-On-A-Vinyl-Record)
- [Discogs format guidelines](https://support.discogs.com/hc/en-us/articles/360005006654-Database-Guidelines-6-Format)

Review provider capabilities and terms again before implementing each provider-dependent phase;
this roadmap records the state checked on the date above.
