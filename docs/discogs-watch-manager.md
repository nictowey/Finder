# Private self-service watches

This is an implemented, offline-tested UI, narrow backend and default-off private
broker/host integration, **not a deployed management service**. Operator enablement,
production authorization review and a user-confirmed test are outstanding. The older Finder dashboard uses a different watch model and
must not be pointed at or migrated into this table.

## What the owner can do

The `/watches` interface adds exact Discogs release links or IDs with an item-price
limit and currency, edits a saved watch, pauses/resumes it, searches saved names,
and displays the last successful aggregate sample. Any-price availability watches
are supported. The form distinguishes an inclusive price ceiling from a delivered
price. Use a ceiling of 39.99 for a strict under-40 USD preference.

These are aggregate leads only. No individual-listing discovery, condition check,
US-seller filter, shipping quote, tax, payment or purchase is claimed. No catalog
lookup is performed. A display title is optional; a blank label uses “Discogs release [ID]”. New enabled watches can
cause the existing notification worker to deliver through its existing private
channel; the form says so. Saving does not send anything synchronously and cannot
start a globally paused monitor.

Edits preserve the saved initial-availability, price-drop and count-increase alert
flags. New price watches use initial availability and price drops; new any-price
watches use initial availability and count increases. Remove asks for confirmation
showing the title and release ID, then moves the retained watch to a separate
Removed view. Its settings, samples and notification history stay in the database.
Restore returns the watch **paused**; only a separate explicit Resume starts it.
Adding or editing a removed release directs the owner to restore it first and
keeps the draft. Paused test watches are not re-enabled by loading the page. A saved rule edit uses the existing canonical save behavior:
unsent old-revision events are revoked, claimed deliveries remain uncertain, and
the sample is reset for a fresh observation. A no-op leaves samples untouched.

## Existing-store boundary

`WatchBackend` receives the existing initialized `AggregatePriceAlerts` instance.
It reads the existing `discogs_price_rules` and `discogs_price_samples` tables and
uses `save_rule` for edits and `set_removed` for removal/restoration, each with an
optimistic revision inside the canonical writer. The additive removal schema is
provisioned explicitly by an operator, never from a request or read-only startup.
There is no second database, provider operation, destination setting, credential
access or global enable/disable control. The canonical writer enforces
the optional 200-rule admission limit inside its SQLite transaction. Existing
larger stores remain readable and editable; they cannot add more through this UI.
Removed watches still count toward this existing 200-retained-rule limit. Removal
does not free storage or erase historical watches.

Do not give the public-facing web UID direct access to the production database.
The optional IPC adapter runs in the existing DB-owning review broker and exposes
only watch snapshot and save, with authenticated peer identity, bounded requests,
fixed operation schemas and replay/revision checks. Both broker and web host gates
are default-off. The checksum-pinned Caddy template remains unchanged; watch routing is opt-in below. Existing authentication,
proxy identity stripping, request limits, safe logging, secret isolation and TLS
must be retained. No credentials or permissions are created by this implementation.

## HTTP integration contract

Construct `WatchApp(backend=approved_backend, origin=canonical_https_origin,
authenticate=verified_owner_callback)`. The callback must derive the sole authorized
owner from the existing verified private gateway, never from an untrusted header.

**Use a single shared WatchApp instance per origin.** Its bounded, expiring CSRF
store is in process memory; do not run independent worker processes behind a
round-robin proxy. Restart expires forms safely and requires Refresh. Any later
multi-process design needs an explicitly reviewed shared token store.

Mount only `/watches`, `/watches/state`, and `/watches/save` after authentication.
Apply a 4096-byte body limit and bounded header/request timeouts at the host. GETs
cannot mutate. POSTs require canonical Origin, same-origin browser metadata when
present, JSON content, exact fields and a one-use owner-bound 10-minute token.
Duplicate clicks and expired/stale forms cannot overwrite a newer rule. Refresh
keeps draft fields; after a revision conflict, load the latest saved watch and
reapply reviewed changes. The browser does not retry an uncertain POST. If the
response is lost, Refresh first to inspect the saved state. Successful POSTs are
reported as saved even if the subsequent independent state refresh fails.

The UI uses text nodes for saved data, nonce-constrained inline assets, no external
resources, no browser storage, and no private state in URLs. Hosting must retain
TLS, no-store/no-referrer policy and private access. No deployment or real-user
end-to-end claim follows from isolated tests.

## Reversible removal schema and rollout

The additive v1 schema consists of `discogs_price_removed(release_id, removed)` and
three narrowly scoped INSERT/UPDATE/DELETE guards on `discogs_price_rules`. Exact
stored definitions are checked on startup and before lifecycle writes. Partial or
changed schema fails closed; the initializer does not repair it. No rule model
field is added, so a running previous aggregate worker still reads the saved rule
as ordinary `enabled:false` JSON. Guards block older writers from changing,
re-enabling, deleting, UPSERTing or replacing a removed row, including with
`recursive_triggers` off. Unrelated release IDs remain writable.

