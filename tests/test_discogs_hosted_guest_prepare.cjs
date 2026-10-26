'use strict';
// No live browser or provider request. Observed selectors/summary/source-derived
// address shape are sanitized. Positive continuity/detail/address/cart-count
// markup below is an EXPLICITLY SYNTHETIC EXTENSION. Its cart caption follows
// the sole-text structure captured in hosted capture 1e05b67; labels are synthetic.
const assert = require('node:assert/strict');
const { before, test } = require('node:test');
const { JSDOM } = require('jsdom');
const { webcrypto, createHash, randomUUID } = require('node:crypto');
const { mkdtemp, writeFile, readFile, readdir, rm } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { Readable, Writable } = require('node:stream');
let port, helper, operator;
before(async () => { port = await import('../scripts/discogs_hosted_guest_prepare.mjs');
  helper = await import('../scripts/discogs_guest_card_prepare.mjs');
  operator = await import('../scripts/discogs_guest_checkout_operator.mjs'); });
const sha = value => createHash('sha256').update(value).digest('hex');
const json = value => JSON.stringify(value) + '\n';
const CART = 'https://www.discogs.com/sell/cart/';
const CHECKOUT = 'https://www.discogs.com/marketplace/checkout';
const DETAIL = 'https://www.discogs.com/sell/item/1366950910';
const PROVIDER = 'https://www.paypal.com/smart/buttons?PRIVATE=never-output';
const ADDRESS = ['Synthetic Buyer', '1 Fixture Street', 'Test City, TS, 00000', 'US'];
const addressMarkup = ADDRESS.map(line => `<span class="_ships-to-address__line_fixture">${line}</span>`).join('');
const go = '<button class="order_button checkout_v2" name="Action.ProceedCommerceCheckout" type="submit" value="Go to Checkout">Go to Checkout</button>';
const cartMarkup = `<!doctype html><title>Cart</title><h1>You have 1 item in your cart from 1 seller.</h1>
<form class="legacy"><div id="cart-order-fixture" class="order_container"><table><tr class="order_row"><td>
<a class="item_link" href="/sell/item/1366950910">Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>
<span>Media: Mint Sleeve: Generic</span></td></tr></table>
<div id="shipping_address_fixture"><address>${addressMarkup}</address><a class="edit_shipping_address">Edit</a></div>
<table class="order_summary"><tr class="order_subtotal"><td>Subtotal</td><td class="order_summary_value">$0.10 USD</td></tr>
<tr><td>Shipping</td><td class="order_shipping_amount order_summary_value">$4.95 USD</td></tr>
<tr class="order_total"><td>Total</td><td class="order_summary_value">$5.05 USD</td></tr></table>
<div class="order_shipping_fields"><select class="shipping_method" name="shipping_method"><option selected>Economy - Media Mail</option></select></div>
${go}</div></form>`;
const summary = `<!doctype html><title>Checkout</title><h1>Checkout from Mailman21842</h1><h2>Items (1)</h2>
<div class="_checkout-summary__content_fixture"><ul class="_checkout-summary__unordered-list_fixture">
<li class="_checkout-sell-item__list-item_fixture"><h3 class="_checkout-sell-item__title_fixture"><span>Fixture Artist</span><span> - </span><span>Synthetic KC Single</span><span> (7&quot;, 45 RPM, Single)</span></h3>
<div class="_checkout-sell-item__condition_fixture">Media: Mint<br>Sleeve: Generic</div>
<div class="_checkout-sell-item__price_fixture">$0.10 USD</div></li></ul>
<dl class="_checkout-summary__totals_fixture">
<div class="_checkout-summary__subtotal_fixture"><dt>Subtotal</dt><dd class="_checkout-summary__money-value_fixture">$0.10 USD</dd></div>
<div class="_checkout-summary__subtotal_fixture"><dt>Shipping via Economy - Media Mail</dt><dd class="_checkout-summary__money-value_fixture">$4.95 USD</dd></div>
<div class="_checkout-summary__total--color-red_fixture"><dt>Total</dt><dd class="_checkout-summary__money-value_fixture">$5.05 USD</dd></div>
</dl></div><div class="_ships-to-address__container_fixture"><h2>My Shipping Address</h2>
<div class="_ships-to-address__content_fixture"><address>${addressMarkup}</address></div></div>
<iframe src="${PROVIDER}"></iframe>`;
const detailMarkup = `<!doctype html><title>Item</title><main>
<a class="hifi-heading-link-large" href="https://www.discogs.com/release/3422100">Synthetic KC Single</a>
<span class="hifi-grade-chip-display"><span>Media</span><span>Mint</span><span>|</span><span>Sleeve</span><span>Generic</span></span>
<div data-testid="item-details"><p class="break-words whitespace-pre-line">Synthetic condition.</p></div>
<a href="/seller/Mailman21842/profile">Mailman21842</a><p class="hifi-body-kind-heavy">100%</p>
<a href="/sell/seller_feedback/Mailman21842">View all 100 reviews</a><h2 class="hifi-heading-large">$0.10</h2>
<button disabled>In Cart</button></main>`;
const providerMarkup = '<!doctype html><title>PayPal</title><a role="link" data-funding-source="card" aria-label="Debit or Credit Card" aria-disabled="false">Debit or Credit Card</a><div id="card-fields-container"></div><button>Pay</button>';
function dom(html, url) {
  const d = new JSDOM(html, { url, runScripts: 'outside-only' });
  Object.defineProperty(d.window.document, 'readyState', { value: 'complete', configurable: true });
  Object.defineProperty(d.window.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
  Object.defineProperty(d.window.crypto, 'subtle', { value: webcrypto.subtle });
  d.window.TextEncoder = TextEncoder;
  d.window.Element.prototype.getClientRects = function () { return this.hidden ? [] : [{ width: 200, height: 40 }]; };
  return d;
}
function browser({ cart = cartMarkup, checkout = summary, detail = detailMarkup, provider = providerMarkup,
  beforeEvaluate, beforeGo, beforeCardClick, afterGo, duplicateFrames = false, frameUrl = PROVIDER } = {}) {
  const all = [], effects = { go: 0, card: 0, goIssued: 0, cardIssued: 0, pay: 0, pages: 0, evaluate: 0 };
  let navigationResolve = null;
  const main = {}, state = { merchant: dom(cart, CART), detail: dom(detail, DETAIL), provider: dom(provider, frameUrl) };
  all.push(...Object.values(state));
  const evaluate = async (which, fn, arg) => {
    effects.evaluate++; if (beforeEvaluate) await beforeEvaluate({ which, fn, arg, state, effects });
    return state[which].window.eval(`(${fn.toString()})`)(arg);
  };
  const visible = element => element?.isConnected && !element.hidden && element.style.display !== 'none';
  const handle = (which, element) => ({
    isVisible: async () => visible(element), isEnabled: async () => !element.disabled && element.getAttribute('aria-disabled') !== 'true',
    getAttribute: async name => element.getAttribute(name),
    evaluate: async fn => state[which].window.eval(`(${fn.toString()})`)(element),
    contentFrame: async () => frame, ownerFrame: async () => main,
    click: async () => {
      assert.ok(visible(element), 'bound element must remain visible and connected');
      if (which === 'merchant') {
        effects.goIssued++; if (beforeGo) await beforeGo(state);
        effects.go++; assert.equal(element.getAttribute('name'), 'Action.ProceedCommerceCheckout');
        state.merchant = dom(checkout, CHECKOUT); all.push(state.merchant); if (afterGo) await afterGo(state);
        if (navigationResolve) { const url = state.merchant.window.location.href; navigationResolve({ url: () => url }); navigationResolve = null; }
      } else {
        effects.cardIssued++;
        if (beforeCardClick) await beforeCardClick(state);
        assert.equal(element.getAttribute('data-funding-source'), 'card'); effects.card++;
      }
    },
  });
  const frame = { url: () => state.provider.window.location.href, parentFrame: () => main,
    frameElement: async () => handle('merchant', state.merchant.window.document.querySelector('iframe')),
    evaluateHandle: async () => state.provider.window.document,
    evaluate: (fn, arg) => evaluate('provider', fn, arg),
    $: async selector => { const e = state.provider.window.document.querySelector(selector); return e ? handle('provider', e) : null; },
    locator: selector => ({ count: async () => state.provider.window.document.querySelectorAll(selector).length }),
  };
  const page = { url: () => state.merchant.window.location.href,
    evaluateHandle: async () => state.merchant.window.document,
    evaluate: (fn, arg) => evaluate('merchant', fn, arg),
    $: async selector => { assert.equal(selector, port.HOSTED_GO_SELECTOR); const e = state.merchant.window.document.querySelector(selector); return e ? handle('merchant', e) : null; },
    waitForNavigation: () => new Promise(resolve => { navigationResolve = resolve; }),
    waitForURL: async url => assert.equal(page.url(), url), mainFrame: () => main,
    frames: () => page.url() === CHECKOUT ? duplicateFrames ? [frame, frame] : [frame] : [] };
  const detailPage = { url: () => state.detail.window.location.href,
    evaluateHandle: async () => state.detail.window.document,
    evaluate: (fn, arg) => evaluate('detail', fn, arg) };
  const context = { pages: () => { effects.pages++; return [page, detailPage]; } };
  return { context, page, detailPage, frame, effects, state, close: () => all.forEach(d => d.window.close()) };
}
async function ledgerFixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'guest-hosted-fixture-'));
  const now = Date.now() / 1000;
  const put = async (name, value) => { const bytes = json(value); await writeFile(join(directory, name), bytes, { mode: 0o600 }); return sha(bytes); };
  const original = { schema: 'discogs_guest_card_init_fence_v1', intent_id: randomUUID(), scope_digest: 'a'.repeat(64), release_id: 3422100, listing_id: '1366950910', created_at: now - 30 };
  const a = await put(helper.GUEST_CARD_FENCE, original);
  const recovery = { schema: 'discogs_guest_card_recovery_review_v1', original_fence_sha256: a, original_intent_id: original.intent_id,
    original_scope_digest: original.scope_digest, retry_intent_id: randomUUID(), retry_scope_digest: 'b'.repeat(64) };
  const b = await put(helper.GUEST_CARD_RECOVERY_REVIEW, recovery);
  const retry = { schema: 'discogs_guest_card_recovery_attempt_v1', attempt: 1, original_fence_sha256: a,
    reconciliation_sha256: b, intent_id: recovery.retry_intent_id, scope_digest: recovery.retry_scope_digest, created_at: now - 29 };
  const c = await put(helper.GUEST_CARD_RECOVERY_FENCE, retry);
  const navigation = { schema: 'discogs_guest_card_navigation_reopen_review_v1', original_fence_sha256: a,
    original_intent_id: original.intent_id, original_scope_digest: original.scope_digest, recovery_review_sha256: b,
    recovery_fence_sha256: c, recovery_intent_id: retry.intent_id, recovery_scope_digest: retry.scope_digest,
    reopen_intent_id: randomUUID(), reopen_scope_digest: 'c'.repeat(64) };
  const d = await put(helper.GUEST_CARD_NAVIGATION_REOPEN_REVIEW, navigation);
  await put(helper.GUEST_CARD_NAVIGATION_REOPEN_FENCE, { schema: 'discogs_guest_card_navigation_reopen_attempt_v1', attempt: 1,
    original_fence_sha256: a, recovery_review_sha256: b, recovery_fence_sha256: c, reconciliation_sha256: d,
    intent_id: navigation.reopen_intent_id, scope_digest: navigation.reopen_scope_digest, created_at: now - 28 });
  const scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842',
    quantity: 1, currency: 'USD', max_total: '5.05', rules_revision: 0, quote_fingerprint: 'd'.repeat(64), destination_ref: randomUUID(),
    terms_fingerprint: 'e'.repeat(64), issued_at: now, expires_at: now + 300 };
  const request = { job_id: randomUUID(), ready_ref: randomUUID(), scope,
    testAuthorization: { source: 'explicit_user_authorized_no_submit_test', approval_ref: scope.approval_ref,
      scope_digest: helper.guestCardScopeDigest(scope), attempt_id: scope.intent_id, authorized_at: now - 3600,
      issued_at: now, expires_at: now + 300, card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false },
    priorFailure: { source: 'operator_reviewed_normal_discogs_ui', last_error_code: 'SESSION_EXPIRED', release_id: scope.release_id,
      listing_id: scope.listing_id, seller: scope.seller, quantity: 1, currency: 'USD', max_total: '5.05', card_data_entered: false,
      pay_invoked: false, approval_invoked: false, capture_invoked: false, failure_observed_at: now - 20,
      order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50', order_history_archived: 'All',
      order_history_status: 'All', order_history_count: 0, order_history_complete: true, order_history_observed_at: now,
      reviewed_at: now, expires_at: now + 300 } };
  const authorization = { job_id: request.job_id, scope_digest: helper.guestCardScopeDigest(scope),
    approved_destination_ref: scope.destination_ref, approved_destination_digest: sha(ADDRESS.join('\n')) };
  try { await run({ directory, request, authorization }); }
  finally { await rm(directory, { recursive: true, force: true }); }
}
const execute = (f, b, signal) => port.createHostedGuestPrepareFixture({ context: b.context, directory: f.directory, authorization: f.authorization, signal })(f.request);
const records = async directory => Promise.all((await readdir(directory)).filter(name => /^guest-checkout-[0-9]{6}\.json$/u.test(name)).sort().map(async name => JSON.parse(await readFile(join(directory, name), 'utf8'))));

