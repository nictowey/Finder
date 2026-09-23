# Finder private pilot: useful, operable, measured

Owner decisions, September 23, 2026: exact-target pressing review comes first; free hosting
only; review alerts may include explicitly uncertain editions. Three watches remain the
supported capacity. No automated purchase, exact-identity claim, or valuation.

## Product judgment

A pressing-specific watchlist is worth testing if it saves a collector repeated searches and
helps them inspect relevant copies while still available. API connectivity, an attractive inbox,
and a growing test suite do not establish that value. The difficult work is search coverage,
wrong-edition rejection, handling uncertainty without flooding the collector, and timely delivery.
Keep this a narrow personal pilot until those outcomes are measured. A public or paid product
also depends on the unresolved provider-use decisions in `decisions/0001-provider-use.md`.

## Scorecard and release checks

| Area | Current evidence | Required evidence / next action |
| --- | --- | --- |
| Scheduling | Pre-instrumentation dispatches at 20:05 and 20:45 UTC both completed 3/3 watches. This is one observed 40-minute interval. | Collect 14 consecutive days of due/start/finish history. Investigate every >60-minute freshness gap, failed/interrupted scan and quota pause. Include missing runs, not only attempted scans. Do not reset the window to hide failures. |
| Dispatch provenance | New dispatcher records reserved/accepted/rejected/unknown outcomes and sends a correlation ID. | Deployment #13 passed; no watches were due. Observe the first unattended correlated dispatch reaching the worker. Acceptance without work is not success. |
| Recovery | Offline lease-expiry, stale-worker, edit-race, quota-pause and failed-dispatch tests. | Deployment #13 passed isolated PostgreSQL upgrade, synthetic backup/restore, migration/deletion and private-access checks; the authenticated health panel renders. A real production recovery drill remains separate. |
| Capacity | Three-watch cap; per-watch shared quota preflight; scan attempt/retry counters. | Review real daily request totals and shared quota readings across a full day. Interrupted requests/other workflows may be absent from local counters. No expansion based only on a nominal limit. |
| Delivery | Generic review payload, bounded retries, expired-device removal, explicit test flow. | Nic enrolls his actual phone, sees a test while the app is in the background, then sees one eligible real review alert. Push-service acceptance alone is insufficient. Inspect duplicate/retry behavior. |
| Discovery | Bounded newest and rotating older pages; caps exposed. | Under permitted use, compare a separately assembled active-listing sample. Record found and missed eligible items, including known misses submitted manually. Aim for 95/100 eligible offers; a smaller sample is incomplete, not a pass. |
| Pressing usefulness | Review leads remain unverified; strict mode available. | Privately inspect both plausible and rejected listings with a decisive basis. Record correct target, wrong edition, cannot tell, and unavailable; keep unresolved cases. Real retention/evaluation depends on the recorded provider-use decision. |
| Speed of useful discovery | Scan timing is measurable; listing discovery delay is not yet established. | At least 100 qualifying newly listed sampled offers, with publication and first discovery time; target 95 within 60 minutes. Extend the period if rare inventory is too sparse. |
| Collector value | No measured time saved or purchase-worthy yield. | Nic identifies whether the feed saved searches and which leads were worth inspecting. If ordinary eBay saved searches are equally useful, improve pressing discrimination/coverage before adding features. |

## Operational evidence

Migration 2 keeps 30 days of metadata; the private dashboard reads a rolling 14-day view.
The clock begins at installation. This is elapsed time, not certified observation coverage.
A watch attempt captures its due time before leasing, start, expiry, finish/outcome, workflow ID,
and bounded counters. A killed worker cannot vanish from the report. Current watch freshness
and due time expose an outage even before another attempt begins. Deleted watches lose their
linked history; report those changes when evaluating the pilot.

An unknown dispatch response may still have reached GitHub. Preserve it as unknown and use
worker correlation to resolve it; never infer failure or success from a timeout alone.
The trigger header/timestamp/cooldown are abuse bounds, not strong invocation authentication.
The trigger exposes no saved-watch or provider data.

## Failure response

1. Check the dashboard's current last success, overdue state and recent run link.
2. A stale trigger heartbeat plus overdue watches: inspect the deployment/trigger and token
   expiry (current credential expires October 23, 2026). Do not create overlapping schedules
   or rotate credentials by guessing. A rejected dispatch HTTP status is now retained.
3. An interrupted scan: lease expiry allows retry; reject the previous worker's late result.
4. A quota pause: retain due time/cursor and inspect the shared quota. Do not bypass the guard.
5. Failed/invalid push: enroll again when necessary, send a test, and confirm device display.
6. Provider failure or uncertain pressing: show degraded evidence; do not promote identity.

A dashboard cannot notify anyone while they are not viewing it, and in-stack monitoring cannot
prove an entire stack outage. Independent periodic inspection and a verified owner notification
channel are still needed before unattended reliability is established. The project must not be
represented as continuously supervised by an assistant after a conversation ends.

## Stop or change direction

- If useful targets routinely exceed the free request budget, reduce depth/cadence or supported
  watches explicitly. The owner declined paid hosting; do not silently incur costs.
- If search misses the examples a collector cares about, prioritize retrieval over more UI.
- If review alerts mostly mean 'cannot tell', improve distinguishing evidence or change the
  supported scope; do not solve alert volume by claiming certainty.
- If provider permission blocks a public product, keep the permitted private scope and assess
  a different authorized data route before monetization work.

## September 23 deployment verification

[Deployment #13](https://github.com/nictowey/Finder/actions/runs/35920314329) deployed
PR #59 at `16c82ba8d0da2e7bcc2ec10bea2e43b0bb71b370`. All 292 Python and 29 Node tests,
Ruff and TypeScript passed in the deployment. The isolated PostgreSQL rehearsal returned
both passing results. Production migration completed and access checks passed.
History began at 21:07:25 UTC; no watches were due at deployment, so its worker attempted
zero scans. Existing three watches and inbox entries remained visible after deployment.
The authenticated dashboard displays the new health and notification setup controls.
At inspection there were zero registered devices and no recorded trigger heartbeat yet.
The first automatic correlated dispatch and actual phone display remain unverified.
