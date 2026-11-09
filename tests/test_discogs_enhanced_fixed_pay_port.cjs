'use strict';
// Actual read-only producer and real temporary SQLite; fixed click intercepted.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { randomUUID } = require('node:crypto');
const { mkdtemp, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { JSDOM } = require('jsdom');
let api, quote, attemptEnhancedPayment, createEnhancedOrderLedgerFixturePort;
before(async () => {
  api = await import('../scripts/discogs_enhanced_fixed_pay_port.mjs');
  quote = await import('../scripts/discogs_enhanced_payment_quote.mjs');
  ({ attemptEnhancedPayment } = await import('../scripts/discogs_enhanced_payment_adapter.mjs'));
  ({ createEnhancedOrderLedgerFixturePort } = await import('../scripts/discogs_enhanced_order_ledger_port.mjs'));
});
const URL = 'https://www.discogs.com/marketplace/checkout';
const HTML = `<h1>Checkout from Mailman21842</h1><div class="_checkout-summary__content_fixture">
<ul class="_checkout-summary__unordered-list_fixture"><li class="_checkout-sell-item__list-item_fixture">Public item</li></ul>
<dl class="_checkout-summary__totals_fixture">
<div class="_checkout-summary__subtotal_fixture"><dt>Subtotal</dt><dd class="_checkout-summary__money-value_fixture">$0.10 USD</dd></div>
<div class="_checkout-summary__subtotal_fixture"><dt>Shipping via Economy - Media Mail</dt><dd class="_checkout-summary__money-value_fixture">$4.95 USD</dd></div>
<div class="_checkout-summary__total--color-red_fixture"><dt>Total</dt><dd class="_checkout-summary__money-value_fixture">$5.05 USD</dd></div></dl></div>`;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function fixture(t, { gate = true } = {}) {
  const folder = await mkdtemp(join(tmpdir(), 'fixed-pay-port-'));
  const ledger = createEnhancedOrderLedgerFixturePort(join(folder, 'orders.sqlite3'));
  await ledger.initialize();
  const now = Date.now() / 1000, scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', one_copy_limit: 1, currency: 'USD', max_total: '5.05',
    rules_revision: 1, quote_fingerprint: 'a'.repeat(64), destination_ref: randomUUID(), payment_ref: randomUUID(),
    terms_fingerprint: 'b'.repeat(64), expires_at: now + 180 };
  scope.quote_fingerprint = api.fixedEnhancedPayQuoteFingerprint(scope);
  const effects = { reads: 0, clicks: 0, fences: 0, selectors: [], quoteReports: 0, quoteTimes: [], quoteRefs: [], disposals: 0 }, frames = [], doms = [];
  const state = { intent: 'prepared', cache: null, reread: true, hook: null, clickHook: null, contextOpen: true };
  const convert = value => value?.__document !== undefined ? value.__document :
    value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).map(([k,v]) => [k,convert(v)])) : value;
  const documentHandle = document => ({ __document: document, dispose: async () => { effects.disposals++; } });
  function frame(url, parent, html) {
    const dom = new JSDOM(`<html><body>${html}<aside>FORBIDDEN_ADDRESS</aside><input value="FORBIDDEN_PAN"></body></html>`, { url, runScripts: 'outside-only' }); doms.push(dom);
    dom.window.Element.prototype.getClientRects = () => [{ width: 100, height: 20 }];
    Object.defineProperty(dom.window.document, 'readyState', { get: () => 'complete' });
    Object.defineProperty(dom.window.HTMLInputElement.prototype, 'value', { get() { throw new Error('No card values'); } });
    for (const method of ['click', 'focus']) dom.window.HTMLElement.prototype[method] = () => { throw new Error('No DOM effects'); };
    dom.window.fetch = () => { throw new Error('No network'); };
    const item = { dom, url, parent, visible: true, attached: true, owner: parent, doc: dom.window.document };
    const element = { isVisible: async () => { effects.reads++; return item.visible; }, contentFrame: async () => item.attached ? value : null,
      ownerFrame: async () => item.owner, dispose: async () => {} };
    const value = { url: () => { effects.reads++; return item.url; }, parentFrame: () => item.parent,
      frameElement: async () => element,
      evaluateHandle: async (fn, arg) => { effects.reads++; await state.hook?.('handle', item); const result = dom.window.eval(`(${fn.toString()})`)(convert(arg));
        if (result?.nodeType === 1) return { asElement() { return this; }, evaluate: async (fn, arg) => {
          effects.reads++; await state.hook?.('control', item); return dom.window.eval(`(${fn.toString()})`)(result, convert(arg)); },
          click: async options => { effects.clicks++; assert.ok(options.timeout > 0); assert.equal(state.intent, 'submission_unknown'); await state.clickHook?.(); }, dispose: async () => {} };
        return documentHandle(result); },
      evaluate: async (fn, arg) => { effects.reads++; await state.hook?.('evaluate', item); return dom.window.eval(`(${fn.toString()})`)(convert(arg)); },
      locator: selector => { effects.selectors.push(selector); assert.equal(selector, '#submit-button'); return {
        count: async () => dom.window.document.querySelectorAll(selector).length,
        elementHandle: async () => {
          const node = dom.window.document.querySelector(selector);
          return { evaluate: async (fn, arg) => { effects.reads++; await state.hook?.('control', item); return dom.window.eval(`(${fn.toString()})`)(node, convert(arg)); },
            click: async options => { effects.clicks++; assert.ok(options.timeout > 0); assert.equal(state.intent, 'submission_unknown'); await state.clickHook?.(); },
            dispose: async () => {} };
        } }; } };
    item.frame = value; frames.push(value); return item;
  }
  const main = frame(URL, null, HTML), buttons = frame('https://www.paypal.com/smart/buttons?token=FORBIDDEN_TOKEN', main.frame, '<div>Public container</div>');
  const guest = frame('https://www.paypal.com/smart/fixture-guest?token=FORBIDDEN_TOKEN', buttons.frame,
    '<button id="submit-button" type="button">Pay $5.05</button>');
  const hidden = frame('https://www.paypal.com/smart/fixture-hidden', guest.frame, '<div>Hidden child</div>'); hidden.visible = false;
  const context = { pages: () => state.contextOpen ? [page] : [] };
  const page = { ...main.frame, context: () => context, mainFrame: () => main.frame, frames: () => frames };
  let held = false; const acquire = () => { if (held) return null; held = true; return () => { held = false; }; };
  const shipping = randomUUID(), current = { source: 'current_guarded_candidate', intent_id: scope.intent_id,
    release_id: scope.release_id, listing_id: scope.listing_id, seller: scope.seller, rules_revision: 1,
    claim_state: 'cart_observed', owned: false, active: true, kill_switch: false, one_copy_limit: 1 };
  const destination = { source: 'verified_current_checkout_destination', current: true, selected_for_checkout: true,
    destination_ref: scope.destination_ref, selected_shipping_ref: shipping, approved_for_intent: scope.intent_id };
  const method = { source: 'verified_current_enhanced_payment_method', selected: true, method_kind: 'card', payment_ref: scope.payment_ref, approved_for_intent: scope.intent_id };
  const terms = { source: 'owner_confirmed_current_cart_terms', current: true, fingerprint: scope.terms_fingerprint, approved_for_intent: scope.intent_id };
  const authorization = { source: 'owner_approved_exact_order', current: true, revoked: false, scope };
  const readers = { readCurrentState: async () => ({ ...current, intent_state: state.intent }), readDestination: async () => ({ ...destination }),
    readPaymentMethod: async () => ({ ...method }), readTerms: async () => ({ ...terms }), readAuthorization: async () => authorization,
    readVerifiedQuoteReport: async () => {
      if (!state.cache || state.reread) {
        effects.quoteReports++;
        state.cache = await quote.inspectCurrentEnhancedPaymentQuote(page, {
          timeoutMs: state.quoteTimeout ?? 1500,
          destinationMatcher: async request => ({ source: 'trusted_same_document_destination_binding', document_ref: request.document_ref,
            destination_ref: scope.destination_ref, match: true }),
          finalQuoteMatcher: async request => ({ schema: 'discogs_guest_post_entry_confirmation_v1', source: 'trusted_same_document_post_entry_confirmation',
            merchant_document_ref: request.merchant_document_ref, provider_document_ref: request.provider_document_ref,
            observed_at: Date.now()/1000, expires_at: request.expires_at, entry_confirmed_at: Date.now()/1000, entry_complete: true,
            final_total: { amount: '5.05', currency: 'USD' }, all_payable_charges_included: true,
            tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total', deferred_charges: false,
            competing_currency: false, conversion_notice: false, new_notice: false, destination_ref: scope.destination_ref, destination_match: true,
            shipping_selection_verified: true, terms_verified: true, terms_fingerprint: scope.terms_fingerprint }) });
        effects.quoteTimes.push(state.cache.provenance?.observed_at);
        effects.quoteRefs.push(state.cache.provenance?.document_ref);
      }
      return state.cache;
    } };
  const fence = { fencePossibleSubmit: async (...args) => { effects.fences++; const result = await ledger.fencePossibleSubmit(...args);
    if (result.durable) state.intent = 'submission_unknown'; await state.fenceHook?.(); return result; } };
  const port = api.createEnhancedFixedPayFixturePort({ page, context, acquireContextLease: acquire, evidencePorts: readers, orderFencePort: fence, liveGate: gate });
  t.after(async () => { api.closeFixedEnhancedPayPort(port); await api.waitForFixedEnhancedPayPortSettlement(port);
    await quote.waitForEnhancedPaymentQuoteSettlement(page); doms.forEach(dom => dom.window.close()); await rm(folder, { recursive: true, force: true }); });
  const enter = async () => { assert.equal((await api.beginFixedEnhancedPaySecureEntry(port, scope)).status, 'entry_ready');
    const result = await api.resumeFixedEnhancedPayAfterEntry(port, scope); return result; };
  return { port, scope, state, effects, ledger, page, context, current, destination, method, terms, authorization, readers, acquire,
    main, buttons, guest, hidden, enter, get held() { return held; } };
}

