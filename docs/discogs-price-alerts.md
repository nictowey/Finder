# Official API low item-price leads and optional ntfy delivery

This implementation is **not deployed or activated**. The worker and sender default
to off. It cannot access a cart, order, payment, browser session or purchase ledger.
No topic, credential or test notification has been created or sent by this work.

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

The entire external message contains only the owner-approved **public record
title**, release ID/link, currency, observed minimum item price and this notice:
**Condition and delivered total are unverified. This is a manual-review lead,
not a qualified purchase.** Fixed “Data provided by Discogs” attribution is included. It links to the release marketplace, never a fabricated
individual offer. No seller, address, threshold, grade rules, account identity,
event key or buying permission is published.

## Code and persistence

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
poll interval is600seconds. Claims and due times are durable. Failed reads back
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

## ntfy privacy and owner inputs

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
2. Nominal10minute API cadence and the exact aggregate-only deployment on the
   existing VPS. No additional hosting, browser route or paid resource is needed.
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

## Redacted configuration example — planning only

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
  --poll --deliver --topic-file /run/secrets/sniper-ntfy-topic --run
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
