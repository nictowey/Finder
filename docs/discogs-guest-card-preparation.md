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

### Separate bounded cart structure observation

The next evidence step is an independently scoped, one-call cart structure job.
It does not reuse a qualification inspect/confirmation slot. Its pinned policy
identity and exclusive durable receipt retain the prior six spent receipts and
the historical access stop. It authorizes only the already-open exact KC cart
row, with no navigation, refresh, cart change or provider initialization.

The output contract admits only the visible public item-link subtree and narrow
Media/Sleeve grade nodes, with fixed tags, canonical item/release link paths and
bounded node/text counts. It excludes raw HTML, arbitrary attributes, row-wide
text, address/account/payment data, hidden values and tokens. Unknown or ambiguous
structure fails closed. Captured display structure is evidence for a parser
repair; it is not purchase qualification or permission to initialize payment.

Implementation tests and any actual capture/deployment results are recorded
separately below; this description alone does not establish a live observation.

The integrated capture, issuer and socket suites passed 68 focused tests. They
cover one-use/racing/restarted jobs, cross-mode rejection, fixed item identity,
hidden/ambiguous/oversized title nodes, document changes and cancellation. Tests
also make address text and input-value accessors throw, proving this collector
does not read those fields. Independent review accepted the combined scope.
These are offline results; the cart semantic title parser is still unchanged.

The capture code `1e05b67f719fd9ca2c304a80286688caf91eb118` passed all
required local gates (3,885 Python tests plus 41 subtests, 110 app tests, lint,
format and type checking) and exact CI. The Discogs, feedback/PostgreSQL and
sandbox checks passed; the production job was intentionally skipped. Image
`sniper-browser-view:cart-capture-1e05b67` started at
`2026-10-26T20:32:10.299527385Z`. All three deployed module hashes matched the
tested source. The historical stop, six spent qualification receipts and all
transaction audit/fence records matched their pre-installation hashes.

A single normal cart load rendered the one KC copy and $5.05 USD aggregate
without a challenge. The separately pinned job
`37f72f79-8951-405b-814c-acef94191ddf` then returned
`cart_structure_captured`: one row and the exact bare item link, whose only child
was a text node containing
`K.C. & The Sunshine Band* - Get Down Tonight (7", Single)`.
There are no separate artist/title/format spans to select in that cart link.
The narrow grade collector returned `grade_nodes:[]`; this means unsupported
structure, not absent or accepted grades. This is actual hosted DOM evidence,
not a screenshot-inferred selector or a synthetic fixture.

The new capture receipt is spent. The six earlier receipts and transaction
history still match their prior hashes; the browser remained healthy without an
OOM at roughly 510 MiB / 2 GiB. No provider initializer, card entry, payment or
order submission was invoked. Cart title/grade qualification still needs a
grounded semantic comparison before the full preparation flow can qualify.

### Cart-to-checkout qualification split

The captured cart link is parsed against the full, freshly observed exact-release
title as a literal anchor. One unique artist/core/format decomposition is required;
case, punctuation and parentheses within the release title remain significant.
The initial supported suffix is precisely the captured `7", Single` grammar,
not a generic format extractor. The full cart caption digest must remain stable
across rechecks. Checkout's source-defined artist/title/format spans must then
match the core and artist exactly, with the cart format tokens appearing in order
within the supported checkout format tokens. Unknown or ambiguous shapes stop.

The cart grade DOM remains unsupported. Its grades stay unknown, and never count
as accepted condition evidence. The reviewed split permits only normal,
noncommitting Go to Checkout after exact listing/detail seller and release,
quantity, amount, saved destination, normal access and ledger checks. Fresh
checkout conditions must match the independently verified detail conditions
before any initialization claim or card control could be reached.

A separate bounded navigation-preflight policy is being implemented for the
server test: inspect, same-document saved-destination confirmation, then one
verified Go to Checkout. It retains private address binding inside the browser
process and spends an immutable navigation receipt before that effect. It does
not accept fabricated recovery history or transaction grants and cannot call the
card initializer, claim writer, payment or order controls. An explicit ledger-head
binding preserves historical disposition evidence without treating navigation
permission as initialization permission. Live results remain a separate gate.

The integrated implementation passed 85 focused module/policy/socket tests,
including a replay of the retained public caption and negative identity, title,
format, condition, amount, address, shipping-selection, ledger and cancellation
cases. Required full checks passed: 3,885 Python tests plus 41 subtests, 110 app
tests, lint, formatting and type checking. Independent combined review accepted
the change. This is still offline verification until the bounded server run.

