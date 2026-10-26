'use strict';
const assert = require('node:assert/strict');
const { before, test } = require('node:test');
let makeCuaGuestOperator, makeCurrentCheckoutCuaActor;
before(async () => ({ makeCuaGuestOperator, makeCurrentCheckoutCuaActor } = await import('../scripts/discogs_guest_cua_operator.mjs')));
const id = n => `123e4567-e89b-42d3-a456-${String(n).padStart(12, '0')}`;
const NOW = 1_800_000_000_000;
function setup(patch = {}) {
  const effects = [];
  const view = Object.fromEntries(['click', 'scroll', 'getAXState', 'getScreenshot', 'markHandoff']
    .map(name => [name, async (...args) => { effects.push([name, ...args]); return name; }]));
  Object.assign(view, patch);
  let now = NOW;
  const api = makeCuaGuestOperator(view, { clock: () => now });
  const binding = { intent_id: id(1), ready_ref: id(2), scope_digest: 'a'.repeat(64), deadline: NOW + 60000 };
  const permit = (stage, n) => ({ type: 'operator_action', stage, ...binding,
    request_id: id(n), from_document_ref: stage === 'open_cart' ? null : id(4),
    ...(stage === 'open_cart' ? {} : { control_ref: id(5),
      [stage === 'go_to_checkout' ? 'cart_fingerprint' : 'inspection_fingerprint']: 'b'.repeat(64) }) });
  const screen = stage => ({ source: 'operator_current_screen', observed_at: now / 1000,
    url: stage === 'open_guest_card' ? 'https://www.discogs.com/marketplace/checkout' : 'https://www.discogs.com/sell/cart/',
    document_ref: id(4), control_label: { open_cart: 'Cart', go_to_checkout: 'Go to Checkout', open_guest_card: 'Debit or Credit Card' }[stage],
    point: [350, 550], control_ref: stage === 'open_cart' ? null : id(5),
    binding_fingerprint: stage === 'open_cart' ? null : 'b'.repeat(64),
    approval_scope_verified: true, item_scope_verified: true, quote_verified: true,
    destination_verified: true, terms_verified: true, no_card_data_entered: true, total: '5.05', currency: 'USD' });
  return { api, effects, binding, permit, screen, advance: amount => { now += amount; } };
}
test('binding real supported viewer causes no observation or input before fresh arm', async () => {
  const s = setup(); assert.deepEqual(s.effects, []);
  await assert.rejects(s.api.observe(), /not_live/);
  assert.deepEqual(s.effects, []);
  s.api.arm(s.binding); assert.equal(s.api.status().next_stage, 'open_cart');
  const long = setup(); long.api.arm({ ...long.binding, deadline: NOW + 300000 });
  assert.equal(long.api.status().armed, true);
  const expired = setup(); assert.throws(() => expired.api.arm({ ...expired.binding, deadline: NOW + 300001 }));
  assert.deepEqual(expired.effects, []);
});
test('actual adapter calls only ordered supported mouse actions and observations', async () => {
  const s = setup(); s.api.arm(s.binding);
  for (const [i, stage] of ['open_cart', 'go_to_checkout', 'open_guest_card'].entries()) {
    await s.api.dispatch(s.permit(stage, i + 10), s.screen(stage));
  }
  await s.api.positionBlankForm({ blank_form_verified: true, no_card_data_entered: true, point: [800, 600] });
  await s.api.freezeForEntry();
  assert.deepEqual(s.effects.map(v => v[0]), ['click', 'getAXState', 'click', 'getAXState', 'click', 'getAXState',
    'scroll', 'getAXState', 'getScreenshot', 'markHandoff']);
  for (const call of s.effects.filter(v => ['getAXState', 'getScreenshot'].includes(v[0]))) {
    assert.deepEqual(call[1], { emit: false });
  }
  assert.equal(s.api.status().frozen, true);
  await assert.rejects(s.api.observe(), /not_live/);
  await assert.rejects(s.api.dispatch(s.permit('open_guest_card', 99), s.screen('open_guest_card')), /not_live/);
});
test('wrong target, stale screen, changed binding and extra card values produce zero effects', async () => {
  const changes = [p => { p.intent_id = id(99); }, p => { p.stage = 'open_guest_card'; },
    p => { p.deadline = NOW - 1; }, p => { p.scope_digest = 'b'.repeat(64); }];
  for (const change of changes) {
    const s = setup(); s.api.arm(s.binding); const p = s.permit('open_cart', 10); change(p);
    await assert.rejects(s.api.dispatch(p, s.screen('open_cart'))); assert.deepEqual(s.effects, []);
  }
  for (const patch of [{ url: 'https://example.com/' }, { observed_at: NOW / 1000 - 21 },
    { no_card_data_entered: false }, { card_number: 'synthetic_not_a_card' }, { control_label: 'Pay $5.05' }]) {
    const s = setup(); s.api.arm(s.binding);
    await assert.rejects(s.api.dispatch(s.permit('open_cart', 10), { ...s.screen('open_cart'), ...patch }));
    assert.deepEqual(s.effects, []);
  }
});
test('click exception or expiry after possible effect freezes with no retry', async () => {
  const s = setup({ click: async () => { throw new Error('unknown'); } }); s.api.arm(s.binding);
  await assert.rejects(s.api.dispatch(s.permit('open_cart', 10), s.screen('open_cart')), /uncertain_no_retry/);
  assert.equal(s.api.status().frozen, true);
  await assert.rejects(s.api.dispatch(s.permit('open_cart', 11), s.screen('open_cart')), /not_live/);
  const t = setup(); t.api.arm(t.binding); t.advance(60000);
  await assert.rejects(t.api.dispatch(t.permit('open_cart', 10), t.screen('open_cart')), /not_live/);
  assert.deepEqual(t.effects, []);
});
test('freeze or cancellation during an awaited observation prevents later screenshot', async () => {
  let release; const waiting = new Promise(resolve => { release = resolve; });
  const s = setup({ getAXState: async () => waiting }); s.api.arm(s.binding);
  const read = s.api.observe(); s.api.cancel(); release();
  await assert.rejects(read, /not_live/);
  assert.deepEqual(s.effects, []);
});
test('later stages require exact document and observed control binding', async () => {
  for (const patch of [{ from_document_ref: null }, { from_document_ref: id(90) },
    { control_ref: id(91) }, { cart_fingerprint: 'c'.repeat(64) }]) {
    const s = setup(); s.api.arm(s.binding);
    await s.api.dispatch(s.permit('open_cart', 10), s.screen('open_cart'));
    const before = s.effects.length;
    await assert.rejects(s.api.dispatch({ ...s.permit('go_to_checkout', 11), ...patch }, s.screen('go_to_checkout')));
    assert.equal(s.effects.length, before);
  }
});
test('secure handoff waits for an in-flight screenshot and suppresses its late return', async () => {
  let release, started;
  const waiting = new Promise(resolve => { release = resolve; });
  const begun = new Promise(resolve => { started = resolve; });
  const s = setup({ getScreenshot: async () => { started(); return waiting; } });
  s.api.arm(s.binding);
  const read = s.api.observe(); await begun;
  let handedOff = false;
  const handoff = s.api.freezeForEntry().then(() => { handedOff = true; });
  await Promise.resolve(); assert.equal(handedOff, false);
  await assert.rejects(s.api.observe(), /not_live/);
  release('pre-entry-screenshot');
  await assert.rejects(read, /not_live/); await handoff;
  assert.equal(handedOff, true);
  assert.equal(s.effects.at(-1)[0], 'markHandoff');
});

