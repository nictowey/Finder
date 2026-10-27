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

The IPC addition was activated on October 27 after normal blank-form dismissal,
fresh complete All/All zero-order history and an append-only disposition. The
initial upgrade used one browser-service restart with the same profile; later
policy changes used the existing listener without another restart. The original
access stop, consumed receipts and paused monitoring were preserved. Zero merchant
orders do not establish the absence or cancellation of a provider draft.

### Actual read-only IPC verification

A new bounded server preparation completed saved-destination confirmation in
0.348 seconds and normal Cart-to-Checkout/blank-form preparation in 5.997 seconds.
The saved-address approval was an actual operator visual check; the program then
bound and revalidated its private snapshot. This step is **operator-assisted**,
not autonomous owner-address approval. The original unknown initialization outcome
was retained and a truthful blank-form observation appended. No card data entry
or Pay action occurred.

The subsequent read-only batch reached the actual resident Page and returned
`enhanced_payment_readiness_observed`. Phase one took 0.439 seconds, phase two
0.482 seconds; policy installation and both reads finished in 2.379 seconds.
Current identity, conditions, quantity, title and item price matched. The current
document, observed-form claim and ledger head were verified. The merchant showed
USD 0.10 subtotal, USD 4.95 shipping and USD 5.05 total. One known buttons frame
was associated with the merchant Page. It was **not** qualification of the
provider's Pay/quote document.

All provider quote/finality, currency, tax/charge treatment, delivery, selected
method, full terms and positive receipt profiles remain unqualified. The runtime
reported payment disabled. These live reads prove the scoped read-only wiring;
they do not prove a final payable quote, order creation, payment or an unattended
buyer.

### Phase-specific probe response contract

An ordinary `current_dom_evidence_only` response explicitly reports
`card_entry_allowed`, `payment_allowed` and `final_submission_allowed` as false;
it omits `initialization_allowed`. That omission is not initialization authority.
The enhanced readiness response explicitly adds `initialization_allowed: false`.
Consumers must recognize the known phase, require its explicit false permissions
and stop on any reported effect. They must not demand a phase-two field from an
ordinary probe or treat absence as true.

A local operator helper initially confused these envelopes and stopped before
phase two. Its consumed first phase was preserved. The reviewed recovery used a
distinct read-only policy, unchanged historical form-observation timestamps and
fresh current-form/document evidence. It persisted the returned opaque reference
before later checks. The socket regression retains the actual minimal response
shape with a clearly synthetic replacement UUID; no payment or address values are
part of that fixture.

### Bounded guest-control metadata extension

The read-only collector inspects only the observed `#submit-button` public
control in the merchant main document and bounded visible, directly owned
`https://www.paypal.com` child frames other than the known `/smart/buttons`
frame. It does not guess a guest URL path or export any URL, query or fragment.
The fixed collector reads the control's public Pay label, type, visibility and
disabled state, with same-document and frame-ownership checks. No input value,
address, hidden application state, token or network payload is collected.

The `guest_pay_metadata` diagnostic distinguishes `unsupported_collection`,
`no_eligible_frame`, `ambiguous` and a uniquely observed control. Missing metadata
from the older `provider: null` producer means unsupported collection; it is not
evidence that a live guest frame is missing. A no-eligible result refers only to
the bounded allowlist. A `$` label alone leaves currency unknown. Even observed
Pay metadata does not qualify a final quote, delivery, selected method, terms,
receipt, or payment dispatch. Production Pay remains hard-disabled.

The browser-only `f11f0f7` image was activated on October 27 after a normal blank
form dismissal, complete All/All zero-order reconciliation and append-only
disposition 29. Its single restart retained the same profile, 179 preserved files,
original access stop and inactive monitoring. Web UI deployment was separately
rejected by approval review and remains excluded pending specific approval.

### Historical read-only probe budget diagnosis