For an authorized operator rollout only:

1. Record all existing rule JSON/revisions, samples, retained notification history
   and control values. Preserve the database using the normal backup procedure.
2. Run `python -m finder.discogs_watch_removals --database /absolute/existing/rules.db`
   as the existing DB-owning identity. This validated, idempotent transaction adds
   only the table and guards, with no existing row changes. The normal aggregate
   initializer also provisions this schema; do not replace or restart a private
   aggregate runtime merely to do this migration.
3. Compare all existing rows byte-for-byte/logically, then restart the reviewed
   removal-capable broker and web host. `for_existing_store` and snapshots never
   provision schema; they fail closed if it is missing. Keep the current socket,
   authentication, private runtime pacing, Caddy routes and permission settings.
4. Test only an explicitly authorized synthetic watch. Never remove a real watch
   merely to verify deployment. An offline pass is not a live verification claim.

Removal increments the rule revision even if already paused, sets only its enabled
setting to false, and marks it removed in the same `BEGIN IMMEDIATE` transaction.
Pending/retry-wait events are revoked, claimed sends become uncertain, and active
sample claims are cleared. Samples and extrema are retained, with continuous
comparison reset. An observation started under the old revision is discarded.
The existing immediate-before-send guard rejects invalidated leases; an alert
already sent to a provider cannot be recalled and remains subject to the existing
uncertain-outcome policy. Accepted history is never changed by removal.

Restore atomically clears the marker and increments revision again, preserving
settings and samples while remaining disabled. Both actions retain the normal
one-use CSRF, durable IPC replay ledger and uncertain-response/no-retry rules.
Refresh is mandatory after uncertainty before deciding whether another action is
needed. Repeated actions or stale revisions cannot silently re-enable a watch.

Use the existing `/watches/save` endpoint and `watch_save` IPC operation. The normal
save fields are unchanged. Lifecycle POSTs have exactly `action` (`remove` or
`restore`), `release` (decimal ID string), `revision` and `csrf`; IPC strips only
`csrf`. Success remains `{"saved":true}` and is followed by a separate state read.
Every snapshot row contains a boolean `removed`, including retained removed rows.
No extra endpoint, privilege, public response data or provider capability is added.

Rollback must retain the removal-capable broker/writer and additive schema once
any removal marker exists. A UI-only rollback may hide removal controls, but must
not switch to an old writer or allow removed rows to be edited. Database guards
are a safety backstop, not a recovery UI. Do not drop the schema, permanently delete
retained watches, restore an old database over new history, or broadly replace the
private aggregate runtime. Existing already-running aggregate polling/sending
code is compatible; it does not need a restart for this feature.

## Validation

- Python: `PYTHONPATH=src:. python -m pytest tests/test_discogs_watch_manager.py tests/test_discogs_watch_removals.py tests/test_discogs_price_alerts.py`
- UI interaction: `node --import tsx --test functions/discogs-watch-ui.test.ts`
- Full repository checks: Python regression suite, `npm test`, `npm run typecheck`,
  Ruff lint and format checks. Report environment-limited checks separately.
- Tests use synthetic local databases. A rollout must separately preserve and
  compare the real saved rules, then use a clearly labeled temporary test watch
  with owner confirmation before declaring the live interface verified.


## Optional private host integration

The existing broker and web-host CLIs both accept `--enable-watch-management`.
Without the flag, the broker rejects watch operations and the web host does not
mount the three watch paths. Both flags and the explicitly described Caddy route
change below are needed. The existing `/listing-review` path, identity replacement,
pre-auth rate gate, socket directory permissions and secret isolation remain.
No additional socket, credential or database access for the web UID is needed.

The DB-owning broker uses `AggregatePriceAlerts.for_existing_store` for read-only
schema preflight, not the initializing constructor. It wraps that service in
`WatchBackend`; startup neither migrates nor seeds the watch list. The web host
receives only `WatchBrokerClient`, never the database path. Its single shared
WatchApp instance supports the existing two Waitress threads; do not add worker
processes. The same verified PrivateBoundary callback authenticates both UIs.

Watch IPC frames are bounded to 4096 request bytes and 1 MiB response bytes.
Snapshot projection includes only rule fields and bounded sample metadata;
stores above 1000 rules fail closed rather than silently truncating. Only the
existing allowed web UID can call the broker, and the client verifies the broker
UID with Linux SO_PEERCRED before sending. Save requests have exact fields and a
new opaque request ID. The existing durable, bounded 4096-entry receipt ledger
consumes IDs before any save, including conflicts/failures. Once full it requires
operator review; it is never pruned automatically. Replayed IDs and stale revisions
cannot mutate. CSRF tokens never leave the web process. Lost responses are never
retried; refresh the saved state before deciding what to do.

