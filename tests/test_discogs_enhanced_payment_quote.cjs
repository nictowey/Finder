'use strict';
// Observed merchant summary shape; all provider contracts below are synthetic
// unqualified facts, never a live payment or a qualified provider DOM fixture.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
const { randomUUID } = require('node:crypto');
let collectEnhancedPaymentQuoteDom, inspectCurrentEnhancedPaymentQuote,
  parseEnhancedPaymentQuoteEvidence, waitForEnhancedPaymentQuoteSettlement, collectGuestPayControlMetadataDom, sanitizeGuestPayMetadata, isVerifiedCurrentEnhancedPaymentQuote;
before(async () => ({ collectEnhancedPaymentQuoteDom, inspectCurrentEnhancedPaymentQuote,
  parseEnhancedPaymentQuoteEvidence, waitForEnhancedPaymentQuoteSettlement, collectGuestPayControlMetadataDom, sanitizeGuestPayMetadata, isVerifiedCurrentEnhancedPaymentQuote } = await import('../scripts/discogs_enhanced_payment_quote.mjs')));
const URL = 'https://www.discogs.com/marketplace/checkout';
const HTML = `<!doctype html><html><head><title>Checkout</title></head><body>
  <h1>Checkout from ExampleSeller</h1><div class="_checkout-summary__content_fixture">
    <ul class="_checkout-summary__unordered-list_fixture"><li class="_checkout-sell-item__list-item_fixture">TOP_SECRET_ITEM_TEXT</li></ul>
    <dl class="_checkout-summary__totals_fixture">
      <div class="_checkout-summary__subtotal_fixture"><dt>Subtotal</dt><dd class="_checkout-summary__money-value_fixture">$0.10 USD</dd></div>
      <div class="_checkout-summary__subtotal_fixture"><dt>Shipping via Economy - Media Mail</dt><dd class="_checkout-summary__money-value_fixture">$4.95 USD</dd></div>
      <div class="_checkout-summary__total--color-red_fixture"><dt>Total</dt><dd class="_checkout-summary__money-value_fixture">$5.05 USD</dd></div>
    </dl></div><aside>TOP_SECRET_ADDRESS</aside>
    <input name="cardNumber" value="TOP_SECRET_PAN"><input name="securityCode" value="TOP_SECRET_CVV">
    <input name="expiry" value="TOP_SECRET_EXPIRY"><input name="email" value="TOP_SECRET_ACCOUNT">
    <label><input type="checkbox" checked>Ship to billing address</label><button>Pay $5.05</button>
  </body></html>`;
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function fixture(html = HTML) {
  const dom = new JSDOM(html, { url: URL, runScripts: 'outside-only' });
  dom.window.Element.prototype.getClientRects = () => [{ width: 100, height: 20 }];
  for (const key of ['value', 'checked']) Object.defineProperty(dom.window.HTMLInputElement.prototype, key, { get() { throw new Error('Forbidden payment input read'); } });
  for (const method of ['click', 'focus']) dom.window.HTMLElement.prototype[method] = () => { throw new Error('Forbidden control effect'); };
  dom.window.fetch = () => { throw new Error('Forbidden network'); };
  const calls = [], documentHandle = { dispose: async () => calls.push('dispose') }, main = { parentFrame: () => null };
  let currentDocument = documentHandle, url = URL;
  const page = { url: () => { calls.push('url'); return url; },
    evaluateHandle: async () => { calls.push('document_handle'); return currentDocument; },
    evaluate: async (fn, arg) => {
      if (fn === collectEnhancedPaymentQuoteDom) { calls.push('merchant_dom'); return dom.window.eval(`(${fn.toString()})()`); }
      if (fn === collectGuestPayControlMetadataDom) { calls.push('public_pay_metadata');
        return dom.window.eval(`(${fn.toString()})`)({ ...arg, expectedDocument: arg.expectedDocument === currentDocument ? dom.window.document : {} }); }
      if (fn.name === 'sameKnownDocument') return arg.expectedDocument === currentDocument && url === arg.url;
      calls.push('same_document'); assert.equal(arg, documentHandle); return currentDocument === documentHandle;
    }, frames: () => { calls.push('frames'); return []; }, mainFrame: () => main };
  return { page, calls, dom, main, documentHandle, changeDocument: () => { currentDocument = {}; }, changeUrl: value => { url = value; } };
}
function input(now = 1000) {
  const documentRef = randomUUID();
  return { merchant: { schema: 'discogs_enhanced_checkout_dom_v1', status: 'observed', source: 'visible_enhanced_checkout_dom',
    purchase_allowed: false, seller_handle: 'ExampleSeller', visible_row_count: 1,
    displayed: { subtotal: { amount: '0.10', currency: 'USD' }, shipping: { amount: '4.95', currency: 'USD' }, total: { amount: '5.05', currency: 'USD' } },
    selected_shipping_label: 'Economy - Media Mail' }, provider: null, destination: null,
  provenance: { schema: 'discogs_payment_quote_provenance_v1', source: 'current_enhanced_checkout', document_ref: documentRef,
    observed_at: now, expires_at: now + 20, same_document_verified: true,
    frame_association: { known_buttons_count: 1, known_buttons_associated: true, payment_quote_frame_qualified: false } } };
}
function syntheticProvider(value, now = 1000) {
  value.provider = { schema: 'discogs_provider_display_evidence_v1', layout_id: 'synthetic_unqualified_provider', amount: '5.05', currency: 'USD',
    final_total_verified: true, tax_finality: 'included_in_total', mandatory_charges_finality: 'none_applicable_verified',
    shipping_selection_verified: true, destination_mode: 'approved_saved_destination', destination_match: true,
    terms_verified: true, terms_fingerprint: 'a'.repeat(64), provenance: { source: 'qualified_visible_provider_dom',
      merchant_document_ref: value.provenance.document_ref, provider_document_ref: randomUUID(), observed_at: now, expires_at: now + 20,
      same_document_verified: true, frame_associated: true } };
  value.destination = { source: 'trusted_same_document_destination_binding', document_ref: value.provenance.document_ref,
    destination_ref: 'saved_shipping_address', match: true };
  value.provenance.frame_association.payment_quote_frame_qualified = true;
  return value;
}

test('actual observed merchant selector shape yields preparation-only USD evidence without accessing any payment inputs', async () => {
  const f = fixture();
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page); await waitForEnhancedPaymentQuoteSettlement(f.page);
    assert.equal(result.merchant_displayed.total.amount, '5.05'); assert.equal(result.merchant_displayed.total.currency, 'USD');
    assert.equal(result.merchant_displayed.tax, null); assert.equal(result.merchant_displayed.fees, null);
    assert.equal(result.amount_basis, 'merchant_aggregate_preparation_only'); assert.equal(result.final_quote_verified, false);
    assert.equal(result.destination_verified, false); assert.equal(result.payment_allowed, false); assert.equal(result.provider_terms_verified, false);
    assert.deepEqual(result.provider_displayed, { amount: null, currency: null });
    assert.equal(result.missing_evidence.includes('provider_dom_qualification'), true);
    assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false); assert.equal(f.calls.includes('dispose'), true);
  } finally { f.dom.window.close(); }
});

