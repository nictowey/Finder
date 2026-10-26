# Discogs order evidence: offline contract

`DiscogsOrderLedger` is a separate owner-only SQLite ledger for a possible
future website checkout. It has no browser adapter, network call, submit
method, payment action, or grant issuer. Nothing in the deployed Sniper service
constructs it. `ExpectedOrder.approval_ref` is an opaque binding supplied by a
future, separately reviewed authorization path; this type does **not** verify
owner approval by itself. All tests use synthetic values.

The ledger has one permanent slot per exact release. `prepare` stores the
expected listing, seller, quote total/currency/fingerprint, rules revision,
destination and payment references, and quote expiry. `fence_possible_submit`
must commit `submission_unknown` before a future committing website action.
That state is never eligible for automatic retry, even if an order lookup
finds nothing or fails. A read-only order observation can attach a Discogs
two-part numeric order ID and classify the displayed status independently from
payment. Identity or price mismatches retain the order ID for review and keep
the one-copy fence. Later observations cannot roll a paid state back to unpaid.

Discogs' [developer documentation](https://www.discogs.com/developers)
illustrates order IDs in `1-1` form. Its
[buying guide](https://support.discogs.com/hc/en-us/articles/360001573434-How-To-Buy-Music-On-Discogs)
says the “Place order and pay now” action creates an order before payment
checkout. Its [order-status guide](https://support.discogs.com/hc/en-us/articles/360007525494-What-Do-The-Different-Order-Statuses-Mean)
distinguishes Invoice Sent, Payment Pending, and Payment Received. The ledger
therefore maps Invoice Sent to created/unpaid; Payment Pending to uncleared;
Payment Received, In Progress, and Shipped to paid. Other statuses require
review. Order creation is never reported as completed payment.

The current observed cart defers tax. Its displayed total is not a verified
final payable quote, destination/payment bindings are not established, and no
normal order-page reader or receipt lookup has been live tested on the VPS.
The adapter is intentionally absent: the committing control cannot be used as
a quote preview. A future live checkout needs an exact authorized item and
ceiling, complete final quote, matching destination/payment references,
ordinary website control evidence, and separately verified post-action order
and payment reconciliation. No record order is authorized for development.
