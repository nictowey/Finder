'use strict';
// Synthetic page only. No live browser, provider, credentials or order actions.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let prepareCurrentListingCart, collectCartDom, CART_PREPARE_TIMEOUT_MS;
before(async () => {
  ({ prepareCurrentListingCart, CART_PREPARE_TIMEOUT_MS } = await import('../scripts/discogs_cart_prepare.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
});
const ID = '123456';
const LISTING = `https://www.discogs.com/shop/item/${ID}`;
const CART = 'https://www.discogs.com/sell/cart/';
const OPTIONS = { expectedListingId: ID, allowAddToCart: true };
const HREF = `/sell/cart/?add=${ID}&ev=`;

function snapshot(id = ID) {
  const quote = (label, value) => ({ count: 1, visible: true, label, value });
  return {
    schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: `/sell/item/${id}`, listing_matches_expected: id === ID,
    quantity_control_count: 0, address_container_count: 1, missing_address: true,
    edit_shipping_address_present: true, summary_count: 1,
    subtotal: quote('Subtotal', '$20.00'), shipping: quote('Shipping', 'To be determined by seller'),
    tax: { count: 1, visible: false, label: null, value: null },
    total: quote('Total', 'To be determined by seller'), final_button_count: 1,
    final_button_visible: true, final_button_enabled: true, agreement_count: 1,
    agreement_visible: true, payment_control_count: 1, payment_control_visible: true,
  };
}

function syntheticPage(options = {}) {
  const state = { url: options.url ?? LISTING, clicks: 0, navigations: [], calls: [], urlReads: 0 };
  const permitted = (object, methods) => new Proxy(object, {
    get(target, key) {
      assert.ok(methods.includes(key), `Forbidden operation ${String(key)}`);
      return target[key];
    },
  });
  const page = permitted({
    url() {
      state.calls.push('url'); state.urlReads++;
      return options.urlRead ? options.urlRead(state) : options.asyncUrl ? Promise.resolve(state.url) : state.url;
    },
    async evaluate(fn, id) {
      state.calls.push('evaluate'); assert.equal(fn, collectCartDom); assert.equal(id, ID);
      if (options.evaluateError) throw options.evaluateError;
      return options.snapshot ?? snapshot();
    },
    async goto(url, gotoOptions) {
      state.calls.push('goto'); state.navigations.push(url);
      assert.equal(url, `${new URL(options.url ?? LISTING).origin}/sell/cart/`);
      assert.ok(!url.includes('?'));
      assert.equal(gotoOptions.waitUntil, 'domcontentloaded');
      assert.ok(gotoOptions.timeout > 0 && gotoOptions.timeout <= CART_PREPARE_TIMEOUT_MS);
      assert.deepEqual(Object.keys(gotoOptions).sort(), ['timeout', 'waitUntil']);
      if (options.gotoError) throw options.gotoError;
      state.url = options.afterGotoUrl ?? url;
    },
    getByRole(role, query) {
      state.calls.push(`getByRole:${role}`);
      assert.deepEqual(query, { name: role === 'button' ? 'In Cart' : 'Add to Cart', exact: true });
      assert.ok(['button', 'link'].includes(role));
      const items = role === 'button' ? options.buttons ?? [] : options.links ?? [{ visible: false }, { visible: true }];
      return permitted({
        async count() {
          if (role === 'button' && options.buttonCount) return options.buttonCount();
          return items.length;
        },
        nth(index) {
          const item = items[index]; assert.ok(item);
          return permitted({
            async isVisible() { return item.visible; },
            async isDisabled() { assert.equal(role, 'button'); return item.disabled; },
            async getAttribute(name) {
              assert.equal(role, 'link'); assert.equal(name, 'href');
              if (options.onHrefRead) options.onHrefRead(state);
              return item.href ?? HREF;
            },
            async click(clickOptions) {
              assert.equal(role, 'link'); assert.equal(item.visible, true);
              assert.ok(clickOptions.timeout > 0 && clickOptions.timeout <= CART_PREPARE_TIMEOUT_MS);
              assert.deepEqual(Object.keys(clickOptions), ['timeout']);
              state.calls.push('click'); state.clicks++;
              if (options.click) return options.click(state);
              state.url = options.afterClickUrl ?? CART;
            },
          }, role === 'button' ? ['isVisible', 'isDisabled'] : ['isVisible', 'getAttribute', 'click']);
        },
      }, ['count', 'nth']);
    },
  }, ['url', 'evaluate', 'getByRole', 'goto']);
  return { page, state };
}

function assertSafe(result) {
  assert.equal(result.status, 'blocked');
  assert.equal(result.purchase_allowed, false);
  assert.equal(result.stop_required, true);
  assert.match(result.code, /^[a-z_]+$/u);
  assert.match(result.preparation_state, /^[a-z_]+$/u);
  if (result.cart_inspection) {
    assert.equal(result.cart_inspection.purchase_allowed, false);
    assert.equal(result.cart_inspection.actual_final_verification, 'unsupported');
  }
  for (const value of ['123456', '20.00', 'https:', '/sell/', 'SECRET']) assert.ok(!JSON.stringify(result).includes(value));
}

test('default preparation is read-only and cannot add without explicit true', async () => {
  for (const options of [{ expectedListingId: ID }, { expectedListingId: ID, allowAddToCart: false }]) {
    const { page, state } = syntheticPage();
    const result = await prepareCurrentListingCart(page, options);
    assertSafe(result); assert.equal(result.code, 'add_to_cart_not_authorized');
    assert.equal(state.clicks, 0); assert.deepEqual(state.calls, ['url']);
  }
});

test('only one visible responsive Add to Cart link is clicked once, followed by read-only cart inspection', async () => {
  const { page, state } = syntheticPage();
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'cart_observed_after_add');
  assert.equal(result.preparation_state, 'cart_observed'); assert.equal(state.clicks, 1);
  assert.ok(result.cart_inspection.observations.includes('shipping_address_missing'));
  assert.ok(result.cart_inspection.observations.includes('tax_hidden_unknown'));
  const repeated = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(repeated); assert.equal(repeated.code, 'cart_already_contains_expected_listing');
  assert.equal(state.clicks, 1);
  state.url = LISTING;
  const back = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(back); assert.equal(back.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});

test('current cart is only inspected; mismatched item is neither removed nor supplemented', async () => {
  for (const id of [ID, '999999']) {
    const { page, state } = syntheticPage({ url: CART, snapshot: snapshot(id) });
    const result = await prepareCurrentListingCart(page, { expectedListingId: ID });
    assertSafe(result); assert.equal(result.code, id === ID ? 'cart_already_contains_expected_listing' : 'cart_requires_review');
    assert.equal(state.clicks, 0); assert.ok(!state.calls.some(call => call.startsWith('getByRole:')));
  }
});

test('visible disabled In Cart button stops without trusting header or navigating', async () => {
  const { page, state } = syntheticPage({ buttons: [{ visible: false }, { visible: true, disabled: true }] });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'listing_reports_in_cart_unverified');
  assert.equal(state.clicks, 0); assert.equal(state.url, LISTING);
  assert.ok(!state.calls.includes('getByRole:link'));
});

test('wrong listing, origin, path, query, fragment or user info stops before controls are read', async () => {
  for (const url of ['https://www.discogs.com/shop/item/999999', `${LISTING}?x=1`, `${LISTING}#fragment`,
    'http://www.discogs.com/shop/item/123456', 'https://evil.invalid/shop/item/123456',
    'https://www.discogs.com:443/shop/item/123456', 'https://user@www.discogs.com/shop/item/123456',
    'https://www.discogs.com/shop/item/123456/', `${CART}?add=123456&ev=`]) {
    const { page, state } = syntheticPage({ url });
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.equal(result.code, 'current_url_not_allowed');
    assert.deepEqual(state.calls, ['url']); assert.equal(state.clicks, 0);
  }
});

test('strict options reject missing ID, unsafe IDs, unknown flags and non-boolean permission before page access', async () => {
  for (const options of [undefined, {}, { expectedListingId: 123456 }, { expectedListingId: '9007199254740992' },
    { expectedListingId: '01' }, { ...OPTIONS, enableBuy: true }, { ...OPTIONS, allowAddToCart: 'true' },
    Object.defineProperty({ expectedListingId: ID }, 'allowAddToCart', { get() { throw new Error('Getter must not run'); } })]) {
    const { page, state } = syntheticPage();
    const result = await prepareCurrentListingCart(page, options);
    assertSafe(result); assert.ok(result.code.startsWith('invalid_')); assert.deepEqual(state.calls, []);
  }
});

test('missing, hidden, duplicate visible, excessive or unknown controls stop with no action', async () => {
  for (const options of [
    { links: [] }, { links: [{ visible: false }] }, { links: [{ visible: true }, { visible: true }] },
    { links: [{ visible: false }, { visible: false }, { visible: true }] },
    { buttons: [{ visible: true, disabled: false }] },
    { buttons: [{ visible: true, disabled: true }, { visible: true, disabled: true }] },
  ]) {
    const { page, state } = syntheticPage(options);
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.ok(result.code.startsWith('unsupported_')); assert.equal(state.clicks, 0);
  }
});

test('only the observed exact add href is allowed, never direct URL requests', async () => {
  for (const href of ['/sell/cart/?add=999999&ev=', '/sell/cart/?add=123456',
    '/sell/cart/?ev=&add=123456', 'https://www.discogs.com/sell/cart/?add=123456&ev=',
    '/sell/cart/?add=123456&ev=&extra=1', '/sell/cart/?add=123456&ev=#fragment',
    '/sell/cart/?add=123456&ev=\n', '/orders/new']) {
    const { page, state } = syntheticPage({ links: [{ visible: true, href }] });
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.equal(result.code, 'unexpected_add_link'); assert.equal(state.clicks, 0);
  }
});

test('current URL is rechecked immediately before the only possible click', async () => {
  const { page, state } = syntheticPage({ onHrefRead(state) { state.url = 'https://evil.invalid/'; } });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'listing_changed_before_add'); assert.equal(state.clicks, 0);
});

