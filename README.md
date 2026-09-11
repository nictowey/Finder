# Finder

Finder is the foundation for a marketplace-monitoring and mispricing-detection platform. The eventual workflow is: define a monitor → discover listings → identify the exact product and variant → compare against real comparable transactions → evaluate opportunities → alert the user.

The current foundation has two connected parts: a local Python CLI ingests active eBay listings through the official Browse API, and a Discogs catalog provider stores canonical release metadata and evaluates deterministic match candidates. Its first monitor targets hip-hop/rap vinyl, with an intended focus on 2010–2026 releases. There is no scraping, frontend, account system, payment flow, notification delivery, LLM matching, or valuation.

## Quick start

Requires Python 3.11+. Run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
cp .env.example .env
# Edit .env with credentials for the command you want to run.
```

Discogs catalog search can be tested while eBay access is pending:

```bash
finder catalog-search "Kendrick Lamar DAMN"
```

With eBay production access, run the marketplace scan:

```bash
finder scan
```

After setup, **`finder scan`** searches the default monitor, enriches results, normalizes them, commits each valid listing, and prints a summary:

```text
Scan: completed
Listings fetched: 100
New listings: 80
Updated listings: 17
Skipped/invalid listings: 3
Total listings currently stored: 125
Configured scan limit reached; additional results may exist.
```

These numbers are illustrative. A repeat scan updates the same marketplace/item identities; it does not add duplicate rows. `python -m finder scan` is equivalent to the installed command.

Other invocations:

```bash
finder scan --monitor rap-vinyl --config config/monitors.toml
finder scan --json
finder scan --env-file /path/to/local.env
finder catalog-search "Travis Scott Rodeo" --limit 10
finder catalog-search "194398653419" --json
finder match --marketplace ebay --item-id 'v1|123456789012|0'
```

JSON logs go to stderr; the human or JSON summary goes to stdout. `.env` loading never overrides existing environment variables. Local database paths and the default configuration path resolve from the current working directory.

## eBay credentials and access

1. Register at the [eBay Developers Program](https://developer.ebay.com/).
2. Create an application keyset in [Application Keys](https://developer.ebay.com/my/keys).
3. Put its **App ID / Client ID** in `EBAY_CLIENT_ID` and **Cert ID / Client Secret** in `EBAY_CLIENT_SECRET`. A Dev ID, your eBay username/password, a redirect URI, and a user refresh token are not used by this implementation.
4. Use the matching environment: `EBAY_ENVIRONMENT=production` for live inventory or `sandbox` for eBay test data. Ensure your application has Browse access; valid keys alone do not resolve API access restrictions. Consult eBay's [Buy API access requirements](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html) for current production eligibility and onboarding requirements.
5. Optionally set **both** `EBAY_DELIVERY_COUNTRY` and `EBAY_DELIVERY_POSTAL_CODE` for destination-aware shipping estimates. The example sets country to `US` but leaves postal code blank. Without a complete destination, calculated shipping can be absent or less useful.

Finder requests an application OAuth token using `client_credentials` and the scope `https://api.ebay.com/oauth/api_scope`, caches it in memory until shortly before expiry, and renews it once on a Browse 401 response. No token is saved to disk. Never commit your `.env`, paste real credentials into tests, or put secrets in monitor configuration. `.env.example` contains empty credential values and `.gitignore` excludes local secrets and databases.

Sandbox uses `finder-sandbox.db` when the default SQLite URL is unchanged. **If you customize `FINDER_DATABASE_URL`, use different databases for Sandbox and Production.** Sandbox data is not real market inventory.

## Discogs catalog credentials and scope

