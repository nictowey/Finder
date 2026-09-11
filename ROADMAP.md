# Finder Development Roadmap

Last updated: 2026-09-11

## North star

Finder should help a collector define an exact item they want, continuously inspect supported
marketplaces, distinguish the correct collectible variant, estimate a defensible acquisition
value from lawful comparable data, and surface only opportunities that meet explicit confidence
and price thresholds.

The first vertical is hip-hop/rap vinyl, primarily releases from 2010–2026, using eBay for active
listings and Discogs for catalog identity. Later categories and marketplaces must fit the same
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
7. **Schema changes remain deliberate.** Add migrations before a hosted database or any schema
   change that must preserve production data.
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
set, but the holdout set determines whether the change is accepted.

## Roadmap overview

| Phase | Status | Exit gate |
| --- | --- | --- |
| 0. Foundations | Complete | Offline tests and live Discogs smoke test pass |
| 1. Real eBay data validation | Blocked on credentials | Bounded scans and sanitized replays pass |
| 2. Pressing identity engine | In progress | Labeled precision gates met |
| 3. User-defined vinyl monitors | Planned | Exact/family targets produce stable discovery plans |
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

Status: **Blocked on approved eBay credentials**

### Deliverables

1. Run small production scans with strict request and result caps.
2. Record API response-field presence rates without logging credentials or prohibited payloads.
3. Sanitize representative responses and commit replay fixtures.
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

Status: **In progress; useful work can continue before eBay approval**

### 2.1 Target ontology

Add explicit category models without changing the generic core:

- `VinylFingerprint`: normalized manufacturing evidence for a pressing
- `CollectibleAttribute`: autograph, number, sealed status, obi, insert, hype sticker, or other
  copy/packaging characteristic
- `EvidenceRecord`: value, source, extraction method, observed time, and reliability class
- `MatchDecision`: outcome, ranked candidates, conflicts, missing evidence, and policy version

### 2.2 Candidate retrieval

- Generate multiple Discogs queries from artist/title plus strong identifiers when present.
- Retrieve candidates at the master/family level before ranking exact releases.
- Normalize aliases, punctuation, featured artists, alternate catalog-number formatting, and
  barcode formatting without discarding original values.
- Bound candidate counts and API use; cache catalog releases with freshness metadata.
- Never assume the first Discogs search result is the correct pressing.

### 2.3 Deterministic matching

- Separate family match evidence from pressing match evidence.
- Add explicit positive, negative, and missing evidence.
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

Status: **Planned after identity semantics stabilize**

Users should be able to monitor at three levels:

1. **Release family:** any acceptable vinyl pressing of an album
2. **Exact variant:** one specific Discogs release/pressing
3. **Variant plus collectible attributes:** for example, exact red pressing plus verified signed
   claim

### Monitor definition

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
are restricted data and are outside Finder's current permitted scope.

Before writing valuation code, document candidate sources against:

- Written commercial-use rights and retention rights
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
- Legal/commercial use is clear enough for the intended product.
- A representative sample maps to Finder identities with measured coverage.

## Phase 5 — Valuation and opportunity scoring

Status: **Planned after Phase 4**

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

Introduce infrastructure in this order:

1. Idempotent scheduled scans
2. Persistent job state and leases
3. Feed freshness and failure monitoring
4. Shadow-mode opportunity generation without notifications
5. Alert deduplication, cooldowns, and state transitions
6. One delivery channel
7. Hosted database migrations, backups, and restore tests

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

Do not optimize onboarding or payments before users can trust the matches and valuations.

## Phase 8 — Additional marketplaces and categories

Status: **Planned**

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

- Introduce Alembic before deploying a persistent hosted database.
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

## Immediate work while eBay approval is pending

The following work is useful without pretending real eBay accuracy is already known:

1. Define `VinylFingerprint`, `CollectibleAttribute`, `EvidenceRecord`, and `MatchDecision`
   contracts with serialization tests.
2. Split family matching from exact-variant matching.
3. Add matching-policy versioning and explicit missing/conflicting evidence.
4. Expand deterministic evaluations for shared barcodes, catalog-number reuse, colored variants,
   unofficial releases, signed claims, and numbered copies.
5. Build the labeled-evaluation format and annotation guide so real eBay fixtures can be added
   consistently after approval.
6. Add a comparable-source decision template; do not implement valuation yet.
7. Prepare migration tooling before any hosted database exists.

Avoid frontend, hosting, alerts, payment infrastructure, scraping, automated image/LLM matching,
and valuation until their preceding gates are met.

## Decisions that require explicit review

Do not make these decisions implicitly in a feature commit:

- Lowering an accuracy gate
- Treating a seller claim as authenticated
- Treating active asking prices as sold comparables
- Adding a provider endpoint or data field with new terms restrictions
- Adding image/OCR/LLM evidence to automatic exact matching
- Persisting raw marketplace responses
- Migrating to hosted infrastructure
- Supporting a new marketplace or category

Record the evidence, tradeoffs, and approval in the pull request or a dedicated decision record.

## Official constraints informing this roadmap

- [eBay Browse API search](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [eBay Buy API support and Marketplace Insights restriction](https://developer.ebay.com/api-docs/buy/static/ref-marketplace-supported.html)
- [Discogs API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
- [Discogs unique-release guidelines](https://support.discogs.com/hc/en-us/articles/360005006334-Database-Guidelines-1-General-Rules)
- [Discogs vinyl identification guide](https://support.discogs.com/hc/en-us/articles/360008602254-How-To-Find-Information-On-A-Vinyl-Record)
- [Discogs format guidelines](https://support.discogs.com/hc/en-us/articles/360005006654-Database-Guidelines-6-Format)

Review provider capabilities and terms again before implementing each provider-dependent phase;
this roadmap records the state checked on the date above.
