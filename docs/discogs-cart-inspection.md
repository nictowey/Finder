# Read-only on-site cart inspection

`scripts/discogs_cart_inspector.mjs` contains bounded helpers for an existing, separately
authorized browser page. The inspection helpers report blockers and **never** add an item, navigate, fill an
address, select payment, accept agreements, place an order or pay. There is no submit
functionality or enable-buy flag.

## Evidence and boundaries

The owner requested an on-site program and explicitly prohibited orders. Initial screenshot
inspection showed missing shipping information/address and both Shipping and Total displayed
as “To be determined by seller.” Subsequent owner-authorized local browser inspection verified
the cart DOM selectors below. This did not recover or bypass the separately blocked cloud
browser session. No account identifiers, private screenshots or real cart contents are
committed; automated regression fixtures are synthetic.

The final button was DOM-enabled despite missing address and unknown final amounts. Enabled
controls are **not** proof of readiness or permission. A listing page's shipping quote cannot
replace the cart's final destination-specific quote. Subtotal is not final delivered cost;
an invisible tax row is unknown, never zero.

## Verified read-only DOM contract

Import `inspectCurrentCartDom` and pass an already-authorized Playwright-like page plus an
explicit expected listing ID. It uses only `page.url()` and `page.evaluate(collectCartDom, id)`. URL reads may return a
string or a Promise; both initial and post-read values are awaited within a bounded deadline.
The collector is self-contained and serializable for a supported read-only DOM evaluation
interface. It does not launch/connect browsers, read/export cookies, configure credentials,
make requests, or grant persistent access.

```js
import { inspectCurrentCartDom } from './scripts/discogs_cart_inspector.mjs';
// page must already be authorized and on the cart. No navigation occurs here.
const result = await inspectCurrentCartDom(page, { expectedListingId: '123456' });
```

Observed generic selectors, without hardcoded seller identifiers:

- Seller scope: `form.legacy [id^='cart-order-'].order_container`.
- Item rows: `tr.order_row`; listing link: `a.item_link[href^='/sell/item/']`.
- Address warning: `[id^='shipping_address_']` and `a.edit_shipping_address`.
- Summary: `.order_summary`, first cell labels and `td.order_summary_value`;
  subtotal `tr.order_subtotal`, shipping `td.order_shipping_amount`, total `tr.order_total`.
- Shipping method: `.order_shipping_fields select.shipping_method[name='shipping_method']`,
  reading `selectedIndex` and each option's current `selected` property, never its value.
- Agreement: `input.agree_terms[name='agree_terms']`.
- Final control: `button.order_button.checkout_v1`, never clicked.

Exact current cart URLs accept both `/sell/cart` and `/sell/cart/` forms. Strict URL and
option validation precede reading; post-read URL checks reject navigation. Ambiguous/missing
scopes, unexpected rows/links, hidden amounts, unknown shape and read failures stop. Visibility
is checked rather than treating hidden DOM tax information as a displayed quote.

The collector returns only bounded structural observations, not full body text, address,
seller name or payment information. The wrapper returns sanitized diagnostic codes, bounded visible money observations when
unambiguous, and permanent `purchase_allowed: false`. Observed amounts are not verified
destination-specific all-in costs. A matching item link establishes only the observed selected listing
reference. It does not prove a release ID, seller reputation, conditions, quantity, destination
or final all-in cost. No quantity input was observed in the inspected one-row cart; this does
not establish a universal quantity model. Those checks remain unresolved.

`inspectCurrentCart` and `inspectCartText` retain the earlier screenshot-oriented text-only
fallback. Its exact-line matching is conservative and may reject normal DOM grouping. Use the
DOM collector for verified structural observations; neither helper can authorize checkout.

## Verification status