1. Sign in to Discogs and open [Settings > Developers](https://www.discogs.com/settings/developers).
2. Generate a personal access token and put it in `DISCOGS_TOKEN`.
3. Keep an identifying application name and project URL or contact method in `DISCOGS_USER_AGENT`.
4. Run `finder catalog-search "artist album"` to verify access. One search with a limit of five makes one database-search request plus up to five release-detail requests.

For repository-level testing, save the token as a GitHub Actions repository secret named
`DISCOGS_TOKEN`, then run **Discogs smoke test** from the repository's Actions tab. The workflow
runs the offline test suite, synthesizes an eBay-shaped listing from a live barcode-bearing
Discogs release, scores the returned candidates, persists them, and verifies that an intentionally
conflicting barcode is rejected. It reports whether more than one release remains a strong
candidate; a passing test does not claim that a barcode uniquely identifies a pressing. Never
place the token in `.env.example`, workflow YAML, command output, issues, or commit history.

Finder calls only Discogs' `/database/search` and `/releases/{id}` catalog endpoints. It does not request seller inventory, marketplace statistics, pricing suggestions, orders, fees, or sales history. Discogs classifies titles, dates, formats, track listings, barcodes, identifiers, credits, and release relationships as CC0 catalog data. Its marketplace and pricing data is restricted and may not be used commercially under the current [Discogs API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use).

The CLI attributes results to Discogs and stores the source URL and observation time. Any future interface that displays Discogs-derived data must show “Data provided by Discogs” next to the data with a link to the relevant Discogs page, and must refresh displayed data within the freshness limits in Discogs' terms. This application uses Discogs’ API but is not affiliated with, sponsored or endorsed by Discogs. “Discogs” is a trademark of Zink Media, LLC.

## Configuring discovery

`config/monitors.toml` holds named, validated monitors. Select one with `--monitor`.

The default monitor uses the US Vinyl Records category (`176985`), the query `vinyl (rap,hip-hop,hip hop)`, fixed-price and auction formats, and newest-first ordering. It fetches up to two pages of 50 summaries, then gets details for each unique item. This is a bounded sample of active listings, not exhaustive marketplace coverage.

The **2010–2026 range is discovery intent, not an enforced release-year filter**. Seller titles and item specifics often omit years, and listing creation dates are not album release dates. The query can include older records and miss relevant listings with no genre terms in their titles. Customize the query or add an eBay `aspect_filter` using the site's category taxonomy to tune discovery. Do not treat any ingested record as a verified pressing or release-year match.

| Setting | Purpose |
| --- | --- |
| `id`, `name`, `description` | Generic monitor identity and intent |
| `marketplace` | Adapter selection; currently only `ebay` |
| `query` | Search text; not embedded elsewhere in the application |
| `source_options.marketplace_id` | eBay site, default `EBAY_US` |
| `source_options.category_ids` | Zero or one eBay category ID; values differ across sites |
| `source_options.buying_options` | Fixed-price, auction, and/or best-offer formats |
| `source_options.sort` | `newlyListed`, `endingSoonest`, `price`, `-price`, or `bestMatch` |
| `source_options.page_size` | 1–200 results per search page |
| `source_options.max_pages` | Scan budget; page size × pages must not exceed 10,000 |
| `source_options.fetch_details` | Default `true`; get item specifics through `getItem` |
| `source_options.aspect_filter` | Optional eBay-native aspect expression; requires a category |

Unknown configuration fields, duplicate monitor IDs, invalid types, and out-of-range budgets fail validation. Increasing the scan budget increases API use. A full default scan normally uses up to **103 HTTP requests**: one OAuth request, two searches, and up to 100 details, before retries. Set `fetch_details = false` to reduce usage, accepting missing item specifics. Provider pagination URLs are never followed; subsequent offsets are constructed against the configured official host.

## Architecture

| Module | Responsibility |
| --- | --- |
| `domain.py` | Generic `Listing`, `Monitor`, `Product`, `Variant`, and match-evidence models |
| `catalog.py` | Catalog-provider interface, separate from marketplace ingestion |
| `adapters/base.py` | `MarketplaceAdapter` protocol and observation/scan counters |
| `adapters/ebay/client.py` | HTTP transport, OAuth, bounded retry policy |
| `adapters/ebay/adapter.py` | Browse search, pagination, item detail enrichment |
| `adapters/ebay/normalize.py` | Pure eBay-to-domain normalization |
| `adapters/discogs/*` | Authenticated CC0 catalog search, release lookup, and normalization |
| `categories/vinyl.py` | Vinyl-only identity extraction for pressing and edition evidence |
| `matching.py` | Conservative deterministic candidate scoring with visible evidence |
| `persistence.py` | Listing/catalog repository protocols and SQLAlchemy implementation |
| `service.py` | Scan orchestration, durable writes, summary counts |
| `config.py`, `cli.py`, `logging.py` | Settings, command wiring, JSON logging |

The service depends on adapter and repository interfaces. Another marketplace can implement the marketplace adapter without changing storage or scan orchestration. A catalog source implements `CatalogProvider` instead. Marketplace and catalog identities are extensible strings, rather than closed provider enums.

`Product` represents a catalog work or release family. `Variant` represents one exact catalog release or edition. Discogs master and release IDs provide their current external identities. `listing_variant_candidates` connects marketplace observations to possible variants without asserting that a match is correct. `Comparable`, `Valuation`, `Opportunity`, and `Alert` remain planned concepts. Vinyl-specific interpretation of pressing, matrix/runout, label, and grading still belongs in a later category model.

Candidate scoring uses only explicit, inspectable evidence: barcode, catalog number, seller-supplied artist, title similarity, release year, and format descriptors. Conflicting barcodes force rejection, while title similarity alone cannot create a strong match. A `strong_candidate` is still a candidate and requires later validation; Finder does not yet designate exact matches.

Vinyl-specific extraction is isolated in `categories/vinyl.py`. It maps listing specifics and
Discogs release fields into artist, year, label, catalog number, barcode, color, edition, country,
format, size, speed, and matrix/runout evidence. Color or edition conflicts prevent a candidate
from being labeled strong even when broader album identity agrees. Shared barcodes can still
produce multiple strong candidates, which Finder reports as ambiguity rather than breaking ties
arbitrarily.

### Stored listing data

The `listings` table has a composite primary key **(`marketplace`, `marketplace_item_id`)**, indexed latest-observation timestamp, original first-observation timestamp, and a normalized JSON `data` payload. The append-only `listing_observations` table stores one immutable snapshot per marketplace item and observation timestamp; repeating the same observation is idempotent. JSON is used through SQLAlchemy's portable type rather than SQLite-specific SQL. The payload contains:

- Full eBay Browse item ID, title, current price, currency, and price kind.
- Quoted shipping amount and its currency, plus computed total acquisition cost.
- Condition text/ID; seller username, feedback percentage, and score.
- Listing URL, primary image URL, categories, buying formats.
- Item specifics as a mapping from aspect names to lists of values.
- Listing creation, original listing, and end timestamps when present.
- Finder's first/latest observation and last successful detail-observation timestamps.
- Data-quality flags, search/destination context, and selected eBay metadata, including both original price fields, shipping options, and group/legacy IDs when supplied.

Money is calculated with `Decimal` and serialized as decimal strings, preserving exact values in SQLite. Dates are UTC. The full Browse item ID is retained, including its variation component. Group-level search results are not expanded or asserted to be exact variants. Images are stored as URLs; no image downloads or HTML descriptions are collected.

### Cost and uncertainty

`total_acquisition_cost = current_price + shipping_cost` only when both amounts are known and their currencies match. It is a **one-unit, pre-tax quoted subtotal**, excluding tax, duties, and other fees; no currency conversion is performed. The lowest returned shipping quote in the price currency is selected. Original shipping options remain available for inspection. A quote is not a guarantee of final checkout cost.

Missing shipping is **null, never zero**. Explicit free shipping is zero. Missing or malformed prices are retained as unknown with a quality flag; missing identity or title makes a listing invalid. Auction prices have `price_kind = current_bid`, and any subtotal based on them is provisional, not a winning bid or a buy-it-now price. No price is treated as fair market value.

Updates preserve the original first-observation time and refresh current values. Older observations cannot replace newer data. If detail enrichment fails or is disabled after an earlier successful lookup, stored item specifics are retained with their original `details_observed_at` and an `item_specifics_stale` flag. Other unavailable current fields become null rather than silently reusing stale prices or shipping.

### Database portability

The default is `FINDER_DATABASE_URL=sqlite:///finder.db`. SQLAlchemy keeps SQL and engine management behind the repository. A hosted PostgreSQL deployment can use a URL such as `postgresql+psycopg://user:password@host/finder` after installing that driver's package; the service and adapter need no changes. Hosted databases have **not been integration-tested in Phase 1**. Provision the database and use a new empty schema for an initial move; changing the URL does not transfer existing data.

`create_all` bootstraps `listings`, `listing_observations` (observation history), `products`, `variants`, and `listing_variant_candidates`. It is not a migration system. Introduce versioned migrations before changing a deployed schema or migrating production data. SQLite is appropriate for this small CLI; the unique keys prevent duplicate identities, but the MVP is not a distributed scan scheduler. Observation history records what Finder saw; it does not yet infer sales, removal reasons, or fair value. Listings absent from a later bounded scan are not deleted or marked sold; `total_stored` is all retained identities, not a live-inventory count.

### Replaying sanitized eBay responses

When live eBay access is available, captured API-shaped bundles can be converted into safe,
offline regression fixtures. The input format is `schema_version: 1`, a `search_responses` list,
and a `detail_responses` object keyed by item ID. Sanitation replaces item and seller identities,
removes captured timestamps and pagination URLs, and substitutes non-routable example URLs.

```bash
python scripts/sanitize_ebay_fixture.py raw-ebay-bundle.json sanitized-ebay-bundle.json
python scripts/replay_ebay_fixture.py sanitized-ebay-bundle.json
```

Replay refuses bundles not explicitly marked as sanitized. Review a generated fixture before
committing it; titles, prices, and item specifics remain because they are needed for regression
coverage. Never capture authorization headers, OAuth responses, credentials, or full HTTP logs.

## Failure behavior and summary semantics

- Timeouts, connection failures, 429s, and server errors get up to three retries after the initial attempt, with backoff/jitter. `Retry-After` seconds or HTTP dates are honored; a requested wait over 60 seconds stops the scan instead of retrying too soon.
- OAuth failures, persistent rate limits, Browse permission failures, malformed search pages, and exhausted search retries fail the scan. Previously committed listings remain available.
- An unavailable detail item (404/410) or a known ended listing is skipped. Other detail request/response failures retain the search summary with `details_unavailable`; authorization/rate-limit failures remain scan-wide errors.
- Missing optional fields retain nulls/quality flags. A malformed listing does not stop other listings.
- Deliberate log fields exclude credentials, auth headers, full request/response bodies, and database URLs. Logs include item IDs and scan metadata; treat stored listings as marketplace data.

`fetched` counts search summary entries returned, including duplicates and invalid entries. `new` counts committed inserts. `updated` counts existing identities re-observed, even when their prices are unchanged. `skipped_invalid` includes malformed, duplicate-in-scan, unavailable, and ended items; JSON includes reason counts. `unprocessed` reports fetched entries left unwritten after a fatal error. `partial_details` counts stored observations whose optional detail lookup failed. `limit_reached` means further results may exist beyond the configured budget.

Exit codes: **0** for a completed bounded scan (possibly with skipped listings or optional detail failures), **1** for a failed scan, **2** for setup/configuration errors, and **130** for interruption. Check the summary's limit and detail-failure fields even on exit 0. A zero-result response is a successful scan; it does not prove no matching inventory exists elsewhere.

## Tests

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
```

Tests run offline with `httpx.MockTransport` and temporary SQLite databases. Coverage includes eBay and Discogs normalization, exact monetary arithmetic, missing data, configuration, authentication, retries, pagination, deduplication, observation history, catalog persistence, deterministic match evidence, fixture sanitation/replay, repeat scans, and both CLI flows. The 20-case vinyl evaluation set uses real album names and release years but deliberately synthetic identifiers and candidate metadata; it tests ranking and ambiguity policy, not the accuracy of Discogs community data or a real seller's description. The live workflow separately checks current Discogs response shapes and end-to-end persistence with your encrypted repository secret.

## Next phases

1. Validate Discogs catalog search with a personal token and eBay discovery when its developer account is approved.
2. Refine candidate retrieval and add a vinyl-specific identity model with explicit ambiguity handling.
3. Ingest a commercially permitted source of real sold comparables; normalize condition, shipping, currency, and transaction dates before valuation.
4. Implement valuations and opportunity scoring with freshness, confidence, and explicit uncertainty.
5. Add alert delivery and persistent scheduling once ingestion and scoring are dependable.
6. Add other marketplace adapters and collectible categories, then a user-facing product when warranted.

Active asking prices alone are not sold comparables, and Phase 1 makes no profitability claims.

## Official API references

- [Browse search](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [Browse getItem](https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem)
- [OAuth application tokens](https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html)
- [Buying-format and delivery filters](https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html)
- [Shipping context headers](https://developer.ebay.com/api-docs/buy/static/api-browse.html)
- [Discogs API documentation](https://www.discogs.com/developers)
- [Discogs API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
