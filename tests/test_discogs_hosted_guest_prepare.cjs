'use strict';
// No live browser or provider request. Observed selectors/summary/source-derived
// address shape are sanitized. Positive continuity/detail/address/cart-count
// markup below is an EXPLICITLY SYNTHETIC EXTENSION, not captured server DOM.
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
<a class="item_link" href="/sell/item/1366950910">Synthetic KC Single</a>
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
    let clock = actualNow(), confirming = false, advanced = false;
    Date.now = () => clock;
    const b = browser({ beforeEvaluate: async () => {
      if (confirming && !advanced) {
        advanced = true;
        await Promise.resolve(); // The request was valid when the pending read began.
        clock += 20;
      }
    } });
    const grant = readonlyGrant();
    try {
      const first = await probe(b, grant);
      clock += boundary === 'reference' ? 299990 : 59990;
      const next = confirmation(grant, first);
      if (boundary === 'ack') next.authorization.destination_ack.observed_at -= 59.99;
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

test('composite cart item-link title remains fail-closed without source-proven cart semantic title structure', async () => ledgerFixture(async f => {
  const cart = cartMarkup.replace('>Synthetic KC Single</a>', '>Fixture Artist - Synthetic KC Single (7&quot;, 45 RPM, Single)</a>');
  const b = browser({ cart });
  try {
    const result = await execute(f, b);
    assert.equal(result.code, 'visible_scope_evidence_missing');
    assert.ok(result.missing_fields.includes('title_continuity'));
    assert.deepEqual([b.effects.go, b.effects.card, b.effects.pay], [0, 0, 0]);
    assert.equal((await records(f.directory)).length, 0);
  } finally { b.close(); }
}));