The first new scoped read returned `probe_deadline_expired` in 1.732 seconds while
105.611 seconds remained on its policy. Confirmation, checkout navigation and
initialization were never dispatched; the consumed inspect phase stays spent.
The ledger remains at disposition 29. No live guest-control metadata was obtained.

Code confirms that the probe had a fixed 1500ms internal deadline. The specific
slow read was not retained and remains unknown. A Cart probe does not execute
the enhanced guest-metadata collector, so this timeout is not evidence that the
new collector failed. Cold-page readiness or browser read latency are possible
contributors, not established causes.

The scoped fix uses a fixed 5000ms probe budget, matching the existing enhanced
read-only inspection bound and leaving 3 seconds within the fixed 8 second client
timeout. The deadline is still the earliest of that budget, the original policy
expiry, and (for confirmation) the document reference and 60 second visual-ACK
expiry. No expiry is renewed by an observation or a timeout. Cancellation
returns promptly and pending browser reads retain their context lease; late
results cannot create destination authority.

`probe_timing` reports only a fixed schema, effective budget, capped monotonic
elapsed milliseconds, fixed last-stage name and up to 10 unique stage records
with capped dispatched-read counts and elapsed time. It carries no page values,
addresses, URLs, tokens or exception text. Malformed or cross-action diagnostics
are rejected; reported effects remain visible as scope violations. Offline
delayed-read tests establish behavior, not that 5000ms will solve the prior live
timeout.

The running browser holds imported modules in memory. Policy reload cannot
install source fixes, and no supported hot-load route exists. The probe/socket
fix was subsequently activated as `2fd7c25` with the same profile and preserved
history; later Cart probes completed in 244 ms and 164 ms. The historical slow
stage remains unknown. No initialized form was discarded by that update: the
preceding attempt had stopped before initialization. Subsequent jobs described
below are distinct scoped jobs, not reuse of a spent inspect phase. Web deployment,
Pay, purchases and monitoring activation remain excluded.

### Actual guest metadata result and bounded selection repair

On October 27, the `2fd7c25` runtime completed another authorized no-submit
preparation after fresh complete All-archive / All-status zero-order history.
Confirmation took 0.353 seconds and server preparation took 5.483 seconds.
The blank form visibly showed `Pay $5.05`; no card data was entered and Pay was
not clicked. Observation 34 was appended once after the unknown initialization
outcome. Both records remain immutable; initialization remains denied until a
separately reviewed disposition. Provider-draft, order and payment states remain
unverified.

The new read-only job completed both reads in 2.431 seconds. It verified the
resident document, claim, ledger head, merchant identity/conditions and USD
0.10 + 4.95 = 5.05. One known buttons frame was associated. Guest Pay metadata
returned `unsupported_collection`, `truncated=true`, associated count 1 and
eligible count 0; its origin and candidate counts saturated at 2. This does not
establish that a guest Pay control was absent. Source inspection confirms the
old collector counted all non-buttons PayPal frames against its two-frame cap
before checking direct ownership and visibility. The exact skipped frame was
not retained, so identifying any particular live frame as the omitted Pay frame
would be an inference.

The repair applies supported origin, direct-parent, visibility and frame-element
association checks before the two-guest-frame cap. Total inventory traversal
remains bounded at 32. All considered frame identities and classifications are
rechecked after awaited work; a hidden, nested or other-origin frame becoming
eligible late invalidates the observation. Multiple eligible Pay controls and
incomplete traversal remain unqualified. The optional `selection_diagnostic`
contains only its fixed schema, an enumerated reason and counts capped at 32;
no URL, query, fragment, input value, address or exception text is returned.
Its offline multi-frame tests simulate the observed capped-count failure, not
the unretained live frame hierarchy. Public Pay metadata still cannot qualify
provider finality, currency, delivery, terms, selected funding or a receipt.

