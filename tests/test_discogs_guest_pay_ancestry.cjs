'use strict';
// Offline synthetic Frame/Document graphs only; never a live provider profile.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
let startGuestPayAncestryDiagnostic, sanitizeGuestPayAncestryDiagnostic, collectGuestPayAncestryControlDom;
before(async () => ({ startGuestPayAncestryDiagnostic, sanitizeGuestPayAncestryDiagnostic, collectGuestPayAncestryControlDom } =
  await import('../scripts/discogs_guest_pay_ancestry.mjs')));
const CHECKOUT = 'https://www.discogs.com/marketplace/checkout';
function fixture() {
  const all = [], doms = [], calls = [];
  function frame(url, parent = null, submitCount = 0) {
    const state = { url, parent, visible: true, attached: true, owner: parent, handleHook: null, queryHook: null, sameHook: null };
    function makeDom(value) {
      const dom = new JSDOM(`<html><body>${'<button id="submit-button" type="button">Public</button>'.repeat(submitCount)}
        <aside>TOP_SECRET_ADDRESS</aside><input value="TOP_SECRET_CARD"><input type="hidden" value="TOP_SECRET_TOKEN"></body></html>`,
      { url: value, runScripts: 'outside-only' });
      Object.defineProperty(dom.window.document, 'readyState', { get: () => 'complete' });
      Object.defineProperty(dom.window.HTMLInputElement.prototype, 'value', { get() { throw new Error('Forbidden input values'); } });
      const query = dom.window.document.querySelectorAll.bind(dom.window.document);
      dom.window.document.querySelectorAll = selector => {
        calls.push({ kind: 'query', state, selector }); state.queryHook?.();
        assert.equal(selector, 'button#submit-button[type="button"]'); return query(selector);
      };
      for (const method of ['click', 'focus']) dom.window.HTMLElement.prototype[method] = () => { throw new Error('Forbidden effect'); };
      dom.window.fetch = () => { throw new Error('Forbidden HTTP'); }; doms.push(dom); return dom;
    }
    state.dom = makeDom(url);
    const element = { isVisible: async () => state.visible, contentFrame: async () => state.attached ? result : null,
      ownerFrame: async () => state.owner, dispose: async () => {} };
    const result = { url: () => state.url, parentFrame: () => state.parent, frameElement: async () => element,
      evaluateHandle: async (fn, arg) => { calls.push({ kind: 'handle', state }); await state.handleHook?.(); return state.dom.window.eval(`(${fn.toString()})`)(arg); },
      evaluate: async (fn, arg) => { calls.push({ kind: fn === collectGuestPayAncestryControlDom ? 'control' : 'same', state });
        const value = state.dom.window.eval(`(${fn.toString()})`)(arg);
        if (fn !== collectGuestPayAncestryControlDom) await state.sameHook?.();
        return value; } };
    state.frame = result; state.element = element;
    state.replaceDocument = () => { state.dom = makeDom(state.url); };
    state.redirect = url => { state.url = url; state.dom.reconfigure({ url }); };
    all.push(state); return state;
  }
  const main = frame(CHECKOUT), buttons = frame('https://www.paypal.com/smart/buttons?token=TOP_SECRET_BUTTONS', main.frame);
  let inventory = [main.frame, buttons.frame];
  const page = { url: main.frame.url, evaluate: main.frame.evaluate, evaluateHandle: main.frame.evaluateHandle, frames: () => inventory };
  const read = async operation => operation(), readHandle = read;
  const association = { value: true, frame: buttons.frame, element: buttons.element };
  const add = (url, parent, controls = 0) => { const value = frame(url, parent, controls); inventory.push(value.frame); return value; };
  const begin = (nestedFrames, overrides = {}) => startGuestPayAncestryDiagnostic(page, { frames: inventory,
    main: main.frame, mainDocument: main.dom.window.document, nestedFrames, buttonsAssociation: association, read, readHandle, ...overrides });
  return { main, buttons, page, add, begin, calls, association, all, get inventory() { return inventory; },
    setInventory: value => { inventory = value; }, close: () => doms.forEach(dom => dom.window.close()) };
}
const safe = value => { const text = JSON.stringify(value); assert.equal(/TOP_SECRET|https?:|token|input|ADDRESS|CARD/u.test(text), false); };