Selectors were observed through an explicitly authorized local browser inspection. Tests
recreate the reported structure with synthetic DOM/page fixtures and verify read-only behavior,
visibility checks, timeouts, changed URLs, ambiguous cart contents and sanitized output.
Synthetic fixtures are not evidence of completed checkout or a working cloud session.
The first exact collector execution stopped with `dom_read_failed`: the supported local
DOM tool lacked `createTreeWalker`, and responsive address sections required visible-node
selection. Those assumptions were corrected rather than treating the failed run as a pass.
The corrected exact collector was subsequently executed successfully through the authorized
local Chrome DOM-read tool. It observed one matching cart row, a visible missing-address
warning, shipping/total still TBD, hidden tax with no value returned, and an enabled final
control that did not grant permission. This verifies that exact collector in that local
browser/tool context only, not a cloud runtime or completed checkout. The revised orchestration
also passed its already-in-cart path through the authorized local two-method read-only port
(async URL and DOM evaluation). That test did not add anything; the scripted-add path remains
synthetic-fixture-tested. The child did not independently verify the forwarded bundle hash.

## What remains

A verified discovery source and deployed cloud runtime are separate requirements. The helper
does not discover listings, alter saved rules, create ownership markers or operate an inbox.
Reliable release/condition/seller validation and a complete fresh destination-specific quote
are required before any future preparation step. Auth/session/deployment setup needs separate
approval. No order, payment, real session export, deployment or cost is part of this change.

## Required production acceptance gate

Actual operation must continue on its approved cloud host with the owner's computer switched
off. The user-authorized local browser is for inspection/testing only, not a production
runtime, Chrome-extension dependency, local CDP endpoint or tunnel. Portable page helpers do
not themselves provide hosting or a cloud browser session. Before describing this as running
autonomously, separately verify the approved cloud browser provider, supported authentication
and session lifetime, durable rule state, scheduler availability, resource/cost limits, and
a Mac-off end-to-end test. None of those deployment gates is satisfied by local UI tests.


## Optional, explicitly authorized one-click cart preparation

`scripts/discogs_cart_prepare.mjs` is separate from the read-only collector. It accepts an
already-authorized page currently on the explicitly selected listing or cart. It does not
launch a browser, create a session, navigate to a guessed URL, or send direct cart HTTP
requests. The ordinary visible Add to Cart link and its resulting item row were inspected
and exercised by a separately authorized local-browser task, without placing an order.

Preparation is read-only unless the caller explicitly passes `allowAddToCart: true` for
that selected listing. This flag is not user authentication or approval discovery: the
caller must already have authorization for the specific cart addition. Any future cloud
endpoint must retain existing owner authentication, Origin/CSRF and stop controls; no such
endpoint is introduced here. A disabled In Cart control or matching existing cart row must
not cause another add. Responsive hidden copies are not clickable candidates.

When explicitly allowed, the helper can click the one verified visible Add to Cart link
once, navigate to the canonical cart without mutation parameters when the verified add
query remains in the URL, and then reconcile the expected cart row. The mutation-bearing
URL is never replayed. It never removes items automatically,
checks agreements, changes addresses/payment, or invokes the final order control. A timeout
after a click is an uncertain outcome, not a reason to retry. In-process duplicate guards
are not durable multi-instance purchase locks; new processes require fresh reconciliation
rather than assuming a previous attempt failed. No invocation returns purchase permission.

The add link's raw href must be exactly `/sell/cart/?add=<expectedID>&ev=` or that exact
path/query prefixed by the current listing's literal HTTPS origin. No URL normalization is
used: foreign origins, ports, credentials, encoded paths, query changes and fragments stop.
The same raw href must remain unchanged at the pre-click recheck, even if switching between
the otherwise allowed relative and absolute forms.

Unknown authenticated shapes, a different listing, ambiguous links or cart contents, unknown
final costs and access errors remain stops. Normal UI automation is not permission to
retry a denied protected-browser route. Synthetic tests establish state-machine behavior;
they do not establish that the actual hosted browser runtime is configured or working.


Preparation entry point:

```js
import { prepareCurrentListingCart } from './scripts/discogs_cart_prepare.mjs';
// Read/reconcile an already-open cart without authorizing any addition.
const result = await prepareCurrentListingCart(page, { expectedListingId: '123456' });
// allowAddToCart: true is only for an explicitly authorized, selected listing.
```

The preparation budget remains 2,000 ms by default. An explicitly supplied `timeoutMs`
must be an integer from 5,000 through 30,000 inclusive. It bounds the complete preparation
operation, including initial/recheck URL reads, control reads, the one click and canonical
navigation. Increasing it never permits retries after an uncertain mutation. The independent
DOM inspector retains its 1,500 ms read cap; a longer preparation budget does not enlarge it.

