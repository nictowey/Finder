# Finder Agent Guide

## Purpose and scope

Finder is a pressing hunter under a **zero-cost development and product evaluation** mandate.
The owner saves specific vinyl pressings
(Discogs releases), sets their own prices, and gets alerts when an eBay listing is plausibly
that pressing at or below those prices. It is not approved for public launch. Price judgments come
from the owner, never from a model of eBay data.

- Category: vinyl records, any genre. Other collectibles only after vinyl works well.
- Marketplace: eBay Browse API only; never scrape eBay.
- Catalog: Discogs catalog endpoints only.
- Development: local SQLite, synthetic fixtures and disposable local PostgreSQL. Hosted jobs
  and live provider checks are paused under the strict $0 mandate; see
  [zero-cost development](docs/zero-cost-development.md). Do not add paid services, trials
  requiring billing, or production database work. Free-tier branding is not a cost guarantee.
- Current development interface: `finder-local` for the loopback review workspace and
  `finder-offline` for the synthetic SQLite demo. Legacy live CLI/dashboard/worker commands
  are outside this zero-cost development path.

The October 3, 2026 mandate supersedes the former personal-only goal with zero-cost product
development. The public-launch gates in `ROADMAP.md` and `docs/decisions/` remain unsatisfied. Read
[the personal pressing hunter plan](docs/personal-pressing-hunter-plan.md) before changing
product behavior, and [the evidence log](docs/evidence-log.md) for dated operational history.

## How it works

1. Each watch is a Discogs release plus owner settings: the price for a likely match
   (`maximum_subtotal`), an optional lower price for unclear listings (`gamble_max`),
   auction alert timing, extra searches, and a cheat sheet of signs (`tells`) and
   common-version signs (`anti_tells`).
2. `watch_profile.py` caches the release's sibling vinyl versions from the Discogs master
   (weekly) and suggests distinguishing signs (`categories/vinyl_clues.py`). The owner accepts
   suggestions; nothing is applied automatically.
3. `discovery_worker.py` runs resumable search passes over up to six queries per watch
   (album searches, owner searches, one barcode `gtin:` search), hydrates listings, and
   re-reads them on a schedule. The new-listing cadence is 10 minutes, stretching as enabled
   queries grow so polling stays within `POLL_BUDGET` searches per day.
4. `watch_worker.assess_review` sorts each listing into **likely yours**
   (`possible_pressing`), **unclear** (`family_review`) or **likely another version**
   (`conflicting`), then decides whether it alerts. Auctions alert only near their end.
5. The dashboard shows photos, signs found or missing, a seller-question draft and verdict
   buttons. Verdicts measure how well each tier works.

Up to `MAX_WATCHES` (20) watches; keep `watch_store.py` and `functions/watchlist.ts` in sync.

## Architecture boundaries

- `domain.py`: generic Listing, Monitor, Product, Variant, and candidate evidence
- `adapters/base.py`: marketplace adapter protocol
- `adapters/ebay/`: eBay transport, discovery, normalization, and offline replay
- `config/watch_targets.toml`: optional per-release title aliases added to a watch's searches
- `adapters/discogs/`: Discogs catalog transport and normalization
- `categories/vinyl.py`: vinyl-specific identity extraction
- `categories/vinyl_clues.py`: cheat-sheet signs, suggestions and seller-text matching
- `watch_store.py`, `watch_profile.py`, `watch_worker.py`, `discovery_*.py`: private watch loop
- `functions/watchlist*.ts`: owner-only dashboard and API
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
- Whole identifier values `None`, `N/A` (including `N / A`) and `Does not apply` mean
  missing evidence, case-insensitively. Preserve real identifiers and original source fields.
- Conflicting barcodes force rejection. Color and edition conflicts prevent strong status.
- Shared identifiers may produce ambiguity and must not be resolved with arbitrary tie-breaking.
- Active asking prices are not sold comparables or fair market value.


