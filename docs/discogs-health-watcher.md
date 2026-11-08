# Default-off scheduled health notifications

This optional watcher observes the aggregate monitor every five minutes and sends
one debounced degradation notification and, when appropriate, one recovery. It
uses the existing private ntfy configuration. It does not poll Discogs, change
record thresholds, buy anything, enable aggregate monitoring, modify firewall
rules, create credentials, or use email. No installation or activation occurs
when importing the code or deploying these files.

## Important coverage limits

**This is a same-server, same-channel watcher, not an external uptime monitor.**
A lost or powered-off VPS cannot run it. An unavailable ntfy server or invalid
notification configuration cannot deliver its alert. If the main database is
missing, locked or corrupt, the watcher cannot safely persist an incident or
reserve the shared send budget, so it logs a fixed local diagnostic and **does not
send a notification through another route**. These limitations are intentional;
there is no fallback that bypasses a rate gate. Journal output is useful for a
later diagnosis, not a promise of immediate remote warning.

Provider acceptance means ntfy accepted a request, never that the phone displayed
it. The watcher does not inspect phone notification permission, subscription,
battery/background settings or receipts. Recovery reports current checks only;
previous failed or uncertain messages are never replayed or claimed delivered.

## Incident and delivery policy

- The watcher has its own default-off enablement. Its state lives in additive
  `discogs_health_*` tables in the existing tagged database. The existing
  `health_report` command remains read-only.
- Explicit activation baselines all existing failed, uncertain and expired
  aggregate events, including projected expiry. Their lifetime counts remain in
  the diagnostic report but never cause a historical notification storm.
- Current stopped monitoring, no enabled rules, polling errors/staleness, invalid
  supplied configuration, and waiting/backlogged notification work can degrade
  the watcher. Newly observed terminal delivery problems are remembered by a
  private hash and folded into a single debounced incident. Once durably reported
  in that incident, they do not keep it open forever. Their old outbox rows remain
  intact, and recovery does not resolve their uncertain delivery.
- Two bad checks at least 300 seconds apart open an incident. Duplicate/early
  invocations do not advance the count. A gap over 900 seconds resets the
  consecutive-check count, and a reversed clock blocks delivery. Consequently a
  failure may take roughly five to ten minutes after it first becomes visible to
  be reported; the existing freshness threshold is an additional 20 minutes for
  stale evidence. This is not instant fault detection.
- Changing causes accumulate into the existing incident. No recurring reminders,
  escalation loop or routine healthy notification is sent.
- Two clear checks allow recovery only if the degradation was provider-accepted.
  An unsent degradation that clears is cancelled. Uncertain, failed, expired or
  muted degradation never creates a recovery notification. An unsent recovery is
  held while checks degrade again and cannot dispatch until checks are clear
  twice. A terminal or expired recovery closes that incident without replay.
- Ordinary health messages have at least 15 minutes between attempts and at most
  four attempts per UTC day, including retries and explicit canaries. A cap can
  defer or prevent a health notification; there is no override to accelerate a
  test. Pending health notifications expire after 24 hours.
- Every attempt also atomically reserves the **existing shared 90-second pacing
  and 200-attempt UTC daily cap** used by aggregate notifications. The separate
  15-minute health spacing does not delay ordinary aggregate messages for 15
  minutes. A 429 from either path blocks the other through the same `next_send`
  gate. Known rejection alone may retry with the existing bounded backoff and
  attempt limit. Read/write ambiguity, 5xx and expired leases are uncertain and
  never retried automatically. Late 429 responses still update the shared gate
  after disable/lease expiry, but are consumed only once.
- No network call occurs inside a database transaction. A claim is rechecked
  before dispatch. A crash or disable racing a request may leave uncertainty;
  exactly-once delivery is not promised.
- A claimed event binds to one opaque destination. Config rotation never reroutes
  its retry. No topic, token, payload, file path, record interests or raw error
  text is printed. Health notifications contain only fixed operational wording.

## Commands and local diagnostics

Use the existing monitor account, interpreter and protected file paths. The
following paths are placeholders; verify them rather than copying blindly.
Never provide configuration contents in argv or change permissions to make a
failed read pass.

