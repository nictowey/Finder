'use strict';
// Executable offline rehearsal:
// node --test tests/test_discogs_enhanced_checkout_coordinator.cjs
// Actual temporary SQLite/Python ledger; all browser/provider/dispatch evidence
// below is intercepted synthetic metadata. No HTTP, payment or receipt DOM claim.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID } = require('node:crypto');
let createEnhancedCheckoutCoordinator, createEnhancedCheckoutCoordinatorFixture,
  createBoundEnhancedOrderFencePort, createEnhancedOrderLedgerFixturePort,
  attemptEnhancedPayment, KC_ORDER_SCOPE;
before(async () => {
  ({ createEnhancedCheckoutCoordinator, createEnhancedCheckoutCoordinatorFixture,
    createBoundEnhancedOrderFencePort } = await import('../scripts/discogs_enhanced_checkout_coordinator.mjs'));
  ({ createEnhancedOrderLedgerFixturePort } = await import('../scripts/discogs_enhanced_order_ledger_port.mjs'));
  ({ attemptEnhancedPayment } = await import('../scripts/discogs_enhanced_payment_adapter.mjs'));
  ({ KC_ORDER_SCOPE } = await import('../scripts/discogs_merchant_order_evidence.mjs'));
});
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-coordinator-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, 'orders.sqlite3'), port = createEnhancedOrderLedgerFixturePort(path), now = Date.now() / 1000;
  const scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100, listing_id: '1366950910',
    seller: 'Mailman21842', one_copy_limit: 1, currency: 'USD', max_total: '5.05', rules_revision: 4,
    quote_fingerprint: 'a'.repeat(64), destination_ref: randomUUID(), payment_ref: randomUUID(), terms_fingerprint: 'b'.repeat(64), expires_at: now + 180 };
  const money = amount => ({ amount, currency: 'USD' }), shippingRef = randomUUID();
  const quote = { source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id, release_id: scope.release_id,
    listing_id: scope.listing_id, seller: scope.seller, rules_revision: scope.rules_revision, quantity: 1, currency: 'USD',
    quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref, payment_ref: scope.payment_ref,
    final_quote_verified: true, all_payable_charges_included: true, payment_action: 'none', shipping_selection_verified: true,
    selected_shipping_ref: shippingRef, selected_shipping_label: 'Synthetic selected shipping', observed_at: now, expires_at: now + 120,
    item: money('0.10'), shipping: money('4.95'), tax: money('0.00'), fees: null, fees_absent_verified: true, total: money('5.05') };
  const fence = createBoundEnhancedOrderFencePort(scope, quote, { ledgerPort: port });
  return { port, path, scope, quote, fence, now, shippingRef };
}
function receipt(f, displayedStatus = 'Invoice Sent') {
  return { order_id: '123456-789012', release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842',
    total: '5.05', currency: 'USD', displayed_status: displayedStatus, observed_at: Date.now() / 1000, source: 'normal_order_page' };
}
function interceptedPaymentPorts(f, dispatch) {
  return { kind: 'trusted_production_enhanced_payment_v1',
    readCurrentState: async () => ({ source: 'current_guarded_candidate', intent_id: f.scope.intent_id,
      release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', rules_revision: f.scope.rules_revision,
      claim_state: 'cart_observed', owned: false, active: true, kill_switch: false, one_copy_limit: 1,
      intent_state: (await f.port.status()).ledger?.state || 'prepared' }),
    readFinalQuote: async () => structuredClone(f.quote),
    readDestination: async () => ({ source: 'verified_current_checkout_destination', current: true, selected_for_checkout: true,
      destination_ref: f.scope.destination_ref, selected_shipping_ref: f.shippingRef, approved_for_intent: f.scope.intent_id }),
    readPaymentMethod: async () => ({ source: 'verified_current_enhanced_payment_method', selected: true, method_kind: 'card',
      payment_ref: f.scope.payment_ref, approved_for_intent: f.scope.intent_id }),
    readTerms: async () => ({ source: 'owner_confirmed_current_cart_terms', current: true, fingerprint: f.scope.terms_fingerprint, approved_for_intent: f.scope.intent_id }),
    readAuthorization: async () => ({ source: 'owner_approved_exact_order', current: true, revoked: false, scope: { ...f.scope } }),
    readPayControl: async () => ({ source: 'verified_current_enhanced_pay_control', url: 'https://www.discogs.com/marketplace/checkout',
      control_ref: f.controlRef, control_fingerprint: 'c'.repeat(64), observed_label: 'Synthetic intercepted commit', visible: true, enabled: true,
      selector_verified: true, commit_semantics: 'creates_order_or_payment', observed_at: f.now, expires_at: f.now + 20 }),
    fencePossibleSubmit: f.fence.fencePossibleSubmit, dispatchObservedPay: dispatch };
}