let currentSerial = 0;
function currentSetup({ patch = {}, grantPatch = {}, factory = makeCurrentCheckoutCuaActor } = {}) {
  const serial = ++currentSerial;
  const effects = [];
  const view = Object.fromEntries(['click', 'getAXState', 'getScreenshot', 'markHandoff']
    .map(name => [name, async (...args) => { effects.push([name, ...args]); return `${name}-capture`; }]));
  Object.assign(view, patch);
  let now = NOW;
  const scope = { intent_id: id(101), approval_ref: id(102), release_id: 3422100, listing_id: '1366950910',
    seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05', rules_revision: 2,
    quote_fingerprint: 'a'.repeat(64), destination_ref: id(103), terms_fingerprint: 'b'.repeat(64),
    issued_at: NOW / 1000, expires_at: NOW / 1000 + 300 };
  const permit = { type: 'operator_action', intent_id: scope.intent_id, ready_ref: id(104), scope_digest: 'c'.repeat(64),
    request_id: id(1000 + serial), stage: 'open_guest_card', deadline: NOW + 120000, from_document_ref: id(106),
    control_ref: id(107), inspection_fingerprint: 'd'.repeat(64) };
  const grant = { type: 'checkout_claim_granted', claim_sha256: serial.toString(16).padStart(64, '0'), permit, expected_scope: scope,
    order_status: 'unverified', payment_status: 'unverified', autonomous_checkout: false, requires_execution_revalidation: true,
    human_payment_ready: false, card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false, ...grantPatch };
  const api = factory(view, grant, { clock: () => now });
  const proof = observed => ({ source: 'operator_current_screen', observation_id: observed.observation_id,
    claim_sha256: grant.claim_sha256, intent_id: permit.intent_id, ready_ref: permit.ready_ref, scope_digest: permit.scope_digest,
    url: 'https://www.discogs.com/marketplace/checkout', document_ref: permit.from_document_ref, control_ref: permit.control_ref,
    inspection_fingerprint: permit.inspection_fingerprint, control_label: 'Debit or Credit Card', control_count: 1,
    control_visible: true, control_enabled: true, point: [350, 550], approval_scope_verified: true,
    item_scope_verified: true, quote_verified: true, destination_verified: true, terms_verified: true, no_card_data_entered: true,
    normal_checkout: true, history_restore: false, resubmission_prompt: false, extra_agreement_required: false,
    challenge_present: false, release_id: scope.release_id, listing_id: scope.listing_id, seller: scope.seller,
    quantity: 1, item_count: 1, seller_count: 1, rules_revision: scope.rules_revision, approval_ref: scope.approval_ref,
    currency: 'USD', item: '0.10', shipping: '4.95', total: '5.05', quote_fingerprint: scope.quote_fingerprint,
    destination_ref: scope.destination_ref, terms_fingerprint: scope.terms_fingerprint, authorization_current: true,
    authorization_revoked: false, already_owned: false, existing_order: false, uncertain_prior_action: false,
    duplicate_guard_verified: true, rules_current: true, criteria_satisfied: true, shipping_selection_verified: true,
    preparation_quote_verified: true, final_payment_quote_verified: false, deferred_total_notice: false,
    amount_basis: 'explicit_merchant_checkout_total' });
  return { api, view, grant, scope, permit, effects, proof, advance: amount => { now += amount; } };
}
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test('current-checkout actor serializes without module dependencies and captures actual supported view', async () => {
  const { runInThisContext } = require('node:vm');
  const factory = runInThisContext(`(${makeCurrentCheckoutCuaActor.toString()})`);
  const s = currentSetup({ factory }); assert.deepEqual(s.effects, []);
  const observed = await s.api.observe();
  assert.equal(observed.screenshot, 'getScreenshot-capture'); assert.equal(observed.ax_state, 'getAXState-capture');
  assert.equal(observed.observed_at, NOW / 1000);
  assert.deepEqual(s.effects, [['getAXState', { emit: false }], ['getScreenshot', { emit: false }]]);
  s.advance(20000);
  assert.equal((await s.api.dispatch(s.proof(observed))).status, 'effect_requires_screen_observation');
  assert.deepEqual(s.effects.at(-1), ['click', [350, 550]]);
  assert.equal(s.api.status().consumed, true); assert.equal(s.api.status().frozen, true);
  await assert.rejects(s.api.dispatch(s.proof(observed)), /not_live/);
  await assert.rejects(s.api.observe(), /not_live/);
  assert.equal(s.effects.filter(v => v[0] === 'click').length, 1);
});