test('nested visible PayPal chain reports actual verified buttons ancestor and fixed public count without secrets', async () => {
  const f = fixture(); const guest = f.add('https://www.paypal.com/fixture-unqualified?secret=TOP_SECRET#TOP_SECRET', f.buttons.frame, 1);
  try {
    const report = await (await f.begin([guest.frame]))();
    assert.equal(report.status, 'observed'); assert.equal(report.reason, 'complete'); assert.equal(report.candidate_count, 1);
    const candidate = report.candidates[0]; assert.equal(candidate.depth, 2); assert.equal(candidate.stop, 'merchant_root');
    assert.equal(candidate.public_submit_count, 1); assert.equal(candidate.buttons_ancestor_verified, true);
    assert.deepEqual(candidate.hops.map(hop => [hop.child_category, hop.parent_category]),
      [['paypal_other', 'paypal_buttons'], ['paypal_buttons', 'merchant_checkout']]);
    assert.equal(f.calls.filter(call => call.kind === 'query').length, 1); safe(report);
    assert.deepEqual(sanitizeGuestPayAncestryDiagnostic(report), report);
  } finally { f.close(); }
});

test('unrelated known buttons sibling is never reported as ancestor', async () => {
  const f = fixture(), parent = f.add('https://www.paypal.com/smart/fixture-parent', f.main.frame), guest = f.add('https://www.paypal.com/fixture-guest', parent.frame, 1);
  try {
    const report = await (await f.begin([guest.frame]))();
    assert.equal(report.status, 'observed'); assert.equal(report.candidates[0].buttons_ancestor_verified, false);
    assert.equal(report.candidates[0].hops[1].child_category, 'paypal_smart_other'); safe(report);
  } finally { f.close(); }
});

test('unknown, outside-inventory, missing and cyclic parents stop before any nested DOM/control read', async () => {
  for (const mode of ['unknown', 'outside', 'missing', 'cycle']) {
    const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', null, 1);
    if (mode === 'unknown') guest.parent = f.add('https://unknown.example/private?token=TOP_SECRET', f.main.frame).frame;
    else if (mode === 'outside') guest.parent = { url() { throw new Error('outside URL forbidden'); } };
    else if (mode === 'cycle') guest.parent = guest.frame;
    try {
      const report = await (await f.begin([guest.frame]))();
      assert.equal(report.status, 'incomplete'); assert.equal(report.candidates[0].stop,
        { unknown: 'unknown_origin', outside: 'outside_inventory', missing: 'missing_parent', cycle: 'cycle' }[mode]);
      assert.equal(f.calls.some(call => ['handle', 'control', 'query'].includes(call.kind)), false); safe(report);
    } finally { f.close(); }
  }
});

test('hidden, content-frame and owner mismatches remain incomplete and never query controls', async () => {
  for (const mode of ['hidden', 'content', 'owner']) {
    const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', f.buttons.frame, 1);
    if (mode === 'hidden') guest.visible = false;
    if (mode === 'content') guest.attached = false;
    if (mode === 'owner') guest.owner = f.main.frame;
    try {
      const report = await (await f.begin([guest.frame]))();
      assert.equal(report.status, 'incomplete'); assert.equal(report.candidates[0].stop, 'association_mismatch'); assert.equal(report.candidates[0].public_submit_count, null);
      assert.equal(f.calls.some(call => call.kind === 'query'), false); safe(report);
    } finally { f.close(); }
  }
});

test('multiple public controls and multiple distinct public-control chains are ambiguous diagnostics only', async () => {
  for (const mode of ['two-in-one', 'two-candidates']) {
    const f = fixture(), first = f.add('https://www.paypal.com/fixture-first', f.buttons.frame, mode === 'two-in-one' ? 2 : 1);
    const candidates = [first.frame]; if (mode === 'two-candidates') candidates.push(f.add('https://www.paypal.com/fixture-second', f.buttons.frame, 1).frame);
    try { const report = await (await f.begin(candidates))(); assert.equal(report.status, 'incomplete'); assert.equal(report.reason, 'ambiguous_controls'); safe(report); }
    finally { f.close(); }
  }
});

