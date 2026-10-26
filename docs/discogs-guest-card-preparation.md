# One-use guest-card preparation

The default-disabled exact-item preparation helper accepts the merchant's explicit
aggregate checkout total for the approved one-copy test, capped at $5.05 USD.
It does not need a separately displayed zero tax or fee row. Unknown component
values stay null and unverified; no unknown amount is filled with zero.
A deferred-total notice, changed aggregate amount, wrong identity, destination,
terms or authorization blocks preparation.

Preparation is not payment. The provider amount and terms still require review
before secure user card entry, and the result always says final payment quote
verification is false. The helper neither reads card data nor enters it, accepts
agreements, submits payment or establishes that a Discogs order exists.

Before the one injected action, the helper atomically creates and fsyncs a
permanent listing-specific uncertainty fence. A timeout, disconnect, process
crash or unknown result cannot retry, even with a new intent ID. The actual
dispatch port must revalidate the immutable scope and complete inspection
fingerprint immediately before dispatch. Same-button quote changes or revoked
authorization must cause no click.

The module creates no public endpoint, persistent directory, live browser port or
automatic payment method. The operator-assisted test used a private record
directory on the existing VPS, with the original fence retained as an owner-only
file. Its metadata helper ran in an ephemeral, network-disabled container with a
read-only root filesystem: only two source files were mounted read-only and the
record directory was writable. It had no browser profile or bridge-socket mount.
The operator used the existing private viewer for the single click; no production
checkout adapter or scheduler was activated.
Do not describe injected offline tests or a displayed form as an autonomous
buyer or a completed order.

## Observed hosted flow, October 26, 2026

| Stage | Observed normal control or evidence | Meaning and stop condition |
| --- | --- | --- |
| Existing cart | One selected item and seller; selected shipping; explicit USD subtotal, shipping and Total | Read the existing item; do not repeat Add to Cart. A changed item, destination, total or deferred notice stops. |
| Review | Visible `Go to Checkout` opens `/marketplace/checkout` | This route was exercised without entering payment details. Recheck the one-item summary, saved destination and aggregate Total. |
| Payment choice | Visible `PayPal` and `Debit or Credit Card`, powered by PayPal | The card control was previously observed as a link with `data-funding-source="card"`, `aria-label="Debit or Credit Card"` and `aria-disabled="false"` in the provider buttons frame. These are observed selectors, not proof of an order or a reusable saved card. |
| First card initialization | One operator-assisted normal card-button click after durable fencing | It returned a visible PayPal-session-expired message. No card form was observed, no card data entered and no capture/approval control operated. The first fence stayed consumed; no blind retry occurred. |
| Initial reconciliation | Normal `/sell/purchases`, all statuses, Not Archived, after the attempt | It displayed zero orders and cart count one; this first view did not cover archived orders. |
| Full reconciliation | Normal Purchases filters `Archived: All`, `Status: All`; `/sell/purchases?archived=A&status=All&limit=50` | The fresh view displayed `0–0 of 0`, `No orders found` and cart count one. This includes active and archived orders, but does not prove that no provider draft exists. |
| Reviewed recovery | Normal cart → `Go to Checkout`, fresh exact-item/total/destination checks, then one separately fenced card-button click | The guest form opened without an error or challenge. The original fence remained unchanged and the fixed recovery slot was consumed. The later navigation-reopen attempt below also executed once and is spent. |
| Reviewed navigation reopening | Fresh all-status/all-archive zero-order review, the existing merchant checkout at $5.05 USD, then one separately fenced card-button click | It returned `SESSION_EXPIRED`; no guest form opened. The navigation slot was consumed, all earlier records were retained and no retry followed. |

The merchant origin is `https://www.discogs.com`. The observed SDK buttons frame
uses `https://www.paypal.com/smart/buttons`; query parameters are deliberately
not recorded because they can contain session data. No direct private endpoint
was called. Any challenge, explicit access denial, login requirement or unknown
initialization outcome stops the workflow rather than triggering another click.

The public merchant script was exported through Firefox's normal Debugger file
download control for offline inspection. Its SHA256 was
`1933d4028b07f0194e350adc6d4608ac1a9364344ab5b52c0a3d26532f7fb27a`.
The registered creation callback awaits payment creation and returns a transaction
ID. The approval callback awaits payment capture, requires an order ID, then
sets the thank-you state. Its Purchases link is generic, not an exact receipt.
This proves frontend sequencing, not when the backend first commits an order.
No manual unpaid-order branch or saved-card/vault configuration was found in
the merchant component.

The exact observed message was “Your session with PayPal expired. Please return
to your cart and try your purchase again.” In that captured script it maps to
the GraphQL error code `SESSION_EXPIRED`. The creation and capture callbacks use
the same error handler, so the message alone cannot identify the failing branch.
There was no form, card entry, approval or capture during the observed attempt.
This is an expired checkout-session response, not evidence of an account
limitation or a new access denial.

## Reviewed recovery boundary

Independent review accepted the combined expiration, absence of card entry or
approval, and fresh all-status/all-archive zero-order view as the operational
basis for **one** normal cart-to-fresh-checkout recovery. This is stronger than
an empty order list alone; it is not proof of perfect merchant consistency or
absence of a provider draft. Use the visible cart and `Go to Checkout` controls,
not an inferred session-creation endpoint. Recheck the exact item, aggregate
$5.05 USD total, saved destination, terms and normal access before dispatch.

After focused tests and independent review, one recovery executed with a separate
immutable reconciliation record bound to the original fence and new exact scope,
plus the fixed second-attempt fence before the click. The original consumed fence
was retained byte-for-byte in the same directory. Neither record was deleted or
reset. Directory substitution, reuse of a spent slot and automatic retry chaining
remain prohibited; the separately bounded navigation-reopen slot below is also
now spent.

