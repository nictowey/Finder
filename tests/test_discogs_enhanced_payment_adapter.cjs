'use strict';
// Intercepted synthetic ports only. No browser, merchant request or payment.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');

let attemptEnhancedPayment;
before(async () => {
  ({ attemptEnhancedPayment } = await import('../scripts/discogs_enhanced_payment_adapter.mjs'));
});

function syntheticFixture() {
  const nowMs = 1_000_000; const now = nowMs / 1000;
  const scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', one_copy_limit: 1,
    currency: 'USD', max_total: '5.05', rules_revision: 7, quote_fingerprint: 'a'.repeat(64),
    destination_ref: randomUUID(), payment_ref: randomUUID(), terms_fingerprint: 'b'.repeat(64),
    expires_at: now + 120 };
  const state = { source: 'current_guarded_candidate', intent_id: scope.intent_id,
    release_id: scope.release_id, listing_id: scope.listing_id, seller: scope.seller,
    rules_revision: scope.rules_revision, claim_state: 'cart_observed', owned: false,
    active: true, kill_switch: false, one_copy_limit: 1, intent_state: 'prepared' };
  const money = amount => ({ amount, currency: 'USD' });
  const selectedShipping = randomUUID();
  const quote = { source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id,
    release_id: scope.release_id, listing_id: scope.listing_id, seller: scope.seller,
    rules_revision: scope.rules_revision, quantity: 1, currency: 'USD',
    quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref,
    payment_ref: scope.payment_ref, final_quote_verified: true,
    all_payable_charges_included: true, payment_action: 'none',
    shipping_selection_verified: true, selected_shipping_ref: selectedShipping,
    selected_shipping_label: 'Synthetic selected shipping',
    observed_at: now, expires_at: now + 60,
    item: money('0.10'), shipping: money('4.95'), tax: money('0.00'),
    fees: money('0.00'), total: money('5.05') };
  const destination = { source: 'verified_current_checkout_destination', current: true,
    selected_for_checkout: true, destination_ref: scope.destination_ref,
    selected_shipping_ref: selectedShipping, approved_for_intent: scope.intent_id };
  const paymentMethod = { source: 'verified_current_enhanced_payment_method', selected: true,
    method_kind: 'card', payment_ref: scope.payment_ref, approved_for_intent: scope.intent_id };
  const terms = { source: 'owner_confirmed_current_cart_terms', current: true,
    fingerprint: scope.terms_fingerprint, approved_for_intent: scope.intent_id };
  const authorization = { source: 'owner_approved_exact_order', current: true,
    revoked: false, scope: { ...scope } };
  const control = { source: 'verified_current_enhanced_pay_control',
    url: 'https://www.discogs.com/marketplace/checkout', control_ref: randomUUID(),
    control_fingerprint: 'c'.repeat(64), observed_label: 'Synthetic committing control',
    visible: true, enabled: true, selector_verified: true,
    commit_semantics: 'creates_order_or_payment', observed_at: now, expires_at: now + 20 };
  const effects = { reads: 0, fences: 0, dispatches: 0, dispatchArgs: null };
  const ports = { kind: 'trusted_production_enhanced_payment_v1',
    readCurrentState: async () => { effects.reads++; return { ...state }; },
    readFinalQuote: async () => { effects.reads++; return structuredClone(quote); },
    readDestination: async () => { effects.reads++; return { ...destination }; },
    readPaymentMethod: async () => { effects.reads++; return { ...paymentMethod }; },
    readTerms: async () => { effects.reads++; return { ...terms }; },
    readAuthorization: async () => { effects.reads++; return { ...authorization, scope: { ...authorization.scope } }; },
    readPayControl: async () => { effects.reads++; return { ...control }; },
    fencePossibleSubmit: async intentId => {
      assert.equal(intentId, scope.intent_id); effects.fences++;
      if (state.intent_state !== 'prepared') throw new Error('one-copy durable fence already used');
      state.intent_state = 'submission_unknown';
      return { intent_id: scope.intent_id, state: 'submission_unknown',
        may_submit_again: false, durable: true };
    },
    dispatchObservedPay: async args => { effects.dispatches++; effects.dispatchArgs = args; },
  };
  const run = options => attemptEnhancedPayment(scope, ports,
    { enablePay: true, clock: () => nowMs, ...options });
  return { scope, state, quote, destination, paymentMethod, terms, authorization,
    control, effects, ports, run, nowMs };
}