test('default live gate is off and forged port has no brand; no DOM, evidence, fence or click', async () => {
  let calls = 0; const never = new Proxy({}, { get() { calls++; throw new Error('No read'); } });
  const port = api.createEnhancedFixedPayPort({ page: never, context: never, evidencePorts: never });
  assert.equal(api.isFixedEnhancedPayPort({ kind: port.kind }), false);
  assert.equal((await port.readPayControl({})).code, 'fixed_pay_disabled_or_entry_incomplete');
  assert.equal((await api.beginFixedEnhancedPaySecureEntry(port, {})).status, 'blocked');
  assert.equal(calls, 0); api.finishFixedEnhancedPayPortAttempt(port); await api.waitForFixedEnhancedPayPortSettlement(port);
});

test('actual producer -> bound aggregate mapping -> adapter -> real SQLite fence -> one fixed intercepted click', async t => {
  const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  assert.equal(quote.isVerifiedCurrentEnhancedPaymentQuote(f.state.cache, { page: f.page }), true);
  const mapped = await f.port.readFinalQuote(f.scope);
  assert.equal(mapped.charge_basis, 'aggregate_inclusive'); assert.equal(mapped.tax, null); assert.equal(mapped.fees, null);
  assert.deepEqual(mapped.total, { amount: '5.05', currency: 'USD' }); assert.equal(f.state.cache.provider_displayed.currency, null);
  const result = await attemptEnhancedPayment(f.scope, f.port, { enablePay: true, liveGate: true });
  assert.equal(result.code, 'submission_unknown'); assert.equal(f.effects.fences, 1); assert.equal(f.effects.clicks, 1);
  await api.waitForFixedEnhancedPayPortSettlement(f.port); assert.equal(f.held, false);
  assert.equal((await f.ledger.status()).ledger.state, 'submission_unknown');
  assert.ok(f.effects.quoteReports > 3); assert.ok(new Set(f.effects.quoteTimes).size > 1); assert.ok(new Set(f.effects.quoteRefs).size > 1);
  await attemptEnhancedPayment(f.scope, f.port, { enablePay: true, liveGate: true }); assert.equal(f.effects.clicks, 1);
  assert.equal(f.effects.selectors.every(value => value === '#submit-button'), true);
});