test('concrete positive SYNTHETIC EXTENSION navigates once, initializes once, records unknown and never Pay', async () => ledgerFixture(async f => {
  const b = browser();
  try {
    const result = await execute(f, b);
    assert.equal(result.code, 'guest_form_observation_required', JSON.stringify(result));
    assert.equal(result.card_initialization_issued, true);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 1, 0]);
    const audit = await records(f.directory);
    assert.deepEqual(audit.map(value => value.kind), ['ready', 'review', 'attempt', 'outcome']);
    assert.equal(audit[3].payload.outcome, 'unknown');
    assert.equal(audit[1].payload.provenance.source, 'fixed_server_dom_current_checkout');
    assert.ok(!json(result).includes('PRIVATE') && !json(result).includes('Fixture Street'));
  } finally { b.close(); }
}));

test('observed summary lacks continuity fields: exact missing fields fail closed before card', async () => ledgerFixture(async f => {
  const captured = summary.replace(/<h2>Items \(1\)<\/h2>/u, '').replace(/<h3 class="_checkout-sell-item__title_fixture">.*?<\/h3>/u, '')
    .replace(/<div class="_checkout-sell-item__condition_fixture">.*?<\/div>/u, '').replace(/<div class="_checkout-sell-item__price_fixture">.*?<\/div>/u, '');
  const b = browser({ checkout: captured });
  try {
    const result = await execute(f, b);
    assert.equal(result.code, 'visible_scope_evidence_missing');
    assert.deepEqual(result.missing_fields, ['quantity', 'title_continuity', 'condition_continuity', 'item_price_continuity']);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]);
    assert.equal((await records(f.directory)).length, 0);
  } finally { b.close(); }
}));

test('missing current detail and absent explicit cart item count block before navigation', async () => ledgerFixture(async f => {
  const b = browser(); b.context.pages = () => [b.page];
  try { assert.equal((await execute(f, b)).code, 'listing_detail_required'); assert.equal(b.effects.go, 0); }
  finally { b.close(); }
  const c = browser({ cart: cartMarkup.replace('You have 1 item in your cart from 1 seller.', 'Cart') });
  try { const result = await execute(f, c); assert.equal(result.code, 'visible_scope_evidence_missing'); assert.ok(result.missing_fields.includes('quantity')); assert.equal(c.effects.go, 0); }
  finally { c.close(); }
}));

test('scope, currency, job, destination, authorization and raw address injection fail before DOM', async () => {
  for (const alter of [
    f => { f.request.scope.currency = 'EUR'; }, f => { f.request.scope.listing_id = '1366950911'; },
    f => { f.request.scope.max_total = '5.06'; }, f => { f.request.scope.quantity = 2; },
    f => { f.request.job_id = randomUUID(); }, f => { f.authorization.approved_destination_ref = randomUUID(); },
    f => { f.request.approved_destination = { lines: ADDRESS }; }, f => { f.request.testAuthorization.payment_allowed = true; },
  ]) await ledgerFixture(async f => {
    alter(f); const b = browser();
    try { assert.equal((await execute(f, b)).code, 'scoped_authorization_required'); assert.equal(b.effects.pages, 0); }
    finally { b.close(); }
  });
});

test('observed address structure must match ephemeral approved destination; mismatches and duplicates fail', async () => {
  for (const checkout of [summary.replace('1 Fixture Street', '2 Fixture Street'),
    summary.replace('</address>', '</address><address>' + addressMarkup + '</address>'),
    summary.replace('class="_ships-to-address__content_fixture"', 'class="unknown"')]) await ledgerFixture(async f => {
    const b = browser({ checkout });
    try { const result = await execute(f, b); assert.ok(result.missing_fields.includes('destination')); assert.equal(b.effects.card, 0); }
    finally { b.close(); }
  });
});

test('challenge, extra agreement, changed listing seller, duplicate go, wrong money stop before actions', async () => {
  for (const config of [
    { cart: cartMarkup.replace('<title>Cart</title>', '<title>Cloudflare</title>') },
    { cart: cartMarkup.replace('</form>', '<input type="checkbox"></form>') },
    { cart: cartMarkup.replace(go, go + go) },
    { cart: cartMarkup.replace('$5.05 USD', '$5.06 USD') },
    { detail: detailMarkup.replace('/seller/Mailman21842/profile', '/seller/Other/profile') },
  ]) await ledgerFixture(async f => {
    const b = browser(config);
    try { assert.equal((await execute(f, b)).status, 'blocked'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]); }
    finally { b.close(); }
  });
});

test('provider association, duplicate frames and duplicate/disabled/hidden card controls stop', async () => {
  for (const config of [ { duplicateFrames: true }, { frameUrl: 'https://www.paypal.com/other?private=secret' },
    { provider: providerMarkup.replace('</a>', '</a><a role="link" data-funding-source="card" aria-label="Debit or Credit Card" aria-disabled="false">Other</a>') },
    { provider: providerMarkup.replace('aria-disabled="false"', 'aria-disabled="true"') },
    { provider: providerMarkup.replace('<a role', '<a hidden role') },
  ]) await ledgerFixture(async f => {
    const b = browser(config);
    try { assert.equal((await execute(f, b)).status, 'blocked'); assert.equal(b.effects.card, 0); }
    finally { b.close(); }
  });
});

test('revalidation catches DOM quote, title, destination and document changes after durable claim', async () => {
  for (const mutation of ['quote', 'title', 'address', 'document', 'control']) await ledgerFixture(async f => {
    let changed = false;
    const b = browser({ beforeEvaluate: async ({ state }) => {
      if (changed || (await records(f.directory)).length !== 3) return;
      changed = true;
      const doc = state.merchant.window.document;
      if (mutation === 'quote') doc.querySelector('._checkout-summary__total--color-red_fixture dd').textContent = '$5.06 USD';
      if (mutation === 'title') doc.querySelector('h3').textContent = 'Changed title';
      if (mutation === 'address') doc.querySelector('address span').textContent = 'Changed destination';
      if (mutation === 'document') state.merchant = dom(summary, CHECKOUT);
      if (mutation === 'control') state.provider.window.document.querySelector('a').remove();
    } });
    try { const result = await execute(f, b); assert.equal(result.status, 'blocked'); assert.equal(b.effects.card, 0); assert.equal((await records(f.directory)).at(-1).payload.outcome, 'unknown'); }
    finally { b.close(); }
  });
});

test('pending, guest_form_visible and unknown survive restart and block before ANY browser access', async () => {
  for (const outcome of ['pending', 'guest_form_visible', 'unknown']) await ledgerFixture(async f => {
    const b = browser(); try { await execute(f, b); } finally { b.close(); }
    const file = join(f.directory, 'guest-checkout-000004.json');
    if (outcome === 'pending') await rm(file);
    else if (outcome === 'guest_form_visible') { const value = JSON.parse(await readFile(file)); value.payload.outcome = outcome; await writeFile(file, json(value), { mode: 0o600 }); }
    const c = browser();
    try { const result = await execute(f, c); assert.equal(result.code, 'prior_attempt_unresolved'); assert.equal(c.effects.pages, 0); assert.equal(c.effects.evaluate, 0); }
    finally { c.close(); }
  });
});

test('cancelled request and concurrent same-context invocation never dispatch later', async () => ledgerFixture(async f => {
  const controller = new AbortController(); controller.abort();
  const b = browser();
  try { assert.equal((await execute(f, b, controller.signal)).code, 'cancelled'); assert.equal(b.effects.pages, 0); }
  finally { b.close(); }
  let release, entered;
  const enteredPromise = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let held = false;
  const c = browser({ beforeEvaluate: async () => { if (!held) { held = true; entered(); await gate; } } });
  const first = execute(f, c); await enteredPromise;
  assert.equal((await execute(f, c)).code, 'operation_busy'); release();
  try { await first; assert.equal(c.effects.card, 1); }
  finally { c.close(); }
}));

test('factory defaults deny and restricts fixed ledger; URL matching never exports private query', async () => ledgerFixture(async f => {
  const b = browser();
  try {
    assert.equal((await port.createHostedGuestPrepareOperation()({})).code, 'scoped_authorization_required');
    assert.equal((await port.prepareHostedGuestForm(b.context, f.request, { authorization: f.authorization, ledgerDirectory: '/tmp/new-ledger' })).code, 'fixed_ledger_required');
    assert.equal(b.effects.pages, 0);
    assert.equal(port.isObservedCardFrame(PROVIDER), true);
    for (const url of ['https://evil.test/smart/buttons', 'https://www.paypal.com/smart/buttons/other', 'https://www.paypal.com/smart/buttons#x']) assert.equal(port.isObservedCardFrame(url), false);
  } finally { b.close(); }
}));

