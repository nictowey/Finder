# Official API low item-price leads and optional ntfy delivery

This document retains the historical October 28 foundation and activation plan;
its historical “not activated” statements are not current deployment status.
Current code accepts operator-configured **120–86400-second** polling (600-second
default), with the [shared API guard](discogs-api-read-guard.md). See the
[private ntfy transport](#prepared-private-ntfy-transport-not-activation),
[reliability release gates](sniper-reliability-release-gates.md), and
[actionable formatting](#actionable-notification-formatting-and-offline-proof)
sections for subsequent behavior. The new formatting change itself is offline-only
until separately reviewed and deployed. No cart, order or payment is enabled.

## Meaning of a lead

The official public `GET /marketplace/stats/{release_id}?curr_abbr=USD` was verified
from the existing VPS on October 28, 2026. It reports an aggregate offer count and
lowest **item** price, not a listing ID, grade, seller or delivered quote. At
04:06 UTC, release28720177 reported17 offers and USD15 minimum; release27048621
reported0 and no minimum. These are dated observations, not current inventory.
No source inventory-as-of timestamp was supplied; API cache/update lag is unknown.

A fresh same-currency minimum at or below the explicit item-price prefilter creates
one `manual-review lead`. The first low observation alerts by default; set
`alert_initial_low:false` to establish it silently. One episode continues while
prices stay below the ceiling, even if count or price changes. An observed price
above the ceiling or a verified zero count with no price ends the episode. A later
low observation starts another. Missing price/currency, wrong currency, API errors
and stale/future/out-of-order observations never establish zero stock or re-arm it.
There is no automatic currency conversion.

The saved all-in and grade rules remain separate and unchanged. A USD10 item-price
prefilter can be a necessary check for a USD10 all-in ceiling; it is insufficient
for that buying rule. A cheap Poor copy can hide a new VG+ copy, and constant count
and minimum can hide replacement offers. Short-lived offers between polls can be
missed. No new-listing, instant-delivery or qualifying-price guarantee is made.

Notifications identify the public artist/record title and current minimum item price
or aggregate offer-count change in the visible title and first body line. The body
shows the exact saved inclusive item-price cap and currency, or **any price**. It
never reconstructs a round-dollar “under” threshold from a stored `.99` ceiling.
An inclusive USD 10.00 cap stays inclusive. These are item-only watch settings,
not a delivered-price or condition qualification.

Every notification states that the observation is aggregate-only, condition and
shipping are unknown, and the delivered total is unverified. It remains a
manual-review lead, not a qualified purchase, with “Data provided by Discogs”
attribution and the canonical release marketplace link. No individual listing,
seller, destination, account identity, topic, credential, event key or buying
permission is included in the message. Watch selection and saved thresholds can
be private interests; use only the approved notification destination.

## Code and persistence (historical foundation)

- `discogs_price_alerts.py`: strict independent `PriceLeadRule`; default-off alert
  control; official stats-only one-shot polling; durable episodes and price-aware
  outbox. Reuses the existing tagged `RulesStore`, public API adapter, opaque
  notification binding, request-spacing table and delivery retry constants.
- `discogs_ntfy.py`: default-off sender; fixed HTTPS JSON POST to `https://ntfy.sh/`.
  The topic is inside the JSON body, never in the request URL or application logs.
  It accepts only the canonical public message. No credentials, paid-plan fallback,
  arbitrary server, redirects, attachments, email or phone-call API exists.
- `discogs_price_worker.py`: explicit aggregate-only callable/CLI path. Polling and
  delivery are separate flags; neither starts merely by importing or constructing
  the worker. The default is one inert tick; `--run` is another explicit opt-in.

Additive tables are `discogs_price_control`, `discogs_price_rules`,
`discogs_price_samples` and `discogs_price_outbox`. The existing
`discogs_monitor_control.next_request` coordinates the minimum three-second API
spacing with the older API poller in the **same database**. Nominal per-release
poll interval defaults to 600 seconds. Explicit `--poll-seconds 300` selects five
minutes; current code accepts integer intervals from 120 through 86400 seconds. The option
alone never enables polling, delivery or stored alert controls. Claims and due times
are durable: changing the interval on restart does not shorten an existing due time,
claim or provider cooldown; it applies when the next read is reserved. Failed-read
backoff is never shorter than the selected interval. Use one worker per database
with one consistent interval. Failed reads back
off,429 honors a bounded Retry-After, and HTTP401/403 durably stops the alert
control until a separately approved explicit restart. There is no same-tick retry or browser
fallback. The last good sample/episode is retained through failures. A changed
rule/control discards an in-flight sample. No transaction spans network work.

Delivery revalidates rule revision, enabled state, episode, latest observed price,
freshness and destination binding immediately before dispatch. A queued message's
minimum tracks newer successful observations until claimed. Stale queued messages
expire after1200seconds; they are not resurrected within the same episode. API
errors/unknown samples block delivery until a successful current sample. One
claimed event is bound to one SHA-256 opaque destination reference; changing the
topic never reroutes that event. Rule edits revoke queued events and quarantine
claimed ones. Existing buying targets, global buying stop, browser leases and
transaction ledgers are never released or edited. The **alert control is separate**
and must receive its own activation approval.

At most one delivery is claimed per90seconds and200 claims per UTC day, including
retries, persistently across restarts. These are conservative local limits below
the reviewed anonymous250/day allowance, not a promise that ntfy will accept them
(other users on an IP and provider limits may reduce availability). No paid tier is
selected. Provider acceptance is never reported as phone delivery.

Only known non-acceptance retries: connect/pool failures before a request is sent,
or HTTP429, with at least15minutes backoff, bounded Retry-After and at most5
attempts. Other4xx/redirect responses fail without following them.5xx, read/write
failures and crash/expired in-flight leases become **uncertain** and never retry
automatically. There is no verified ntfy idempotency facility here. A changing rule,
price, pause or signal can race a request already sent; retain uncertainty rather
than promise exactly-once delivery. Reconciliation of uncertain messages is manual;
never reset their state to get a retry. A failed/expired/uncertain message does not
create another lead in the same episode.

## ntfy privacy and owner inputs (historical public-topic setup)

The owner selected free ntfy after the public-topic limitation was explained.
Anonymous topics are not access-controlled accounts: anyone knowing the name can
read or publish. ntfy can read/cache content and log topic names/IPs. Use a random,
non-guessable24–64-character topic, not a personal name. Never paste it in chat,
commit it, put it in argv/a URL, log request bodies, enable raw HTTP tracing, or
include it in an exception report. The sender intentionally publishes to the root
JSON endpoint so ordinary HTTP URL logging does not reveal the topic. Only the
hashed reference enters the database. A public record title is still a user
interest disclosed to ntfy and subscribers; no sensitive user data belongs there.

The owner must still provide/approve:

1. Exact alert release IDs and public titles; a price ceiling for Kendrick27048621
   is missing.56 Nights28720177 has a saved USD10 **all-in** ceiling and VG+/VG+
   rules, but its independent alert rule must be explicitly approved, not silently
   enabled from the buying rule. Decide whether to alert initially low stock.
2. An explicit API cadence (default 10 minutes; minimum 5 minutes) and the exact
   aggregate-only deployment on the existing VPS. No additional hosting, browser route or paid resource is needed.
3. A securely entered private topic file and subscription in the official phone
   app to that same topic, with OS notification permission. No signup, access token
   or card is required by this adapter. Review the current free limits/terms at
   activation; accepting any new terms remains an owner action.
4. Separate permission for code deployment, alert activation and **one public-data
   test message**, followed by owner confirmation of actual phone delivery.
   Selection of ntfy alone has not activated or sent anything in this pass.

Primary references reviewed October28,2026:
[JSON publishing](https://docs.ntfy.sh/publish/#publish-as-json),
[privacy and service guarantees](https://docs.ntfy.sh/faq/),
[limits](https://docs.ntfy.sh/publish/#limitations),
[phone subscription](https://docs.ntfy.sh/subscribe/phone/).

## Redacted configuration example — historical planning only

No code automatically loads this example or changes a live database. The existing
rule API is explicit and revision-checked; a deployment operator must apply only
owner-approved values. All flags below remain off and no topic name appears:

```json
{
  "polling_enabled": false,
  "delivery_enabled": false,
  "run_continuously": false,
  "interval_seconds": 600,
  "topic_file": "/run/secrets/sniper-ntfy-topic",
  "rules": [{
    "release_id": 28720177,
    "public_title": "DJ Esco / Future - 56 Nights",
    "currency": "USD",
    "item_price_ceiling": "10.00",
    "enabled": false,
    "alert_initial_low": true
  }]
}
```

The existing private tagged database must already exist. For an approved later
activation, use `AggregatePriceAlerts.save_rule(rule, expected_revision=...)` and
`set_enabled(True, expected_revision=...)`; both require current revisions. Applying
an identical rule by saving again creates a **new rule revision** and may cause a
new initial alert, so do not replay configuration on every restart. Read/compare
current stored configuration first. Do not release `cloud_control.killed` or
unpause the browser monitor. Use a single owned worker per database.

Default-off readiness command (no provider call):

```sh
python -m finder.discogs_price_worker --database /path/to/existing/rules.db
```

After separate deployment/activation and message approval only, the actual worker
entry point is:

```sh
python -m finder.discogs_price_worker --database /path/to/existing/rules.db \
  --poll --poll-seconds 300 --deliver --topic-file /run/secrets/sniper-ntfy-topic --run
```

The topic file must already be an owner-only0600 regular file, not a symlink or
hard-linked file. The worker never creates it or repairs permissions. Do not place
a topic in a shell command/history; use the separately approved secure setup flow.
No startup unit, scheduler or credential file is installed by this change.

## Activation and rollback checklist

1. Pin the reviewed passing commit; verify existing service/runtime/database
   identity and backup **operational data only through the approved local process**.
   Exclude browser profiles, payment/card data and secrets from code bundles.
2. Upload/build the code only after deployment approval. Preserve original
   transaction audit/stop markers and all paused purchase/browser services. Add
   only the alert tables through the explicit worker construction; buying tables
   and grades/all-in caps remain unchanged.
3. Apply approved alert rules/revisions; check defaults/stop state and an intercepted
   rehearsal first. Confirm no other API worker duplicates polling outside the
   shared budget. Do not automatically enable known-listing reads.
4. Securely configure the topic/subscription, then approve/execute one test message
   containing only public facts. Record provider acceptance separately from the
   owner's phone confirmation. A failed test is not a reason for repeated sends.
5. Activate the single aggregate-only worker only after the owner approves ongoing
   scope/cadence. Verify current API evidence, no checkout capability, no shared
   buying-control changes, bounded logs and the200/day local attempt ceiling.
6. To stop, use the alert `set_enabled(False, expected_revision=...)` and stop only
   that worker. In-flight sends may be uncertain. Preserve episodes/outbox across
   restarts/rollbacks; never delete a database or reset claims to re-send. Remove
   the sender configuration only through the approved owner process. Do not
   restart or unpause checkout/monitoring as part of alert rollback.

## Verification

Offline tests use disposable SQLite files and `httpx.MockTransport`; no real topic
or message is involved. They exercise the actual worker tick from stats API shape
through the episode/outbox bridge to the fixed JSON sender, plus constant-count
price changes, initial policy, repeated/changed data, exit/re-entry, null currency/
price, zero stock versus errors, stale ordering, restart/claim competition,
revocation, safe retry, ambiguous delivery and topic redaction. These are code
checks, not live notification delivery or condition/all-in qualification.

## Any-price availability rules

For an explicitly requested availability watch, use `any_price:true` and omit
`item_price_ceiling` (or set it to JSON `null`). A ceiling and `any_price:true`
are mutually exclusive. Existing saved rules without `any_price` keep the original
ceiling behavior; omitting both modes is rejected rather than silently enabling
unlimited-price alerts. Currency is still required for the official stats request.
For example, the rule shape below is inert until separately saved and enabled:

```json
{
  "release_id": 31475657,
  "public_title": "Lil Uzi Vert - Pink Tape",
  "currency": "USD",
  "any_price": true,
  "item_price_ceiling": null,
  "enabled": false,
  "alert_initial_low": true
}
```

A valid positive aggregate count establishes availability even when the reported
minimum price is missing. No arbitrary ceiling is substituted. The compatibility
field `alert_initial_low` also controls the initial availability alert. Changes in
count or price while copies remain available do not create new episodes. A verified
zero count with no price ends the episode; later availability creates another.
Invalid counts, mismatched observation currency and stale/failed reads never rearm
an episode. These alerts cannot detect every individual listing or offers that
appear and disappear between polls.

Availability titles show the record and **any price**, with the current minimum
when already retained by the enabled price-drop watch; otherwise they say **available**.
The body includes the configured currency and explicitly describes aggregate
availability, not an individual new-listing alert. A missing minimum is never
rendered as zero or as a price qualification. The sender accepts only canonical
message shapes, and delivery rechecks the saved rule's mode and payload before
dispatch.

No SQL migration is required. Rules are validated JSON; existing outbox and sample
price fields use an empty internal marker for availability episodes, never a
monetary value. Existing revision, pause, freshness, retry, uncertain-send and
purchase-stop safeguards are unchanged. A mode change is a rule revision and
revokes pending events or quarantines claimed events as usual. Downgrading to code
without `any_price` support requires disabling the worker and restoring compatible
rule configuration first; never reset outboxes to force delivery.

## Prepared private ntfy transport (not activation)

The optional `--ntfy-config-file /run/secrets/sniper-ntfy.json` replaces
`--topic-file`; the two flags are mutually exclusive and the private configuration
requires explicit `--deliver`. The existing topic-file route remains fixed to
`https://ntfy.sh/` with its existing destination binding.

The private file must be a current-user-owned, single-link regular file with exact
0600 permissions, at most 4096 bytes. It contains exactly three JSON string keys:
`origin`, `topic`, and `token`. The only permitted origin is
`https://vps-a0c5b746.vps.ovh.us` (no trailing slash, alternate port, path, query or
credentials). `topic` must be 24–64 ASCII letters, digits, underscores or hyphens.
`token` must be an ntfy bearer token (`tk_` followed by 16–128 ASCII alphanumerics).
Provision real values only through a separately approved secure setup process,
never command arguments, environment, fixtures, documentation or logs. This code
does not create credentials, change permissions, install a server or activate sends.

Publishing uses the fixed HTTPS root, JSON body and Authorization header. TLS
verification remains enabled; redirects and environment proxies remain disabled.
Payload validation, local limits, backoff and uncertain-send behavior are unchanged.
The private destination hash includes origin, topic and credential identity (token);
token rotation deliberately changes this binding. Old pending/retry/uncertain
messages are never implicitly moved or replayed to the new destination. Review any
existing destination-bound work during an approved migration; do not clear claims,
reset episodes or delete the database to force re-notification.

Self-hosted ntfy native iOS delivery may still rely on its upstream push service.
For an approved independent Web Push setup, verify HTTPS, ntfy Web Push server
configuration and an iOS 16.4+ Safari Home Screen installation. Server acceptance
is not proof of a device notification. Credential provisioning, Web Push setup,
server deployment and actual phone delivery remain separate verification gates.

## Read-only health command

For non-mutating operational checks use `python -m finder.discogs_price_health`,
not the worker's inert tick or `AggregatePriceAlerts.status()`, which may initialize
schema or reconcile outbox states. The health command reports per-rule freshness,
failed polls, controls, queue/retry/uncertain states, and supplied configuration
validity without network calls or secrets. Provider acceptance remains distinct
from phone delivery. See [aggregate monitor health](discogs-price-health.md) for
arguments, exit codes, limitations and the offline/live verification gates.


## Optional aggregate change signals

Existing entry and availability defaults remain unchanged. The additive opt-ins
`alert_price_drops:false` and `alert_count_increases:false` can report new observed
price lows and offer-count highs within an eligible period. See
[aggregate change leads](discogs-aggregate-changes.md) for exact semantics,
no-flood activation, queue/recovery behavior, and mandatory legacy-code rollback
preparation. These signals are aggregate changes, never individual new listings.

The optional health-watcher milestone also tightens shared ntfy dispatch:
aggregate and health delivery share an owner-only single-host sender lock, finish-
time pacing, known numeric/HTTP-date provider deadlines, and bounded interrupted-
attempt recovery. Explicit Retry-After deadlines are not clipped to the internal
backoff cap, including on uncertain responses. Unrepresentable deadlines require
manual attention through the existing shared gate. This does not change Discogs
polling or record criteria. See [health watcher](discogs-health-watcher.md) for the
coordinated version cutover and downgrade limitations.


## Actionable notification formatting and offline proof

Synthetic examples (not real watches or current inventory):

- `Fixture Artist - Album | USD 9.99 min`
- `Fixture Artist - Album | USD 8.99 min (was USD 9.99); offers 5 -> 6`
- `Fixture Artist - Album | available; any price`

The body states `Watch: item price at or below USD 99.99.` for that exact saved
cap, or `Watch: any price (USD).` A count-only change can also show a known
current minimum without claiming that the minimum decreased. Unknown prices
stay absent. A price/count change compares earlier observed extrema in the watch
period; it does not establish a newly listed or sold copy.

`discogs_price_messages.py` owns generation and strict canonical validation.
The old `lead_message`, `availability_message` and `change_message` import paths
and positional arguments remain supported. Legacy helper calls that do not supply
a cap do not invent one. Current rule-driven calls include the saved cap/mode and
currency. Existing rule data, revisions, price matching, episode keys, queue states,
provider guards, cadence, budgets and retry rules are not migrated or reset.

Unicode controls, line/paragraph separators and malformed surrogate characters
in display titles are replaced with spaces. Whitespace is collapsed; the body
keeps at most 120 title characters. The visible title is truncated at a valid
UTF-8 boundary to 180 bytes, reserving room for the complete price/count summary.
Canonical payload JSON is limited to 4,000 bytes, including JSON escaping and
leaving room below 4,096 bytes for the bounded topic envelope. The warning and
link are never truncated. Saved titles are not rewritten.

Run the isolated proof from a development environment:

```sh
PYTHONPATH=src python -m pytest -q tests/test_discogs_price_messages.py
```

It uses 19 entirely synthetic rules (13 cap shapes and six any-price shapes),
disposable SQLite state, the real stats adapter/worker/matcher/outbox and private
sender with intercepted HTTP, plus a socket/DNS fail-closed guard. Each rule covers
entry, combined changes, unchanged data after restart, and count-only changes;
accepted messages are not called phone-delivered. It verifies saved rules/targets
stay unchanged and accepted outbox entries are not replayed. Additional cases cover
exact `.99` and inclusive `.00` boundaries, all supported currencies, missing
prices, malformed/control/long Unicode titles, bounded UTF-8 and payloads,
tampering and canonical disclaimer/link validation. Existing alert and aggregate
signal suites retain failure, stale data, cooldown, revocation and uncertainty
coverage. This is offline evidence, not a live send, deployment or phone test.


### Formatting cutover and rollback gate

Stop the aggregate worker and coordinate the health watcher before inspecting the
final cutover snapshot. Require zero `pending`, `retry_wait` and `leased` rows in
`discogs_price_outbox` before switching either forward or backward. A pre-stop
empty queue is not sufficient: a poll may enqueue an event before the worker stops.
If unfinished work exists, let the currently reviewed version settle it within
normal cooldown/freshness rules before stopping again, or defer the switch. Never
delete/reset events, clear uncertainty or force an extra send to pass this gate.
Preserve accepted, failed, revoked, expired and uncertain history and all budgets.
Uncertain attempts need the existing manual reconciliation, never automatic replay.

The outbox stores event/rule/price metadata, **not serialized notification text**.
A pending event is rendered only when claimed, so previous and new code can each
render their own canonical format from unchanged stored rows. This update changes
no table, saved rule shape or revision. In-memory leased reservations must not be
transferred between versions: stop/drain first, and retain the existing abandoned
lease quarantine if a crash occurs. Do not restore an older database to roll back
formatting, since that can discard acceptance evidence and replay notifications.
