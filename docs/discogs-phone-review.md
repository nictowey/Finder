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

## Manual listing intake and reusable links

GET `/listing-review` now accepts optional `listing`, `release`, `under`, `media`
and `sleeve` query parameters. These only prefill visible, editable controls. GET
does not read Discogs, reserve provider budget, modify database state, submit a
review, create a saved watch, or discover listings. The existing backend status
check and owner authentication still apply. Opening a prepared link always mints
a fresh principal-bound form token; the link contains no token or credentials and
does not grant access.

- `listing` accepts exactly one HTTPS `discogs.com/sell/item/` or
  `www.discogs.com/sell/item/` URL with a valid positive ASCII numeric ID. It is
  canonicalized to `www.discogs.com`. Query strings, fragments, ports, credentials,
  alternate hosts/paths, surrounding whitespace and encoded path tricks are
  rejected. Numeric IDs remain supported in the ordinary POST review form, not
  as GET intake links.
- The exact release ID, positive USD item cap and supported media/sleeve grades
  are independently validated when present. The cap remains strictly-under,
  item-only and at most two decimal places. No saved watch data is consulted.
- Queries are bounded to 2,048 characters and five fields. Unknown/repeated
  parameters, malformed percent encodings, non-ASCII characters, controls and
  invalid criteria fail closed before minting a token. The sole blank-value
  exception is optional `listing=`, used by the criteria-only link builder.
- The separate **Prepare a reusable link without reading Discogs** form uses
  GET, contains no CSRF token and cannot perform a review. It returns a copy-ready
  URL and prefilled review form without redirecting. A normal explicit review
  still uses a query-free POST with exact same-origin checks and a one-use token.
  POST requests with query strings, missing/null/foreign origins or repeated
  parameters remain rejected. No JavaScript or additional CSP permission is used.
- Ordinary listing and criteria values enter browser history, bookmarks and
  copied/shared URLs, which may sync across devices. This is disclosed on the
  page. Do not put secrets in a prepared link. No-store does not erase browser
  history. Existing no-query/body logging requirements remain essential. The
  same-origin referrer policy and `noreferrer noopener` Discogs handoff prevent
  external query referrer leakage without weakening the POST Origin requirement.

### Phone steps

1. Open the existing authenticated review page. Expand **Prepare a reusable link
   without reading Discogs** and choose the exact release ID, USD item cap and
   media/sleeve minimums. Leave the listing blank to reuse these criteria for
   different copies, or paste one canonical known listing link.
2. Tap **Prepare link only**. Copy the displayed link or bookmark the page. This
   has not contacted Discogs and does not start monitoring.
3. Reopen the link when needed. Paste a known listing URL/ID into the review form
   if absent, inspect every visible value, and change any criteria as desired.
4. Tap **Review this listing once**. Only this explicit POST can make one public
   listing read. Review its timestamp, uncertainties and seller description before
   using **Open listing on Discogs** for a manual handoff. Refresh/resubmission
   does not repeat the read; start a new form for another explicit review.

This is a manual browser workflow, not an installed phone share-sheet target or
all-seller monitoring. Existing path-only proxy routing and authentication need no
configuration expansion. Local WSGI tests simulate both forms and their submitted
controls; they do not establish physical-phone visual or live-proxy validation.

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

No result or input is persisted by the page itself. Optional prepared links contain
ordinary listing/criteria inputs in the URL; browser history, bookmarks, copied
links and device sync may retain them. No-store responses prevent intentional
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

Prepared-link intake update:

- Page suite: 122 passed. Page, authenticated wrapper and operations suites
  together: 188 passed. The no-JavaScript WSGI proof parses real form controls,
  submits the GET builder, reopens its copy-ready link, verifies no provider calls
  or SQLite changes, then submits the review POST once and rejects its replay.
- Full Python: 4,735 passed, 41 subtests passed, one skipped, and 18 blocked
  failures. All failures are existing AF_UNIX socket tests raising
  `PermissionError: [Errno 1] Operation not permitted`; no bypass was attempted.
  This is not a full-suite pass. Exact-commit CI remains a publication gate.
- Repository Ruff lint/format, TypeScript and diff whitespace checks passed.
- `npm test`: 110 passed on the isolated rerun. The first run, concurrent with
  Python, hit a native V8/Wasm assertion in unchanged `decisions.test.ts`.
- No provider request, live database change, deployment or physical-phone visual
  test was performed for this feature's local verification.

Original page implementation:

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