test('installed default is disabled even with complete intercepted synthetic ports', async () => {
  const f = syntheticFixture();
  const result = await attemptEnhancedPayment(f.scope, f.ports, { clock: () => f.nowMs });
  assert.equal(result.code, 'enhanced_payment_disabled');
  assert.equal(f.effects.reads, 0); assert.equal(f.effects.fences, 0);
  assert.equal(f.effects.dispatches, 0);
});

test('a non-test subprocess cannot turn on Pay by passing enablePay and arbitrary ports', () => {
  const f = syntheticFixture();
  const script = `import { attemptEnhancedPayment } from './scripts/discogs_enhanced_payment_adapter.mjs';
    const scope = ${JSON.stringify(f.scope)};
    const ports = new Proxy({}, { get() { throw new Error('production port was touched'); } });
    const result = await attemptEnhancedPayment(scope, ports, { enablePay: true });
    process.stdout.write(result.code);`;
  const env = { ...process.env }; delete env.NODE_TEST_CONTEXT;
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', script],
    { cwd: process.cwd(), env, encoding: 'utf8', timeout: 5000 });
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stdout, 'enhanced_payment_disabled');
});

test('intercepted synthetic complete evidence fences once before a possible dispatch and never claims payment success', async () => {
  const f = syntheticFixture();
  const result = await f.run();
  assert.equal(result.code, 'submission_unknown');
  assert.equal(result.purchase_allowed, false); assert.equal(result.may_submit_again, false);
  assert.equal(f.effects.fences, 1); assert.equal(f.effects.dispatches, 1);
  assert.equal(f.effects.dispatchArgs.scope.intent_id, f.scope.intent_id);
  assert.equal(f.effects.dispatchArgs.control_ref, f.control.control_ref);
  assert.equal(f.effects.dispatchArgs.control_fingerprint, f.control.control_fingerprint);
  assert.equal(Object.hasOwn(f.effects.dispatchArgs, 'card_number'), false);
  const replay = await f.run();
  assert.equal(replay.code, 'payment_preflight_unverified');
  assert.equal(f.effects.fences, 1); assert.equal(f.effects.dispatches, 1);
});

test('current enhanced displayed total alone, unknown tax, and unknown Pay control never reach the fence', async () => {
  for (const change of [
    f => { f.quote.final_quote_verified = false; },
    f => { f.quote.tax = null; },
    f => { f.quote.total.amount = '5.06'; },
    f => { f.control.selector_verified = false; },
    f => { f.control.url = 'https://www.discogs.com/sell/cart/'; },
    f => { f.paymentMethod.card_number = 'synthetic-forbidden-field'; },
    f => { f.authorization.revoked = true; },
  ]) {
    const f = syntheticFixture(); change(f);
    const result = await f.run();
    assert.equal(result.code, 'payment_preflight_unverified');
    assert.equal(f.effects.fences, 0); assert.equal(f.effects.dispatches, 0);
  }
});

test('absent separate buyer-fee line needs explicit verified absence; it is never inferred as zero', async () => {
  const blocked = syntheticFixture();
  blocked.quote.fees = null;
  assert.equal((await blocked.run()).code, 'payment_preflight_unverified');
  assert.equal(blocked.effects.fences, 0);
  const verified = syntheticFixture();
  verified.quote.fees = null; verified.quote.fees_absent_verified = true;
  assert.equal((await verified.run()).code, 'submission_unknown');
  assert.equal(verified.effects.fences, 1);
});