test('concurrent calls cannot clear the first call lock or cause a second add', async () => {
  let release, entered;
  const ready = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const { page, state } = syntheticPage({ buttonCount() { entered(); return gate; } });
  const first = prepareCurrentListingCart(page, OPTIONS);
  await ready;
  for (let index = 0; index < 3; index++) {
    const concurrent = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(concurrent); assert.equal(concurrent.code, 'preparation_in_progress');
  }
  assert.equal(state.clicks, 0);
  release(0);
  assert.equal((await first).code, 'cart_observed_after_add'); assert.equal(state.clicks, 1);
});

test('error thrown by the click is uncertain, is sanitized, and cannot retry from the listing', async () => {
  const { page, state } = syntheticPage({ click() { throw new Error('SECRET_ERROR'); } });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'add_outcome_uncertain'); assert.equal(state.clicks, 1);
  const repeated = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(repeated); assert.equal(repeated.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});

test('unconfirmed destination, unexpected query and mismatched cart after add stay blocked without retries', async () => {
  for (const options of [{ afterClickUrl: 'https://evil.invalid/' },
    { afterClickUrl: `${CART}?add=123456&ev=&extra=1` }, { snapshot: snapshot('999999') },
    { evaluateError: new Error('SECRET_ERROR') }]) {
    const { page, state } = syntheticPage(options);
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.equal(result.code, 'add_outcome_unconfirmed'); assert.equal(state.clicks, 1);
    state.url = LISTING;
    assert.equal((await prepareCurrentListingCart(page, OPTIONS)).code, 'add_attempt_already_recorded');
    assert.equal(state.clicks, 1);
  }
});

