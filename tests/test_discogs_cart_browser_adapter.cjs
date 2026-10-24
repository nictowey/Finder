'use strict';
// Synthetic supported-tab contract only; these tests never connect to a browser.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let createSupportedLocalCartPage, prepareCurrentListingCart, collectCartDom;
before(async () => {
  ({ createSupportedLocalCartPage } = await import('../scripts/discogs_cart_browser_adapter.mjs'));
  ({ prepareCurrentListingCart } = await import('../scripts/discogs_cart_prepare.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
});
const ID = '123456';
const LISTING = `https://www.discogs.com/shop/item/${ID}`;
const CART = 'https://www.discogs.com/sell/cart/';
const OPTIONS = { expectedListingId: ID, allowAddToCart: true };

function cartSnapshot() {
  const quote = (label, value) => ({ count: 1, visible: true, label, value });
  return {
    schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: `/sell/item/${ID}`, listing_matches_expected: true,
    quantity_control_count: 0, address_container_count: 1, missing_address: true,
    edit_shipping_address_present: true, summary_count: 1,
    subtotal: quote('Subtotal', '$100.00 USD'), shipping: quote('Shipping', 'To be determined by seller'),
    tax: { count: 1, visible: false, label: null, value: null },
    total: quote('Total', 'To be determined by seller'), final_button_count: 1,
    final_button_visible: true, final_button_enabled: true, agreement_count: 1,
    agreement_visible: true, payment_control_count: 1, payment_control_visible: true,
  };
}

function localTab(options = {}) {
  const state = { url: LISTING, reads: 0, clicks: 0, navigations: [], clickOptions: [], calls: [] };
  const strict = (target, names) => new Proxy(target, { get(object, key) {
    assert.ok(names.includes(key), `Unsupported local tool ${String(key)}`); return object[key];
  } });
  const tab = strict({
    async url() {
      state.calls.push('url'); state.reads++;
      if (state.reads === 1 && options.initialDelayMs) await new Promise(resolve => setTimeout(resolve, options.initialDelayMs));
      return state.url;
    },
    async goto(...args) {
      state.calls.push('goto');
      assert.equal(args.length, 1, 'Local goto accepts only the URL, not Playwright options');
      assert.equal(args[0], CART); state.navigations.push(args[0]);
      state.url = options.unsettledGoto ? 'https://www.discogs.com/sell/cart/?add=123456&ev=' : CART;
    },
    playwright: strict({
      async evaluate(fn, arg) {
        state.calls.push('evaluate'); assert.equal(fn, collectCartDom); assert.equal(arg, ID);
        return cartSnapshot();
      },
      getByRole(role, query) {
        state.calls.push(`role:${role}`);
        assert.deepEqual(query, { name: role === 'button' ? 'In Cart' : 'Add to Cart', exact: true });
        assert.ok(['button', 'link'].includes(role));
        const hasButton = Object.hasOwn(options, 'enabled');
        return strict({
          async count() { return role === 'button' ? Number(hasButton) : 1; },
          nth(index) {
            assert.equal(index, 0);
            return strict({
              async isVisible() { return Object.hasOwn(options, 'visible') ? options.visible : true; },
              async isEnabled() { assert.equal(role, 'button'); return options.enabled; },
              async getAttribute(name) { assert.equal(role, 'link'); assert.equal(name, 'href'); return `/sell/cart/?add=${ID}&ev=`; },
              async click(clickOptions) {
                assert.equal(role, 'link'); assert.deepEqual(Object.keys(clickOptions), ['timeoutMs']);
                assert.ok(Number.isInteger(clickOptions.timeoutMs) && clickOptions.timeoutMs >= 1 && clickOptions.timeoutMs <= 30_000);
                state.clickOptions.push(clickOptions); state.clicks++; state.calls.push('click');
                if (options.hangingClick) return new Promise(() => {});
                state.url = `${CART}?add=${ID}&ev=`;
              },
            }, ['isVisible', 'isEnabled', 'getAttribute', 'click']);
          },
        }, ['count', 'nth']);
      },
    }, ['evaluate', 'getByRole']),
  }, ['url', 'goto', 'playwright']);
  return { tab, state };
}

function safe(result) {
  assert.equal(result.purchase_allowed, false); assert.equal(result.stop_required, true);
  assert.equal(result.status, 'blocked');
  if (result.cart_inspection) assert.equal(result.cart_inspection.purchase_allowed, false);
}

test('explicit adapter maps the supported surface and translates click timeout exactly', async () => {
  const { tab, state } = localTab();
  const page = createSupportedLocalCartPage(tab);
  assert.deepEqual(state.calls, [], 'Factory creates no session and invokes no browser operation');
  const result = await prepareCurrentListingCart(page, { ...OPTIONS, timeoutMs: 5_000 });
  safe(result); assert.equal(result.code, 'cart_observed_after_add');
  assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
  assert.ok(state.clickOptions[0].timeoutMs > 2_000 && state.clickOptions[0].timeoutMs <= 5_000);
  assert.ok(state.calls.includes('evaluate'));
});

test('repeat wrapping returns the same frozen page and cannot reset an attempted-add guard', async () => {
  const { tab, state } = localTab();
  const first = createSupportedLocalCartPage(tab), second = createSupportedLocalCartPage(tab);
  assert.equal(first, second); assert.equal(Object.isFrozen(first), true);
  assert.equal((await prepareCurrentListingCart(first, OPTIONS)).code, 'cart_observed_after_add');
  state.url = LISTING;
  const result = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
  safe(result); assert.equal(result.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});

test('strict enabled conversion recognizes only booleans, never truthiness', async () => {
  for (const enabled of [false, true, 'false', 0, 1, null, undefined, {}]) {
    const { tab, state } = localTab({ enabled });
    const result = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
    safe(result); assert.equal(state.clicks, 0); assert.deepEqual(state.navigations, []);
    assert.equal(result.code, enabled === false ? 'listing_reports_in_cart_unverified' :
      enabled === true ? 'unsupported_listing_controls' : 'preparation_read_failed');
  }
});

test('strict visibility conversion rejects unsupported booleans before any click', async () => {
  for (const visible of [false, 'true', 0, 1, null, undefined, {}]) {
    const { tab, state } = localTab({ visible });
    const result = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
    safe(result); assert.equal(state.clicks, 0);
    assert.equal(result.code, visible === false ? 'unsupported_add_link_shape' : 'preparation_read_failed');
  }
});

test('explicit five-second budget accommodates delayed reads while default remains two seconds', { timeout: 7_000 }, async () => {
  const ordinary = localTab({ initialDelayMs: 2_100 });
  const extended = localTab({ initialDelayMs: 2_100 });
  const [defaultResult, extendedResult] = await Promise.all([
    prepareCurrentListingCart(createSupportedLocalCartPage(ordinary.tab), OPTIONS),
    prepareCurrentListingCart(createSupportedLocalCartPage(extended.tab), { ...OPTIONS, timeoutMs: 5_000 }),
  ]);
  safe(defaultResult); safe(extendedResult);
  assert.equal(defaultResult.code, 'preparation_read_timeout'); assert.equal(ordinary.state.clicks, 0);
  assert.equal(extendedResult.code, 'cart_observed_after_add'); assert.equal(extended.state.clicks, 1);
});

test('timeout after supported click is uncertain and repeated wrapping cannot repeat it', { timeout: 5_000 }, async () => {
  const { tab, state } = localTab({ hangingClick: true });
  const result = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
  safe(result); assert.equal(result.code, 'add_outcome_uncertain'); assert.equal(state.clicks, 1);
  const repeated = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
  safe(repeated); assert.equal(repeated.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1);
});

test('local goto is not assumed to settle navigation; current URL must be verified before DOM', async () => {
  const { tab, state } = localTab({ unsettledGoto: true });
  const result = await prepareCurrentListingCart(createSupportedLocalCartPage(tab), OPTIONS);
  safe(result); assert.equal(result.code, 'add_outcome_unconfirmed');
  assert.equal(state.clicks, 1); assert.deepEqual(state.navigations, [CART]);
  assert.ok(!state.calls.includes('evaluate'));
});

test('adapter exposes no order controls, arbitrary evaluation, mutation URL navigation or unbounded click option', () => {
  const { tab, state } = localTab(); const page = createSupportedLocalCartPage(tab);
  assert.throws(() => page.getByRole('button', { name: 'Place order and pay now', exact: true }));
  assert.throws(() => page.evaluate(() => {}, ID));
  assert.throws(() => page.goto(`${CART}?add=${ID}&ev=`, { timeout: 1_000 }));
  const link = page.getByRole('link', { name: 'Add to Cart', exact: true }).nth(0);
  for (const options of [{ timeout: 0 }, { timeout: 30_001 }, { timeoutMs: 1_000 }, { timeout: 1_000, force: true }]) {
    assert.throws(() => link.click(options));
  }
  assert.equal(state.clicks, 0); assert.deepEqual(state.navigations, []);
});