## Guest form observed after recovery

The form was visibly observed at 14:32:59.258 UTC on October 26, 2026
(Unix time `1793025179.258`),
under recovery intent `2c6ce1cd-cbd9-4acb-ab58-748c55e630f4`. The reviewed test scope
was one copy of listing `1366950910`, release `3422100`, seller `Mailman21842`.
The fresh merchant review showed one item, the saved shipping address and Total
$5.05 USD before the recovery click.

The blank guest form showed email, card number, expiry, CSC, billing country
(United States), first/last name, street, apartment, city, state, ZIP and mobile
fields. These are visually observed field labels; input selectors and exact
hosted-field frame identities have not yet been verified. No field values were
entered or recorded.

**`Ship to billing address` was checked by default.** This means the original
Discogs shipping destination cannot be assumed to remain the provider delivery
destination after billing entry. Reverify delivery against the approved saved
destination before any final payment, without reading or logging card values or
retaining the full address. The checkbox was not changed during this observation.

The visible notice says continuing confirms the buyer is 18 years or older. The
final blue control reads `Pay $5.05`. The merchant still explicitly showed USD;
a separate provider currency code was not observed. Do not promote that button
label into a fully verified final quote or accept its terms automatically.

No card data was entered, no checkbox changed and `Pay` was not pressed. Order
and payment remain unverified; initialization may have created a provider draft.
The successful form opening is not a receipt, a purchase or proof of autonomous
card payment. The consumed recovery slot cannot authorize another initialization;
the separately reviewed navigation-reopen slot below also executed and is spent.

## Subsequent navigation and handoff correction

After the successful form observation, the user reported a white screen and
confirmed that no card details had been entered. The current tab showed
`chrome://browser/content/blanktab.html`; its visible Back-history menu retained
`Discogs Checkout` immediately before that entry. This establishes same-tab
navigation, not a demonstrated tab closure. The container and Firefox process
remained running with no OOM or service restart. The input responsible is not
proven. A prior intended `Ctrl+Home` scroll and later unexpected shortcut/menu
behavior make shortcut translation a risk; do not attribute it to the site or
claim a verified keyboard cause.

A separately reviewed single Back-history selection returned to the normal
merchant checkout, showing the same one-item seller summary and Total $5.05 USD,
without a challenge or resubmission prompt. The guest fields were absent and the
payment-choice buttons were visible again. At that point inspection stopped:
neither button was clicked and no third initialization was performed. A later,
separately reviewed navigation-reopen attempt is recorded below; the earlier
consumed initialization records remain intact.

For future secure handoff, position the form with mouse scrolling only, then
freeze all agent input, screenshots and page inspection while the user enters
card details. Do not use a keyboard shortcut to reposition the payment page.
Resuming inspection requires the user's explicit ready signal and privacy-safe
selection of only the necessary non-sensitive evidence.

### Reviewed navigation reopening — executed and spent

A separate review accepted preparing **one** fixed navigation-reopen slot after
the user's confirmation of no card entry or `Pay` action, a current all-status /
all-archive Purchases view showing zero orders, and the observed blank-tab Back
history and return to merchant payment choices. This is an implementation
exception to the default denial, not reuse of either consumed initialization.
The immutable navigation-review and attempt records linked the raw SHA256 hashes
of the original initialization fence, recovery reconciliation record and
recovery-attempt fence, plus the new exact one-copy KC scope and $5.05 USD ceiling.
The prior records were retained; the new strict review and attempt fence are
consumed. Review expiry, order-history freshness (observation plus 300 seconds),
current quote and cancellation gates applied before dispatch.

The fresh all-status/all-archive zero-order view was observed at
15:13:10.354 UTC (Unix time `1793027590.354`). Exactly one navigation-reopen
dispatch followed under intent `d4b81e41-1351-414b-8b77-1bab9953e629`. At
15:14:07.142 UTC (Unix time `1793027647.142`), the same merchant checkout and
$5.05 USD summary showed the exact session-expired message quoted above. No
guest form opened, no card data was entered and `Pay` was not clicked. The
helper returned `guest_card_initialization_uncertain` with
`outcome_requires_reconciliation`; order and payment remain unverified.

The returned old checkout session was expired; this is not an inferred access
denial. A future legitimate session refresh would require the normal cart →
`Go to Checkout` flow, but that was neither executed nor authorized as a follow-on
in this step. No retry occurred. This spent slot permits no automatic follow-on
initialization, record reset, directory substitution or retry chain.

Only mouse scrolling may preposition the form for handoff. Then freeze **all
screenshots, DOM/page reads, logging and agent input** during human card entry.
Resume only after the user's explicit ready signal and a privacy-safe handoff;
do not inspect or record sensitive form values.

### Fresh-session preparation — not executed

After that expired attempt, normal All-archive / All-status Purchases again
showed zero orders at Unix time `1793027941.218`. A second tab's normal cart
showed the same single KC listing and Mailman21842: $0.10 USD plus selected
Economy Media Mail shipping of $4.95, Total $5.05 USD, with the saved destination
present and unchanged. `Go to Checkout` was **not** pressed; the first tab still
contained the expired checkout. This reconciliation does not establish absence
of a provider draft or a completed payment.

