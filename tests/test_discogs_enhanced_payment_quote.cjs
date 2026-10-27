'use strict';
// Observed merchant summary shape; all provider contracts below are synthetic
// unqualified facts, never a live payment or a qualified provider DOM fixture.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
const { randomUUID } = require('node:crypto');
let collectEnhancedPaymentQuoteDom, inspectCurrentEnhancedPaymentQuote,
  parseEnhancedPaymentQuoteEvidence, waitForEnhancedPaymentQuoteSettlement, collectGuestPayControlMetadataDom, sanitizeGuestPayMetadata;
before(async () => ({ collectEnhancedPaymentQuoteDom, inspectCurrentEnhancedPaymentQuote,
  parseEnhancedPaymentQuoteEvidence, waitForEnhancedPaymentQuoteSettlement, collectGuestPayControlMetadataDom, sanitizeGuestPayMetadata } = await import('../scripts/discogs_enhanced_payment_quote.mjs')));
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
  const calls = [], documentHandle = { dispose: async () => calls.push('dispose') }, main = {};
  let currentDocument = documentHandle, url = URL;
  const page = { url: () => { calls.push('url'); return url; },
    evaluateHandle: async () => { calls.push('document_handle'); return currentDocument; },
    evaluate: async (fn, arg) => {
      if (fn === collectEnhancedPaymentQuoteDom) { calls.push('merchant_dom'); return dom.window.eval(`(${fn.toString()})()`); }
      if (fn === collectGuestPayControlMetadataDom) { calls.push('public_pay_metadata');
        return dom.window.eval(`(${fn.toString()})`)({ ...arg, expectedDocument: arg.expectedDocument === currentDocument ? dom.window.document : {} }); }
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
      contentFrame: async () => attached ? frame : null, ownerFrame: async () => f.main }),
    evaluate: async (fn, arg) => {
      if (fn === collectGuestPayControlMetadataDom) {
        const result = dom.window.eval(`(${fn.toString()})`)({ ...arg, expectedDocument: arg.expectedDocument === currentHandle ? dom.window.document : {} });
        changeDuringRead?.({ hide: () => { visible = false; }, detach: () => { attached = false; },
          replace: () => { currentHandle = {}; }, redirect: () => { url = 'https://www.paypal.com/changed'; },
          reparent: () => { parent = {}; } });
        return result;
      }
      assert.equal(arg, handle); return currentHandle === handle;
    } };
  return { frame, dom };
}

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
      assert.equal(result.guest_pay_metadata.label, null); assert.equal(result.guest_pay_metadata.frame_associated, false);
    } finally { await waitForEnhancedPaymentQuoteSettlement(f.page); guests.forEach(item => item.dom.window.close()); f.dom.window.close(); }
  }
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