test('secure entry holds the existing context lease and blocks every reader until fresh trusted confirmation', async t => {
  const f = await fixture(t);
  assert.equal((await api.beginFixedEnhancedPaySecureEntry(f.port, f.scope)).status, 'entry_ready');
  const count = f.effects.reads; assert.equal(f.acquire(), null);
  for (const name of ['readCurrentState','readFinalQuote','readDestination','readPaymentMethod','readTerms','readAuthorization','readPayControl'])
    assert.equal((await f.port[name](f.scope)).status, 'blocked');
  assert.equal(f.effects.reads, count); assert.equal(f.effects.clicks, 0);
  f.authorization.revoked = true;
  assert.equal((await api.resumeFixedEnhancedPayAfterEntry(f.port, f.scope)).code, 'fresh_post_entry_evidence_required');
  assert.equal(f.effects.fences, 0); assert.equal(f.held, true);
});

test('wrong control, amount/currency, destination, terms, method, and copied producer report cannot fence', async t => {
  for (const mode of ['type','amount','currency','destination','terms','method','copied','document','cancel']) {
    const sub = await fixture(t); assert.equal((await sub.enter()).code, 'fresh_post_entry_context_verified');
    if (mode === 'type') sub.guest.dom.window.document.querySelector('button').setAttribute('type','submit');
    if (mode === 'amount') sub.guest.dom.window.document.querySelector('button').textContent = 'Pay $5.06';
    if (mode === 'currency') sub.main.dom.window.document.querySelector('[class*="total--"]').querySelector('dd').textContent = '$5.05 CAD';
    if (mode === 'destination') sub.destination.destination_ref = randomUUID();
    if (mode === 'terms') sub.terms.current = false;
    if (mode === 'method') sub.method.selected = false;
    if (mode === 'copied') { sub.state.cache = structuredClone(sub.state.cache); sub.state.reread = false; }
    if (mode === 'document') sub.state.contextOpen = false;
    const controller = new AbortController(); if (mode === 'cancel') controller.abort();
    const result = await attemptEnhancedPayment(sub.scope, sub.port, { enablePay: true, liveGate: true, signal: controller.signal });
    assert.notEqual(result.code, 'submission_unknown', mode); assert.equal(sub.effects.fences, 0, mode); assert.equal(sub.effects.clicks, 0, mode);
    await api.waitForFixedEnhancedPayPortSettlement(sub.port);
  }
});