After `SESSION_EXPIRED`, preparation must start from the normal cart and create
a fresh checkout through `Go to Checkout`; a Back-restored checkout is not a
fresh session, even when its summary still shows the approved total.
`prepareFreshGuestCheckoutSession` implements this sequence as an offline-tested
injected coordinator. The supervised operator wiring below is installed in its
no-action waiting state. The next live UI run remains unverified; this is not
autonomous checkout.
The four ports are `openCartViaVisibleControl`, `inspectCurrentCart`,
`clickGoToCheckout` and `inspectFreshCheckout`. A scope-bound explicit human-ready
token is required before any navigation. The current helper/CLI freezes one human
availability deadline of at most 300 seconds, bounded by approval, readiness,
review and order-history expiry. Each stage has at most 120 seconds within that
same availability window; guest initialization is additionally bounded by the
checkout observation plus 20 seconds. CUA arming uses the availability deadline,
not the first stage's deadline. Each mutating port must call the supplied
`revalidateBeforeDispatch()` immediately before its effect after any await;
revocation, expiry or cancellation makes that guard fail and aborts its signal.
The coordinator rejects Back/history-restored documents and returns only fresh
merchant-review provenance, with initialization and payment still disabled.

No new live initialization may run until the user explicitly signals readiness.
The prepared sequence is: fresh order-history reconciliation → exact cart item,
quote and destination checks → normal `Go to Checkout` → fresh merchant review
checks → a separately reviewed, durably fenced card-button click → mouse-only
form positioning → freeze all screenshots, DOM/page reads, logging and agent
input for secure human card entry. Earlier fence and review records remain
unchanged. This sequence has not yet been dispatched.

### Operator wiring — installed, fresh readiness required

`scripts/discogs_guest_checkout_operator.mjs` is a callable metadata stdio
harness around that coordinator. Its verified VPS execution is a
network-disabled, 128 MiB helper running as UID 10001, with public source mounted
read-only and only the private fence directory writable. A process lock and an
append-only sequence/hash chain anchor all five prior records without changing
them. The ready UUID is consumed before the first instruction. The preceding
ready signal has expired; a **new explicit human-ready signal is required after
code validation and installation**, alongside fresh All/All zero-order evidence
after the latest expiration and confirmation of no card entry or `Pay` action.
The installed helper reported `waiting_for_user_ready`; no ready UUID, operator
attempt or UI instruction was consumed during installation. Its anchor hash
matched the independently computed manifest of all five preserved records.
The command runner must keep stdin open (the verified invocation uses its
interactive terminal mode with `scripts/operator_stdio_transport.sh` to disable
canonical buffering and echo); a closed input stops without producing an action.
Canonical input held the earlier 1,544-byte start line before the server parsed
it, with no ready record or UI request. The regression uses a real PTY and
checks exact 1,544-byte and 8,192-byte start frames plus a 4,096-byte ACK.
`--transport-check` is a separate no-action parser: it never opens the state
directory, consumes readiness or calls the operator runner; it prints only
frame sizes/types and can run with no state or browser mount.
The staged diagnostic was also exercised through the actual SSH/PTY path with
no state or browser mount: it measured 1,544 and 8,192 bytes for two complete
START frames and 4,096 bytes for an ACK, then returned `transport_complete`,
three frames and exit zero. Send tool input in chunks of at most 2,048 ASCII
bytes, appending the newline only to the last chunk; a combined large tool
write was rejected before execution. Chunked newline framing passed. The
waiting operator uses the same noncanonical/no-echo launcher; the old readiness
signals were not replayed and no real ready/attempt record was consumed.

The operator sequence is:

1. `open_cart` → the integration owner uses the visible cart control, inspects
   the actual page and acknowledges exact item, quote and destination evidence.
2. `go_to_checkout` → the owner uses normal `Go to Checkout`, inspects the fresh
   merchant review and acknowledges it.
3. Only after a durable linked attempt record, `open_guest_card` → the owner
   performs the reviewed card-button action and acknowledges either the visible
   form or error. The outcome is appended; uncertainty stops further dispatch.

Each instruction and acknowledgement binds intent, ready UUID, request, stage,
scope and deadline. These are manual visual attestations, not automatic DOM
verification. The page metadata explicitly uses `operator_verified_current_screen`,
and document/control UUIDs are operator-assigned observation references, not
browser-generated proof. `scripts/discogs_guest_cua_operator.mjs` is bound to the
existing supported `hostView`, unarmed; its installed source copy matches the
reviewed factory. All 64 focused tests passed (46 preparation, 11 real-process
CLI and 7 CUA adapter tests), and independent review accepted the integrated
path. No new live initialization has run. There is no automatic retry loop or bypass of an
unresolved outcome. After mouse-only positioning, all UI reads and agent input
freeze for human card entry. Neither harness is a production payment adapter.

`prepareOperatorAckTemplates` precomputes only serializable, unattested metadata;
it consumes no readiness and performs no file or UI action. `buildOperatorAck`
binds the current request to those unchanged templates and requires fresh
operator proof for that stage. Precomputation does not attest a future screen or
permit reuse of an earlier observation.

### Local CUA/CLI timing measurement — synthetic only

A local fixture staged at approximately Unix time `1793034105.666` was exercised
through the real CLI and supported CUA, sequentially. The BEGIN request followed
14.295 seconds later; the run reported 52,272 ms elapsed, exit zero and four audit
records. Three actual fixture clicks produced these observations:

| Fixture stage | Observed Unix time | ACK proof age | Generated ACK bytes |
| --- | --- | --- | --- |
| Cart | `1793034126.118` | 7,734 ms | 2,118 |
| Checkout | `1793034138.444` | 7,472 ms | 2,428 |
| Blank guest form | `1793034151.341` | 6,585 ms | 430 |