function readonlyGrant() {
  const now = Date.now() / 1000, job = randomUUID();
  return { request: { schema: 'discogs_guest_readonly_probe_v1', job_id: job, phase: 'inspect' },
    authorization: { schema: 'discogs_guest_readonly_authorization_v1', job_id: job,
      issued_at: now, expires_at: now + 1800, release_id: '3422100', listing_id: '1366950910',
      seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05', approved_destination_ref: 'saved_shipping_address' } };
}
function confirmation(grant, observed) {
  return { request: { ...grant.request, phase: 'confirm_saved_destination', document_ref: observed.document_ref },
    authorization: { ...grant.authorization, destination_ack: { document_ref: observed.document_ref,
      source: 'operator_visual_saved_destination_match', approved_destination_ref: 'saved_shipping_address', observed_at: Date.now() / 1000 } } };
}
const probe = (b, grant, signal) => port.probeCurrentGuestPreparationEvidence(b.context, grant.request, { authorization: grant.authorization, signal });

test('distinct read-only authority reports honest unbound destination and safe structure without transaction evidence', async () => ledgerFixture(async f => {
  const b = browser(); b.state.merchant = dom(summary, CHECKOUT);
  try {
    const result = await probe(b, readonlyGrant());
    assert.equal(result.status, 'observed', JSON.stringify(result));
    assert.deepEqual(result.missing_fields, ['destination_match']);
    assert.equal(result.merchant_route, 'enhanced_checkout');
    assert.equal(result.address_present, true); assert.equal(result.destination_binding_available, false);
    assert.equal(result.destination_match, null); assert.equal(result.observed.destination_match, null);
    assert.equal(result.structure.media_grade, 'M'); assert.equal(result.structure.sleeve_grade, 'Generic');
    assert.equal(result.structure.item_title_present, true); assert.equal(result.structure.item_row_count, 1);
    assert.equal(result.initialization_allowed, false); assert.equal(result.normal_cart_transition_verified, false);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    assert.equal((await records(f.directory)).length, 0);
    for (const forbidden of ['Fixture Street', 'Synthetic KC Single', 'PRIVATE', sha(ADDRESS.join('\n'))]) assert.ok(!json(result).includes(forbidden));
    b.state.provider.window.document.querySelector('#card-fields-container').innerHTML = '<input autocomplete="cc-number" value="NEVER_READ">';
    const existing = await probe(b, readonlyGrant());
    assert.equal(existing.observed.existing_guest_form, true); assert.ok(!json(existing).includes('NEVER_READ'));
    const transaction = await port.probeCurrentGuestPreparationEvidence(b.context, f.request, { authorization: f.authorization });
    assert.equal(transaction.code, 'readonly_authorization_required');
  } finally { b.state.merchant.window.close(); b.close(); }
}));

test('provider challenge and existing guest container preserve current form without initialization', async () => {
  for (const provider of [providerMarkup.replace('<title>PayPal</title>', '<title>Security check</title>'),
    providerMarkup.replace('id="card-fields-container"></div>', 'id="card-fields-container"><input autocomplete="cc-number"></div>')]) await ledgerFixture(async f => {
    const b = browser({ provider });
    try { const result = await execute(f, b); assert.ok(['access_restricted', 'existing_guest_form_preserved'].includes(result.code)); assert.equal(b.effects.card, 0); }
    finally { b.close(); }
  });
});

test('stale reconciliation and cancellation during DOM reads prevent checkout navigation', async () => ledgerFixture(async f => {
  f.request.priorFailure.order_history_observed_at -= 301;
  const b = browser();
  try { assert.equal((await execute(f, b)).code, 'scoped_authorization_required'); assert.equal(b.effects.pages, 0); }
  finally { b.close(); }
  f.request.priorFailure.order_history_observed_at += 301;
  const controller = new AbortController();
  const c = browser({ beforeEvaluate: () => controller.abort() });
  try { assert.equal((await execute(f, c, controller.signal)).code, 'cancelled'); assert.deepEqual([c.effects.go, c.effects.card], [0, 0]); }
  finally { c.close(); }
}));

test('stale post-claim DOM observation cannot dispatch card and remains durably unresolved', async () => ledgerFixture(async f => {
  const actualNow = Date.now;
  let advanced = false;
  const b = browser({ beforeEvaluate: async () => {
    if (!advanced && (await records(f.directory)).length === 3) { advanced = true; Date.now = () => actualNow() + 21000; }
  } });
  try { const result = await execute(f, b); assert.equal(result.code, 'inspection_stale'); assert.equal(b.effects.card, 0); assert.equal((await records(f.directory)).at(-1).payload.outcome, 'unknown'); }
  finally { Date.now = actualNow; b.close(); }
}));

test('unexpected normal-navigation redirect and unrelated provider association cannot initialize', async () => ledgerFixture(async f => {
  const b = browser({ afterGo: state => { state.merchant = dom(summary, 'https://www.discogs.com/login'); } });
  try { assert.equal((await execute(f, b)).code, 'checkout_redirected'); assert.equal(b.effects.card, 0); }
  finally { b.state.merchant.window.close(); b.close(); }
  const c = browser(); c.frame.parentFrame = () => ({});
  try { assert.equal((await execute(f, c)).code, 'provider_frame_unassociated'); assert.equal(c.effects.card, 0); }
  finally { c.close(); }
}));

test('unmatched disposition reference, secret-shaped extras and invalid authorization times fail before pages', async () => {
  for (const alter of [
    f => { f.request.priorFormDisposition_sha256 = 'f'.repeat(64); },
    f => { f.request.priorFailure.unapproved_extra = 'NEVER_ECHO'; },
    f => { f.request.testAuthorization.authorized_at = -1; },
    f => { f.request.testAuthorization.issued_at = String(f.request.testAuthorization.issued_at); },
  ]) await ledgerFixture(async f => {
    alter(f); const b = browser();
    try { const result = await execute(f, b); assert.equal(result.status, 'blocked'); assert.equal(b.effects.pages, 0); assert.ok(!json(result).includes('NEVER_ECHO')); }
    finally { b.close(); }
  });
});

test('unknown browser and authorization errors never echo exception text or caller-forged stop labels', async () => ledgerFixture(async f => {
  const b = browser(); b.context.pages = () => { throw Object.assign(new Error('PRIVATE_ERROR_NEVER_LOG'), { safeCode: 'PRIVATE_FORGED_CODE' }); };
  try {
    const result = await execute(f, b);
    assert.equal(result.code, 'unknown_internal_stop'); assert.ok(!json(result).includes('PRIVATE'));
    const operation = port.createHostedGuestPrepareOperation({ getContext: () => { throw new Error('PRIVATE'); }, authorize: () => f.authorization });
    assert.equal((await operation(f.request)).code, 'operation_unavailable');
  } finally { b.close(); }
}));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const eventLoopTurn = () => new Promise(resolve => setImmediate(resolve));

test('cancelled delayed checkout click retains flight guards until completion and never advances to card', async () => ledgerFixture(async f => {
  const started = deferred(), finishClick = deferred(), completed = deferred();
  const controller = new AbortController();
  const b = browser({ beforeGo: async () => { started.resolve(); await finishClick.promise; }, afterGo: () => completed.resolve() });
  const other = browser();
  try {
    const running = execute(f, b, controller.signal); await started.promise;
    controller.abort();
    const result = await running;
    assert.equal(result.code, 'cancelled');
    assert.deepEqual([b.effects.goIssued, b.effects.go, b.effects.cardIssued, b.effects.card], [1, 0, 0, 0]);
    assert.equal((await execute(f, b)).code, 'operation_busy');
    assert.equal((await execute(f, other)).code, 'operation_busy');
    assert.equal(other.effects.pages, 0);
    const readsAtCancellation = b.effects.evaluate;
    let drainFinished = false;
    const drained = port.waitForHostedGuestPreparationSettlement(b.context).then(() => { drainFinished = true; });
    await eventLoopTurn(); assert.equal(drainFinished, false);
    finishClick.resolve(); await completed.promise; await drained; await eventLoopTurn();
    assert.deepEqual([b.effects.go, b.effects.cardIssued, b.effects.card, b.effects.pay], [1, 0, 0, 0]);
    assert.equal(b.effects.evaluate, readsAtCancellation);
    assert.equal((await execute(f, b)).code, 'navigation_requires_reconciliation');
    assert.equal((await records(f.directory)).length, 0);
  } finally { finishClick.resolve(); await eventLoopTurn(); b.close(); other.close(); }
}));

test('cancelled delayed card click retains guards, records unknown, and never queues a second effect', async () => ledgerFixture(async f => {
  const started = deferred(), finishClick = deferred();
  const controller = new AbortController();
  const b = browser({ beforeCardClick: async () => { started.resolve(); await finishClick.promise; } });
  const other = browser();
  try {
    const running = execute(f, b, controller.signal); await started.promise;
    controller.abort();
    const result = await running;
    assert.equal(result.code, 'cancelled'); assert.equal(result.initialization, 'outcome_requires_reconciliation');
    assert.deepEqual([b.effects.go, b.effects.cardIssued, b.effects.card, b.effects.pay], [1, 1, 0, 0]);
    assert.equal((await records(f.directory)).at(-1).payload.outcome, 'unknown');
    assert.equal((await execute(f, b)).code, 'operation_busy');
    assert.equal((await execute(f, other)).code, 'operation_busy');
    assert.equal(other.effects.pages, 0);
    const readsAtCancellation = b.effects.evaluate;
    let drainFinished = false;
    const drained = port.waitForHostedGuestPreparationSettlement(b.context).then(() => { drainFinished = true; });
    await eventLoopTurn(); assert.equal(drainFinished, false);
    finishClick.resolve(); await drained; await eventLoopTurn();
    assert.deepEqual([b.effects.go, b.effects.cardIssued, b.effects.card, b.effects.pay], [1, 1, 1, 0]);
    assert.equal(b.effects.evaluate, readsAtCancellation);
    assert.equal((await execute(f, b)).code, 'navigation_requires_reconciliation');
    assert.equal((await execute(f, other)).code, 'prior_attempt_unresolved');
    assert.equal(other.effects.pages, 0);
    assert.equal((await records(f.directory)).length, 4);
  } finally { finishClick.resolve(); await eventLoopTurn(); b.close(); other.close(); }
}));

async function operatorRecord(directory, mode, value) {
  let output = '';
  const sink = new Writable({ write(chunk, encoding, done) { output += chunk.toString(); done(); } });
  await operator.runCurrentCheckoutClaim({ directory, mode, input: Readable.from([json(value)]), output: sink });
  return JSON.parse(output);
}

test('reviewed form disposition preserves the genuine historical SESSION_EXPIRED evidence and requires newer reconciliation', async () => ledgerFixture(async f => {
  const originalFailureAt = f.request.priorFailure.failure_observed_at;
  const initial = browser();
  try { assert.equal((await execute(f, initial)).code, 'guest_form_observation_required'); }
  finally { initial.close(); }
  // This isolated fixture models an operator-observed visible form; no live
  // dismissal or form-visibility claim is produced by the preparation port.
  const attemptBytes = await readFile(join(f.directory, 'guest-checkout-000003.json'));
  const attempt = JSON.parse(attemptBytes);
  const outcomeFile = join(f.directory, 'guest-checkout-000004.json');
  const outcome = JSON.parse(await readFile(outcomeFile)); outcome.payload.outcome = 'guest_form_visible';
  const outcomeBytes = json(outcome); await writeFile(outcomeFile, outcomeBytes, { mode: 0o600 });
  const actualNow = Date.now;
  let clock = Math.ceil(actualNow() / 1000) * 1000 + 5000;
  Date.now = () => clock;
  let nextBrowser;
  try {
    const at = clock / 1000, s = f.request.scope;
    const disposition = await operatorRecord(f.directory, 'disposition', {
      type: 'record_form_disposition', source: 'operator_reviewed_normal_sdk_dismissal',
      intent_id: s.intent_id, ready_ref: f.request.ready_ref, scope_digest: helper.guestCardScopeDigest(s),
      previous_claim_sha256: sha(attemptBytes), previous_outcome_sha256: sha(outcomeBytes),
      release_id: s.release_id, listing_id: s.listing_id, seller: s.seller, quantity: 1, currency: 'USD', max_total: '5.05',
      blank_form_verified: true, blank_form_observed_at: at - 4, sdk_close_verified: true,
      dismissal_kind: 'visible_sdk_close', dismissal_observed_at: at - 3, card_data_entered: false,
      pay_invoked: false, approval_invoked: false, capture_invoked: false, current_form_present: false,
      current_url: CART, normal_route_verified: true, current_observed_at: at - 2,
      order_history_url: f.request.priorFailure.order_history_url, order_history_archived: 'All',
      order_history_status: 'All', order_history_count: 0, order_history_complete: true,
      order_history_observed_at: at - 1, reviewed_at: at, expires_at: at + 300 });
    assert.equal(disposition.type, 'form_disposition_recorded', JSON.stringify(disposition));
    const state = await operator.inspectGuestCheckoutLedger({ directory: f.directory });
    assert.equal(state.init_allowed, false); assert.equal(state.no_submit_init_allowed, true);
    clock += 1000;
    const now = clock / 1000;
    f.request.job_id = randomUUID(); f.request.ready_ref = randomUUID();
    f.request.scope = { ...s, intent_id: randomUUID(), issued_at: now, expires_at: now + 300 };
    f.request.priorFormDisposition_sha256 = disposition.disposition_sha256;
    f.request.testAuthorization = { ...f.request.testAuthorization, attempt_id: f.request.scope.intent_id,
      scope_digest: helper.guestCardScopeDigest(f.request.scope), issued_at: now, expires_at: now + 300 };
    f.request.priorFailure = { ...f.request.priorFailure, order_history_observed_at: now,
      reviewed_at: now, expires_at: now + 300 };
    f.authorization = { ...f.authorization, job_id: f.request.job_id, scope_digest: helper.guestCardScopeDigest(f.request.scope) };
    const deniedBrowser = browser();
    try {
      f.request.priorFailure.last_error_code = 'guest_form_dismissed';
      assert.equal((await execute(f, deniedBrowser)).code, 'scoped_authorization_required');
      assert.equal(deniedBrowser.effects.pages, 0);
    } finally { f.request.priorFailure.last_error_code = 'SESSION_EXPIRED'; deniedBrowser.close(); }
    nextBrowser = browser();
    assert.equal((await execute(f, nextBrowser)).code, 'guest_form_observation_required');
    assert.deepEqual([nextBrowser.effects.go, nextBrowser.effects.card, nextBrowser.effects.pay], [1, 1, 0]);
    assert.equal(f.request.priorFailure.failure_observed_at, originalFailureAt);
    const audit = await records(f.directory);
    assert.equal(audit.length, 9); assert.equal(audit[6].payload.priorFormDisposition_sha256, disposition.disposition_sha256);
    assert.equal(audit[5].payload.failure_observed_at, originalFailureAt);
    assert.ok(originalFailureAt < attempt.recorded_at);
    assert.equal(audit[8].payload.outcome, 'unknown');
  } finally { Date.now = actualNow; nextBrowser?.close(); }
}));

test('probe cancellation drains hidden inner-inspector DOM promise before runtime may release common lease', async () => ledgerFixture(async f => {
  const entered = deferred(), finishRead = deferred();
  const controller = new AbortController();
  const b = browser({ beforeEvaluate: async ({ fn }) => {
    if (fn.name === 'collectEnhancedCheckoutDom') { entered.resolve(); await finishRead.promise; }
  } });
  b.state.merchant = dom(summary, CHECKOUT);
  try {
    const running = probe(b, readonlyGrant(), controller.signal);
    await entered.promise; controller.abort();
    assert.equal((await running).code, 'cancelled');
    const readsAtCancellation = b.effects.evaluate;
    let drained = false;
    const settlement = port.waitForHostedGuestPreparationSettlement(b.context).then(() => { drained = true; });
    await eventLoopTurn(); assert.equal(drained, false);
    finishRead.resolve(); await settlement; await eventLoopTurn();
    assert.equal(drained, true);
    assert.equal(b.effects.evaluate, readsAtCancellation);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    assert.equal((await records(f.directory)).length, 0);
  } finally { finishRead.resolve(); await eventLoopTurn(); b.state.merchant.window.close(); b.close(); }
}));

test('readonly inspect then explicit saved-address visual acknowledgement binds only unchanged current document', async () => ledgerFixture(async f => {
  for (const checkout of [false, true]) {
    const b = browser(); if (checkout) b.state.merchant = dom(summary, CHECKOUT);
    const grant = readonlyGrant();
    try {
      const first = await probe(b, grant);
      assert.equal(first.status, 'observed', JSON.stringify(first)); assert.equal(first.address_present, true);
      assert.equal(first.destination_match, null); assert.equal(first.destination_binding_available, false);
      assert.equal(first.merchant_route, checkout ? 'enhanced_checkout' : 'cart');
      assert.match(first.document_ref, /^[0-9a-f-]{36}$/u);
      const next = confirmation(grant, first);
      const confirmed = await probe(b, next);
      assert.equal(confirmed.code, 'approved_saved_destination_bound', JSON.stringify(confirmed));
      assert.equal(confirmed.destination_match, true); assert.equal(confirmed.destination_binding_available, true);
      assert.equal(confirmed.document_ref, first.document_ref);
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
      assert.equal((await records(f.directory)).length, 0);
      for (const secret of [...ADDRESS, sha(ADDRESS.join('\n'))]) assert.ok(!json(first).includes(secret) && !json(confirmed).includes(secret));
      const reads = b.effects.evaluate;
      assert.equal((await probe(b, grant)).code, 'probe_job_replayed');
      assert.equal((await probe(b, next)).code, 'probe_job_replayed'); assert.equal(b.effects.evaluate, reads);
    } finally { if (checkout) b.state.merchant.window.close(); b.close(); }
  }
}));

test('readonly source-prefix diagnostics canonicalize exactly observed Poor (P), never infer unknown public grades', async () => {
  for (const [label, expected] of [['Poor (P)', 'P'], ['Poor', 'P'], ['P', 'P'], ['Poor (VG)', null], ['constructor', null]]) {
    const b = browser(); b.state.merchant = dom(summary.replace('Media: Mint', `Media: ${label}`), CHECKOUT);
    try {
      const result = await probe(b, readonlyGrant());
      assert.equal(result.status, 'observed'); assert.equal(result.structure.media_grade, expected);
      assert.equal(result.structure.sleeve_grade, 'Generic'); assert.equal(result.structure.item_title_present, true);
      assert.ok(!json(result).includes('Synthetic KC Single'));
    } finally { b.state.merchant.window.close(); b.close(); }
  }
});

test('distinct readonly authorization rejects extra transaction evidence, wrong scope, caller ack and unapproved source before DOM', async () => {
  for (const alter of [
    g => { g.request.priorFailure = { last_error_code: 'SESSION_EXPIRED' }; },
    g => { g.authorization.listing_id = 'other'; }, g => { g.authorization.quantity = 2; },
    g => { g.authorization.currency = 'EUR'; }, g => { g.authorization.approved_destination_digest = 'a'.repeat(64); },
    g => { g.request.destination_ack = { source: 'operator_visual_saved_destination_match' }; },
    g => { g.authorization.expires_at += 1; },
  ]) {
    const b = browser(), grant = readonlyGrant(); alter(grant);
    try { assert.equal((await probe(b, grant)).code, 'readonly_authorization_required'); assert.equal(b.effects.pages, 0); }
    finally { b.close(); }
  }
});

test('confirmation rejects changed address snapshot, replaced document, changed URL, different context, and unseen reference', async () => {
  for (const mode of ['address', 'document', 'url', 'context', 'reference']) {
    const b = browser(), grant = readonlyGrant(); b.state.merchant = dom(summary, CHECKOUT);
    let other;
    try {
      const first = await probe(b, grant), next = confirmation(grant, first);
      if (mode === 'address') b.state.merchant.window.document.querySelector('address span').textContent = 'Changed private destination';
      if (mode === 'document') b.state.merchant = dom(summary, CHECKOUT);
      if (mode === 'url') b.state.merchant = dom(cartMarkup, CART);
      if (mode === 'context') { other = browser(); }
      if (mode === 'reference') { next.request.document_ref = randomUUID(); next.authorization.destination_ack.document_ref = next.request.document_ref; }
      const result = await probe(other || b, next);
      assert.equal(result.status, 'blocked');
      assert.equal(result.code, mode === 'address' ? 'saved_destination_snapshot_changed' : ['context', 'reference'].includes(mode) ? 'probe_reference_unverified' : 'probe_document_changed');
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
      assert.ok(!json(result).includes('Changed private destination'));
    } finally { b.state.merchant.window.close(); b.close(); other?.close(); }
  }
});

test('confirmation requires genuine fresh private visual acknowledgement after reference and rejects expired references', async () => {
  for (const mode of ['missing_ack', 'wrong_source', 'too_early', 'stale_ack', 'expired_ref']) {
    const b = browser(), grant = readonlyGrant();
    const actualNow = Date.now;
    try {
      const first = await probe(b, grant), next = confirmation(grant, first);
      if (mode === 'missing_ack') delete next.authorization.destination_ack;
      if (mode === 'wrong_source') next.authorization.destination_ack.source = 'current_dom_address_present';
      if (mode === 'too_early') next.authorization.destination_ack.observed_at = grant.authorization.issued_at - 1;
      if (mode === 'stale_ack') { Date.now = () => actualNow() + 61000; }
      if (mode === 'expired_ref') { Date.now = () => actualNow() + 301000; next.authorization.destination_ack.observed_at = Date.now() / 1000; }
      const reads = b.effects.evaluate;
      assert.equal((await probe(b, next)).code, mode === 'expired_ref' ? 'probe_reference_expired' : 'readonly_authorization_required');
      assert.equal(b.effects.evaluate, reads);
    } finally { Date.now = actualNow; b.close(); }
  }
});

test('concurrent confirmations can consume reference only once and no address presence auto-approves', async () => {
  const b = browser(), grant = readonlyGrant();
  try {
    const first = await probe(b, grant), next = confirmation(grant, first);
    const results = await Promise.all([probe(b, next), probe(b, next)]);
    assert.equal(results.filter(value => value.code === 'approved_saved_destination_bound').length, 1);
    assert.equal(results.filter(value => value.code === 'probe_job_replayed').length, 1);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.close(); }
  const missing = browser({ cart: cartMarkup.replace(/<address>.*?<\/address>/u, '<div>No address</div>') }), g = readonlyGrant();
  try {
    const first = await probe(missing, g); assert.equal(first.address_present, false); assert.equal(first.destination_match, null);
    assert.equal((await probe(missing, confirmation(g, first))).code, 'saved_destination_unverified');
  } finally { missing.close(); }
});

test('address mutation during later provider reads cannot promote the earlier snapshot to an approved binding', async () => {
  let armed = false;
  const b = browser({ beforeEvaluate: ({ which, state }) => {
    if (armed && which === 'provider') state.merchant.window.document.querySelector('address span').textContent = 'Changed during confirmation';
  } });
  b.state.merchant = dom(summary, CHECKOUT);
  const grant = readonlyGrant();
  try {
    const first = await probe(b, grant); armed = true;
    const result = await probe(b, confirmation(grant, first));
    assert.equal(result.code, 'saved_destination_snapshot_changed');
    assert.ok(!json(result).includes('Changed during confirmation'));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.state.merchant.window.close(); b.close(); }
});

test('a new module boot cannot consume an opaque reference retained only in the previous process', async () => {
  const b = browser(), grant = readonlyGrant();
  try {
    const first = await probe(b, grant), next = confirmation(grant, first);
    const reloaded = await import('../scripts/discogs_hosted_guest_prepare.mjs?readonly-boot=' + randomUUID());
    const calls = b.effects.pages;
    const result = await reloaded.probeCurrentGuestPreparationEvidence(b.context, next.request, { authorization: next.authorization });
    assert.equal(result.code, 'probe_reference_unverified'); assert.equal(b.effects.pages, calls);
  } finally { b.close(); }
});

test('confirmation pending DOM read crossing either reference or visual-ack expiry never creates binding', async () => {
  for (const boundary of ['reference', 'ack']) {
    const actualNow = Date.now;
    let clock = actualNow(), confirming = false, advanced = false, boundaryExpiresAt;
    Date.now = () => clock;
    const b = browser({ beforeEvaluate: async () => {
      if (confirming && !advanced) {
        advanced = true;
        const pendingAt = clock;
        assert.ok(clock < boundaryExpiresAt, 'the boundary is still valid before the pending DOM read');
        await Promise.resolve(); // The request was valid when the pending read began.
        // Leave a real one-second scheduling margin before the production
        // timer, then cross only the clamped expiry, not the 1500ms read budget.
        clock += 1200;
        assert.ok(clock >= boundaryExpiresAt);
        assert.ok(clock < pendingAt + 1500);
      }
    } });
    const grant = readonlyGrant();
    try {
      const first = await probe(b, grant);
      assert.equal(first.status, 'observed');
      const referenceExpiresAt = clock + 300000;
      clock += boundary === 'reference' ? 299000 : 59000;
      const next = confirmation(grant, first);
      if (boundary === 'ack') next.authorization.destination_ack.observed_at -= 59;
      boundaryExpiresAt = boundary === 'reference' ? referenceExpiresAt : (next.authorization.destination_ack.observed_at + 60) * 1000;
      confirming = true;
      const result = await probe(b, next);
      assert.equal(advanced, true);
      assert.equal(result.status, 'blocked');
      assert.equal(result.code, 'probe_deadline_expired');
      assert.notEqual(result.destination_binding_available, true); assert.notEqual(result.destination_match, true);
      assert.equal((await probe(b, confirmation(grant, first))).status, 'blocked');
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { Date.now = actualNow; b.close(); }
  }
});

// Source-derived condition children from retained merchant bundle SHA256
// 1933d4028b07f0194e350adc6d4608ac1a9364344ab5b52c0a3d26532f7fb27a:
// DIV -> 'Media: ', mediaCondition, BR, 'Sleeve: ', sleeveCondition.
// Zero-width BR rectangles below are an offline model, not measured live DOM.
const sourceCondition = '<div class="_checkout-sell-item__condition_brkft_34">Media: Poor (P)<br>Sleeve: Generic</div>';
function withCondition(html) {
  return summary.replace(/<div class="_checkout-sell-item__condition_fixture">.*?<\/div>/u, html);
}
function modelZeroWidthBreaks(b) {
  const element = b.state.merchant.window.Element.prototype, original = element.getClientRects;
  element.getClientRects = function () { return this.tagName === 'BR' ? [{ width: 0, height: 18 }] : original.call(this); };
}

test('source-derived Media BR Sleeve structure reads Poor/Generic with modeled zero-width BR and no actions', async () => {
  const b = browser(); b.state.merchant = dom(withCondition(sourceCondition), CHECKOUT); modelZeroWidthBreaks(b);
  try {
    const result = await probe(b, readonlyGrant());
    assert.equal(result.status, 'observed');
    assert.equal(result.structure.media_grade, 'P'); assert.equal(result.structure.sleeve_grade, 'Generic');
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.state.merchant.window.close(); b.close(); }
});

test('hidden line breaks and hidden ancestors cannot create a visible condition separator', async () => {
  for (const condition of [sourceCondition.replace('<br>', '<br hidden>'), sourceCondition.replace('<br>', '<br aria-hidden="true">'),
    sourceCondition.replace('<br>', '<br style="display:none">'), sourceCondition.replace('<br>', '<span hidden><br></span>'),
    sourceCondition.replace('<div ', '<div hidden ')]) {
    const b = browser(); b.state.merchant = dom(withCondition(condition), CHECKOUT); modelZeroWidthBreaks(b);
    try {
      const result = await probe(b, readonlyGrant());
      assert.equal(result.structure.media_grade, null); assert.equal(result.structure.sleeve_grade, null);
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.state.merchant.window.close(); b.close(); }
  }
});

test('duplicate source-prefix condition nodes remain unverified even when one is hidden', async () => {
  for (const duplicate of [sourceCondition, sourceCondition.replace('<div ', '<div hidden ')]) {
    const b = browser(); b.state.merchant = dom(withCondition(sourceCondition + duplicate), CHECKOUT);
    try {
      const result = await probe(b, readonlyGrant());
      assert.equal(result.structure.media_grade, null); assert.equal(result.structure.sleeve_grade, null);
      assert.ok(result.missing_fields.includes('condition_continuity'));
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.state.merchant.window.close(); b.close(); }
  }
});

test('blank already-open detail page reports bounded readiness diagnostics and unknown identity/title', async () => {
  const b = browser({ detail: '<!doctype html><title>Discogs</title><nav>Navigation only</nav>' });
  b.state.merchant = dom(withCondition(sourceCondition), CHECKOUT);
  try {
    const result = await probe(b, readonlyGrant());
    assert.equal(result.status, 'observed');
    assert.deepEqual(result.detail_diagnostic, { status: 'incomplete', error_code: 'listing_main_unavailable',
      ready_state: 'complete', main_count: 0, release_link_count: 0, grade_count: 0 });
    assert.equal(result.observed.listing_detail_supported, false); assert.equal(result.observed.title_continuity, false);
    assert.equal(result.structure.media_grade, 'P'); assert.equal(result.structure.sleeve_grade, 'Generic');
    for (const secret of ['Navigation only', 'testids', 'hifi-', '/sell/item/', '/release/']) assert.ok(!json(result).includes(secret));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.state.merchant.window.close(); b.close(); }
});

test('unready detail and missing exact header remain unverified; readiness counts are capped without alternate selectors', async () => {
  for (const mode of ['loading', 'unknown_header', 'ambiguous_counts']) {
    const b = browser({ detail: mode === 'unknown_header' ? detailMarkup.replace('/release/3422100', '/release/3422100-unverified-slug') :
      mode === 'ambiguous_counts' ? detailMarkup.replace('</main>',
        '<a href="/release/3422100">Extra</a><a href="/release/3422100">Extra</a>' +
        '<span class="hifi-grade-chip-display">Extra</span><span class="hifi-grade-chip-display">Extra</span></main><main></main><main></main>') : detailMarkup });
    b.state.merchant = dom(withCondition(sourceCondition), CHECKOUT);
    if (mode === 'loading') Object.defineProperty(b.state.detail.window.document, 'readyState', { value: 'loading' });
    try {
      const result = await probe(b, readonlyGrant());
      assert.equal(result.status, 'observed'); assert.equal(result.observed.listing_detail_supported, false);
      assert.equal(result.observed.title_continuity, false);
      assert.equal(result.detail_diagnostic.error_code, mode === 'loading' ? 'listing_not_ready' : mode === 'unknown_header' ? 'listing_release_mismatch' : 'listing_main_unavailable');
      assert.equal(result.detail_diagnostic.ready_state, mode === 'loading' ? 'loading' : 'complete');
      if (mode === 'ambiguous_counts') {
        assert.equal(result.detail_diagnostic.main_count, 2);
        assert.equal(result.detail_diagnostic.release_link_count, 2); assert.equal(result.detail_diagnostic.grade_count, 2);
      }
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.state.merchant.window.close(); b.close(); }
  }
});

// Source-derived fRu title group from retained SHA1933…27a plus public labels
// observed by the parent. These are offline fixtures, not a live DOM capture.
const sourceCore = 'Get Down Tonight';
const sourceArtist = 'K.C. &amp; The Sunshine Band*';
const sourceFormat = ' (7&quot;, 45 RPM, Single, Stereo)';
function titleGroup(core, { artist = sourceArtist, format = sourceFormat } = {}) {
  return `<h3 class="_checkout-sell-item__title_brkft_27"><span>${artist}</span><span> - </span><span>${core}</span><span>${format}</span></h3>`;
}
function withTitle(group) { return summary.replace(/<h3 class="_checkout-sell-item__title_fixture">.*?<\/h3>/u, group); }
async function titleProbe(core, group) {
  const detail = detailMarkup.replace('>Synthetic KC Single</a>', `>${core}</a>`).replace('<span>Mint</span>', '<span>Poor</span>');
  const b = browser({ detail }); b.state.merchant = dom(withCondition(sourceCondition).replace(/<h3 class="_checkout-sell-item__title_fixture">.*?<\/h3>/u, group), CHECKOUT);
  try {
    // Isolate each source-title fixture's ephemeral job store; production keeps
    // its bounded per-process capacity and durable runtime budget unchanged.
    const isolated = await import('../scripts/discogs_hosted_guest_prepare.mjs?title-fixture=' + randomUUID());
    const grant = readonlyGrant();
    const result = await isolated.probeCurrentGuestPreparationEvidence(b.context, grant.request, { authorization: grant.authorization });
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    for (const secret of [core, 'K.C.', 'Sunshine', sourceFormat, sha(core)]) assert.ok(!json(result).includes(secret));
    return result;
  } finally { b.state.merchant.window.close(); b.close(); }
}

test('source-derived artist separator core-title and format spans compare only the semantic core to exact-release detail', async () => {
  const result = await titleProbe(sourceCore, titleGroup(sourceCore));
  assert.equal(result.observed.listing_detail_supported, true);
  assert.equal(result.observed.condition_continuity, true);
  assert.equal(result.observed.title_continuity, true);
  assert.equal(result.structure.item_title_present, true);
});

test('semantic core title uses only whitespace and NFC normalization, preserving case punctuation and parentheses', async () => {
  for (const [detailCore, checkoutCore, expected] of [
    ['Cafe\u0301 (Part II)', 'Café   (Part II)', true],
    ['Title (Live)', 'Title (Live)', true],
    ['Get Down Tonight', 'Get Down Tonight (Live)', false],
    ['Get Down Tonight (Live)', 'Get Down Tonight', false],
    ['Get Down Tonight', 'get down tonight', false],
    ['Get Down Tonight', 'Get Down Tonight!', false],
    ['Another Core (1975)', 'Another Core (1975)', true],
  ]) {
    const result = await titleProbe(detailCore, titleGroup(checkoutCore));
    assert.equal(result.observed.title_continuity, expected, JSON.stringify([detailCore, checkoutCore]));
  }
});

test('source-shaped multiple artist spans retain joining text inside each span and do not require a fixed artist name', async () => {
  const group = titleGroup(sourceCore, { artist: 'First Artist &amp; </span><span>Second Artist*' });
  const result = await titleProbe(sourceCore, group);
  assert.equal(result.observed.title_continuity, true);
});

test('unknown hidden nested duplicate separator core artist and format structures cannot qualify a semantic title', async () => {
  const group = titleGroup(sourceCore);
  const cases = [
    `<h3 class="_checkout-sell-item__title_brkft_27">${sourceCore}</h3>`,
    `<h3 class="_checkout-sell-item__title_brkft_27"><span>${sourceCore}</span></h3>`, // no-release description fallback
    titleGroup(sourceCore, { artist: '   ' }), titleGroup(sourceCore, { artist: '<em>Nested Artist</em>' }),
    titleGroup(sourceCore, { format: '' }), titleGroup(sourceCore, { format: '7&quot;, 45 RPM' }),
    titleGroup(sourceCore, { format: '(7&quot;, 45 RPM)' }), titleGroup(sourceCore, { format: ' (7&quot;, 45 RPM' }),
    titleGroup(sourceCore, { format: ' (<b>7&quot;</b>, 45 RPM)' }),
    group.replace('<span> - </span>', '<span>-</span>'),
    group.replace('<span> - </span>', '<span> - </span><span> - </span>'),
    group.replace(`<span>${sourceCore}</span>`, `<span>${sourceCore}</span><span>${sourceCore}</span>`),
    group.replace(`<span>${sourceCore}</span>`, `<span><em>${sourceCore}</em></span>`),
    group.replace(`<span>${sourceCore}</span>`, `<span hidden>${sourceCore}</span>`),
    group.replace(`<span>${sourceArtist}</span>`, `<span aria-hidden="true">${sourceArtist}</span>`),
    group.replace(`<span>${sourceFormat}</span>`, `<span style="display:none">${sourceFormat}</span>`),
    group.replace('</h3>', '<a>Unknown direct child</a></h3>'),
    group.replace('</h3>', 'Unknown direct text</h3>'),
    group + group.replace('<h3 ', '<h3 hidden '),
  ];
  for (const malformed of cases) {
    const result = await titleProbe(sourceCore, malformed);
    assert.equal(result.observed.title_continuity, false);
    assert.equal(result.structure.item_title_present, false);
    assert.ok(result.missing_fields.includes('title_continuity'));
  }
});

test('uncaptured cart format caption remains fail-closed without broad format stripping', async () => ledgerFixture(async f => {
  const cart = cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '>Fixture Artist - Synthetic KC Single (7&quot;, 45 RPM, Single)</a>');
  const b = browser({ cart });
  try {
    const result = await execute(f, b);
    assert.equal(result.code, 'visible_scope_evidence_missing');
    assert.ok(result.missing_fields.includes('title_continuity'));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    assert.equal((await records(f.directory)).length, 0);
  } finally { b.close(); }
}));

function cartCaptureGrant() {
  const grant = readonlyGrant(); grant.request.phase = 'capture_cart_structure';
  grant.authorization.mode = 'capture_cart_structure'; return grant;
}

test('separate capture mode returns only bounded exact-KC public title subtree and narrow grade tokens without address or values', async () => {
  const grade = '<span><b>Media:</b><span>Poor (P)</span><br><b>Sleeve:</b><span>Generic</span></span>';
  const cart = cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '><span>KC - </span><strong>Get Down Tonight</strong><small> (7&quot;, 45 RPM)</small></a>')
    .replace('<span>Media: Mint Sleeve: Generic</span>', grade);
  const b = browser({ cart }), grant = cartCaptureGrant();
  const address = b.state.merchant.window.document.querySelector('address');
  Object.defineProperty(address, 'textContent', { get() { throw new Error('ADDRESS_MUST_NOT_BE_READ'); } });
  const input = b.state.merchant.window.document.createElement('input'); input.value = 'PRIVATE_FIELD_VALUE';
  Object.defineProperty(input, 'value', { get() { throw new Error('VALUE_MUST_NOT_BE_READ'); } });
  b.state.merchant.window.document.body.append(input);
  try {
    const result = await probe(b, grant);
    assert.equal(result.code, 'cart_structure_captured', JSON.stringify(result));
    const captured = result.cart_structure;
    assert.equal(captured.row_count, 1); assert.equal(captured.listing_href, '/sell/item/1366950910');
    assert.equal(captured.title_link.tag, 'A'); assert.equal(captured.title_link.text, 'KC - Get Down Tonight (7", 45 RPM)');
    assert.deepEqual(Array.from(captured.title_link.nodes, node => node.tag), ['SPAN', '#text', 'STRONG', '#text', 'SMALL', '#text']);
    assert.ok(captured.grade_nodes.some(node => node.label === 'Media')); assert.ok(captured.grade_nodes.some(node => node.grade === 'P'));
    assert.ok(captured.grade_nodes.some(node => node.label === 'Sleeve')); assert.ok(captured.grade_nodes.some(node => node.grade === 'Generic'));
    for (const secret of ['Fixture Street', 'PRIVATE_FIELD_VALUE', 'PRIVATE=']) assert.ok(!json(result).includes(secret));
    for (const amount of ['$0.10', '$4.95', '$5.05']) assert.ok(!json(captured).includes(amount));
    for (const node of [...captured.title_link.nodes, ...captured.grade_nodes]) {
      assert.ok(node.path.length <= 4 && node.path.every(index => Number.isInteger(index) && index >= 0 && index <= 31));
      assert.ok(node.text.length <= 160); assert.ok(!Object.hasOwn(node, 'attributes'));
    }
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    const calls = b.effects.evaluate; assert.equal((await probe(b, grant)).code, 'probe_job_replayed'); assert.equal(b.effects.evaluate, calls);
  } finally { b.close(); }
});

test('layout capture batches exact collector gates, form associations and public summary shapes without invoking the text-reading collector', async () => {
  const b = browser({ beforeEvaluate: ({ fn }) => { assert.notEqual(fn.name, 'collectCartDom'); } });
  try {
    const result = await probe(b, cartCaptureGrant()), layout = result.cart_layout;
    assert.equal(result.code, 'cart_structure_captured');
    assert.equal(layout.schema, 'discogs_cart_layout_v1'); assert.equal(layout.truncated, false);
    assert.equal(layout.collector_gate.code, 'not_run_private_text_guard');
    for (const field of ['form_count', 'order_container_count', 'summary_count', 'address_wrapper_count', 'address_count',
      'go_count', 'shipping_select_count', 'selected_count', 'option_count']) assert.equal(layout.collector_gate[field], 1, field);
    assert.equal(layout.collector_gate.selected_index, 0);
    for (const field of ['form_contains_row', 'container_contains_row', 'container_in_form', 'go_form_present', 'go_form_legacy',
      'go_form_contains_row', 'go_in_container', 'shipping_form_present', 'shipping_form_same_as_go', 'shipping_in_container']) assert.equal(layout.collector_gate[field], true, field);
    assert.ok(layout.ancestry.some(node => node.tag === 'FORM' && node.hints.includes('legacy')));
    assert.ok(layout.ancestry.some(node => node.hints.includes('cart_order_prefix')));
    const table = layout.tables.find(node => node.hints.includes('order_summary'));
    assert.equal(table.row_count, 3); assert.equal(table.rows.length, 3);
    assert.deepEqual(Array.from(table.rows, row => Array.from(row.cells, cell => cell.public_text)),
      [['Subtotal', '$0.10 USD'], ['Shipping', '$4.95 USD'], ['Total', '$5.05 USD']]);
    assert.ok(layout.address_shapes.some(node => node.kind === 'address' && node.tag === 'ADDRESS'));
    assert.ok(layout.address_shapes.some(node => node.kind === 'shipping_wrapper' && node.hints.includes('shipping_address_prefix')));
    assert.ok(!json(result).includes('Synthetic Buyer')); assert.ok(!json(result).includes('Fixture Street'));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.close(); }
});

test('layout preserves form reparenting and unknown table/address shape diagnostics without guessing selectors', async () => {
  const cart = cartMarkup.replace('<form class="legacy">', '<form class="unknown_private_class">')
    .replace('class="order_container"', 'class="unknown_private_container"')
    .replace('class="order_summary"', 'class="unknown_private_summary"')
    .replaceAll('class="order_subtotal"', '').replaceAll('class="order_total"', '')
    .replaceAll('class="order_summary_value"', '').replaceAll('class="order_shipping_amount order_summary_value"', '')
    .replace('id="shipping_address_fixture"', 'id="private_address_987654"')
    .replace('<address>', '<h2>Shipping Address</h2><section>').replace('</address>', '</section>');
  const b = browser({ cart });
  try {
    const result = await probe(b, cartCaptureGrant()), layout = result.cart_layout;
    assert.equal(result.code, 'cart_structure_captured');
    for (const field of ['form_count', 'order_container_count', 'summary_count', 'address_wrapper_count', 'address_count']) assert.equal(layout.collector_gate[field], 0, field);
    assert.equal(layout.collector_gate.go_form_present, true); assert.equal(layout.collector_gate.go_form_legacy, false);
    assert.equal(layout.collector_gate.shipping_form_same_as_go, true);
    assert.ok(layout.address_shapes.some(node => node.kind === 'shipping_heading_following' && node.tag === 'SECTION'));
    assert.ok(layout.tables.every(table => table.rows.every(row => row.cells.every(cell => cell.public_text === null))));
    for (const secret of ['unknown_private_class', 'unknown_private_container', 'unknown_private_summary', '987654', ...ADDRESS]) assert.ok(!json(result).includes(secret));
  } finally { b.close(); }
  const split = browser();
  try {
    const document = split.state.merchant.window.document;
    document.body.append(document.querySelector('button'), document.querySelector('.order_shipping_fields'));
    const result = await probe(split, cartCaptureGrant()), gate = result.cart_layout.collector_gate;
    assert.equal(gate.go_count, 1); assert.equal(gate.go_form_present, false); assert.equal(gate.go_form_contains_row, false);
    assert.equal(gate.shipping_select_count, 1); assert.equal(gate.shipping_form_present, false); assert.equal(gate.shipping_form_same_as_go, false);
  } finally { split.close(); }
});

test('layout never reads address or field values; mixed, nested private, hidden, and non-USD summary cells emit no text', async () => {
  const b = browser();
  const document = b.state.merchant.window.document;
  const address = document.querySelector('address'), wrapper = address.parentElement;
  const privateGet = () => { throw new Error('PRIVATE_TEXT_READ'); };
  for (const node of [address, wrapper, ...address.querySelectorAll('*')]) {
    Object.defineProperty(node, 'textContent', { get: privateGet });
    Object.defineProperty(node, 'innerText', { get: privateGet });
  }
  for (const node of [...address.querySelectorAll('span')].flatMap(node => [...node.childNodes])) Object.defineProperty(node, 'nodeValue', { get: privateGet });
  const input = document.createElement('input'); Object.defineProperty(input, 'value', { get: privateGet }); wrapper.append(input);
  const summary = document.querySelector('table.order_summary');
  summary.insertAdjacentHTML('beforeend', '<tr><td>Total PRIVATE_ACCOUNT</td><td>$5.05</td></tr><tr><td>Other PRIVATE_MESSAGE</td><td>$5.05 EUR</td></tr><tr><td>Subtotal</td><td hidden>$0.10 USD</td></tr>');
  const nested = document.createElement('tr'); nested.innerHTML = '<td>Total</td><td class="order_summary_value">$5.05 USD</td>';
  nested.lastElementChild.append(address); summary.append(nested);
  try {
    const result = await probe(b, cartCaptureGrant()); assert.equal(result.code, 'cart_structure_captured', json(result));
    const rows = result.cart_layout.tables.find(table => table.hints.includes('order_summary')).rows;
    assert.deepEqual(Array.from(rows.slice(3, 5), row => Array.from(row.cells, cell => cell.public_text)), [[null, null], [null, null]]);
    assert.equal(rows[5].cells[1].public_text, null); assert.equal(rows[6].cells[1].public_text, null);
    for (const secret of ['PRIVATE_ACCOUNT', 'PRIVATE_MESSAGE', ...ADDRESS.filter(line => line !== 'US')]) assert.ok(!json(result).includes(secret));
    assert.ok(!json(result).includes('"US"'));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.close(); }
});

test('layout caps all structural arrays/counts and never returns attributes, raw class names, or unknown text', async () => {
  const b = browser(), document = b.state.merchant.window.document;
  const form = document.querySelector('form');
  for (let i = 0; i < 40; i++) {
    const table = document.createElement('table'); table.innerHTML = '<tr><td>PRIVATE_UNKNOWN</td></tr>'.repeat(40);
    form.append(table);
    const address = document.createElement('address'); address.id = 'PRIVATE_ID_' + i;
    address.innerHTML = '<span>PRIVATE_ADDRESS</span>'.repeat(20); form.append(address);
  }
  const summary = document.querySelector('table.order_summary');
  summary.insertAdjacentHTML('beforeend', '<tr>' + '<td><span></span></td>'.repeat(40) + '</tr>'.repeat(1));
  try {
    const result = await probe(b, cartCaptureGrant()), layout = result.cart_layout;
    assert.equal(result.code, 'cart_structure_captured'); assert.equal(layout.truncated, true);
    assert.equal(layout.collector_gate.address_count, 32); assert.ok(layout.tables.length <= 4); assert.ok(layout.address_shapes.length <= 6);
    for (const table of layout.tables) {
      assert.ok(table.row_count <= 32 && table.rows.length <= 8);
      for (const row of table.rows) { assert.ok(row.cell_count <= 32 && row.cells.length <= 4); for (const cell of row.cells) assert.ok(cell.child_types.length <= 16); }
    }
    for (const shape of layout.address_shapes) { assert.ok(shape.children.length <= 8 && shape.child_types.length <= 16); for (const child of shape.children) assert.ok(child.child_types.length <= 16); }
    assert.ok(!json(result).includes('PRIVATE_')); assert.ok(!json(result).includes('attributes'));
  } finally { b.close(); }
});

test('observed Your Shipping Address plain labels protect following shape and fixed summary survives form reparenting', async () => {
  for (const label of ['<h2>Your Shipping Address</h2>', '<strong>Your Shipping Address</strong>', '<b>Your Shipping Address</b>',
    '<p><strong>Your Shipping Address</strong></p>', '<div><b>Your Shipping Address</b></div>']) {
    const b = browser(), document = b.state.merchant.window.document;
    const summary = document.querySelector('table.order_summary'); document.body.append(summary);
    document.querySelector('[id^="shipping_address_"]').remove();
    const wrapper = document.createElement('div'); wrapper.innerHTML = label + '<section><strong>PRIVATE_ADDRESS_TEXT</strong><input></section>';
    document.body.append(wrapper);
    const address = wrapper.querySelector('section'), privateGet = () => { throw new Error('PRIVATE_TEXT_READ'); };
    for (const node of [address, address.querySelector('strong')]) {
      Object.defineProperty(node, 'textContent', { get: privateGet }); Object.defineProperty(node, 'innerText', { get: privateGet });
    }
    Object.defineProperty(address.querySelector('strong').firstChild, 'nodeValue', { get: privateGet });
    Object.defineProperty(address.querySelector('input'), 'value', { get: privateGet });
    try {
      const result = await probe(b, cartCaptureGrant()), layout = result.cart_layout;
      assert.equal(result.code, 'cart_structure_captured', label);
      assert.equal(layout.collector_gate.summary_count, 0); assert.equal(layout.collector_gate.address_count, 0);
      assert.ok(layout.tables[0].hints.includes('order_summary'));
      assert.equal(layout.tables[0].rows[2].cells[1].public_text, '$5.05 USD');
      assert.ok(layout.address_shapes.some(node => node.kind === 'shipping_heading_following' && node.tag === 'SECTION'));
      assert.ok(!json(result).includes('PRIVATE_ADDRESS_TEXT')); assert.ok(!json(result).includes('Your Shipping Address'));
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.close(); }
  }
});

test('unsupported grade parent is omitted without dropping useful public title or leaking sibling messages', async () => {
  const cart = cartMarkup.replace('<span>Media: Mint Sleeve: Generic</span>',
    '<div><span>Media:</span><span>Poor (P)</span><span>Sleeve:</span><span>Generic</span><span>PRIVATE_SELLER_MESSAGE</span></div>');
  const b = browser({ cart });
  try {
    const result = await probe(b, cartCaptureGrant());
    assert.equal(result.code, 'cart_structure_captured'); assert.equal(result.cart_structure.title_link.text, 'Fixture Artist - Synthetic KC Single (7", Single)');
    assert.equal(result.cart_structure.grade_nodes.length, 0); assert.ok(!json(result).includes('PRIVATE_SELLER_MESSAGE'));
  } finally { b.close(); }
});

test('capture authority cannot cross-use ordinary inspect or confirmation grants and admits no caller selectors', async () => {
  for (const mode of ['missing_mode', 'inspect_mode', 'caller_selector', 'caller_url', 'destination_ack']) {
    const b = browser(), grant = cartCaptureGrant();
    if (mode === 'missing_mode') delete grant.authorization.mode;
    if (mode === 'inspect_mode') grant.request.phase = 'inspect';
    if (mode === 'caller_selector') grant.request.selector = 'body';
    if (mode === 'caller_url') grant.request.url = CART;
    if (mode === 'destination_ack') grant.authorization.destination_ack = { document_ref: randomUUID() };
    try { assert.equal((await probe(b, grant)).code, 'readonly_authorization_required'); assert.equal(b.effects.pages, 0); }
    finally { b.close(); }
  }
});

test('ambiguous off-target hidden unsafe oversized or deeply nested title structure fails without partial capture', async () => {
  for (const cart of [
    cartMarkup.replace('/sell/item/1366950910', '/sell/item/1366950910?secret=PRIVATE'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', ' hidden>Synthetic KC Single</a>'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '><span hidden>PRIVATE_HIDDEN</span>Public</a>'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '><input value="PRIVATE_INPUT"></a>'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '><script>PRIVATE_SCRIPT</script>Public</a>'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '><span><span><span><span><span>Too deep</span></span></span></span></span></a>'),
    cartMarkup.replace('>Fixture Artist - Synthetic KC Single (7&quot;, Single)</a>', '>' + 'X'.repeat(301) + '</a>'),
    cartMarkup.replace('</td></tr>', '</td></tr><tr class="order_row" hidden><td>Other</td></tr>'),
  ]) {
    const b = browser({ cart });
    try {
      const result = await probe(b, cartCaptureGrant()); assert.equal(result.code, 'cart_capture_unverified');
      assert.ok(!Object.hasOwn(result, 'cart_structure')); assert.ok(!json(result).includes('PRIVATE'));
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.close(); }
  }
});

test('capture rejects a changed cart document and access restrictions without navigation or fields', async () => {
  let captured = false;
  const b = browser({ beforeEvaluate: ({ fn, state }) => {
    if (fn.name === 'collectHostedCartStructureDom') captured = true;
    else if (captured && fn.toString().includes('document === expected')) state.merchant = dom(cartMarkup, CART);
  } });
  try { assert.equal((await probe(b, cartCaptureGrant())).code, 'probe_document_changed'); assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]); }
  finally { b.state.merchant.window.close(); b.close(); }
  const blocked = browser({ cart: cartMarkup.replace('<title>Cart</title>', '<title>Security check</title>') });
  try { assert.equal((await probe(blocked, cartCaptureGrant())).code, 'access_restricted'); assert.deepEqual([blocked.effects.go, blocked.effects.card], [0, 0]); }
  finally { blocked.close(); }
});