test('known buttons-frame metadata is not qualification of a final-payment quote frame and never reads its DOM or query tokens', async () => {
  const f = fixture();
  const frame = { url: () => 'https://www.paypal.com/smart/buttons?token=TOP_SECRET_TOKEN', parentFrame: () => f.main,
    frameElement: async () => ({ isVisible: async () => true, contentFrame: async () => frame, ownerFrame: async () => f.main }),
    evaluate: () => { throw new Error('Provider DOM not qualified'); } };
  f.page.frames = () => [frame, { url: () => 'https://unknown.example/form?private=TOP_SECRET_OTHER',
    frameElement: () => { throw new Error('Unknown frame touched'); } }];
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page);
    assert.equal(result.provenance.frame_association.known_buttons_associated, true);
    assert.equal(result.provenance.frame_association.payment_quote_frame_qualified, false);
    assert.equal(result.final_quote_verified, false); assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

function publicGuestFrame(f, { html = '<button id="submit-button" type="button">Pay $5.05</button>', changeDuringRead = null } = {}) {
  // Synthetic anatomy only. No particular live provider path has been observed.
  const dom = new JSDOM(`<html><body>${html}<aside>TOP_SECRET_ADDRESS</aside><input value="TOP_SECRET_PAN"></body></html>`,
    { url: 'https://www.paypal.com/fixture-unqualified?token=TOP_SECRET#TOP_SECRET', runScripts: 'outside-only' });
  dom.window.Element.prototype.getClientRects = () => [{ width: 100, height: 20 }];
  Object.defineProperty(dom.window.HTMLInputElement.prototype, 'value', { get() { throw new Error('Forbidden private input'); } });
  Object.defineProperty(dom.window.document.querySelector('aside'), 'textContent', { get() { throw new Error('Forbidden address read'); } });
  for (const method of ['click', 'focus']) dom.window.HTMLElement.prototype[method] = () => { throw new Error('Forbidden effect'); };
  const handle = { dispose: async () => {} };
  let visible = true, currentHandle = handle, url = dom.window.location.href, parent = f.main, attached = true;
  const frame = { url: () => url, parentFrame: () => parent,
    evaluateHandle: async () => handle,
    frameElement: async () => ({ dispose: async () => {}, isVisible: async () => visible,
      contentFrame: async () => attached ? frame : null, ownerFrame: async () => parent }),
    evaluate: async (fn, arg) => {
      if (fn === collectGuestPayControlMetadataDom) {
        const result = dom.window.eval(`(${fn.toString()})`)({ ...arg, expectedDocument: arg.expectedDocument === currentHandle ? dom.window.document : {} });
        changeDuringRead?.({ hide: () => { visible = false; }, detach: () => { attached = false; },
          replace: () => { currentHandle = {}; }, redirect: () => { url = 'https://www.paypal.com/changed'; },
          reparent: () => { parent = {}; } });
        return result;
      }
      if (fn.name === 'sameKnownDocument') return arg.expectedDocument === currentHandle && url === arg.url;
      if (fn.name === 'collectGuestPayAncestryControlDom') return dom.window.eval(`(${fn.toString()})`)({
        ...arg, expectedDocument: arg.expectedDocument === currentHandle ? dom.window.document : {} });
      assert.equal(arg, handle); return currentHandle === handle;
    } };
  return { frame, dom, setVisible: value => { visible = value; }, setParent: value => { parent = value; },
    setAttached: value => { attached = value; }, setUrl: value => { url = value; dom.reconfigure({ url: value }); }, replaceDocument: () => { currentHandle = {}; } };
}

function observedNestedGuestFixture() {
  // Actual observed categories/relationships, with synthetic paths and secrets.
  // Fourteen inventory members: main, known buttons, one visible guest, two
  // hidden nested peers and nine unrelated origins. This is not a quote profile.
  const f = fixture(), buttons = publicGuestFrame(f, { html: '<div>buttons container</div>' }), guest = publicGuestFrame(f);
  buttons.setUrl('https://www.paypal.com/smart/buttons?token=TOP_SECRET_BUTTONS');
  guest.setUrl('https://www.paypal.com/smart/fixture-guest?token=TOP_SECRET_GUEST'); guest.setParent(buttons.frame);
  const hiddenSmart = publicGuestFrame(f), hiddenOther = publicGuestFrame(f);
  hiddenSmart.setUrl('https://www.paypal.com/smart/fixture-hidden?token=TOP_SECRET_HIDDEN'); hiddenSmart.setParent(guest.frame); hiddenSmart.setVisible(false);
  hiddenOther.setUrl('https://www.paypal.com/fixture-hidden-other?token=TOP_SECRET_HIDDEN'); hiddenOther.setParent(guest.frame); hiddenOther.setVisible(false);
  const unrelated = Array.from({ length: 9 }, (_, i) => ({ url: () => `https://unrelated.example/${i}?token=TOP_SECRET`, parentFrame: () => f.main,
    frameElement: () => { throw new Error('Unrelated DOM forbidden'); }, evaluate: () => { throw new Error('Unrelated DOM forbidden'); } }));
  const frames = [f.main, ...unrelated.slice(0, 4), hiddenSmart.frame, buttons.frame, hiddenOther.frame, ...unrelated.slice(4), guest.frame];
  f.page.frames = () => frames;
  let guestLabelReads = 0; const evaluate = guest.frame.evaluate;
  guest.frame.evaluate = async (fn, arg) => { if (fn === collectGuestPayControlMetadataDom) guestLabelReads++; return evaluate(fn, arg); };
  const close = async () => { await waitForEnhancedPaymentQuoteSettlement(f.page); [buttons, guest, hiddenSmart, hiddenOther].forEach(value => value.dom.window.close()); f.dom.window.close(); };
  return { f, buttons, guest, hiddenSmart, hiddenOther, frames, unrelated, close, get guestLabelReads() { return guestLabelReads; } };
}

function assertNoPaymentQualification(result) {
  assert.equal(result.payment_allowed, false); assert.equal(result.final_submission_allowed, false); assert.equal(result.final_quote_verified, false);
  assert.equal(result.destination_verified, false); assert.equal(result.provider_terms_verified, false);
  assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
}

test('actual fourteen-frame observed relationship admits one public nested smart guest through the verified buttons frame only', async () => {
  const f = observedNestedGuestFixture();
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.f.page), metadata = result.guest_pay_metadata;
    assert.equal(f.frames.length, 14); assert.equal(metadata.status, 'observed'); assert.equal(metadata.label, 'Pay $5.05');
    assert.equal(metadata.currency, null); assert.equal(metadata.location, 'paypal_child'); assert.equal(metadata.eligible_count, 1);
    assert.equal(metadata.same_document_verified, true); assert.equal(metadata.frame_associated, true);
    assert.equal(metadata.selection_diagnostic.eligible_frames, 1); assert.equal(metadata.selection_diagnostic.non_direct, 2);
    assert.equal(metadata.ancestry_diagnostic.candidate_count, 3);
    const positive = metadata.ancestry_diagnostic.candidates.find(value => value.public_submit_count === 1);
    assert.equal(positive.category, 'paypal_smart_other'); assert.equal(positive.depth, 2); assert.equal(positive.buttons_ancestor_verified, true);
    assert.deepEqual(metadata.ancestry_diagnostic.candidates.filter(value => value.public_submit_count === null).map(value => value.hops[0].parent_category),
      ['paypal_smart_other', 'paypal_smart_other']);
    assert.equal(f.guestLabelReads, 3); assert.deepEqual(sanitizeGuestPayMetadata(metadata), metadata); assertNoPaymentQualification(result);
  } finally { await f.close(); }
});

function postEntryConfirmation(request, changes = {}) {
  // Explicitly synthetic trusted port fixture. No card fields are inspected.
  const now = Date.now() / 1000;
  return { schema: 'discogs_guest_post_entry_confirmation_v1', source: 'trusted_same_document_post_entry_confirmation',
    merchant_document_ref: request.merchant_document_ref, provider_document_ref: request.provider_document_ref,
    observed_at: now, expires_at: Math.min(now + 20, request.expires_at), entry_confirmed_at: now - 1, entry_complete: true,
    final_total: { amount: '5.05', currency: 'USD' }, all_payable_charges_included: true,
    tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total', deferred_charges: false,
    competing_currency: false, conversion_notice: false, new_notice: false,
    destination_ref: request.approved_destination_ref, destination_match: true, shipping_selection_verified: true,
    terms_verified: true, terms_fingerprint: 'a'.repeat(64), ...changes };
}

test('observed nested layout qualifies current DOM and explicit merchant USD context without pretending pre-entry finality', async () => {
  const f = observedNestedGuestFixture();
  try {
    const report = await inspectCurrentEnhancedPaymentQuote(f.f.page);
    assert.equal(report.gates.provider_dom_qualification, true); assert.equal(report.gates.provider_frame_and_document, true);
    assert.equal(report.gates.provider_explicit_currency, true); assert.equal(report.gates.provider_amount_matches_merchant, true);
    assert.equal(report.current_layout_qualifications.post_entry_confirmation_verified, false);
    assert.equal(report.effective_currency.currency, 'USD'); assert.equal(report.effective_currency.source, 'explicit_merchant_usd_matching_bound_pay');
    assert.equal(report.effective_currency.notice_review_verified, false); assert.equal(report.provider_displayed.currency, null);
    assert.equal(report.guest_pay_metadata.currency, null); assert.equal(Object.hasOwn(report.final_quote, 'aggregate_total_verified'), false);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false); assertNoPaymentQualification(report);
  } finally { await f.close(); }
});

