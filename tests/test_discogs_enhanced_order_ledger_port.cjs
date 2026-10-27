'use strict';
// Real local SQLite via separate Python processes, intercepted Pay only.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID } = require('node:crypto');

let createEnhancedOrderLedgerFixturePort, attemptEnhancedPayment;
before(async () => {
  ({ createEnhancedOrderLedgerFixturePort } = await import('../scripts/discogs_enhanced_order_ledger_port.mjs'));
  ({ attemptEnhancedPayment } = await import('../scripts/discogs_enhanced_payment_adapter.mjs'));
});

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-order-ledger-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, 'orders.sqlite3');
  const port = createEnhancedOrderLedgerFixturePort(path);
  const now = Date.now() / 1000;
  const scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', one_copy_limit: 1,
    currency: 'USD', max_total: '5.05', rules_revision: 3, quote_fingerprint: 'a'.repeat(64),
    destination_ref: randomUUID(), payment_ref: randomUUID(), terms_fingerprint: 'b'.repeat(64),
    expires_at: now + 180 };
  const money = amount => ({ amount, currency: 'USD' });
  const shippingRef = randomUUID();
  const quote = { source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id,
    release_id: scope.release_id, listing_id: scope.listing_id, seller: scope.seller,
    rules_revision: scope.rules_revision, quantity: 1, currency: 'USD',
    quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref,
    payment_ref: scope.payment_ref, final_quote_verified: true, charge_basis: 'itemized',
    all_payable_charges_included: true, payment_action: 'none',
    shipping_selection_verified: true, selected_shipping_ref: shippingRef,
    selected_shipping_label: 'Synthetic selected shipping', observed_at: now,
    expires_at: now + 120, item: money('0.10'), shipping: money('4.95'),
    tax: money('0.00'), fees: null, fees_absent_verified: true, total: money('5.05') };
  return { port, path, scope, quote, now, shippingRef };
}

test('fixed ledger port is noncreating and prepares/fences only exact immutable metadata', async t => {
  const f = await fixture(t);
  assert.equal((await f.port.status()).status, 'blocked');
  assert.equal((await f.port.initialize()).status, 'initialized');
  const prepared = await f.port.prepare(f.scope, f.quote);
  assert.equal(prepared.ledger.state, 'prepared');
  assert.equal((await f.port.prepare(f.scope, f.quote)).ledger.state, 'prepared');
  const fence = await f.port.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope);
  assert.deepEqual(fence, { intent_id: f.scope.intent_id, state: 'submission_unknown',
    may_submit_again: false, durable: true });
  assert.equal((await f.port.status()).ledger.state, 'submission_unknown');
  assert.equal((await f.port.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).status, 'blocked');
  assert.equal((await f.port.reconcile(f.scope.intent_id, null)).ledger.state, 'submission_unknown');
});

test('same real SQLite file permits only one competing fence, including after new JS factory instance', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  const another = createEnhancedOrderLedgerFixturePort(f.path);
  const outcomes = await Promise.all(Array.from({ length: 4 }, (_, index) =>
    (index % 2 ? another : f.port).fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)));
  assert.equal(outcomes.filter(value => value.durable === true).length, 1);
  assert.equal((await another.status()).ledger.state, 'submission_unknown');
});

test('unknown charges or changed exact scope cannot create or fence a slot', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  for (const quote of [
    { ...f.quote, fees_absent_verified: false },
    { ...f.quote, tax: null },
    { ...f.quote, total: { amount: '5.04', currency: 'USD' } },
    { ...f.quote, charge_basis: 'unknown' },
  ]) {
    assert.equal((await f.port.fencePossibleSubmit(f.scope.intent_id, quote, f.scope)).status, 'blocked');
  }
  const changed = { ...f.scope, payment_ref: randomUUID() };
  assert.equal((await f.port.fencePossibleSubmit(changed.intent_id, f.quote, changed)).status, 'blocked');
  assert.equal((await f.port.status()).ledger, null);
});

test('authoritative synthetic aggregate total fences once across SQLite process and port restart', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  Object.assign(f.quote, { charge_basis: 'aggregate_inclusive', tax: null, fees: null,
    tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total',
    deferred_charges: false, aggregate_total_verified: true,
    fees_absent_verified: false });
  assert.equal((await f.port.prepare(f.scope, f.quote)).ledger.state, 'prepared');
  const another = createEnhancedOrderLedgerFixturePort(f.path);
  const outcomes = await Promise.all([
    f.port.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope),
    another.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope),
  ]);
  assert.equal(outcomes.filter(value => value.durable === true).length, 1);
  assert.equal((await another.status()).ledger.state, 'submission_unknown');
  assert.equal((await another.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).status, 'blocked');
});

