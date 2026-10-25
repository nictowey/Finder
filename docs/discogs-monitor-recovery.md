# Proposed read-only monitor recovery (dormant)

This code is prepared but **not deployed or activated**. The existing hosted
`/bridge/access-stopped` file stays in place. Ordinary reads, the recurring
worker, cart writes and order submission remain unavailable. The historical
`access_restricted` response did not retain whether it came from HTTP 401/403,
a redirect, or a challenge. The old marker is history, not proof of a current
challenge. This procedure does not rewrite that history.

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

Review the three trial results and audit files against the actual hosted
browser state. Confirm exact release identity, complete page coverage,
consistent listing IDs, no denial/challenge or rate-limit response, and that
the browser process and owner-only socket remain private. A reviewed follow-up
would need an explicit, bounded read-only lease for only these saved releases,
with expiration, request limits, durable stop on any new restriction, and a
separate site-access basis. This implementation deliberately does not create
such a lease. A successful no-order fixture run is not live acquisition proof.

No action here places a record order or payment. There are no saved production
monitoring targets, and the hosted schedule remains paused.