test('fresh opaque post-entry confirmation can verify an aggregate final quote without reading or exporting card/billing values', async () => {
  const f = observedNestedGuestFixture(); let callbacks = 0;
  try {
    const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => {
      callbacks++; assert.equal(Object.isFrozen(request), true); assert.equal(Object.isFrozen(request.merchant_total), true);
      assert.deepEqual(Object.keys(request).sort(), ['schema','merchant_document_ref','provider_document_ref','observed_at','expires_at',
        'merchant_total','pay_amount','raw_pay_currency','selected_shipping_label','approved_destination_ref'].sort());
      assert.equal(request.raw_pay_currency, null); assert.equal(request.merchant_total.currency, 'USD');
      assert.equal(request.pay_amount, '5.05'); assert.equal(JSON.stringify(request).includes('TOP_SECRET'), false);
      return postEntryConfirmation(request);
    } });
    assert.equal(callbacks, 1); assert.equal(report.final_quote_verified, true); assert.equal(report.destination_verified, true);
    assert.equal(report.provider_terms_verified, true); assert.equal(report.current_layout_qualifications.post_entry_confirmation_verified, true);
    assert.equal(report.guest_pay_metadata.currency, null); assert.equal(report.provider_displayed.currency, null);
    assert.equal(report.effective_currency.currency, 'USD'); assert.equal(report.effective_currency.notice_review_verified, true);
    assert.deepEqual(report.final_quote, { amount: '5.05', currency: 'USD', tax: null, fees: null, charge_basis: 'aggregate_inclusive',
      total: { amount: '5.05', currency: 'USD' }, tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total',
      deferred_charges: false, aggregate_total_verified: true, all_payable_charges_included: true });
    assert.equal(Object.values(report.gates).every(Boolean), true); assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), true);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { page: f.f.page }), true);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { page: f.f.page, destinationRef: 'saved_shipping_address', termsFingerprint: 'a'.repeat(64) }), true);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { destinationRef: randomUUID() }), false);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { termsFingerprint: 'b'.repeat(64) }), false);
    assert.equal(report.merchant_displayed.seller_handle, 'ExampleSeller');
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { page: {} }), false);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(structuredClone(report)), false);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { now: report.provenance.expires_at }), false);
    assert.equal(Object.isFrozen(report), true); assert.equal(Object.isFrozen(report.final_quote.total), true);
    assert.equal(report.payment_allowed, false); assert.equal(report.final_submission_allowed, false); assert.equal(report.purchase_allowed, false);
    assert.equal(JSON.stringify(report).includes('TOP_SECRET'), false);
  } finally { await f.close(); }
});

test('final producer brand expires at the earlier trusted confirmation deadline', async () => {
  const f = observedNestedGuestFixture(); let expires = null;
  try {
    const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => {
      expires = Date.now() / 1000 + 0.5; return postEntryConfirmation(request, { expires_at: expires });
    } });
    assert.equal(report.final_quote_verified, true); assert.equal(report.provenance.expires_at, expires);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { now: expires + 0.001 }), false);
  } finally { await f.close(); }
});

test('final quote confirmation rejects changed amount/currency, notices, wrong destination, unaccepted terms and stale or injected facts', async () => {
  const changes = [ { final_total: { amount: '5.06', currency: 'USD' } }, { final_total: { amount: '5.05', currency: 'CAD' } },
    { competing_currency: true }, { conversion_notice: true }, { new_notice: true }, { deferred_charges: true },
    { destination_match: false }, { destination_ref: randomUUID() }, { shipping_selection_verified: false },
    { terms_verified: false }, { terms_fingerprint: null }, { tax_treatment: 'unverified' }, { fees_treatment: 'unverified' },
    { all_payable_charges_included: false }, { entry_complete: false }, { entry_confirmed_at: Date.now() / 1000 - 61 },
    { merchant_document_ref: randomUUID() }, { provider_document_ref: randomUUID() }, { private_card: 'TOP_SECRET_CARD' } ];
  for (const change of changes) {
    const f = observedNestedGuestFixture();
    try {
      const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => postEntryConfirmation(request, change) });
      assert.equal(report.final_quote_verified, false, JSON.stringify(change)); assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false);
      assert.equal(Object.hasOwn(report.final_quote, 'aggregate_total_verified'), false); assertNoPaymentQualification(report);
      if (change.competing_currency || change.conversion_notice || change.new_notice || change.deferred_charges || change.final_total?.currency === 'CAD')
        assert.equal(report.gates.provider_explicit_currency, false);
    } finally { await f.close(); }
  }
});

test('post-entry callback cannot qualify changed merchant/guest/buttons documents or a changed displayed amount', async () => {
  for (const mode of ['merchant-document', 'guest-document', 'buttons-document', 'hidden-visible', 'pay-amount', 'merchant-amount']) {
    const f = observedNestedGuestFixture();
    try {
      const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => {
        if (mode === 'merchant-document') f.f.changeDocument();
        if (mode === 'guest-document') f.guest.replaceDocument();
        if (mode === 'buttons-document') f.buttons.replaceDocument();
        if (mode === 'hidden-visible') f.hiddenOther.setVisible(true);
        if (mode === 'pay-amount') f.guest.dom.window.document.querySelector('#submit-button').textContent = 'Pay $5.06';
        if (mode === 'merchant-amount') f.f.dom.window.document.querySelector('[class*="_checkout-summary__total--"]').querySelector('dd').textContent = '$5.06 USD';
        return postEntryConfirmation(request);
      } });
      assert.equal(report.final_quote_verified, false, mode); assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false); assertNoPaymentQualification(report);
    } finally { await f.close(); }
  }
});

test('final merchant document proof cannot mint a stale guest quote after late guest replacement, amount change or detach', async () => {
  for (const mode of ['document', 'amount', 'detach']) {
    const f = observedNestedGuestFixture(); let callbackDone = false, proofs = 0, mutated = false;
    const evaluate = f.f.page.evaluate;
    f.f.page.evaluate = async (fn, arg) => {
      const value = await evaluate(fn, arg);
      if (callbackDone && arg === f.f.documentHandle && ++proofs === 4) {
        mutated = true;
        if (mode === 'document') f.guest.replaceDocument();
        if (mode === 'amount') f.guest.dom.window.document.querySelector('#submit-button').textContent = 'Pay $6.05';
        if (mode === 'detach') f.guest.setAttached(false);
      }
      return value;
    };
    try {
      const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => {
        callbackDone = true; return postEntryConfirmation(request);
      } });
      assert.equal(mutated, true, mode); assert.equal(report.final_quote_verified, false, mode);
      assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false, mode); assertNoPaymentQualification(report);
    } finally { await f.close(); }
  }
});

test('same-document Pay amount mutation during the last leaf proof is checked by the final public read', async () => {
  const f = observedNestedGuestFixture(); let callbackDone = false, leafProofs = 0, mutated = false;
  const evaluate = f.guest.frame.evaluate;
  f.guest.frame.evaluate = async (fn, arg) => {
    const value = await evaluate(fn, arg);
    if (callbackDone && fn.toString().includes('document === expected') && ++leafProofs === 4) {
      mutated = true; f.guest.dom.window.document.querySelector('#submit-button').textContent = 'Pay $6.05';
    }
    return value;
  };
  try {
    const report = await inspectCurrentEnhancedPaymentQuote(f.f.page, { finalQuoteMatcher: async request => {
      callbackDone = true; return postEntryConfirmation(request);
    } });
    assert.equal(mutated, true); assert.equal(report.final_quote_verified, false);
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false);
    assert.equal(report.guest_pay_metadata.status, 'unsupported_collection'); assertNoPaymentQualification(report);
  } finally { await f.close(); }
});

test('cancelled post-entry confirmation retains pending work and never mints a late final quote', async () => {
  const f = observedNestedGuestFixture(), entered = deferred(), finish = deferred(), controller = new AbortController();
  try {
    const task = inspectCurrentEnhancedPaymentQuote(f.f.page, { signal: controller.signal, finalQuoteMatcher: async request => {
      entered.resolve(); await finish.promise; return postEntryConfirmation(request);
    } });
    await entered.promise; controller.abort(); const report = await task; assert.equal(report.code, 'read_cancelled');
    let settled = false; const drain = waitForEnhancedPaymentQuoteSettlement(f.f.page).then(() => { settled = true; });
    await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false); finish.resolve(); await drain;
    assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report), false); assertNoPaymentQualification(report);
  } finally { finish.resolve(); await f.close(); }
});

test('copied observed-layout provider booleans cannot mint the actual producer brand', () => {
  const value = syntheticProvider(input()); value.provider.layout_id = 'paypal_smart_nested_guest_v1';
  const report = parseEnhancedPaymentQuoteEvidence(value, { now: 1000 });
  assert.equal(report.gates.provider_dom_qualification, false); assert.equal(report.final_quote_verified, false);
  assert.equal(isVerifiedCurrentEnhancedPaymentQuote(report, { now: 1000 }), false);
});

