'use strict';
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
let collectListingDetailDom, collectListingReadinessDom;
before(async () => ({ collectListingDetailDom, collectListingReadinessDom } =
  await import('../scripts/discogs_listing_detail_snapshot.mjs')));

const release = '234567', listing = '123456';
const url = `https://www.discogs.com/shop/item/${listing}`;
const html = `<!doctype html><html><head><link rel="canonical" href="${url}"></head><body>
<main><a class="hifi-heading-link-large" href="https://www.discogs.com/release/${release}">Synthetic album</a>
<span class="hifi-grade-chip-display"><span>Media</span><span>Mint</span><span>|</span><span>Sleeve</span><span>Near Mint</span></span>
<div data-testid="item-details"><p class="break-words whitespace-pre-line">No skips. Both discs included.</p></div>
<a href="/seller/Synthetic_1/profile">Synthetic seller</a>
<p class="hifi-body-kind-heavy">100.0%</p>
<a href="https://www.discogs.com/sell/seller_feedback/Synthetic">View all 10,020 reviews</a>
<h2 class="hifi-heading-large">$199.99</h2><div><p>$0.00</p><p>shipping</p></div>
<button disabled>In Cart</button></main></body></html>`;
function collect(markup = html, expected = { release_id: release, listing_id: listing }, current = url) {
  const dom = new JSDOM(markup, { url: current, runScripts: 'outside-only' });
  const { window } = dom, calls = [];
  Object.defineProperty(window.document, 'readyState', { get: () => 'complete' });
  window.Element.prototype.getClientRects = function () { return [{ width: 100, height: 20 }]; };
  Object.defineProperty(window.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
  for (const method of ['click', 'focus', 'submit', 'requestSubmit']) {
    window.HTMLElement.prototype[method] = () => { calls.push(method); throw Error(method); };
  }
  window.fetch = () => { calls.push('fetch'); throw Error('fetch'); };
  const prior = window.document.documentElement.outerHTML;
  const result = window.eval(`(${collectListingDetailDom.toString()})`)(expected);
  assert.equal(window.document.documentElement.outerHTML, prior);
  assert.deepEqual(calls, []);
  window.close();
  return JSON.parse(JSON.stringify(result));
}

test('fixed detail collector reads only exact visible item facts with unknown payable quote', () => {
  const result = collect();
  assert.equal(result.status, 'observed');
  assert.equal(result.complete, true);
  assert.equal(result.release_id, Number(release));
  assert.equal(result.listing_id, listing);
  assert.equal(result.media, 'M'); assert.equal(result.sleeve, 'NM');
  assert.equal(result.seller_percent, 100); assert.equal(result.feedback, 10020);
  assert.equal(result.seller_handle, 'Synthetic_1');
  assert.equal(result.item, '199.99'); assert.equal(result.currency, null);
  assert.equal(result.reported_shipping_text, '$0.00 shipping');
  assert.equal(result.seller_comments, 'No skips. Both discs included.');
  assert.equal(result.seller_comments_supported, true);
  assert.equal(result.available, true); assert.equal(result.purchase_allowed, false);
  assert.equal(result.cart_state, 'already_in_cart');
  assert.ok(!('tax' in result) && !('destination' in result));
});

test('seller identity comes only from one bounded visible profile href', () => {
  assert.equal(collect(html.replace('/seller/Synthetic_1/profile', '/seller/Other/profile'))
    .seller_handle, 'Other');
  for (const markup of [html.replace('/seller/Synthetic_1/profile', '/seller/Synthetic_1/profile?x=1'),
    html.replace('/seller/Synthetic_1/profile', '/seller/Other Name/profile'),
    html.replace('<a href="/seller/Synthetic_1/profile">Synthetic seller</a>', ''),
    html.replace('<a href="/seller/Synthetic_1/profile">Synthetic seller</a>',
      '<a href="/seller/Synthetic_1/profile">One</a><a href="/seller/Other/profile">Two</a>')]) {
    const result = collect(markup);
    assert.equal(result.seller_handle, null);
    assert.equal(result.purchase_allowed, false);
  }
});

test('Generic sleeve is preserved as a distinct disclosed value, not a grade', () => {
  const result = collect(html.replace('<span>Near Mint</span>', '<span>Generic</span>'));
  assert.equal(result.status, 'observed');
  assert.equal(result.complete, true);
  assert.equal(result.sleeve, 'Generic');
  assert.equal(result.purchase_allowed, false);
});

test('wrong release, changed listing URL, ambiguous grades and missing details fail closed', () => {
  assert.equal(collect(html, { release_id: '234568', listing_id: listing }).error_code,
    'listing_release_mismatch');
  assert.equal(collect(html, { release_id: release, listing_id: '123457' }).error_code,
    'listing_url_mismatch');
  assert.equal(collect(html.replace('<span>Near Mint</span>', '<span>Unknown</span>')).error_code,
    'listing_fields_unavailable');
  const missing = collect(html.replace(/<div data-testid="item-details">.*?<\/div>/u, ''));
  assert.equal(missing.status, 'observed');
  assert.equal(missing.seller_comments_supported, false);
  assert.equal(missing.seller_comments, null);
});

test('query strings and extra requested actions are rejected before data extraction', () => {
  assert.equal(collect(html, { release_id: release, listing_id: listing, action: 'cart' }).error_code,
    'invalid_exact_identity');
  assert.equal(collect(html, { release_id: release, listing_id: listing }, `${url}?add=1`).error_code,
    'listing_url_mismatch');
});

test('readiness diagnostic reports structure and restriction markers without page text', () => {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  Object.defineProperty(dom.window.document, 'readyState', { get: () => 'complete' });
  const diagnostic = dom.window.eval(`(${collectListingReadinessDom.toString()})`)();
  assert.equal(diagnostic.main_count, 1);
  assert.equal(diagnostic.details_count, 1);
  assert.equal(diagnostic.grade_count, 1);
  assert.deepEqual([...diagnostic.testids], ['item-details']);
  assert.equal(diagnostic.challenge_marker_present, false);
  assert.equal(diagnostic.password_input_present, false);
  assert.ok(!JSON.stringify(diagnostic).includes('No skips'));
  dom.window.close();
  const restricted = new JSDOM('<main><input type="password"><div id="captcha"></div></main>',
    { url, runScripts: 'outside-only' });
  const blocked = restricted.window.eval(`(${collectListingReadinessDom.toString()})`)();
  assert.equal(blocked.password_input_present, true);
  assert.equal(blocked.challenge_marker_present, true);
  restricted.window.close();
});
