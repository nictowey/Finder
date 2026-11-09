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
watches use initial availability and count increases. There is no delete action:
pause retains the watch and history. Paused test watches are not re-enabled by
loading the page. A saved rule edit uses the existing canonical save behavior:
unsent old-revision events are revoked, claimed deliveries remain uncertain, and
the sample is reset for a fresh observation. A no-op leaves samples untouched.

## Existing-store boundary

`WatchBackend` receives the existing initialized `AggregatePriceAlerts` instance.
It reads the existing `discogs_price_rules` and `discogs_price_samples` tables and
uses `save_rule` with an optimistic revision for every actual mutation. There is
no second database, schema migration, provider operation, destination setting,
credential access or global enable/disable control. The canonical writer enforces
the optional 200-rule admission limit inside its SQLite transaction. Existing
larger stores remain readable and editable; they cannot add more through this UI.

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

## Validation

- Python: `PYTHONPATH=src:. python -m pytest tests/test_discogs_watch_manager.py tests/test_discogs_price_alerts.py`
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