The installed read-only methods cannot return the new classifications: both
guest metadata and the separate buttons-frame diagnostic retain their old
capped schemas. Reloading a policy changes authorization only. Job
`2280f56e-e9e2-48f1-b529-de7df2575f40` is spent and cannot be replayed. No
undocumented or alternate control path is used to extract additional evidence.
The repaired producer and coordinator retain a `total_frame_limit` diagnostic
when inventory exceeds 32, without querying those frames or inventing a zero
buttons count. The hosted inspection's existing blocked-result boundary may
still reduce that overflow to `payment_inspection_unverified`; it does not yet
forward the richer overflow diagnostic. The current at-most-32-frame selection
diagnostics use the existing metadata field and its strict sanitizer.

Activation is a separate consolidated step after CI/review, not part of this
repair pass. Preserve the current blank form and observation 34. Any required
restart first needs normal blank-form dismissal, verified form absence, fresh
complete All/All reconciliation and a truthful append-only disposition. Update
the strict private operator projection for the optional diagnostic alongside the
runtime, then use a distinct bounded no-submit preparation and read-only job
for live proof. No restart, new initialization, Pay, web deployment, merge or
monitoring activation is performed by this offline repair.

### October 27: actual bounded selection result and ancestry-only investigation

The reviewed `4adc07d` private activation preserved 220 files, the saved browser
profile, historical access stop and the 35-record ledger. Normal blank-form
dismissal and fresh complete All/All Purchases with zero orders preceded its
append-only disposition. This does not establish absence of a provider draft.
The next bounded server preparation took 5.617 seconds, with 28.556 seconds left
on the original destination acknowledgement. It opened a blank guest form;
normal visual observation verified Pay $5.05 and appended observation 40 without
altering the preceding unknown record. Card entry and Pay remained untouched.

The subsequent read-only runtime job completed in 2.295 seconds. Its actual
selection diagnostic traversed all 14 frames: four PayPal-origin frames, one
buttons frame excluded, three non-direct frames rejected, zero eligible guest
frames, zero inspection errors and no truncation. Known buttons association
passed. Those counts do not identify which rejected frame contains the guest
form or establish any ancestry relationship. Merchant USD 0.10 + 4.95 = 5.05
remained observable; provider quote, currency, finality, delivery, terms, selected
method and a positive merchant receipt remained unqualified.

Normal Firefox Inspector separately selected the visible
`BUTTON#submit-button[type=button]` labelled Pay $5.05. Inspector/Console mouse
controls responded, but attempted ordinary clipboard and keyboard diagnostic
entry produced no expression or result. No diagnostic was evaluated, and no
frame ancestry is claimed from that attempt. It did not reload, dismiss or
reinitialize the form. The script now acquires the missing evidence through the
existing fixed read-only runtime collector rather than repeated Console input.

The optional `ancestry_diagnostic` is diagnostic evidence only. It visits at most
32 inventoried frames, four nested candidates and four parent edges per
candidate. Exceeding a limit reports the bounded failure instead of choosing an
arbitrary initial subset. Output contains only fixed origin/path categories,
depth, ownership/visibility/document-continuity booleans, stop reasons and the
capped count of `button#submit-button[type="button"]`. It excludes full URLs,
queries/fragments, arbitrary text, hidden state, payment input values and
exception text. Unknown or absent parents, cycles and unverified edges stop
before control inspection. Every permitted edge must bind the actual child to
its iframe element, owner frame and content frame, and the actual known
Documents. A verified buttons ancestor is reported only if that same verified
frame occurs in the observed chain; it may instead be a sibling.

The new diagnostics never add a nested frame to eligible children or authorize
Pay. A complete synthetic ancestry in tests therefore still leaves nested Pay
metadata ineligible. Ambiguous controls, changed documents/edges and incomplete
relationships stay explicitly unqualified. The read-only helper uses the
existing cancellation, pending-read drainage, deadlines and handle cleanup.
Final ancestry edge audits follow its final document reads; the existing direct
Pay-child audit follows all added diagnostic awaits. Tests cover both late-read
races with offline fixtures, not a claimed live provider hierarchy.

