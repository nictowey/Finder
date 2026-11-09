# Private 30-day remembered devices (default off)

This is offline implementation, not an enabled login service. The existing Basic-only
review/watch installation is unchanged unless an operator explicitly enables the new
host gate and replaces its dedicated-origin proxy routes. No password, certificate,
access grant, real session or production state is created by this patch.

## Required isolated address

Use an approved **dedicated DNS hostname** on standard HTTPS port 443, for example
`https://sniper.example.com`. It must not host IVC, ntfy, or another application on
any port. The CLI requires `--session-hostname sniper.example.com` to exactly match
`--origin https://sniper.example.com` and rejects IPs, nonstandard ports and paths.
This is a configuration assertion: the operator must verify actual exclusivity.
Cookies are not isolated by port. A path-scoped cookie on a shared hostname is not
an alternative security boundary. Do not reuse the shared notification hostname.

Before activation, verify DNS ownership and A/AAAA records, existing proxy routing,
firewall reachability, no conflicts with other sites, browser-trusted HTTPS and
certificate renewal. Do not buy a domain or hosting, change firewall/network policy,
or repurpose unrelated services without authorization. Existing IVC/ntfy routes,
credentials, notifications and services remain untouched. A separate exclusive IP
with a trusted IP-SAN certificate is conceptually another host boundary, but is
not implemented or assumed available by this patch.

## Authentication and persistence

- The existing Caddy Basic owner credential verifies only `/session/login` and
  `/session/start`. The Python host never receives the password or Authorization.
- Login GET displays a consent form. An explicit POST with canonical Origin,
  same-origin browser metadata, exact form fields and a one-use ten-minute CSRF
  challenge creates a new cryptographically random 256-bit opaque token. Basic
  verification alone is not treated as CSRF protection. A prior token is rotated.
- The browser receives `__Host-sniper_session`, with `Secure`, `HttpOnly`,
  `SameSite=Strict`, `Path=/`, no Domain, and a fixed 30-day Max-Age/expiry.
  Activity never extends the expiry. Tokens never appear in URLs, browser storage,
  logs, manifest data, HTML or application/broker requests.
- Only the SHA-256 digest, owner identity, creation time and expiry are persisted
  in a separate SQLite auth database. A copied hash cannot be used as the cookie.
  The web UID owns that file, mode 0600, in a real private mode-0700 directory.
  It never gains access to the watch database. Startup validates an existing
  auth store and never initializes, migrates or repairs it. A missing, malformed,
  inaccessible or wrongly tagged store fails closed.
- At most 32 unexpired sessions are retained. Expired sessions are removed when
  creating a new one. Rotation of a valid current device deletes its old hash,
  checks capacity and inserts the replacement in one transaction, so rotation
  works at capacity and a failed insert preserves the old session. Unknown or
  other-owner cookies cannot free a slot. No automatic reset or eviction of
  unrelated active devices occurs.
- Every normal watch/listing request authenticates the session. Middleware removes
  Cookie and proxy identity headers before calling the existing apps and gives
  their CSRF code a device-specific verified identity. Another device cannot reuse
  its forms. A revoked session fails on its next request; already-authorized work
  in flight is not retroactively undone.
- The existing pre-Basic gate remains at 30 requests per minute per host process
  and bounds password-verification work. Both proxy gate calls and app calls count.
  Session assets count too. This is a conservative global limit, not an IP trust
  scheme or an availability guarantee. No untrusted forwarding header is used.

## Sign-out and recovery

The watch page includes **Device sign-in**, linking to `/session`.
Only GET `/listing-review` may forward a bounded query of listing/criteria fields
(`listing`, `release`, `under`, `media`, `sleeve`) to the existing checker for its
independent semantic validation. Duplicates, unknown fields, overlong queries,
POST queries and queries on every other route are rejected. The public baseline
checker still rejects all queries; the separately deployed private checker can
preserve its valid prefills without copying that private code into this patch.
An expired-session sign-in does not carry criteria through redirects; reopen the
original listing link after signing in. Session tokens are never accepted from URLs.
That page offers **Sign out this device** and **Sign out all devices**. Both require
an authenticated session plus a same-origin POST with a one-use, device-bound
CSRF challenge. GET never revokes. Success removes server-side records and expires
the current cookie. All-device revocation takes effect across Safari and installed
web apps on their next request. Owner Basic login remains available for recovery.

Operator recovery, run as the existing web UID against the isolated auth store:

    python -I -m finder.discogs_device_sessions revoke-all \
      --database /var/lib/sniper-review-auth/devices.db --principal review-owner