The navigation scope is a new three-use pinned job, not reuse of a spent read.
It binds the exact immutable ledger head, current Cart document and confirmed
private destination. The navigation receipt is persisted before dispatch.
The separate `cart_checkout_preflight` action cannot call the initializer and
rejects unexpected effect reports rather than hiding them. Unknown cart grades
remain explicit in its result. The observed selected method's bare label or exact
`Economy - Media Mail - $4.95` rendering is accepted only alongside independently
verified USD amounts and one unchanged selected option; arbitrary suffixes or
inferred currency are not accepted.

### Actual cart inspection and batched layout diagnosis

The server inspection on the new preflight path verified exact item/release
details, seller, quantity and the repaired literal cart-title comparison. It did
not verify the cart summary, displayed amounts or private destination: summary
and amount flags were false, and the supplemental address-container count was
zero. The run stopped before destination confirmation or Go to Checkout. Cart
condition/provider flags being unknown were expected at that preparatory stage;
they do not explain the independent money/address failures.

The browser control connection was restored by binding the current instance of
the same Chrome extension and retrieving the existing viewer tab. No alternate
browser/profile was used. The original expired preflight job remained unused;
the reviewed replacement spent only its inspection slot. Both normal merchant
pages rendered without a challenge. No initialization, payment or order action
occurred, and all historical preservation hashes matched.

The next capture extends the existing one-call, exact-item structural operation.
It reports the legacy form/container gate counts, row ancestry, form associations
of Go and shipping controls, bounded table/cell structure, fixed public labels or
whole explicit USD values, and address node types/children. Raw collector calls,
address or wrapper text, field values, HTML, arbitrary attributes and identifiers
are excluded. Only fixed CSS hints leave the process. Known summary tables are
considered even after HTML form reparenting; the observed public heading “Your
Shipping Address” and its plain nested-label variants are included. Private
regions are excluded before reading any public summary text.

This is a diagnostic capture, not a selector repair or eligibility change. It
uses a separate fresh max-one job; the spent preflight inspect slot cannot be
reused. Its purpose is to ground one batch repair of form, summary, address and
control-association mismatches instead of guessing one field per iteration.

The capture-only extension passed 92 focused module/policy/socket tests and all
required full checks (3,885 Python tests plus 41 subtests, 110 app tests, lint,
format and type checking). Negative cases cover private getters, mixed/hidden
or non-USD cells, unknown classes/attributes, malformed schemas and bounds.
Independent review accepted the final scope. These results verify the diagnostic
code offline; they do not yet establish the actual layout or repair its selectors.


### Captured nonlegacy cart repair (October 26)

The bounded actual capture at revision `1ea2be8` established both root causes:
there were no `form.legacy` or `address` elements, although one ordinary form
owned the exact item row, Go to Checkout button and selected shipping control.
One visible nested-DIV shipping-address wrapper was inside the merchant container.
The existing subtotal/shipping/total selectors already matched the displayed
explicit USD amounts. The redacted structural capture is retained in
`tests/fixtures/discogs_cart_layout_1ea2be8.json`; private lines and deeper line
markup were not captured. Address values and deeper markup in tests are synthetic.

The batch repair derives the owner form from the unique container and checks the
row, shipping select and Go button associations together. Split, duplicate,
detached or changed controls stop. The read-only public collector classifies
legacy commitment versus V2 navigation and leaves V2 address state unknown.
The separate hosted collector privately normalizes only bounded visible address
lines, omitting the observed heading and plain “Change your address” link without
reading its href. Unknown controls/links, ambiguous wrappers and changes during
hashing stop. Only a private matching destination binding can authorize the
noncommitting preflight navigation; an unknown public address flag cannot.

All item/release/seller/count, explicit money, shipping, ledger and document
continuity checks remain required. Cart grades stay unknown until independently
verified on the fresh checkout. Displayed aggregate amounts do not prove final
tax or payment readiness. No card initialization or payment is part of this
repair. A new bounded server preflight is still required to verify the actual
private line layout and end-to-end navigation; offline fixtures do not prove it.

The integrated batch passed 263 focused cart/checkout tests, 3,885 Python tests
plus 41 subtests, 110 app tests, lint, format and type checks. Independent review
accepted the repair and the bounded three-phase preflight plan. Four older
synthetic legacy snapshots now explicitly declare their control kind; their
quote/claim expectations were not relaxed. These checks establish offline
compatibility, not live navigation or purchase readiness.


