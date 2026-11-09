# Private phone review hosting runbook

Prepared code and templates, not a deployment record. The owner explicitly approved
a separate page-only login and HTTPS port 8443 on the existing VPS. No paid service,
ntfy account/ACL change, purchase, Discogs credentials, or old CloudApp integration.

## Origin and proxy boundary

Use `https://vps-a0c5b746.vps.ovh.us:8443/listing-review`.
Never use the ntfy origin's `/listing-review`: a root-scoped ntfy service worker
or another script on that origin could intercept it. A different port is a
different browser origin and service-worker scope; cookies are NOT port-scoped.
This page uses no cookies and Caddy strips Cookie on the upstream request.
Check that port 8443 has no existing service and retain the existing :443 ntfy
site verbatim. No automatic redirect from the ntfy origin is supplied.

Caddy 2.6.2 template: `deploy/phone-review/Caddyfile.fragment`.
Use the existing Caddy process and certificate automation; the existing hostname
certificate also covers this hostname on port 8443. The review template must match the existing explicit ACME issuer exactly;
see `docs/discogs-review-tls-policy.md` for the Caddy2.6 policy-consolidation fix
and firewall-isolated fixture staging. Verify the adapted configuration
preserves the entire existing TLS app and serves a valid chain. Do not copy/export its
private key or weaken TLS. Restrict the firewall addition to TCP 8443 only.

Caddy first calls a private Unix-socket rate gate before password verification.
The gate is not a public route and forwards no authorization/cookies. Its 204
only permits the subsequent Basic check; it does not grant a principal. Caddy
performs HTTP Basic authentication on every GET/POST for exactly one
page-only account. It replaces, never appends, X-Review-Principal using its
verified authenticated account and removes Authorization, Cookie and Forwarded.
Host and Origin are preserved and independently checked by the app. Caddy may
supply its own X-Forwarded fields; Waitress clears them and the app ignores them.
Wrong paths are 404, not a proxy fallback to ntfy. Do not enable Caddy debug or
request/access logging on this site, and inspect inherited global log settings.

The WSGI callback is deliberately ONLY suitable for this Unix-socket deployment.
An HTTP header by itself is NOT authentication. Filesystem exclusivity supplies
the trust boundary: `/run/sniper-review` must be runtime-owned, group
`sniper-review-proxy`, mode 0750; socket mode 0660. Only the Caddy service account
and review runtime belong to that group. Root is implicitly trusted. No TCP mode
is offered. Verify Caddy's supplementary group membership in its running process
(after a controlled restart if needed); editing groups alone does not update it.

## Database isolation gate: broker required

The production page must NOT receive database access. The default full-DB sharing
and relocation plan is superseded by `docs/discogs-review-broker.md`.
Keep the canonical DB at `/var/lib/sniper/rules.db`; keep all aggregate, health,
legacy-web and notification lock paths unchanged. Do not move/copy the DB,
change monitor configuration, or grant `sniper-review` access to the sniper group
or database directory. The page service now accepts only a broker socket, runs
with AF_UNIX only, and has `/var/lib/sniper` inaccessible.

The private broker owns the exact known-listing GET and real shared guard
lifecycle. It exposes only bounded status/read operations over a Unix socket
restricted to the page UID, with reciprocal broker UID verification. No arbitrary
URL, path, SQL, credential, or monitoring/notification operation is accepted.
See the broker runbook for additive provisioning, bounded replay receipts,
refusal persistence, hard request timeout and same-UID residual risks.

## Provisioning sequence (operator, private terminal)

All following steps are gated on verified inputs above. They are not an unattended
installer. Avoid command tracing, secret-bearing shell arguments/history, logs,
public CI, or placing values in this repository.

- Install the exact reviewed commit into root-owned `/opt/sniper-review`, including
  an isolated venv with the project's `cloud` extra (Waitress >=3.0.2,<4). The unit
  uses `python -I` so the package must actually be installed, not PYTHONPATH-loaded.
- Create non-login `sniper-review` UID, groups `sniper-review-proxy` and
  `sniper-review-broker`; add only Caddy to the proxy group. Add only the page UID
  to the broker group; do not add Caddy. Do not add page UID to
  the Caddy or sniper groups. No secrets/environment files are loaded by this unit.
- Follow the broker runbook: privately provision its additive page/latch and receipt
  tables in the existing DB with `initialize_broker_state(connection)`. Do not
  enable aggregate control, reset refusals/guard state, alter watch rules, or
  prune existing receipt IDs. Inventory and sandbox every secret path first.
- Install and verify `deploy/phone-review/sniper-review-broker.service`, using the
  existing verified DB-owner UID. Start only after its static/runtime sandbox,
  no-secrets and private-socket checks pass. There is no HTTP listener.
- Privately generate a strong random page-only password. Hash with Caddy's supported
  bcrypt password prompt, without putting plaintext in argv. Set principal
  `review-owner`. Store only the hash for Caddy in a root-owned private configuration
  file readable by Caddy (not by review UID); never reuse notification credentials.
  The three fragment variables are `REVIEW_ORIGIN` set to
  `https://vps-a0c5b746.vps.ovh.us:8443`, `REVIEW_PRINCIPAL` set to `review-owner`,
  and `REVIEW_PASSWORD_HASH` set to the private hash. Prefer a private rendered
  Caddy fragment, not a broadly readable environment file. Dollar signs in bcrypt
  must be preserved literally. Do not echo rendered config into a shared transcript.
- Verify all live secret paths and extend InaccessiblePaths as needed. Review UID
  must not read ntfy config/DB, existing sniper config, service EnvironmentFiles,
  SSH keys, Caddy config/hash, process environments or other credentials. Confirm
  the page cannot read the DB at all. Verify broker-only access and the separate
  broker runbook's secret/process isolation gates.