test('verified post-add query gets exactly one canonical navigation, never replaying the add URL', async () => {
  const { page, state } = syntheticPage({ afterClickUrl: `${CART}?add=123456&ev=` });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'cart_observed_after_add');
  assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
  const again = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(again); assert.equal(again.code, 'cart_already_contains_expected_listing');
  assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
});

test('canonical navigation error or unexpected landing leaves mutation outcome uncertain and cannot retry', async () => {
  for (const extra of [{ gotoError: new Error('SECRET_ERROR') }, { afterGotoUrl: 'https://evil.invalid/' }]) {
    const { page, state } = syntheticPage({ afterClickUrl: `${CART}?add=123456&ev=`, ...extra });
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.ok(['add_outcome_uncertain', 'add_outcome_unconfirmed'].includes(result.code));
    assert.deepEqual(state.navigations, [CART]); assert.equal(state.clicks, 1);
    state.url = LISTING;
    assert.equal((await prepareCurrentListingCart(page, OPTIONS)).code, 'add_attempt_already_recorded');
    assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
  }
});

test('in-flight and attempted guards cover the whole page even when another caller changes listing ID', async () => {
  let release, entered;
  const ready = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const { page, state } = syntheticPage({ buttonCount() { entered(); return gate; } });
  const first = prepareCurrentListingCart(page, OPTIONS);
  await ready;
  state.url = 'https://www.discogs.com/shop/item/999999';
  const other = await prepareCurrentListingCart(page, { expectedListingId: '999999', allowAddToCart: true });
  assertSafe(other); assert.equal(other.code, 'preparation_in_progress');
  release(0);
  assert.equal((await first).code, 'listing_changed_before_add'); assert.equal(state.clicks, 0);
  state.url = LISTING;
  assert.equal((await prepareCurrentListingCart(page, OPTIONS)).code, 'cart_observed_after_add');
  state.url = 'https://www.discogs.com/shop/item/999999';
  const afterAttempt = await prepareCurrentListingCart(page, { expectedListingId: '999999', allowAddToCart: true });
  assertSafe(afterAttempt); assert.equal(afterAttempt.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});

test('click timeout preserves the attempted guard and never retries a delayed outcome', { timeout: 5_000 }, async () => {
  const { page, state } = syntheticPage({ click() { return new Promise(() => {}); } });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'add_outcome_uncertain'); assert.equal(state.clicks, 1);
  assert.equal((await prepareCurrentListingCart(page, OPTIONS)).code, 'add_attempt_already_recorded');
  assert.equal(state.clicks, 1);
});

