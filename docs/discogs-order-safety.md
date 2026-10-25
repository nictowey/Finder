# Simulated order lifecycle

`finder.discogs_order_safety` is an isolated, offline safety model. It is not
connected to the private web service, hosted browser, cart, or Discogs. Its
`SyntheticOrderAdapter` has no network or payment methods. The service exposes
`order_authorization_configured: false`, `order_submission_enabled: false`,
and `order_reconciliation_configured: false`.

The model requires an explicitly initialized, owner-only SQLite ledger. A
simulation grant binds the exact target settings digest, release ID, rules
revision, currency, all-in ceiling, opaque destination and payment references,
and expiry. The references are random IDs, never card or address data. A
synthetic final quote must match the release, listing, destination, component
amounts, reviewed observation and current rules. Missing media, disclosed
damage, stale or unknown totals, or an over-ceiling amount stop preparation.

One release gets one durable intent slot. Before invoking the synthetic adapter,
the intent changes from `prepared` to `submitting` in a transaction. A crash or
unknown result leaves it fenced as `submitting` or `uncertain`; it cannot invoke
submit again. A read-only synthetic lookup may attach a matching receipt or a
matching, later absence result. Even confirmed absence does not recycle the
one-copy slot automatically. Revocation or changed rules stop an unstarted
attempt. Nothing in this model can approve or perform a real purchase.

Operational work remains: restore an allowed normal Discogs access path,
verify actual item details and a final delivered quote, establish precise
purchase scope and payment handoff, then build and validate a provider-specific
order adapter and reconciliation route under separate authorization. Live
collection, cart writes, and orders remain off.
