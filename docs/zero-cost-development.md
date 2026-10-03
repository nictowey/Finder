# Zero-cost development transition

Finder is moving toward a product that can be built with no paid services. The first step
is to contain hosted work while the local architecture and provider rights are evaluated.
This document is not a claim that an existing hosting account has no usage or charges.

## Paused execution

The following GitHub jobs have a literal `if: ${{ false }}` at job scope, before a runner,
service container, secret-using step or production connection starts:

- Private watchlist scans, including schedule and catch-up dispatches
- Watchlist and eBay deletion endpoint deployments
- Production bounded scans, target scans, target matching and color-pair probes
- Private target panels, vinyl catalog panels and eBay quota checks
- Production eBay smoke validation

Live Sandbox eBay and authenticated Discogs smoke steps are also disabled. Their ordinary
unit tests remain enabled, without provider secrets. The required PostgreSQL test workflow
uses a disposable localhost service and synthetic data. Package installation and GitHub
runner operations still require network access; they do not connect to Neon or providers.

New-listing discovery and review notifications stop when the guarded workflow reaches main.
Saved watch configurations, owner judgments, prediction history and database contents are
not changed. Nothing is replayed or automatically re-enabled by this change. A scan already
running from an older commit is not canceled by a source-code guard.

## Remaining hosted exposure

This repository change does not disable resources that are already deployed. In particular:

- The Neon `scan-catchup` schedule invokes `scantrigger` every ten minutes. Its deployed
  function still records a heartbeat, checks due work and attempts a GitHub dispatch.
  Skipping the GitHub job does not stop that function's database traffic.
- The existing dashboard and authenticated API can still use Neon when accessed.
- The eBay deletion callback remains deployed for required deletion handling.
- Existing account storage, authentication, platform activity and prior usage may still
  count against that account's limits. The billing plan and remaining headroom must be
  checked before claiming that ongoing hosting is free.

Suspend the existing catch-up schedule using its supported platform control after verifying
its identity. Do not delete the database, watches, credentials or seller-deletion service to
contain costs. No production database read, migration, provider scan or deployment is needed
to validate this workflow change.

## Development and resumption

Use synthetic fixtures, SQLite and disposable local PostgreSQL for development. No new paid
service, free trial requiring billing, client-side provider secret, or production data export
is part of this transition. A commercial launch still requires an architecture and provider
rights review; the former personal-use plan is historical context, not launch approval.

To resume hosted execution later, obtain approval for the concrete cost and provider plan,
then review a focused source change restoring only the needed job/step conditions. Recheck
quota limits, freshness, notification deduplication, expired events and seller deletion before
resuming. Do not blindly revert this whole transition or replay old notification events.
