'use strict';
// Sanitized markup based only on the observed enhanced checkout summary shape.
// No browser session, network, payment field, or committing action.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
let collectEnhancedCheckoutDom, inspectCurrentEnhancedCheckout;
before(async () => ({ collectEnhancedCheckoutDom, inspectCurrentEnhancedCheckout } =
  await import('../scripts/discogs_enhanced_checkout.mjs')));

const URL = 'https://www.discogs.com/marketplace/checkout';
const HTML = `<!doctype html><html><head><title>Checkout</title></head><body>
  <h1>Checkout from ExampleSeller</h1>
  <div class="_checkout-summary__content_55auu_25">
    <ul class="_checkout-summary__unordered-list_55auu_30">
      <li class="_checkout-sell-item__list-item_brkft_1">SECRET ITEM TITLE / ADDRESS</li>
    </ul>
    <dl class="_checkout-summary__totals_55auu_34">
      <div class="_checkout-summary__subtotal_55auu_38"><dt>Subtotal</dt>
        <dd class="_checkout-summary__money-value_55auu_56">$0.10 USD</dd></div>
      <div class="_checkout-summary__subtotal_55auu_38"><dt>Shipping via Economy - Media Mail</dt>
        <dd class="_checkout-summary__money-value_55auu_56">$4.95 USD</dd></div>
      <div class="_checkout-summary__total--color-red_55auu_45"><dt>Total</dt>
        <dd class="_checkout-summary__money-value_55auu_56">$5.05 USD</dd></div>
    </dl>
  </div>
  <iframe title="PayPal SECRET PAYMENT FRAME"></iframe>
  <aside>SECRET STREET ADDRESS</aside>
</body></html>`;

function fixture(html = HTML, url = URL) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  dom.window.Element.prototype.getClientRects = function () {
    return this.hasAttribute('data-no-layout') ? [] : [{ width: 100, height: 20 }];
  };
  for (const method of ['click', 'focus']) {
    dom.window.HTMLElement.prototype[method] = () => { throw new Error(`Forbidden ${method}`); };
  }
  dom.window.fetch = () => { throw new Error('Forbidden network'); };
  const calls = [];
  const page = { url: () => { calls.push('url'); return url; },
    evaluate: fn => { calls.push('evaluate'); assert.equal(fn, collectEnhancedCheckoutDom);
      return dom.window.eval(`(${fn.toString()})()`); } };
  return { dom, page, calls };
}

test('one observed summary yields minimal display evidence, never final quote or identity', async () => {
  const f = fixture();
  try {
    const result = await inspectCurrentEnhancedCheckout(f.page);
    assert.equal(result.status, 'observed');
    assert.deepEqual(result.displayed, {
      subtotal: { amount: '0.10', currency: 'USD' },
      shipping: { amount: '4.95', currency: 'USD' },
      total: { amount: '5.05', currency: 'USD' }, tax: null, fees: null,
    });
    assert.equal(result.seller_handle, 'ExampleSeller');
    assert.equal(result.selected_shipping_label, 'Economy - Media Mail');
    assert.equal(result.visible_row_count, 1);
    assert.equal(result.quantity, null); assert.equal(result.quantity_verified, false);
    assert.equal(result.listing_id, null); assert.equal(result.release_id, null);
    assert.equal(result.final_quote_verified, false);
    assert.equal(result.purchase_allowed, false);
    assert.equal(result.legacy_checkout_compatible, false);
    assert.deepEqual(f.calls, ['url', 'evaluate', 'url']);
    for (const secret of ['SECRET', 'PayPal', 'ADDRESS']) {
      assert.equal(JSON.stringify(result).includes(secret), false);
    }
  } finally { f.dom.window.close(); }
});

test('strict route rejects before DOM access and changed URL rejects after read', async () => {
  const wrong = fixture(HTML, 'https://www.discogs.com/sell/cart/');
  try {
    assert.equal((await inspectCurrentEnhancedCheckout(wrong.page)).code, 'url_unverified');
    assert.deepEqual(wrong.calls, ['url']);
  } finally { wrong.dom.window.close(); }
  const f = fixture(); let reads = 0;
  f.page.url = () => ++reads === 1 ? URL : 'https://www.discogs.com/sell/cart/';
  try { assert.equal((await inspectCurrentEnhancedCheckout(f.page)).code, 'url_changed'); }
  finally { f.dom.window.close(); }
});

test('wrapper validates a returned snapshot and discards unapproved extra fields', async () => {
  const snapshot = { schema: 'discogs_enhanced_checkout_dom_v1', status: 'observed',
    source: 'visible_enhanced_checkout_dom', purchase_allowed: false,
    seller_handle: 'ExampleSeller', visible_row_count: 1,
    displayed: { subtotal: { amount: '0.10', currency: 'USD' },
      shipping: { amount: '4.95', currency: 'USD' },
      total: { amount: '5.05', currency: 'USD' } },
    selected_shipping_label: 'Economy - Media Mail',
    secret: 'SECRET ADDRESS', listing_id: '9999', quantity: 1 };
  const page = { url: () => URL, evaluate: async () => snapshot };
  const observed = await inspectCurrentEnhancedCheckout(page);
  assert.equal(observed.status, 'observed');
  assert.equal(observed.listing_id, null); assert.equal(observed.quantity, null);
  assert.equal(JSON.stringify(observed).includes('SECRET'), false);
  assert.equal(JSON.stringify(observed).includes('9999'), false);
  for (const bad of [
    { subtotal: { amount: 0.10, currency: 'USD' } },
    { shipping: { amount: '4.95', currency: 'EUR' } },
    { total: { amount: '5.06', currency: 'USD' } },
  ]) {
    const corrupted = { ...snapshot, displayed: { ...snapshot.displayed, ...bad } };
    assert.equal((await inspectCurrentEnhancedCheckout({ url: () => URL,
      evaluate: async () => corrupted })).status, 'blocked');
  }
  const arbitraryError = { schema: 'discogs_enhanced_checkout_dom_v1',
    status: 'blocked', code: 'secret_token_leaked' };
  assert.equal((await inspectCurrentEnhancedCheckout({ url: () => URL,
    evaluate: async () => arbitraryError })).code, 'summary_unverified');
});