This ancestry-only change is not activated in this pass. A consolidated live
evidence plan must first verify the current form still blank, normally dismiss
it, obtain fresh complete All/All Purchases and append a new disposition after
observation 40. Preserve every old ledger/job byte, then build the two reviewed
modules on the exact current private image and perform one scoped browser
restart with the same profile. Stage the strict operator projection for the
optional ancestry field before issuing fresh bounded jobs. If a startup
read-only policy is needed to bind the existing IPC listener, keep it unused and
archive it unchanged before the distinct reconciled preparation job; do not
weaken expiry or rearm a spent grant. Use the proven batched no-submit
preparation, append a genuine form observation, then a distinct bounded
read-only metadata job. Report actual ancestry before proposing any eligibility
extension. No web deployment, payment, merge or monitoring activation is part
of this plan.

### October 27: live two-hop guest-frame evidence

The private `ea60e1c` activation and subsequent bounded no-submit run obtained
actual ancestry evidence. Normal blank-form dismissal, fresh complete All/All
Purchases with zero orders, and append-only disposition 41 preceded activation;
the earlier unknown record remained intact. The next server preparation took
5.709 seconds, with 41.377 seconds of destination freshness remaining. A visual
observation of blank fields and Pay $5.05 appended observation 46. No card data
was entered and Pay was untouched.

The separate read-only metadata job completed in 3.157 seconds. Of 14 inventoried
frames, three were non-direct PayPal candidates. One `paypal_smart_other` frame
had an actual two-hop chain to the exact verified buttons frame and then the
merchant checkout. Both edges passed visibility, content-frame, owner-frame,
parent and Document-continuity checks; that guest frame contained one fixed
public `button#submit-button[type="button"]`. The other two candidates failed
visible association at their first edge. There was no traversal truncation or
inspection error. The aggregate diagnostic is `incomplete` because those two
candidate chains are rejected; this does not negate the individually verified
two-hop chain or qualify an arbitrary nested frame.

At that revision nested eligibility remained disabled, so the public Pay
metadata still reported `no_eligible_frame`. This evidence supports only a
narrow same-process two-hop eligibility extension retaining the original Frame,
Document and iframe ownership objects. Sanitized diagnostic booleans alone are
not an authority to select a frame. Merchant USD 0.10 + 4.95 = 5.05 remained
observable; provider explicit currency, final-charge/tax treatment, delivery,
terms, selected payment method and positive merchant receipt remain unqualified.
Zero merchant orders does not establish absence of a provider draft.

The narrow public-metadata collector now admits only the verified two-hop
`paypal_smart_other -> same verified paypal_buttons -> merchant_checkout`
relationship. It retains the actual Frame, iframe element and Document objects,
checks both edges before and after the fixed public-label read, and rechecks the
excluded hidden peers after awaited work. The observed hidden peers remain
excluded through their own visible/ownership/content/Document proofs; no higher
relationship is guessed for them. Other visible nesting, ambiguous controls,
changed documents/URLs/ownership and traversal limits fail closed. The existing
unique `#submit-button` collector must still prove BUTTON/type=button, visibility,
enabled status and its bounded public label. A duplicate ID of another type is
not ignored. Direct plus nested candidates share the original two-frame cap.
The final nested check proves ancestors and the merchant first, then finishes
with leaf URL, parent, iframe association/visibility and Document identity, so
a leaf replacement during an awaited ancestor proof cannot retain eligibility.

The 14-frame regression graph uses synthetic URLs and credentials-shaped test
strings to model the observed categories and relationships. It is offline
evidence, not a claim of another live pass. `paypal_child` in the existing
metadata schema includes this explicitly verified descendant; the ancestry
diagnostic records the two-hop relationship. A bare Pay $5.05 label still has
`currency: null`. Public control eligibility never enables Pay or qualifies a
provider final quote, destination, payment method, terms or merchant receipt.

### Disabled payment integration and secure-entry boundary