test('production coordinator cannot accept payment toggles/dispatchers and attempt remains disabled without ledger mutation', async () => {
  for (const config of [{ enablePay: true }, { ledgerPort: {} }, { dispatchObservedPay: () => { throw new Error('Forbidden'); } }]) {
    assert.throws(() => createEnhancedCheckoutCoordinator(config), /fixed_production_coordinator_configuration_required/);
  }
  const coordinator = createEnhancedCheckoutCoordinator(); assert.equal(coordinator.paymentEnabled, false);
  const result = await coordinator.attemptPayment({}, { enablePay: true });
  assert.equal(result.code, 'enhanced_payment_disabled'); assert.equal(result.may_submit_again, false);
});

test('current quote reader is composed into truthful missing-profile readiness; inspection never fences', async t => {
  const f = await fixture(t); let fences = 0;
  const coordinator = createEnhancedCheckoutCoordinatorFixture({ ledgerPort: { reconcile: f.port.reconcile, fencePossibleSubmit: () => { fences++; } },
    readQuote: async () => ({ schema: 'discogs_enhanced_payment_quote_v1', final_quote_verified: false,
      gates: { provider_dom_qualification: false, provider_delivery_binding: false },
      missing_evidence: ['provider_dom_qualification', 'provider_delivery_binding'] }) });
  const result = await coordinator.inspectPaymentReadiness({});
  assert.equal(result.payment_allowed, false); assert.equal(result.payment.code, 'enhanced_payment_disabled');
  assert.ok(result.missing_profiles.includes('provider_final_quote_dom')); assert.ok(result.missing_profiles.includes('merchant_positive_order_dom'));
  assert.equal(fences, 0); assert.equal((await f.port.status()).status, 'blocked');
});

test('immutable fence binding rejects changed quote/scope and snapshots caller mutations before actual CLI', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal(Object.isFrozen(f.fence.expectedOrder), true);
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, { ...f.quote, total: { amount: '5.06', currency: 'USD' } }, f.scope)).code, 'immutable_order_binding_changed');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, { ...f.scope, payment_ref: randomUUID() })).code, 'immutable_order_binding_changed');
  assert.equal((await f.port.status()).ledger, null);
  const originalQuote = structuredClone(f.quote), originalScope = { ...f.scope };
  f.quote.total.amount = '9.99'; f.scope.payment_ref = randomUUID();
  const fenced = await f.fence.fencePossibleSubmit(originalScope.intent_id, originalQuote, originalScope);
  assert.equal(fenced.durable, true); assert.equal((await f.port.status()).ledger.state, 'submission_unknown');
});

test('unknown actual provider final quote cannot create an immutable order binding', async t => {
  const f = await fixture(t);
  assert.throws(() => createBoundEnhancedOrderFencePort(f.scope, { ...f.quote, final_quote_verified: false }, { ledgerPort: f.port }), /verified_final_quote_binding_required/);
  assert.equal((await f.port.status()).status, 'blocked');
});

test('offline CLI rehearsal: validated synthetic quote -> actual SQLite fence before intercepted dispatch -> unpaid/pending/paid receipts', async t => {
  const f = await fixture(t); f.controlRef = randomUUID(); assert.equal((await f.port.initialize()).status, 'initialized');
  let dispatches = 0;
  const ports = interceptedPaymentPorts(f, async () => { assert.equal((await f.port.status()).ledger.state, 'submission_unknown'); dispatches++; });
  const outcome = await attemptEnhancedPayment(f.scope, ports, { enablePay: true });
  assert.equal(outcome.code, 'submission_unknown'); assert.equal(dispatches, 1);
  for (const [displayed, paymentState] of [['Invoice Sent', 'unpaid'], ['Payment Pending', 'pending'], ['Payment Received', 'paid']]) {
    const positive = receipt(f, displayed);
    const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: f.port,
      readMerchant: async (page, scope) => { assert.deepEqual(scope, KC_ORDER_SCOPE); return { status: 'observed', order_page_evidence: positive }; } });
    const reconciled = await coordinator.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now });
    assert.equal(reconciled.positive_order_evidence_recorded, true); assert.equal(reconciled.ledger.state, 'order_seen');
    assert.equal(reconciled.ledger.payment_state, paymentState); assert.equal(reconciled.may_submit_again, false);
  }
  const restarted = createEnhancedOrderLedgerFixturePort(f.path);
  assert.equal((await restarted.status()).ledger.payment_state, 'paid');
  assert.equal((await attemptEnhancedPayment(f.scope, ports, { enablePay: true })).code, 'payment_preflight_unverified');
  assert.equal(dispatches, 1); assert.equal((await restarted.status()).ledger.may_submit_again, false);
});

