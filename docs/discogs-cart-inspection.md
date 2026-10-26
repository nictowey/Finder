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

A later read of a user-opened local cart found one matching item row, a present address
section, an explicitly selected paid shipping method, and displayed USD subtotal,
shipping and total. Its hidden responsive rows used the exact notice label
`Plus Applicable Sales Tax` twice. The collector now recognizes that label without
reading hidden values and reports tax as unknown. The exact local cart collector
returned a bounded observation; the quote interpreter still returned
`final_quote_unverified`, because a displayed total and selected method do not prove
tax treatment, destination match or a final payable amount. The item page's
displayed shipping differed from the selected cart method; the cart method is the
relevant observation for this cart, but neither amount is an approved purchase quote.
The payment radio was selected, the buyer-policy checkbox was unchecked, and the
enabled `Place order and pay now` control was a submit button; none was operated.

## Hosted checkout preflight

`finder.discogs_hosted_checkout.HostedCartReadOnlyReader` sends only the existing
`current_cart` fixed action and expected listing ID to the private in-server browser
socket. It accepts only the bridge's stopped, read-only result for one exact item.
`HostedCheckoutPreflight` checks the current candidate/rule revision and durable
`cart_observed` one-copy claim both before and after that read. It compares the
displayed total's explicit currency and amount with the saved ceiling. A changed
candidate, unresolved cart claim, mismatched currency or over-ceiling display stops.
The result always says `order_submission: unavailable` and `purchase_allowed: false`.
No server-side submit, click, checkbox, payment, navigation or order adapter was added.

The server transport and preflight passed synthetic socket and database tests using
sanitized amounts matching the observed local cart shape. On October 25, 2026, an
approved one-use read of the already-open hosted cart stopped before yielding verified
quote evidence. Its grant was consumed and was not retried. The Python adapter returned
the generic `cart_read_stopped` code, so the specific bridge stop code was not retained;
the visible cart alone cannot establish which DOM check failed. The adapter now logs only
fixed, allowlisted stop codes and accepts the bridge's separate
`missing_address_and_unknown_final_quote` result as blocked evidence. That consumed
read was not retried. A separately approved scoped grant then succeeded on the
already-open hosted cart for listing `3526572746`: subtotal $199.99 USD, selected
shipping $5.85 USD, displayed total $205.84 USD, with a hidden tax notice and no
visible tax or fee amount. The distinct grant was consumed. This proves the hosted
reader's item and displayed-money extraction, not a final payable quote or checkout.
The durable access stop remains, and no buy target is enabled.