test('definitive read failure before click permits later fresh inspection, not automatic retry', async () => {
  let fail = true;
  const { page, state } = syntheticPage({ buttonCount() {
    if (fail) throw new Error('SECRET_READ_ERROR');
    return 0;
  } });
  const first = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(first); assert.equal(first.code, 'preparation_read_failed'); assert.equal(state.clicks, 0);
  fail = false;
  assert.equal((await prepareCurrentListingCart(page, OPTIONS)).code, 'cart_observed_after_add');
  assert.equal(state.clicks, 1);
});

test('preparation supports asynchronous initial, pre-click, post-click and post-navigation URL reads', async () => {
  const { page, state } = syntheticPage({ asyncUrl: true, afterClickUrl: `${CART}?add=123456&ev=` });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'cart_observed_after_add');
  assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
  assert.ok(state.urlReads >= 7);
});

test('asynchronous wrong initial URL or changed pre-click URL never causes a mutation', async () => {
  for (const options of [{ asyncUrl: true, url: 'https://evil.invalid/' },
    { asyncUrl: true, onHrefRead(state) { state.url = 'https://evil.invalid/'; } }]) {
    const { page, state } = syntheticPage(options);
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.ok(['current_url_not_allowed', 'listing_changed_before_add'].includes(result.code));
    assert.equal(state.clicks, 0); assert.deepEqual(state.navigations, []);
  }
});

test('asynchronous initial and pre-click URL hangs are bounded with no mutations', { timeout: 5_000 }, async () => {
  await Promise.all([1, 2, 3].map(async hangAt => {
    const { page, state } = syntheticPage({ urlRead(state) {
      return state.urlReads === hangAt ? new Promise(() => {}) : Promise.resolve(state.url);
    } });
    const result = await prepareCurrentListingCart(page, OPTIONS);
    assertSafe(result); assert.equal(result.code, 'preparation_read_timeout');
    assert.equal(state.clicks, 0); assert.deepEqual(state.navigations, []);
    if (hangAt === 1) assert.deepEqual(state.calls, ['url']);
  }));
});

test('post-click async URL timeout is uncertain and preserves the no-retry guard', { timeout: 5_000 }, async () => {
  let hang = true;
  const { page, state } = syntheticPage({ urlRead(state) {
    return hang && state.clicks > 0 ? new Promise(() => {}) : Promise.resolve(state.url);
  } });
  const result = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(result); assert.equal(result.code, 'add_outcome_uncertain'); assert.equal(state.clicks, 1);
  hang = false; state.url = LISTING;
  const repeated = await prepareCurrentListingCart(page, OPTIONS);
  assertSafe(repeated); assert.equal(repeated.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});