test('current-checkout constructor rejects malformed, widened and expired grants before I/O', () => {
  for (const patch of [{ requires_execution_revalidation: false }, { card_entry_allowed: true },
    { payment_allowed: true }, { final_submission_allowed: true }, { human_payment_ready: true },
    { autonomous_checkout: true }, { claim_sha256: 'bad' }, { expected_scope: null }, { unexpected: true }]) {
    assert.throws(() => currentSetup({ grantPatch: patch }), /grant_unverified/);
  }
  const base = currentSetup();
  const variants = [g => { g.permit.deadline = NOW; }, g => { g.permit.deadline = NOW + 300001; },
    g => { g.permit.intent_id = id(999); }, g => { g.permit.stage = 'go_to_checkout'; },
    g => { g.expected_scope.listing_id = '1366950911'; }, g => { g.expected_scope.release_id++; },
    g => { g.expected_scope.seller = 'other'; }, g => { g.expected_scope.quantity = 2; },
    g => { g.expected_scope.max_total = '5.06'; }, g => { g.expected_scope.expires_at += 1; },
    g => { g.expected_scope.raw_address = 'not accepted'; }, g => { g.permit.control_ref = null; }];
  for (const mutate of variants) {
    const grant = structuredClone(base.grant); mutate(grant);
    assert.throws(() => makeCurrentCheckoutCuaActor(base.view, grant, { clock: () => NOW }), /grant_unverified/);
  }
  const getter = { ...base.grant };
  Object.defineProperty(getter, 'permit', { enumerable: true, get() { throw new Error('getter_called'); } });
  assert.throws(() => makeCurrentCheckoutCuaActor(base.view, getter, { clock: () => NOW }), /grant_unverified/);
  assert.deepEqual(base.effects, []);
});

