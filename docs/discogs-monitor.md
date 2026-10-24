# Read-only Discogs cloud monitoring

Continuation of PR #107 from `a4a3768458fdebc3436427303db9d94ba5e4c13a`.
This is **not yet a working release-link-only sniper**. A permitted, reliable source of
individual listings by release is still missing. No live provider calls from the new worker,
cloud deployment, production database changes, order or payment have been performed.

## Implemented behavior

1. Enter an exact release ID/link and save minimum media/sleeve grades, seller positive
   percentage, feedback count, currency, destination and an inclusive all-in ceiling.
2. An explicitly enabled cloud worker polls public `GET /marketplace/stats/{release_id}`
   with the target currency at a nominal ten-minute cadence. It records the reported count
   and lowest **item** price, not listing identities or a delivered total. An unchanged count
   does not prove no new listing appeared. A count decrease does not prove a sale.
3. Optionally save known listing IDs/links for five-minute public listing rechecks. The
   first successful read establishes only their reported release association. A wrong
   release, unavailable item, missing/below-minimum grades or seller information, unsupported
   currency, shipping restriction or item price already above the ceiling blocks review.
   Known missing playable media and unknown media-omission checks also block review; grades
   never establish that all records are present.
4. Only a listing with an acceptable disclosed-media-omission assessment is shown as
   **Needs delivered total**. Scheduled listing polls remain one GET and do not fetch catalog
   metadata, so their media-omission result stays unknown unless an authorized integration
   supplied fresh authoritative metadata. Explicit known-listing review may read the exact
   catalog release using the same durable per-request budget; see
   [record-set assessment and metadata integration](private-discogs-rules.md#playable-record-sets-and-disclosed-omissions). API-reported shipping is
   not a destination-confirmed quote. Shipping, tax, mandatory fees and physical pressing
   evidence remain unverified. `total: null`, `qualifies_all_in: false` and
   `purchase_allowed: false` are invariant for these live-read triage results.
5. A private in-app change log records normalized snapshot updates, with unchanged snapshots
   suppressed. It is visible on Refresh. No email monitoring, mail permissions, email alerts,
   external push setup, automatic cart, orders or payments exist.

The UI shows configured/stopped/running status separately and exposes Start/Stop cloud reads.
Source links point back to the exact release or listing. The worker runs on the server,
independently of an open page or the owner's computer. It only runs after an approved cloud
deployment is configured and enabled; this implementation has not been deployed.

## State and failure behavior

The existing tagged rules database gains additive `discogs_monitor_control`, `discogs_poll`
and `discogs_monitor_events` tables. Legacy databases still fail the existing application-ID
guard. No production data is imported or migrated by this work.

- One request per scheduler tick. Work claims, next due times and a three-second minimum
  request spacing persist before the request, including across worker restarts.
- Paused/owned targets and the default-stopped persistent kill switch prevent new polls.
  Rules/control changes or removing a tracked listing discard an in-flight response.
- No database transaction spans network I/O. The application still targets one cloud
  process/replica; database claims also guard against accidental duplicate worker polls.
- A failed request remains unavailable, never an empty successful inventory. HTTP 429
  cooldown honors bounded integer Retry-After; other failures back off. No immediate retry,
  redirect following, key rotation or fallback browser transport occurs.
- Old snapshots are visibly stale; listing eligibility fails closed after its nominal
  refresh window. Payloads and change-log events older than six hours are removed even
  when an authenticated snapshot is read while polling is stopped. The latest 100 events
  and at most 100 supplied listing references are retained. References remain for rechecks;
  expired seller/price/condition payloads are not returned.
- Tracking changes use both rules and monitor revisions. Failed/uncertain saves require
  Refresh before another change. Owner identity, exact Host/Origin, CSRF and existing
  no-store/security headers cover all new endpoints.
- Provider responses are normalized and bounded. Audit rows contain only operation/time/
  outcome, never cookies, owner addresses, listing IDs, sellers or raw provider payloads.

The worker is a rate-bounded poller, not a real-time or exhaustive discovery guarantee.
Rate limits, downtime and queue length can delay its nominal cadence.

## Runtime and cost gate

The existing WSGI factory accepts `FINDER_CLOUD_MONITOR=1` to create public read adapters and
one scheduler thread. This flag does not release the kill switch. Missing owner/auth/origin/
database configuration still fails startup. New databases must be initialized explicitly.
No real environment values, credentials, access grants, browser profiles or sessions were
created or persisted. `.env.example` contains empty placeholders only.

No automatically deployed or permanently running workflow is added. Tests run offline using
synthetic responses and sessions. The existing production jobs remain disabled.

Render's free web tier is unsuitable for this Python/SQLite scheduler: it sleeps without
inbound traffic, loses filesystem state on sleep/restart/redeploy, and cannot attach a
persistent disk. Free Render Postgres expires after 30 days. Do not use keep-alive traffic
or an unbounded public Actions artifact to pretend this is a private, durable free service.
An always-on paid service/disk or a separately designed free scheduler plus durable storage
needs a concrete deployment/cost review. Ask the owner before paid hosting or persistent
credential configuration; nothing has been purchased or configured here.

Official hosting limits checked October 24, 2026:
https://render.com/docs/free

## Listing discovery and browser gate

No documented, permitted release-listing enumeration transport has been established in this
environment. The worker never invents `/marketplace/search`, guesses listing IDs, crawls
seller inventories, scrapes protected pages or falls back to email monitoring. The public
release-statistics endpoint cannot provide per-listing conditions, sellers or delivery costs.

Discogs' documented native Saved Searches support exact Wantlist release, media/sleeve,
minimum seller rating/feedback and item-price filters, with Realtime site-bell notifications.
Personal Email can be disabled separately. Those features are a possible no-email discovery
route, but no saved search, Wantlist or account setting was changed. Their item-price filter
is not verified all-in pricing, and no speed/completeness guarantee has been established.

During this continuation the provided managed cloud browser route reached a public release
page and exposed release identity and sort controls. Sign-in then showed Cloudflare's
"Performing security verification" bot-screening page. The attempt stopped without a retry,
challenge interaction, alternate route or recovery of the previously blocked browser.
No authenticated listing/cart controls or current checkout form selectors were verified.
The owner's login offer does not override this restriction or authorize persistent sessions.

The supplied earlier cart screenshot remains evidence only of missing shipping/address
information and an unknown final total. `Place order and pay now` is a prohibited stop
boundary. No checkout adapter or HTTP endpoint has been added.

Official references:

- https://www.discogs.com/developers/#page:marketplace
- https://support.discogs.com/hc/en-us/articles/26355982507149-Using-Filters-Saved-Searches
- https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use
- https://help.openai.com/articles/20001280-using-cloud-browser-in-chatgpt#when-a-website-blocks-the-task

This application uses Discogs' API but is not affiliated with, sponsored or endorsed by
Discogs. 'Discogs' is a trademark of Zink Media, LLC. Data provided by Discogs.

## Verification scope

`tests/test_discogs_monitor.py` exercises fixed public GETs, validation, conditions/seller/
price gates, unknown delivered costs, wrong releases, persistent cooldown, deduplication,
pausing/ownership, concurrent claims, in-flight changes and six-hour expiration. The cloud
DOM fixture uses the actual authenticated page template with synthetic fetch responses;
it exercises stop/start, tracking/removal, aggregate-vs-listing labels and uncertain saves.
These checks validate application behavior, not live Discogs discovery or checkout.

Selected-release rendered snapshot/diff logic is documented in [discogs-release-snapshots.md](discogs-release-snapshots.md). It accepts bounded observations only; it does not enable continuous page acquisition.
