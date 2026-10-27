# Discogs order evidence: offline contract

`DiscogsOrderLedger` is a separate owner-only SQLite ledger for a possible
future website checkout. It has no network call, submit method, payment action,
or grant issuer.
The deployed Sniper service does not construct it. The dormant enhanced-checkout
composition below now connects its fixed metadata-only CLI in offline tests.
`ExpectedOrder.approval_ref` is an opaque binding supplied by a
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
Schema v2 records submission time transactionally with that fence. Receipt
observations preceding submission cannot verify an order or payment. The additive
v1 migration preserves permanent slots and existing order IDs, but marks legacy
attempts without a fence timestamp unverified and review-required. The separate
real-ledger read-only guard can block checkout on any existing release slot;
it does not reuse the synthetic order-safety executor interface.

Discogs' [developer documentation](https://www.discogs.com/developers)
illustrates order IDs in `1-1` form. Its
[buying guide](https://support.discogs.com/hc/en-us/articles/360001573434-How-To-Buy-Music-On-Discogs)
says the “Place order and pay now” action creates an order before payment
checkout. Its [order-status guide](https://support.discogs.com/hc/en-us/articles/360007525494-What-Do-The-Different-Order-Statuses-Mean)
distinguishes Invoice Sent, Payment Pending, and Payment Received. The ledger
therefore maps Invoice Sent to created/unpaid; Payment Pending to uncleared;
Payment Received, In Progress, and Shipped to paid. Other statuses require
review. Order creation is never reported as completed payment.

Earlier DS2 cart evidence deferred tax. Its displayed total was not a verified
final payable quote. No normal positive order-page reader or receipt lookup has
been live tested on the VPS. A live payment dispatcher remains absent: the
committing control cannot be used as
a quote preview. A future live checkout needs an exact authorized item and
ceiling, complete final quote, matching destination/payment references,
ordinary website control evidence, and separately verified post-action order
and payment reconciliation. This module does not authorize a record order.

## Manual payment after order creation

The owner selected a separate mode in which a specifically authorized Discogs
order may be created and PayPal payment is left to the owner. This does not
remove the final-quote, destination, terms, authorization or duplicate-order
checks before the committing order control. An unpaid order is a purchase
commitment, not a temporary reservation or guarantee that the seller will ship.

`discogs_manual_payment` projects only existing durable ledger state. It does
not add a lifecycle store, submit an order, pay, or query the website. An exact
recorded order with unpaid status can be labeled manual payment due as last
recorded; payment pending must not invite a second payment. Unknown, conflicting
or review-required evidence blocks the handoff. No canonical buyer order-page
URL was established by the retained evidence, so the contract reports the link
as unavailable rather than guessing one. It makes no current-status or
availability claim. Its tests use synthetic order-page evidence and do not
establish a live order or payment handoff.
## Enhanced checkout integration, October 27

The current enhanced checkout is distinct from the legacy order control described
above. Two real server runs reached a blank guest-card form displaying Pay $5.05.
Neither submitted payment nor established a merchant order. Those observations do
not qualify a final provider quote, selected payment method, delivery destination,
full terms, or positive merchant receipt layout.

`discogs_enhanced_checkout_coordinator.mjs` composes the current read-only quote
producer, merchant evidence reader, payment boundary and existing real-order ledger.
Its production `attemptPayment` always passes `enablePay: false`; callers cannot
supply a dispatcher or enable switch. The exported adapter also rejects enabled
execution outside the isolated Node test context. The provider DOM qualification
registry is empty. Missing tax, fee treatment, currency, delivery and terms remain unqualified;
an observed merchant aggregate is preparation evidence only. No card values are
read or stored.

The fixed `discogs_order_ledger_port.py` opens `/bridge/discogs-real-orders.sqlite3`
without creating it. Production JavaScript exposes no initialization method.
The immutable intent binds exact item/release/seller, current quote fingerprint,
rules revision, amount/currency, destination/payment references and quote expiry.
The CLI uses server time and the existing SQLite transaction to persist
`submission_unknown` before any intercepted dispatch can run. Lost acknowledgments,
process crashes or cancellation after fencing cannot authorize a retry. A zero or
unqualified receipt lookup leaves that permanent slot unknown.

The current merchant reader deliberately produces no positive order evidence:
actual positive receipt DOM and Purchases selectors have not been qualified.
Order IDs, paid status and receipt fields in the executable rehearsal below are
synthetic and available only through the isolated test factory. They demonstrate
real ledger transitions, not live merchant or payment compatibility.

### Executable offline rehearsal

```sh
node --test tests/test_discogs_enhanced_checkout_coordinator.cjs
```

This uses synthetic quote/destination/terms/payment-control metadata, intercepted
no-network dispatch, and the actual Python CLI with a private temporary SQLite
ledger. It verifies validation, durable pre-dispatch fencing, unknown outcome,
unpaid/pending/paid receipt reconciliation, restart persistence and duplicate
suppression. It cannot submit a payment or reach a provider. The CI checkout gate
runs this and the separate quote, adapter, ledger and merchant-reader regressions.

### Remaining live evidence

Before any payment implementation can be enabled, normal supported UI inspection
must establish the provider's explicit currency and final all-in amount, tax and
mandatory-charge treatment, actual delivery selection, full applicable terms,
selected secure payment method and exact Pay-control association. Read-only
inspection must exclude payment input values and session identifiers. Existing
blank-form labels alone do not establish those fields. A previously existing
authorized merchant order could qualify a positive receipt layout read-only;
otherwise that positive layout remains unverified until a separately authorized
real transaction. No order should be placed merely to manufacture a fixture.
The absence of a supported Wallet/browserAuth route currently prevents secure
automatic card entry; no persistent card credential setup is authorized.

### Operator and read-only staging checklist

Normal Firefox Inspector observation on October 27 established one matching
`#submit-button` node, an HTML `button` with `type="button"`, displaying
`Pay $5.05` in the currently open guest form. Its containing form also displayed
the billing-address section, checked `Ship to billing address` choice and age
notice. This is operator-inspected public metadata, **not** an installed provider
DOM profile: exact provider frame origin/document association, explicit currency,
final-charge treatment, destination match, payment method and full terms remain
unqualified. No input values were read. The button was not activated.

Before further inspection:

- Claim the viewer using its fresh full tab record. The extension attachment can
  change while the numeric tab ID stays the same.
- Check native Chrome focus when viewer input appears ineffective. An open outer
  Chrome View menu was observed intercepting input; dismissing that menu through
  its exposed Cancel action and using normal Firefox context-menu Inspect
  restored Inspector access. This did not require a payment-page reload.
- Verify the remote UI actually changed before the next action; the outer noVNC
  accessibility tree does not expose or settle the remote Firefox DOM. Focus the
  Inspector search visibly before typing public selectors. Never type into a
  payment field, activate Pay or capture input values during qualification.
- Close only Inspector and preserve the blank form. A public ID and dollar label
  alone cannot enable payment or resolve an existing submission fence.

For dormant staging, preserve the module tree exercised by the packaged-layout
test in `test_discogs_enhanced_checkout_coordinator.cjs`: ten MJS dependencies,
`scripts/discogs_order_ledger_port.py` and
`src/finder/discogs_order_reconciliation.py`. A normal, non-test subprocess must
import, construct and report `enhanced_payment_disabled` with no browser, network
or file effects. Do not initialize or open the real ledger merely to smoke-test
staging: opening an older ledger may migrate its schema, and reconciliation
writes evidence. No live Page object is available in a separate import process;
read-only browser use requires the scoped runtime path below. Do not introduce a
debugging connection or reuse a spent grant to make the library callable.

### Scoped read-only readiness IPC

The source adds `inspect_enhanced_payment` to the existing owner-only guest bridge.
It invokes only `coordinator.inspectPaymentReadiness` on the resident checkout Page;
it exposes no Pay dispatcher, payment enable switch, navigation, initialization,
card-entry method or real-order-ledger operation. Production Pay remains disabled
in both the coordinator and adapter. No second browser or controller is created.

The default-off `discogs_guest_payment_inspection_policy_v1` allows two ordered
calls within five minutes for the exact KC scope. First, the existing read-only
probe binds a fresh Page/Document reference. Second, `inspect_payment_readiness`
consumes its distinct durable slot and must match that resident reference, the
expected append-only ledger head and latest observed-form claim. Missing or
changed identity, document or claim cannot be repaired by the caller. The shared
context lease remains held until dispatched reads and handle cleanup settle, even
when cancellation or a deadline returns earlier. The response is a bounded
readiness report; unknown provider fields stay unqualified and grant no payment
authority.

After a valid non-null policy at startup enables the existing guest listener, its
access and authorization functions reload the same local policy for each request.
This supports a later separately reviewed inspection job without restarting a
browser merely to replace the earlier preparation job. Missing, invalid, expired,
revoked or mutated policies deny access; durable consumed receipts remain spent.
The wrapper never creates a policy, grants a job or enables an absent startup
listener.

The currently running viewer predates this IPC addition. Loading the changed
static socket/runner code requires an initial browser-service restart. That would
discard the open initialized form, so activation is held pending a reviewed form
preservation/disposition decision. A successful import or offline socket test is
not live Page qualification. No restart, new job or payment is implied by staging.
