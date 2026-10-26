# Private browser lifecycle and memory health

The browser process is separate from Xvfb, VNC and the private HTTPS viewer. A
working viewer connection, a running container or an HTTP 200 response does not
prove Firefox is alive. None proves checkout readiness.

On October 26, 2026 the kernel recorded Firefox reaching the existing 2 GiB
container limit at 13:04:45 UTC. Firefox was killed while the display and viewer
remained running. The visible outcome was a black screen on both clients. A
browser-service restart restored the saved profile without clearing the access
stop or consumed grants. It did not edit the server-side cart or attempt payment.

The recovered empty browser used about 359 MiB. One normal cart load used about
547 MiB, and the subsequent single enhanced-checkout review used about 634 MiB.
These bounded measurements do not establish a leak rate or identify the page,
developer tools or component responsible for the earlier growth. Do not raise
the limit or reload pages repeatedly as a substitute for diagnosis.

## Implemented safeguards

- The runtime observes unexpected context closure and browser disconnection.
  It closes the restricted read socket and reports only `browser_unavailable`.
- Socket shutdown first aborts all active requests and then destroys clients.
  Delayed operator verification cannot activate a lease after a lost connection.
- Cleanup has a two-second bound. The runner exits nonzero on unexpected browser
  loss, while an ordinary SIGTERM or SIGINT completes gracefully. The deployed
  entrypoint waits for that runner and terminates its display helpers on exit.
- Historical stop files, consumed grants and transaction fences are never reset
  by this health handling. Browser restart is not permission to retry an uncertain
  cart mutation or order, and new browser state requires fresh observation.
- A local memory log contains only fixed cgroup counters: current bytes, peak
  bytes, configured limit and OOM-kill count. It samples initially and once per
  minute, serially, and stops with the runner. Unavailable or malformed counters
  are labelled unknown. At 85 percent of a known limit it labels pressure; this
  does not close tabs, restart the browser, navigate or authorize an action.

No URL, page text, profile contents, cookies, process arguments, address or
payment data is logged. Memory status is diagnostic evidence, not transaction
authority. If the node process itself is killed, it cannot log that event;
kernel/cgroup records and service exit status remain necessary.

## Deployment and next measurement

Source tests do not prove the live browser image has these changes. A future
reviewed image must copy `sniper-browser-bridge-runner.mjs` to the installed
`/app/scripts/sniper-browser-runner.mjs`, include its memory module and retain
the existing private mounts, stop history and network boundaries. Installing it
requires a coordinated browser restart; do not restart during a user handoff.

After installation, observe the fixed counters while using a single existing
checkout tab. Correlate pressure or a new OOM event with operator actions and
process exit, without recording page contents or running a load loop. A rising
counter alone does not establish which component caused growth. Do not claim
unattended payment, recovery or monitoring from this diagnostic test.