test('a cancelled issued fixed click remains fenced unknown and retains lease until actual settlement', async t => {
  const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  const entered = deferred(), finish = deferred(), controller = new AbortController();
  f.state.clickHook = async () => { entered.resolve(); await finish.promise; };
  const operation = attemptEnhancedPayment(f.scope, f.port, { enablePay: true, liveGate: true, signal: controller.signal });
  await entered.promise; controller.abort(); const result = await operation;
  assert.equal(result.code, 'submission_unknown'); assert.equal(f.held, true); assert.equal(f.effects.clicks, 1);
  finish.resolve(); await api.waitForFixedEnhancedPayPortSettlement(f.port); assert.equal(f.held, false);
  await f.port.dispatchObservedPay({ scope: f.scope }); assert.equal(f.effects.clicks, 1);
});

async function primedFence(f) {
  assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  const q = await f.port.readFinalQuote(f.scope), control = await f.port.readPayControl(f.scope);
  assert.equal((await f.port.fencePossibleSubmit(f.scope.intent_id, q, f.scope)).durable, true);
  return { scope: f.scope, control_ref: control.control_ref, control_fingerprint: control.control_fingerprint, timeout_ms: 5000 };
}

test('concurrent dispatch is reserved before awaits and concurrent secure-entry arming is refused', async t => {
  const f = await fixture(t), args = await primedFence(f);
  await Promise.all([f.port.dispatchObservedPay(args), f.port.dispatchObservedPay(args)]);
  assert.equal(f.effects.clicks, 1); assert.equal(f.effects.fences, 1);
  const other = await fixture(t), first = api.beginFixedEnhancedPaySecureEntry(other.port, other.scope);
  const second = await api.beginFixedEnhancedPaySecureEntry(other.port, other.scope);
  assert.equal(second.status, 'blocked'); assert.equal((await first).status, 'entry_ready');
  assert.equal((await api.beginFixedEnhancedPaySecureEntry(other.port, other.scope)).status, 'blocked');
});

test('cached branded report is insufficient and same-document amount changes before/after fence never click', async t => {
  const cached = await fixture(t); assert.equal((await cached.enter()).code, 'fresh_post_entry_context_verified'); cached.state.reread = false;
  assert.equal((await attemptEnhancedPayment(cached.scope, cached.port, { enablePay: true })).code, 'payment_preflight_unverified');
  assert.equal(cached.effects.fences, 0);
  for (const after of [false,true]) {
    const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
    const change = () => { f.main.dom.window.document.querySelector('[class*="total--"]').querySelector('dd').textContent = '$5.06 USD'; };
    if (after) f.state.fenceHook = change; else change();
    const result = await attemptEnhancedPayment(f.scope, f.port, { enablePay: true });
    assert.equal(result.code, after ? 'submission_unknown' : 'payment_preflight_unverified');
    assert.equal(f.effects.fences, after ? 1 : 0); assert.equal(f.effects.clicks, 0);
  }
});

test('new same-document competing public control is detected by a fresh actual producer read', async t => {
  const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  f.main.dom.window.document.body.insertAdjacentHTML('beforeend','<button id="submit-button" type="button">Pay $5.05</button>');
  assert.equal((await attemptEnhancedPayment(f.scope, f.port, { enablePay: true })).code, 'payment_preflight_unverified');
  assert.equal(f.effects.fences, 0); assert.equal(f.effects.clicks, 0);
});

