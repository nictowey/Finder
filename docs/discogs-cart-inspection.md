# Read-only on-site cart inspection

`scripts/discogs_cart_inspector.mjs` is a bounded helper for an existing, separately
authorized browser page. It reads the current cart's visible body text and reports blockers.
It does **not** implement checkout, add an item, navigate, fill an address, select payment,
accept agreements, place an order, or pay. It has no submit functionality or enable-buy flag.

The owner requested an on-site program but explicitly prohibited placing orders. A supplied
cart screenshot established these visible warnings: missing shipping information, no shipping
address, and both Shipping and Total shown as “To be determined by seller.” A displayed
subtotal is therefore not a final delivered price. The final “Place order and pay now” control
is a stop boundary, not something this script invokes. No private screenshot is committed.

## Browser contract and verification limits

Import `inspectCurrentCart` from the module and pass an already-authorized Playwright-like
page plus an explicit expected listing ID. The page must expose `url()` and
`locator('body').innerText({ timeout })`. The module does not launch or connect to a browser,
read/export cookies, configure credentials, or grant persistent access. `body` is a standard
read-only text locator, not a verified Discogs checkout form selector. Exact current cart URLs
are checked before reading; unknown URLs, missing expected shape, failures and timeouts stop.

```js
import { inspectCurrentCart } from './scripts/discogs_cart_inspector.mjs';
// page must already be authorized and on the cart; this helper never opens it.
const result = await inspectCurrentCart(page, { expectedListingId: '123456' });
```

The expected listing ID is only an operator input. Visible cart text cannot prove it matches
that listing or release. The helper must not treat an album title as an exact pressing ID.
Seller reputation, media/sleeve conditions, destination, shipping, taxes, fees, currency and
final amount still require reliable structured page evidence and fresh validation against
saved rules before any future preparation step. Even apparently complete text never produces
purchase permission. Text warnings can establish reasons to stop, not reasons to buy.

An actual authenticated cart DOM/browser connection was not available for testing. Tests use
synthetic visible-text/page fixtures and verify read-only behavior, redacted output and failure
paths. They do not prove compatibility with a live cart, a cloud browser session, payment flow,
or authenticated checkout. No blocked protected browser action was retried through another
route. A screenshot establishes visible language, not hidden DOM or usable selectors.

## What remains

Direct release-ID/link marketplace monitoring still needs a verified inventory source.
This helper does not discover listings and is not connected to an inbox. Real authorized
cloud-browser inspection is required to verify cart identity, accessible controls and quote
fields before extending the program. Additional authentication/session or deployment setup
requires its own approval. No order, payment, real session configuration, deployment or cost
was incurred by this change.