### Predicate diagnosis after a16c29a live inspection

The CI-passed `a16c29a` deployment matched both module hashes. Its fresh exact-item
inspection still passed seller, release/detail, quantity and title continuity,
but failed summary/money support and reported zero supplemental address containers.
No destination acknowledgement or Go action followed. The historical ledger and
all 41 preservation hashes remained unchanged; the distinct preflight job spent
only its inspect slot. This was an application validation failure, not an
observed challenge or access denial.

The retained structural capture cannot identify the exact rejecting predicate.
In particular, address discovery is deliberately suppressed when the shared cart
binding is invalid: zero reported containers need not mean a missing wrapper.
An offline two-variant reproduction matched every captured gate fact while
changing only the uncaptured Go-button value. The exact-value variant completed
the collectors; the absent-value variant reproduced the failed money/summary and
zero supplemental address count. Both variants are synthetic; this does not
establish that the real button value is absent.

The diagnostic-only patch preserves the existing qualification checks. It adds
fixed first-failed gate enums, capped structural counts and explicit booleans
for each Go/form/row/shipping association and visibility predicate. The visible
address-wrapper count is reported independently of whether discovery was
attempted. The socket admits only the exact diagnostic schemas and rejects
unknown fields, enums and out-of-range counts. No raw button value, form action,
address line, destination digest, session field or arbitrary DOM text is exported.
A separately reviewed bounded read is needed to identify the live predicate
before changing eligibility logic; no consumed job may be reused for that read.

Diagnostic-only validation passed 507 exact-CI-list Node tests, 3,885 Python
tests plus 41 subtests, 110 app tests, lint, format and type checking. Independent
review accepted the output bounds and unchanged eligibility predicates. A new
ordinary qualification policy can leave its acknowledgement null and use only
its inspect phase; its required max_calls2 schema must not be relabelled as a
max-one policy, and confirmation stays denied without a later explicit update.
The spent preflight job and its receipts remain separate and unchanged.


### Live-confirmed Go-value assumption (October 26)

The separately authorized inspect-only job on CI-passed `8447cc2` confirmed
`first_failed: go_value_matches`. Every other captured Go/form/row/shipping
predicate passed, including exact visible label, submit type, enabled state,
visibility and form ownership. The raw collector stopped at
`control_binding_mismatch`; its counts showed one Go control, select, container,
row and summary, with no legacy control. One visible address wrapper was present,
but discovery was suppressed by that early binding failure. No destination
acknowledgement, Go navigation, card initialization or payment occurred.

The actual button value was deliberately not exported: it is only established
that it differs from the visible label, not that it is empty. The fixed, redacted
predicates are retained in `tests/fixtures/discogs_cart_binding_8447cc2.json`.
The minimal correction removes value-to-label equality as an eligibility
assumption. Unique known action control, exact visible label, enabled/visible
submit type and current form ownership remain required; the final Go recheck
uses those same semantic controls. `go_value_matches` remains informational in
diagnostics and cannot be a required first-failed gate. Exact item, seller,
quantity, amount, destination, document and ledger checks remain independent.

The diagnostic opened only the cart; its missing detail evidence is expected,
not a verified detail-reader regression. A fresh bounded preflight must open
the exact detail page and cart, establish their full continuity and amounts,
privately bind the approved saved destination, and only then dispatch one normal
server Go action. The current correction is not evidence that this live
transition or any payment is working.

The live-evidenced correction passed 510 tests in the exact CI Node list, 3,885
Python tests plus 41 subtests, 110 app tests, lint, format and type checks.
Independent review accepted the change and the fresh bounded preflight plan.
Synthetic alternate submit values exercise one normal Go transition with no
card initialization or claim, while wrong labels, types, forms, hidden/disabled
controls and late mutations stop before Go. These are offline results; the
corrected server transition still requires live verification.

### First actual server Cart → Checkout transition (October 26, late UTC)

On deployed `f02749d`, reviewed job `2e8d8100-285c-487d-9cc2-503ae4e5472f`
used all three phases. Fresh cart/detail inspection verified the exact KC item,
seller, title, price and quantity; same-document visual acknowledgement bound the
approved saved destination. The server then dispatched exactly one normal Go
control and reached `/marketplace/checkout`. No transaction claim, card
initialization, card entry, payment or order submission followed. The result was
`visible_scope_evidence_missing` for quantity and destination, with
`checkout_navigation_issued:true`. The spent job, 50 preservation hashes and
11-record ledger head remain unchanged. This is actual server navigation proof,
not a successful final preflight or buyer.