### Explicit supported-local-browser port

`scripts/discogs_cart_browser_adapter.mjs` exports `createSupportedLocalCartPage(tab)` for
an already authorized supported local tab. It launches nothing and configures no session.
It forwards async `tab.url()` and `tab.playwright.evaluate(fn, arg)`, maps the two observed
role locators, converts a strictly boolean `isEnabled()` to `isDisabled()`, and also requires
strict boolean visibility. Unsupported return types stop preparation rather than using
truthiness. Click's bounded `{ timeout }` is translated to the supported `{ timeoutMs }`.

```js
import { createSupportedLocalCartPage } from './scripts/discogs_cart_browser_adapter.mjs';
import { prepareCurrentListingCart } from './scripts/discogs_cart_prepare.mjs';
// tab must already be authorized. Retain and reuse this same tab object.
const page = createSupportedLocalCartPage(tab);
const result = await prepareCurrentListingCart(page, {
  expectedListingId: '123456', timeoutMs: 15000,
});
// This example does not authorize adding an item.
```

The factory caches the same frozen page for the same tab object, so repeated wrapping does
not reset the in-process attempted-add guard. This does not establish durable identity across
different tab objects, reconnects, module instances or processes. After an uncertain action,
do not create a new wrapper/connection to evade reconciliation or retry an addition.

The local navigation API is only `tab.goto(url)`: no Playwright options or `waitUntil`
guarantee are forwarded. Only the helper's exact query-free canonical cart URL is eligible.
Preparation separately verifies the URL and cart DOM afterward; an unsettled or mismatched
result stays unconfirmed, with no repeated click or mutation-URL replay. The adapter was fixture-tested and then exercised through the authorized local browser
on October 24, 2026. It is a local verification port, not the required cloud-only production
runtime, authentication integration or permission to place orders.

A later authorized local test executed the complete supplied esbuild-minified artifact:
the existing-cart branch passed, the operator removed only the specifically approved test
item, and one preparation call clicked its visible add link and reconciled one matching cart
row at the canonical cart URL. The final quote remained unverified and purchase remained
false. No agreement, address, shipping selection, payment or order was changed. The child
reported using the complete artifact but did not independently verify its SHA. This validates
that local adapter flow, not production Playwright, cloud hosting or automatic checkout.


## After shipping information is entered by the owner

A fresh authorized local observation after the owner manually added shipping information
showed numeric subtotal, shipping and total instead of TBD. The collector observed those
values without reading or retaining the address. The absent missing-address warning does
not verify a destination, and visible numeric amounts do not establish final tax/fee treatment
or purchase eligibility. The interpreter distinguishes numeric observations from unknown
values and still stops with final-quote verification outstanding.

A cheaper local-pickup option must not be silently substituted for shipping. No delivery
option is selected or changed by these helpers. The shipping selector was subsequently inspected: the collector reads at most 20 bounded
labels and current selected/disabled properties. It does not rely on stale HTML selected
attributes or store dynamic option values. A displayed selection is not verified delivery
coverage, destination or final price, and never causes another option to be chosen.

The shipping-selection-aware collector passed a read-only live retest after the authorized
local computer reconnected on October 24, 2026. The current full interpreter and existing-cart
path also reported the observed shipping/total while keeping final verification unresolved.
These are local verification results, not cloud deployment or order completion. Quotes and
selection are historical snapshots, not guarantees of current availability or cost. The later supported-local-browser adapter and single-add verification is described above.

## Optional saved-release identity gate

`scripts/discogs_rule_cart_gate.mjs` exports
`prepareRuleQualifiedListingCart(page, authenticatedDataPort, options)`. Its scope is
**saved-release identity only**, not complete buying-rule qualification. Media/sleeve grade,
seller thresholds, currency and price ceilings are not enforced by this gate. A matching
Discogs reference does not independently verify the seller's physical copy. Unknown final
shipping, tax and fees remain unknown, never zero; `eligible_for_review` is not purchase
permission. Every result explicitly reports unsupported purchase eligibility and keeps
`purchase_allowed: false` and `stop_required: true`.

