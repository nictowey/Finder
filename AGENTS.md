# Finder Agent Guide

## Purpose

Finder is a marketplace-monitoring and mispricing-detection platform. A user defines what
they want monitored; Finder discovers marketplace listings, identifies the exact product and
variant, records observations, and will eventually calculate valuations, score opportunities,
and deliver alerts.

Keep the core domain marketplace- and category-neutral. Marketplace-specific behavior belongs
behind adapters. Category-specific identity rules belong in category modules.

## Current scope

- Category: vinyl records
- Any genre of vinyl; the first broad monitor and historical evaluation set focus on hip-hop/rap
- Marketplace: eBay Browse API only; never scrape eBay
- Catalog identity source: Discogs CC0 catalog endpoints only
- Persistence: SQLAlchemy with SQLite locally and portable repository boundaries
- Interface: Python CLI, private owner-only watchlist dashboard and scheduled review worker,
  manual bounded cloud validation, and a hosted eBay deletion endpoint

## Current project state

The accuracy-gated development sequence and product decisions are maintained in `ROADMAP.md`.
Read it before proposing or implementing a new phase.

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

### Verified baseline

- Latest verified implementation baseline: current `main` after required checks
- Offline suite: Python and Node tests passing; rerun required checks before commit
- Ruff lint and formatting checks passing
- Live Discogs validation passing through GitHub Actions
- Multi-query Discogs smoke passed on the `discogs-candidate-retrieval-2026` branch; this uses
  a synthetic seller listing derived from a real catalog release, not a labeled eBay listing
- Live validation: five releases persisted, one unambiguous strong candidate scored 100,
  and a conflicting barcode was rejected
- Live eBay Sandbox validation passed through GitHub Actions: application OAuth token issued,
  `vinyl` search returned 10 items, 9 normalized and round-tripped through persistence
  (1 skipped as ended). Sandbox inventory is test data, not vinyl market data.

### Production status

The free Neon Production branch and deletion Function are deployed with Production credentials.
Candidate persistence now guards against recreating eBay-derived evidence after a seller
deletion. PostgreSQL concurrency behavior still needs an integration check; see the data
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

## Architecture boundaries

- `domain.py`: generic Listing, Monitor, Product, Variant, and candidate evidence
- `adapters/base.py`: marketplace adapter protocol
- `adapters/ebay/`: eBay transport, discovery, normalization, and offline replay
- `config/watch_targets.toml`: versioned internal exact-target search plans
- `adapters/discogs/`: Discogs catalog transport and normalization
- `categories/vinyl.py`: vinyl-specific identity extraction
- `matching.py`: deterministic, auditable candidate scoring and ranking
- `persistence.py`: repository protocols, current listing snapshots, observation history,
  catalog entities, and candidate persistence
- `service.py`: scan orchestration and summary semantics
- `functions/ebay-deletion.ts`: eBay challenge, signed deletion notices, and tombstones
- `config/monitors.toml`: monitor definitions; do not hard-code discovery queries elsewhere
- `evaluations/`: deterministic policy evaluations, not claims about real market value
- `scripts/`: live validation and sanitized fixture utilities

Do not put vinyl-only fields on the generic Listing, Product, or Variant models. Do not put
Discogs behavior in the eBay adapter or marketplace behavior in catalog providers.

## Data and matching rules

- Deduplicate listings by `marketplace + marketplace_item_id`.
- Preserve the first observation and update the current snapshot only with an equal or newer
  observation.
- Record each distinct observation timestamp in the append-only history table.
- Missing shipping is unknown, not zero.
- Total acquisition cost is price plus same-currency shipping only; it excludes tax, duties,
  and fees.
- A strong candidate is not an asserted exact match.
- `probable_variant` is provisional; never turn it into `exact_variant` before the labeled
  precision gate and representative catalog coverage are established.
- Conflicting barcodes force rejection. Color and edition conflicts prevent strong status.
- Shared identifiers may produce ambiguity and must not be resolved with arbitrary tie-breaking.
- Active asking prices are not sold comparables or fair market value.

## Provider and legal constraints

- Use official APIs; do not add scraping.
- Discogs access is limited to `/database/search` and `/releases/{id}`.
- Do not use Discogs marketplace, pricing, sales-history, seller, order, or fee data for Finder.
- For a catalog-numbered pressing, a shared barcode/color or a seller title serial claim alone
  must not promote the candidate; an explicit structured numbered claim is still unverified.
  Keep the individual copy number separate from the catalog release identity.
- An explicitly negated seller claim such as "Not Numbered" is not a positive numbered claim.
- An exact watch target can reserve one Discogs detail slot even when seller-text search misses
  the release. A directly fetched target is a candidate to assess, never proof that the eBay
  listing is that release. Preserve target search-miss and missing numbered-claim reasons.
- Target evaluation may use one additional catalog-derived family search (four searches total)
  to retrieve competitors. Keep the same detail cap and distinguish seller-search recall from
  catalog-derived retrieval.
- eBay's API agreement restricts using eBay content to suggest or model prices for eBay items;
  obtain a written provider-use decision before deal scoring, price-based alerts, or paid launch.
- Review eBay's intermediate-copy and algorithm-training restrictions before retaining real
  labeled listings, publishing sanitized real fixtures, or tuning matching policy on them.
- Resolve Discogs' non-Discogs traffic restriction before publicly showing API-derived catalog
  evidence beside outbound eBay listing links, including in a free watchlist.
- Review Discogs' API conditions for paid apps, display freshness, caching, and required notices.
- Preserve “Data provided by Discogs” attribution wherever Discogs-derived data is displayed.
- Never log credentials, authorization headers, OAuth responses, or full sensitive payloads.
- Live smoke logs are public; do not log listing IDs, titles, sellers, URLs, or seller-provided
  item-specific names there.

## Secrets

Real credentials belong only in:

- A local untracked `.env` file
- Encrypted GitHub Actions repository secrets
- Neon Function environment variables for the Production deletion endpoint

Never put real values in `.env.example`, workflow YAML, fixtures, documentation, issues,
prompts, or commit history. If a credential is committed, revoke it first and then remove it
from reachable Git history.

## Required checks

Run from the repository root before committing:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
npm test
npm run typecheck
```

When Discogs behavior changes, also run the **Discogs smoke test** workflow and confirm both the
offline suite and authenticated matching validation pass.

When eBay access becomes available:

1. Run one bounded live scan.
2. Sanitize captured API-shaped responses before committing fixtures.
3. Replay sanitized fixtures offline.
4. Inspect missing item specifics, shipping uncertainty, ambiguous variants, rate limits,
   and response-shape differences.
5. Add regression tests before changing matching weights.

## Definition of done

A material change is complete only when:

- The implementation respects adapter, category, and persistence boundaries.
- New behavior has automated tests, including failure and ambiguity cases where relevant.
- Existing tests, lint, and formatting pass.
- Documentation and `.env.example` are updated when configuration changes.
- No secrets or unsanitized marketplace identities are committed.
- The change is reviewable in a PR, then merged to `main` with a clear message when authorized.
- Any applicable live smoke test passes.

## Near-term priorities

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

## Maintaining this file

Update **Current project state**, **Verified baseline**, **Active blocker**, and
**Near-term priorities** after each material phase. Update `ROADMAP.md` when a phase, gate, or
product decision changes. Keep detailed implementation history in Git commits and the README;
do not turn this file into a line-by-line changelog.
