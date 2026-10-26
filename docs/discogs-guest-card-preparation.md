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