test('current-checkout proof rejects stale or caller-renewed timestamps and every changed binding', async () => {
  const changes = { source: 'other', observation_id: 'invented', claim_sha256: 'f'.repeat(64), intent_id: id(999),
    ready_ref: id(999), scope_digest: 'f'.repeat(64), url: 'https://www.discogs.com/marketplace/checkout?history=1',
    document_ref: id(999), control_ref: id(999), inspection_fingerprint: 'f'.repeat(64), control_label: 'Pay',
    control_count: 2, control_visible: false, control_enabled: false, point: [-1, 1],
    approval_scope_verified: false, item_scope_verified: false, quote_verified: false, destination_verified: false,
    terms_verified: false, no_card_data_entered: false, normal_checkout: false, history_restore: true,
    resubmission_prompt: true, extra_agreement_required: true, challenge_present: true, release_id: 1,
    listing_id: 'other', seller: 'other', quantity: 2, item_count: 2, seller_count: 2, rules_revision: 3,
    approval_ref: id(999), currency: 'EUR', item: '0.11', shipping: '4.94', total: '5.06', quote_fingerprint: 'f'.repeat(64),
    destination_ref: id(999), terms_fingerprint: 'f'.repeat(64), authorization_current: false, authorization_revoked: true,
    already_owned: true, existing_order: true, uncertain_prior_action: true, duplicate_guard_verified: false,
    rules_current: false, criteria_satisfied: false, shipping_selection_verified: false,
    preparation_quote_verified: false, final_payment_quote_verified: true, deferred_total_notice: true,
    amount_basis: 'estimate', observed_at: NOW / 1000, card_number: 'not accepted', address: 'not accepted' };
  for (const [key, value] of Object.entries(changes)) {
    const s = currentSetup(), observed = await s.api.observe();
    await assert.rejects(s.api.dispatch({ ...s.proof(observed), [key]: value }), /screen_unverified/, key);
    assert.equal(s.effects.some(v => v[0] === 'click'), false, key);
  }
  const s = currentSetup(), observed = await s.api.observe(); s.advance(20001);
  await assert.rejects(s.api.dispatch(s.proof(observed)), /screen_unverified/);
  assert.equal(s.effects.some(v => v[0] === 'click'), false);
});

