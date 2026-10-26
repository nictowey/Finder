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