test('nested public metadata rejects wrong leaf category, deeper ancestry, unrelated or multiple buttons and visible unknown peers', async () => {
  for (const mode of ['wrong-leaf', 'depth-three', 'sibling-buttons', 'multiple-buttons', 'visible-peer']) {
    const f = observedNestedGuestFixture(); let added = null;
    try {
      if (mode === 'wrong-leaf') f.guest.setUrl('https://www.paypal.com/fixture-guest');
      if (mode === 'depth-three') { added = publicGuestFrame(f.f, { html: '<div>intermediate</div>' }); added.setUrl('https://www.paypal.com/smart/intermediate');
        added.setParent(f.buttons.frame); f.guest.setParent(added.frame); f.frames.push(added.frame); }
      if (mode === 'sibling-buttons') { f.buttons.setUrl('https://www.paypal.com/smart/unverified-container'); }
      if (mode === 'multiple-buttons') { added = publicGuestFrame(f.f, { html: '<div>other buttons</div>' }); added.setUrl('https://www.paypal.com/smart/buttons?other=TOP_SECRET'); f.frames.push(added.frame); }
      if (mode === 'visible-peer') f.hiddenOther.setVisible(true);
      const result = await inspectCurrentEnhancedPaymentQuote(f.f.page);
      assert.notEqual(result.guest_pay_metadata?.status, 'observed', mode); assert.equal(f.guestLabelReads, 0, mode); assertNoPaymentQualification(result);
    } finally { if (added) added.dom.window.close(); await f.close(); }
  }
});

test('both ancestry edges must be visible with matching owner/content and duplicate public IDs are rejected', async () => {
  for (const mode of ['leaf-hidden', 'leaf-detached', 'buttons-hidden', 'buttons-detached', 'leaf-owner', 'buttons-owner', 'duplicate-id']) {
    const f = observedNestedGuestFixture();
    try {
      if (mode === 'leaf-hidden') f.guest.setVisible(false);
      if (mode === 'leaf-detached') f.guest.setAttached(false);
      if (mode === 'buttons-hidden') f.buttons.setVisible(false);
      if (mode === 'buttons-detached') f.buttons.setAttached(false);
      if (mode === 'leaf-owner') { const element = await f.guest.frame.frameElement(); f.guest.frame.frameElement = async () => ({ ...element, ownerFrame: async () => f.f.main }); }
      if (mode === 'buttons-owner') { const element = await f.buttons.frame.frameElement(); f.buttons.frame.frameElement = async () => ({ ...element, ownerFrame: async () => ({}) }); }
      if (mode === 'duplicate-id') f.guest.dom.window.document.body.insertAdjacentHTML('beforeend','<input id="submit-button" value="TOP_SECRET">');
      const result = await inspectCurrentEnhancedPaymentQuote(f.f.page);
      assert.notEqual(result.guest_pay_metadata?.status, 'observed', mode); assertNoPaymentQualification(result);
    } finally { await f.close(); }
  }
});

test('nested metadata rejects same-URL leaf/buttons/main replacement and hidden-peer changes after matcher awaits', async () => {
  for (const mode of ['leaf-document', 'buttons-document', 'main-document', 'hidden-visible', 'hidden-document', 'leaf-url', 'control-changed']) {
    const f = observedNestedGuestFixture();
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.f.page, { destinationMatcher: async () => {
        if (mode === 'leaf-document') f.guest.replaceDocument();
        if (mode === 'buttons-document') f.buttons.replaceDocument();
        if (mode === 'main-document') f.f.changeDocument();
        if (mode === 'hidden-visible') f.hiddenOther.setVisible(true);
        if (mode === 'hidden-document') f.hiddenOther.replaceDocument();
        if (mode === 'leaf-url') f.guest.setUrl('https://unknown.example/private?token=TOP_SECRET');
        if (mode === 'control-changed') f.guest.dom.window.document.querySelector('#submit-button').textContent = 'Pay $6.05';
        return null;
      } });
      assert.notEqual(result.guest_pay_metadata?.status, 'observed', mode); assertNoPaymentQualification(result);
    } finally { await f.close(); }
  }
});

test('nested capture cancellation retains the pending handle and performs no late public control read', async () => {
  const f = observedNestedGuestFixture(), entered = deferred(), finish = deferred(), controller = new AbortController();
  const handle = f.guest.frame.evaluateHandle;
  f.guest.frame.evaluateHandle = async (fn, arg) => {
    if (fn.name === 'captureNestedGuestDocument') { entered.resolve(); await finish.promise; }
    return handle(fn, arg);
  };
  try {
    const request = inspectCurrentEnhancedPaymentQuote(f.f.page, { signal: controller.signal }); await entered.promise; controller.abort();
    const result = await request; assert.equal(result.code, 'read_cancelled'); assert.equal(f.guestLabelReads, 0);
    let settled = false; const drain = waitForEnhancedPaymentQuoteSettlement(f.f.page).then(() => { settled = true; });
    await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false);
    finish.resolve(); await drain; assert.equal(f.guestLabelReads, 0); assertNoPaymentQualification(result);
  } finally { finish.resolve(); await f.close(); }
});

test('late hidden-peer activation during the final public re-read cannot retain nested eligibility', async () => {
  const f = observedNestedGuestFixture(); let publicReads = 0; const evaluate = f.guest.frame.evaluate;
  f.guest.frame.evaluate = async (fn, arg) => {
    const value = await evaluate(fn, arg);
    if (fn === collectGuestPayControlMetadataDom && ++publicReads === 2) f.hiddenOther.setVisible(true);
    return value;
  };
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.f.page);
    assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
    assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
    assert.equal(result.guest_pay_metadata.ancestry_diagnostic.status, 'changed'); assertNoPaymentQualification(result);
  } finally { await f.close(); }
});

test('final ancestor/main proof cannot hide a late same-URL leaf replacement, detach or reparent', async () => {
  for (const mode of ['document', 'detach', 'reparent', 'visibility']) {
    const f = observedNestedGuestFixture(); let inventories = 0, finalRootProofs = 0, mutated = false;
    f.f.page.frames = () => { inventories++; return f.frames; };
    const evaluate = f.f.page.evaluate;
    f.f.page.evaluate = async (fn, arg) => {
      const result = await evaluate(fn, arg);
      if (inventories >= 4 && arg === f.f.documentHandle && ++finalRootProofs === 2) {
        mutated = true;
        if (mode === 'document') f.guest.replaceDocument();
        if (mode === 'detach') f.guest.setAttached(false);
        if (mode === 'reparent') f.guest.setParent(f.f.main);
        if (mode === 'visibility') f.guest.setVisible(false);
      }
      return result;
    };
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.f.page);
      assert.equal(mutated, true, mode); assert.equal(result.guest_pay_metadata.status, 'unsupported_collection', mode);
      assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed', mode);
      assert.equal(result.guest_pay_metadata.ancestry_diagnostic.status, 'changed', mode); assertNoPaymentQualification(result);
    } finally { await f.close(); }
  }
});

test('direct plus nested controls stay ambiguous and combined eligible-child cap remains two', async () => {
  for (const count of [1, 2]) {
    const f = observedNestedGuestFixture(), direct = Array.from({ length: count }, () => publicGuestFrame(f.f));
    f.frames.push(...direct.map(value => value.frame));
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.f.page), metadata = result.guest_pay_metadata;
      assert.equal(metadata.status, count === 1 ? 'ambiguous' : 'unsupported_collection');
      assert.equal(metadata.selection_diagnostic.eligible_frames, count + 1);
      if (count === 2) { assert.equal(metadata.truncated, true); assert.equal(metadata.selection_diagnostic.reason, 'eligible_frame_limit'); }
      assertNoPaymentQualification(result);
    } finally { direct.forEach(value => value.dom.window.close()); await f.close(); }
  }
});

test('fixed public Pay metadata supports merchant main document and never infers currency from the dollar sign', async () => {
  for (const label of ['Pay $5.05', 'Pay $5.05 USD']) {
    const f = fixture(HTML.replace('<button>Pay $5.05</button>', `<button id="submit-button" type="button">${label}</button>`));
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.guest_pay_metadata.status, 'observed');
      assert.equal(result.guest_pay_metadata.location, 'merchant_main');
      assert.equal(result.guest_pay_metadata.amount, '5.05');
      assert.equal(result.guest_pay_metadata.currency, label.endsWith('USD') ? 'USD' : null);
      assert.equal(result.guest_pay_metadata.frame_associated, true);
      assert.equal(result.guest_pay_metadata.same_document_verified, true);
      assert.deepEqual(result.provider_displayed, { amount: null, currency: null });
      assert.equal(result.final_quote_verified, false); assert.equal(result.payment_allowed, false);
      assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
  }
});

test('public PayPal child is associated by actual frame/document identity without qualifying its unknown path or exposing tokens', async () => {
  const f = fixture(), guest = publicGuestFrame(f);
  f.page.frames = () => [guest.frame];
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page);
    assert.equal(result.guest_pay_metadata.status, 'observed');
    assert.equal(result.guest_pay_metadata.location, 'paypal_child');
    assert.equal(result.guest_pay_metadata.currency, null); assert.equal(result.guest_pay_metadata.eligible_count, 1);
    assert.equal(result.provenance.frame_association.payment_quote_frame_qualified, false);
    assert.equal(result.gates.provider_dom_qualification, false);
    assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
    assert.equal(JSON.stringify(result).includes('fixture-unqualified'), false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
});

