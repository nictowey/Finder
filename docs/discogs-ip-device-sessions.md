# Explicit IPv4 remembered-device option (offline, default off)

This extends the remembered-device implementation with an explicitly enabled
IP-literal origin. It does not provision a certificate, change a route, install
software, create a key/account/session, or activate any production service.
The ordinary DNS-hostname mode is unchanged. Prefer an approved dedicated DNS
hostname when available. Actual TLS, routing, access and iPhone behavior remain
operator/user verification gates, not claims made by unit tests.

## Why an IP can be isolated, and what must be checked

Cookies are separated by the URL host, not by the physical machine. An IP-literal
Sniper URL and a DNS-name IVC/ntfy URL on the same server can be isolated if the
IP-literal Host is served exclusively by Sniper. Cookies do not isolate ports:
an unrelated application must not accept that IP Host on **any** other public
listener, including legacy HTTPS ports. A bare IP returning an empty page today
is neither a certificate nor proof that every other listener rejects it.

The operator must inventory all public HTTP/HTTPS listeners and Host/SNI routing,
then verify exact IP Host rejection everywhere except the approved Sniper HTTPS
site and its restricted HTTP certificate challenge. Keep existing valid DNS-host
IVC/ntfy routes and notification behavior unchanged. Include requests with explicit
ports, alternate host spellings, mismatched SNI/Host and forwarding headers in the
routing review. Do not rely on the middleware alone: a request routed to another
application never reaches it. The template below cannot enforce other services.

## Separate instance: preserve existing Basic access

The host accepts one canonical origin. Keep the existing Basic DNS:8443 web
process, its origin, private checker functionality, Caddy site and
`/run/sniper-review/review.sock` unchanged for fallback/rollback. Do not point the
IP site at that process or change its canonical origin. Run a separately approved
bounded session web instance using the same existing narrow web UID and broker
client group, with its own `/run/sniper-session` runtime directory. Its host still
uses the fixed filename `review.sock`, giving `/run/sniper-session/review.sock`.
The IP Caddy sample points only there. Both clients use the existing broker socket;
neither receives watch-database permissions or new provider/notification access.

`deploy/phone-review/sniper-session.service.example` is an uninstalled template,
not an active unit or an enable script. Its deliberately invalid IP placeholder
prevents accidental use. Review the actual existing UID/groups/package paths,
separate private auth-store provisioning, memory/task/request bounds, Caddy socket
access and secret-denying sandbox before installation. Its allowed writable paths
are only the separate runtime directory and auth store. Session storage is not configured
for any other app. The old Basic process is not reconfigured to read that store.
The same UID is an existing trust boundary, not protection from a compromised
same-UID process; retain the existing service hardening and code controls.

## Explicit application opt-in

Enable the existing device-session gate and both new options:

    --enable-device-sessions \
    --allow-ip-session-origin --session-ip-address "$APPROVED_PUBLIC_IPV4" \
    --origin "https://$APPROVED_PUBLIC_IPV4" \
    --session-database /var/lib/sniper-review-auth/devices.db

Use only the user's own approved address, never a test-fixture or documentation
address. Omit `--session-hostname` in IP mode. Both an explicit IP flag and the
address are required; neither enables device sessions on its own. An address
without the flag, a flag without an address, or an IP/DNS mixture fails closed.

The address must be canonical globally routable unicast IPv4. IPv6, loopback,
private/link-local/shared/documentation/benchmark/reserved/multicast ranges,
integer/hex/short/zero-padded spellings and surrounding whitespace are rejected.
The only accepted origin is exactly `https://` plus the configured address, with
no explicit port, trailing slash, credentials, query or fragment. It uses HTTPS
port 443. HTTP, alternate ports and an explicitly written `:443` are rejected to
keep one canonical origin. The incoming Host must also exactly match the IP.

All existing protections are unchanged: Basic-verified explicit bootstrap,
pre-Basic rate gate, one-use same-origin CSRF, fixed 30-day opaque hashed sessions,
Secure/HttpOnly/SameSite=Strict `__Host-` cookie without Domain, atomic rotation,
revocation, separate private auth state and broker-only watch access. No session
is accepted from URLs. Normal requests still strip cookies before reaching the
existing watch/listing applications. Public IPv4 fixtures in tests are never
contacted or treated as authorized deployment destinations.

## Operator preparation and separate approval gates

1. Verify that the user controls the proposed IPv4 endpoint and approves its use,
   required persistent-access/security changes, TLS provisioning and routing.
   Inventory current Caddy version, configuration, admin isolation, existing
   certificate manager, listeners and service sandbox permissions. Preserve
   reviewed rollback copies. No paid service is assumed or authorized.
