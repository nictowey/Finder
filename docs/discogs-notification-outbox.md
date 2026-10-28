# Sniper notification handoff: state contract only

`NotificationOutbox` is an additive SQLite ledger for changes already accepted by
`ReleaseSnapshots` and materialized in `InAppChangeOutbox`. It has no network
transport, sender, account credentials, or provider configuration in the private
Sniper service. Its default `ProviderCapability` is unavailable. The private
in-app review remains the only usable alert surface today. No record buying is
enabled by this module.

Each retained change gets a stable SHA-256 event key from its release ID,
baseline revision, change kind, and listing ID. The outbox has a unique source
sequence and binds that event to one opaque provider ID and destination reference.
The reference syntax excludes raw email addresses and URLs; callers must
ensure the opaque reference is not itself a credential or token. An existing
pending event cannot silently move to a
different destination. A terminal event remains bound to its original
destination; only later new changes can use a new binding.

There is no public endpoint. A future owner-authenticated configuration path
must explicitly provide the binding and a real provider capability before a
sender is added. The required integration point is **after a complete release
snapshot is ingested**, using the captured rules/control revisions to call
`enqueue`. It first reconciles the private in-app change ledger. It does not
send. An owner review acknowledgment suppresses unsent provider events. The
private review UI and the provider outbox share source change sequences but
have separate delivery state.

The future sender contract is:

1. `claim(binding, provider)` reserves one due event for 120 seconds under a
   SQLite write lock. It returns a generic payload containing no seller text,
   price, listing ID, or credential. The event key is the only provider-side
   idempotency reference. The current service has no sender and cannot perform
   this handoff with its unavailable provider.
2. A confirmed provider acceptance calls `record_accepted`. This records
   provider acceptance, **not device delivery**. A response proving the
   provider did not accept the request calls `record_known_failure`; retry waits
   at least 15 minutes, uses exponential backoff up to one day, honors a
   bounded Retry-After of up to one day, and stops after five attempts.
3. A timeout, disconnect, crash, or other uncertain submission calls
   `record_uncertain` or is moved to `uncertain` by `reconcile` when its claim
   expires. Uncertain submissions are not automatically retried. They need
   provider-side reconciliation against the same event key before any further
   attempt. Revoking a binding also stops queued work; an in-flight claim
   becomes uncertain.

The existing cloud kill switch blocks claims. Pausing a release or marking its
buying-rule target owned revokes unsent work; an in-flight attempt becomes
uncertain. A missing retained source change expires an unsent queue entry.
The status contract reports
`sender_connected:false`, `notifications_enabled:false`, and
`provider_acceptance_is_delivery:false`. No status or test count should be
presented as proof that Nic saw an alert. Offline tests cover restart and
concurrent claims, binding changes, owner review suppression, known-failure
backoff, uncertain outcomes, revocation, and the stopped default. They use a
synthetic provider capability only; no provider request or message is sent.
