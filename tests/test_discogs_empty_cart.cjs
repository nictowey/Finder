'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

const URL = 'https://www.discogs.com/sell/cart/';
const load = () => import('../scripts/discogs_empty_cart.mjs');
const markup = `<!doctype html><html><head><title>Cart</title></head><body>
  <div id="page" class="cart_page aside_right with_tabs">
    <div id="page_content" role="main">
      <h1>Your cart is empty</h1>
      <p class="page_description">Browse the marketplace.</p>
      <a class="button button-green" href="/sell/list">Browse</a>
      <div class="orders"></div>
      <script id="shipping_hint_template" type="text/template"></script>
    </div>
  </div>
</body></html>`;

function withDom(html, run, { url = URL, ready = 'complete', documentAge = 25,
  performanceNow = () => documentAge } = {}) {
  const dom = new JSDOM(html, { url });
  const previous = { document: global.document, location: global.location };
  Object.defineProperty(dom.window.document, 'readyState', { configurable: true, value: ready });
  Object.defineProperty(dom.window.performance, 'now', { configurable: true, value: performanceNow });
  dom.window.HTMLElement.prototype.getClientRects = () => [{ width: 20, height: 20 }];
  global.document = dom.window.document;
  global.location = dom.window.location;
  try { return run(dom.window.document); }
  finally {
    global.document = previous.document;
    global.location = previous.location;
    dom.window.close();
  }
}

test('observed unique visible empty-cart structure proves only displayed zero rows', async () => {
  const { collectEmptyCartDom } = await load();
  const result = withDom(markup, () => collectEmptyCartDom());
  assert.deepEqual(result, { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
    source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
    document_age_ms: 25, purchase_allowed: false });
  assert.equal('scope_digest' in result, false);
  assert.equal('observed_at_ms' in result, false);
});

test('Marketplace heading outside main is allowed, but second main heading is not', async () => {
  const { collectEmptyCartDom } = await load();
  const observed = markup.replace('<div id="page_content" role="main">',
    '<h1 class="header_with_tabs hide_mobile">Marketplace</h1><div id="page_content" role="main">');
  assert.equal(withDom(observed, () => collectEmptyCartDom()).status, 'verified_empty');
  const duplicateMain = observed.replace('<h1>Your cart is empty</h1>',
    '<h1>Your cart is empty</h1><h1>Your cart is empty</h1>');
  assert.equal(withDom(duplicateMain, () => collectEmptyCartDom()).code,
    'empty_cart_heading_unverified');
});

test('partial page and wrong URL cannot report empty', async () => {
  const { collectEmptyCartDom } = await load();
  assert.equal(withDom(markup, () => collectEmptyCartDom(), { ready: 'interactive' }).code,
    'cart_not_ready');
  assert.equal(withDom(markup, () => collectEmptyCartDom(),
    { url: 'https://www.discogs.com/sell/cart/?other=1' }).code, 'cart_url_unverified');
});

test('duplicate, hidden and generic empty headings are insufficient', async () => {
  const { collectEmptyCartDom } = await load();
  for (const html of [
    markup.replace('<h1>Your cart is empty</h1>',
      '<h1>Your cart is empty</h1><h1>Your cart is empty</h1>'),
    markup.replace('<div class="orders"></div>', '<div class="orders"></div><div class="orders"></div>'),
    markup.replace('<h1>Your cart is empty</h1>', '<h1 hidden>Your cart is empty</h1>'),
    markup.replace('Your cart is empty', 'Empty'),
    markup.replace('<div class="orders"></div>', '<div class="orders" hidden></div>'),
    markup.replace('<div class="orders"></div>', '<div class="orders"><span></span></div>'),
  ]) assert.notEqual(withDom(html, () => collectEmptyCartDom()).status, 'verified_empty');
});

test('challenge/login and unexpected item structures stop', async () => {
  const { collectEmptyCartDom } = await load();
  for (const html of [
    markup.replace('<title>Cart</title>', '<title>Just a moment - Cloudflare</title>'),
    markup.replace('</body>', '<input type="password"></body>'),
    markup.replace('</body>', '<iframe src="https://challenges.cloudflare.com/"></iframe></body>'),
  ]) assert.equal(withDom(html, () => collectEmptyCartDom()).code, 'access_restricted');
  for (const html of [
    markup.replace('</body>', '<table><tbody><tr class="order_row"></tr></tbody></table></body>'),
    markup.replace('</body>', '<div id="cart-order-123" class="order_container"></div></body>'),
    markup.replace('</body>', '<a class="item_link" href="/sell/item/123"></a></body>'),
  ]) assert.equal(withDom(html, () => collectEmptyCartDom()).code, 'cart_items_present');
});

test('wrapper reads only URL, fixed collector, URL and rejects changed page', async () => {
  const { collectEmptyCartDom, inspectCurrentEmptyCart } = await load();
  const calls = [];
  const page = { url: () => { calls.push('url'); return URL; },
    evaluate: fn => { calls.push('evaluate'); assert.equal(fn, collectEmptyCartDom);
      return withDom(markup, () => fn()); } };
  const result = await inspectCurrentEmptyCart(page);
  assert.equal(result.status, 'verified_empty');
  assert.equal(result.cart_url, URL);
  assert.ok(result.document_age_ms >= 25 && result.document_age_ms <= 5000);
  assert.deepEqual(calls, ['url', 'evaluate', 'url']);
  assert.equal('scope_digest' in result || 'observed_at_ms' in result, false);
  let reads = 0;
  const switched = { ...page, url: () => ++reads === 1 ? URL : 'https://www.discogs.com/' };
  assert.equal((await inspectCurrentEmptyCart(switched)).code, 'cart_url_changed');
});

