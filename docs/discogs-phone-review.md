# Protected known-listing phone review

Implementation only; not deployed. `finder.discogs_phone_review.PhoneReviewApp`
provides exactly `/listing-review`. The separately reviewed hosting wrapper and
gated templates are described in `docs/discogs-phone-review-deployment.md`. It has
no credentials and no dependency on CloudApp or its control/mutation model.

## What the page does

Enter a known Discogs listing ID/canonical URL, an exact numeric release ID, a
positive USD item cap with at most two decimal places, and minimum media/sleeve
grades. A submit makes at most one official public GET
`https://api.discogs.com/marketplace/listings/{id}?curr_abbr=USD`.
There are no retries, redirects, catalog fallback, discovery, saved watches,
notifications, email, cart, checkout, orders, or payment actions.

The page reuses the preview's rounded-UP price comparison, strictly below the
cap. Seller-reported release/grades/availability and deterministic description
checks are presented with an observation timestamp. Any unknown grade, unsupported
description, disclosed omission, damage or mismatch blocks an item-criteria match.
No claimed match establishes physical completeness, authentic pressing, seller
reliability, destination shipping, tax, fees or delivered total. The cap is item-only.
The canonical Discogs link hands control to the owner; current details must be
checked there. The form does not accept checkout or purchase instructions.

## Authentication and deployment contract (requires separate authorization)

The constructor requires `database` (absolute path to the EXISTING aggregate
SQLite database) and `origin` (one canonical HTTPS origin, no trailing slash).
`authenticate(environ)` defaults to absent/deny and must return the independently
verified owner's stable principal, or None. Exceptions also fail closed.
There is deliberately no default header authentication, login, token provisioning,
Discogs app auth, or new persistent access. A constant principal is only appropriate
inside offline tests, never a production wrapper.

Before exposing this page, approve and independently review a separate page-only
auth boundary on the existing VPS. The hosting wrapper/proxy must:

- Bind WSGI exclusively to loopback or a private Unix socket. Do not expose its
  port. Permit only the exact route through the authenticated HTTPS proxy.
- Verify the owner at every GET AND POST. Reject other authenticated users too.
  Strip client-supplied identity headers before adding a verified upstream
  assertion. Configure the callback only to trust the protected boundary; never
  turn an arbitrary incoming header into a principal.
- Preserve/validate the canonical Host and Origin. The app does not trust
  Forwarded/X-Forwarded headers. POST requires its exact HTTPS Origin and a
  principal-bound, unpredictable, one-use form token.
- Use one WSGI process with bounded worker threads and request/body timeouts.
  Tokens are in-memory, expire after ten minutes and are bounded to 128 open
  forms. Multi-process routing is deliberately not supported; tokens routed to
  another process fail closed. Use a bounded server queue and proxy request rate
  limits. Authentication/logout and session revocation belong to the host.
- Disable request-body, query, result, cookie, authorization and exception-payload
  logging/tracing. The module emits no logs. Configure access logs to avoid
  sensitive headers and rejected query strings. Disable proxy caching and HTML
  transformations; preserve no-store, same-origin Referrer-Policy, CSP and nosniff headers.
- Give the runtime no email/Discogs credentials, no purchase adapters and no
  broader app imports. The outbound reader ignores environment proxies/netrc.
  Test provider connectivity without weakening TLS or challenge/access blocks.

## Shared API budget and least privilege

Every submitted request opens and closes its own SQLite connection, using mode=rw
(never creates a replacement DB), synchronous=FULL, and the existing
DatabaseReadBudget. It never initializes, repairs or clears guard state. The
existing DB must already have the correct workspace application ID, initialized
shared guard and monitor-control tables. The aggregate enabled control must be on;
its revision is checked inside reservation and after the response. A stop or
revision change suppresses the result, and the page never enables that control. Confirm the path is the same database
used by the aggregate monitor, not a copy, with durable local storage.

Only the pending-token / pending-time / known-deadline columns of the read guard,
monitor-control.next_request, and the page-specific access_denied latch can be
updated through the request connection's SQLite authorizer. It cannot alter watches, rules, notification settings or schema.
No transactions are held over network I/O. Concurrent requests share the durable
guard; cooldown, locks, invalid state, or an unknown interrupted request stop reads.
An unresolved pending guard must follow the existing manual recovery procedure;
this page never resets it, even after a restart or long delay.