The fixture's guest-click DOM mark was `1793034151151` ms, 12.707 seconds after
the quote observation. This measures actual CUA/CLI round trips against
synthetic local state, with no Discogs request or new live readiness. It is not
hosted-page timing, a full run of the exact CUA adapter factory, or proof of live
checkout, order creation or payment.

### Subsequent hosted Cart-only attempt

The October 26 17:11 UTC readiness produced only a consumed ready record,
`guest-checkout-000002.json`, at Unix time `1793034785.846`. All/All Purchases
showed zero orders. The operator opened the existing one-item KC cart; it did
not invoke Go to Checkout, guest initialization, card entry or payment.

The Cart instruction's 120-second stage ended at `1793034905.854`; the settled
cart observation was `1793034888.101`, leaving 17.753 seconds after 102.247
seconds of operator work. Building and sending the two-part ACK had a reported
tool wall time of 8.6 seconds, excluding the intervening model time. The exact
server ACK-arrival timestamp was not retained. The process stopped at its
deadline, so the fixture timing above did not establish reliable hosted timing.

An earlier pre-click `operator_binding_or_screen_unverified` was an internal
JavaScript validation error, not a platform approval rejection or site denial.
Replaying the captured fields offline accepted ages through 20 seconds and
rejected 20.001 seconds; a mocked replay in the same CUA runtime also accepted
them. The original failed predicate was not logged and remains unverified.
No guard should be weakened on the assumption that this was simply stale data.

The replacement under development removes navigation ACKs: normal supervised
CUA navigation is followed by a one-shot, locked claim at the current checkout.
It retains the same ledger and historical fences, current transaction checks,
and a durable pending-attempt stop before one form-opening click. This remains
supervised development tooling, not autonomous server checkout. Purchase testing
is on hold until the no-submit path is reliable.

The reviewed command contract uses `--claim-current-checkout` with one complete
metadata JSON document on stdin, then exits. It accepts the exact scope,
current readiness, fresh All/All reconciliation, normal Cart-to-new-Checkout
provenance and current inspection. It performs no browser or payment operation.
Only after the existing locked ledger has fsynced linked ready/review/attempt
records may it return `checkout_claim_granted`. Human-readiness claims retain
their original 20-second permit. There are no intermediate navigation ACKs or
terminal-ready process to keep alive.

`--record-claim-outcome` records one outcome bound to the exact claim hash,
intent, ready reference and scope. It cannot replace an outcome or renew click
permission. Pending, unknown, access-restricted and visible-form attempts block
new claims in both this path and the older operator. No legacy file or consumed
readiness is cleared. These commands are not proof of repeatable live checkout.

A diagnostic run without a person standing by uses the distinct
`testAuthorization` input, sourced from explicit no-submit test approval. It
retains that approval's original time separately from a fresh operator attempt
issued for at most 300 seconds; it never fabricates human payment readiness.
The claim explicitly reports human payment readiness false and card entry,
payment and final submission disabled. The same original checkout observation,
transaction checks and pending-attempt rules still apply. Failures report only
fixed, allowlisted stop reasons, with no exception text or account data.

For no-submit diagnostics, the backend now returns staged authority marked
`requires_execution_revalidation`, with the original fixed job/scope/history
and evidence expiry. It still rejects initial checkout observations older than
20 seconds. The self-contained `makeCurrentCheckoutCuaActor` must then capture
the supported viewer itself, timestamping before either capture await, and bind
the model's exact current-screen proof to that one-use observation. The actor
checks its age is at most 20 seconds immediately before clicking. Capture cannot
renew the job deadline; there is no terminal round trip between that capture
and click. Scope, quote, destination, terms and control bindings must match.
Consumption precedes the awaited effect; cancellation or uncertainty prevents
retry. A same-runtime reservation prevents constructing a second actor for the
same claim or request. Lost runtime state is not permission to reconstruct it.

The live no-submit run on revision `42aaf2f` reached fresh merchant checkout,
but did not initialize the guest form. One input-producer error was reproduced:
giving a quote observed 17.227 seconds earlier the job's `now + 300` expiry made
its lifetime 317.227 seconds. The validator correctly rejected it. Bounding the
producer expiry by `min(scope expiry, observation + 300)` fixes the input without
changing its observation time; the regression preserves that rejection.
The corrected claim `b9c5a24a65a8fdb738b4876fbd3dd534d4fcd449d15caef27d6e8861c866c4df`
was granted, but the actor guard ran 22.887 seconds after observation and returned
`expired_before_effect`, `clicked:false`. Its conservative unknown outcome is
retained. This is not evidence of form initialization, order creation or payment.

An unknown may be reconciled for another no-submit claim only with a typed,
hash-bound proof of that original definitive no-effect result, matching the old
request and the old outcome's timing, plus newer complete All/All order history.
A later expired-grant probe, timeout or exception does not establish no effect.
The new review retains that proof; no old claim, outcome or fence is replaced.
This path is supervised CUA development tooling, not autonomous server checkout.

### Successful actor-local no-submit observation

On October 26, 2026, revision
`12ab704eeff8a31d22632f176838f98766a65cb2` passed its exact CI and independent
review. Staged server hashes matched; the serialized actor was installed without
module dependencies or a browser restart. The exported function source hash was
`a49c89eb700cf6773dc0ca15882feb8afd9f1b0ff04cd678eb62b43458f88e9a`;
its whitespace-only installation form had hash
`41a5ecec9ba283f0756798a8855b451162ec4b70a958bf9a8d65e5b5aa3804fe`
and 8,280 characters. The installed runtime's length and diagnostic checksum
matched that copy. Fresh All/All history showed zero
orders before the test. Normal item/release links verified item `1366950910`
and release `3422100`. The current cart and fresh merchant checkout showed
one item from seller `Mailman21842`, item $0.10 plus
selected Economy/Media Mail $4.95, aggregate $5.05 USD and the approved saved
destination. No cart or shipping selection was changed.

