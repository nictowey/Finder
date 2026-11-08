# Private known-listing broker (implementation, not deployed)

This replaces the full-DB grant/relocation approach. The public-facing page has no
DB handle, DB path, notification lock access, credentials or network family other
than AF_UNIX. Its production CLI requires `--broker-socket` and `--broker-user`;
`--database` is no longer accepted. Direct DB backend remains only for offline
fixtures/operator tests, not the production launcher.

## Small architecture and unchanged semantics

- `discogs_phone_review.py`: authenticated form, one-use CSRF, input validation,
  exact release/grades/strict USD price and seller-description evaluation, escaped
  no-store result, canonical manual Discogs handoff. `BrokerClient` is its backend.
- `discogs_review_backend.py`: private original DB safety path extracted without
  changing monitor/watch code. Requires correct DB identity, existing guard and
  page latch; checks aggregate enabled/revision before reservation and after read.
  Exactly one public GET `/marketplace/listings/{id}?curr_abbr=USD`; no credentials,
  proxies/netrc, redirects, retries, catalog fallbacks, notifications or purchasing.
- `discogs_review_broker.py`: bounded private protocol and process lifecycle.
  Broker, not page, owns API reservation, response classification, durable refusal
  and finalization. It returns a minimal API-shaped record of identities, grades,
  availability, reported/original/shipping prices, shipping uncertainty and bounded
  raw comments. Seller identities, profile, URLs, account data and catalog fields
  are excluded. Keeping raw comments through MAX_COMMENTS+1 retains unsupported
  markup/truncation signals; sanitizing never upgrades a qualification.

The original `/var/lib/sniper/rules.db`, all genuine sidecars and
`rules.db.ntfy-send.lock` remain in place. Existing aggregate, health, legacy web,
watch rules and notification consumers do not change. The broker never uses the
notification send lock or changes notification state.

## Fixed protocol

Linux AF_UNIX stream, one request and one response per connection. Each direction
has a 4-byte network-order length prefix followed by strict UTF-8 JSON. Duplicate
keys, NaN/Infinity, invalid types, extra fields and invalid lengths fail closed.
Requests are at most 512 bytes; responses at most 32,768 bytes. No pipelining,
reconnection, retry or returned result cache is implemented.

Exact request schemas:

- `{"v":1,"op":"status"}`: no provider request; ready, denied or capacity status.
- `{"v":1,"op":"read","request_id":"32 lowercase hex characters","listing_id":123,"currency":"USD"}`.

No URL/SQL/path/token/credential input is accepted. Only positive safe-range
integer listing IDs and literal USD are valid. Read responses echo the opaque
request ID; the client validates it and validates the complete minimal payload.
All errors use fixed codes, never exception text or provider payloads.

The broker checks SO_PEERCRED against the configured exact non-root page UID
before reading a frame. The client checks SO_PEERCRED against the configured exact
non-root broker UID before sending a frame. Peer identity is not derived from
protocol fields. Socket directory is broker-owned 0750, socket 0660, group shared
only with page UID. Caddy has no broker group membership. The page cannot replace
the socket. An existing socket at broker startup is not automatically unlinked.

Receive timeouts are absolute across fragmented frames (2 seconds inbound,
30 seconds client response); connect/send timeouts are bounded. Single broker
process handles one operation at a time with backlog2. Every accepted connection
has a production hard25-second SIGALRM deadline that terminates the broker, not a
retry thread. This bounds DNS, slow provider bodies and unexpected storage stalls.
Transport additionally checks elapsed body time (12 seconds plus a bounded
transport operation), and existing HTTP size/timeout limits remain intact.
The service has Restart=no. A timeout/crash requires operator inspection before
restart. Pending HTTP remains durable, not automatically expired or reset.

## Durable no-replay receipts

Before a read can attempt a provider call, the broker commits its opaque request
ID under BEGIN IMMEDIATE + synchronous FULL in the original DB. A reused ID is
rejected across process restarts and different connections. IDs are consumed even
when the later API budget is busy or a provider read fails. The page generates a
new ID only for another explicit submitted form. A complete submitted frame may
finish even after its client disconnects; no automatic retry follows an uncertain
response. Incomplete frames and wrong peers cause no provider read.

The additive `discogs_review_receipts` table contains only request IDs, never
listing IDs, form contents, results or credentials. `discogs_review_broker_metadata`
validates schema version1. Capacity is4096 receipts, intentionally not silently
pruned. At capacity only the page/broker stops; aggregate control/guard/notification
state is not changed. No capacity reset API or automatic cleanup exists.

Capacity recovery: stop the page and broker, inspect the receipt count and current
shared guard without changing either, and request a separately reviewed capacity
increase preserving all existing IDs. Do not delete the ledger, rebuild it empty,
rotate its metadata or reuse an old backup; those actions would permit replay.
A bounded higher cap requires a code/config review and tests before restart.

## Access refusals, interruptions and persistence failures

401/403 sets the existing page-only durable access_denied latch before releasing
the shared reservation. Normal known refusal leaves aggregate enabled state
unchanged and preserves the provider/shared cooldown. New forms and restarts
cannot retry the denied page. No fallback auth or route is attempted.