Two distinct issues were investigated without reusing a spent IPC phase. Normal
Firefox Inspector UI on the already-rendered public Items heading confirmed an H2
with the known summary-header class prefix and two direct text nodes, `Items `
and `(1)`. No console evaluation, new page request or payment action was used.
The retained renderer emits the heading without a count and no address until
checkout session data exists. The preflight had treated the document load event
as data readiness. A hydration race fits the transient missing fields, but the
original instant was not captured well enough to prove this timing diagnosis.

A separate destination mismatch is confirmed: the cart uses terminal country
label `United States`; checkout's retained `SRu` renderer uses country code `US`.
Previous successful checkout-local visual binding never tested that cross-page
comparison. The repair canonicalizes only that exact terminal-country
equivalence in the private digest. All name/street/city/postal lines and the raw
same-read mutation checks remain unchanged; no address or digest is exported.

After a fresh navigation, the preflight now waits only for the source-derived
checkout DOM data to settle, within its original absolute deadline. It does not
reload, navigate again or wait for a wrong populated quantity/address to become
acceptable. Full strict validation and document identity checks still follow.
Cancellation, expiry or a replaced document cannot resume the flow or initialize
payment. Blocked results retain only fixed predicate booleans and capped counts,
so an absent address can be distinguished from an actual destination mismatch
without revealing its content.

Regression fixtures distinguish the actual public Inspector/source facts from
synthetic hydration timing. They exercise delayed data, exact country
equivalence, wrong quantity, changed address/country, document replacement,
cancellation and expiry. A test pass does not establish a new live checkout
qualification. The current spent job cannot be replayed; another collector
observation needs separately reviewed bounded read-only scope. Installing new
browser code would require a service restart, so the current checkout is kept
intact while that next step is reviewed. No further full navigation test or
payment is authorized by this repair itself.

The repaired batch passed 101 focused preparation/IPC tests, the exact 518-test
CI Node list, 3,885 Python tests plus 41 subtests, 110 app tests, lint/format and
type checking. Independent review accepted it. A synthetic-address replay of the
actual country labels fails equivalence on `f02749d` and passes on the repair;
`CA` remains a mismatch on both. The original live quantity failure is still
classified as a timing inference, not retrospectively proven by these tests.

### Cart document readiness (October 26, late UTC)

After `b069958` passed CI and loaded-checkout qualification returned no missing
fields, the next reviewed server transition test stopped before Go with
`page_not_ready`. Quantity and destination matched and the address structure was
present. The exact failing predicate requires `document.readyState ===
'complete'`; neither the actual earlier state token nor the delaying resource was
exported. A visible full page is not proof its load event has completed. The
spent three-phase job and 62-file preservation baseline remain unchanged.

The repair does not accept `interactive` or fabricate completion. Both read-only
qualification and the preflight wait for the same fixed document's complete
state, within their existing 1.5-second and 30-second absolute budgets. Redirect
or restriction detection exits the wait into the existing strict stop checks.
After waiting, document identity and all cart fields are read again; the final
bound Go control also requires a complete document. Qualification cannot return
an opaque destination reference while its current or final DOM is unready, and
successful observations explicitly report `page_ready:true`.

The redacted failure facts are retained in
`tests/fixtures/discogs_cart_readiness_b069958.json`. Delayed `interactive` to
`complete` regression timing is synthetic; wrong price, quantity, destination,
changed URL/document, cancellation, timeout and expiry remain stops. A real
post-navigation pass is still required. No readiness fix authorizes replay of a
spent job, another initialization or payment.

The minimal readiness batch passed 91 preparation tests, 17 IPC tests, the exact
525-test CI Node list, 3,885 Python tests plus 41 subtests, 110 app tests,
lint/format and type checking. Independent review accepted the unchanged
readiness threshold, absolute deadlines and same-document/effect guards. These
are offline results; a fresh bounded live transition remains a separate gate.

### Verified server transition and dormant preparation issuer (October 27)

Deployed `1ce0598` passed a fresh reviewed three-phase live preflight. The actual
server followed one normal Go to Checkout and returned
`cart_checkout_preflight_observed`, with normal transition, fresh checkout
conditions and approved destination all verified. The selected merchant
aggregate remained $5.05 USD; the client round trip was 5.404 seconds. No card
initialization, transaction claim, payment or order submission occurred. The job
is spent. The 70-file preservation baseline, 11-record ledger and original access
stop were unchanged; monitoring remained inactive. This is real server
preflight evidence, not final payment-total or purchase verification.

