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
- Initial genre focus: hip-hop/rap releases from roughly 2010–2026
- Marketplace: eBay Browse API only; never scrape eBay
- Catalog identity source: Discogs CC0 catalog endpoints only
- Persistence: SQLAlchemy with SQLite locally and portable repository boundaries
- Interface: Python CLI; no frontend, accounts, payments, notifications, or hosting yet

## Current project state

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

### Verified baseline

- Latest verified implementation baseline: commit `51c6a8f`
- Offline suite: 107 tests passing
- Ruff lint and formatting checks passing
- Live Discogs validation passing through GitHub Actions
- Live validation: five releases persisted, one unambiguous strong candidate scored 100,
  and a conflicting barcode was rejected

### Active blocker

Live eBay validation requires approved eBay developer credentials. Until approval, do not claim
that ingestion or matching has been validated against real eBay seller data.

## Architecture boundaries

- `domain.py`: generic Listing, Monitor, Product, Variant, and candidate evidence
- `adapters/base.py`: marketplace adapter protocol
- `adapters/ebay/`: eBay transport, discovery, normalization, and offline replay
- `adapters/discogs/`: Discogs catalog transport and normalization
- `categories/vinyl.py`: vinyl-specific identity extraction
- `matching.py`: deterministic, auditable candidate scoring and ranking
- `persistence.py`: repository protocols, current listing snapshots, observation history,
  catalog entities, and candidate persistence
- `service.py`: scan orchestration and summary semantics
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
- Conflicting barcodes force rejection. Color and edition conflicts prevent strong status.
- Shared identifiers may produce ambiguity and must not be resolved with arbitrary tie-breaking.
- Active asking prices are not sold comparables or fair market value.

## Provider and legal constraints

- Use official APIs; do not add scraping.
- Discogs access is limited to `/database/search` and `/releases/{id}`.
- Do not use Discogs marketplace, pricing, sales-history, seller, order, or fee data for Finder.
- Preserve “Data provided by Discogs” attribution wherever Discogs-derived data is displayed.
- Never log credentials, authorization headers, OAuth responses, or full sensitive payloads.

## Secrets

Real credentials belong only in:

- A local untracked `.env` file
- Encrypted GitHub Actions repository secrets
- A future hosting provider's secret manager

Never put real values in `.env.example`, workflow YAML, fixtures, documentation, issues,
prompts, or commit history. If a credential is committed, revoke it first and then remove it
from reachable Git history.

## Required checks

Run from the repository root before committing:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
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
- The change is committed to `main` with a clear message.
- Any applicable live smoke test passes.

## Near-term priorities

1. Validate eBay Browse ingestion with approved production credentials.
2. Turn sanitized real eBay responses into regression fixtures.
3. Measure candidate ranking against messy seller titles and incomplete item specifics.
4. Refine vinyl identity evidence based on observed failures, not guesses.
5. Identify a commercially permitted source of sold comparables.
6. Add valuation and opportunity scoring only after identity quality and comparable rights are
   established.

Do not build hosting, scheduling, alerts, or a frontend until live ingestion and identity
matching are demonstrably reliable.

## Maintaining this file

Update **Current project state**, **Verified baseline**, **Active blocker**, and
**Near-term priorities** after each material phase. Keep detailed implementation history in Git
commits and the README; do not turn this file into a line-by-line changelog.