The original blocked claim and its unknown outcome were preserved. A new review
retained the exact definitive no-dispatch proof and newer order reconciliation.
The new claim was
`5877754b846069fc0d3b8e096f044e25792f44fef58c02b5b07cc2dcc825b17f`.
The actor captured its own checkout observation at Unix time `1793038050.410`
and dispatched once with that observation **11.168 seconds old**. No terminal
round trip occurred between capture and dispatch. The blank guest form was
visibly present at `1793038073.351`; outcome `guest_form_visible` was recorded
in `guest-checkout-000010.json`, whose hash is
`1cdc66a4ef7ecfe21ed525580687375cf41496b102d173e144caa3f27501849c`.

Observed fields were blank, including email, card number, expiry, security code
and billing fields. The lower form displayed **Pay $5.05**, default-checked
**Ship to billing address**, and an age-18 notice. No details were entered;
no Pay, approval or capture action occurred. The actor was consumed and frozen.
Provider currency code, final post-entry destination/terms, merchant order
creation and payment were not verified. All/All history was not rechecked after
initialization, so this observation does not establish that no merchant order or
provider draft exists.

Browser memory was 995.5 MiB of 2 GiB before the run and 1.143 GiB afterward;
a subsequent passive process check still showed 1.143 GiB. OOM and restart
counters remained false/zero. These few samples show the run stayed within its
limit; they do not diagnose a memory leak or establish long-duration stability.
The current form was last observed blank and left untouched; an idle form is
not a promise that its provider session remains usable indefinitely.

### Repeatability gate and autonomous-server gap

Offline replay must stitch the real claim/outcome commands to the exact
serialized actor, with intercepted view actions: delayed transport followed by
fresh actor capture, one card-control effect, zero card-entry/Pay capabilities,
idempotent accounting and durable refusal of duplicate/pending/unknown claims.
Stale or changed observations, cancellation and expired scope must produce zero
new effects. Lost-runtime recovery must request a fresh claim and stop when the
audit is pending or unknown; it must never replay an old grant into a new actor.
The runtime registry does not persist across a new JavaScript realm, so that
last rule remains an installation/integration requirement, not cross-runtime
revocation proved by the registry. This is fixture evidence, not another live
success.

One additional bounded live repetition is a conditional plan, not enabled work:
preserve the blank form until its disposition is reviewed; verify no entry,
Pay, approval or capture, observe normal SDK dismissal, then obtain newer
complete All/All merchant history showing zero orders. A new immutable review
must link the successful claim/outcome and this disposition before another
exact-scope no-submit claim may run. A challenge, order, incomplete history or
uncertain disposition stops it. No user card standby or test purchase is needed.
The current `guest_form_visible` guard remains in force. The append-only disposition command described below now supports recording
that reviewed evidence; a zero-order view alone still cannot mint another claim.
No disposition has been recorded for the live form.

The captured checkout source distinguishes a creation transaction ID from the
merchant order ID returned after approval/capture. That supports treating an
unapproved payment draft separately; it does not prove backend order-creation
timing or guarantee merchant-history consistency. Fresh reconciliation plus
normal dismissal provides an operational basis for one reviewed repetition,
not proof that all provider drafts are absent. A second controlled pass would
demonstrate one repetition, not unattended reliability across all conditions.

Current success depends on Mac-hosted CUA and manual screen attestations. The
smallest useful production step is one fixed enhanced-checkout preparation port
inside the existing hosted Firefox service: actual sanitized DOM/frame evidence,
normal Cart-to-Checkout controls, the shared durable claim and same-process
fresh validation before one form-opening effect. The server DOM/control/frame
association must first be captured and verified under its permitted access;
no hidden endpoints or broad browser API are implied. This replaces the manual
actor rather than adding another coordinator. Live order submission, final
payment and receipt reconciliation remain separate, unverified requirements.

At this checkpoint the web and browser services were active, with no restart or
OOM since their 13:42 start; browser memory was 850.7 MiB of 2 GiB. Monitoring
remained inactive. These health observations do not verify checkout or payment.

## Amount and runtime evidence

The summary computes its aggregate from item prices, shipping and taxes. Its
tax row appears only for a positive amount, while missing data also falls back
to zero in rendering. An absent tax row must therefore remain an unknown
component; it must not be recorded as confirmed zero tax. Preparation may use
the explicit merchant aggregate, but provider amount and terms still require
verification before any final payment.

The earlier one-off preparation tests used a 30-second default and 90-second
maximum, bounded by their approval/evidence expiry. Those historical limits do
not describe the current staged helper/CLI's availability and stage deadlines
above.
The private operator transport has no browser, network or payment access. A
fresh operator revalidation precedes its sole CUA click, and the durable record
is retained on timeout or uncertainty. This is test evidence, not an enabled
production checkout adapter. Provider field labels and the visible pay control
are now observed, but their DOM selectors, final quote/delivery/terms validation,
payment, receipt normalization and order-versus-paid reconciliation remain
unverified.

## Verification and provenance

The hosted-flow observations above are from October 26, 2026. The source hash
identifies the already-loaded public script exported for offline inspection;
the full third-party script, session tokens, card data and address details are
not committed. Source/frame origins and observed selectors are recorded above;
provider query strings are intentionally omitted.