test('last-dispatch rule/claim/quantity, label and expiry changes retain durable unknown without click', async t => {
  for (const mode of ['rules','claim','quantity','label','expiry','revoked']) {
    const f = await fixture(t), args = await primedFence(f);
    if (mode === 'rules') f.current.rules_revision++;
    if (mode === 'claim') f.current.claim_state = 'revoked';
    if (mode === 'quantity') f.current.one_copy_limit = 2;
    if (mode === 'label') f.guest.dom.window.document.querySelector('button').textContent = 'Pay $5.05 USD';
    if (mode === 'expiry') f.scope.expires_at = Date.now()/1000 - 1;
    if (mode === 'revoked') f.authorization.revoked = true;
    await assert.rejects(f.port.dispatchObservedPay(args), undefined, mode);
    assert.equal(f.effects.clicks, 0, mode); assert.equal((await f.ledger.status()).ledger.state, 'submission_unknown', mode);
    assert.equal((await f.port.dispatchObservedPay(args)).status, 'blocked', mode);
  }
});

test('aborted pending document handles are retained for disposal and entry is never reported ready', async t => {
  const f = await fixture(t), entered = deferred(), finish = deferred(), controller = new AbortController();
  let paused = false;
  f.state.hook = async (kind, item) => { if (!paused && kind === 'handle' && item === f.guest) { paused = true; entered.resolve(); await finish.promise; } };
  const operation = api.beginFixedEnhancedPaySecureEntry(f.port, f.scope, { signal: controller.signal });
  await entered.promise; controller.abort(); assert.equal((await operation).status, 'blocked');
  api.closeFixedEnhancedPayPort(f.port); let settled = false;
  const drain = api.waitForFixedEnhancedPayPortSettlement(f.port).then(() => { settled = true; });
  await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false); assert.equal(f.held, true);
  finish.resolve(); await drain; assert.equal(f.held, false); assert.ok(f.effects.disposals > 0); assert.equal(f.effects.clicks, 0);
});

test('a restart-created new port cannot retry the existing real unknown ledger intent', async t => {
  const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  await attemptEnhancedPayment(f.scope, f.port, { enablePay: true }); await api.waitForFixedEnhancedPayPortSettlement(f.port);
  const again = api.createEnhancedFixedPayFixturePort({ page: f.page, context: f.context, acquireContextLease: f.acquire,
    evidencePorts: f.readers, orderFencePort: f.ledger, liveGate: true });
  assert.equal((await api.beginFixedEnhancedPaySecureEntry(again, f.scope)).status, 'entry_ready');
  assert.equal((await api.resumeFixedEnhancedPayAfterEntry(again, f.scope)).status, 'blocked');
  api.closeFixedEnhancedPayPort(again); await api.waitForFixedEnhancedPayPortSettlement(again); assert.equal(f.effects.clicks, 1);
});

test('producer timeout keeps the shared lease until its underlying browser read actually settles', async t => {
  const f = await fixture(t); assert.equal((await api.beginFixedEnhancedPaySecureEntry(f.port, f.scope)).status, 'entry_ready');
  const entered = deferred(), finish = deferred(); let paused = false;
  f.state.quoteTimeout = 20;
  f.state.hook = async (kind, item) => { if (!paused && kind === 'evaluate' && item === f.main) { paused = true; entered.resolve(); await finish.promise; } };
  const operation = api.resumeFixedEnhancedPayAfterEntry(f.port, f.scope); await entered.promise;
  assert.equal((await operation).status, 'blocked'); api.closeFixedEnhancedPayPort(f.port);
  let settled = false; const drain = api.waitForFixedEnhancedPayPortSettlement(f.port).then(() => { settled = true; });
  await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false); assert.equal(f.held, true);
  finish.resolve(); await drain; assert.equal(f.held, false); assert.equal(f.effects.clicks, 0);
});

test('lost fence response preserves real unknown and cannot dispatch or retry', async t => {
  const f = await fixture(t); assert.equal((await f.enter()).code, 'fresh_post_entry_context_verified');
  f.state.fenceHook = async () => { throw new Error('Synthetic response lost'); };
  assert.equal((await attemptEnhancedPayment(f.scope, f.port, { enablePay: true })).code, 'submission_unknown');
  await api.waitForFixedEnhancedPayPortSettlement(f.port);
  assert.equal((await f.ledger.status()).ledger.state, 'submission_unknown'); assert.equal(f.effects.clicks, 0);
  await attemptEnhancedPayment(f.scope, f.port, { enablePay: true }); assert.equal(f.effects.fences, 1);
});
