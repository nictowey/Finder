# One-use guest-card preparation

The dormant exact-item preparation helper accepts the merchant's explicit
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

There is no public endpoint, persistent directory, live browser port or automatic
payment method configured by this module. An operator-assisted test using the
normal private viewer is separate from a production server browser adapter.
Do not describe injected offline tests or a displayed form as an autonomous
buyer or a completed order.

## Observed hosted flow, October 26, 2026

| Stage | Observed normal control or evidence | Meaning and stop condition |
| --- | --- | --- |
| Existing cart | One selected item and seller; selected shipping; explicit USD subtotal, shipping and Total | Read the existing item; do not repeat Add to Cart. A changed item, destination, total or deferred notice stops. |
| Review | Visible `Go to Checkout` opens `/marketplace/checkout` | This route was exercised without entering payment details. Recheck the one-item summary, saved destination and aggregate Total. |
| Payment choice | Visible `PayPal` and `Debit or Credit Card`, powered by PayPal | The card control was previously observed as a link with `data-funding-source="card"`, `aria-label="Debit or Credit Card"` and `aria-disabled="false"` in the provider buttons frame. These are observed selectors, not proof of an order or a reusable saved card. |
| One-use card initialization | One operator-assisted normal card-button click after durable fencing | It returned a visible PayPal-session-expired message. No card form was observed, no card data entered and no capture/approval control operated. The consumed fence was retained, and initialization was not retried. |
| Reconciliation | Normal `/sell/purchases`, all statuses, Not Archived, after the attempt | It displayed zero orders and cart count one. This observation alone does not cover archived orders or prove provider-draft state. |

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