test('actual unqualified merchant reader is composed with null lookup preserving unknown, including after restart', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).durable, true);
  const restarted = createEnhancedOrderLedgerFixturePort(f.path);
  const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: restarted });
  const page = { url: () => 'https://www.discogs.com/marketplace/checkout', title: () => { throw new Error('Not a receipt'); }, evaluate: () => { throw new Error('Not a receipt'); } };
  const result = await coordinator.reconcileCurrentMerchantOrder(page, { afterAttemptAt: f.now });
  assert.equal(result.lookup_evidence, 'no_qualified_positive_order_evidence'); assert.equal(result.ledger.state, 'submission_unknown');
  assert.equal(result.absence_verified, false); assert.equal(result.provider_draft_status, 'unverified');
});

test('zero history, malformed/stale/provider receipt evidence cannot clear real submission uncertainty', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).durable, true);
  for (const report of [
    { status: 'observed', observed_order_count: 0, order_page_evidence: null },
    { status: 'unverified', order_page_evidence: receipt(f) },
    { status: 'observed', order_page_evidence: { ...receipt(f), source: 'provider_sdk_callback' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), order_id: 'SDK_TOKEN' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), listing_id: '3526572746' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), total: '5.06' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), observed_at: f.now - 1 } },
    { status: 'observed', order_page_evidence: { ...receipt(f), address: 'TOP_SECRET' } },
  ]) {
    const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: f.port, readMerchant: async () => report });
    const result = await coordinator.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now });
    assert.equal(result.positive_order_evidence_recorded, false); assert.equal(result.ledger.state, 'submission_unknown');
    assert.equal(result.ledger.payment_state, 'unknown'); assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  }
});

test('cancellation and missing immutable intent stop reconciliation before ledger calls', async t => {
  const f = await fixture(t); let lookups = 0;
  const ledgerPort = { reconcile: async () => { lookups++; throw Error('Forbidden'); } };
  const cancelled = new AbortController(); cancelled.abort();
  const absent = createEnhancedCheckoutCoordinatorFixture({ ledgerPort });
  assert.equal((await absent.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now })).code, 'merchant_reconciliation_context_required');
  const present = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort });
  assert.equal((await present.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now, signal: cancelled.signal })).code, 'merchant_reconciliation_cancelled');
  assert.equal(lookups, 0);
});

test('production coordinator invokes the actual fixed quote producer and never promotes merchant aggregate to final quote', async () => {
  const { collectEnhancedPaymentQuoteDom } = await import('../scripts/discogs_enhanced_payment_quote.mjs');
  const { waitForEnhancedCheckoutCoordinatorSettlement } = await import('../scripts/discogs_enhanced_checkout_coordinator.mjs');
  const calls = [], handle = { dispose: async () => calls.push('dispose') };
  const page = { url: () => 'https://www.discogs.com/marketplace/checkout', evaluateHandle: async () => handle,
    evaluate: async (collector, argument) => {
      if (collector !== collectEnhancedPaymentQuoteDom) { assert.equal(argument, handle); return true; }
      calls.push('fixed_quote_collector'); return { schema: 'discogs_enhanced_checkout_dom_v1', status: 'observed', source: 'visible_enhanced_checkout_dom',
        purchase_allowed: false, seller_handle: 'ExampleSeller', visible_row_count: 1,
        displayed: { subtotal: { amount: '0.10', currency: 'USD' }, shipping: { amount: '4.95', currency: 'USD' }, total: { amount: '5.05', currency: 'USD' } },
        selected_shipping_label: 'Economy - Media Mail' };
    }, frames: () => [], mainFrame: () => ({}) };
  const coordinator = createEnhancedCheckoutCoordinator();
  const result = await coordinator.inspectPaymentReadiness(page); await waitForEnhancedCheckoutCoordinatorSettlement(page);
  assert.equal(result.quote.merchant_displayed.total.amount, '5.05'); assert.equal(result.quote.final_quote_verified, false);
  assert.equal(result.quote.amount_basis, 'merchant_aggregate_preparation_only'); assert.equal(result.payment.code, 'enhanced_payment_disabled');
  assert.ok(calls.includes('fixed_quote_collector')); assert.ok(calls.includes('dispose'));
});
