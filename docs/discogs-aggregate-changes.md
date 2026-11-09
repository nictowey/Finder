# Opt-in aggregate change leads

This additive change is tested offline. It does not activate or alter a live
watch, start a worker, configure a destination, or send a real notification.
It uses the same official aggregate stats reader and existing private/public
ntfy transport. There is no scraping, listing discovery, browser, purchase,
email, payment, or new hosting path.

## Exact behavior

`PriceLeadRule` adds two **strict boolean, default-false** flags:

- `alert_price_drops`: alert when the reported minimum item price reaches a
  strictly lower value than previously observed in the current eligible period.
- `alert_count_increases`: alert when the reported aggregate offer count reaches
  a strictly higher value than previously observed in that period.

Eligibility is unchanged: at/below the existing item-price ceiling for a price
rule, or a valid positive aggregate count for an any-price rule. A verified zero
count with no price ends eligibility. A valid above-ceiling price also ends it
for a ceiling rule. The first eligible observation establishes change baselines;
it does not produce a separate change notification. Existing entry/availability
notifications, including `alert_initial_low`, retain their behavior.

A durable price low-water and count high-water prevent oscillation alerts. For
example, count `5 → 6 → 4 → 5 → 6 → 7` can alert at 6 and 7, not the intermediate
recoveries. Price `10.00 → 9.00 → 10.00 → 9.00 → 8.99` can alert at 9.00 and 8.99.
A count rise below or equal to the previous high-water does **not** alert. Neither
signal identifies a new listing, a sold listing, a seller, grade, or a qualifying
delivered price. Changes hidden by unchanged count/minimum remain invisible.

Prices retain the existing exact two-decimal, same-currency validation. A one-cent
new low is a signal when explicitly enabled; this is not an FX/noise filter, and
it makes no claim about why the reported minimum changed. Count changes must be
bounded integers, not booleans, floats, strings, or missing data. Any-price rules
can still observe availability/count when price is unknown. With price-drop
alerts enabled, their price channel resumes silently at its next valid price.

After an error, unknown sample, pause, or a gap of at least 1,200 seconds between
successful observations, comparison continuity is broken. The first fresh valid
sample silently merges into the existing extrema: it may lower the low-water or
raise the high-water, but cannot erase earlier marks. This avoids replaying old
levels after recovery. Events that occurred during the gap are not inferred.
A pending change loses any reasons that cannot be established across recovery.
Unchanged threshold-entry messages keep their existing recovery behavior.

The same freshness window applies to change comparisons, so polling at 1,200
seconds or longer cannot establish continuous change signals. Existing entry
alerts still work with the wider supported polling intervals. The live five-minute
cadence is inside this window.

## Queue, dispatch, and uncertainty

- One queued notification absorbs changes within a qualifying period. An unsent
  entry message takes precedence and tracks the latest minimum. A pending change
  can combine price and count reasons in one message. It retains its event key,
  attempts, bound destination, and retry due time rather than resetting backoff.
- Pending messages always describe the latest supported observation. A rebound
  removes a no-longer-supported reason; if no reason remains, the event is revoked.
  Revisiting the same price/count extrema does not enqueue it again.
- A claimed message is immutable. Concurrent observations are checked immediately
  before sending. In-flight changes are absorbed conservatively rather than
  duplicating a potentially already-sent message. Later distinct extrema can
  generate distinct events; this is not a retry of uncertain delivery.
- Final dispatch checks control, rule revision, eligibility, freshness, current
  price, current count for count messages, enabled signal flags, and the exact
  canonical public payload. A count-only change cannot escape the current-count
  check merely because price did not change.
- Existing 90-second spacing, 200 claims per UTC day, five-attempt limit, stale
  expiry, fixed destinations, and no automatic uncertain-delivery retry remain.
  A known retryable rejection additionally sets a shared worker cooldown so a
  replacement event cannot bypass the provider's backoff.
- A failure, expiration, or uncertainty never recreates the same event. Rate
  limits, missing data and missed polls can suppress notifications. Provider
  acceptance is not proof of phone delivery.

Messages use “reported minimum item price decreased” and/or “reported aggregate
offers increased,” with explicit aggregate/manual-review warnings. They contain
only the public record title, release/link, currency and supporting observed
price/count facts. They never disclose the owner's threshold, grades, address,
account, destination secrets, or buying permission.

## Additive persistence

The original tables and columns are unchanged. Construction idempotently adds:

- `discogs_price_extrema(release_id, price_floor, count_high, continuous)`
- `discogs_price_changes(event_key, previous_price, previous_count, count)`