// Literal sanitized real hosted capture 1e05b67; CI never reads /tmp captures.
const ACTUAL_KC_CART_CAPTION = 'K.C. & The Sunshine Band* - Get Down Tonight (7", Single)';
test('captured sole-text cart caption parses against a fresh literal core and retains full-caption digest', () => {
  const parsed = port.parseCapturedCartCaption(ACTUAL_KC_CART_CAPTION, 'Get Down Tonight');
  assert.equal(parsed.core, 'Get Down Tonight'); assert.equal(parsed.artist, 'K.C. & The Sunshine Band*');
  assert.deepEqual(parsed.formats, ['7"', 'Single']); assert.equal(parsed.full_caption_digest, sha(ACTUAL_KC_CART_CAPTION));
  assert.equal(port.checkoutCaptionMatchesCart(parsed, parsed.artist, ['7"', '45 RPM', 'Single', 'Stereo']), true);
  assert.equal(port.parseCapturedCartCaption('Artist - Café (Live) (7", Single)', 'Cafe\u0301 (Live)').core, 'Café (Live)');
  for (const caption of [
    'Artist - Get Down Tonight (Live) (7", Single)', 'Artist - get down tonight (7", Single)',
    'Get Down Tonight Ensemble - Get Down Tonight (7", Single)', 'Artist - Wrong Core (7", Single)',
    'Artist - Get Down Tonight (Single, 7")', 'Artist - Get Down Tonight (7", Single, Single)',
    'Artist - Get Down Tonight (7", Reissue)', 'Artist - Get Down Tonight (7",45 RPM,Single)',
  ]) assert.equal(port.parseCapturedCartCaption(caption, 'Get Down Tonight'), null);
  for (const [artist, formats] of [['Different Artist', ['7"', 'Single']], [parsed.artist, ['Single', '7"']],
    [parsed.artist, ['7"', 'Single', 'Single']], [parsed.artist, ['7"', 'Reissue', 'Single']]]) {
    assert.equal(port.checkoutCaptionMatchesCart(parsed, artist, formats), false);
  }
});

