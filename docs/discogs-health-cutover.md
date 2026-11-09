# Staged phone canary and coordinated health-watcher cutover

This is an operator checklist, not an installer. Run only after the exact candidate
passes offline checks and independent review and the actual canary is approved.
Use verified existing service names, account, paths and interpreter; no credentials
are created or printed. The candidate health timer remains off during staging.

Preconditions:

- The deployed aggregate version is the verified c365848 baseline, or its shared
  gate compatibility has separately been checked.
- Only the known aggregate service publishes through this database. There are no
  duplicate manual/cron workers or outside publishers relying on this protocol.
- Candidate code is staged in a separate directory. Both candidate dispatch paths
  include the shared lock. Existing rules and polling settings are unchanged.
- The existing private ntfy/app configuration passes local validation under the
  same operating-system account. Do not change permissions to work around failure.
- The exact-base downgrade rehearsal passed for a long provider deadline and a
  manual-attention hold. Both are carried by the old `next_send` gate; old polling
  remains independent.

Set these variables from verified deployment information, not guesses:

```sh
AGGREGATE_UNIT='verified-existing-aggregate.service'
MONITOR_USER='verified-existing-monitor-user'
PYTHON='/verified/existing/venv/bin/python'
CANDIDATE='/verified/staged/candidate'
DATABASE='/verified/existing/rules.db'
APP_FILE='/verified/existing/discogs-app.json'
NTFY_FILE='/verified/existing/private-ntfy.json'
TEST_ID='health-canary-20261108'

candidate() {
  sudo -u "$MONITOR_USER" env PYTHONPATH="$CANDIDATE/src" "$PYTHON" \
    -m finder.discogs_health_watcher "$@"
}
```

## Stage one explicitly labeled notification

1. Stop and drain the existing aggregate service. Verify its processes exited and
   no other publisher remains. Do not run a candidate sender alongside c365848.

```sh
sudo systemctl stop "$AGGREGATE_UNIT"
sudo systemctl status "$AGGREGATE_UNIT" --no-pager
```

2. Explicitly initialize additive candidate state. Inspect it and confirm watcher
   status is disabled. `init` is idempotent but does not turn an already-enabled
   watcher off. If it reports enabled, stop and investigate rather than guessing
   a revision or proceeding with two publishers.

```sh
candidate init --database "$DATABASE"
candidate inspect --database "$DATABASE" \
  --discogs-app-file "$APP_FILE" --ntfy-config-file "$NTFY_FILE"
```

3. The two unfinished-attempt counts must be zero before sending. If either count
   is nonzero after all publishers are demonstrably stopped, use the explicit
   **non-sending** recovery command, then inspect again. It quarantines abandoned
   attempts once, preserves known deadlines, and adds the bounded recovery hold.
   `busy` means a lock is still held: stop and investigate, never remove the lock.

```sh
candidate recover --database "$DATABASE"
candidate inspect --database "$DATABASE" \
  --discogs-app-file "$APP_FILE" --ntfy-config-file "$NTFY_FILE"
```

4. Run the exact approved canary. Its title is “TEST: Sniper health alert”; its
   body says this is a test and record rules are unchanged. It uses a separate
   test incident, the real destination, and the real shared/health budget.

```sh
candidate test --database "$DATABASE" --test-id "$TEST_ID" --phase degraded \
  --ntfy-config-file "$NTFY_FILE" --deliver
```

`accepted` proves provider acceptance only. `idle` may mean a cap/cooldown; wait
for the real gate and reuse this ID if another attempt is appropriate. Never
change clocks, clear counters, or create another ID to force an immediate retry.
`uncertain` must not be replayed. `uncertain_recovery` means recovery occurred, not
that this invocation sent a notification. No outcome alone proves phone receipt.

5. After the candidate process exits, run non-sending recovery if the attempt
   crashed or is unfinished. Then inspect. Before resuming old code require:

- `unfinished_health_attempts` and `unfinished_aggregate_attempts` are both zero;
- no candidate sender process/held lock remains, and the health timer is off;
- earned cooldowns/manual holds remain intact and visible;
- aggregate enabled state, rule values and polling settings are unchanged.

```sh
candidate recover --database "$DATABASE"
candidate inspect --database "$DATABASE" \
  --discogs-app-file "$APP_FILE" --ntfy-config-file "$NTFY_FILE"
sudo systemctl start "$AGGREGATE_UNIT"
```

Do not restart the old service if recovery returns `busy` or evidence is
unavailable. A completely finalized uncertain event has zero unfinished count
but remains uncertain and un-replayable; its known provider deadline is retained.
An old service can resume polling while honoring the preserved notification gate.
A manual hold is not permission to clear it merely to resume notifications.

**Never restore a pre-send database snapshot.** Doing so would erase attempt
history/budgets and could duplicate messages. Preserve every earned cooldown,
provider outcome, incident and cursor through staging and rollback.

6. Ask the owner to confirm the actual test appeared on the phone. Keep the timer
   off until receipt is confirmed and the complete candidate is approved for the
   final cutover. Recovery behavior has deterministic offline proof. A second
   real recovery test is optional, requires explicit approval, the same test ID,
   provider acceptance of the first test, a drained old sender again, and at
   least the real 15-minute health spacing plus any longer shared hold.

## Final coordinated cutover

After the phone-confirmed canary and all gates pass:

1. Stop/drain the old aggregate service again and perform non-sending recovery/
   inspection if needed. There must be no unfinished or still-running publisher.
2. Switch the aggregate service to the reviewed lock-aware code without changing
   database, record rules, private configuration or polling arguments.
3. Start the updated aggregate worker and verify a normal poll and intact shared
   budget. Install the reviewed health service/timer templates under the same
   account and using the same database path.
4. Inspect the current watcher revision. Explicitly enable it with that revision,
   then enable/start only the new health timer. Do not guess a revision.
5. Verify a scheduled check. Healthy checks should not send routine messages.

For rollback, stop/disable the health timer, drain the oneshot, disable the
watcher with its current revision, then follow the preserved-budget/no-unfinished-
lease requirements before returning the aggregate service to old code. Do not
delete the lock file or health tables. The full policy and coverage limitations
are in [health watcher](discogs-health-watcher.md).
