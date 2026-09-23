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

1. Keep the existing Production secrets. Add `FINDER_OWNER_EMAIL` as an encrypted GitHub
   **production environment** secret. This address alone can access the dashboard after email
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

- At most three watches, three searches per watch, and ten items per search. Details dedupe
  across queries. At full use this is at most 4,752 Browse requests per day, excluding other
  operator workflows and retries. Actual quotas must be checked before increasing capacity.
- Initial discovery uses relevance; subsequent scans use newest first. Page caps are visible.
- Leases expire after twelve minutes. Edits invalidate in-flight work through revision checks.
- Inbox identity is watch + marketplace + listing. Dismissal survives rescans and edits.
- One notification event per listing per watch. Raising a ceiling can notify a previously
  ineligible listing; an already alerted listing does not alert again after editing.
- Possible pressing leads can notify without a ceiling. Ceiling notifications additionally
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