async function preflightFixture(run, config = {}, seed) {
  await ledgerFixture(async f => {
    if (seed) await seed(f);
    const b = browser(config), isolated = await import('../scripts/discogs_hosted_guest_prepare.mjs?preflight-fixture=' + randomUUID());
    const grant = readonlyGrant();
    const callProbe = g => isolated.probeCurrentGuestPreparationEvidence(b.context, g.request, { authorization: g.authorization });
    try {
      const first = await callProbe(grant);
      assert.equal(first.status, 'observed', JSON.stringify(first));
      const ack = confirmation(grant, first), confirmed = await callProbe(ack);
      assert.equal(confirmed.code, 'approved_saved_destination_bound', JSON.stringify(confirmed));
      const state = await operator.inspectGuestCheckoutLedger({ directory: f.directory });
      const request = { schema: 'discogs_guest_checkout_preflight_v1', job_id: grant.request.job_id,
        phase: 'navigate_checkout_preflight', document_ref: first.document_ref };
      const authorization = { ...ack.authorization, schema: 'discogs_guest_preflight_authorization_v1',
        mode: 'cart_checkout_preflight', expected_ledger_head: state.current_head };
      const execute = (signal, auth = authorization, req = request) => isolated.createHostedGuestPreflightFixture({
        context: b.context, directory: f.directory, authorization: auth, signal })(req);
      await run({ f, b, isolated, grant, request, authorization, execute });
    } finally { b.close(); }
  });
}