test('wrapper rejects cancellation, unverified collector output and unsupported URLs', async () => {
  const { inspectCurrentEmptyCart } = await load();
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  const page = { url: () => { calls++; return URL; }, evaluate: () => ({}) };
  assert.equal((await inspectCurrentEmptyCart(page, { signal: controller.signal })).code,
    'empty_cart_cancelled');
  assert.equal(calls, 0);
  assert.equal((await inspectCurrentEmptyCart(page)).code, 'empty_cart_unverified');
  assert.equal((await inspectCurrentEmptyCart({ ...page, url: () => `${URL}?x=1` })).code,
    'cart_url_unverified');
});

test('cancellation during DOM read stops without another URL read', async () => {
  const { inspectCurrentEmptyCart } = await load();
  const controller = new AbortController();
  let finish, urls = 0;
  const page = { url: () => { urls++; return URL; },
    evaluate: () => new Promise(resolve => { finish = resolve; }) };
  const running = inspectCurrentEmptyCart(page, { signal: controller.signal });
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  controller.abort();
  finish({ schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
    source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
    purchase_allowed: false });
  assert.equal((await running).code, 'empty_cart_cancelled');
  assert.equal(urls, 1);
});

test('bounded deadline stops an unresolved DOM read', async () => {
  const { inspectCurrentEmptyCart } = await load();
  let urls = 0;
  const page = { url: () => { urls++; return URL; }, evaluate: () => new Promise(() => {}) };
  assert.equal((await inspectCurrentEmptyCart(page, { timeoutMs: 10 })).code,
    'empty_cart_timeout');
  assert.equal(urls, 1);
});

test('only an intrinsically young cart document can report displayed empty rows', async () => {
  const { collectEmptyCartDom } = await load();
  for (const documentAge of [0, 25, 4999, 5000]) {
    const result = withDom(markup, () => collectEmptyCartDom(), { documentAge });
    assert.equal(result.status, 'verified_empty');
    assert.equal(result.document_age_ms, documentAge);
  }
  for (const documentAge of [5000.01, 5001, 60_000, 3_600_000]) {
    const result = withDom(markup, () => collectEmptyCartDom(), { documentAge });
    assert.equal(result.code, 'cart_document_stale');
    assert.equal(result.empty, false);
    assert.equal('document_age_ms' in result, false);
  }
});

test('missing, invalid, throwing or backward intrinsic clocks cannot prove a young document', async () => {
  const { collectEmptyCartDom } = await load();
  for (const documentAge of [-1, NaN, Infinity, -Infinity, '25', null, true]) {
    assert.equal(withDom(markup, () => collectEmptyCartDom(), { documentAge }).code,
      'cart_document_age_unverified');
  }
  for (const performanceNow of [null, false, () => { throw new Error('private clock data'); }]) {
    const result = withDom(markup, () => collectEmptyCartDom(), { performanceNow });
    assert.equal(result.code, 'cart_document_age_unverified');
    assert.equal(JSON.stringify(result).includes('private clock data'), false);
  }
  let reads = 0;
  assert.equal(withDom(markup, () => collectEmptyCartDom(), {
    performanceNow: () => ++reads === 1 ? 25 : 24,
  }).code, 'cart_document_age_unverified');
});

test('crossing the document-age limit during DOM inspection rejects empty proof', async () => {
  const { collectEmptyCartDom } = await load();
  let reads = 0;
  const result = withDom(markup, () => collectEmptyCartDom(), {
    performanceNow: () => ++reads === 1 ? 4999 : 5001,
  });
  assert.equal(result.code, 'cart_document_stale');
  assert.equal(result.empty, false);
  assert.equal(reads, 2);
});

test('rereading an old loaded empty document cannot refresh its intrinsic age', async () => {
  const { collectEmptyCartDom } = await load();
  let age = 100;
  withDom(markup, () => {
    assert.equal(collectEmptyCartDom().status, 'verified_empty');
    age = 60_000;
    assert.equal(collectEmptyCartDom().code, 'cart_document_stale');
    assert.equal(collectEmptyCartDom().code, 'cart_document_stale');
  }, { performanceNow: () => age });
});

test('wrapper requires intrinsic age rather than a newly attached observation timestamp', async () => {
  const { inspectCurrentEmptyCart } = await load();
  const valid = { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
    source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
    purchase_allowed: false, observed_at_ms: Date.now() };
  for (const documentAge of [undefined, -1, 5001, NaN, Infinity, '25', null, true]) {
    const result = await inspectCurrentEmptyCart({ url: () => URL,
      evaluate: async () => ({ ...valid, document_age_ms: documentAge }) });
    assert.equal(result.code, 'empty_cart_unverified');
    assert.equal(result.empty, false);
  }
});

test('wrapper accounts for document aging during the bounded read without loading anything', async () => {
  const { inspectCurrentEmptyCart } = await load();
  const actualNow = Date.now;
  let now = actualNow();
  Date.now = () => now;
  const calls = [];
  try {
    const result = await inspectCurrentEmptyCart({
      url: () => { calls.push('url'); return URL; },
      evaluate: async () => {
        calls.push('evaluate'); now += 2;
        return { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
          source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
          document_age_ms: 4999, purchase_allowed: false };
      },
      goto: () => { throw new Error('unexpected navigation'); },
      reload: () => { throw new Error('unexpected refresh'); },
    });
    assert.equal(result.code, 'cart_document_stale');
    assert.deepEqual(calls, ['url', 'evaluate', 'url']);
  } finally { Date.now = actualNow; }
});
