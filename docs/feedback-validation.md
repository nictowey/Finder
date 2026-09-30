# Feedback PostgreSQL release gate

The **Isolated PostgreSQL feedback checks** pull-request workflow runs the existing
`scripts/check_watch_postgres.py` rehearsal against an official `postgres:17` GitHub Actions
service container. The container, database, credentials and randomized host port exist only
for that job. It has read-only repository permissions, no production environment or repository
secrets, no deployment or marketplace calls, and a ten-minute timeout. Checkout does not retain
its token. It follows GitHub's [PostgreSQL service-container workflow](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

`scripts/check_isolated_postgres.py` rejects any connection that is not the synthetic fixture
on `127.0.0.1`, including connection-query overrides, before importing or running the rehearsal.
The rehearsal creates a random schema and drops it in its cleanup path. The service container
is removed by Actions even if the job fails or times out. No persistent database is provisioned.

The gate exercises actual PostgreSQL migration, repeated migration, legacy purchase-only
backfill, exact backup/restore, decision/purchase round trips, seller-deletion cascades and
worker lease behavior. The feedback concurrency check first observes the save backend blocked
on the worker's watch lock, then inserts a pending event and commits the worker. The save must
see and remove that event after acquiring its lock. An elapsed timer alone cannot prove that
interleaving. This is the acceptance gate for the post-lock transaction snapshot behavior.

The rehearsal also saves a purchase-only decision through the canonical SQL routine, then
reevaluates that baseline item with the Python discovery worker. It must create no pending
event and consume no initial digest. A second, unjudged baseline item must then create exactly
one pending event and consume the digest. This binds the API suppression mirror to the runtime
accounting without treating purchase as identity or a queued event as device delivery.

Local SQLite and embedded PostgreSQL tests cover deterministic logic but do not substitute
for this multi-session result. Before merging or deploying the combined UI/feedback change,
verify this workflow succeeded on its exact reviewed PR revision. A workflow added locally,
a queued run or a green unrelated job is not a passed release gate. No production migration
or deployment is authorized by this test workflow.