test('separate confirmed cart preflight navigates once with unknown cart grades, fully verifies checkout, and never claims or initializes', async () => preflightFixture(async ({ f, b, execute }) => {
  b.frame.$ = () => { throw new Error('FORBIDDEN_CARD_ACCESS'); };
  const result = await execute();
  assert.equal(result.code, 'cart_checkout_preflight_observed', JSON.stringify(result));
  assert.equal(result.cart_conditions_verified, false); assert.equal(result.checkout_conditions_verified, true);
  assert.equal(result.normal_cart_transition_verified, true); assert.equal(result.initialization_allowed, false);
  assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal((await records(f.directory)).length, 0);
  assert.equal((await execute()).code, 'probe_job_replayed');
}));

test('preflight refuses changed exact identity price destination caption or ledger before Go', async () => {
  for (const mode of ['listing', 'price', 'destination', 'caption', 'head']) await preflightFixture(async ({ f, b, execute, authorization }) => {
    const document = b.state.merchant.window.document;
    if (mode === 'listing') document.querySelector('a.item_link').setAttribute('href', '/sell/item/999');
    if (mode === 'price') document.querySelector('tr.order_total td.order_summary_value').textContent = '$5.06 USD';
    if (mode === 'destination') document.querySelector('address span').textContent = 'Other private address';
    if (mode === 'caption') document.querySelector('a.item_link').textContent = 'Other Artist - Synthetic KC Single (7", Reissue)';
    const result = await execute(null, mode === 'head' ? { ...authorization, expected_ledger_head: 'f'.repeat(64) } : authorization);
    assert.equal(result.status, 'blocked'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]);
    assert.equal((await records(f.directory)).length, 0); assert.ok(!json(result).includes('Other private address'));
  });
});

