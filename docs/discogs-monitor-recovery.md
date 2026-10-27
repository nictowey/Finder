# Read-only monitor recovery: bounded worker trial complete

The separate hosted worker was installed and completed six of six scheduled
read-only checks under a bounded browser lease on October 26, 2026 UTC. It was
then paused and stopped. The three exact-release monitoring targets remain
saved; continuous monitoring is not active. The existing hosted
`/bridge/access-stopped` file stays in place, and buying remains disabled. The
historical `access_restricted` response did not retain whether it came from HTTP 401/403,
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
The original stop marker retained its prior modification time. At that early
checkpoint, the private web service reported zero saved targets, a paused
schedule, no live listing reader and order submission disabled. Targets were
saved and a separate bounded worker trial ran later, as described below. A
complete single snapshot for each ID proves only bounded acquisition at that
time. It does not prove change detection,
restart recovery, future access or permission for recurring collection.
The one-time bridge responses were summarized as counts, ranges and listing IDs
for this review; their full row bodies were not saved as a replay bundle or
ingested into the user's rules database. Audit files hold only safe status
metadata. Reconstructing missing fields would be synthetic, not a replay of
live observations.

## Original one-time recovery procedure

The original recovery used bridge revision
`49428f4918fdf0a260c0b72ffe2500b60087686a`. Its one-use grant and three-read
trial were distinct from the later six-check worker lease.

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

## Bounded hosted worker result, October 26, 2026 UTC

A later reviewed browser update and fresh owner verification enabled an explicit
read-only lease for the same three release IDs. The lease allowed six attempts
over two hours, with at least 15 minutes between global request slots and no
overlapping reads. The separately installed worker completed all six scheduled
checks. Successive observations of the same releases exercised change detection;
changed listings were observed and unchanged observations suppressed duplicate
change events. These were live worker results, separate from the earlier
one-time counts of 19, 23 and 21 offers.

The scheduler was explicitly paused and the worker stopped after the bounded
trial. No lease was automatically renewed, and the historical access-stop record
and audit history were preserved. This document does not reconstruct later offer
counts, event counts, precise read times or old/new price amounts that are not
retained here.

Monitoring targets for `28720177`, `5520534` and `13053315` were saved. The
per-release editor also persisted a **disabled** 56 Nights rule: maximum USD
10.00 all-in, including shipping, tax and mandatory fees; minimum media VG+ and
sleeve VG+; no seller-rating or feedback-count minimum. Unknown delivered totals
cannot qualify. Saving this rule did not authorize or enable a purchase.

The completed lease demonstrates bounded scheduling and successive-snapshot
handling. It does not establish continuously fresh inventory or unattended
checkout. The global request spacing also differs from snapshot freshness: three
targets may be revisited roughly every 45 minutes, while the separate freshness
flag expires after five minutes. A retained snapshot is an observation, not a
guarantee of current availability.

## Access basis and remaining monitoring validation

The one-time observations, later worker trial and private socket boundary passed their
bounded checks. Discogs' current
[Terms of Service](https://support.discogs.com/hc/en-us/articles/360009334333-Terms-of-Service)
license excludes collection and use of product listings, descriptions and
prices, and use of data-mining, robots, scraping or similar extraction tools,
without express written consent. No such consent is verified. Nic declined
support outreach and, after this concern was disclosed, asked to proceed with
private-use monitoring. That decision does not override a future live access
restriction or prove the site permits the collection. The separate
[API Terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
also limit inconsistent automated access, but the site Terms apply directly to
the proposed hosted browser route.

The bounded lease was deployed and exercised. Its request budget is spent and
cannot authorize another bridge read. Any later monitoring activation must retain exact-release
scope, expiration and request caps, the global request spacing, single-flight
access, durable stop on a fresh challenge or denial, and revocation without
erasing historical records. Saving targets alone cannot clear the old stop or
start the worker.

The live worker trial supplied successive snapshots, observed changes and
duplicate suppression. Live disappearance/reappearance handling and preservation
of the prior baseline after incomplete or failed reads remain unverified.
Restart the actual hosted worker to prove durable cadence, deduplication and
recovery; later browser restarts while monitoring is stopped do not establish
that behavior. Exercise a legitimately accessible
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
Those tests passed in the project's earlier full local suite and GitHub CI.
Their synthetic restart and failure coverage does not replace the remaining
hosted validation or live multi-page coverage.

No monitoring action here placed a record order or payment. Three monitoring
targets and the disabled 56 Nights rule are saved. The hosted schedule remains
paused and its worker stopped.