The next implementation separates observed layout evidence from final payment
confirmation. The actual bound merchant USD summary and matching guest Pay
amount establish a currency context; the raw button currency remains unknown.
A competing currency, conversion choice or new notice prevents final
qualification. A final quote additionally needs a fresh, trusted confirmation
after secure entry, bound to the current opaque merchant/provider document
references, final amount, delivery choice, shipping and visible notices. The
confirmation callback receives only public facts and opaque references, never
Page/Frame/Document handles or any payment input value. Copied JSON evidence
cannot acquire the actual producer's private current-Page qualification.

An authoritative all-in aggregate uses `charge_basis: aggregate_inclusive` and
retains `tax: null` and `fees: null`; it does not fabricate a zero component or
assert that an included fee is absent. Explicit inclusion, finality, no deferred
charges, current currency/destination bindings and the all-in cap remain
mandatory. The existing itemized branch retains its numeric arithmetic rules.

The fixed-control port is disabled by default and is not exposed by the
read-only IPC. It uses the existing shared context lease and durable order
fence. Secure-entry handoff first drains outstanding reads and holds the lease;
no further inspection is allowed during entry. Resumption requires fresh
post-entry amount, delivery, payment-method and notice confirmation, with card
field reads still forbidden. The only dispatch target is the retained verified
public Pay button; no caller selector or dispatcher is accepted. Explicit live
gates and an actual branded port are required outside isolated test fixtures.
Neither a click return nor a timeout can establish success or permit a retry.

Readiness now reports public control observation, pending dispatch/method
bindings, and post-submit receipt verification as separate facts. A positive
receipt cannot precede the same first order. Submission is durably marked
unknown before the committing action, followed by ordinary merchant order/status
reconciliation; absent or unqualified receipt evidence retains unknown and the
one-copy fence. This batch is offline implementation and intercepted testing,
not live payment qualification, activation or permission for another item.

### Dormant resident-runtime caller

The launcher now attaches `close.payment` to the same resident browser context
and shared lease used by its read-only sockets. The ordinary runner supplies no
live gate. There is no new payment RPC, listener, browser, profile or credential.
The default-off caller performs no policy, ledger or browser reads.

The caller's fixed owner-only metadata files are
`/bridge/guest-card-actions/enhanced-payment-authorization.json` and
`/bridge/guest-card-actions/enhanced-payment-confirmation.json`. Neither is
created by installation or this implementation pass. Exact schema, ownership,
0600 permissions, expiry, revocation and one-copy KC scope are required. The
authorization explicitly records a single-item test mode; it does not invent an
enabled generic buying rule. It pins the actual existing guest claim and
observed-form head, validates their full anchored chain and exact scope, and
reads the existing real-order ledger. Payment and preparation may have distinct
intent IDs; the trusted authorization's immutable claim/head references provide
their binding. A prior unknown real-order outcome blocks a new entry handoff.

Entry startup is reserved synchronously. Browser inventory and private document
witnesses are read only while the shared context lease is held; that same lease
is transferred once to the fixed port. Once `entry_ready` is returned, browser
reads remain suspended until explicit resumption. The confirmation must contain
the returned entry reference and the original genuine post-entry timestamp,
explicit final amount/currency, delivery choice, selected card-method reference,
terms and notice answers. These facts are never defaulted to true or renewed by
rereading the file. Fresh quote reads receive current DOM observation times
while retaining the user's original confirmation time and expiry. No payment
input values or full destination values enter either metadata file.

The executable rehearsal uses the actual launcher and runtime readers with
isolated private files, an anchored synthetic guest ledger and a real temporary
SQLite order ledger. Only browser DOM and its final fixed click are intercepted;
no payment network is available. Passing it is offline integration evidence,
not evidence of live payment, payment-method availability or a merchant receipt.
Live gates remain off and the existing blank form/audit are preserved pending a
separate consolidated activation plan and genuine user entry/confirmation.