test('readable scoped documents with no fixed Pay control differ from unsupported public-control anatomy and legacy absence', async () => {
  const noControl = fixture();
  try { assert.equal((await inspectCurrentEnhancedPaymentQuote(noControl.page)).guest_pay_metadata.status, 'no_eligible_frame'); }
  finally { await waitForEnhancedPaymentQuoteSettlement(noControl.page); noControl.dom.window.close(); }
  assert.equal(sanitizeGuestPayMetadata(undefined).status, 'unsupported_collection');
  assert.equal(sanitizeGuestPayMetadata(null).status, 'unsupported_collection');
  for (const html of ['<button id="submit-button" type="submit">Pay $5.05</button>',
    '<button id="submit-button" type="button">PRIVATE</button>',
    '<button id="submit-button" type="button" hidden>Pay $5.05</button>',
    '<button id="submit-button" type="button">Pay $5.05<input value="PRIVATE"></button>']) {
    const f = fixture(), guest = publicGuestFrame(f, { html }); f.page.frames = () => [guest.frame];
    try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection'); assert.equal(result.guest_pay_metadata.label, null);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('duplicate eligible public controls and bounded excess frames cannot be reported as a unique associated Pay control', async () => {
  for (const count of [2, 3]) {
    const f = fixture(), guests = Array.from({ length: count }, () => publicGuestFrame(f));
    f.page.frames = () => guests.map(item => item.frame);
    try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.guest_pay_metadata.status, count === 2 ? 'ambiguous' : 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.truncated, count === 3);
      assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, count === 3 ? 'eligible_frame_limit' : 'complete');
      assert.equal(result.guest_pay_metadata.selection_diagnostic.eligible_frames, count);
      assert.equal(result.guest_pay_metadata.label, null); assert.equal(result.guest_pay_metadata.frame_associated, false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guests.forEach(item => item.dom.window.close()); f.dom.window.close(); }
  }
});

test('hidden and nested PayPal frames do not spend eligible-child slots before the actual visible associated guest document', async () => {
  const f = fixture(), guest = publicGuestFrame(f);
  const hidden = Array.from({ length: 3 }, () => publicGuestFrame(f)), nested = Array.from({ length: 2 }, () => publicGuestFrame(f));
  let forbidden = 0;
  for (const frame of hidden) frame.setVisible(false);
  for (const frame of nested) frame.setParent({});
  for (const item of [...hidden, ...nested]) {
    item.frame.evaluate = item.frame.evaluateHandle = () => { forbidden++; throw new Error('Excluded document queried'); };
  }
  const buttons = { url: () => 'https://www.paypal.com/smart/buttons?secret=TOP_SECRET', parentFrame: () => f.main,
    frameElement: async () => ({ isVisible: async () => true, contentFrame: async () => buttons, ownerFrame: async () => f.main }),
    evaluate: () => { forbidden++; throw new Error('Known buttons queried'); } };
  f.page.frames = () => [f.main, ...hidden.map(item => item.frame), ...nested.map(item => item.frame), buttons, guest.frame];
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page);
    const metadata = result.guest_pay_metadata;
    assert.equal(metadata.status, 'observed'); assert.equal(metadata.location, 'paypal_child'); assert.equal(metadata.truncated, false);
    assert.equal(metadata.candidate_count, 2); assert.equal(metadata.associated_count, 2); assert.equal(metadata.eligible_count, 1);
    assert.deepEqual(metadata.selection_diagnostic, { schema: 'discogs_guest_pay_selection_v1', reason: 'complete',
      total: 8, visited: 8, origin: 7, buttons_excluded: 1, non_direct: 2, hidden: 3, association_rejected: 0,
      eligible_frames: 1, inspected: 2, error: 0 });
    assert.equal(forbidden, 0); assert.equal(result.final_quote_verified, false); assert.equal(result.payment_allowed, false);
    assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); [...hidden, ...nested, guest].forEach(item => item.dom.window.close()); f.dom.window.close(); }
});

test('total-frame limit reports bounded selection diagnostics through the existing production coordinator without inventing frame counts', async () => {
  const f = fixture(); let reads = 0;
  f.page.frames = () => Array.from({ length: 33 }, () => ({ url: () => { reads++; throw new Error('Over-limit frame visited'); } }));
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page);
    assert.equal(result.code, 'frame_metadata_unverified'); assert.equal(reads, 0);
    assert.equal(result.guest_pay_metadata.status, 'unsupported_collection'); assert.equal(result.guest_pay_metadata.truncated, true);
    assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'total_frame_limit');
    assert.equal(result.guest_pay_metadata.selection_diagnostic.total, 32); assert.equal(result.guest_pay_metadata.selection_diagnostic.visited, 0);
    assert.equal(result.provenance, undefined); assert.ok(sanitizeGuestPayMetadata(result.guest_pay_metadata));
    await waitForEnhancedPaymentQuoteSettlement(f.page);
    const { createEnhancedCheckoutCoordinator, waitForEnhancedCheckoutCoordinatorSettlement } = await import('../scripts/discogs_enhanced_checkout_coordinator.mjs');
    const report = await createEnhancedCheckoutCoordinator().inspectPaymentReadiness(f.page);
    await waitForEnhancedCheckoutCoordinatorSettlement(f.page);
    assert.equal(report.quote.guest_pay_metadata.selection_diagnostic.reason, 'total_frame_limit');
    assert.equal(report.quote.code, 'frame_metadata_unverified'); assert.equal(report.payment_allowed, false); assert.equal(reads, 0);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('nested ancestry diagnostics count public controls without making nested frames eligible', async () => {
  // Synthetic parent chain, not a claim about the still-unqualified live tree.
  for (const count of [1, 2]) {
    const f = fixture(), parent = publicGuestFrame(f, { html: '<div>public container</div>' });
    const nested = Array.from({ length: count }, () => publicGuestFrame(f));
    nested.forEach(item => item.setParent(parent.frame));
    f.page.frames = () => [f.main, parent.frame, ...nested.map(item => item.frame)];
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      const metadata = result.guest_pay_metadata, ancestry = metadata.ancestry_diagnostic;
      assert.equal(metadata.status, 'no_eligible_frame'); assert.equal(metadata.eligible_count, 0);
      assert.equal(metadata.label, null); assert.equal(metadata.frame_associated, false);
      assert.equal(ancestry.status, count === 1 ? 'observed' : 'incomplete');
      assert.equal(ancestry.reason, count === 1 ? 'complete' : 'ambiguous_controls');
      assert.equal(ancestry.candidate_count, count); assert.equal(ancestry.truncated, false);
      for (const candidate of ancestry.candidates) {
        assert.equal(candidate.public_submit_count, 1); assert.equal(candidate.stop, 'merchant_root');
        assert.equal(candidate.depth, 2); assert.equal(candidate.chain_complete, true);
        assert.equal(candidate.buttons_ancestor_verified, false);
      }
      assert.equal(result.final_quote_verified, false); assert.equal(result.payment_allowed, false);
      assert.equal(result.provenance.frame_association.payment_quote_frame_qualified, false);
      assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
      assert.deepEqual(sanitizeGuestPayMetadata(metadata), metadata);
      assert.equal(sanitizeGuestPayMetadata({ ...metadata,
        ancestry_diagnostic: { ...ancestry, private_url: 'TOP_SECRET' } }), null);
    } finally {
      await waitForEnhancedPaymentQuoteSettlement(f.page);
      [parent, ...nested].forEach(item => item.dom.window.close()); f.dom.window.close();
    }
  }
});

test('nested diagnostics retain changed-ancestor evidence without qualifying a Pay control', async () => {
  const f = fixture(), parent = publicGuestFrame(f, { html: '<div>public container</div>' }), nested = publicGuestFrame(f);
  nested.setParent(parent.frame); f.page.frames = () => [f.main, parent.frame, nested.frame];
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => {
      parent.replaceDocument(); return null;
    } });
    assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
    assert.equal(result.guest_pay_metadata.ancestry_diagnostic.status, 'changed');
    assert.equal(result.guest_pay_metadata.ancestry_diagnostic.reason, 'inventory_changed');
    assert.equal(result.guest_pay_metadata.ancestry_diagnostic.candidates[0].public_submit_count, null);
    assert.equal(result.guest_pay_metadata.ancestry_diagnostic.candidates[0].chain_complete, false);
    assert.equal(result.payment_allowed, false); assert.equal(result.final_quote_verified, false);
  } finally {
    await waitForEnhancedPaymentQuoteSettlement(f.page); [parent, nested].forEach(item => item.dom.window.close()); f.dom.window.close();
  }
});