test('current-checkout repeated observations invalidate old IDs and never renew the fixed claim deadline', async () => {
  const s = currentSetup(), first = await s.api.observe(); s.advance(21000);
  const second = await s.api.observe(); assert.notEqual(second.observation_id, first.observation_id);
  await assert.rejects(s.api.dispatch(s.proof(first)), /screen_unverified/);
  assert.equal((await s.api.dispatch(s.proof(second))).status, 'effect_requires_screen_observation');
  const t = currentSetup(), captured = await t.api.observe();
  t.grant.permit.deadline = NOW + 300000; t.scope.expires_at += 300;
  assert.equal(t.api.status().deadline, NOW + 120000);
  t.advance(120000);
  await assert.rejects(t.api.observe(), /not_live/);
  await assert.rejects(t.api.dispatch(t.proof(captured)), /not_live/);
  assert.equal(t.effects.some(v => v[0] === 'click'), false);
});

test('current-checkout cancellation, expiry and superseded capture cannot authorize a late click', async () => {
  for (const method of ['getAXState', 'getScreenshot']) {
    const waiting = deferred(), started = deferred();
    const s = currentSetup({ patch: { [method]: async () => { started.resolve(); return waiting.promise; } } });
    const read = s.api.observe(); await started.promise;
    await assert.rejects(s.api.observe(), /busy/);
    s.api.cancel(); waiting.resolve('late-capture');
    await assert.rejects(read, /not_live/);
    assert.equal(s.effects.some(v => v[0] === 'click'), false);
    if (method === 'getAXState') assert.equal(s.effects.some(v => v[0] === 'getScreenshot'), false);
  }
  const waiting = deferred(), started = deferred();
  const s = currentSetup({ patch: { getScreenshot: async () => { started.resolve(); return waiting.promise; } } });
  const read = s.api.observe(); await started.promise; s.advance(120000); waiting.resolve('late-capture');
  await assert.rejects(read, /not_live/); assert.equal(s.api.status().frozen, true);
  const t = currentSetup(), observed = await t.api.observe(); t.api.cancel();
  await assert.rejects(t.api.dispatch(t.proof(observed)), /not_live/);
});

test('current-checkout freshness starts before delayed capture and clock rollback fails closed', async () => {
  let s;
  s = currentSetup({ patch: { getScreenshot: async () => { s.advance(20001); return 'delayed'; } } });
  const observed = await s.api.observe(); assert.equal(observed.observed_at, NOW / 1000);
  await assert.rejects(s.api.dispatch(s.proof(observed)), /screen_unverified/);
  const t = currentSetup(), old = await t.api.observe(); t.advance(-1);
  await assert.rejects(t.api.dispatch(t.proof(old)), /not_live/);
  assert.equal(t.api.status().frozen, true);
});

test('current-checkout consumes before awaited click and freezes every uncertain outcome without retry', async () => {
  for (const outcome of ['reject', 'cancel', 'expire']) {
    const click = deferred(), started = deferred(); let clicks = 0;
    const s = currentSetup({ patch: { click: async () => { clicks++; started.resolve(); return click.promise; } } });
    const observed = await s.api.observe(), action = s.api.dispatch(s.proof(observed)); await started.promise;
    assert.equal(s.api.status().consumed, true);
    await assert.rejects(s.api.dispatch(s.proof(observed)), /not_live/);
    if (outcome === 'cancel') s.api.cancel();
    if (outcome === 'expire') s.advance(120000);
    if (outcome === 'reject') click.reject(new Error('unknown effect')); else click.resolve();
    await assert.rejects(action, /uncertain_no_retry/);
    assert.equal(s.api.status().reason, 'effect_outcome_uncertain');
    assert.equal(s.api.status().frozen, true); assert.equal(clicks, 1);
    await assert.rejects(s.api.dispatch(s.proof(observed)), /not_live/);
  }
});

test('current-checkout handoff drains pending captures and suppresses their return', async () => {
  const screenshot = deferred(), started = deferred();
  const s = currentSetup({ patch: { getScreenshot: async () => { started.resolve(); return screenshot.promise; } } });
  const read = s.api.observe(); await started.promise;
  let completed = false;
  const freeze = s.api.freezeForEntry().then(() => { completed = true; });
  await Promise.resolve(); assert.equal(completed, false);
  screenshot.resolve('late'); await assert.rejects(read, /not_live/); await freeze;
  assert.equal(s.effects.at(-1)[0], 'markHandoff'); assert.equal(s.api.status().frozen, true);
});