- The actual host's systemd 255 lacks BPF support; do NOT rely on IPAddressDeny.
  Complete `docs/discogs-review-admin-isolation.md` before starting the page:
  Caddy admin must move from localhost:2019 to a private 0700 Unix-socket directory.
  The page unit also hides that directory. Verify actual denial from the runtime
  UID and removal of every TCP admin listener. The page requires and starts after
  Caddy, so its private directory exists before sandbox setup. Verify reboot
  ordering; explicitly restart the page after Caddy maintenance if needed.
  Other local TCP services, including
  ntfy, remain reachable and must enforce their own authorization. Audit inherited
  process environment; no manager or wrapper may inject secrets. Core dumps are off.
- Install `deploy/phone-review/sniper-review.service` root-owned into
  `/etc/systemd/system/`. Validate `systemd-analyze verify` and execute
  `systemctl daemon-reload`, then `systemctl start sniper-review`.
- With the current Caddy binary, privately run `caddy adapt` and `caddy validate`
  against the complete candidate config. Check exact host/port routing and route
  order; retain the existing ntfy site and its settings byte-for-byte. Reload only
  after private backend and proxy tests pass; enable the new unit only after QA.
- Add only the approved TCP 8443 firewall rule after authentication/TLS are verified
  through the authorized host-testing route. Confirm no TCP listener from Waitress.

Service starting opens no provider read. A GET can only show a form. Live provider
verification needs an explicit known listing submit, must honor the shared guard,
and must not retry refusal/challenge responses or any ambiguous pending read.

## Credential delivery and revocation

Parent/operator owns secure credential creation and storage. Deliver the new
username/password only through an approved secure handoff, not an ordinary chat,
source code, logs, screenshots, public CI, or this document. Do not claim a vault
saved it without verifying that action. Never request/reuse Discogs or ntfy secrets.
Basic-auth browsers cache credentials and have no reliable page Logout action.
Explain that fact; rotation/removal of this page account at Caddy immediately
revokes subsequent requests. Stop the page service/remove its route to revoke
availability; preserve durable guard/refusal state. Do not alter ntfy access.

## Request limits and required live verification

Waitress uses one process, two threads, eight connections/backlog, 15-second idle
channel timeout, 8 KiB headers, 2 KiB body, no tracebacks/socket-error logs. The
wrapper bounds host requests to 30/minute in memory, including the Caddy pre-auth
gate and the authenticated page request (up to 15 successful browser requests per fixed
minute window). Rejected password attempts consume gate slots before bcrypt. This is a
global cap, so an attacker can temporarily exhaust it; it is not availability or
DDoS protection. Restart resets this in-memory rate window but not DB safety state.
Caddy body limit and upstream dial/write/read/header timeouts bound proxy traffic.
`deploy/phone-review/Caddy-global.fragment` supplies frontend header/body/write/idle
timeouts for only the :8443 listener. Merge it into the existing global block and
validate that listener matching works in Caddy2.6.2. Never change :443 ntfy timeouts.
Real proxy tests must demonstrate that a non-2xx rate-gate result prevents Basic
verification and that errors/timeouts fail closed. No third-party module is used.
These settings do not establish resistance to unrestricted concurrent connections;
review existing ingress resource limits and slow-client tests before exposure.

Offline tests exercise actual WSGI/auth boundary + real temporary SQLite + mock
provider GET and response, replay, wrong identity, Host/Origin/port, forwarding,
body limits, socket directory modes and rate bounds. Template assertions are
static contract checks, NOT an actual Caddy authentication or socket test.
Sockets were previously denied in this environment; this package does not bypass
that restriction. No Caddy binary is available here. Required live gates:

- Caddy2.6 adaptation, TLS and proxy behavior on exact final commit/config.
- Missing/wrong credentials -> 401; only new page credential -> 200; ntfy credential
  alone -> 401; forged/repeated/mixed-case identity headers cannot change principal.
- Wrong Host/Origin/port, CORS/cross-site POST, invalid path/query, oversized/chunked
  body, slow client, duplicate submit and unsupported methods all fail safely.
  Waitress dechunks requests and removes Transfer-Encoding; direct WSGI rejection
  tests do not establish wire-level chunk rejection. Verify bounded dechunking and
  strict frontend framing rather than claiming every chunked request is rejected.
- No other local UID can connect to the socket or read secrets; page cannot access any DB/secret path and cannot open INET sockets; broker
  sidecar modes and shared cooldown remain correct. Verify same-UID isolation
  controls described in the broker runbook.
- Existing ntfy login/subscribe/publish behavior unchanged and root service worker
  cannot control the :8443 page; no cookie/auth payload appears in logs.
- Real phone login, 390px layout, reload/back/repeated submission, interrupted submit,
  one deliberate provider read and manual Discogs handoff. No purchase test.

Do not label the page production-ready until these gates are independently verified.

## Offline verification record (2026-11-08)

- Final focused app/wrapper suites: 73 passed, including fixed socket-only launch
  options. No listening socket or live provider used by these suites.
- Repository Python lint and formatting: passed.
- JavaScript tests: 110 passed. TypeScript check: passed.
- Broad Python run before the final admission-gate/test additions: 4,548 passed,
  41 subtests passed, 18 blocked failures, all socket `PermissionError: [Errno 1]
  Operation not permitted` in existing pipeline/catalog/scheduler suites. This is
  not a full-suite pass or exact-final-commit CI result; rerun in authorized CI.
- Independent review of implemented gate, proxy fragments, unit and runbook found
  no further code blocker for the gated package. All live gates above remain open.