test('challenge, duplicate, hidden, partial and malformed totals fail closed', async () => {
  for (const [html, code] of [
    [HTML.replace('<title>Checkout</title>', '<title>Just a moment - Cloudflare</title>'), 'access_restricted'],
    [HTML.replace('<aside>SECRET STREET ADDRESS</aside>',
      '<iframe src="https://challenges.cloudflare.com/turnstile"></iframe>'), 'access_restricted'],
    [HTML.replace('<aside>SECRET STREET ADDRESS</aside>',
      '<div class="captcha-box"></div>'), 'access_restricted'],
    [HTML.replace('</ul>', '<li class="_checkout-sell-item__list-item_brkft_1">Second</li></ul>'), 'row_count_unverified'],
    [HTML.replace('</ul>', '<li class="_checkout-sell-item__list-item_brkft_1" hidden>Second</li></ul>'), 'row_count_unverified'],
    [HTML.replace('<h1>Checkout from ExampleSeller</h1>', '<h1>Checkout from ExampleSeller</h1><h1>Other</h1>'), 'seller_heading_unverified'],
    [HTML.replace('<h1>Checkout from ExampleSeller</h1>', '<h1>Checkout from <span hidden>ExampleSeller</span></h1>'), 'seller_heading_unverified'],
    [HTML.replace('</dl>', '<div class="_checkout-summary__subtotal_55auu_38" hidden><dt>Tax</dt><dd>$1.00 USD</dd></div></dl>'), 'totals_shape_unverified'],
    [HTML.replace('</dl>', '<div class="_checkout-summary__total--color-red_55auu_45"><dt>Total</dt><dd>$5.05 USD</dd></div></dl>'), 'totals_shape_unverified'],
    [HTML.replace('class="_checkout-summary__content_55auu_25"', 'class="_checkout-summary__content_55auu_25" hidden'), 'summary_unverified'],
    [HTML.replace('Shipping via Economy - Media Mail', 'Shipping'), 'totals_labels_unverified'],
    [HTML.replace('Shipping via Economy - Media Mail', 'Shipping via <span hidden>Other</span>Economy - Media Mail'), 'totals_labels_unverified'],
    [HTML.replace('$4.95 USD', '<span hidden>$4.95 USD</span>'), 'totals_labels_unverified'],
    [HTML.replace('$4.95 USD', '<span hidden>HIDDEN</span>$4.95 USD'), 'totals_labels_unverified'],
    [HTML.replace('$4.95 USD', '$4.95'), 'displayed_money_unverified'],
    [HTML.replace('$4.95 USD', '4.95 EUR'), 'displayed_money_unverified'],
    [HTML.replace('$5.05 USD', '$5.06 USD'), 'visible_total_inconsistent'],
    [HTML.replace('$5.05 USD', 'To be determined by seller'), 'displayed_money_unverified'],
  ]) {
    const f = fixture(html);
    try {
      const result = await inspectCurrentEnhancedCheckout(f.page);
      assert.equal(result.status, 'blocked'); assert.equal(result.code, code);
      assert.equal(result.purchase_allowed, false);
    } finally { f.dom.window.close(); }
  }
});

test('seller heading with a hidden descendant is not the observed plain-text shape', async () => {
  const f = fixture(HTML.replace('<h1>Checkout from ExampleSeller</h1>',
    '<h1>Checkout from <span hidden>HiddenSeller</span>ExampleSeller</h1>'));
  try {
    const result = await inspectCurrentEnhancedCheckout(f.page);
    assert.equal(result.status, 'blocked');
    assert.equal(result.code, 'seller_heading_unverified');
    assert.equal(JSON.stringify(result).includes('HiddenSeller'), false);
  } finally { f.dom.window.close(); }
});

test('cancelled or timed-out current-page read cannot return a quote', async () => {
  const f = fixture();
  try {
    const controller = new AbortController(); controller.abort();
    assert.equal((await inspectCurrentEnhancedCheckout(f.page,
      { signal: controller.signal })).code, 'read_cancelled');
    f.page.evaluate = () => new Promise(() => {});
    assert.equal((await inspectCurrentEnhancedCheckout(f.page,
      { timeoutMs: 5 })).code, 'read_timeout');
    const during = new AbortController();
    const pending = inspectCurrentEnhancedCheckout(f.page,
      { signal: during.signal, timeoutMs: 500 });
    during.abort();
    assert.equal((await pending).code, 'read_cancelled');
  } finally { f.dom.window.close(); }
});

test('a synchronous read that completes after deadline is still rejected', async () => {
  const page = { url: () => {
    const until = Date.now() + 8;
    while (Date.now() < until) { /* synthetic delayed port */ }
    return URL;
  }, evaluate: () => { throw new Error('late read must not reach DOM'); } };
  assert.equal((await inspectCurrentEnhancedCheckout(page, { timeoutMs: 2 })).code, 'read_timeout');
});