For an authorized rollout, preserve the existing database and compare all saved
rules and global settings before and after enabling these gates. Review both
systemd templates, use the actual existing identities/socket paths, replace the
example HTTPS origin privately, and retain all existing secret-denying sandbox
settings. Change the existing protected matcher from `/listing-review` to the exact allowlist
`/listing-review /watches /watches/state /watches/save`, and its request body limit
to 4096; review requests retain their own 2048-byte app limit. Do not change the
checksum-pinned source template or its provenance gate. Apply the reviewed route
change only to the staged private live configuration.
Do not expose the private gate or broaden the path matcher. Keep global Caddy
header/read/write timeouts and safe logging policy. No deployment is performed by
these templates. Rollback removes both flags and the optional proxy paths; saved
rules/history stay intact. Real-host access isolation, TLS/authentication and an
owner-confirmed test remain required before declaring live operation verified.

Additional offline integration checks:
`PYTHONPATH=src:. python -m pytest tests/test_discogs_watch_broker.py tests/test_discogs_review_broker.py tests/test_discogs_phone_review_server.py`.

## Notification counts

Each card shows retained provider-accepted aggregate-watch events for that release,
across saved prices, plus accepted events whose rule revision matches the current
watch settings. Counts are events, not send attempts. Queued/retry-wait, in-flight
and uncertain outcomes are shown separately and are never added to accepted totals.
Failed, revoked and expired events do not count. No notification is inferred from
an aggregate sample, price match, or provider-acceptance record on another release.

Provider acceptance is not confirmation that a phone received or displayed an
alert. Historical phone delivery receipts are not available. These figures cover
retained database history, not an asserted lifetime count. Editing or pausing a
watch increments its revision, so the current-settings count restarts while the
retained accepted total stays. Earlier events cannot reliably be attributed to the
current price merely by their observed minimum, since earlier saved caps are not
stored on those records. No historical price-setting counts are invented.


## Reading saved check results

Each card exposes the last successful check time and latest recorded result without
opening notification details. A failed or unknown check does not replace the prior
successful price/count. Those values are labeled **last observed**, not current
availability. Missing success history and an unavailable result are explicit; a
saved settings change resets the sample, so missing history applies to the current
settings. Dates use the browser's time zone.

The global banner describes the saved monitor enable setting, not whether the
worker is currently running or healthy. Watch enable/pause state is separate from
check history, and disabled global monitoring is called out on enabled watches.
No stale threshold, next-check countdown, polling interval or automatic page
refresh is inferred. Refresh reads stored results only; it does not trigger a
provider check. Aggregate-only, grade/US-seller and delivered-price limitations
remain visible. Notification-service acceptance still is not phone delivery.

## Touch zoom in the watch workspace

The workspace requests scrolling without pinch zoom using `touch-action: pan-x pan-y`
on its root and body, including without JavaScript. There is no zoom checkbox,
gesture handler or saved zoom preference. Form text remains at least 16px, and the
viewport has no maximum/minimum scale or user-scalable restriction.
There are no gesture-cancellation listeners or browser/OS accessibility changes.
Browsers may honor accessibility overrides or differ in their touch implementation.

The offline browser harness checks computed styles in Chromium and WebKit, plus
Chromium two-contact touch input with a temporary test-only CSS zoom override as
a positive control (removed afterward), plus normal one-finger scrolling and navigation taps. These tests must run in CI; computed styles or emulated
Chromium gestures do not prove behavior on physical iPhone Safari or an installed
home-screen app. Physical-device validation remains necessary before claiming it.

## Per-watch change-alert preferences

Retained watches expose explicit Save/Reset controls for aggregate price drops and
aggregate count increases. Both operate within the watch's existing eligibility
and price setting. Count increases do not prove a new individual listing. These
controls do not mute availability-entry alerts; pause the watch to stop new checks
and alerts. The initial-eligible-check flag is preserved, not exposed as a misleading
availability on/off switch. New-watch defaults are unchanged.

The exact `preferences` operation uses the existing owner, CSRF, single-use token
and bounded IPC path. It changes only the two explicit boolean flags, requires the
saved positive revision, rejects removed watches, and delegates to
`configure_change_alerts`. Identical settings do not advance revision. A changed
preference preserves price/threshold, samples, history and polling/backoff; the next
fresh observation establishes the change baseline. Queued old-revision alerts are
revoked and already claimed sends become uncertain and may still arrive.

Preference drafts survive navigation, filtering and refresh with their original
revision, so refresh cannot silently turn a stale draft into an overwrite. Reset
uses the latest loaded settings. Writes lock on failure or uncertainty and are not
automatically retried. Saving preferences does not submit or clear the watch-edit
form. Removed watches have no preference controls.