The cart showed a shipping address, but the reader deliberately does not return or
store its personal details. No saved destination or payment reference was supplied
to this read for comparison, so destination and payment binding remain unverified. The
hidden `Plus Applicable Sales Tax` notice leaves tax unknown, not zero; the
collector does not invent a fee amount when none is displayed. Discogs' [Seller
Policy](https://support.discogs.com/hc/en-us/articles/14587233205005-Seller-Policy)
puts its marketplace fee on the seller and prohibits payment-method surcharges;
seller handling belongs in shipping. This cart shows no separate buyer fee line,
so an absent fee line is not by itself a checkout blocker. Any new payable charge
would still need to be included in a fresh final quote. The current page
has an enabled `Place order and pay now` submit control, but no verified separate
noncommitting final-quote step. Its displayed total and selected shipping method
are therefore not a validated final payable amount. A live buyer would still need
an allowed noncommitting quote view or equivalent supported evidence resolving
tax, destination, payment and final total. Discogs' [buyer guide](https://support.discogs.com/hc/en-us/articles/360001573434-How-To-Buy-Music-On-Discogs)
and [post-order payment guide](https://support.discogs.com/hc/en-us/articles/360007626933-I-Just-Placed-An-Order-How-Do-I-Pay-For-It)
confirm that `Place order and pay now` creates an order before payment checkout;
abandoning payment does not undo it. It cannot be used to preview a quote in
this no-order phase.

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

An October 26 inspection of the already-rendered hosted item in Firefox Inspector
confirmed the same absolute link form and `data-tracking="add_to_cart"`. Inspector
found two DOM text matches; the adapter still requires exactly one visible
matching link. This verifies the link structure only; no hosted add was clicked
and its cart outcome remains unverified.

The dormant host-side coordinator additionally requires matching persisted
candidate, snapshot, rule, seller and detail evidence before a durable claim can
reach an adapter. A synthetic or public observation wrapped as hosted detail is
rejected before any claim. A separate `evaluate_pre_cart` verdict applies the
current grade, seller, completeness, damage, availability, freshness and one-copy
rules. Unknown currency, destination charges or physical pressing evidence can
remain unresolved for this reversible quote inspection only. Known currency or
destination conflicts, and known same-currency costs already over the ceiling,
still block. No amounts are filled with zero to manufacture eligibility.

Cart preparation also requires an immutable, exact `cart_quote_inspection`
authorization bound to the listing, release, seller, rule revision, observation
fingerprint and idempotency key, valid for at most five minutes. It is a scope
contract for an already-authorized caller, not an authentication mechanism or
grant issuer. It is checked before the claim, at the adapter boundary and after
reconciliation. Changed or expired scope leaves an uncertain durable claim that
cannot automatically retry. The persisted pre-cart verdict is separate from
the existing final review and cannot pass final checkout or order guards.

No live hosted cart adapter or grant is enabled by this implementation. Offline
intercepted tests prove one adapter invocation and durable reconciliation; the
actual hosted cart addition and final quote remain unverified.

Unknown authenticated shapes, a different listing, ambiguous links or cart contents and
access errors remain stops. Unknown final costs still block purchase qualification.
Normal UI automation is not permission to
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
**saved-release identity and pre-cart rule checks**, not complete buying qualification. The
gate rejects below-minimum media/sleeve grades, seller rating and feedback, mismatched
currency, item prices above the saved all-in ceiling, and blocked shipping. A matching
Discogs reference does not independently verify the seller's physical copy. Unknown final
shipping and tax remain unknown, never zero; any additional payable charge must be
included if it appears. `eligible_for_review` is not purchase
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
This is not by itself a durable multi-instance purchase guard, a live verified authenticated
data integration, or autonomous cloud deployment. Existing cloud-only production gates remain.

## Read-only no-order rehearsal and durable claim primitive

`scripts/discogs_no_order_rehearsal.mjs` exports `rehearseCurrentCart(page,
authenticatedDataPort, options)`. It accepts an already-authorized existing cart page,
read-only `readSnapshot` and `readListing` methods, an exact release ID, exact listing ID,
saved rules revision, and a UUID idempotency key. The helper rejects stale/changed rules,
wrong release, insufficient condition or seller evidence, disclosed missing media, disclosed
playable-media damage or an unknown damage assessment, and a
visible cart amount above the target ceiling. It requires one expected cart row and can
read an optional durable claim. Its return always has `purchase_allowed: false`,
`stop_required: true`, and `order_submission: disabled`. It has no navigation, cart click,
checkout, payment, or order method. A visible cart total is explicitly **not** a verified
delivered final quote: destination, shipping and tax can change, and any newly
displayed payable charge would need fresh review.
The hosted Python preflight now treats known, same-currency cart components as
a lower bound. It stops on mixed currencies, a displayed total below that bound,
or a bound above the saved ceiling even when tax or final total is deferred.
These checks do not make the displayed amount a final payable quote.

`src/finder/discogs_cart_claims.py` provides an isolated SQLite one-copy claim primitive.
It reserves a release with one UUID idempotency key and records `reserved`, `attempting`,
`cart_observed`, or `uncertain`. A competing reservation for the release fails across
process restarts. An interrupted `attempting` or `uncertain` state stays blocked; there is
no retry, expiry, deletion, payment, or order method. The claim file is created only by
an explicit `create=True` call. This is a storage primitive, not an authorization check.
No authenticated adapter currently connects this primitive to the browser gate or private
service. The no-order rehearsal therefore reports `durable_one_copy_claim_unconfigured`
unless an owner-authorized read port is provided. Do not infer that a simulated claim
authorizes a real cart add.

On October 25, 2026, the normal signed-in Chrome release marketplace page for release
`27048621` loaded without an access challenge and displayed `0–0 of 0` listings. This is
one current read-only observation. It does not establish a permitted hosted acquisition
feed or continuous monitoring. The documented marketplace API offers release-level stats
and lookup of a known listing ID; no verified exact-release new-listing delivery path is
wired here. Discogs' service terms restrict collection and use of product listings,
descriptions and prices, including scraping-like extraction tools, without express written
consent. The dedicated VPS browser profile and fixed read-only private socket are now
configured, but recurring acquisition remains paused. A provider-permitted delivery
mechanism remains a prerequisite for hosted autonomous acquisition.
Relevant official references: [marketplace API](https://www.discogs.com/developers/#page:marketplace),
[service terms](https://support.discogs.com/hc/en-us/articles/360009334333-Terms-of-Service),
[API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use),
and [Saved Searches](https://support.discogs.com/hc/en-us/articles/26355982507149-Using-Filters-Saved-Searches).

On October 24, 2026 at approximately 21:47 UTC, the exact listing-release collector passed
an authorized local read-only verification: one visible main, one identity heading and one
Release Details link matched the intended release, listing URL, canonical URL and `og:url`.
No discrepancy or mutation was reported. This verifies that collector in that local context
only. The full saved-state/API identity-gated preparation pipeline has not been live-tested
or connected to a hosted authenticated data transport.

### Complete playable record sets and damage assessment
The rule-qualified preparation gate additionally requires the recomputed assessment to be
`reported_complete` or `no_disclosed_media_omission` for the exact release, with a bounded
positive authoritative vinyl count. Ordinary fully read comments need not explicitly state
that every record is included; absence of a disclosed omission is not physical verification.
Incomplete sets are rejected regardless of condition grade; unknown or unavailable evidence
also stops before preparation. This check repeats immediately before the Add to Cart click.
The same preflight and final recheck require `media_defects.status` to be
`no_disclosed_media_defect` with `verified: false`. An affirmative playable-media or
playback defect blocks; absent, unsupported or ambiguous damage evidence also blocks.
The read-only no-order cart rehearsal uses this shared gate. No text assessment proves
the physical condition of the record.
Seller-reported completeness is not independent physical verification. The bare low-level
helper is not an autonomous entry point. Explicit known-listing reviews can now fetch the
exact saved release catalog metadata through the shared request budget. Missing, stale or
unsupported metadata still leaves the omission assessment unknown and blocks preparation.
The combined live listing-plus-catalog test could not be completed in the test environment
and remains unverified. The standalone public catalog quantity read succeeded;
offline tests exercise the integrated path without claiming hosted or live checkout proof.

## Hosted preparation and payment contracts, October 26

The separate `HostedCartPrepareAdapter` and `discogs_hosted_cart_helper.mjs` define
one fixed, scoped preparation protocol. Neither is enabled by the current private
service. The helper requires an owner-created acknowledgement bound to the boot,
exact immutable scope and idempotency key, and exclusively records use before
touching a page. It keeps the original read-access stop and its history. No
arbitrary URL, browser command, account setting or order action is exposed.
Each exclusive owner-only marker and its parent directory are synced before
browser access or a possible click. A helper-wide mutation fence remains after
any issued add, including a confirmed result; there is no automatic reset path.
An already-issued browser click may finish after a disconnect, so uncertainty
blocks subsequent preparation instead of causing a retry.

The proposed separate host path `/var/lib/sniper/cart-bridge` maps to
`/cart-bridge` in the browser container. Its group access must be reviewed before
configuration; it is not the existing read-only socket or profile mount. The
browser profile stays owner-only. No mount, listener, acknowledgement or live
cart authorization was installed by this code change.

Two earlier local DOM-tool reads timed out before command dispatch. A later
normal hosted Firefox UI visit on October 26 displayed an empty cart without a
challenge. Inspector verified `#page.cart_page.aside_right.with_tabs`, its
`#page_content[role="main"]`, the main's `h1` text `Your cart is empty`, and an
empty `div.orders`. The separate Marketplace H1 lies outside main. No account or
address data is part of the sanitized fixture. `discogs_empty_cart.mjs` now
checks that exact structure read-only; a fixed existing-page verifier binds the
result to the current scope and time. The cart helper checks again before a
possible click. Intrinsic document age must remain within five seconds; merely
rereading an old empty tab cannot renew it. Any other cart or checkout page in
the context invalidates the proof. This is a recently loaded display check,
not an atomic guarantee of merchant cart state. No automatic refresh was added.
These collectors are fixture-tested; they have not been
executed through the hosted program or enabled as a live cart helper.

`PaymentQuoteEvidence` is an assessment-only contract for a future noncommitting
quote source. The no-order coordinator binds it to the current candidate,
revision, observation fingerprint, one-copy claim, explicit currency, ceiling and
opaque destination/payment references before and after assessment. Complete
assertions still return `final_payable_quote_verified: false` and
`purchase_allowed: false`; they cannot supply live provenance or enable submit.
A missing fee line remains absent, without inventing a buyer surcharge or
manufacturing a displayed zero-fee line. Deferred tax and an order-creating
surface remain blockers. No supported Wallet capability was available during
this implementation, and no substitute card-handling route was configured.

The hosted PayPal sign-in was not authenticated. After the owner personally
completed its challenge on October 26, the existing page displayed
`You have been blocked.` and `We couldn't load the security challenge.` A
non-mutating observation recorded that restriction; no reload, alternate route,
further login attempt or payment followed. Live payment access remains blocked.
These offline contracts do not resolve that restriction or verify the account.

Order reconciliation ignores stale evidence before applying order-identity
conflicts. The synthetic order-safety store separately records submission time
before calling its simulated adapter. Its additive v1-to-v2 migration preserves
permanent duplicate fences and treats legacy uncertain/absent attempts without
a submission timestamp conservatively; an older absence lookup cannot clear
uncertainty. These are storage and no-submit tests, not live payment proof.

## Enhanced checkout observed on the hosted browser

After that empty-cart observation, one normal visible Add to Cart action added
only the specifically authorized KC test item. The resulting hosted cart showed
one item from one seller, explicit USD amounts, selected Economy - Media Mail
shipping, the existing saved address, and a displayed total equal to the owner's
ceiling. This was an operator UI inspection, not execution of the dormant helper.
No order, payment, agreement, address or shipping selection was submitted or
changed.

This cart's visible navigation button is
`button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]`, with
value `Go to Checkout` and type `submit`. Following it normally opened
`https://www.discogs.com/marketplace/checkout` without creating a visible order.
The review showed the same one item, saved address, USD subtotal/shipping/total,
and PayPal plus Debit or Credit Card buttons, both powered by PayPal. No separate
unpaid-order control was visible. No payment button was operated. There was no
explicit tax amount or tax-finality evidence captured; this display alone is
not promoted to a verified all-in payable quote.

The dormant `discogs_hosted_order_commit.mjs` is limited to the previously
observed legacy `checkout_v1` committing control. It requires exact current
authorization, candidate, final quote, destination and terms ports, explicit
matching displayed currency/amounts/tax/shipping, and a durable ledger fence
before at most one intercepted click. Those live ports are absent. It does not
operate the enhanced PayPal checkout, create an unpaid-order route, or establish
that such a route exists. Uncertain results cannot authorize a retry.

Official-document review on October 26 found that the
[buying guide](https://support.discogs.com/hc/en-us/articles/360001573434-How-To-Buy-Music-On-Discogs)
(displayed update November 19, 2025) and
[post-order payment guide](https://support.discogs.com/hc/en-us/articles/360007626933-I-Just-Placed-An-Order-How-Do-I-Pay-For-It)
(August 22, 2024) describe the older Place order and pay now flow and payment
from an existing order. They do not identify the observed checkout_v2 control
or establish its exact order-creation boundary. The current
[order-status guide](https://support.discogs.com/hc/en-us/articles/360007525494-What-Do-The-Different-Order-Statuses-Mean)
says PayPal orders normally arrive with payment received and distinguishes
incomplete payment from uncleared payment. This supports the separate status
model, not a promise that enhanced checkout can create an unpaid order alone.
No payment control, hidden legacy control or alternate payment path was used
to resolve that unknown.