test('four nested-candidate and four-edge bounds do not inspect arbitrary first candidates', async () => {
  const f = fixture(); const candidates = Array.from({ length: 5 }, (_, i) => f.add(`https://www.paypal.com/fixture-${i}`, f.buttons.frame, 1).frame);
  try { const report = await (await f.begin(candidates))(); assert.equal(report.reason, 'candidate_limit');
    assert.equal(report.candidate_count, 5); assert.deepEqual(report.candidates, []); assert.equal(f.calls.length, 0); safe(report); }
  finally { f.close(); }
  const g = fixture(); let parent = g.buttons.frame;
  for (let i = 0; i < 4; i++) parent = g.add(`https://www.paypal.com/deep-${i}`, parent, 1).frame;
  try { const report = await (await g.begin([parent]))(); assert.equal(report.candidates[0].stop, 'depth_limit');
    assert.equal(report.candidates[0].depth, 4); assert.equal(g.calls.some(call => call.kind === 'query'), false); }
  finally { g.close(); }
});

test('inventory cap32 and invalid candidate membership fail without any DOM reads', async () => {
  const f = fixture(); for (let i = 0; i < 31; i++) f.add(`https://unknown.example/${i}`, f.main.frame);
  try { const report = await (await f.begin([]))(); assert.equal(report.reason, 'inventory_limit'); assert.equal(report.inventory_count, 32);
    assert.equal(f.calls.length, 0); assert.deepEqual(sanitizeGuestPayAncestryDiagnostic(report), report); }
  finally { f.close(); }
  const g = fixture();
  try { const report = await (await g.begin([{}]))(); assert.equal(report.reason, 'invalid_candidates'); assert.equal(g.calls.length, 0); }
  finally { g.close(); }
});

test('late document, parent, visibility, ownership, URL and inventory changes invalidate captured ancestry', async () => {
  for (const mode of ['child-doc', 'parent-doc', 'main-doc', 'parent', 'visibility', 'owner', 'content', 'url', 'inventory', 'mutable-inventory']) {
    const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest?token=TOP_SECRET', f.buttons.frame, 1);
    try {
      const finalize = await f.begin([guest.frame]);
      if (mode === 'child-doc') guest.replaceDocument();
      if (mode === 'parent-doc') f.buttons.replaceDocument();
      if (mode === 'main-doc') f.main.replaceDocument();
      if (mode === 'parent') guest.parent = f.main.frame;
      if (mode === 'visibility') guest.visible = false;
      if (mode === 'owner') guest.owner = f.main.frame;
      if (mode === 'content') guest.attached = false;
      if (mode === 'url') guest.redirect('https://unknown.example/private?token=TOP_SECRET');
      if (mode === 'inventory') f.setInventory([...f.inventory].reverse());
      if (mode === 'mutable-inventory') f.inventory.push({});
      const report = await finalize(); assert.equal(report.status, 'changed', mode);
      assert.equal(report.candidates[0].public_submit_count, null, mode); assert.equal(report.candidates[0].buttons_ancestor_verified, false, mode); safe(report);
    } finally { f.close(); }
  }
});

test('late final-document proof cannot preserve a newly hidden, detached or reparented ancestry edge', async () => {
  for (const mode of ['leaf-hidden', 'leaf-detached', 'leaf-reparented', 'leaf-owner', 'ancestor-hidden', 'ancestor-detached']) {
    const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', f.buttons.frame, 1);
    try {
      const finalize = await f.begin([guest.frame]); let mainProofs = 0;
      f.main.sameHook = () => {
        if (++mainProofs !== 2) return;
        if (mode === 'leaf-hidden') guest.visible = false;
        if (mode === 'leaf-detached') guest.attached = false;
        if (mode === 'leaf-reparented') guest.parent = f.main.frame;
        if (mode === 'leaf-owner') guest.owner = f.main.frame;
        if (mode === 'ancestor-hidden') f.buttons.visible = false;
        if (mode === 'ancestor-detached') f.buttons.attached = false;
      };
      const report = await finalize(); assert.equal(mainProofs, 2, mode);
      assert.equal(report.status, 'changed', mode); assert.equal(report.reason, 'inventory_changed', mode);
      assert.equal(report.candidates[0].chain_complete, false, mode);
      assert.equal(report.candidates[0].buttons_ancestor_verified, false, mode);
      assert.equal(report.candidates[0].public_submit_count, null, mode); safe(report);
    } finally { f.close(); }
  }
});

