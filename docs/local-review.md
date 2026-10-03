# Local development review

This isolated development adapter uses Finder's existing matcher and owner-price policy with
synthetic or user-authored cases. It is not a market-ready replacement for autonomous remote
Finder. No providers, catalog requests, Neon, notifications, hosted deployment, owner-data
migration or credential imports are involved. Optional authored pressing profiles let the existing
policy compare invented alternatives. Coverage is always incomplete. A possible pressing means
support among the supplied profiles, not a verified market or physical identity. Without supplied
alternatives, matching claims remain unclear, as in observation schema v1.

## Start and return

Use the project's existing Python environment, in a clean shell without exported live settings:

```bash
PYTHONPATH=src python -m finder.local_web --database finder-local-review.sqlite3 --init
```

After an editable installation, `finder-local` invokes the same entrypoint. Initialization
requires a new filename and an existing parent directory. It never overwrites a database.
Open the exact printed `http://127.0.0.1:8765` address in your browser. `--port 8766` chooses
another local port; `--port 0` prints the chosen available port. No browser launches automatically.

1. Fill **Your local pressing profile** and select **Save local profile**. This saves no
   candidate or dummy listing. Later edits to target facts, owner caps, destination, conditions,
   auction settings and signs are saved through this same form. Blank formats/colors/identifiers
   stay unknown; color does not automatically add a required sign.
2. Original v1 fields are the default for a new profile. Explicitly enable **detailed profile
   fields and authored comparisons (v2)** to add country/year/edition/cover/package facts. Save
   the profile to apply this upgrade. Existing v1 observations and target history are unchanged;
   downgrading a saved v2 profile is not supported.
3. Optionally add comparison profiles, edit any saved comparison, or remove one. IDs and facts
   must be distinct, and their artist/album must match the target. Comparisons always remain
   incomplete. The profile form also lets you deliberately add required signs, supporting signs
   and common-version anti-signs; imported supported flags and settings are retained.
4. Fill **Candidate observation** and save. This sends only the candidate plus optimistic
   state guards; it cannot resend or revert old target settings. Price and shipping currencies
   are independent. Blank price/shipping/details/formats stay unknown, and price kind starts
   as **unknown**. Supply the actual observation time, and the details time only if known.
5. To revisit a saved case, choose **Record another observation** on its review card. This
   copies its saved claims and original source times into one draft. Review carried-forward
   claims and supply a genuinely new observation time. Changing data at an already recorded
   time is rejected with guidance; correcting the one pending draft then saving a real later
   observation succeeds without clearing JSON. This is not historical correction or editing.
6. Read the existing matcher evidence, unresolved checks, and your two price caps. The policy
   simulation is informational; it never sends an alert. Save **Mine**, **Other**, or **Unsure**.
   These judgments are independent of purchases and retain their original provenance.
7. **Advanced: observation JSON import and export** remains available for complete bundles.
   It is optional for normal use. **Copy saved profile to JSON** includes no observations.
   The synthetic example has fixed, historical, intentionally stale fixture times; it never
   fills the current clock. **Export review record** is archival JSON, not a restorable backup.
8. Stop with Ctrl+C. Restart with the same database and omit `--init` to recover saved profiles,
   settings, observations and decisions. Saved profile fields hydrate when the page reloads.

Clean close/reopen is verified. Reopening refuses any existing SQLite journal, WAL or SHM
sidecar, including after a crash. Preserve the workspace and all sidecars for recovery; do not
delete them to bypass the refusal. Automatic crash recovery is outside this first adapter.

Unsubmitted drafts are held only in page memory. **Refresh saved review** retains dirty profile,
comparison, candidate and JSON drafts; validation errors, failed requests and stale-state conflicts
also retain inputs. Inputs edited while a request is pending are not replaced by its response.
Repeated clicks send one pending mutation. The page requests the browser's ordinary unsaved-change
warning before leaving, but closing/reloading after dismissing that warning discards unsaved work;
there is no browser storage or draft-restoration claim.

Profile and comparison writes require the revision on which editing began. A conflicting write
refreshes the saved review without replaying the mutation or overwriting draft fields. Compare
those drafts with the saved facts, then use **Reload saved profile (discard profile edits)** or
close/reopen the comparison before editing again. Candidate writes also require the saved case's
stable current-observation token. After reviewing a conflict, **Keep draft and use refreshed saved
state** explicitly rebases its guards without changing claims or source timestamps. Neither stale
responses nor candidate saves can silently revert the current profile. A timeout is uncertain:
refresh to inspect saved state before retrying. Earlier observations and first judgments survive.

## Observation bundle

The JSON editor and file importer accept v1 and v2. Existing v1 inputs and stored history retain
their original meaning; reading never adds new profile facts or upgrades the version. V1 still
rejects v2-only fields. A v2 example is:

```json
{
  "schema_version": 2,
  "source": "synthetic",
  "target": {
    "artist": "Example Ensemble",
    "album": "Offline Horizons",
    "colors": ["Blue"],
    "formats": ["LP"],
    "country": "US",
    "release_year": 2024,
    "editions": ["Limited Edition"],
    "required_components": ["signed_insert"]
  },
  "alternatives": [{
    "id": "invented-black",
    "artist": "Example Ensemble",
    "album": "Offline Horizons",
    "colors": ["Black"],
    "formats": ["LP"],
    "country": "US",
    "release_year": 2023
  }],
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
    "country": "US",
    "release_year": 2024,
    "editions": ["Limited Edition"],
    "current_price": "20.00",
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
arrays. V2 adds country (plain name or code), release_year (integer 1900–2099), editions,
required_components and cover_edition. Release country is independent of settings.country and delivery_country,
which describe the destination. Country values are passed unchanged to the existing normalizer;
the importer does not infer country aliases.

V2 alternatives is absent/null, an empty list, or at most 20 full target-shaped profiles, each
with a local id. Absent/null/empty all mean **comparison not checked**, not a successful search
with zero competitors. Supplied profiles must have the same normalized artist and album as the
target. IDs are unique ignoring case; duplicate authored facts within the alternative list are
rejected, including different list order or letter case. A single alternative may share all the
target's facts, deliberately representing unresolved competing evidence. These are local authored
identities, never fabricated Discogs release IDs. V2 profiles share one local work identity derived
from their normalized artist and album; that represents the authored grouping, not a verified
catalog relationship. Supplied comparisons are unsupported when an artist or album has no words
recognized by the existing normalizer (for example, punctuation-only or some non-Latin inputs).
Empty normalized identities never establish a common work. V1 and inputs without alternatives
retain their existing acceptance; no transliteration or new matching rules are added.
Repeated equivalent values in a profile cannot make duplicate facts count
as a distinct profile. Alternative order has no meaning and is
canonicalized by ID. There is no complete-catalog switch.

The only package component accepted is `signed_insert`. It maps to the existing explicit
`All Media` / `Signed Insert` format component, so an unambiguous seller-title denial can invoke
the existing conflict guard. It represents a target package component; it does not verify that
the seller included it or invent a positive seller claim. It does not authenticate signatures,
verify package completeness, or support arbitrary components. A barcode/color-supported case
with no insert mention can still be possible_pressing under current policy. That result does
not establish insert inclusion. Missing mention, positive signing words and ambiguous wording
do not establish completeness. Putting `Signed Insert` into the old formats descriptions alone
retains its old meaning and does not assert this requirement.

An optional cover_edition names one explicit alternative cover, such as `Alpha` or `River Scene`.
It maps to an `All Media` format component with text `<name> Alternative Cover`, allowing the
existing named-cover conflict rule to inspect the supplied family. Names must be 3–60 characters,
at most five normalized words, and recognized by the existing cover-name parser. Names containing
recognized disc colors, including mint, teal and cream, are explicitly unsupported here: the
current normalizer reads format text as color evidence too, so accepting these names could
manufacture a disc-color requirement. Names that the existing edition extractor interprets as
edition facts, such as `Promo`, `Unofficial Skyline` and `Test Pressing`, are likewise unsupported
as cover names. The adapter checks the isolated generated cover component so it cannot manufacture
edition evidence; explicitly authored editions remain valid in the editions field alongside a
supported cover name. Do not rename an unsupported cover just to pass validation.

Cover names are not arbitrary seller keywords. A target cover's name alone does not promote a
case. Missing, ambiguous, company/label/artist credits, accessories and photograph claims retain
the existing guards. Exact target identifiers also retain the existing contradictory-evidence
behavior. There is no new seller-text parser or physical cover verification.

Target editions become existing format descriptions; the existing normalizer recognizes terms
such as edition, reissue, repress, remaster, promo, unofficial, test pressing and numbered. Seller
editions become the existing Edition item specific. The UI shows both supplied facts and the
scorer's available comparisons. Country/year disagreements do not automatically reject a case
under the current review policy; edition handling likewise remains the existing policy. These
new inputs do not add classifier rules or change the policy version.

Settings accept the two optional positive price caps, currency, paired country/postal_code,
condition_ids, alert_mode (`review_leads` or `strict`), auction_alert_minutes, tells and anti_tells.
The unclear cap cannot exceed the likely-match cap. Each tell has kind, value and required.

Each case requires id, title and a timezone-aware observed_at. Optional fields are
 details_observed_at, artist, album, colors, catalog_numbers, barcodes, formats, current_price,
 currency, shipping_cost, shipping_currency, price_kind (`fixed_price`, `current_bid`, `unknown`),
 condition_id, listing_ends_at, delivery_country and delivery_postal_code. Unknown price/shipping
is null, never zero. Shipping must have the same currency to form a subtotal; destination quotes
must explicitly match saved country and postal code for capped eligibility.
V2 cases also accept country, release_year and editions. Candidate form entry does not silently upgrade v1; explicitly enable detailed fields and
save the profile first. In advanced JSON, set schema_version to 2 explicitly.
When upgrading saved v1 inputs, first use `listings: []` to change only the target/profile
contract, then add a new case or a genuinely new observation. Re-sending old v1 observations
as v2 at their original timestamps conflicts with immutable observation history, because the
v2 canonical shape adds fields. Never invent a fresh observation time just to bypass a conflict.

All extra fields are rejected. IDs use 1–64 letters, digits, hyphens or underscores, starting
with a letter or digit. Inputs are limited to 256 KiB per request and 100 case identities in a
workspace. Whole batches validate before writing. Observations are additive; stale observations
cannot overwrite newer current inputs. Target/settings changes invalidate stale review saves.
No URLs, photos, provider payloads, owner data, or credentials belong in this format. Invalid or
duplicate profiles reject the whole batch before any target, observation or judgment write.
Changed profiles invalidate old verdict save tokens and retain the original judgment provenance.
Archival exports preserve v2 inputs and every old v1 target revision without conversion.

## Stateless assessment contract

`finder.local_workspace.validate_bundle(value, now=aware_datetime)` validates the same bounded
v1/v2 input contract without a database or provider calls. The returned model's
`model_dump(mode="json")` is its canonical input representation: defaults are explicit, money
uses two decimal places, timestamps use UTC and authored alternatives have canonical order.

`review_local_case(bundle, row, now=aware_datetime)` accepts that validated bundle and one
validated listing input. It returns id, converted listing, the existing assess_review result,
and comparison_evidence from the existing scorer. Expected answers and judgments are not inputs.
An evaluation supplies its clock explicitly; the local UI always supplies actual UTC. Workspace
and stateless reviews use the same conversions and always incomplete comparison policy.

Synthetic case agreement verifies implementation behavior only. It cannot establish actual
market accuracy, complete catalog coverage, physical pressing identity or human review time saved.

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
PYTHONPATH=src python -m pytest -q tests/test_local_workspace.py tests/test_local_profiles.py tests/test_local_web.py
```

