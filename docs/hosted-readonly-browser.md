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
- `{"action":"current_listing","release_id":"<decimal ID>","listing_id":"<decimal ID>"}`
  reads one already-open exact item page. `observe_listing` accepts the same two
  IDs and opens one temporary tab at `/sell/item/<listing ID>`, allowing only the
  site's exact `/shop/item/<listing ID>` landing URL. On an item readiness timeout,
  the bridge returns only bounded DOM structure counts and fixed class/test IDs;
  it does not return page text, cookies, or a screenshot. A detected login or
  challenge marker stops access. These structural hints do not establish whether
  a listing is sold or a page is merely late to render. Neither action accepts a
  caller URL or code. The fixed collector verifies the displayed release and
  listing, then returns bounded grades, seller feedback, seller comments, item
  price and displayed shipping. A dollar sign is not accepted as currency
  evidence; displayed shipping is not a destination quote. Tax, mandatory fees,
  physical completeness and checkout total remain unknown. An already-in-cart
  button is recorded separately and cannot qualify as a new addable candidate.
  These actions have
  no cart control, order route, recurring worker, or public listener.

The navigation command allows at most one release load per 15 minutes per running
browser process. It makes no automatic retries. HTTP 429/503 applies the longer of
that interval or a bounded `Retry-After` (up to 24 hours). HTTP 401/403, recognized
login or challenge redirects, and challenge titles stop further loads in the process.
Unexpected redirects, timeouts, missing containers, changed pages and collector errors
stop. Only a complete single-page snapshot may become a baseline; pagination or partial
records return an explicit incomplete code. Individually validated rows from a stable
first page may be included in a **report-only partial observation** with the displayed
range and total, but cannot be promoted to a baseline, interpreted as absence, or
purchase-qualified. The reader never follows pagination links in this mode. The prior
Rocky one-use read returned only `partial_page_unverified` with no DOM rows retained;
the new reporting behavior has not been verified live. Client
disconnect cancels the read and closes the temporary tab. Socket requests are serialized.
On the user's already-loaded **local** Rocky page, a read-only DOM inspection found
25 visible `#pjax_container table.mpitems tbody tr` rows, two matching
`nav.pagination .pagination_total` labels reading `1 – 25 of 47`, a selected
`select[name="limit"]` value of `25`, and ordinary page-2 links. All 25 rows had
distinct `a.item_description_title[href^="/sell/item/"]` links and one exact
release identity link; the page showed USD, EUR, and AUD price currency attributes.
The inspected structure also had seller profile and feedback links in every row.
One seller comment was visually clipped, so its full text remains unavailable to
the strict collector. This was an inspection of a user-opened local tab without
navigation or requests; it did not read the hosted browser or verify the new code live.
Partial market coverage and an individual listing's transaction eligibility are
different questions. A separately rechecked item could someday qualify after exact
identity, full media, seller, condition, destination total, current rule, and checkout
evidence; no such live eligibility or order action is enabled here.
Item-detail navigation has its own 15-minute in-process cooldown and identical
HTTP denial, challenge, redirect and cancellation stops. The socket stops all
further requests after an authentication or access restriction from either
navigation action. These limits do not authorize unattended polling.

## Verification and limits

At commit `74aea6b39ae4a19dbfe63f177fe990189bce6aa6`, the hosted signed-in browser
retained the `tankcogs` session and its pre-existing single cart item after the approved
controller switch. A one-time `current_release` read of release 27048621 returned a
complete empty page with zero offers and purchasing disabled. The cart read reached its
existing item and stopped at `final_quote_unverified`. This evidence does not prove
unattended discovery, any positive-listing path on the VPS, a payable quote, or checkout.
Subsequent one-time hosted `observe_release` reads confirmed a complete zero-offer
page for 27048621 and seven visible single-page offers for 7609839. They did not
establish permission or reliability for unattended collection.

The restricted hosted browser runner now requires an owner-only durable
`/bridge/access-stopped` marker on startup and keeps all ordinary socket reads
blocked across process restarts. A missing marker is created in the stopped state;
an invalid marker prevents startup. An operator-confirmed recovery request for only
release `4204639` can read the already-open page once, without navigation. It
requires a new owner-only `/bridge/operator-rocky-ack` file created after the browser
starts and after the owner confirms reopening the verified page. The browser writes a
fresh private `/bridge/operator-boot` identifier at startup; the acknowledgement's
content binds `operator_confirmed_current_release:4204639:` to that identifier,
followed by a newline. A prior boot's acknowledgement is invalid. Before touching
the browser, the reader exclusively creates `/bridge/operator-rocky-used`, so replay,
concurrent requests and restarts fail closed. Wrong page, changed URL, challenge title,
incomplete marketplace and other uncertainty consume the one use without returning
listing rows. This is a bounded recovery observation, not a general unlock or
authorization for unattended polling. A new challenge may require fresh manual
verification; a solved challenge does not imply lasting access. No recurring
collection is enabled. Discogs' [API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
restrict inconsistent automated scraping and circumvention of technical limits. A
successful one-page read does not establish permission or reliability for unattended
collection. Do not activate a scheduler or bypass a challenge based on this test.

## Approved private socket handoff (stopped)

The owner approved Sniper code reading the signed-in browser through a restricted
private connection. A dedicated `/var/lib/sniper/browser-bridge` host directory
is mounted at `/bridge` in the browser container. It is owned by browser UID
10001, group `sniper` (GID 113), mode `2710`; only the browser UID can create
files there. The fixed socket is owned by UID 10001, group 113, mode `0660`.
The `sniper` group can traverse the directory and connect to that one socket.
The browser profile remains a separate UID 10001 mode `0700` mount, inaccessible
to the Sniper service user. No TCP debugger, generic browser command, cookie
export, cart write, or public listener is added.

The host-side reader validates the exact path, owner/group and parent/socket
modes before connecting. It sends only `observe_release` with a validated exact
release ID or `observe_listing` with validated exact release and listing IDs.
The browser constructs canonical URLs and enforces fixed actions. The optional
candidate reviewer validates identity, source and time after the detail read,
then keeps delivered total and pressing evidence unknown. It is not instantiated
by the active server.
`deployment/HostedBrowserBridge.Dockerfile` layers only these restricted scripts
over the verified local read-only browser image, without installing packages or
changing its profile mount. The browser runner fixes the socket path at `/bridge`.
The handoff is passive: the scheduler remains paused and its kill switch stopped,
with no recurring worker.

Discogs' [Terms of Service](https://support.discogs.com/hc/en-us/articles/360009334333-Terms-of-Service)
exclude collection and use of product listings, descriptions or prices and data
mining/robots/scraping tools from the personal-use license without express written
consent. The [API Terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
also restrict inconsistent automated access to online properties, but the site
Terms apply directly to the normal browser-page route. The owner declined a
support request; no unattended schedule is activated or permission inferred.

Playwright's supported persistent Firefox mechanism is documented in
[BrowserType.launchPersistentContext](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context).