test('current-checkout structured metadata rejects accessors and symbols without reading them', async () => {
  for (const mutate of [p => {
    Object.defineProperty(p, 'total', { enumerable: true, get() { throw new Error('must_not_read'); } });
  }, p => { p[Symbol('unexpected')] = true; }, p => {
    Object.defineProperty(p.point, '0', { enumerable: true, get() { throw new Error('must_not_read'); } });
  }]) {
    const s = currentSetup(), observed = await s.api.observe(), proof = s.proof(observed); mutate(proof);
    await assert.rejects(s.api.dispatch(proof), /screen_unverified/);
    assert.equal(s.effects.some(v => v[0] === 'click'), false);
  }
  const s = currentSetup(), observed = await s.api.observe(), proof = s.proof(observed);
  // Array iterators are not trusted to choose the dispatched coordinates.
  Object.setPrototypeOf(proof.point, { [Symbol.iterator]: function* () { yield 1; yield 2; } });
  await s.api.dispatch(proof);
  assert.deepEqual(s.effects.at(-1), ['click', [350, 550]]);
});

test('current-checkout handoff waits for pending click and treats cancellation as uncertain', async () => {
  const click = deferred(), started = deferred();
  const s = currentSetup({ patch: { click: async () => { started.resolve(); return click.promise; } } });
  const action = s.api.dispatch(s.proof(await s.api.observe())); await started.promise;
  let completed = false;
  const freeze = s.api.freezeForEntry().then(() => { completed = true; });
  await Promise.resolve(); assert.equal(completed, false);
  click.resolve(); await assert.rejects(action, /uncertain_no_retry/); await freeze;
  assert.equal(s.effects.at(-1)[0], 'markHandoff');
  assert.equal(s.api.status().reason, 'effect_outcome_uncertain');
});

test('one installed serialized factory reserves claim and request across actor instances and all outcomes', async () => {
  const { runInThisContext } = require('node:vm');
  const factory = runInThisContext(`(${makeCurrentCheckoutCuaActor.toString()})`);
  const s = currentSetup({ factory });
  const construct = grant => factory(s.view, grant, { clock: () => NOW });
  assert.throws(() => construct(structuredClone(s.grant)), /already_reserved/);
  assert.throws(() => construct({ ...s.grant, claim_sha256: '9'.repeat(64) }), /already_reserved/);
  assert.throws(() => construct({ ...s.grant, permit: { ...s.permit, request_id: id(990) } }), /already_reserved/);
  await s.api.dispatch(s.proof(await s.api.observe()));
  assert.throws(() => construct(s.grant), /already_reserved/);
  assert.equal(s.effects.filter(v => v[0] === 'click').length, 1);
  const registry = Object.getOwnPropertyDescriptor(factory, 'currentCheckoutReservations');
  assert.equal(registry.enumerable, false); assert.equal(registry.writable, false); assert.equal(registry.configurable, false);
  for (const stop of ['cancel', 'expire']) {
    const t = currentSetup({ factory });
    if (stop === 'cancel') t.api.cancel(); else { t.advance(120000); await assert.rejects(t.api.observe(), /not_live/); }
    assert.throws(() => factory(t.view, t.grant, { clock: () => NOW }), /already_reserved/);
  }
});

test('invalid construction does not reserve a claim or request before validation', () => {
  const { runInThisContext } = require('node:vm');
  const factory = runInThisContext(`(${makeCurrentCheckoutCuaActor.toString()})`);
  const source = currentSetup(), invalid = { ...source.grant, card_entry_allowed: true };
  assert.throws(() => factory(source.view, invalid, { clock: () => NOW }), /grant_unverified/);
  assert.equal(Object.hasOwn(factory, 'currentCheckoutReservations'), false);
  const valid = factory(source.view, source.grant, { clock: () => NOW });
  assert.equal(valid.status().consumed, false); assert.deepEqual(source.effects, []);
});
