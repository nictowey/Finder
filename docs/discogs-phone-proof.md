# One-shot actionable phone proof

This is an explicit operator diagnostic, not a background publisher or a new watch.
It does not poll Discogs, discover listings, change credentials, or change live rules,
samples, or ordinary outbox records. Only a diagnostic journal and the shared delivery
budget are changed. Disposable watch state and retained evidence live in an owner-only
directory. No real destination, watch ID, or credential belongs in commands committed here.

## Required operator gates

Independent code review and offline checks must pass before use. The operator must verify:

- Authorization covers exactly one clearly labeled test for the selected release.
- Both deployed aggregate and health publishers use the reviewed `notification_lock`,
  shared budget, and max-only `recover_abandoned` implementation. Record their actual
  deployed versions and process configuration outside public logs. This helper does not
  claim to prove deployment compatibility from a command-line checkbox.
- All publishers share the exact same canonical source DB path and lock. No alternate
  database, symlink/hardlink alias, older unsupported publisher, other host, external
  sender, or concurrent operator/settings change is permitted during this procedure.
- The evidence parent directory already exists, is private to the operating account, and
  is on durable storage. Source database storage must honor SQLite FULL synchronization. OFF/MEMORY journal
  modes are rejected, never repaired; effective FULL synchronization is verified.
- Choose one fixed unique non-secret proof ID ONCE. It identifies this authorization,
  not a process invocation. Never substitute a new ID to bypass a previous stop.
- The selected release already has an enabled, eligible rule and a fresh successful
  stored observation. If unavailable, stop. Do not fabricate a sample or perform a new GET.

Using the verified candidate checkout/environment, the eventual single invocation is:

```sh
python -m finder.discogs_phone_proof \
  --database "$VERIFIED_CANONICAL_SOURCE_DB" \
  --private-config "$EXISTING_PRIVATE_NTFY_CONFIG" \
  --evidence-root "$PRIVATE_DURABLE_PROOF_DIRECTORY" \
  --proof-id "$FIXED_APPROVED_PROOF_ID" \
  --release-id "$APPROVED_RELEASE_ID" \
  --authorize-one-test-send
```

Without the authorization flag the API/CLI does not read configuration or send. This
command is a procedure template, not evidence that approval or live execution occurred.
Pass the config PATH only. Never print its contents, token, topic, or HTTP request body.

## What happens

The helper holds the canonical source notification lock continuously. It fails closed on
existing cooldowns/holds, full or invalid budgets, clock reversal, pre-existing pending/
retry-wait work or unfinished live leases,
incompatible health/journal schema, source inode changes, and missing/stale/ineligible
observations. It does not call recovery to make preconditions pass.

The selected rule's economic settings and actual stored observation are passed through
candidate matcher/claim/delivery-current code in an isolated fixture. Only its public title
is prefixed `TEST ONLY: ` and its fixture initial alert is enabled. The original observation
timestamp is retained; there is no invented earlier price/count or synthetic change claim.

One durable source transaction creates a truthful diagnostic journal reservation, consumes
one daily send attempt, and places `next_send=+infinity`. Confirmed commit and readback are
required before the single actual private sender call. A commit/readback exception never
permits sending or retry. The source lock remains held through fixture.finish and source
finalization. Regular aggregate and health sends are blocked by the lock and then the fence.

Only definitive accepted/rejected results without manual-attention flags may release the
owned fence, in the same uninterrupted invocation and lock session. Token/state/budget
ownership must still match. Journal outcome, retry delay/manual flag, hold reason, applied deadline and release
commit atomically. A fixture finish result inconsistent with the provider outcome also
retains the fence. Rejection is
terminal for the diagnostic and incurs at least 900 seconds of shared backoff. Success
retains at least 90 seconds from completion. Explicit provider deadlines and retry delays
are preserved; a midnight crossing does not erase the consumed reservation-day budget.

Unknown outcome, failed/redirect response, malformed Retry-After, clock reversal, crash,
fixture persistence failure, source persistence failure, or changed ownership leaves the
fence intact. The API has no resumed finalizer, retry command, or automatic cleanup. A crash
before source commit may leave only the evidence directory; even that directory is not reused.

## If execution stops or reports delivery_fenced

All supported notifications may be paused indefinitely. This is a real operational blocker,
not a successful phone proof. Keep the fixed proof ID, source journal, and fixture directory.
Do not rerun, change IDs, clear infinity, restore a database backup, delete evidence, or replay
any uncertain attempt. Arrange separately reviewed reconciliation of the exact state and
provider outcome. An ambiguous final commit must be inspected; do not assume it rolled back.
A prolonged fence can legitimately produce a health-backlog incident; do not suppress or
fabricate health events to hide it.

Provider acceptance does not establish phone receipt. Ask the user whether the TEST ONLY
alert arrived and whether its link opened the selected release marketplace page. Record
phone presentation and tap behavior as unverified until confirmed.

## Offline verification

`python -m pytest -q tests/test_discogs_phone_proof.py` uses temporary source/fixture databases,
synthetic private configuration and `httpx.MockTransport`. Socket creation and DNS are forbidden.
Tests cover crash/persistence boundaries, no replay, source-content preservation, provider
outcomes/deadlines, ownership, locks, old publisher guards, midnight, and missing observations.
They do not constitute a live phone proof or authorize running the template above.

## Explicit fixture-only threshold test

Only with separate explicit authorization for this variation, add
`--test-item-ceiling "$APPROVED_TEMPORARY_TEST_CAP"` to the same one-shot command.
Keep the already assigned proof ID; this is not permission for a second attempt.
The optional amount must be positive ASCII decimal text with exactly two fractional
digits and no currency symbol, grouping, whitespace, exponent, or rounding.
The currency is the selected source rule's validated currency; conversion is not supported.

This variation can exercise a fresh actual minimum that does not meet the saved source
cap. It changes the ceiling only in the disposable fixture, and the actual observation
must meet that temporary cap. Source rule/sample integrity and fingerprints, freshness,
positive count, shared budget, source lock, and all fence gates are unchanged. It cannot
make up a minimum or replace an unknown one. The original source cap is never written.

Both the canonical title and first body line begin with
`TEST ONLY: TEMPORARY TEST CAP <currency> <amount>;` before any record name. The usual
canonical Watch line repeats the exact temporary cap. Placing the label first prevents
long-title truncation from hiding the test context; the canonical payload fingerprint
includes this context. The isolated fixture retains the actual price, count, currency,
and original observation timestamp. Omitting the option retains the original behavior.