`tests/test_discogs_guest_card_prepare.cjs` covers the preparation policy,
immutable dispatch revalidation, exclusive durable fencing, competing workers,
process crash/restart, timeout/disconnect and unknown-outcome stops with offline
injected ports. It does not establish live provider payment or receipt behavior.
The recovery tests additionally cover incomplete or stale reconciliation,
approval binding, original-record tampering, competing recovery workers and
crash/restart. Its dispatch deadline includes the order-history freshness limit.
The fixed expired-session recovery and navigation-reopen slots are both spent
and cannot be reused. Neither path permits an automatic follow-on chain. The
navigation attempt's session-expired outcome is not a successful provider-form,
order or payment verification.


### Fixed server preparation implementation (not activated)

The existing Firefox launcher now accepts an optional, locally supplied
`guestPreparation` configuration. Its default runner does not supply one, so
no preparation socket is opened by default. This is actual browser-context code,
not a renamed CUA actor. The fixed operation reads normal rendered DOM and frame
associations in the Firefox process, validates the existing exact KC item and
cart, follows the verified Go to Checkout control, then revalidates the fresh
checkout and card control before a single fenced initialization. It has no card
entry, Pay, approval, capture, arbitrary URL, selector or script operation.

The separate fixed IPC address is `/bridge/sniper-guest-prepare.sock`, mode
0600, owned by the existing browser UID. It does not grant the scheduler group
access to this write operation. The ordinary read-only socket and preparation
port share one browser-context lease. The original access-stop marker and all
old claim records remain unchanged. Local trusted `accessGuard` and `authorize`
callbacks must authorize the exact operation and bind the approved destination
reference/digest; a request cannot approve itself or choose a directory. The
runner has no production issuer configured. Adding that issuer or activating
this socket is a distinct remaining integration step, not an accomplished live
capability.

The ledger is checked before any preparation DOM access or navigation. A visible
form, unresolved/unknown attempt, or crash leaving only a ready/review prefix
blocks a new preparation. Cancellation cannot resume a later click. Actual
underlying work must settle or the context remains fenced against overlapping
work. Probe results contain only allowlisted evidence booleans and missing-field
codes; no address lines, payment inputs or provider query parameters are returned.
The probe does not navigate, click, write a claim or establish transaction safety.

Evidence levels remain separate. Existing source/DOM observations establish the
Go to Checkout selector, enhanced summary prefixes, provider button origin and
card control semantics. Positive server integration fixtures extend the available
summary with explicitly synthetic quantity/title/condition/address markup; they
are not captures of the current hosted form. The captured summary-only shape
fails closed with specific missing continuity fields. The server port therefore
has not been live-qualified. After initialization it records `unknown` rather
than claiming a successful form from unobserved guest-field anatomy. This safely
fences another attempt but does not finish automated checkout.

### Append-only blank-form disposition

`--record-form-disposition` accepts only reviewed normal SDK dismissal evidence
linked to the exact successful claim and outcome hashes. Required ordered
observations are: a fresh blank form with no card entry/Pay/approval/capture;
normal visible SDK close; form absent on a supported merchant page; then fresh,
complete Purchases history with Archived All, Status All and zero orders. This
appends a new disposition record and never edits or deletes the successful
outcome. It does not prove provider draft cancellation or backend order timing.
Unknown or access-restricted outcomes cannot be resolved through this path.

The disposition's `expires_at` is the deadline for recording its original fresh
observations, not reusable buying authority. Once written it is historical audit
evidence. A later no-submit claim needs a new exact scope/test authorization,
the latest disposition hash, a genuinely newer complete order-history read after
the disposition write, and fresh checkout evidence. Human/payment and legacy
paths remain blocked. A new claim consumes the latest audit head, so a concurrent
worker or partial ready/review write cannot replay the same disposition.

No live dismissal, new order-history read, disposition write, browser restart,
preparation activation, new initialization, card entry or payment was performed
for this implementation. The currently successful live form claim remains
pending its reviewed disposition. The next live phase must first confirm blank
state and normal dismissal is supported, record the reviewed observations, and
qualify the actual server DOM/destination/frame association without inferring
missing fields. Another initialization is not permitted merely because these
offline fixtures passed.


### Live disposition checkpoint after 5ba29e6

Exact revision `5ba29e6ea2247c9c9839493c8b8de5813e905063` passed all required
remote checks: Discogs smoke, isolated PostgreSQL feedback, and sandbox validation
(the production job was intentionally skipped). The existing form was observed
with blank email, card, expiry/security-code and billing inputs. A first coordinate
click landed on noncommitting page space after the viewport moved; it did not
close the form. The close control was re-observed at its current position, clicked
once there, and the subsequent screen confirmed the normal guest form disappeared
while the merchant checkout remained. No card entry, Pay, approval or capture
occurred. No provider initialization was repeated.

The blank observation time was `1793040670.238`; verified dismissal/form-absence
was `1793040702.485`. Normal account navigation opened Purchases. Selecting
Archived All left Status All and Show 50; at `1793040743.371` the canonical
`/sell/purchases?archived=A&status=All&limit=50` view displayed both `0 - 0 of 0`
and `No orders found`, with no pagination or challenge. This is a fresh complete
merchant-history observation, not proof of provider draft cancellation or backend
order timing.

