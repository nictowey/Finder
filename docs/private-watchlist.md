# Private collector watchlist

## Goal and scope

One owner saves a Discogs vinyl release, watches eBay listings, and reviews possible pressing
leads. Optional buyer-set item-plus-shipping ceilings and seller condition IDs filter
notifications. These are unverified leads, never exact identity or fair-market-value claims.

The owner explicitly authorized this private product loop on September 23, 2026. This changes
the earlier sequencing rule that deferred all scheduling and interfaces. Public launch,
automatic exact matching, sold-price valuation, and Discogs marketplace access remain outside
this pilot. Discogs catalog attribution is retained; catalog and listing views expire after
six hours. A dedicated production auth email sender is needed before an external-user launch.

## Operator setup

1. Keep the existing Production secrets and `FINDER_OWNER_EMAIL` encrypted GitHub
   **production environment** secret. Only that address can access the dashboard after email
   verification. It is never written into repository files or Actions output.
2. Run **Deploy private watchlist** from `main`. It checks all offline tests, exercises an
   isolated PostgreSQL schema, applies additive migration 1, seeds the existing secret-backed
   target if absent, scans, configures managed Neon Auth, and deploys the new Function.
3. Open the dashboard, sign in with the email code, add up to three watches, and enable browser
   notifications on the desired device. Notification permission requires a user gesture.
4. **Private watchlist scans** runs at minutes 17 and 47. GitHub scheduling may delay or skip
   runs; the UI shows the last successful scan and flags freshness after an hour.

An unset owner email fails closed. Sign-in uses the managed provider's rate-limited email OTP;
the Function proxies only the two needed login endpoints and never returns credentials in JSON.
Cookies are HttpOnly/Secure as supplied by the provider, host-scoped, and SameSite Strict.
Mutations require the configured origin and JSON. Every private request checks the provider
session, its expiry, verified email, and owner allow-list. No external auth SDK runs in the UI.

## Scan and notification semantics

- At most three watches, three searches per watch, and ten items per search on the first
  relevance scan. Refreshes fetch eight newest items per query. Every other refresh also samples
  six older best-match results for one rotating query, through offset 30, then restarts. On the
  alternate refresh, one previously surfaced possible pressing that was not rediscovered gets
  a direct item-detail check. Results and details deduplicate across searches.