```sh
# Explicit, additive initialization. Remains disabled. No provider calls.
python -m finder.discogs_health_watcher init --database /path/to/rules.db

# Read-only diagnostics, including the current watcher revision.
python -m finder.discogs_health_watcher inspect --database /path/to/rules.db \
  --discogs-app-file /path/to/discogs-app.json \
  --ntfy-config-file /path/to/private-ntfy.json

# Explicit activation using the revision actually reported by inspect.
python -m finder.discogs_health_watcher enable --database /path/to/rules.db \
  --expected-revision 0

# One observation that persists diagnostic state but sends nothing.
python -m finder.discogs_health_watcher tick --database /path/to/rules.db \
  --discogs-app-file /path/to/discogs-app.json \
  --ntfy-config-file /path/to/private-ntfy.json
```

Adding `--deliver` to `tick` permits one due reserved send. It does not enable the
watcher or bypass a cap. `inspect` is read-only; `init`, `enable`, `disable`,
`tick`, `test`, and explicit non-sending `recover` have the explicitly described state writes. Exit 2 means local
failure/evidence unavailable. Inspect the fixed JSON state rather than treating
exit 0 as a delivery receipt or as healthy monitoring.

A repeated `init` does not erase history, disable an enabled watcher, reset limits
or replay incidents. `enable`/`disable` are revision-checked. Re-enabling after
maintenance baselines historical terminal events again; current polling faults
still go through the normal debounce.

## Offline proof and isolated actual canary

Offline tests exercise actual aggregate worker observations, the private mocked
transport, watcher persistence and health diagnostics. Coverage includes the
state transitions above, concurrent claims/checks, restarts, provider failures,
429 in both directions, caps/reset, redaction, invalid/locked databases,
configuration rotation and disabling while preserving aggregate operation.

After the full checks and independent review, an explicitly approved, clearly
labeled canary can prove the real private transport. It uses a separate test
incident, never normal incident state, and never claims the live monitor failed.
It works while normal health monitoring remains disabled. No fake production
clock or changed rate limit is available in the CLI.

```sh
python -m finder.discogs_health_watcher test --database /path/to/rules.db \
  --test-id health-canary-1 --phase degraded \
  --ntfy-config-file /path/to/private-ntfy.json --deliver
```

The test consumes the real shared/health budget and persists a deduplicated test
record. Repeating the same ID cannot resend an accepted or uncertain event.
Scheduled ticks never dispatch test events. A blocked attempt returns `idle`;
wait for the actual gates, then reuse the same ID rather than creating a new one
or changing clocks. An `accepted` response requires separate owner confirmation
of phone receipt.

**One phone-confirmed canary is sufficient for the transport gate**, with the
recovery state machine proven offline. If a second real synthetic recovery is
explicitly requested, use the same ID with `--phase recovered`, after provider
acceptance of the first test and at least the real 15-minute health spacing. It
remains subject to all shared caps/cooldowns. No recovery test is allowed after an
uncertain degradation test.

## Scheduling, activation and rollback

`deployment/sniper-health.service.example` and `.timer.example` are uninstalled
systemd templates. Replace every placeholder with verified existing values and
review them before any install. They create no account, credential or external
monitoring service. The checker intentionally does not depend on the aggregate
worker service being active, since a stopped worker is a condition it observes.
The service account needs access to the existing database directory for SQLite
journals and to the already-provisioned protected configuration files. Do not
relax file ownership or permissions as a workaround.
The service explicitly sets `PYTHONPATH` to the reviewed code directory’s `src`
folder. A shared virtualenv may still be editable-installed against an older code
tree; `WorkingDirectory` alone does not select the candidate package. Verify the
resolved `finder.discogs_health_watcher.__file__` under the exact service account,
interpreter and `PYTHONPATH` before installing/enabling the timer.

Before activation:

1. Run all required checks and independent review against the exact candidate.
2. Run a disposable end-to-end rehearsal and compare existing rule/control data.
3. Stage the candidate separately, explicitly initialize its additive state in
   the existing database, and run the approved actual canary through the shared
   budget. Confirm the phone receipt separately. A schema change is an explicit
   deployment action; never initialize the live database as an incidental read.
