'use strict';
// All observations are synthetic. This suite has no browser, network or order access.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let rehearseCurrentCart;
before(async () => { ({ rehearseCurrentCart } = await import('../scripts/discogs_no_order_rehearsal.mjs')); });

const KEY = '123e4567-e89b-42d3-a456-426614174000';
const OPTIONS = { expectedReleaseId: '111', expectedListingId: '222', expectedRevision: 3, idempotencyKey: KEY };
const CART = 'https://www.discogs.com/sell/cart/';
const saved = () => ({ revision: 3, targets: [{ release_id: 111, enabled: true,
  min_media: 'VG+', min_sleeve: 'VG', min_seller_percent: 99, min_feedback: 100,
  currency: 'USD', max_total: '150.00' }], owned: [], kill_switch: false, control_revision: 2 });
const listing = () => ({ listing: { listing_id: '222', release_id: 111, available: true,
  media: 'VG+', sleeve: 'VG+', seller_percent: 99.5, feedback: 200,
  currency: 'USD', item: '100.00', shipping_blocked: false },
  evaluation: { rules_revision: 3, purchase_allowed: false,
    media_defects: { status: 'no_disclosed_media_defect', verified: false, reasons: ['No damage disclosed'] },
    media_completeness: { status: 'no_disclosed_media_omission', release_id: 111,
      expected_media_count: 1, verified: false } } });
const quote = (label, value) => ({ count: 1, visible: true, label, value });
function cart(total = '$115.00 USD') {
  return { schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    checkout_control_kind: 'legacy_place_order',
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: '/sell/item/222', listing_matches_expected: true, quantity_control_count: 0,
    address_container_count: 1, missing_address: false, edit_shipping_address_present: true,
    summary_count: 1, subtotal: quote('Subtotal', '$100.00 USD'),
    shipping: quote('Shipping', '$10.00 USD'), tax: quote('Tax', '$5.00 USD'),
    total: quote('Total', total), final_button_count: 1, final_button_visible: true,
    final_button_enabled: true, agreement_count: 1, agreement_visible: true,
    payment_control_count: 1, payment_control_visible: true };
}
function fixture(changes = {}) {
  const calls = [];
  const page = new Proxy({
    async url() { calls.push('url'); return changes.url ?? CART; },
    async evaluate(fn, arg) { calls.push('cart'); assert.equal(fn.name, 'collectCartDom');
      assert.equal(arg, '222'); return structuredClone(changes.cart ?? cart()); },
  }, { get(target, name) { assert.ok(['url', 'evaluate'].includes(name), `Forbidden page operation: ${String(name)}`); return target[name]; } });
  const port = {
    async readSnapshot() { calls.push('snapshot'); return structuredClone(changes.snapshot?.(calls) ?? saved()); },
    async readListing(input) { calls.push('listing'); assert.deepEqual(input, { release_id: 111, listing: '222' });
      return structuredClone(changes.listing ?? listing()); },
  };
  if (changes.claim !== undefined) port.readCartClaim = async input => {
    calls.push('claim'); assert.deepEqual(input, { release_id: 111, listing: '222',
      rules_revision: 3, idempotency_key: KEY }); return structuredClone(changes.claim);
  };
  return { page, port, calls };
}
function blocked(result, code) {
  assert.equal(result.code, code); assert.equal(result.status, 'blocked');
  assert.equal(result.source, 'read_only_cart_rehearsal');
  assert.equal(result.purchase_allowed, false); assert.equal(result.stop_required, true);
  assert.equal(result.order_submission, 'disabled');
}

test('normal visible total stays unverified, with no claim and no page mutation', async () => {
  const { page, port, calls } = fixture();
  const result = await rehearseCurrentCart(page, port, OPTIONS);
  blocked(result, 'durable_one_copy_claim_unconfigured');
  assert.deepEqual(result.observed_total, { currency: 'USD', amount: '115.00', verified: false });
  assert.equal(result.cart_inspection.actual_final_verification, 'unsupported');
  assert.deepEqual(calls, ['snapshot', 'listing', 'url', 'cart', 'url', 'snapshot']);
  blocked(await rehearseCurrentCart(page, port, OPTIONS), 'durable_one_copy_claim_unconfigured');
  assert.ok(!calls.includes('claim'));
});