The existing production issuer now implements a distinct, default-off
`discogs_guest_prepare_policy_v1`. It permits exactly one inspect, one
same-document destination confirmation and one `prepare_guest_form` operation.
Ordinary qualification, capture and preflight policies cannot grant preparation.
The fixed request contains only schema, job, phase and the opaque document
reference. It cannot choose a URL, selector, directory, scope, address hash,
quote fingerprint, callback or payment permission.

The owner-only policy pins the exact KC scope, immutable attempt/reference IDs,
ledger head, genuine prior SESSION_EXPIRED history and disposition reference,
plus fresh complete All/All zero-order reconciliation. A historical user
no-submit authorization can precede that new reconciliation; its timestamp is
not rewritten as a new approval. Reconciliation, policy and scope remain bounded
by five minutes, while visual acknowledgement remains bounded by 60 seconds.
The issuer exclusively creates and fsyncs its prepare receipt before returning
any grant. It cannot rearm a spent phase across races or process restart.

The preparation operation resolves its destination only from the actual
in-process confirmed cart document. Its quote fingerprint derives from that
process's validated item/shipping/total, selected label, caption and exact detail
identity/grades. Changed private snapshot/document, quote, title/grades or
shipping selection stops the operation. The separate preparation-terms hash
represents no visible extra agreements and an explicit merchant aggregate; it is
not provider terms acceptance or a verified final payable quote.

Preparation reuses the verified same-document readiness and fresh-navigation
response checks. Trusted policy revocation is checked after startup, before Go,
and in the final card callback. Current cart facts are re-read after issuer I/O;
current checkout/frame facts are re-read inside that last callback. Cancellation,
original observation age, permit expiry and reconciliation expiry are checked
before setting the dispatch flag or calling the control. An expired permit is
never converted to a positive one-millisecond timeout, and an old observation
is never made fresh by copying a new timestamp onto it.

After a card-control initialization, the server still records a durable
`unknown` outcome and returns `guest_form_observation_required`. No new guest
field anatomy has been verified by this implementation. A separate normal
observation must establish blank fields and the displayed Pay amount; no card
entry, Pay, approval, capture, order submission or automatic retry is exposed.
The issuer/socket/engine regression test uses real private IPC and authoritative
isolated claim/disposition records with intercepted browser controls. Every
simulated effect and the synthetic ledger seed remain separate from live proof.
No live initialization was performed to implement or test this mode.

The integrated preparation batch passed 132 focused issuer/engine/IPC tests, the
exact 536-test CI Node list, 3,885 Python tests plus 41 subtests, 110 app tests,
lint/format and type checks. Independent review accepted the final pre-Go
revocation fix and last-callback expiry/cancellation guards. These results do
not authorize a live job or establish provider form/payment success. The code
remains default off pending a separately reviewed fresh reconciliation/binding
policy for the one exact no-submit initialization.

### October 27: first scoped preparation and stale document handle

The owner completed the displayed Discogs challenge. A fresh normal Purchases
view with Archived All and Status All showed zero total orders. Job
`790d8e7e-32df-42a5-b685-5c9a61710659` then consumed inspect, same-document saved
destination confirmation and its sole prepare phase. The actual server navigated
the exact KC cart to checkout, but returned `unknown_internal_stop` after 4.04
seconds, with `card_initialization_issued:false` and `initialization:not_dispatched`.
The 78-file baseline and 11-record ledger head were unchanged. The job remains
spent; no new live initialization or payment was attempted to diagnose it.

The preparation path attempted to compare checkout with a document ElementHandle
from the cart after full navigation. An isolated Firefox container using the same
installed browser/Playwright build, no network and only two synthetic `data:`
documents reproduced `Page.adoptNode: Cannot find object with id`. A handle
created in the new document compared successfully. The previous jsdom mock passed
raw Document objects across windows and therefore hid this production API error.
The original live exception stack was not retained, so this is a reproduced
failure of that exact code branch rather than a recovered live stack trace.

The fix retains the required full navigation response and exact checkout URL,
then binds subsequent checks to the fresh checkout document. It never submits
the destroyed cart handle to checkout. Same-document URL changes still fail the
required navigation response, and later checkout document replacement remains
blocked. A strict document-handle fixture reproduces the old live-shaped stop
before the fix and reaches the intercepted no-submit boundary after it. This is
offline regression evidence; corrected live form initialization is not yet proven.
