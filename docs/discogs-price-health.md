# Read-only aggregate monitor health

`finder-price-health` (or `python -m finder.discogs_price_health`) inspects the
existing aggregate monitor database without starting the worker, polling Discogs,
sending a notification, changing rules/controls, reconciling delivery states, or
creating/migrating a database. This is operational evidence, not listing discovery,
a purchase capability, a new web endpoint, or a device-delivery receipt.

## Run against the existing configuration

Use the same operating-system account and existing file paths as the worker. Do
not put configuration contents, topics, tokens, app keys or secrets on the command
line. The arguments below are placeholders, not automatically detected paths:

```sh
python -m finder.discogs_price_health \
  --database /path/to/existing/rules.db \
  --poll-seconds 300 \
  --discogs-app-file /path/to/existing/discogs-app.json \
  --ntfy-config-file /path/to/existing/ntfy.json
```

Add `--format json` for automation. Default output is readable text. The optional
legacy `--topic-file` and `--ntfy-config-file` arguments are mutually exclusive.
The health command never creates/repairs these files or their permissions.

Exit status:

- **0**: Stored evidence is healthy at the snapshot time, with valid supplied
  configuration. This does not prove process liveness, connectivity, or phone receipt.
- **1**: Attention, no saved rules, or monitoring disabled. Inspect `status`,
  `issues`, per-rule findings, and the queue details. Disabled is never healthy.
- **2**: Evidence unavailable, invalid arguments accepted by the report API, or
  missing/foreign/incompatible/corrupt/locked database. No migration or recovery is
  attempted. CLI syntax errors also use exit 2.

Without supplied configuration files, their status is `not_checked`, not a claim
that configuration is absent or delivery is disabled. This makes the overall
result `attention` for an otherwise enabled, healthy monitor. `unavailable` means
an explicitly supplied configuration could not pass the worker's existing local
validation. `valid` means only that local validation passed. No server reachability,
authentication, subscription, process arguments or push permission is tested.

## Evidence and interpretation

- Every saved rule is reported by release ID, revision, enabled flag and price
  mode. Titles, thresholds, payloads, event keys, opaque destination hashes,
  claim tokens, paths and configuration contents are not printed.
- `last_success_at`, `last_success_age_seconds` and `freshness` use the persisted
  successful observation, not the last attempted poll. `sample_status` and
  `consecutive_failed_polls` preserve failures even if an older good sample is
  still fresh. A successful poll resets the worker's failure count. The database
  does not persist a distinct failed-at timestamp or failure reason, so neither is
  invented. Unknown/error samples are not zero stock.
- `fresh` means less than the existing 1200-second delivery freshness bound.
  Stale begins at exactly 1200 seconds. Future timestamps are flagged. This is
  local observation age, not Discogs' inventory timestamp or proof of live stock.
- `--poll-seconds` is the operator's expected interval, 300–86400 seconds (default
  600). It does not change polling or infer the running worker's interval. A rule
  is `poll_overdue` when at least one expected interval past its stored due time,
  unless its current poll claim is still live. This grace avoids declaring normal
  three-second request spacing a failure. Error backoff/provider cooldowns remain
  visible and are never shortened. An interval greater than the freshness bound
  can legitimately produce stale evidence; the command does not relax safety.
- Stored global alert enablement, control revision, request/send cooldowns and
  UTC daily send-attempt cap are shown. The count includes retries, not confirmed
  messages. If present, the separate `cloud_control.killed` flag is shown; it does
  not control this aggregate worker. A missing cloud control is `null`, not false.
  This command never changes either control.
- `stored_states` are the exact persisted queue counts. `effective_states` project
  what reconciliation would do now: expired queued observations become `expired`,
  and expired in-flight leases become `uncertain`. **The rows are not updated.**
  Never use this report to reset/replay uncertain work.
- Waiting counts, oldest waiting observation age, next retry timestamp, per-rule
  states, and blocked-reason counts explain backlog. Reasons may overlap.
  `eligible_now` applies current control/rule/sample/freshness/retry/attempt-limit
  and destination-binding checks. It is a snapshot, not a reservation or promise
  of sending. A notification destination mismatch is visible without exposing the
  destination. Changing a file does not rebind old events.
- Waiting work at least one expected poll interval old produces `queue_backlog`.
  Retry-wait, uncertain, failed and expired counts require attention. Failed,
  expired and uncertain counts include history: they do not necessarily describe
  a current outage, and this command does not auto-dismiss them.
- `provider_accepted_total` counts stored provider acceptance, not notifications
  observed on the phone. `phone_delivery` is always `unverified`, and
  `provider_acceptance_is_delivery` is always false. Confirm actual phone delivery
  separately with the owner. A successful earlier phone check would not prove
  later messages arrived.

JSON has `schema_version: 1`, Unix-second timestamps, fixed status/issue codes,
and no raw exception text. All computation uses one timestamp and one consistent
SQLite read transaction. The database is opened using URI `mode=ro` with
`query_only=ON` and `trusted_schema=OFF`, never through the mutating store or
`AggregatePriceAlerts.status()`. Read-only mode still uses SQLite's normal locking
and WAL shared-memory coordination; it does not claim an immutable byte-for-byte
copy of a database that another process is actively writing. Committed WAL data
must be visible, so `immutable=1` is deliberately not used. Database/journal data
and application state are not written by this command.

## Verification before a live update

The deterministic tests use temporary databases, synthetic protected configuration
and intercepted HTTP. They exercise the actual app-authenticated worker and private
sender for 19 rules, then inspect health evidence; no external request or real
notification is sent. Further scenarios cover empty/disabled/never-polled states,
zero stock, failed/unknown reads and recovery, 401/403 safety stop, stale boundary,
retry/acceptance, active/expired leases, backlog, daily reset/cap, destination
mismatch, malformed data, redaction, invalid protected files, read-only SQL, and
visibility of committed WAL data. Database data hashes and content are compared
before/after health checks, including expiry projections. CLI JSON/text and exit
codes are exercised, as is the module entry point.

```sh
python -m pytest -q tests/test_discogs_price_health.py
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
npm test
npm run typecheck
```

Before copying into a live deployment, run the health CLI with the deployed code
and its existing database/configuration in a disposable staging directory, compare
its report to an independent read-only database snapshot, and verify source data
and configuration are unchanged. That live read is separate evidence; passing
mocked tests does not establish production access or phone delivery. No service
restart, rule edits, notification test, scheduler installation or deployment is
performed by this command.

When the optional aggregate-change milestone is installed, health also reads the
additive `discogs_price_changes` table when present. Waiting change events must
pass the worker's shared `change_supported` predicate in addition to the original
queue gates. A recovered count or price no longer supporting the queued change
is `change_not_supported`; incompatible or malformed change evidence fails closed.
Older databases need neither the new table nor the helper module. This inspection
still makes no changes to extrema, episodes, metadata or outbox rows.

## Optional scheduled failure notifications

The separate [health watcher](discogs-health-watcher.md) owns durable debounced
incidents and a rate-gated health outbox. It defaults off; this diagnostic command
remains read-only. Historical queue warnings are baselined rather than repeatedly
notified, and all health sends share the aggregate notification pacing/cap.

A provider deadline that cannot be represented safely can place the shared
notification budget in a persistent manual-attention hold. The read-only report
shows `notification_manual_attention:true`, `send_cooldown_seconds:null`, and
`provider_manual_attention`; it never emits a nonfinite JSON number or resets the
hold. Polling remains separately enabled. See the watcher documentation for
provider deadline handling and downgrade-safe recovery requirements.
