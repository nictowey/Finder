# Hosted browser observation boundary

The private OVH login browser currently runs Firefox directly, in a dedicated profile at
`/var/lib/sniper/browser-profile`. The profile is not copied into this repository. The
browser is visible through noVNC on VPS loopback port 6080 and a private Mac SSH tunnel.
The profile directory is owner-only (UID 10001, mode 0700); Firefox password saving is off.
Only VPS administrators and the browser UID can access the session data. The separate
Sniper rule editor remains loopback-only with purchasing and listing reads disabled.

## Staged fixed observer

`scripts/discogs_hosted_readonly.mjs` is **not running in the hosted browser yet**.
`scripts/discogs_hosted_browser_runtime.mjs` stages a Playwright Firefox persistent
context for the existing `/profile` mount and attaches the fixed observer at
`/tmp/sniper-read-only.sock`. The launch code does not navigate; the owner opens an exact
release page in the private browser view. It accepts a context inside the same container
and only two requests over a mode-0600 Unix socket inside the container:

- `{"action":"current_release","release_id":"<decimal ID>"}` reads the existing exact
  `/sell/release/<ID>` tab with `collectReleaseMarketplaceDom`. Any positive safe-integer
  release ID is accepted; the code is not tied to one record. It does not navigate or
  schedule reads. Partial, changed, ambiguous, or oversized results stop.
- `{"action":"current_cart","listing_id":"<decimal ID>"}` reads an already-open cart
  with `inspectCurrentCartDom`, requiring the caller's exact listing ID. It does not read
  cookies, address text, payment data, or full HTML. Numeric totals remain displayed
  observations, not verified final payable quotes.

Unknown commands/fields, duplicate matching tabs, wrong URLs, timeouts and exceptions
return sanitized stop codes. Every response has `purchase_allowed: false` and
`stop_required: true`. There is no generic JavaScript endpoint, browser debugger, HTTP
control server, click, add-to-cart, agreement, payment or order method. The socket must
remain inside the private container; any caller would require an administrator-owned
`docker exec` into that container. Unit tests use synthetic pages and verify the exact
collector functions, rejected commands, sanitized errors and socket permissions.
An isolated Firefox test with a synthetic profile confirmed that Playwright can restart
and retain a persistent test cookie. It did not mount or inspect the user's profile.

## Live integration approval boundary

Playwright documents `firefox.launchPersistentContext(userDataDir, {headless:false})` as
the supported persistent Firefox mechanism. Replacing the current direct Firefox process
with a Playwright-owned process would attach the controller to the user's logged-in
profile. No TCP remote-debugging port is required. It would still add programmatic
read access to the authenticated session. Obtain a specific approval for that change
before switching the running service. Preserve the current profile in place; do not
copy/export cookies or save credentials in repository files or logs. Validate the
session and private view after the switch before considering live observations.
The currently running direct Firefox startup is unchanged; integrating the staged
launcher into that entrypoint is the approval-gated step.

The staged observer does not establish permission or cadence for unattended Discogs
release-page collection. No scheduler or recurring collection is enabled. Cart mutation
and final ordering require separate bounded authorization and implementation.

Official mechanism: [Playwright BrowserType persistent context](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context).