test('changed quote or revoked owner grant after the durable fence leaves uncertainty and does not dispatch', async () => {
  for (const change of [
    f => { f.quote.shipping.amount = '4.94'; },
    f => { f.authorization.revoked = true; },
    f => { f.state.active = false; },
  ]) {
    const f = syntheticFixture();
    const original = f.ports.fencePossibleSubmit;
    f.ports.fencePossibleSubmit = async intentId => {
      const result = await original(intentId); change(f); return result;
    };
    const result = await f.run();
    assert.equal(result.code, 'submission_unknown');
    assert.equal(f.effects.fences, 1); assert.equal(f.effects.dispatches, 0);
  }
});

test('uncertain fence result is permanent and no intercepted dispatch follows', async () => {
  const f = syntheticFixture();
  f.ports.fencePossibleSubmit = async () => {
    f.effects.fences++; f.state.intent_state = 'submission_unknown';
    throw new Error('synthetic response lost after durable transition');
  };
  assert.equal((await f.run()).code, 'submission_unknown');
  assert.equal(f.effects.dispatches, 0);
  assert.equal((await f.run()).code, 'payment_preflight_unverified');
  assert.equal(f.effects.fences, 1);
});

test('clock crossing at the last dispatch check never rounds an expired action up to one millisecond', async () => {
  const baseline = syntheticFixture(); let calls = 0, dispatchAt = null;
  baseline.ports.dispatchObservedPay = async () => { baseline.effects.dispatches++; dispatchAt = calls; };
  assert.equal((await baseline.run({ clock: () => { calls++; return baseline.nowMs; } })).code,
    'submission_unknown');
  assert.equal(baseline.effects.dispatches, 1);
  assert.ok(Number.isSafeInteger(dispatchAt));

  const f = syntheticFixture(); let crossingCalls = 0, crossed = false;
  const clock = () => {
    crossingCalls++;
    if (crossingCalls >= dispatchAt) {
      crossed = true;
      return f.control.expires_at * 1000;
    }
    return f.nowMs;
  };
  assert.equal((await f.run({ clock })).code, 'submission_unknown');
  assert.equal(crossed, true);
  assert.equal(f.effects.fences, 1);
  assert.equal(f.effects.dispatches, 0);
});

test('aborted interrupted dispatch remains unknown with no second dispatch', async () => {
  const f = syntheticFixture(); const controller = new AbortController();
  let issued, settleDispatch;
  const called = new Promise(resolve => { issued = resolve; });
  const pendingDispatch = new Promise(resolve => { settleDispatch = resolve; });
  f.ports.dispatchObservedPay = async () => {
    f.effects.dispatches++; issued();
    await pendingDispatch; // Synthetic already-issued action cannot be cancelled by Promise.race.
  };
  const resultPromise = f.run({ signal: controller.signal });
  await called; controller.abort();
  assert.equal((await resultPromise).code, 'submission_unknown');
  assert.equal((await f.run()).code, 'payment_attempt_busy');
  assert.equal(f.effects.fences, 1); assert.equal(f.effects.dispatches, 1);
  settleDispatch(); await new Promise(resolve => setImmediate(resolve));
  assert.equal((await f.run()).code, 'payment_preflight_unverified');
  assert.equal(f.effects.dispatches, 1);
});

test('aborted pending preflight read cannot continue or allow same-intent reentry until it settles', async () => {
  const f = syntheticFixture(); const controller = new AbortController();
  let started, settleRead;
  const called = new Promise(resolve => { started = resolve; });
  const pendingRead = new Promise(resolve => { settleRead = resolve; });
  f.ports.readCurrentState = async () => {
    f.effects.reads++; started(); await pendingRead; return { ...f.state };
  };
  const resultPromise = f.run({ signal: controller.signal });
  await called; controller.abort();
  assert.equal((await resultPromise).code, 'payment_preflight_unverified');
  assert.equal((await f.run()).code, 'payment_attempt_busy');
  assert.equal(f.effects.fences, 0);
  settleRead(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.effects.reads, 1); // The abandoned flow issued no further reads.
});
