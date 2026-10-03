# Local development review

This isolated development adapter uses Finder's existing matcher and owner-price policy with
synthetic or user-authored cases. It is not a market-ready replacement for autonomous remote
Finder. No providers, catalog requests, Neon, notifications, hosted deployment, owner-data
migration or credential imports are involved. There is no catalog-alternative coverage, so a
plausible matching color remains unclear; the UI never claims verified pressing identity.

## Start and return

Use the project's existing Python environment, in a clean shell without exported live settings:

```bash
PYTHONPATH=src python -m finder.local_web --database finder-local-review.sqlite3 --init
```

After an editable installation, `finder-local` invokes the same entrypoint. Initialization
requires a new filename and an existing parent directory. It never overwrites a database.
Open the exact printed `http://127.0.0.1:8765` address in your browser. `--port 8766` chooses
another local port; `--port 0` prints the chosen available port. No browser launches automatically.

1. Load the invented example, enter a target and case in the form, or load observation JSON.
2. Review the draft, then select **Save observation bundle**. Form entry never invents manual
   observation times or delivery confirmation. The synthetic example uses the current fixture time.
3. Read the existing matcher evidence, unresolved checks, and your two price caps. The policy
   simulation is informational; it never sends an alert.
4. Save **Mine**, **Other**, or **Unsure**. These judgments are independent of purchases.
5. Use **Export review record** for archival JSON containing inputs, observations and decisions.
   This export is not a restorable backup; the importer accepts observation bundles only.
6. Stop with Ctrl+C. Restart with the same database and omit `--init` to recover saved decisions.

Clean close/reopen is verified. Reopening refuses any existing SQLite journal, WAL or SHM
sidecar, including after a crash. Preserve the workspace and all sidecars for recovery; do not
delete them to bypass the refusal. Automatic crash recovery is outside this first adapter.

Unsubmitted drafts are held only in page memory. Refreshing the saved review retains your draft;
reloading the browser discards it. Validation errors retain draft inputs. A conflicting verdict
save refreshes the current assessment and asks you to review it again. Historical judgment
provenance remains intact when evidence or a target changes.

## Observation bundle

The JSON editor and file importer use the same contract. A minimal valid bundle is:

```json
{
  "schema_version": 1,
  "source": "synthetic",
  "target": {
    "artist": "Example Ensemble",
    "album": "Offline Horizons",
    "colors": ["Blue"],
    "formats": ["LP"]
  },
  "settings": {
    "maximum_subtotal": "30.00",
    "gamble_max": "15.00",
    "currency": "USD",
    "country": "US",
    "postal_code": "00000",
    "tells": [{"kind": "color", "value": "Blue", "required": true}]
  },
  "listings": [{
    "id": "example-blue",
    "title": "Example Ensemble Offline Horizons blue vinyl",
    "observed_at": "2026-10-03T12:00:00Z",
    "details_observed_at": "2026-10-03T12:00:00Z",
    "current_price": "10.00",
    "currency": "USD",
    "shipping_cost": "4.00",
    "shipping_currency": "USD",
    "price_kind": "fixed_price",
    "delivery_country": "US",
    "delivery_postal_code": "00000"
  }]
}
```

Times above are illustrative. Supply the actual time of your manual observation; old details stay
stale, and no import refreshes their age by itself. Choose `source: "manual"` for user-authored
cases and `"synthetic"` for invented fixtures. The source describes input provenance, not a
provider verification.

Targets require artist and album, with optional colors, catalog_numbers, barcodes and formats
arrays. Settings accept the two optional positive price caps, currency, paired country/postal_code,
condition_ids, alert_mode (`review_leads` or `strict`), auction_alert_minutes, tells and anti_tells.
The unclear cap cannot exceed the likely-match cap. Each tell has kind, value and required.

Each case requires id, title and a timezone-aware observed_at. Optional fields are
 details_observed_at, artist, album, colors, catalog_numbers, barcodes, formats, current_price,
 currency, shipping_cost, shipping_currency, price_kind (`fixed_price`, `current_bid`, `unknown`),
 condition_id, listing_ends_at, delivery_country and delivery_postal_code. Unknown price/shipping
is null, never zero. Shipping must have the same currency to form a subtotal; destination quotes
must explicitly match saved country and postal code for capped eligibility.

All extra fields are rejected. IDs use 1–64 letters, digits, hyphens or underscores, starting
with a letter or digit. Inputs are limited to 256 KiB per request and 100 case identities in a
workspace. Whole batches validate before writing. Observations are additive; stale observations
cannot overwrite newer current inputs. Target/settings changes invalidate stale review saves.
No URLs, photos, provider payloads, owner data, or credentials belong in this format.

## Local safety boundary

Only tagged local SQLite workspaces can reopen. Ordinary production, demo and unrelated database
files are refused. The command rejects live environment settings and ignores rather than loads
`.env`. It binds only to `127.0.0.1`, requires the exact Host on every route, and requires the exact
Origin plus a random process CSRF token for mutations. GET routes never change the workspace.
There is no generic file browser, CORS, external asset, analytics or browser-storage use.

The runtime guard permits the local listener and selected SQLite path while rejecting outbound
connections, DNS, subprocesses, dotenv access and other SQLite databases. Reads time out; input
length, JSON and content type are bounded and validated. Responses do not reflect incoming payloads
or secrets. This guards trusted development code against regressions. It is not a sandbox against
malicious local processes or concurrent filesystem tampering.

## Verification

```bash
PYTHONPATH=src python -m pytest -q tests/test_local_workspace.py tests/test_local_web.py
```

`tests/test_local_ui.cjs` is a distinct offline DOM verification using an already installed jsdom,
with fetch mocked to the local API contract and all resource loading denied. It takes a rendered
HTML filename and a snapshot fixture filename. It exercises import, evidence/caps, verdicts,
reload, repeated clicks, stale conflicts, retained inputs and hostile text. It is not a real
browser rendering, accessibility or end-to-end network check. Do not install packages or invoke
a registry-backed test command under the strict zero-service-use mandate.

With jsdom already installed in this checkout, run this new check directly:

```bash
PYTHONPATH=src python scripts/prepare_local_ui_check.py /tmp/finder-local-ui-check
node tests/test_local_ui.cjs /tmp/finder-local-ui-check/index.html /tmp/finder-local-ui-check/snapshot.json
```

The helper builds only invented inputs under the offline I/O guard. The Node check blocks native
connections, DNS and resource dispatch, and makes no registry requests.

For the actual service integration check, with the same existing dependencies:

```bash
PYTHONPATH=src python scripts/check_local_review_flow.py
```

This starts only a guarded loopback server on an allocated local port. jsdom imports synthetic
cases through the UI, saves a verdict, reloads, exports, and checks it after server restart.
The harness also reopens SQLite to verify persistence. The fetch bridge permits only that exact
loopback origin, with all other native connections, DNS and resource loading blocked. This is a
real UI-to-service integration check, but it does not verify actual browser rendering or layout.
