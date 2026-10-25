# Hosted browser observation boundary

The private OVH browser runs Playwright-managed Firefox against the owner's dedicated
`/var/lib/sniper/browser-profile`. No profile, cookies, password, address or payment data
are copied into this repository. Password saving is off. The browser view is reachable
only on VPS loopback port 6080 through an SSH tunnel. The profile is UID 10001 mode 0700.
The separate Sniper rule editor remains loopback-only with purchasing and listing reads
disabled. The browser service uses a read-only container filesystem, drops capabilities,
and runs as UID 10001. The prior image remains available for rollback.

`scripts/discogs_hosted_browser_runtime.mjs` starts a persistent Firefox context and
`scripts/discogs_hosted_readonly.mjs` binds `/tmp/sniper-read-only.sock` inside the
container with mode 0600. There is no TCP debugger, generic JavaScript endpoint, public
control server, cookie export, order control or checkout method. Only a VPS administrator
or the browser UID can connect to the socket. Responses are bounded and always contain
`purchase_allowed: false` and `stop_required: true`.

## Fixed requests

- `{"action":"current_release","release_id":"<decimal ID>"}` reads one already-open
  exact release page with the strict DOM collector.
- `{"action":"current_cart","listing_id":"<decimal ID>"}` reads the current cart for
  the caller's exact listing ID. It cannot add/remove items, change checkout settings,
  accept terms, or submit an order. The live one-item cart read stopped at
  `final_quote_unverified`; displayed amounts are not a verified payable total.
- `{"action":"observe_release","release_id":"<decimal ID>"}` opens one temporary tab
  at the canonical `/sell/release/<ID>` URL, waits for the marketplace structure, runs
  the strict collector, and closes the tab. The ID is a positive safe decimal integer;
  callers cannot supply a URL, request options or page script. The current cart tab is
  left open. The command is on demand only; no scheduler is connected.

The navigation command allows at most one release load per 15 minutes per running
browser process. It makes no automatic retries. HTTP 429/503 applies the longer of
that interval or a bounded `Retry-After` (up to 24 hours). HTTP 401/403, recognized
login or challenge redirects, and challenge titles stop further loads in the process.
Unexpected redirects, timeouts, missing containers, changed pages and collector errors
stop. The collector accepts only a complete single-page snapshot; pagination or partial
records return an explicit incomplete code, with no rows promoted to a baseline. Client
disconnect cancels the read and closes the temporary tab. Socket requests are serialized.

## Verification and limits

At commit `74aea6b39ae4a19dbfe63f177fe990189bce6aa6`, the hosted signed-in browser
retained the `tankcogs` session and its pre-existing single cart item after the approved
controller switch. A one-time `current_release` read of release 27048621 returned a
complete empty page with zero offers and purchasing disabled. The cart read reached its
existing item and stopped at `final_quote_unverified`. This evidence does not prove
unattended discovery, any positive-listing path on the VPS, a payable quote, or checkout.
The `observe_release` navigation command needs its own one-time hosted result before
it is described as live verified; synthetic tests alone do not prove that path.

The in-process interval and access-stop latch reset after a browser process restart.
They are safety limits for on-demand tests, not a durable polling policy. No recurring
collection is enabled. Discogs' [API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
restrict inconsistent automated scraping and circumvention of technical limits. A
successful one-page read does not establish permission or reliability for unattended
collection. Do not activate a scheduler or bypass a challenge based on this test.

Playwright's supported persistent Firefox mechanism is documented in
[BrowserType.launchPersistentContext](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context).