A change row supplements its original outbox row by event key. Historical
entry/availability events have no supplemental row. `episode` remains the durable
outbox sequence and, with an opt-in enabled, also advances for distinct change
events. It is not a count of listings or strictly a count of stock episodes.
All observations, extrema changes, and queue writes share one SQLite transaction.
No PostgreSQL schema or buying target is changed.

Old serialized rules without these fields remain unchanged. New serialization
omits the two flags when false, allowing ordinary disabled-change rules to remain
readable by the preceding binary. **Enabled flags are not backward compatible
with the old strict rule validator.** Use the rollback procedure below before
starting old code; do not assume additive tables alone make a rollback safe.

## Controlled rollout without an initial-alert flood

The operator must have separate deployment/activation approval. Keep the existing
worker, destination, 300-second cadence, buying stop, and other services unchanged.

1. Verify the exact passing commit and the current service/database identity.
   Verify every selected rule has prior observations (`seen=1`), current data,
   and the expected current mode/threshold/revision. Resolve queued/in-flight
   notifications first. This is a rollout of existing initialized watches, not
   a bulk creation of fresh rules.
2. Deploy the code with both flags off. Construction only adds the tables and
   does not modify existing rules, samples, or notification history. Check health
   and a mocked end-to-end rehearsal before applying settings.
3. For each explicitly selected existing watch, use the narrow operation below
   with the current saved revision. Do not bulk call `save_rule` on startup.

   ```python
   new_revision = alerts.configure_change_alerts(
       release_id,
       alert_price_drops=False,      # Choose only the explicitly enabled signals.
       alert_count_increases=True,
       expected_revision=current_revision,
   )
   ```

   This changes only the two opt-ins and the rule revision. It preserves the
   threshold, any-price mode, enabled state, initial-alert policy, prior `low`/
   `seen` state, price/count, failures, and poll/backoff timing. Repeating identical
   settings is a no-op. Changed settings revoke old-revision queued notifications
   and quarantine leased ones; they never replay them. Existing extrema survive,
   but continuity is broken so the next observation is silent for change signals.
4. Verify the first successful sample establishes/merges the baseline without a
   change alert. A genuine eligibility transition still follows the preexisting
   entry policy. A watch with no prior observations still follows its existing
   initial-alert policy; the API does not silently change that preference.
5. Validate one selected change end-to-end under a separately approved controlled
   test, recording provider acceptance separately from owner phone receipt.
   Verify no repeated alert on identical observations, accurate message wording,
   and healthy polling. Broader enablement comes only after that proof.

Enabling any-price **count changes alone** does not require enabling price-drop
alerts or changing its meaning to a price-qualified watch. The policy choices
remain independent. Do not lower thresholds or convert any-price rules as part
of feature rollout.

## Safe code rollback without notification replay

Never restore an old database snapshot after sending: that can erase accepted or
uncertain history and cause duplicate notifications. Preserve the live ledger.

1. Stop the alert worker through the authorized service operation. Set the
   separate alert control to disabled using its current revision. Do not change
   the global buying stop or restart other services. A request already in flight
   may have been delivered; leased events are retained as uncertain.
2. While running this new code, call:

   ```python
   result = alerts.prepare_legacy_rollback(
       expected_control_revision=current_stopped_control_revision,
   )
   ```

   It requires disabled alert control and the exact current control revision.
   In one transaction it removes only the two new JSON fields, increments affected
   rule revisions, revokes their unsent notifications, and quarantines leased
   ones. It also revokes any residual change notification so the old binary cannot
   misinterpret it as an ordinary entry alert. Sample/queue history, accepted and
   uncertain outcomes, attempts, provider binding, and cooldowns remain intact.
   It does not delete tables or re-enable the worker. Repeating it is safe.
3. Verify all rule JSON is readable by the old strict model, no active change
   events remain, and history/cooldowns are preserved. Deploy the previously
   passing binary. Re-enable only the separately approved alert service/control,
   not checkout or browsing. Existing `low`/`seen` state prevents initial replay.

## Verification

Tests use temporary SQLite databases, deterministic clocks and
`httpx.MockTransport`. The actual official reader → worker tick → queue → private
ntfy JSON sender flow is exercised for both threshold and any-price modes, with
all requests intercepted. Coverage includes legacy default behavior, baseline
upgrade, restart, low/high-water oscillations, exact cents, no-flood opt-in,
coalescing, current-reason invalidation, unknown counts/prices, gaps and recovery,
provider backoff, global caps, concurrent claims, stale samples, rule changes,
pause, destination binding, canonical message tampering, and rollback/history.
These checks are not a live notification test or a guarantee of individual
listing discovery or actual phone delivery.