If recording the refusal fails, the pending API guard remains in place and the
page fails closed. If the process dies after API reservation, both receipt and
pending guard survive. A client disconnect after known successful finalization
loses only its result; reusing its ID is still blocked. There is no reset UI.
Recovery follows existing shared-guard procedure, never automatic expiration.

## Operator provisioning and least privilege (not executed here)

1. Keep page unexposed. Inventory actual runtime UIDs, canonical DB path and every
   secret path using names/modes only. Confirm existing DB-owner UID is `sniper`
   before using that template identity. Preserve existing service flags, watches,
   guards, queues and lock inode. No directory relocation or group widening.
2. In a private approved operator step, take a consistent backup and call
   `initialize_broker_state(connection)` on the verified original DB. It adds only
   page-state/latch plus receipt/metadata tables, never clears existing state or
   enables anything. This is not performed during any web or broker request.
3. Create the dedicated `sniper-review-broker` group; only page UID joins it.
   Broker runs as existing DB-owner UID with this primary group, no credentials
   or EnvironmentFile. RuntimeDirectory creates the dedicated private socket
   directory. Existing monitoring unit configuration/groups stay unchanged.
4. Review/install both templates. Page unit requires broker, only AF_UNIX, no DB
   write path and inaccessible `/var/lib/sniper`. Broker needs original directory
   write access for genuine SQLite sidecars but hides known secrets:
   `/var/lib/sniper/private-auth`, `/var/lib/sniper/private-ntfy.json`, `/etc/ntfy`,
   `/var/lib/ntfy`, `/etc/sniper`, `/etc/caddy`, `/run/caddy-admin`, `/root`, `/home`.
   Missing paths are deliberate fail-closed startup blockers requiring review,
   not reason to remove isolation. Extend this list after inventory as needed.
5. Broker also masks `/proc`, denies signal/ptrace/process-memory/pidfd-getfd
   syscalls, and denies namespaces as defense against interacting with same-UID
   monitor processes. Verify the exact host accepts and enforces these restrictions
   and that Python, SQLite, DNS/TLS work without `/proc`. Do not remove a failing
   restriction just to start the service. Capabilities empty, no new privileges,
   strict system filesystem, no core dumps, no request/output/error logging.
6. Verify all inherited environment is nonsecret before launch. `python -I`
   ignores Python environment configuration; HTTP transport ignores environment
   proxies/netrc; broker main clears its environment before listening. That does
   not replace checking service-manager or pre-interpreter loader configuration.
7. Validate installed exact revision, unit files, actual socket ownership/peer UID,
   page inability to read DB/secrets or create INET sockets, broker inability to
   read hidden secrets/process files or signal monitor PIDs, and untouched existing
   workers. Test actual proxy/page/IPC with offline provider fixtures first.

Residual boundary: broker is a small trusted same-UID process, not table-level
SQLite isolation or a separately proven VM. Its filesystem rights still include
the entire original DB. Other trusted processes running that UID can potentially
impersonate it; hardening reduces but does not establish total same-UID separation.
A compromised page can request bounded allowed reads or exhaust the page ledger;
it cannot send SQL, modify watches, access send lock, or obtain raw DB contents
through this protocol. A broker compromise is a different, greater risk and needs
separate incident handling. No broader security claim is made.

## Required verification

Offline tests cover WSGI-to-client-to-framed-handler-to-real SQLite-to-mock official
GET, strict peer and protocol rejection, bounded frames/deadlines, response loss,
receipt replay/restart/capacity, API cooldown, durable refusal, simulated crash,
projection conservatism, and production template/launcher constraints.
Real socket tests skip explicitly on this executor's permission restriction.
No bypass/escalation is attempted. Static template tests do not prove live seccomp,
mount namespaces, peer credentials, TLS, Caddy, proxy or phone rendering.

Live exposure still requires independently reviewed exact-commit CI, verified
production sandbox and IPC behavior, existing worker invariants, authenticated
phone visual/navigation QA, one authorized known-listing read and manual handoff.
No purchase is part of any gate. No live deployment was performed by this change.

### Offline verification record (2026-11-08)

- Final focused page/wrapper/broker suites:129 passed;1 actual Unix-socket test
  explicitly skipped because the executor denies socket creation.
- Broad Python suite:4,628 passed;41 subtests passed;1 socket-gate skip;18 existing
  socket tests failed with `PermissionError: [Errno 1] Operation not permitted`
  in candidate-pipeline, hosted-catalog and hosted-scheduler suites. No bypass
  or escalation attempted. This is not a full-suite pass. Final isolated input
  validation was rechecked by the focused suite afterward; exact-commit CI remains
  necessary before publication/rollout.
- Python lint and formatting passed. TypeScript typecheck passed.
- Final complete npm run:110 passed. An earlier run encountered a V8 JIT
  allocation assertion in existing judged.test.ts; its isolated rerun passed3
  tests, then the complete npm suite passed. No JavaScript code was changed.
- Independent incremental review found no remaining implementation blocker after
  correcting wrong-workspace checks and masking procfs in the broker template.
  Reviewer independently reran126 focused tests with1 actual IPC gate skipped.
- Browser visual, real peer UID, systemd sandbox/seccomp, live provider and production
  rollout gates remain unperformed. No server or credential access occurred.