2. Confirm a supported certificate issuance route for an IP SAN. The reviewed
   plan uses an official Certbot release with IP support (5.4 or newer) and the
   CA's short-lived IP-certificate profile. Verify the installed version's exact
   options and current CA policy before use. Caddy 2.6.x must not be assumed to
   provide native IP issuance; it can serve reviewed externally managed files.
   Installation, CA account/key creation, agreement acceptance and any persistent
   access changes are separate authorized operator actions, not this patch.
3. Prepare an isolated HTTP-01 webroot. Serve only GET/HEAD for
   `/.well-known/acme-challenge/*` on the exact approved HTTP IP Host. Other paths
   return 404. No app traffic, session bootstrap, private file roots, uploads,
   directory browsing or redirects belong on this challenge origin. Review any
   existing port-80 matcher before adding the static sample. Do not weaken
   unrelated routes or network policy to obtain a certificate.
4. Issue and verify the browser-trusted IP-SAN certificate using that controlled
   webroot. Verify its exact IP SAN, chain, validity and actual HTTPS behavior
   without bypassing certificate warnings. Keep private keys outside repositories,
   artifacts, logs, webroots and app writable paths. Give only the necessary TLS
   service identity read access; the web and broker UIDs must not gain key access.
5. Prepare renewal before activation. IP certificates under the reviewed CA
   profile last about six days, so a monthly/manual renewal plan is inadequate.
   Use the supported renewal timer at least twice daily, bounded failure retries,
   and a reviewed post-renewal hook that validates the complete Caddy config before
   reloading through its existing protected administration path. Test the renewal
   workflow safely and verify the new certificate is actually served after reload.
   Define an operator alert/escalation before remaining validity falls below 48h.
   The timer/hook/monitor are a deployment plan, not installed by this patch.
6. Review `deploy/phone-review/Caddyfile.ip-sessions.fragment`. Its externally
   managed certificate paths and IP/webroot placeholders must be supplied privately
   by the operator. It is a new exact IP site targeting the separate session socket,
   not a second overlapping site for the same origin. The old Basic DNS site stays
   intact. Validate the complete proposed
   configuration with the actual Caddy binary and separately verify every public
   IP-Host route rejects unrelated backends. Retain bounded timeouts, no logging,
   exact path allowlists, replacement/stripping of identity headers, and existing
   Caddy-only private socket permissions.
7. Only after explicit activation approval, enable the app flags and reviewed
   proxy configuration and separate session unit together. Keep the old Basic
   service running unchanged. Keep the existing watch database, rules, global
   monitoring settings, broker boundary and all valid IVC DNS routes unchanged.
   Verify unauthenticated/spoofed/expired/revoked access, canonical Host/Origin,
   logout/revoke-all/Basic recovery, listing prefills and normal watch editing.
   The owner must still test iPhone sign-in, Home Screen installation and reopening.

## Rollback and expiry behavior

If issuance, renewal, Host isolation or TLS validation fails, do not enable this
mode and never ask the browser to ignore warnings. Preserve/restore the reviewed
prior Basic-only service and origin rather than exposing cookie sessions through
a fallback host or port. The old service should already be running unchanged;
stop only the separately approved session instance and remove its IP-site mapping
when rolling back. Never stop the shared broker or notification services as part
of this rollback. If already activated, coordinate the app flags and proxy
rollback, revoke remembered devices when authorized, and verify IVC remains
unchanged. Certificate expiry fails closed at the browser; it is an outage to
resolve, not a reason to downgrade to HTTP. No automatic DNS/IP switching is built.

## Offline validation

    PYTHONPATH=src:. python -m pytest tests/test_discogs_device_session_ip.py \
      tests/test_discogs_device_sessions.py tests/test_discogs_phone_review_server.py \
      tests/test_discogs_watch_manager.py tests/test_discogs_watch_broker.py

Tests cover opt-in combinations, public/canonical address validation, exact HTTPS
origin and Host, unchanged DNS behavior, Basic bootstrap, cookie attributes,
watch CSRF, listing review, logout and static proxy-template contracts. They do
not bind listeners, issue certificates, contact test addresses or verify a real
proxy. Full repository CI remains required before publication.

The session instance must use a distinct immutable runtime under its own release directory, not the old Basic service virtual environment. Preserve the original source and dependencies as well as its process/socket. Replace the example release placeholder only with the reviewed staged version. Optional inaccessible paths use the systemd `-` prefix to avoid failure when an otherwise denied path does not exist.
