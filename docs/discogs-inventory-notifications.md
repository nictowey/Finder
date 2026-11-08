# Seeded inventory manual-review notification bridge

This separate bridge is default-off. It does not poll, refresh details, initialize the API
budget, create credentials, install a scheduler, change aggregate rules or place orders.
Implementation is not deployment or activation authorization. Use only the existing private
notification configuration; public ntfy topics are rejected.

Provision explicitly, then separately opt in to one bounded delivery attempt:

```sh
PYTHONPATH=src python -m finder.discogs_inventory_notifications \
  --database /private/existing-rules.db --config /private/inventory.json --init-state

PYTHONPATH=src python -m finder.discogs_inventory_notifications \
  --database /private/existing-rules.db --config /private/inventory.json \
  --private-notify-config /private/existing-notify.json --deliver-one
```

Without a mode the command validates config and reports disabled. No secret or destination
is printed. Provisioning adds only `discogs_inventory_notification_outbox`; repeated
provisioning preserves all terminal records. No background delivery is installed.

## Eligibility and copy

Only newly observed results after a silent ordinary baseline are eligible. Snapshots,
blocked candidates, unfinished/denied sources, changed configuration, stopped aggregate
control, wrong identities, failed grades/price/status checks, future timestamps and detail
evidence at least 120 seconds old cannot send. The six-hour source retention is not send
freshness. The claim and immediate pre-dispatch check both revalidate source state. Stale
results require a separately authorized observer detail refresh; this bridge never makes a
Discogs request. A 90-second shared pacing wait may cause later candidates to expire rather
than sending stale information.

The message identifies the listing and exact release by canonical numeric identifiers and
links directly to `https://www.discogs.com/sell/item/{listing_id}`. Fixed copy describes
one-seller coverage, newly observed rather than newly created listings, timestamped reported
item criteria, uncertain shipping/tax/fees/physical pressing/full contents, and manual review.
It includes Discogs attribution. No provider comments or arbitrary URLs enter the payload.
The source's `eligible_for_notification=false` and `purchase_qualified=false` are unchanged:
this bridge is a distinct manual-review notification, never purchase qualification.

## Delivery and recovery

Every attempt uses the existing database's notification lock, aggregate daily send cap,
90-second pacing, private credential loader and ntfy transport. Reservation commits before
HTTP. No SQL write transaction spans network activity. The opaque destination, config hash,
control revision and observation identity are fenced. A token/config change never silently
rebinds or replays a consumed event.

The deliberately narrow policy makes **every attempted event terminal**, including known
rejection. No automatic retries are implemented. Accepted is provider acceptance, not proof
of phone delivery. Timeouts, interruption and crashes quarantine the event as uncertain.
Shared Retry-After and unparseable provider manual-attention holds remain effective for
aggregate, health and inventory publishers. Terminal keys are retained without detailed
provider payloads; provisioning cannot erase them.

Before activation, perform a coordinated publisher cutover: every aggregate, health, phone
proof and inventory process must use the updated shared `recover_abandoned` implementation
and the same canonical database path. Mixed old/new workers are unsupported. Recovery from
any updated publisher recognizes abandoned inventory reservations, quarantines them and
imposes the shared recovery hold. Do not activate while any legacy publisher remains.

Offline tests use synthetic identities and MockTransport. They are not a live phone or
provider-availability gate. No live send, deployment, credentials or scheduler change is
part of this artifact.

## Offline verification, November 8, 2026

- Bridge suite: 34 passed. Focused inventory/private-ntfy/health/phone/aggregate regression
  suite: 354 passed. Independent review: no blocking findings, 225 regressions and seven
  additional adversarial probes passed.
- Full Python suite: 4,826 passed, one skipped, 41 subtests passed; 18 pre-existing UNIX
  socket tests failed with sandbox `PermissionError: Operation not permitted`. This is not
  a full-suite pass or a live integration gate. No permission workaround was attempted.
- Repository Ruff lint and 228-file format check passed. npm tests: 110 passed. TypeScript
  typecheck and diff whitespace check passed. Publication and deployment remain separate.

## Explicit TEST proof from fresh snapshot evidence

A separate operator-selected proof mode can exercise the same matcher checks, durable
outbox, budget, lock and private sender using an existing **snapshot** result. It does not
convert that result into a newly observed event or require/establish a discovery baseline.
The snapshot, seen IDs, pending queue and aggregate watches remain untouched.

```sh
PYTHONPATH=src python -m finder.discogs_inventory_notifications \
  --database /private/existing-rules.db --config /private/inventory.json \
  --private-notify-config /private/existing-notify.json --deliver-one \
  --test-snapshot-listing 100 --test-key synthetic-proof-01
```

The listing and proof key above are synthetic. Both options are required together. This
mode remains explicit, private-only and default-off. It selects only that exact configured
snapshot listing; newly observed records cannot satisfy a snapshot proof. The canonical
notification title starts `TEST:` and its body states `TEST ONLY: Existing snapshot listing;
this is not a newly observed listing alert.` There is no option to remove those labels.

All ordinary identity, criteria, source-stop and 120-second detail-freshness checks still
apply. A retained old snapshot is insufficient: separately perform an authorized fresh
snapshot detail observation before attempting the proof. This sender never refreshes it.
A shared pacing/cooldown hold can prevent delivery; it must not be bypassed for a test.

Proof reservations use a separate `test:` delivery-key namespace. The original source event
key and snapshot origin remain intact. A stable proof key is bound to the configuration and
consumed once across restarts, even if its snapshot is later refreshed. Never change a proof
key to replay an uncertain result. Provider acceptance still does not confirm phone receipt.
The shared abandoned-send recovery also quarantines proof reservations. Tests consume the
same daily allowance and pacing as ordinary notifications. No alternate table, credentials,
provider stack, production baseline or automatic test scheduler is created.

TEST extension verification: 44 proof/bridge tests and 364 focused regressions passed.
Independent review found no blockers; 202 focused tests and five extra replay/selection
probes passed. Full Python: 4,836 passed, one skipped, 41 subtests passed, with the same 18
sandbox socket permission failures (not a full-suite pass). Ruff, 229-file format check,
npm's 110 tests, TypeScript and whitespace checks passed. No live TEST was sent.