4. Confirm no duplicate timer/cron watcher exists. Inspect current health and
   watcher revision, then explicitly enable the watcher. Install/review the
   systemd templates under the existing service account and enable only the new
   health timer. Do not restart/unpause unrelated buying/browser services.
5. Verify one scheduled check and unchanged aggregate rule values/polling. Normal
   healthy operation should produce no health pushes.

Rollback or planned maintenance:

1. Stop/disable only `sniper-health.timer`. Let an active oneshot finish when
   practical; a forced stop during dispatch may leave an uncertain outcome.
2. Inspect the current watcher revision and run `disable --expected-revision N`.
   Pending ordinary health work is cancelled; in-flight work is quarantined.
3. Leave the aggregate worker running. Do not change `discogs_price_control.enabled`,
   rule data or polling. The disable test proves aggregate polling and delivery
   continue normally. Any shared cooldown already earned still applies.
4. Preserve health incident/outbox/seen rows and all shared counters. Do not drop
   tables, reset leases, replay messages, delete the database or clear the budget
   to roll back. Existing aggregate code ignores the additive health tables.
5. On later reactivation, use the then-current watcher revision. Baseline history
   prevents replay; current real failures may create a new debounced incident.

Rollback never asserts that a request already sent was withdrawn. Likewise
canaries are explicit manual work and not queued for automatic scheduled delivery.

## Shared lock, interrupted attempts and provider deadlines

Both supported delivery pipelines hold one nonblocking owner-only advisory lock
beside the database (`DATABASE.ntfy-send.lock`) across claim, final validation,
HTTP and finalization. A busy lock returns `busy` without a quota reservation.
Polling and database reads/writes can continue; no SQLite write transaction spans
HTTP. The lock is never automatically removed, and unsafe ownership, mode,
symlink or hard-link configuration is refused rather than repaired.

Pacing extends conservatively from attempt completion. After a crash releases
the OS lock, unfinished delivery leases are quarantined once without replay and
a **15-minute shared recovery hold from recovery time** is applied. A longer
known provider deadline is preserved. The diagnostic result is
`uncertain_recovery`; the normal incident classifier can subsequently observe
the new uncertain event. This is a deliberate bounded availability tradeoff,
**not a guarantee about a completely unseen Retry-After response**. A lost reply
cannot reveal whether ntfy accepted it or specified a longer deadline.

Valid numeric and HTTP-date Retry-After headers are honored for every response,
including an uncertain 503. Explicit provider deadlines are kept separate from
the capped internal exponential backoff and are never shortened to 24 hours.
A malformed, unparseable or out-of-supported-range nonempty deadline creates a
durable manual-attention hold. Supported finite deadlines extend through year
9999; values beyond that are not guessed. There is no automatic reset or alternate
sender when this happens. `provider_manual_attention` appears in health issues,
`notification_manual_attention` is true, and `send_cooldown_seconds` is null.
Diagnostic JSON remains finite and valid. Polling remains independent.

The long deadline or manual hold is stored in the **existing** shared `next_send`
gate. The manual hold uses SQLite positive infinity, which the c365848 sender's
existing `next_send > now` comparison also honors. Thus rolling code back cannot
silently bypass a gate stored only in a new watcher table. Older diagnostic code
may report unavailable evidence for this sentinel; that is not permission to clear
it. Resolve a manual hold through a separately reviewed operator action after
understanding the provider response, never by resetting counters during rollback.

The lock is single-host and advisory. Both production senders must use the same
verified database and lock protocol. A coordinated cutover must stop/drain the
old aggregate process before starting the updated one; enable the health timer
only once both dispatch paths use this version. **Mixed old/new workers do not
provide the lock guarantee.** External publishers and providers themselves are
outside this process lock's control. A recovery hold survives returning to old
code, but old code does not implement this new mutual-exclusion protocol.

The [staged cutover checklist](discogs-health-cutover.md) gives the exact command
sequence for the isolated live canary, a non-sending `recover` operation, safe
resumption of c365848, and the final coordinated switch. It explicitly prohibits
restoring a pre-send database snapshot.
