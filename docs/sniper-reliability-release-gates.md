# Sniper reliability release gates

This is an incremental alert-only upgrade plan. No orders, payments, email monitoring,
new credentials, or changes to the owner's price ceilings are part of it.

## Evidence required before activation

1. Run repository checks on the exact candidate revision. Distinguish offline simulation,
   live provider acceptance, and owner-confirmed phone receipt.
2. Prove migration and rollback on a disposable copy containing the old schema and rule
   settings. Repeated initialization must be safe. Existing targets and thresholds must
   compare equal before and after the upgrade.
3. Run the candidate read-only health command on the authorized server. Do not print
   credentials, private topic values, push endpoints, or raw notification-service logs.
4. Exercise candidate behavior against isolated temporary state using one current public
   API observation and a clearly labeled test, never a fictional real target event. One
   accepted notification is not proof that it reached the phone. Obtain receipt confirmation.
5. Back up the production database using SQLite's consistent backup interface; record the
   old code path and service configuration. Stop the worker for the final state transition.
   Do not restore an old database after new notifications have been sent: that could replay
   alerts. A rollback must account for accepted, leased, and uncertain messages.
6. Activate only the feature that passed its gates. Establish silent baselines rather than
   replaying existing qualifying stock. Verify rule count, thresholds, five-minute cadence,
   current samples, queue, destination binding, and service restart behavior.

## Hard boundary: listing discovery

The VPS's ordinary release marketplace-page GET encountered a Cloudflare challenge. That
route is halted. Do not switch hosts, proxies, sessions, or browser routes to evade it.
The official aggregate statistics API supplies counts and a lowest price, not a set of
listing IDs or per-copy media/sleeve grades. Offline parser tests do not establish a working
production discovery path. Grade-qualified automatic monitoring and buying stay unavailable
until an authorized listing-data source is demonstrated end to end.

## Baseline evidence

At 02:30 UTC on November 8, 2026, the existing deployment had 19 successful aggregate
samples, zero accumulated polling failures, an empty outbox, and enabled delivery.
The worker, notification service, and HTTPS proxy were active and enabled at boot.
The worker reported zero restarts; disk usage was 21% with roughly 31 GB available.
A single Web Push subscription was registered. These are point-in-time checks, not a
future availability guarantee or proof of any later phone delivery.