## Identity and alert rules

- A tier is a sorting aid from seller text and catalog data, never verification. The owner
  confirms from photos. Never label a listing an exact pressing, a bargain or a fair value.
- Signs are read from seller text only; explicitly negated mentions ("not a reissue") and
  sleeve colors in titles do not count as common-version signs.
- Explicitly denied target or required colors conflict; they cannot create likely or
  unclear-price alerts. Keep positive clauses separate from negated alternatives, and
  exclude artist, album, sleeve, cover and label color words from disc-color evidence.
- An explicit unambiguous seller-title denial of a catalog-required signed insert conflicts
  with that target package and blocks alerts, including unclear-price alerts. Missing mention,
  shipping signatures, unsigned records/sleeves and ambiguous claims are not package denials.
- Explicit positive disc-color choices (such as "blue or white") remain unclear. Alternative
  branches cannot satisfy color signs; independent definite evidence or identifiers cannot
  erase an unresolved choice. Denials and definite conflicts still take precedence.
- Every required sign present with a successful catalog comparison, no unresolved color
  choice and no unresolved competing pressing → likely yours; any common-version sign → likely another version;
  otherwise unclear. A generic album word such
  as “Rare” needs an explicit title claim; a conflicting structured release title rejects it.
- An owner identity verdict or purchase marker removes that item from the unjudged queue
  and blocks repeat alerts. Purchasing never implies a positive identity judgment. Accuracy
  uses explicit identity labels only; unsure remains separate from the decided denominator.
  Preserve the first available judgment provenance when labels are edited or cleared.
- Unclear listings alert only when the owner set `gamble_max` and the delivered price is at or
  under it. Uncertainty about other pressings is shown, not used to hold an alert, except in
  `strict` mode.
- Stale details, ended listings and unaccepted conditions block alerts. When an owner price
  applies, the delivered subtotal must be known, same-currency and destination-confirmed.
  No-ceiling review leads can alert with unknown totals or an unset destination; a saved
  destination must still match. Label missing information, and never claim an unverified
  price threshold in a generic notification.

## Provider and data constraints

- Use official APIs; do not add scraping.
- Discogs access is limited to `/database/search`, `/releases/{id}` and
  `/masters/{id}/versions`. Never use Discogs marketplace, pricing, sales-history, seller,
  order or fee data.
- Do not build price estimates, "deal scores" or fair values from eBay content; eBay's API
  agreement restricts that. Owner-set prices are fine.
- Listing photos are displayed from eBay's image host only; do not store image files.
- Keep seller-deletion handling working; it is required for the Production keyset.
- Preserve "Data provided by Discogs" attribution wherever Discogs-derived data is displayed.
- Never log credentials, authorization headers, OAuth responses, or full sensitive payloads.
- Actions logs are public; do not log listing IDs, titles, sellers, URLs, or seller-provided
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

During the zero-cost transition, run offline provider regressions only. Authenticated smoke
checks and production scans remain paused; do not treat skipped live checks as passed.

Only after live provider work is explicitly authorized under a verified cost and rights plan:

1. Run one bounded live scan.
2. Sanitize captured API-shaped responses before committing fixtures.
3. Replay sanitized fixtures offline.
4. Inspect missing item specifics, shipping uncertainty, ambiguous variants, rate limits,
   and response-shape differences.
5. Add regression tests before changing matching weights.

## Definition of done

A change is complete when:

- It respects adapter, category, and persistence boundaries.
- New behavior has automated tests, including failure and ambiguity cases where relevant.
- Tests, lint, formatting and type checks pass.
- Documentation and `.env.example` are updated when configuration changes.
- No secrets or unsanitized marketplace identities are committed.
- Schema changes are additive and covered by `scripts/check_watch_postgres.py`.

## Maintaining this file

Keep this file short and current: purpose, rules and boundaries. Record dated deployments and
measurements in `docs/evidence-log.md`, not here.