test('aggregate unknown tax treatment, incomplete charges, lower bound, or changed scope never creates ledger row', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  const aggregate = { ...f.quote, charge_basis: 'aggregate_inclusive', tax: null, fees: null,
    tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total',
    deferred_charges: false, aggregate_total_verified: true,
    fees_absent_verified: false };
  for (const quote of [
    { ...aggregate, tax_treatment: 'unknown' },
    { ...aggregate, fees_treatment: 'deferred' },
    { ...aggregate, deferred_charges: true },
    { ...aggregate, fees_absent_verified: 'unverified' },
    { ...aggregate, fees_absent_verified: null },
    { ...aggregate, aggregate_total_verified: false },
    { ...aggregate, all_payable_charges_included: false },
    { ...aggregate, total: { amount: '5.06', currency: 'USD' } },
    { ...aggregate, shipping: { amount: '5.00', currency: 'USD' } },
    { ...aggregate, total: { amount: '5.05', currency: 'CAD' } },
    { ...aggregate, destination_ref: randomUUID() },
    { ...aggregate, tax: { amount: '0.00', currency: 'USD' } },
  ]) {
    assert.equal((await f.port.prepare(f.scope, quote)).status, 'blocked');
  }
  assert.equal((await f.port.status()).ledger, null);
});

test('itemized ledger quote rejects ambiguous deferred-charge markers', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  for (const marker of [null, 'unverified', 'true', 0]) {
    assert.equal((await f.port.prepare(f.scope,
      { ...f.quote, deferred_charges: marker })).status, 'blocked');
  }
  assert.equal((await f.port.status()).ledger, null);
});

test('intercepted adapter call uses actual durable SQLite fence before possible Pay dispatch', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  let dispatches = 0;
  const state = { source: 'current_guarded_candidate', intent_id: f.scope.intent_id,
    release_id: f.scope.release_id, listing_id: f.scope.listing_id, seller: f.scope.seller,
    rules_revision: f.scope.rules_revision, claim_state: 'cart_observed', owned: false,
    active: true, kill_switch: false, one_copy_limit: 1, intent_state: 'prepared' };
  const destination = { source: 'verified_current_checkout_destination', current: true,
    selected_for_checkout: true, destination_ref: f.scope.destination_ref,
    selected_shipping_ref: f.shippingRef, approved_for_intent: f.scope.intent_id };
  const method = { source: 'verified_current_enhanced_payment_method', selected: true,
    method_kind: 'card', payment_ref: f.scope.payment_ref, approved_for_intent: f.scope.intent_id };
  const terms = { source: 'owner_confirmed_current_cart_terms', current: true,
    fingerprint: f.scope.terms_fingerprint, approved_for_intent: f.scope.intent_id };
  const auth = { source: 'owner_approved_exact_order', current: true,
    revoked: false, scope: { ...f.scope } };
  const control = { source: 'verified_current_enhanced_pay_control',
    url: 'https://www.discogs.com/marketplace/checkout', control_ref: randomUUID(),
    control_fingerprint: 'c'.repeat(64), observed_label: 'Synthetic committing control',
    visible: true, enabled: true, selector_verified: true,
    commit_semantics: 'creates_order_or_payment', observed_at: f.now,
    expires_at: f.now + 20 };
  const ports = { kind: 'trusted_production_enhanced_payment_v1',
    readCurrentState: async () => ({ ...state, intent_state: (await f.port.status()).ledger?.state || 'prepared' }),
    readFinalQuote: async () => structuredClone(f.quote),
    readDestination: async () => ({ ...destination }),
    readPaymentMethod: async () => ({ ...method }),
    readTerms: async () => ({ ...terms }),
    readAuthorization: async () => ({ ...auth, scope: { ...auth.scope } }),
    readPayControl: async () => ({ ...control }),
    fencePossibleSubmit: f.port.fencePossibleSubmit,
    dispatchObservedPay: async () => {
      assert.equal((await f.port.status()).ledger.state, 'submission_unknown');
      dispatches++;
    },
  };
  const result = await attemptEnhancedPayment(f.scope, ports, { enablePay: true });
  assert.equal(result.code, 'submission_unknown');
  assert.equal(dispatches, 1);
  assert.equal((await attemptEnhancedPayment(f.scope, ports, { enablePay: true })).code,
    'payment_preflight_unverified');
  assert.equal(dispatches, 1);
});