The CI-passed metadata helper was staged only on the container's existing writable
`/tmp` mount, without restarting Firefox or weakening the read-only root filesystem.
An initial `docker cp` staging operation failed because the root filesystem is
read-only; no helper or audit write executed in that failed stage. Normal archive
extraction as the existing browser UID into `/tmp` succeeded. The helper then
appended `guest-checkout-000011.json`, disposition hash
`27c067fd52d21684e22431380169b44d834ef1bae394bec1a313721baf74388f`.
The successful record `guest-checkout-000010.json` retained its exact original
hash `1cdc66a4ef7ecfe21ed525580687375cf41496b102d173e144caa3f27501849c`.
The inspector reports 11 records, `reviewed_form_disposition`, ordinary
`init_allowed:false` and disposition-only `no_submit_init_allowed:true`. This
last flag is not authority to initialize: a new exact authorization, later
complete order-history read and actual checkout evidence are still required.

Firefox remained on `sniper-browser-view:health-b356701`, started at
`2026-10-26T13:42:00.456306403Z`, with no restart or OOM. The new browser image
and preparation listener have not been activated. Its trusted local issuer and
approved destination digest are still unconfigured; current hosted DOM/frame
continuity has not yet been qualified by that code. These are implementation and
live-evidence gates, not a new request for broad purchase permission. No new
order, payment or live server initialization was attempted in this checkpoint.


### Bounded hosted read-only qualification policy

The runner can load `/bridge/guest-qualification.json` as ordinary local policy,
not a credential. It must be an owner-only 0600 regular file belonging to the
existing browser UID. Absence or valid expiry leaves qualification off. The fixed
policy admits only `probe_guest_evidence` for KC item `1366950910`, release
`3422100`, seller `Mailman21842`, one copy and the existing $5.05 USD ceiling.
It never grants `prepare_guest_form`, navigation, clicks, card entry or payment.
The socket remains owner-only and the original access-stop marker is untouched.

The job lasts at most 30 minutes and has exactly two durable single-use slots:
inspect, then confirm the saved destination. Exclusive, synced slot records and
an immutable job identity prevent policy edits or process restart from resetting
the budget. A first read returns public bounded structure/grade diagnostics and
an opaque reference to the actual Page/Document; initial destination match is
unknown. Address contents and its digest are retained privately in browser-service
memory and are never returned through IPC, written to policy or logged.

The operator must visually verify the user's already-approved saved address,
then add a same-job acknowledgement of that document reference. Confirmation
requires a reference no older than five minutes and a visual acknowledgement no
older than one minute. It rechecks the same document, URL, browser context and
unchanged private address snapshot under the shared context lease, consuming the
reference before asynchronous work. A restart, changed address/document, replay,
challenge, expired scope or uncertain read fails closed. A successful match is
read-only evidence; it does not qualify a final quote or enable initialization.

The probe's authorization schema is distinct from transaction authorization. It
does not invent an expired-session event, order-history result, quote, readiness
attestation or destination hash merely to inspect a page. Observed public
canonical grade labels such as `Poor (P)` normalize only when the label and grade
token agree; an ambiguous or mismatched grade stays unknown.


### Actual hosted qualification and retained parser diagnosis

Revision `6742a2783aa4d811bcec28009adcdd010340227e` passed exact CI and was
built on the existing VPS without build networking. Image
`sniper-browser-view:qualification-6742a27` started at
`2026-10-26T19:13:02.587717345Z`; profile, loopback ports, isolation, durable
stop and audit hashes were preserved. Its private qualification socket and
policy were mode 0600 under the existing browser UID. No new credential,
process identity, public listener or payment capability was added.

Normal UI navigation reached the existing one-copy cart and enhanced checkout.
Viewer text entry initially truncated a URL into an unintended search; that
unrelated challenge was left untouched. The normal saved cart entry then reached
Discogs without a challenge. No cart content or shipping option was changed.
The separate exact item tab remained navigation-only with a blank body.

Both authorized read-only slots executed in the real Firefox service. They
verified the enhanced summary, seller, item $0.10, selected shipping $4.95,
aggregate $5.05 USD, explicit one-item declaration, item-price continuity,
provider-frame association and the unique enabled card control. A fresh visual
check of the already-approved saved address permitted confirmation of the same
unchanged document/address snapshot. No address or digest was exported. The
reader still reported listing-detail, title-continuity and condition-continuity
as unsupported; media and sleeve diagnostic values were null. The job and both
spent slot receipts remain retained. No new card-form, Pay, approval or capture
action occurred, and merchant/payment status remains unverified.

The retained public bundle with SHA256
`1933d4028b07f0194e350adc6d4608ac1a9364344ab5b52c0a3d26532f7fb27a`
renders the condition DIV as `Media: ` + media value + BR + `Sleeve: ` + sleeve
value. The old text walker tested positive rectangle width before treating BR
as a separator. A source-derived fixture with a zero-width BR reproduced null
grades; the corrected walker preserves a rendered BR separator while retaining
hidden/style/ancestor checks. Hidden breaks and duplicate condition containers
remain unverified. This proves a deterministic parser bug in that structure;
it does not establish that the live BR geometry was measured or that this is
the only cause of the earlier null result.

No alternate detail or release selector is justified by the blank item body.
The next reader reports only fixed detail error/readiness enums and capped
main/release-link/grade counts. It cannot invent an identity, title, grade or
access denial from missing content. The accepted test copy's Poor/Generic
condition remains distinct from the disabled 56 Nights VG+ rule.

A subsequent live check requires a new reviewed, bounded read-only job, preserving
all old receipts; it must not reuse either spent phase. The proposed first read
would verify the corrected condition output and classify the detail failure.
It must stop on a restriction and cannot navigate, initialize a form or submit
payment. No additional qualification read was executed by this parser fix.


After exact CI passed, image `sniper-browser-view:parser-5e8fd29` was installed
as a two-file delta from the previously verified image. It started at
`2026-10-26T19:38:00.371319963Z`; both deployed source hashes matched the
reviewed commit. The private socket/policy remained mode 0600, the historical
access stop and successful outcome/disposition hashes were unchanged, and no
OOM occurred. The prior spent policy was archived with both use receipts intact.

