# Offline development without service usage

The first offline milestone exercises Finder's existing pressing assessment and persistence
with invented records on your own computer. It needs no Neon database, provider account,
credentials, paid services, or provider requests. Existing installed Python dependencies are
enough. Installing dependencies initially still needs access to your package source.

From the repository root, with the project's Python environment active:

```bash
PYTHONPATH=src python -m finder.offline --database finder-offline-demo.sqlite3
```

After an editable install, `finder-offline --database finder-offline-demo.sqlite3` is equivalent.
The command prints JSON. The default database filename is `finder-offline-demo.sqlite3`.
Choose a **new filename for each run**; the command refuses to overwrite or open an existing
file. The parent directory must already exist. SQLite files are excluded from Git.

## What the demonstration does

- Creates a private local SQLite file using the existing listing/catalog/candidate schema
- Saves one invented album, two synthetic pressing variants, and three synthetic listings
- Replays one listing twice and supplies both a newer and an older price observation
- Closes and reopens the database, then runs the existing watch assessment over saved inputs
- Reports `possible_pressing`, `family_review`, and `conflicting`, along with the evidence
  and simulated price/notification eligibility from the existing policy

Expected persistence counts: three listing identities, four distinct observations, two
variants, and six candidates. The blue listing's latest price is 19.00 plus 4.00 shipping,
so its simulated delivered subtotal is 23.00. An older observation cannot replace it.
The invented prices are examples of owner-set thresholds, never market values.

`review.notify` reports what the existing rules would decide for that synthetic case.
The command creates no watch, outbox, subscription, or scheduled job and sends no notification.
Reviews are calculated from reopened data and printed; the persisted artifacts are the
synthetic listings, observation history, catalog records, and candidate evidence.

## Boundaries

This entrypoint is separate from `finder`, the dashboard, and all live worker commands.
It never loads `.env`, exports owner data, accesses Neon, or invokes either provider.
All inputs are invented and carry the `synthetic` marketplace/catalog namespace; there
are no listing URLs, photos, real seller identities, or captured provider responses.
The rules remain provisional review aids and do not verify a pressing's identity.

Exported `FINDER_*`, `EBAY_*`, `DISCOGS_*`, `NEON_*`, `DATABASE_URL`,
`DATABASE_URL_UNPOOLED`, and PostgreSQL connection settings cause the command to stop before
creating a file. The error names only the settings to unset, never their values. Run from a
clean shell when these are present; do not copy or load the live `.env` for this command.
An existing `.env` file is left untouched and ignored.

`--database` accepts a local file path, never a database URL, SQLite URI, or network path.
Existing files and symlinks are refused. Existing `-journal`, `-wal`, or `-shm` siblings,
including dangling symlinks, are also refused before the main database file is created.
A runtime guard rejects Python socket operations, subprocess launches, dotenv reads, and
SQLite connections outside the new demo file. This is a regression guard for this command,
not a security sandbox for untrusted code or concurrent filesystem changes.
If a run fails after file creation, its partial demo file remains; use another new filename.

This milestone does not provide a local web dashboard, live discovery, provider credentials,
owner-data migration, deployment, commercial/provider approval, or an assurance about costs
from separately running hosted services. It is an isolated path for continued matching and
persistence development without causing external service usage.

## Verification

```bash
PYTHONPATH=src python -m pytest -q tests/test_offline.py
```

These tests check durable synthetic data, deduplication, stale observation handling,
assessment ambiguity, untouched existing databases, refused live settings, ignored dotenv
files, import separation, and runtime I/O failures. They make no provider calls.