IMPORTANT: SQLite filesystem write permission still grants process-level access
to the whole database. The SQL authorizer limits this code path, not an exploited
Python process. True OS-level table separation is impossible in SQLite. Do not
misdescribe this as a read-only DB service. Production approval must explicitly
accept a tightly sandboxed page process with shared-DB write access, or introduce
a separately reviewed local budget broker so the page does not receive that
filesystem capability. The latter is not implemented here. A gated dedicated-UID production service template is now supplied separately;
it must not run against a directory containing notification secrets.

## Page-only durable access refusal

Before installing the route, an explicitly approved operator provisioning step must
call `initialize_review_state(db)` on the existing shared database connection. This
adds only `discogs_phone_review_state(id, schema_version, access_denied)`. It is
idempotent but never repairs malformed state or clears an access-denied latch.
The request handler does not initialize schema and fails closed if the table is
missing. No auth credential or permission is provisioned by this helper.

HTTP 401/403 without or with Retry-After sets this page-only latch durably before
releasing the existing shared request reservation. Later forms/restarts do not
retry. GET reports that page reads are stopped and does not mint a form; old forms
check the latch inside the reservation transaction. Normal refusal preserves the
shared cooldown and clears the completed pending reservation; it does not disable
the aggregate monitor or assume its stats endpoint has also been denied. There is
no reset UI or automatic expiry. Any future recovery requires operator review,
new evidence/authorization and a separately reviewed recovery action.

If the latch cannot be committed, the page returns a safety failure and leaves
the shared reservation pending because durable refusal recording is uncertain.
That persistence failure blocks other shared readers too until operator recovery.
Tests exercise this exceptional case separately from a normal known refusal.
The response is only finalized after classification/body processing, so a process
interruption retains conservative pending state instead of losing the refusal.

## Repeated, interrupted and stale flows

Form tokens are consumed before validation/read; a repeated POST cannot repeat
an API call. Browser refresh/Back resubmission returns an expired/already-used
message, never a saved result or automatic retry. Separate tabs get distinct
one-use tokens but still share the database cooldown. Failed reads require a new
form and another explicit submit. Restart discards tokens and all displayed
results; reconnecting must start a new form. A disconnect after reservation does
not clear the guard early. If response headers never arrive the durable pending
state remains for operator review. If headers did arrive and the process finishes classification, the normal durable
provider cooldown is preserved even if body parsing fails. A crash before durable
finalization leaves conservative pending state for operator recovery.

No result or input is persisted by the page; no-store responses prevent intentional
HTTP caching but cannot promise a browser never retains a live/history screen.
Every result is timestamped and explicitly not auto-refreshed.

## Offline verification and remaining gates

`tests/test_discogs_phone_review.py` drives WSGI GET -> form token -> POST ->
mock official transport -> real temporary shared SQLite guard -> rendered handoff.
It covers duplicate/concurrent/interrupted/expired/restarted flows, shared cooldown,
invalid inputs, authentication absence, principal mismatch, Host/Origin/CSRF,
provider restrictions, injection, strict rounding and description exclusions.
It makes no live HTTP request. Run full repository checks before publishing.

Mobile layout uses a viewport meta tag, single 600px-max column, full-width
44px+ controls, 17px input text, wrapping evidence and no JavaScript. This is not
a claim of physical-phone/browser visual QA. Remaining deployment gates include
independent code/auth review, real proxy spoofing tests, authenticated phone
rendering and interrupted navigation, exact shared DB path/permission verification,
controlled provider read and end-to-end handoff. No purchase test is authorized.


The schema addition is private SQLite-only and does not affect PostgreSQL watch
schemas. No migration was applied to a live database. Production provisioning, OS-level DB privilege review, and browser/mobile
validation remain unperformed. See the deployment runbook for current owner
authorization and remaining integration gates.

### Local verification record (2026-11-08)

- Focused page suite: 40 passed, using real temporary SQLite plus mock transport.
- Full Python run on final implementation: 4,367 passed, 41 subtests passed,
  18 blocked failures. All 18 raise `PermissionError: [Errno 1] Operation not
  permitted` creating sockets in existing candidate-pipeline, hosted-catalog,
  and hosted-scheduler tests. Reviewed escalation did not enable sockets.
  Four additional defensive tests were added afterward and included in focused40.
  This is NOT a full-suite pass; exact-commit remote CI is still required.
- `ruff check src tests scripts`: passed.
- `ruff format --check src tests scripts`: passed.
- `npm test`: 110 passed.
- `npm run typecheck`: passed.
- Headless Chromium at 390x844 using synthetic local HTML could not start because
  this environment denies its local socket. No visual screenshot or phone pass
  is claimed. Do not bypass that restriction; validate in an authorized environment.