test('fresh checkout wrong core artist format conditions amount and changed detail cannot produce preflight success or any init', async () => {
  for (const config of [
    { checkout: summary.replace('<span>Synthetic KC Single</span>', '<span>Wrong Core</span>') },
    { checkout: summary.replace('<span>Fixture Artist</span>', '<span>Other Artist</span>') },
    { checkout: summary.replace('7&quot;, 45 RPM, Single', '7&quot;, Reissue, Single') },
    { checkout: summary.replace('Media: Mint', 'Media: Poor (P)') },
    { checkout: summary.replace('$5.05 USD', '$5.06 USD') },
    { afterGo: state => { state.detail.window.document.querySelector('a.hifi-heading-link-large').textContent = 'Changed Core'; } },
  ]) await preflightFixture(async ({ f, b, execute }) => {
    const result = await execute(); assert.equal(result.status, 'blocked');
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal((await records(f.directory)).length, 0);
  }, config);
});

test('pending or unknown ledger is not a navigation grant even when its exact head is supplied', async () => preflightFixture(async ({ b, execute }) => {
  const before = b.effects.pages;
  assert.equal((await execute()).code, 'preflight_ledger_unverified');
  assert.equal(b.effects.pages, before); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]);
}, {}, async f => {
  const actor = browser(); try { await execute(f, actor); } finally { actor.close(); }
}));

test('preflight rejects fabricated historical fields and readonly auth without a scoped navigation grant', async () => preflightFixture(async ({ b, execute, authorization, request }) => {
  assert.equal((await execute(null, { ...authorization, schema: 'discogs_guest_readonly_authorization_v1' })).code, 'preflight_authorization_required');
  assert.equal((await execute(null, authorization, { ...request, priorFailure: { last_error_code: 'SESSION_EXPIRED' } })).code, 'preflight_authorization_required');
  assert.equal(b.effects.go, 0);
}));

test('semantic cart address text/BR flow normalizes privately like source-derived checkout lines without approving unknown markup', async () => {
  for (const markup of [ADDRESS.join('<br>'), ADDRESS.map(value => `<span>${value}</span>`).join('')]) {
    await preflightFixture(async ({ b, execute }) => {
      const result = await execute(); assert.equal(result.code, 'cart_checkout_preflight_observed', JSON.stringify(result));
      assert.deepEqual([b.effects.go, b.effects.card], [1, 0]);
      for (const value of ADDRESS) assert.ok(!json(result).includes(value));
    }, { cart: cartMarkup.replace(`<address>${addressMarkup}</address>`, `<address>${markup}</address>`) });
  }
  for (const markup of [ADDRESS.join('<br hidden>'), '<input value="PRIVATE">' + ADDRESS.join('<br>'), ADDRESS.slice(0, 2).join('<br>')]) {
    const b = browser({ cart: cartMarkup.replace(`<address>${addressMarkup}</address>`, `<address>${markup}</address>`) });
    try { const result = await probe(b, readonlyGrant()); assert.equal(result.address_present, false); assert.equal(b.effects.go, 0); }
    finally { b.close(); }
  }
});

test('actual hosted capture caption anchors the synthetic end-to-end preflight with Poor/Generic and ordered richer checkout formats', async () => {
  const cart = cartMarkup.replace('Fixture Artist - Synthetic KC Single (7&quot;, Single)', ACTUAL_KC_CART_CAPTION);
  const detail = detailMarkup.replace('>Synthetic KC Single</a>', '>Get Down Tonight</a>').replace('<span>Mint</span>', '<span>Poor</span>');
  const checkout = withCondition(sourceCondition).replace(/<h3 class="_checkout-sell-item__title_fixture">.*?<\/h3>/u, titleGroup('Get Down Tonight'));
  await preflightFixture(async ({ b, execute, f }) => {
    const result = await execute(); assert.equal(result.code, 'cart_checkout_preflight_observed', JSON.stringify(result));
    assert.equal(result.cart_conditions_verified, false); assert.equal(result.checkout_conditions_verified, true);
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal((await records(f.directory)).length, 0);
  }, { cart, detail, checkout });
});

test('checkout qualification state changed during final checks blocks observation and never initializes', async () => {
  for (const mode of ['condition', 'destination', 'total']) {
    let checkoutReads = 0;
    await preflightFixture(async ({ b, execute, f }) => {
      const result = await execute(); assert.equal(result.status, 'blocked');
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal((await records(f.directory)).length, 0);
    }, { beforeEvaluate: ({ fn, arg, state }) => {
      if (fn.name === 'collectHostedGuestDom' && arg?.stage === 'checkout' && ++checkoutReads === 2) {
        const document = state.merchant.window.document;
        if (mode === 'condition') document.querySelector('._checkout-sell-item__condition_fixture').textContent = 'Media: Poor (P) Sleeve: Generic';
        if (mode === 'destination') document.querySelector('address span').textContent = 'Other destination';
        if (mode === 'total') document.querySelector('._checkout-summary__total--color-red_fixture dd').textContent = '$5.06 USD';
      }
    } });
  }
});

test('cancelled dispatched preflight navigation holds common settlement and never proceeds to checkout reads or initialization', async () => {
  const entered = deferred(), finish = deferred();
  await preflightFixture(async ({ b, execute, isolated }) => {
    const controller = new AbortController(), running = execute(controller.signal); await entered.promise; controller.abort();
    const result = await running; assert.equal(result.code, 'cancelled');
    const before = b.effects.evaluate; let drained = false;
    const settlement = isolated.waitForHostedGuestPreparationSettlement(b.context).then(() => { drained = true; });
    await eventLoopTurn(); assert.equal(drained, false);
    finish.resolve(); await settlement; await eventLoopTurn();
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal(b.effects.evaluate, before);
  }, { beforeGo: async () => { entered.resolve(); await finish.promise; } });
});

test('observed selected Economy label with explicit $4.95 suffix is accepted only with independent unchanged USD quote', async () => {
  const cart = cartMarkup.replace('<option selected>Economy - Media Mail</option>', '<option selected>Economy - Media Mail - $4.95</option>');
  await preflightFixture(async ({ b, execute }) => {
    assert.equal((await execute()).code, 'cart_checkout_preflight_observed'); assert.deepEqual([b.effects.go, b.effects.card], [1, 0]);
  }, { cart });
  for (const changed of [cart.replace('order_shipping_amount order_summary_value">$4.95 USD', 'order_shipping_amount order_summary_value">$4.96 USD'),
    cart.replace('Economy - Media Mail - $4.95</option>', 'Economy - Media Mail - $4.96</option>')]) {
    await preflightFixture(async ({ b, execute }) => { assert.equal((await execute()).status, 'blocked'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]); }, { cart: changed });
  }
});