If all 32 slots are occupied and every device cookie has been lost or cleared,
Basic login alone cannot create a 33rd session or silently revoke existing devices.
Use the authorized operator `revoke-all` command above, then sign in normally.
If any remembered device remains available, its **Sign out all devices** form is
also a recovery route. There is no new Basic-only mass-revocation endpoint.

This changes access state and must only be run when authorized. Sign-out does not
erase a Basic password already cached or saved in the browser. Someone with that
password can explicitly sign in again. If the password is compromised, revoke
sessions and follow the separately authorized credential-rotation procedure.

On expired/revoked sessions, a page navigation to watches, listing review or device
settings redirects to the explicit Basic login/remember form. API requests return
401 with re-login instructions and never resubmit a save. Reload the page to sign
in, then refresh saved watches before retrying an uncertain edit. A server restart
preserves remembered sessions, but invalidates unsubmitted in-memory CSRF forms.

## Explicit operator enablement plan

1. Finish dedicated-host DNS/TLS/exclusivity verification above. Inventory current
   proxy/service configurations and preserve rollback copies. Keep access logs and
   debug logs disabled; neither cookies nor authorization headers may be logged.
2. Under the existing web UID, provision one separate mode-0700 auth directory.
   Explicitly initialize the absent store once using:

       python -I -m finder.discogs_device_sessions initialize \
         --database /var/lib/sniper-review-auth/devices.db --principal review-owner

   Initialization refuses an existing destination, including a symlink. This
   command does not create a session or password. Do not initialize inside the
   shared watch database directory or give its permissions to the web UID.
3. Keep all existing web-service sandbox restrictions and add only the dedicated
   auth directory to its allowed writable paths. Change its canonical origin to
   the verified dedicated hostname and append:

       --enable-device-sessions --session-hostname sniper.example.com \
       --session-database /var/lib/sniper-review-auth/devices.db

   Watch-management gates and the DB broker are unchanged. A session store without
   the enable flag is an error. No environment variable silently activates it.
4. Use `deploy/phone-review/Caddyfile.sessions.fragment` as the alternative site,
   not as an extra matcher on the old Basic-only/shared origin. It replaces
   incoming owner assertions on Basic bootstrap routes and removes them on all
   cookie-authenticated routes. It removes Authorization and untrusted forwarding
   metadata. Only session middleware receives Cookie. Keep the Caddy-only Unix
   socket permissions and bounded request headers/bodies/timeouts. Validate the
   final complete configuration using the actual installed Caddy version before
   an authorized reload. Do not expose `/_proxy_gate` publicly.
5. Verify unauthenticated, forged-header, wrong-host, cross-origin, expired-token,
   logout/revoke-all and owner recovery behavior through real TLS and Caddy. Check
   broker/web UID separation, no private DB access, no cookie/header logging,
   normal listing review and unchanged saved rules/global monitoring settings.
6. Have the owner sign in and explicitly remember the device on their iPhone.
   Ask them to close/reopen it and test a clearly labeled authorized watch.
   Offline tests cannot verify mobile browser persistence or a production proxy.

## Home-screen behavior

The optional same-origin manifest uses `/watches` as start URL and `/` as scope,
so login, device settings and review remain within scope. A bundled 180px PNG icon
is provided. There is no service worker or offline cache. All private responses
remain no-store. On iOS 17.2+, WebKit documents that cookies are copied from Safari
when adding a website to the Home Screen; subsequent website data is separate.
Sign in in Safari first, then Add to Home Screen, then test reopening. Devices and
browser versions differ: this is a documented intended flow, not an observed
successful iPhone test. Signing out one browser context may leave a copied session
cookie in another context, but server-side revocation invalidates both copies.
Source: https://webkit.org/blog/14787/webkit-features-in-safari-17-2/

## Rollback and offline checks

Remove the session-enable arguments and restore the reviewed Basic-only proxy
configuration/origin. Do not leave cookie routes in front of a Basic-only host.
To invalidate all remembered devices before later re-enabling, use authorized
revoke-all; disabling the feature alone does not erase stored sessions. Preserve
watch data/history and notification settings throughout. Session persistence must
never be copied into a public artifact, backup exposed to a different account, or
repository. No production deployment or user-session creation is part of tests.

Focused validation:

    PYTHONPATH=src:. python -m pytest tests/test_discogs_device_sessions.py \
      tests/test_discogs_phone_review_server.py tests/test_discogs_watch_manager.py \
      tests/test_discogs_watch_broker.py

Run full Python, Node, TypeScript, Ruff lint and format checks before publication.
Caddy configuration validation, real TLS/Unix-peer verification and real iPhone
close/reopen behavior remain separate deployment gates.
