# Cloud-only Finder foundation (not deployed)

The owner requires cloud operation, not an always-on Mac, and explicitly does not want
orders placed yet. This branch adds a cloud WSGI application and a static container recipe.
It is not a functioning Discogs buyer, and no deployment or paid resource is authorized.

The continuation adds optional read-only cloud polling and a private change log. See
[the monitoring contract](discogs-monitor.md). Aggregate release availability and supplied
listing rechecks are implemented; automatic listing discovery and checkout are not.

## Owner identity and routing decision

Reuse the existing Finder verified-owner auth boundary in `functions/watchlist.ts`:
fixed trusted auth service, verified owner email, and an unexpired server-validated session.
The new `OwnerSession` uses that same session response contract and rejects failures,
redirects, malformed/oversized responses, other owners and expired/unverified sessions.
It never trusts an `X-Owner` header, source IP, frontend claim or forwarded host.

Recommended integration is the same authenticated HTTPS origin/gateway. Existing login
and cookie setup remain outside this change. A new hostname needs explicit approved auth
cookie/login integration before it can work. No new credentials, grants or real sessions
were generated, entered, exported or persisted during development. Session cookies are
forwarded only at runtime to the operator-configured trusted auth service, never logged,
returned in a response, saved to SQLite or passed to Discogs. Finder login and any future
Discogs browser session are distinct; this implementation has no Discogs browser session.

All private HTML and APIs require server verification. Writes also require exact configured
Origin, JSON, and an ephemeral session-bound CSRF token. The app validates exact Host,
sets restrictive CSP/security headers and uses no-store responses. It does not honor
untrusted forwarded-host headers. `/health` returns generic health only and no private data.
Deploy only behind a trusted HTTPS terminator; do not expose its plaintext container port
publicly. Hosting firewall/TLS/access configuration is a separate approval and verification.

## Runtime contract

`waitress-serve --threads=1 --call finder.cloud_app:create_app` is the application entry.
The factory requires deployment configuration (none configured by this work):

- `FINDER_CLOUD_ORIGIN`: exact public HTTPS origin.
- `FINDER_CLOUD_AUTH_URL`: existing trusted HTTPS auth base, serving `/get-session`.
- `FINDER_CLOUD_OWNER_EMAIL`: existing approved verified owner identity, not a new account.
- `FINDER_CLOUD_DATABASE`: existing tagged rules SQLite file on private durable storage.
- Optional `FINDER_CLOUD_LISTING_READ=1`: enables the known-listing adapter only after the
  independent persistent kill switch is released by an authenticated CSRF-protected request.
- Optional `FINDER_CLOUD_MONITOR=1`: creates the public read adapters and one in-process
  scheduler. It remains stopped until the same authenticated kill switch is released.
  Requires durable private storage and a cloud process that stays running. No credentials
  are sent to Discogs; no browser session, email, cart, order or payment route is configured.

No dotenv is loaded. Missing configuration or invalid/legacy database fails startup.
Initialize a new rules file explicitly with the existing RulesStore initializer before
serving. Do not replace or import production data automatically. Container UID/GID is 10001;
the mounted directory must be writable by that identity through an approved deployment.
Single process/replica only for this foundation. Requests serialize behind an in-process
lock, while SQLite retains transactional revisions. Scaling, distributed locks, backups,
restore, schema migration and cloud-volume durability need a separate tested design.

`POST /api/kill` requires `killed` (boolean) and the latest `expected_revision`
from snapshot.control_revision after owner,
Origin and CSRF verification. Default is stopped; it survives restarts and prevents known-
listing reads. Releasing it cannot enable checkout: no checkout route or live adapter exists.
State-changing operations first write a sanitized append-only intent, then a completion
after success (time, route and outcome, no credentials or seller payloads). A failed intent
prevents mutation. A failed completion returns 503 and leaves a durable intent; reload and
reconcile the saved state instead of blindly retrying. This is explicitly not atomic with
the existing rule transaction. It is not a complete production
security log and must not be represented as a payment audit trail.

## Mock execution guard

A separate test-only execution ledger exercises reservations, duplicate suppression,
uncertain outcomes and a default-on kill switch. It has no cloud HTTP checkout route and
no real Discogs adapter. Mock success is not evidence that Discogs checkout works.
Never wire a generic browser object into it and claim that purchasing is enabled.

## Container validation and deployment gates

`deployment/Dockerfile` is a non-root, multi-stage Python/Waitress recipe; the build context
uses an allowlist `.dockerignore`. No credentials are baked into layers. No Docker/Podman
runtime was available in the workspace. The existing offline CI workflow now runs
`bash scripts/check_cloud_container.sh` to build the image and exercise its actual production
entrypoint as UID 10001. The smoke test uses a read-only filesystem, disposable tmpfs data,
no published ports and `--network none`; it checks missing-configuration startup failure,
HTTP health, and rejection of unauthenticated, foreign-host and foreign-origin requests.
No real credentials or provider connections are supplied. CI must pass before claiming
container verification; this does not establish deployment, persistent storage or real login.
Before any deployment, verify and pin the base image digest, resolve/lock dependencies,
choose hosting/storage and confirm cost limits, integrate existing auth under the intended
origin, test real sign-in/expiry/CSRF, verify HTTPS/private port exposure and backup/restore.
Do not attach a real browser profile/session or create access credentials without approval.

Official server reference checked during implementation:
https://docs.pylonsproject.org/projects/waitress/en/stable/runner.html

No order, cart, payment, deployment, production database operation or real authentication
flow was performed. Container-level and real-owner-session verification remain outstanding.


## Verification performed

Owner/session validation tests use synthetic cookies and an HTTP mock. A real Waitress
loopback HTTP fixture verifies authenticated page access, saved rules, stale-revision
rejection and the absent checkout route without connecting to any auth/provider service.
Run it with `PYTHONPATH=src python scripts/check_cloud_owner_flow.py`. This is runtime
coverage, not a container launch, production login test or real checkout test.

Final local Python suite: 3,013 passed and 41 subtests passed. Focused cloud auth/state
coverage has 21 tests; the synthetic execution guard has 58. Independent security review
found and fixed stale kill-toggle and uncertain audit-write handling, with regression tests.
Ruff lint/format and TypeScript typecheck passed. These checks do not validate a live owner
session, Docker image, hosting costs, private ingress configuration or real checkout.