test('a selected label changing between accepted bare and suffixed forms during preflight blocks before Go', async () => {
  let reads = 0, armed = false;
  await preflightFixture(async ({ b, execute }) => {
    armed = true;
    const result = await execute(); assert.equal(result.code, 'cart_document_changed'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]);
  }, { beforeEvaluate: ({ fn, state }) => {
    if (armed && fn.name === 'collectCartDom' && ++reads === 2) state.merchant.window.document.querySelector('option').textContent = 'Economy - Media Mail - $4.95';
  } });
});

test('shipping option shape requires exactly one selected total and a matching bounded selected index', async () => {
  const multiple = cartMarkup.replace('name="shipping_method">', 'name="shipping_method" multiple>')
    .replace('</option>', '</option><option selected>Unknown service</option>');
  await preflightFixture(async ({ b, execute }) => {
    assert.equal((await execute()).code, 'cart_scope_unverified'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]);
  }, { cart: multiple });
  for (const mode of ['wrong_index', 'wrong_count', 'hidden', 'two_selectors']) await preflightFixture(async ({ b, execute }) => {
    const evaluate = b.page.evaluate;
    b.page.evaluate = async (fn, arg) => {
      const snapshot = await evaluate(fn, arg);
      if (fn.name === 'collectCartDom') {
        const methods = snapshot.shipping_methods;
        if (mode === 'wrong_index') { methods.options.push({ label: 'Unknown', selected: false, disabled: false }); methods.option_count = 2; methods.selected_index = 1; }
        if (mode === 'wrong_count') methods.option_count = 2;
        if (mode === 'hidden') methods.visible = false;
        if (mode === 'two_selectors') methods.selector_count = 2;
      }
      return snapshot;
    };
    assert.equal((await execute()).code, 'cart_scope_unverified'); assert.deepEqual([b.effects.go, b.effects.card], [0, 0]);
  });
});

// Structure comes from redacted hosted layout 1ea2be8 (nonlegacy owner form,
// prefix DIV/header H2/nested content DIVs; zero ADDRESS). Private line values
// and deeper line/BR semantics here are SYNTHETIC normalization fixtures.
const nestedPrivateCart = cartMarkup.replace('<form class="legacy">', '<form>')
  .replace(`<address>${addressMarkup}</address>`, `<div><h2>Your Shipping Address</h2></div><div><div>${ADDRESS.join('<br>')}</div></div>`);

test('synthetic complete preflight follows captured nonlegacy form and DIV summary/address layout without initialization', async () => {
  const cart = nestedPrivateCart.replace('<a class="edit_shipping_address">Edit</a>', '<a>Change your address</a>')
    .replace('<table class="order_summary">', '<div class="order_summary"><table>')
    .replace('</table>\n<div class="order_shipping_fields">', '</table></div>\n<div class="order_shipping_fields">');
  await preflightFixture(async ({ b, execute, f }) => {
    const result = await execute(); assert.equal(result.code, 'cart_checkout_preflight_observed', json(result));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [1, 0, 0]); assert.equal((await records(f.directory)).length, 0);
    assert.equal(result.destination_match, true); assert.equal(result.initialization_allowed, false);
  }, { cart });
});

test('V2 unknown raw address flag cannot authorize Go without the independent current private binding', async () => {
  for (const mode of ['unknown_private_address', 'fabricated_missing_false', 'wrong_control_kind']) {
    await preflightFixture(async ({ b, execute }) => {
      if (mode === 'unknown_private_address') b.state.merchant.window.document.querySelector('[id^="shipping_address_"]').innerHTML = '<section>UNKNOWN_PRIVATE_ADDRESS</section>';
      else {
        const evaluate = b.page.evaluate;
        b.page.evaluate = async (fn, arg) => {
          const snapshot = await evaluate(fn, arg);
          if (fn.name === 'collectCartDom') {
            if (mode === 'fabricated_missing_false') snapshot.missing_address = false;
            else snapshot.checkout_control_kind = 'v1_order_submission';
          }
          return snapshot;
        };
      }
      const result = await execute(); assert.equal(result.status, 'blocked', mode);
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
      assert.ok(!json(result).includes('UNKNOWN_PRIVATE_ADDRESS'));
    }, { cart: nestedPrivateCart });
  }
});

test('captured nonlegacy owner form and nested prefix DIV address normalize synthetic private lines and ignore hidden editor wrappers', async () => {
  const b = browser({ cart: nestedPrivateCart });
  const isolated = await import('../scripts/discogs_hosted_guest_prepare.mjs?nonlegacy-probe=' + randomUUID());
  const probeCurrent = grant => isolated.probeCurrentGuestPreparationEvidence(b.context, grant.request, { authorization: grant.authorization });
  const document = b.state.merchant.window.document;
  for (let i = 0; i < 2; i++) {
    const editor = document.createElement('div'); editor.id = 'shipping_address_hidden_' + i; editor.hidden = true;
    editor.innerHTML = '<form><input value="PRIVATE_EDITOR"><div>PRIVATE_EDITOR</div></form>';
    Object.defineProperty(editor, 'textContent', { get() { throw new Error('HIDDEN_EDITOR_READ'); } });
    Object.defineProperty(editor.querySelector('input'), 'value', { get() { throw new Error('HIDDEN_VALUE_READ'); } });
    document.body.append(editor);
  }
  try {
    const dom = await b.page.evaluate(port.collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: sha(ADDRESS.join('\n')) });
    assert.equal(document.querySelectorAll('form.legacy').length, 0); assert.equal(document.querySelectorAll('address').length, 0);
    assert.equal(dom.go, true); assert.equal(dom.address_container_count, 1); assert.equal(dom.address_line_count, 4);
    assert.equal(dom.address_present, true); assert.equal(dom.destination, true);
    const grant = readonlyGrant(), first = await probeCurrent(grant), bound = await probeCurrent(confirmation(grant, first));
    assert.equal(first.address_present, true); assert.equal(bound.code, 'approved_saved_destination_bound');
    for (const secret of [...ADDRESS.filter(value => value !== 'US'), 'PRIVATE_EDITOR', sha(ADDRESS.join('\n'))]) assert.ok(!json(bound).includes(secret));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
  } finally { b.close(); }
});

test('supplemental cart binding rejects cross-form controls, detached summary, ambiguous container and multiple visible address wrappers', async () => {
  for (const mode of ['go_form', 'shipping_form', 'summary_outside', 'duplicate_container', 'duplicate_address', 'address_outside']) {
    const b = browser({ cart: nestedPrivateCart }), document = b.state.merchant.window.document;
    const other = document.createElement('form'); other.id = 'synthetic-other-form'; document.body.append(other);
    if (mode === 'go_form') document.querySelector('button').setAttribute('form', other.id);
    if (mode === 'shipping_form') document.querySelector('select').setAttribute('form', other.id);
    if (mode === 'summary_outside') other.append(document.querySelector('.order_summary'));
    if (mode === 'duplicate_container') document.body.append(document.querySelector('.order_container').cloneNode(false));
    if (mode === 'duplicate_address') document.querySelector('.order_container').append(document.querySelector('[id^="shipping_address_"]').cloneNode(true));
    if (mode === 'address_outside') other.append(document.querySelector('[id^="shipping_address_"]'));
    try {
      const snapshot = await b.page.evaluate(port.collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: sha(ADDRESS.join('\n')) });
      assert.equal(snapshot.address_present, false, mode); assert.equal(snapshot.destination, false, mode);
      if (!['duplicate_address', 'address_outside'].includes(mode)) assert.equal(snapshot.go, false, mode);
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.close(); }
  }
});

test('bounded nested address walk rejects visible controls, unknown elements, extra lines and changed values without exporting private text', async () => {
  for (const fragment of ['<input value="PRIVATE">', '<form><input></form>', '<textarea>PRIVATE</textarea>', '<section>PRIVATE</section>', '<a>PRIVATE</a>', '<div>EXTRA_PRIVATE_LINE</div>'.repeat(3)]) {
    const b = browser({ cart: nestedPrivateCart }), document = b.state.merchant.window.document;
    document.querySelector('[id^="shipping_address_"]').insertAdjacentHTML('beforeend', fragment);
    try {
      const snapshot = await b.page.evaluate(port.collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: sha(ADDRESS.join('\n')) });
      assert.equal(snapshot.address_present, false, fragment); assert.equal(snapshot.destination, false, fragment);
      assert.ok(!json(snapshot).includes('PRIVATE'));
    } finally { b.close(); }
  }
  for (const change of ['content', 'same_text_node', 'form']) {
    const b = browser({ cart: nestedPrivateCart });
    try {
      const document = b.state.merchant.window.document, address = document.querySelector('[id^="shipping_address_"]');
      let calls = 0;
      Object.defineProperty(b.state.merchant.window, 'crypto', { value: { subtle: { digest: async (...args) => {
        const result = await webcrypto.subtle.digest(...args);
        if (++calls === 2) {
          const line = address.querySelector('div + div > div').firstChild;
          if (change === 'content') line.nodeValue = 'CHANGED_PRIVATE_LINE';
          if (change === 'same_text_node') line.replaceWith(line.cloneNode());
          if (change === 'form') {
            const other = document.createElement('form'); other.id = 'changed-owner-form'; document.body.append(other);
            document.querySelector('button').setAttribute('form', other.id);
          }
        }
        return result;
      } } } });
      const snapshot = await b.page.evaluate(port.collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: sha(ADDRESS.join('\n')), privateDestinationSnapshot: true });
      assert.equal(snapshot.address_present, false, change); assert.equal(snapshot.destination, false, change);
      assert.ok(!Object.hasOwn(snapshot, 'private_destination_snapshot')); assert.ok(!json(snapshot).includes('CHANGED_PRIVATE_LINE'));
    } finally { b.close(); }
  }
});

test('nested nonlegacy cart confirmation binds only the same current document and unchanged private lines', async () => {
  const isolated = await import('../scripts/discogs_hosted_guest_prepare.mjs?nonlegacy-confirm=' + randomUUID());
  for (const change of ['document', 'address']) {
    const b = browser({ cart: nestedPrivateCart }), grant = readonlyGrant();
    const probeCurrent = grant => isolated.probeCurrentGuestPreparationEvidence(b.context, grant.request, { authorization: grant.authorization });
    try {
      const first = await probeCurrent(grant); assert.equal(first.status, 'observed');
      if (change === 'document') b.state.merchant = dom(nestedPrivateCart, CART);
      else b.state.merchant.window.document.querySelector('[id^="shipping_address_"] div + div > div').firstChild.nodeValue = 'CHANGED_PRIVATE_LINE';
      const result = await probeCurrent(confirmation(grant, first));
      assert.equal(result.code, change === 'document' ? 'probe_document_changed' : 'saved_destination_snapshot_changed');
      assert.notEqual(result.destination_binding_available, true); assert.ok(!json(result).includes('CHANGED_PRIVATE_LINE'));
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { if (change === 'document') b.state.merchant.window.close(); b.close(); }
  }
});

test('observed plain Change your address label is excluded from synthetic nested private lines while unknown links reject', async () => {
  // Public label observed in the actual cart at21:49; private values and deeper
  // line markup remain synthetic, derived from the redacted1ea2be8 structure.
  for (const [link, allowed] of [
    ['<a>Change your address</a>', true],
    ['<a>Change another address</a>', false],
    ['<a><span>Change your address</span></a>', false],
    ['<a>Edit</a>', false],
  ]) {
    const cart = nestedPrivateCart.replace('<a class="edit_shipping_address">Edit</a>', link);
    const b = browser({ cart });
    for (const anchor of b.state.merchant.window.document.querySelectorAll('[id^="shipping_address_"] a')) {
      const getAttribute = anchor.getAttribute.bind(anchor);
      anchor.getAttribute = name => { if (name === 'href') throw new Error('ADDRESS_LINK_HREF_MUST_NOT_BE_READ'); return getAttribute(name); };
    }
    try {
      const snapshot = await b.page.evaluate(port.collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: sha(ADDRESS.join('\n')) });
      assert.equal(snapshot.address_present, allowed, link); assert.equal(snapshot.destination, allowed, link);
      assert.equal(snapshot.address_line_count, allowed ? 4 : null, link);
      for (const secret of ADDRESS.filter(value => value !== 'US')) assert.ok(!json(snapshot).includes(secret));
      assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    } finally { b.close(); }
  }
});