test('ancestry finalizer cannot outlive the final audit of an already selected direct Pay child', async () => {
  for (const mode of ['document', 'association']) {
    const f = fixture(), direct = publicGuestFrame(f), parent = publicGuestFrame(f, { html: '<div>public container</div>' });
    const nested = publicGuestFrame(f); nested.setParent(parent.frame);
    const frames = [f.main, direct.frame, parent.frame, nested.frame]; let inventories = 0;
    f.page.frames = () => {
      // Before the ordering fix this third inventory read ran inside the late
      // ancestry finalizer, after the selected direct child had been audited.
      if (++inventories === 3) {
        if (mode === 'document') direct.replaceDocument(); else direct.setAttached(false);
      }
      return frames;
    };
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.ok(inventories >= 3);
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
      assert.equal(result.guest_pay_metadata.label, null); assert.equal(result.guest_pay_metadata.frame_associated, false);
      assert.equal(result.payment_allowed, false); assert.equal(result.final_quote_verified, false);
    } finally {
      await waitForEnhancedPaymentQuoteSettlement(f.page);
      [direct, parent, nested].forEach(item => item.dom.window.close()); f.dom.window.close();
    }
  }
});

test('excluded frames changing URL, direct parent, visibility or ownership during a matcher invalidate the original selection', async () => {
  for (const mode of ['other_origin', 'non_direct', 'hidden', 'association_rejected']) {
    const f = fixture(), guest = publicGuestFrame(f), excluded = publicGuestFrame(f);
    if (mode === 'other_origin') excluded.setUrl('https://unknown.example/private?token=TOP_SECRET');
    if (mode === 'non_direct') excluded.setParent({});
    if (mode === 'hidden') excluded.setVisible(false);
    if (mode === 'association_rejected') excluded.setAttached(false);
    let forbidden = 0;
    excluded.frame.evaluate = excluded.frame.evaluateHandle = () => { forbidden++; throw new Error('Excluded document read'); };
    f.page.frames = () => [excluded.frame, guest.frame];
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => {
        if (mode === 'other_origin') excluded.setUrl('https://www.paypal.com/newly-eligible');
        if (mode === 'non_direct') excluded.setParent(f.main);
        if (mode === 'hidden') excluded.setVisible(true);
        if (mode === 'association_rejected') excluded.setAttached(true);
        return null;
      } });
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
      assert.equal(result.guest_pay_metadata.frame_associated, false); assert.equal(result.guest_pay_metadata.label, null);
      assert.equal(forbidden, 0); assert.equal(result.final_quote_verified, false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); excluded.dom.window.close(); f.dom.window.close(); }
  }
});

test('mutable frame-array inventory and same-URL selected document replacement cannot preserve positive selection', async () => {
  for (const mode of ['array_mutation', 'document_replacement']) {
    const f = fixture(), guest = publicGuestFrame(f), frames = [guest.frame]; f.page.frames = () => frames;
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => {
        if (mode === 'array_mutation') frames.push({ url: () => 'https://unknown.example/new' });
        else guest.replaceDocument();
        return null;
      } });
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
      assert.equal(result.guest_pay_metadata.label, null);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('same-URL merchant or selected document replacement during a later excluded-frame audit invalidates retained metadata', async () => {
  for (const mode of ['merchant', 'selected_child']) {
    const f = fixture(), guest = publicGuestFrame(f); let changed = false;
    const excluded = { url: () => {
      if (!changed && f.calls.includes('public_pay_metadata')) {
        changed = true;
        if (mode === 'merchant') f.changeDocument(); else guest.replaceDocument();
      }
      return 'https://unknown.example/excluded';
    } };
    f.page.frames = () => [guest.frame, excluded];
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(changed, true);
      if (mode === 'merchant') assert.equal(result.code, 'document_changed');
      else {
        assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
        assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
      }
      assert.equal(result.final_quote_verified, false); assert.equal(result.payment_allowed, false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('selection diagnostic remains optional for legacy metadata and rejects arbitrary text, counts, getters and extra fields', () => {
  const legacy = sanitizeGuestPayMetadata(null);
  assert.ok(sanitizeGuestPayMetadata(legacy));
  const diagnostic = { schema: 'discogs_guest_pay_selection_v1', reason: 'frame_inspection_error', total: 1, visited: 1, origin: 0,
    buttons_excluded: 0, non_direct: 0, hidden: 0, association_rejected: 0, eligible_frames: 0, inspected: 0, error: 1 };
  const cloned = sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: diagnostic });
  assert.deepEqual(cloned.selection_diagnostic, diagnostic); assert.notEqual(cloned.selection_diagnostic, diagnostic);
  for (const bad of [{ ...diagnostic, reason: 'TOP_SECRET' }, { ...diagnostic, total: 33 }, { ...diagnostic, url: 'TOP_SECRET' },
    { ...diagnostic, visited: 2 }, { ...diagnostic, eligible_frames: 2 }, { ...diagnostic, inspected: 2 }]) {
    assert.equal(sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: bad }), null);
  }
  const getter = { ...diagnostic }; Object.defineProperty(getter, 'reason', { enumerable: true, get() { throw new Error('Getter must not run'); } });
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: getter }), null);
  const complete = { schema: 'discogs_guest_pay_selection_v1', reason: 'complete', total: 1, visited: 1, origin: 1,
    buttons_excluded: 0, non_direct: 0, hidden: 0, association_rejected: 0, eligible_frames: 1, inspected: 2, error: 0 };
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, truncated: true, selection_diagnostic: complete }), null);
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: { ...diagnostic, error: 0 } }), null);
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, truncated: true, selection_diagnostic: { ...diagnostic, reason: 'total_frame_limit', total: 31, visited: 0, error: 0 } }), null);
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, truncated: true, selection_diagnostic: { ...complete, reason: 'eligible_frame_limit', total: 2, visited: 2, origin: 2, eligible_frames: 2, inspected: 0 } }), null);
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: { ...complete, visited: 0 } }), null);
  assert.equal(sanitizeGuestPayMetadata({ ...legacy, selection_diagnostic: { ...complete, eligible_frames: 0, inspected: 0 } }), null);
  const observedChild = { ...legacy, status: 'observed', candidate_count: 1, associated_count: 1, eligible_count: 1,
    submit_count: 1, submit_visible: true, submit_enabled: true, button_type_matches: true, label: 'Pay $5.05', amount: '5.05',
    currency: null, location: 'paypal_child', same_document_verified: true, frame_associated: true,
    selection_diagnostic: { ...complete, origin: 0, eligible_frames: 0, inspected: 1 } };
  assert.equal(sanitizeGuestPayMetadata(observedChild), null);
});

test('known buttons association becoming hidden during the matcher is not reported as still associated', async () => {
  const f = fixture(); let visible = true;
  const buttons = { url: () => 'https://www.paypal.com/smart/buttons?private=TOP_SECRET', parentFrame: () => f.main,
    frameElement: async () => ({ isVisible: async () => visible, contentFrame: async () => buttons, ownerFrame: async () => f.main }),
    evaluate: () => { throw new Error('Known buttons DOM must not be read'); } };
  f.page.frames = () => [buttons];
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => { visible = false; return null; } });
    assert.equal(result.provenance.frame_association.known_buttons_associated, false);
    assert.equal(result.guest_pay_metadata.selection_diagnostic.reason, 'frame_changed');
    assert.equal(result.final_quote_verified, false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('cancellation during candidate classification retains pending metadata reads and never proceeds to document queries', async () => {
  const f = fixture(), guest = publicGuestFrame(f), entered = deferred(), finish = deferred(), controller = new AbortController();
  const original = guest.frame.frameElement; let forbidden = 0;
  guest.frame.frameElement = async () => { entered.resolve(); await finish.promise; return original(); };
  guest.frame.evaluate = guest.frame.evaluateHandle = () => { forbidden++; throw new Error('Must not continue canceled classification'); };
  f.page.frames = () => [guest.frame];
  try {
    const reading = inspectCurrentEnhancedPaymentQuote(f.page, { signal: controller.signal });
    await entered.promise; controller.abort(); assert.equal((await reading).code, 'read_cancelled');
    let settled = false; const drainage = waitForEnhancedPaymentQuoteSettlement(f.page).then(() => { settled = true; });
    await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false);
    finish.resolve(); await drainage; assert.equal(forbidden, 0);
  } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
});