Independent review accepted one new exact-KC read-only job and one normal load of
the previously verified same-item `/sell/item/1366950910` route, stopping on any
challenge, denial, redirect or continued blank page. Because the issuer pins the
job at startup, the new bounded policy was staged with the parser restart. Its
expiry is `2026-10-26T20:07:56.158Z`; both inspect and confirm slots were verified
unconsumed. No site navigation or qualification read ran after this restart, and
no initializer/payment authority was enabled. Ephemeral old document/address
bindings were invalidated by restart; that restart is not evidence of SDK dismissal.


### Semantic title comparison repair

The next reviewed two-call live job ran on the ready exact KC item and enhanced
checkout pages. The single normal canonical item request redirected to the same
item's `/shop/item/1366950910` page and rendered normally, without a challenge.
The service verified supported exact listing/release details, seller, quantity,
Poor/Generic condition continuity, the $5.05 USD merchant aggregate, provider
frame/control association and the unchanged approved saved address. The sole
remaining diagnostic was `title_continuity:false`. Both job slots are spent.
The item page's $0.75 shipping display was kept separate from the unchanged
selected $4.95 Economy/Media Mail cart shipping; no final-tax assertion followed.

The old collector hashed the complete checkout H3 label against the exact release
link's title text. These are different semantic units: the observed item title is
`Get Down Tonight`, whereas checkout displays
`K.C. & The Sunshine Band* - Get Down Tonight (7", 45 RPM, Single, Stereo)`.
The retained `fRu` source explicitly renders artist spans, a literal separator
span, the `release.title` span and a separate format span. The new comparison
extracts that source-shaped title span, then applies only Unicode NFC and
whitespace normalization to detail/checkout titles. Case, punctuation, title
parentheses and edition text remain significant. No candidate title is hardcoded,
and the exact listing/release/seller checks remain independent requirements.

The extraction rejects ambiguous headings, hidden/nested/unknown child shapes,
missing or repeated separators, empty titles and unsupported artist/format
structure. Source-derived regression markup uses the observed public labels;
it is not a saved raw live DOM. Wrong core titles and editions must still fail.
The current narrow KC implementation does not support the renderer's generic
no-artist or empty-format branches without further evidence.

Cart item-link labels are a separate unresolved display contract. Their complete
visible label digest remains unchanged and fails closed when it is a composite.
No trailing parentheses or artist prefix is stripped there; no substring match
or guessed format equivalence is used. A passing checkout diagnostic therefore
will not by itself establish that the full server preparation path qualifies.
Actual retained cart structure is still needed to implement that comparison.

No live calls, policy-slot reuse, card initialization or payment were performed
for this title repair. Its next live verification requires a separately reviewed
bounded read-only job. The ordinary-reader stop and all historical audit/receipt
records must remain intact.


The semantic title code revision `454cce09562554b267ab529dfb2c1356f62aed48`
passed exact CI and independent review. All 52 targeted tests and required full
project gates passed. Image `sniper-browser-view:title-454cce0` started at
`2026-10-26T20:04:16.027233155Z`; deployed module SHA256 matched
`ae25bb91b22b039fb9c9d2ee54beebfe11ea000359bc9ac7ff0f78900c66fbf0`.
The original stop, successful outcome/disposition hashes, both old policies and
all four spent qualification receipts were preserved. No OOM was observed.

A separately reviewed exact-KC read-only job was staged before startup, expires
`2026-10-26T20:34:12.978Z`, and has both slots unconsumed. This staging avoids a
second installation restart for an issuer that pins its job at startup. It grants
no preparation or transaction action. No site request or qualification read was
made after this deployment; actual title qualification remains pending the next
bounded check. Cart title qualification still requires its own grounded evidence.


### Live semantic-title verification

The separately reviewed two-call job on `title-454cce0` completed both phases.
A single normal canonical KC load rendered the exact listing through its normal
same-item `/shop/item/1366950910` alias without a challenge. Key-by-key normal URL
entry was visually verified before Return, avoiding the earlier truncated input.
The item remained in cart. The existing cart still displayed one copy, seller
Mailman21842, Poor/Generic and selected $4.95 Economy/Media Mail shipping; no cart
contents, address or shipping option were changed.

Both actual service observations returned `title_continuity:true` with supported
exact listing/release details and matching conditions. After fresh visual saved-
address acknowledgement, the second result was `approved_saved_destination_bound`
with `missing_fields:[]`. Summary, seller, $0.10 item + $4.95 shipping = $5.05 USD
aggregate, quantity, title, conditions, item-price continuity, provider-frame and
unique enabled card-control checks were all true. The public grade diagnostics
were P/Generic; detail diagnostic was observed/complete, error null, main count1,
release-link count2 and grade count1. No guest form was present. This verifies the
deployed semantic title repair in the hosted service, not just a fixture.

The cart's visually observed label remained the artist/title/format composite
ending `(7", Single)`. No cart-node DOM capture or cart semantic extraction was
performed by these two checkout observations. Its full-label gate therefore
remains unqualified; a separately scoped read-only cart-node observation is the
next evidence needed for the full preflight. The configuration remains read-only
and grants no preparation action, card entry, payment or final submission.

Both slots are consumed, with all six receipts across the three jobs retained.
The historical access stop and successful outcome/disposition hashes are unchanged;
image `title-454cce0` remains healthy without an OOM or additional restart. No
initializer was invoked, and merchant order/payment status and final tax/payment
quote remain unverified. No extra site read or new job was created after this run.
