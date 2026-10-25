# Read-only monitor recovery: bounded trial complete

The fixed bridge code from `49428f4918fdf0a260c0b72ffe2500b60087686a` is
deployed on the existing private OVH browser. Nic confirmed the normal 56 Nights
offers page in noVNC, and one fresh, boot-bound operator grant was consumed.
The existing hosted `/bridge/access-stopped` file stays in place. Ordinary
reads, the recurring worker, cart writes and order submission remain
unavailable. The historical
`access_restricted` response did not retain whether it came from HTTP 401/403,
a redirect, or a challenge. The old marker is history, not proof of a current
challenge. This procedure does not rewrite that history.

## One-time hosted result, October 25, 2026 UTC

| Exact release | Complete offers | Pages | Result |
| --- | ---: | ---: | --- |
| `28720177` (56 Nights) | 19 | 1 | observed |
| `5520534` (Oxymoron) | 23 | 1 | observed |
| `13053315` (Not All Heroes Wear Capes) | 21 | 1 | observed |

The fixed reader returned distinct listing IDs and complete first-page ranges
matching each displayed total. The owner-only audit contains three
`trial_observed` events, one `trials_complete_review_required` event and no
`trial_stopped` event. No challenge or denial appeared during these reads.
The original stop marker retained its prior modification time; the private
web service still reports zero saved targets, a paused schedule, no live listing
reader and order submission disabled. A complete single snapshot for each ID
proves only bounded acquisition at that time. It does not prove change detection,
restart recovery, future access or permission for recurring collection.
The one-time bridge responses were summarized as counts, ranges and listing IDs
for this review; their full row bodies were not saved as a replay bundle or
ingested into the user's rules database. Audit files hold only safe status
metadata. Reconstructing missing fields would be synthetic, not a replay of
live observations.

## State transition

1. **Stopped.** An operator with access to the private noVNC view asks the owner
   to open one of releases `28720177`, `5520534`, or `13053315` normally and to
   complete any verification themselves. The bridge makes no request for this
   step. It accepts only an owner-readable, owner-written acknowledgement file
   containing the selected exact release ID, a fresh UUID grant, and the current
   browser boot ID. The file must be no more than five minutes old.
2. **Current-page verification.** The bridge atomically consumes that grant
   before looking at the browser. It reads the already-open page only, requiring
   one exact canonical marketplace URL, a normal title, matching release IDs,
   stable rendered rows and pagination evidence. A partial first page is enough
   to confirm a normal page, but never becomes a complete baseline. A wrong page,
   challenge, login screen, stale grant or uncertain result ends the attempt.
3. **Bounded trial.** A verified page permits one ordinary read-only trial for
   each of the three exact IDs. The existing 15-minute request spacing applies;
   the whole trial expires after one hour and does not survive browser restart.
   Each release is consumed on its first actual attempt. A cooldown before a
   request leaves that release pending; a denial, incomplete read, redirect,
   challenge, timeout, disconnected caller, page drift or uncertain outcome ends
   the trial. A timed-out or disconnected verification cannot activate the trial
   after the caller has gone; an interrupted trial loses its remaining reads.
   The fixed
   reader may follow validated same-release pagination only. It cannot accept
   arbitrary URLs, cart actions, credentials, code or purchase commands.
4. **Review required.** Three complete observations produce an audit event and
   `recovery_review_required: true`. They do **not** clear the old marker, start
   the worker, save targets, enable future reads or authorize purchases.

Audit events are separate owner-only files with a grant ID, exact release ID,
time and bounded status/cause code. A trial-start event precedes each attempted read,
so an interrupted result is visible as uncertain. Future HTTP 401/403, login/challenge
redirects and challenge titles have distinct codes; no URL, cookie, profile,
page text or account data is logged. A new actual restriction stops the trial.
Missing diagnostics stay unknown rather than being called Cloudflare.

## Criteria before any later monitoring transition

The three trial observations and the private socket boundary passed their
bounded checks. Discogs' current
[Terms of Service](https://support.discogs.com/hc/en-us/articles/360009334333-Terms-of-Service)
license excludes collection and use of product listings, descriptions and
prices, and use of data-mining, robots, scraping or similar extraction tools,
without express written consent. No such consent or other supported basis for
this recurring site-page collection is verified. Nic declined support outreach;
the successful one-time trial does not remove this blocker. The separate
[API Terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
also limit inconsistent automated access, but the site Terms apply directly to
the proposed hosted browser route.

If a supported access basis is established, the next change would require
review of an **explicit bounded read-only lease**: only saved exact releases,
expiration and request caps, a permitted cadence, no overlapping reads, durable
stop on a fresh challenge or denial, and a way to revoke without erasing the
historical stop record. No lease or stop-clearing transition is implemented.
Saving targets or starting the worker before that review is out of scope.

Technical validation after an allowed lease would still need at least two
successive live snapshots of the same release to prove new, changed,
disappeared and reappeared listing handling; an incomplete/failed pass must
preserve the prior baseline. Restart the actual hosted worker to prove durable
cadence, deduplication and recovery. Exercise a legitimately accessible
multi-page release to verify hosted next-page traversal and drift handling:
the three live test releases each fit on one page, while multi-page traversal
has only offline fixture coverage. These checks must retain read-only and
purchase-disabled settings. Passing them would establish monitoring behavior,
not checkout readiness or authorization to order.

Existing offline tests already cover the durable code path with synthetic
observations: `test_restart_cadence_baseline_new_reappeared_and_duplicate_suppression`,
`test_failed_and_partial_reads_preserve_baseline_and_backoff`, and
`test_socket_to_durable_baseline_is_an_integrated_offline_dry_run` in
`tests/test_discogs_hosted_scheduler.py`, plus initial/unchanged, reappearance
and partial-baseline cases in `tests/test_discogs_release_snapshots.py`.
Those tests passed in the project's earlier full local suite and the deployed
revision's GitHub CI. They do not supply successive live observations, a
running hosted worker, or live multi-page coverage.

No action here places a record order or payment. There are no saved production
monitoring targets, and the hosted schedule remains paused.