`tests/test_local_ui.cjs` is a distinct offline DOM verification using an already installed jsdom,
with fetch mocked to the local API contract and all resource loading denied. It takes a rendered
HTML filename and a snapshot fixture filename. It exercises import, evidence/caps, verdicts,
reload, repeated clicks, stale conflicts, dirty and pending edits, retained rich-profile inputs,
v1 compatibility, comparison CRUD, same-time rejection/correction and hostile text. It is not a real
browser rendering, accessibility or end-to-end network check. Do not install packages or invoke
a registry-backed test command under the strict zero-service-use mandate.

With jsdom already installed in this checkout, run this new check directly:

```bash
PYTHONPATH=src python scripts/prepare_local_ui_check.py /tmp/finder-local-ui-check
node tests/test_local_ui.cjs /tmp/finder-local-ui-check/index.html /tmp/finder-local-ui-check/snapshot.json
```

Both commands use already installed dependencies; set NODE_PATH to their existing location
when the checkout does not contain node_modules. The helper builds only invented inputs under the offline I/O guard. The Node check blocks native
connections, DNS and resource dispatch, and makes no registry requests.

For the actual service integration check, with the same existing dependencies:

```bash
PYTHONPATH=src python scripts/check_local_review_flow.py
```

This starts only a guarded loopback server on an allocated local port. jsdom creates profiles and candidates through the JSON-free forms, exercises comparison CRUD,
changes a cap from 30 to 25, saves a verdict, rejects changed same-time data, then saves a genuine
later observation. It checks cross-tab conflicts, retained drafts, page reload, export, original
judgment preservation, and persistence after server restart.
The harness also reopens SQLite to verify persistence. The fetch bridge permits only that exact
loopback origin, with all other native connections, DNS and resource loading blocked. This is a
real UI-to-service integration check, but it does not verify actual browser rendering or layout.

The UI routes are bounded envelopes: `/api/profile` takes `profile` and `expected_revision`;
`/api/candidate` takes one `observation`, `expected_revision` and `expected_current` (null for a
new case). The server derives the current profile/settings for a candidate. Advanced UI imports
use `/api/import` with `bundle` and `expected_revision`; the legacy raw observation-bundle route
remains accepted. Duplicate keys are validated from the original JSON at every nesting level.
Observation-time collisions have an `observation_collision` response distinct from stale-state
conflicts. All routes retain the same exact Host, Origin, CSRF, body bounds and CSP protections.

Real-browser visual layout and accessibility QA remain unverified. These automated DOM and
loopback checks do not establish a polished browser experience or real-world matching accuracy.
