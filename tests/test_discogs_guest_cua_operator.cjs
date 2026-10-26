'use strict';
const assert = require('node:assert/strict');
const { before, test } = require('node:test');
let makeCuaGuestOperator;
before(async () => ({ makeCuaGuestOperator } = await import('../scripts/discogs_guest_cua_operator.mjs')));
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