- At three watches and 48 refreshes/day, the nominal worst case is 4,464 Browse requests/day:
  `3 * 48 * (3 * (1 + 8) + ((1 + 6) + 1) / 2)`. The initial scans, retries and manual
  workflows add calls. [eBay publishes a default 5,000 calls/day](https://developer.ebay.com/develop/get-started/api-call-limits)
  for Browse methods other than `getItems`; the application-specific remaining quota has not
  been checked. Never raise watch capacity or search depth on the strength of the nominal
  calculation alone.
- The manual **Production eBay Browse quota** workflow reads the official Developer Analytics
  endpoint with the existing Production application token. It reports only resource names,
  limits, remaining calls and time windows in public Actions logs. If the response is missing
  or malformed, it fails without changing watch state or blocking the scheduled scan. Compare
  all relevant windows, other jobs and retries before increasing capacity.
- Each scan records query counts, returned items, provider total, page caps, partial details,
  request attempts and retries in the private watch summary. The inventory cursor includes a
  plan signature and watch revision; after a failed scan it retries the same page. A page offset
  is not a stable snapshot: new or removed offers can shift positions, and the 36-result sample
  per query is far from a complete search. Caps and overall unmeasured coverage are shown in
  the UI.
- A known lead can be refreshed directly even after it drops out of the newest results and the
  older sample. An item that returns 404 or has ended is moved to “Unavailable on recheck” and
  pending alerts are removed. This does not establish a sale. Other old inbox rows still age out
  of the six-hour dashboard window if they cannot be revisited within the request budget.
- Policy `private-target-review-v2` retrieves up to five catalog alternatives per watch using
  one search and at most five additional release requests, reused across every listing in that
  scan. The selected release still costs its existing one request. A full three-watch scan
  therefore uses at most 21 catalog requests before retries; it does not enumerate a complete
  release family. Failed alternative retrieval is shown and withholds notifications.
- Shared or missing evidence may leave another retrieved pressing compatible. Those leads stay
  visible in Possible pressings with a competing-fit count, but do not notify automatically.
  Zero competing fits within a bounded sample does not prove unique identity.
- Leases expire after twelve minutes. Edits invalidate in-flight work through revision checks.
- Inbox identity is watch + marketplace + listing. Dismissal survives rescans and edits.
- One notification event per listing per watch. Raising a ceiling can notify a previously
  ineligible listing; an already alerted listing does not alert again after editing.
- Possible pressing leads with a completed alternatives check and no unresolved retrieved
  competitor can notify without a ceiling. Ceiling notifications additionally
  require known same-currency fixed price and shipping, matching requested destination context,
  and an accepted condition when specified. All delivery totals need checkout verification.
- Unknown shipping, auction prices, failed detail enrichment, stale listings, explicit conflicts,
  and family-only evidence do not produce a qualifying ceiling notification.
- Browser push uses generic text with no listing, seller, price or catalog details. It links to
  the protected inbox. Push retries are capped at three and reuse a stable browser notification
  tag to collapse repeats. Multiple devices can receive the same notification; network delivery
  is at least once, not an exactly-once promise. Invalid subscriptions are removed.
- Without a push subscription the inbox still works. Old pending events expire before delivery.

## Storage and recovery

`watch_store.migrate` is additive migration 1. `finder_schema_versions` records application;
repeated migration is safe. Forward, rollback, dedup, lease and deletion paths have synthetic
SQLite and isolated live PostgreSQL checks. Rollback drops **only the pilot tables**, and loses
watches and inbox state; ingestion and deletion tombstones survive. Stop the scheduled workflow
and remove the watchlist Function before an operator rollback.

New inbox and outbox records have cascading foreign keys to the existing listings. The eBay
deletion endpoint therefore deletes derived review records without changes to its endpoint.
Workers lock existing listing rows before writing, preventing evidence from returning after
deletion. No raw provider responses or real listing fixtures are added to the repository.

The owner allow-list, push subscriptions and VAPID keypair live in the private Neon database,
accessible only to backend service credentials. This pilot extends the previous secret-storage
list to this managed server-only notification key. The private key is never returned by the
dashboard API. Database role credentials and provider secrets remain in encrypted deployment
secrets and Function configuration. Account backup/restore and longer reliability observation
remain prerequisites for expanding beyond the private pilot.

## What still requires evidence

This proves the software loop when the live checks pass. It does not establish pressing-match
precision, recall, immediate marketplace coverage, or market value. Manually check surfaced
listings before buying, especially numbered copies and variants sharing identifiers. DS2 and
the pink/green release are discovery examples, not enough to estimate accuracy across vinyl.

## Verified deployment — September 23, 2026

- [Deployment run](https://github.com/nictowey/Finder/actions/runs/35820533927) passed:
  239 Python tests, 15 Node tests, lint, format, type checks, isolated live PostgreSQL migration,
  rollback, leases, deduplication, deletion cascades, deploy and anonymous-access checks.
- The first live target scan in the preceding run completed with 22 inbox rows. Repeated deploy
  work respected the due time and performed zero additional scans / inserts.
- [Recurring worker smoke](https://github.com/nictowey/Finder/actions/runs/35820668287) passed.
  No device subscription exists yet, so no actual push was sent.
- [Owner activation deployment](https://github.com/nictowey/Finder/actions/runs/35821058580)
  passed, including the isolated PostgreSQL check, owner configuration, Function deployment,
  anonymous-access denial, and CSRF checks. The owner signed in with an email code; the hosted
  dashboard, saved-watch creation, inbox filters, and refresh were inspected while signed in.
- The owner saved the pink/green Don't Be Dumb and numbered DS2 pressing targets. A
  [two-watch worker run](https://github.com/nictowey/Finder/actions/runs/35821632354)
  completed both scans and inserted 20 new inbox rows. The dashboard showed one provisional
  possible-pressing lead for each, including the known DS2 example. All sampled search pages
  hit their caps. This is a discovery check, not pressing proof, measured recall, or a deal claim.
- No device subscription exists yet, and the notification dispatcher delivered zero pushes.
  The owner must enable notifications in their own browser to verify device delivery. Saved
  ceilings and destination remain unset; the inbox still shows leads at any price.
- The original eBay deletion endpoint still rejects unsigned requests with HTTP 412.
- A direct database connection is required for the isolated schema integration check; the
  ordinary application retains its existing pooled connection.
- [Inventory reconciliation deployment](https://github.com/nictowey/Finder/actions/runs/35855743741)
  passed 256 Python and 16 Node tests, isolated PostgreSQL checks, and private access checks.
  Its two due watches both completed with zero failures and one new inbox row. The signed-in
  dashboard showed an older sample for each watch, and the DS2 newest query hit its cap. The
  prior known DS2 lead was not visible in this first sample's fresh six-hour inbox window; the
  alternate direct known-lead recheck has not yet been observed live. Coverage and identity
  remain unmeasured. The previous successful scheduled scan was around 10:00 UTC and this
  deployment scan around 11:39 UTC, so a 30-minute freshness promise is not yet supported.