test('guest frame visibility, ownership, URL and document mutations during public read invalidate metadata association', async () => {
  for (const key of ['hide', 'detach', 'replace', 'redirect', 'reparent']) {
    const f = fixture(), guest = publicGuestFrame(f, { changeDuringRead: changes => changes[key]() });
    f.page.frames = () => [guest.frame];
    try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.same_document_verified, false); assert.equal(result.guest_pay_metadata.label, null);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('public metadata validator rejects inconsistent counts, text, currency, associations and private extra fields', () => {
  const good = { schema: 'discogs_guest_pay_metadata_v1', status: 'observed', paypal_origin_count: 1,
    candidate_count: 1, associated_count: 1, eligible_count: 1, unsupported_count: 0, truncated: false,
    submit_count: 1, submit_visible: true, submit_enabled: false, button_type_matches: true,
    label: 'Pay $5.05', amount: '5.05', currency: null, location: 'paypal_child', same_document_verified: true, frame_associated: true };
  assert.deepEqual(sanitizeGuestPayMetadata(good), good);
  for (const change of [{ label: 'TOP_SECRET' }, { amount: '5.06' }, { currency: 'USD' }, { location: 'unknown' },
    { frame_associated: false }, { candidate_count: 3 }, { candidate_count: 0 }, { associated_count: 0 },
    { unsupported_count: 2 }, { value: 'TOP_SECRET' }, { same_document_verified: false }]) {
    assert.equal(sanitizeGuestPayMetadata({ ...good, ...change }), null);
  }
});

test('unsupported guest frame APIs and raw read exceptions preserve merchant evidence with explicit unsupported metadata', async () => {
  for (const method of ['parentFrame', 'frameElement', 'evaluateHandle', 'evaluate']) {
    const f = fixture(), guest = publicGuestFrame(f); f.page.frames = () => [guest.frame];
    guest.frame[method] = () => { throw new Error('TOP_SECRET_EXCEPTION'); };
    try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.code, 'provider_final_quote_unverified');
      assert.equal(result.merchant_displayed.total.amount, '5.05');
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('unknown origins and known buttons documents are never inspected for a guest Pay control', async () => {
  const f = fixture(); let forbiddenReads = 0;
  const frame = { url: () => 'https://different.example/unknown?secret=TOP_SECRET',
    evaluate: () => { forbiddenReads++; throw new Error('Forbidden unknown-origin DOM'); },
    frameElement: () => { forbiddenReads++; throw new Error('Forbidden unknown-origin association'); } };
  f.page.frames = () => [frame];
  try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
    assert.equal(forbiddenReads, 0); assert.equal(result.guest_pay_metadata.status, 'no_eligible_frame');
    assert.equal(result.guest_pay_metadata.paypal_origin_count, 0);
    // This is absence only within merchant/eligible PayPal candidates, never all unknown origins.
    assert.equal(result.final_quote_verified, false); assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('cancelled public guest reads drain the actual pending call, reject reentry and issue no later read', async () => {
  const f = fixture(), guest = publicGuestFrame(f), entered = deferred(), finish = deferred(), controller = new AbortController();
  const original = guest.frame.evaluate; let sameDocumentReads = 0;
  guest.frame.evaluate = async (fn, arg) => {
    if (fn === collectGuestPayControlMetadataDom) { entered.resolve(); await finish.promise; return original(fn, arg); }
    sameDocumentReads++; return original(fn, arg);
  };
  f.page.frames = () => [guest.frame];
  try {
    const reading = inspectCurrentEnhancedPaymentQuote(f.page, { signal: controller.signal });
    await entered.promise; const readsBeforeCancel = sameDocumentReads; controller.abort();
    assert.equal((await reading).code, 'read_cancelled');
    assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'quote_read_in_progress');
    let settled = false; const drainage = waitForEnhancedPaymentQuoteSettlement(f.page).then(() => { settled = true; });
    await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false);
    finish.resolve(); await drainage;
    assert.equal(sameDocumentReads, readsBeforeCancel);
  } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
});

test('fixed guest collector checks captured document and actual allowed origin before any DOM query, including a last-moment redirect', async () => {
  for (const when of ['handle', 'evaluation']) {
    const f = fixture(), guest = publicGuestFrame(f); let forbiddenQueries = 0;
    f.page.frames = () => [guest.frame];
    const query = guest.dom.window.document.querySelectorAll.bind(guest.dom.window.document);
    guest.dom.window.document.querySelectorAll = selector => {
      if (guest.dom.window.location.origin !== 'https://www.paypal.com') forbiddenQueries++;
      return query(selector);
    };
    const handle = guest.frame.evaluateHandle, evaluate = guest.frame.evaluate;
    guest.frame.url = () => guest.dom.window.location.href;
    if (when === 'handle') guest.frame.evaluateHandle = async () => {
      const result = await handle(); guest.dom.reconfigure({ url: 'https://unknown.example/private?token=TOP_SECRET' }); return result;
    };
    else guest.frame.evaluate = async (fn, arg) => {
      if (fn === collectGuestPayControlMetadataDom) guest.dom.reconfigure({ url: 'https://unknown.example/private?token=TOP_SECRET' });
      return evaluate(fn, arg);
    };
    try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(forbiddenQueries, 0); assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.label, null); assert.equal(result.final_quote_verified, false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
  const f = fixture(); let queried = 0;
  f.dom.window.document.querySelectorAll = () => { queried++; throw new Error('must not query replaced document'); };
  try {
    const result = f.dom.window.eval(`(${collectGuestPayControlMetadataDom.toString()})`)({ role: 'merchant_main', expectedDocument: {} });
    assert.equal(result.status, 'unsupported_collection'); assert.equal(queried, 0);
  } finally { f.dom.window.close(); }
});

test('child metadata is revalidated after an awaited destination matcher, including navigation or detachment while matcher is pending', async () => {
  for (const change of ['navigate', 'detach']) {
    const f = fixture(), guest = publicGuestFrame(f), entered = deferred(), finish = deferred();
    f.page.frames = () => [guest.frame];
    const originalElement = guest.frame.frameElement;
    let detached = false;
    guest.frame.frameElement = async () => { const element = await originalElement();
      return { ...element, contentFrame: async () => detached ? null : guest.frame }; };
    guest.frame.url = () => guest.dom.window.location.href;
    try {
      const reading = inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => {
        entered.resolve(); await finish.promise; return null;
      } });
      await entered.promise;
      if (change === 'navigate') guest.dom.reconfigure({ url: 'https://www.paypal.com/changed-after-metadata' });
      else detached = true;
      finish.resolve();
      const result = await reading;
      assert.equal(result.guest_pay_metadata.status, 'unsupported_collection');
      assert.equal(result.guest_pay_metadata.frame_associated, false); assert.equal(result.guest_pay_metadata.same_document_verified, false);
      assert.equal(result.guest_pay_metadata.label, null); assert.equal(result.final_quote_verified, false);
    } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); guest.dom.window.close(); f.dom.window.close(); }
  }
});

test('trusted opaque merchant destination match cannot prove provider delivery with default Ship to billing checked', async () => {
  const f = fixture();
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async request => {
      assert.equal(request.page, f.page); assert.equal(request.documentHandle, f.documentHandle);
      return { source: 'trusted_same_document_destination_binding', document_ref: request.document_ref,
        destination_ref: 'saved_shipping_address', match: true };
    } });
    assert.equal(result.merchant_destination_match, true); assert.equal(result.destination_verified, false);
    assert.equal(result.gates.provider_delivery_binding, false); assert.equal(result.payment_allowed, false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('opaque destination evidence must match this exact document and cannot export extra private fields', async () => {
  const f = fixture();
  try {
    const result = await inspectCurrentEnhancedPaymentQuote(f.page, { destinationMatcher: async () => ({
      source: 'trusted_same_document_destination_binding', document_ref: randomUUID(), destination_ref: 'saved_shipping_address', match: true, address: 'TOP_SECRET' }) });
    assert.equal(result.merchant_destination_match, null); assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('wrong route, same-URL document replacement, challenge and malformed merchant shape fail before qualification', async () => {
  const wrong = fixture(); wrong.changeUrl('https://www.discogs.com/sell/cart/');
  try { assert.equal((await inspectCurrentEnhancedPaymentQuote(wrong.page)).code, 'url_unverified'); assert.deepEqual(wrong.calls, ['url']); }
  finally { wrong.dom.window.close(); }
  const changed = fixture();
  try { const result = await inspectCurrentEnhancedPaymentQuote(changed.page, { destinationMatcher: async () => { changed.changeDocument(); return null; } });
    assert.equal(result.code, 'document_changed'); assert.equal(result.payment_allowed, false);
  } finally { await waitForEnhancedPaymentQuoteSettlement(changed.page); changed.dom.window.close(); }
  for (const html of [HTML.replace('<title>Checkout</title>', '<title>Just a moment - Cloudflare</title>'), HTML.replace('$5.05 USD', '$5.06 USD')]) {
    const f = fixture(html); try { const result = await inspectCurrentEnhancedPaymentQuote(f.page);
      assert.equal(result.final_quote_verified, false); assert.equal(f.calls.includes('frames'), false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
  }
});

test('bare Pay $5.05 and billing-address mode cannot satisfy currency, tax finality or approved delivery evidence', () => {
  const value = syntheticProvider(input()); value.provider.currency = null; value.provider.tax_finality = 'unverified';
  value.provider.mandatory_charges_finality = 'unverified'; value.provider.destination_mode = 'billing_address';
  const result = parseEnhancedPaymentQuoteEvidence(value, { now: 1000 });
  assert.equal(result.provider_displayed.amount, '5.05'); assert.equal(result.provider_displayed.currency, null);
  for (const key of ['provider_dom_qualification', 'provider_explicit_currency', 'final_tax_treatment', 'mandatory_charges_finality', 'provider_delivery_binding']) assert.equal(result.gates[key], false);
  assert.equal(result.final_quote_verified, false); assert.equal(result.payment_allowed, false);
});

test('even complete synthetic provider facts do not qualify an unobserved provider DOM layout or enable payment', () => {
  const result = parseEnhancedPaymentQuoteEvidence(syntheticProvider(input()), { now: 1000 });
  assert.deepEqual(result.missing_evidence, ['provider_dom_qualification']);
  assert.equal(result.final_quote_verified, false); assert.equal(result.final_quote.amount, null);
  assert.equal(result.payment_allowed, false); assert.equal(result.final_submission_allowed, false);
});

test('missing fee/tax breakdown is not invented as zero or an extra buyer surcharge', () => {
  const result = parseEnhancedPaymentQuoteEvidence(syntheticProvider(input()), { now: 1000 });
  assert.equal(result.gates.final_tax_treatment, true); assert.equal(result.gates.mandatory_charges_finality, true);
  assert.equal(result.merchant_displayed.tax, null); assert.equal(result.merchant_displayed.fees, null);
  assert.equal(result.final_quote.tax, null); assert.equal(result.final_quote.fees, null);
});

test('freshness, provider association, changed amount/currency, delivery and terms independently remain explicit gates', () => {
  const stale = input(); assert.equal(parseEnhancedPaymentQuoteEvidence(stale, { now: 1020 }).code, 'same_document_freshness_unverified');
  for (const [change, gate] of [
    [value => { value.provider.provenance.frame_associated = false; }, 'provider_frame_and_document'],
    [value => { value.provider.provenance.merchant_document_ref = randomUUID(); }, 'provider_frame_and_document'],
    [value => { value.provider.provenance.same_document_verified = false; }, 'provider_frame_and_document'],
    [value => { value.provider.amount = '5.06'; }, 'provider_amount_matches_merchant'],
    [value => { value.provider.currency = 'EUR'; }, 'provider_explicit_currency'],
    [value => { value.provider.final_total_verified = false; }, 'provider_final_all_in_total'],
    [value => { value.provider.destination_match = false; }, 'provider_delivery_binding'],
    [value => { value.provider.terms_fingerprint = null; }, 'provider_terms'],
  ]) { const value = syntheticProvider(input()); change(value);
    const result = parseEnhancedPaymentQuoteEvidence(value, { now: 1000 }); assert.equal(result.gates[gate], false); assert.equal(result.payment_allowed, false); }
});

test('extra sensitive provider properties and arbitrary exceptions never reach evidence output', async () => {
  const value = syntheticProvider(input()); value.provider.card_number = 'TOP_SECRET';
  const parsed = parseEnhancedPaymentQuoteEvidence(value, { now: 1000 });
  assert.equal(parsed.final_quote_verified, false); assert.equal(JSON.stringify(parsed).includes('TOP_SECRET'), false);
  const f = fixture(); f.page.evaluate = async () => { throw new Error('TOP_SECRET_EXCEPTION'); };
  try { assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'read_failed'); }
  finally { await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('cancelled or timed-out reads retain their underlying settlement and never return a late quote', async () => {
  for (const cancel of [true, false]) {
    const f = fixture(), entered = deferred(), finish = deferred(), controller = new AbortController();
    f.page.evaluate = async fn => { assert.equal(fn, collectEnhancedPaymentQuoteDom); entered.resolve(); await finish.promise; return {}; };
    try {
      const pending = inspectCurrentEnhancedPaymentQuote(f.page, { signal: controller.signal, timeoutMs: cancel ? 1000 : 10 });
      await entered.promise; if (cancel) controller.abort();
      assert.equal((await pending).code, cancel ? 'read_cancelled' : 'read_timeout');
      let settled = false; const drain = waitForEnhancedPaymentQuoteSettlement(f.page).then(() => { settled = true; });
      await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false);
      finish.resolve(); await drain; assert.equal(f.calls.includes('frames'), false);
    } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
  }
});

test('late-created document handles are disposed without enabling a late read or leaking retained browser handles', async () => {
  const f = fixture(), started = deferred(), finish = deferred(), controller = new AbortController(); let disposed = 0;
  f.page.evaluateHandle = async () => { started.resolve(); await finish.promise; return { dispose: async () => { disposed++; } }; };
  try {
    const reading = inspectCurrentEnhancedPaymentQuote(f.page, { signal: controller.signal }); await started.promise; controller.abort();
    assert.equal((await reading).code, 'read_cancelled');
    finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page);
    assert.equal(disposed, 1); assert.equal(f.calls.includes('merchant_dom'), false);
  } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});

test('frame metadata handles are released, and credentials/fragments do not qualify a known buttons frame', async () => {
  for (const url of ['https://www.paypal.com/smart/buttons?token=TOP_SECRET',
    'https://user:TOP_SECRET@www.paypal.com/smart/buttons', 'https://www.paypal.com/smart/buttons#TOP_SECRET']) {
    const f = fixture(); let disposed = 0;
    const frame = { url: () => url, parentFrame: () => f.main, frameElement: async () => ({ dispose: async () => { disposed++; },
      isVisible: async () => true, contentFrame: async () => frame, ownerFrame: async () => f.main }) };
    f.page.frames = () => [frame];
    try {
      const result = await inspectCurrentEnhancedPaymentQuote(f.page); await waitForEnhancedPaymentQuoteSettlement(f.page);
      assert.equal(result.provenance.frame_association.known_buttons_count, url.includes('?') ? 1 : 0);
      assert.equal(disposed, url.includes('?') ? 1 : 0); assert.equal(result.final_quote_verified, false);
      assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
    } finally { f.dom.window.close(); }
  }
});

test('same page rejects concurrent active reads before touching browser state while a different page remains independent', async () => {
  const f = fixture(), other = fixture(), entered = deferred(), finish = deferred();
  f.page.evaluate = async fn => { assert.equal(fn, collectEnhancedPaymentQuoteDom); entered.resolve(); await finish.promise; return {}; };
  try {
    const first = inspectCurrentEnhancedPaymentQuote(f.page); await entered.promise;
    const callsBefore = [...f.calls];
    assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'quote_read_in_progress');
    assert.deepEqual(f.calls, callsBefore);
    assert.equal((await inspectCurrentEnhancedPaymentQuote(other.page)).code, 'provider_final_quote_unverified');
    finish.resolve(); assert.equal((await first).code, 'merchant_summary_unverified');
  } finally { finish.resolve(); await Promise.all([waitForEnhancedPaymentQuoteSettlement(f.page), waitForEnhancedPaymentQuoteSettlement(other.page)]);
    f.dom.window.close(); other.dom.window.close(); }
});

test('cancelled and timed-out quote responses do not permit same-page reentry before the dispatched read actually settles', async () => {
  for (const cancel of [true, false]) {
    const f = fixture(), entered = deferred(), finish = deferred(), controller = new AbortController();
    const original = f.page.evaluate;
    f.page.evaluate = async fn => { assert.equal(fn, collectEnhancedPaymentQuoteDom); entered.resolve(); await finish.promise; return {}; };
    try {
      const first = inspectCurrentEnhancedPaymentQuote(f.page, { signal: controller.signal, timeoutMs: cancel ? 1000 : 10 });
      await entered.promise; if (cancel) controller.abort();
      assert.equal((await first).code, cancel ? 'read_cancelled' : 'read_timeout');
      const callsBefore = [...f.calls];
      assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'quote_read_in_progress');
      assert.deepEqual(f.calls, callsBefore);
      f.page.evaluate = original; finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page);
      assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'provider_final_quote_unverified');
    } finally { finish.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
  }
});

test('completed quote read keeps its page lock while browser-handle disposal is still pending', async () => {
  const f = fixture(), disposeStarted = deferred(), finishDispose = deferred();
  f.documentHandle.dispose = async () => { disposeStarted.resolve(); await finishDispose.promise; };
  try {
    assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'provider_final_quote_unverified'); await disposeStarted.promise;
    assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'quote_read_in_progress');
    finishDispose.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page);
    assert.equal((await inspectCurrentEnhancedPaymentQuote(f.page)).code, 'provider_final_quote_unverified');
  } finally { finishDispose.resolve(); await waitForEnhancedPaymentQuoteSettlement(f.page); f.dom.window.close(); }
});