Require explicit positive decimal-string `expectedReleaseId` and `expectedListingId`, plus
the nonnegative integer `expectedRevision` of saved rules. The release must be uniquely
saved, enabled and unowned, with the kill switch off. Rules and control revisions must
remain unchanged. The API must report the exact listing/release, current availability and
matching `evaluation.rules_revision`. No title, master ID, catalog-number or barcode fallback
is permitted. Exact listing-page identity is required; existing-cart reconciliation remains
with the separate read-only inspector.

The self-contained `collectListingReleaseDom(expected)` requires one visible main, one
visible `main a.hifi-heading-link[href*='/release/']`, and one visible main anchor whose exact
rendered label is `Release Details`. Both release hrefs must be strict same-origin absolute
HTTPS `/release/<ID>` links. Unrelated release links are ignored. Canonical and `og:url`
metadata must each occur once and exactly equal the current HTTPS `/shop/item/<ID>` or
`/sell/item/<ID>` URL. Ports, credentials, query strings, fragments, encoded paths and
ambiguous/hidden identity elements fail closed. The local adapter permits this exact
additional read-only collector, not arbitrary evaluation.

The caller-supplied data port exposes `readSnapshot()` returning the existing
`{ revision, targets, owned, kill_switch, control_revision }` shape, and
`readListing({ release_id: number, listing: string })` returning `{ listing, evaluation }`.
Its name does not authenticate it: a real integration must supply genuinely owner-authorized
reads and preserve authentication, Origin/CSRF controls and provider budgets. No hosted
transport, connector, request client, credentials or session is wired here. Synthetic ports
are fixtures, not production evidence. The existing cloud listing reader's three-second
cooldown still applies; fresh reads that cannot be performed must fail closed. The helper
never sleeps/retries or invents cached freshness to bypass that limit.

```js
import { prepareRuleQualifiedListingCart } from './scripts/discogs_rule_cart_gate.mjs';
// Both arguments must already exist through authorized integrations.
const result = await prepareRuleQualifiedListingCart(page, authenticatedDataPort, {
  expectedReleaseId: '111', expectedListingId: '222', expectedRevision: 3,
  timeoutMs: 15000,
});
// Addition still requires explicit allowAddToCart: true and prior user authorization.
```

Checks run before preparation and again inside the wrapped Add to Cart click. The final gate
rereads saved state, API identity and current DOM, checks saved controls again, then rechecks
the href, visibility and URL immediately before invoking the actual click. Remote reads are
not atomic with browser state. One overall bounded deadline applies; late API/DOM results
cannot trigger a delayed click. Concurrent calls and changed data-port objects stop, and the
same page/port retains a stable wrapper. A failed final recheck consumes the conservative
low-level attempt latch even when no browser click occurred; report that as blocked-before-add
and do not create a new wrapper to retry. Uncertain actual clicks likewise cannot be repeated.
This is not a durable multi-instance purchase guard, a live verified authenticated data
integration, or autonomous cloud deployment. Existing cloud-only production gates remain.

On October 24, 2026 at approximately 21:47 UTC, the exact listing-release collector passed
an authorized local read-only verification: one visible main, one identity heading and one
Release Details link matched the intended release, listing URL, canonical URL and `og:url`.
No discrepancy or mutation was reported. This verifies that collector in that local context
only. The full saved-state/API identity-gated preparation pipeline has not been live-tested
or connected to a hosted authenticated data transport.

### Complete playable record sets
The rule-qualified preparation gate additionally requires the recomputed assessment to be
`reported_complete` or `no_disclosed_media_omission` for the exact release, with a bounded
positive authoritative vinyl count. Ordinary fully read comments need not explicitly state
that every record is included; absence of a disclosed omission is not physical verification.
Incomplete sets are rejected regardless of condition grade; unknown or unavailable evidence
also stops before preparation. This check repeats immediately before the Add to Cart click.
Seller-reported completeness is not independent physical verification. The bare low-level
helper is not an autonomous entry point. Explicit known-listing reviews can now fetch the
exact saved release catalog metadata through the shared request budget. Missing, stale or
unsupported metadata still leaves the omission assessment unknown and blocks preparation.
The combined live listing-plus-catalog test could not be completed in the test environment
and remains unverified. The standalone public catalog quantity read succeeded;
offline tests exercise the integrated path without claiming hosted or live checkout proof.