test('redirect while document handle awaits cannot query forbidden replacement document', async () => {
  const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', f.buttons.frame, 1);
  guest.handleHook = () => guest.redirect('https://unknown.example/private?token=TOP_SECRET');
  try { const report = await (await f.begin([guest.frame]))(); assert.notEqual(report.status, 'observed');
    assert.equal(f.calls.some(call => call.kind === 'query'), false); safe(report); }
  finally { f.close(); }
});

test('API failures are sanitized while caller cancellation and deadline rejections propagate unchanged', async () => {
  const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', f.buttons.frame, 1);
  guest.frame.frameElement = async () => { throw new Error('TOP_SECRET_PROVIDER_FAILURE'); };
  try { const report = await (await f.begin([guest.frame]))(); assert.notEqual(report.status, 'observed'); safe(report); }
  finally { f.close(); }
  for (const code of ['read_cancelled', 'read_timeout']) {
    const g = fixture(), child = g.add('https://www.paypal.com/fixture-child', g.buttons.frame, 1), stop = Object.assign(new Error(code), { [Symbol('private_stop')]: code });
    try { await assert.rejects(g.begin([child.frame], { read: async () => { throw stop; } }), error => error === stop); }
    finally { g.close(); }
  }
  const h = fixture(), child = h.add('https://www.paypal.com/fixture-child', h.buttons.frame, 1);
  let aborted = false; const stop = new Error('read_cancelled');
  const read = async operation => { if (aborted) throw stop; return operation(); };
  try { const final = await h.begin([child.frame], { read, readHandle: read }); aborted = true; await assert.rejects(final(), error => error === stop); }
  finally { h.close(); }
});

test('strict sanitizer rejects getters, extra fields, arbitrary text and contradictory depth/count/chain claims', async () => {
  const f = fixture(), guest = f.add('https://www.paypal.com/fixture-guest', f.buttons.frame, 1);
  try {
    const valid = await (await f.begin([guest.frame]))();
    const mutations = [value => { value.secret = 'TOP_SECRET'; }, value => { value.reason = 'https://private.example'; },
      value => { value.candidate_count = 5; }, value => { value.inventory_count = 33; }, value => { value.candidates[0].depth = 3; },
      value => { value.candidates[0].public_submit_count = 3; }, value => { value.candidates[0].hops[0].parent_category = 'https://private.example'; },
      value => { value.candidates[0].hops[0].visible = false; }, value => { value.candidates[0].buttons_ancestor_verified = true; value.candidates[0].hops[0].parent_category = 'paypal_other'; },
      value => { value.candidates[0].stop = 'cycle'; }, value => { value.status = 'changed'; value.reason = 'inventory_changed'; },
      value => { value.status = 'unsupported'; value.reason = 'inspection_error'; }, value => { value.status = 'limit'; }, value => { value.candidates[0].hops.push({}); }];
    for (const mutate of mutations) { const value = structuredClone(valid); mutate(value); assert.equal(sanitizeGuestPayAncestryDiagnostic(value), null); }
    const value = structuredClone(valid); Object.defineProperty(value, 'reason', { enumerable: true, get() { throw new Error('Getter forbidden'); } });
    assert.equal(sanitizeGuestPayAncestryDiagnostic(value), null);
    const nestedGetter = structuredClone(valid); Object.defineProperty(nestedGetter.candidates[0].hops[0], 'visible', { enumerable: true, get() { throw new Error('Getter forbidden'); } });
    assert.equal(sanitizeGuestPayAncestryDiagnostic(nestedGetter), null);
    const arrayGetter = structuredClone(valid); Object.defineProperty(arrayGetter.candidates, '0', { enumerable: true, get() { throw new Error('Getter forbidden'); } });
    assert.equal(sanitizeGuestPayAncestryDiagnostic(arrayGetter), null);
    safe(valid);
  } finally { f.close(); }
});
