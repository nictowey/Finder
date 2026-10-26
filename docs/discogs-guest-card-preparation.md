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
| One-use card initialization | One operator-assisted normal card-button click after durable fencing | It returned a visible PayPal-session-expired message. No card form was observed, no card data entered and no capture/approval control operated. The consumed fence was retained, and initialization was not retried. |
| Initial reconciliation | Normal `/sell/purchases`, all statuses, Not Archived, after the attempt | It displayed zero orders and cart count one; this first view did not cover archived orders. |
| Full reconciliation | Normal Purchases filters `Archived: All`, `Status: All`; `/sell/purchases?archived=A&status=All&limit=50` | The fresh view displayed `0–0 of 0`, `No orders found` and cart count one. This includes active and archived orders, but does not prove that no provider draft exists. |

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

At this documentation milestone, the recovery helper has passed 29 focused
tests and independent review; no second initialization has executed. The original consumed fence must remain
byte-for-byte unchanged in the same record directory. The recovery requires a
separate immutable reconciliation record bound to that original fence and the
new exact scope, followed by one fixed second-attempt fence before the possible
click. It must not delete/reset the first record, substitute another directory
or permit further retry chaining. Any new unknown outcome stops again.

## Amount and runtime evidence

The summary computes its aggregate from item prices, shipping and taxes. Its
tax row appears only for a positive amount, while missing data also falls back
to zero in rendering. An absent tax row must therefore remain an unknown
component; it must not be recorded as confirmed zero tax. Preparation may use
the explicit merchant aggregate, but provider amount and terms still require
verification before any final payment.

For the operator-assisted test, the default deadline remains 30 seconds and the
maximum is 90 seconds, bounded further by the exact approval/evidence expiry.
The private operator transport has no browser, network or payment access. A
fresh operator revalidation precedes its sole CUA click, and the durable record
is retained on timeout or uncertainty. This is test evidence, not an enabled
production checkout adapter. Final provider form fields, agreements, payment,
receipt normalization and order-versus-paid reconciliation remain unverified.

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
There is only one fixed recovery slot; its consumption permits no further chain.