test('observed above-ceiling total stops before reading a claim', async () => {
  const { page, port, calls } = fixture({ cart: cart('$150.01 USD'), claim: {} });
  blocked(await rehearseCurrentCart(page, port, OPTIONS), 'observed_total_above_ceiling');
  assert.ok(!calls.includes('claim'));
});

test('a matching claim still cannot turn a visible amount into a final quote', async () => {
  const claim = { mode: 'durable_cart_rehearsal', purchase_allowed: false,
    release_id: 111, listing_id: '222', rules_revision: 3,
    idempotency_key: KEY, state: 'cart_observed' };
  const { page, port, calls } = fixture({ claim });
  const result = await rehearseCurrentCart(page, port, OPTIONS);
  blocked(result, 'final_delivered_quote_unverified');
  assert.equal(result.claim_state, 'cart_observed'); assert.equal(calls.at(-1), 'claim');
  claim.state = 'uncertain';
  blocked(await rehearseCurrentCart(page, port, OPTIONS), 'cart_add_outcome_requires_reconciliation');
});

test('wrong release, rules, grades, seller and completeness stop before cart access', async () => {
  const cases = [
    [value => { value.listing.release_id = 112; }, 'api_listing_release_mismatch'],
    [value => { value.listing.media = 'VG'; }, 'media_condition_below_minimum'],
    [value => { value.listing.seller_percent = 98; }, 'seller_rating_below_minimum'],
    [value => { value.evaluation.media_completeness.status = 'incomplete'; }, 'record_set_incomplete'],
    [value => { value.evaluation.media_defects.status = 'defective'; }, 'playable_media_damage_disclosed'],
    [value => { value.evaluation.media_defects.status = 'unknown'; }, 'playable_media_damage_check_unverified'],
  ];
  for (const [change, code] of cases) {
    const response = listing(); change(response);
    const { page, port, calls } = fixture({ listing: response });
    blocked(await rehearseCurrentCart(page, port, OPTIONS), code);
    assert.deepEqual(calls, ['snapshot', 'listing']);
  }
  const { page, port, calls } = fixture({ snapshot: () => ({ ...saved(), revision: 4 }) });
  blocked(await rehearseCurrentCart(page, port, OPTIONS), 'saved_revision_changed');
  assert.deepEqual(calls, ['snapshot']);
});

test('changed saved rules, wrong cart row, missing total and malformed claim stop', async () => {
  const changed = fixture({ snapshot: calls => {
    const value = saved(); if (calls.filter(call => call === 'snapshot').length > 1) value.targets[0].max_total = '200.00';
    return value;
  } });
  blocked(await rehearseCurrentCart(changed.page, changed.port, OPTIONS), 'saved_target_rules_changed');
  assert.ok(!changed.calls.includes('claim'));
  const wrong = cart(); wrong.listing_matches_expected = false;
  const missing = cart('To be determined by seller');
  for (const [value, code] of [[wrong, 'single_expected_cart_row_unverified'], [missing, 'delivered_total_unverified']]) {
    const { page, port } = fixture({ cart: value });
    blocked(await rehearseCurrentCart(page, port, OPTIONS), code);
  }
  const { page, port } = fixture({ claim: { state: 'cart_observed' } });
  blocked(await rehearseCurrentCart(page, port, OPTIONS), 'durable_one_copy_claim_unverified');
});

test('invalid IDs, idempotency keys and ports never read or mutate the page', async () => {
  const { page, port, calls } = fixture();
  for (const options of [
    { ...OPTIONS, expectedReleaseId: '0' }, { ...OPTIONS, expectedListingId: '223' + '\n' },
    { ...OPTIONS, idempotencyKey: 'not-a-uuid' }, { ...OPTIONS, expectedRevision: '3' },
    { ...OPTIONS, allowAddToCart: true },
  ]) blocked(await rehearseCurrentCart(page, port, options), 'invalid_rehearsal_options');
  blocked(await rehearseCurrentCart(page, {}, OPTIONS), 'read_only_ports_required');
  assert.deepEqual(calls, []);
});
