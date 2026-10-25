'use strict';
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let prepareRuleQualifiedListingCart, collectListingReleaseDom, collectCartDom;
before(async () => {
  ({ prepareRuleQualifiedListingCart, collectListingReleaseDom } = await import('../scripts/discogs_rule_cart_gate.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
});
const RELEASE = '111', LISTING_ID = '222';
const LISTING = `https://www.discogs.com/shop/item/${LISTING_ID}`;
const CART = 'https://www.discogs.com/sell/cart/';
const OPTIONS = { expectedReleaseId: RELEASE, expectedListingId: LISTING_ID, expectedRevision: 3, allowAddToCart: true };
const saved = () => ({ revision: 3, targets: [{ release_id: 111, enabled: true, label: 'Same synthetic album',
  min_media: 'VG+', min_sleeve: 'VG', min_seller_percent: 99, min_feedback: 100,
  currency: 'USD', max_total: '150.00' }],
  owned: [], kill_switch: false, control_revision: 2 });
const listing = () => ({ listing: { listing_id: LISTING_ID, release_id: 111, available: true,
  media: 'VG+', sleeve: 'VG+', seller_percent: 99.5, feedback: 200,
  currency: 'USD', item: '100.00', shipping_blocked: false },
  evaluation: { media_completeness: { status: 'reported_complete', release_id: 111, expected_media_count: 2, verified: false },
    media_defects: { status: 'no_disclosed_media_defect', verified: false, reasons: ['No damage disclosed'] },
    rules_revision: 3, eligible_for_review: false, purchase_allowed: false, reasons: ['Final quote unknown'] } });
const identity = () => ({ schema: 'discogs_listing_release_dom_v1', code: 'observed', purchase_allowed: false,
  current_url: LISTING, page_listing_id: LISTING_ID, main_count: 1, heading_count: 1, details_count: 1,
  canonical_count: 1, og_count: 1, heading_release_id: RELEASE, details_release_id: RELEASE,
  canonical_matches_current: true, og_matches_current: true });
function cart() {
  const quote = (label, value) => ({ count: 1, visible: true, label, value });
  return { schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: `/sell/item/${LISTING_ID}`, listing_matches_expected: true, quantity_control_count: 0,
    address_container_count: 1, missing_address: true, edit_shipping_address_present: true, summary_count: 1,
    subtotal: quote('Subtotal', '$100.00 USD'), shipping: quote('Shipping', 'To be determined by seller'),
    tax: { count: 1, visible: false, label: null, value: null }, total: quote('Total', 'To be determined by seller'),
    final_button_count: 1, final_button_visible: true, final_button_enabled: true,
    agreement_count: 1, agreement_visible: true, payment_control_count: 1, payment_control_visible: true };
}
function fixture(options = {}) {
  const state = { url: options.url ?? LISTING, clicks: 0, snapshots: 0, listings: 0, identities: 0, hrefs: 0, calls: [] };
  const port = {
    async readSnapshot() { state.calls.push('snapshot'); state.snapshots++; return options.readSnapshot ? options.readSnapshot(state) : structuredClone(options.snapshot ?? saved()); },
    async readListing(request) {
      assert.deepEqual(request, { release_id: 111, listing: LISTING_ID });
      state.calls.push('listing'); state.listings++;
      return options.readListing ? options.readListing(state) : structuredClone(options.listing ?? listing());
    },
  };
  const only = (object, names) => new Proxy(object, { get(target, key) {
    assert.ok(names.includes(key), `Forbidden page operation: ${String(key)}`); return target[key];
  } });
  const page = only({
    async url() { state.calls.push('url'); return state.url; },
    async evaluate(fn, argument) {
      if (fn === collectListingReleaseDom) {
        assert.deepEqual(argument, { expectedReleaseId: RELEASE, expectedListingId: LISTING_ID });
        state.identities++; state.calls.push('identity');
        return options.readIdentity ? options.readIdentity(state) : structuredClone(options.identity ?? identity());
      }
      assert.equal(fn, collectCartDom); assert.equal(argument, LISTING_ID); state.calls.push('cart'); return cart();
    },
    getByRole(role, query) {
      assert.deepEqual(query, { name: role === 'button' ? 'In Cart' : 'Add to Cart', exact: true });
      return only({
        async count() { return role === 'button' ? 0 : 1; },
        nth(index) {
          assert.equal(index, 0);
          return only({
            async isVisible() { return true; },
            async isDisabled() { return true; },
            async getAttribute(name) {
              assert.equal(name, 'href'); state.hrefs++;
              return options.href ? options.href(state) : `/sell/cart/?add=${LISTING_ID}&ev=`;
            },
            async click() {
              state.clicks++; state.calls.push('click');
              if (options.click) return options.click(state);
              state.url = CART;
            },
          }, ['isVisible', 'isDisabled', 'getAttribute', 'click']);
        },
      }, ['count', 'nth']);
    },
    async goto() { throw new Error('No fixture navigation is required'); },
  }, ['url', 'evaluate', 'getByRole', 'goto']);
  return { page, port, state };
}
function blocked(result) {
  assert.equal(result.purchase_allowed, false); assert.equal(result.stop_required, true);
  assert.equal(result.qualification_scope, 'saved_release_identity_and_media_integrity_check');
  assert.equal(result.purchase_eligibility, 'unsupported');
  assert.ok(!JSON.stringify(result).includes('Same synthetic album'));
  assert.ok(!JSON.stringify(result).includes('SECRET'));
}

test('saved/API/page identities are checked twice and unknown final costs never grant purchase eligibility', async () => {
  const { page, port, state } = fixture();
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'cart_observed_after_add');
  assert.equal(result.identity_preflight_passed, true); assert.equal(result.identity_rechecked_before_click, true);
  assert.equal(state.snapshots, 3); assert.equal(state.listings, 2); assert.equal(state.identities, 2); assert.equal(state.clicks, 1);
  assert.equal(result.cart_inspection.actual_final_verification, 'unsupported');
  assert.equal(result.cart_inspection.observed_quote.shipping, null); assert.equal(result.cart_inspection.observed_quote.tax, null);
  assert.equal(result.cart_inspection.observed_quote.total, null);
  assert.ok(state.calls.lastIndexOf('snapshot') < state.calls.indexOf('click'));
});

test('same album, master ID, missing release, wrong API listing and availability cannot satisfy exact identity', async () => {
  for (const change of [
    response => { response.listing.release_id = 112; response.listing.title = 'Same synthetic album'; },
    response => { delete response.listing.release_id; response.listing.master_id = 111; },
    response => { response.listing.release_id = null; },
    response => { response.listing.release_id = '111'; },
    response => { response.listing.listing_id = '223'; },
    response => { response.listing.available = false; },
  ]) {
    const response = listing(); change(response);
    const { page, port, state } = fixture({ listing: response });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.ok(['api_listing_release_mismatch', 'listing_availability_unverified'].includes(result.code));
    assert.equal(state.clicks, 0); assert.equal(state.identities, 0);
  }
});

test('conditions, seller, currency and item ceiling block before cart preparation', async () => {
  for (const [change, code] of [
    [value => { value.media = 'VG'; }, 'media_condition_below_minimum'],
    [value => { value.sleeve = 'G+'; }, 'sleeve_condition_below_minimum'],
    [value => { value.seller_percent = 98.9; }, 'seller_rating_below_minimum'],
    [value => { value.feedback = 99; }, 'seller_feedback_below_minimum'],
    [value => { value.currency = 'EUR'; }, 'listing_currency_mismatch'],
    [value => { value.item = null; }, 'item_price_unavailable'],
    [value => { value.item = '150.01'; }, 'item_above_total_ceiling'],
    [value => { value.shipping_blocked = true; }, 'shipping_availability_unverified'],
  ]) {
    const response = listing(); change(response.listing);
    const { page, port, state } = fixture({ listing: response });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, code); assert.equal(state.clicks, 0); assert.equal(state.identities, 0);
  }
});

test('disclosed or unverified playable-media damage blocks before cart preparation', async () => {
  for (const [state, code] of [
    ['defective', 'playable_media_damage_disclosed'],
    ['unknown', 'playable_media_damage_check_unverified'],
    ['verified', 'playable_media_damage_check_unverified'],
    ['absent', 'playable_media_damage_check_unverified'],
  ]) {
    const response = listing();
    if (state === 'absent') delete response.evaluation.media_defects;
    else if (state === 'verified') response.evaluation.media_defects.verified = true;
    else response.evaluation.media_defects.status = state;
    const { page, port, state: observed } = fixture({ listing: response });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, code);
    assert.equal(observed.clicks, 0); assert.equal(observed.identities, 0);
  }
});

test('newly disclosed playable-media damage at the final recheck blocks the cart click', async () => {
  const { page, port, state } = fixture({ readListing(state) {
    const response = listing();
    if (state.listings > 1) response.evaluation.media_defects.status = 'defective';
    return response;
  } });
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'playable_media_damage_disclosed');
  assert.equal(state.identities, 1); assert.equal(state.clicks, 0);
});

test('missing saved rules and same-revision rule changes stop before add', async () => {
  for (const change of [
    value => { delete value.targets[0].max_total; },
    value => { value.targets[0].min_media = 'unknown'; },
    value => { value.targets[0].min_seller_percent = 101; },
  ]) {
    const snapshot = saved(); change(snapshot);
    const { page, port, state } = fixture({ snapshot });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, 'saved_target_rules_unavailable'); assert.equal(state.clicks, 0);
  }
  const { page, port, state } = fixture({ readSnapshot: current => {
    const snapshot = saved();
    if (current.snapshots > 1) snapshot.targets[0].max_total = '200.00';
    return snapshot;
  } });
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'saved_target_rules_changed'); assert.equal(state.clicks, 0);
});

test('missing, paused, duplicated, owned, killed, stale or malformed saved release state is blocked', async () => {
  for (const [change, code] of [
    [value => { value.targets = []; }, 'release_not_uniquely_saved'],
    [value => { value.targets[0].enabled = false; }, 'saved_release_paused'],
    [value => { value.targets.push({ ...value.targets[0] }); }, 'release_not_uniquely_saved'],
    [value => { value.owned = [111]; }, 'saved_release_owned'],
    [value => { value.kill_switch = true; }, 'kill_switch_active'],
    [value => { value.revision = 4; }, 'saved_revision_changed'],
    [value => { delete value.control_revision; }, 'saved_state_unavailable'],
  ]) {
    const snapshot = saved(); change(snapshot);
    const { page, port, state } = fixture({ snapshot });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, code); assert.equal(state.clicks, 0); assert.equal(state.listings, 0);
  }
});

test('API/page release disagreement, identity ambiguity or canonical mismatch is blocked', async () => {
  for (const change of [
    value => { value.heading_release_id = '112'; }, value => { value.details_release_id = null; },
    value => { value.page_listing_id = '223'; }, value => { value.main_count = 2; },
    value => { value.heading_count = 2; }, value => { value.details_count = 0; },
    value => { value.canonical_matches_current = false; }, value => { value.og_count = 2; },
  ]) {
    const dom = identity(); change(dom);
    const { page, port, state } = fixture({ identity: dom });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, 'page_release_identity_unverified'); assert.equal(state.clicks, 0);
  }
});

test('revision/control/owned changes at the wrapped click reject before actual mutation', async () => {
  for (const [change, code] of [
    [value => { value.revision = 4; }, 'saved_revision_changed'],
    [value => { value.control_revision = 3; }, 'control_revision_changed'],
    [value => { value.kill_switch = true; }, 'kill_switch_active'],
    [value => { value.owned = [111]; }, 'saved_release_owned'],
  ]) {
    const { page, port, state } = fixture({ readSnapshot(state) { const value = saved(); if (state.snapshots > 1) change(value); return value; } });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, code); assert.equal(result.preparation_state, 'blocked_before_add');
    assert.equal(result.identity_preflight_passed, true); assert.equal(state.clicks, 0);
  }
});

test('fresh API revision mismatch and final saved-state change cannot be hidden by a successful preflight', async () => {
  for (const options of [
    { readListing(state) { const value = listing(); if (state.listings > 1) value.evaluation.rules_revision = 4; return value; } },
    { readSnapshot(state) { const value = saved(); if (state.snapshots > 2) value.kill_switch = true; return value; } },
  ]) {
    const { page, port, state } = fixture(options);
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.ok(['listing_rules_revision_changed', 'kill_switch_active'].includes(result.code)); assert.equal(state.clicks, 0);
  }
});

test('navigation and href races during the final recheck stop before click', async () => {
  for (const options of [
    { readIdentity(state) { const value = identity(); if (state.identities > 1) state.url = 'https://www.discogs.com/shop/item/223'; return value; } },
    { href(state) { return state.hrefs > 2 ? '/sell/cart/?add=223&ev=' : `/sell/cart/?add=${LISTING_ID}&ev=`; } },
  ]) {
    const { page, port, state } = fixture(options);
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.ok(['listing_navigation_changed', 'listing_changed_at_click_gate'].includes(result.code)); assert.equal(state.clicks, 0);
  }
});

test('repeated entry and replacement data ports cannot reset a consumed low-level latch', async () => {
  let changed = true;
  const { page, port, state } = fixture({ readSnapshot(state) { const value = saved(); if (changed && state.snapshots === 2) value.revision = 4; return value; } });
  assert.equal((await prepareRuleQualifiedListingCart(page, port, OPTIONS)).code, 'saved_revision_changed');
  changed = false;
  assert.equal((await prepareRuleQualifiedListingCart(page, port, OPTIONS)).code, 'add_attempt_already_recorded');
  assert.equal((await prepareRuleQualifiedListingCart(page, { ...port }, OPTIONS)).code, 'authenticated_data_port_changed');
  assert.equal(state.clicks, 0);
});

test('repeated invocation after an add never clicks again even if caller returns to the listing page', async () => {
  const { page, port, state } = fixture();
  assert.equal((await prepareRuleQualifiedListingCart(page, port, OPTIONS)).code, 'cart_observed_after_add');
  state.url = LISTING;
  const reads = state.listings;
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'add_attempt_already_recorded'); assert.equal(state.clicks, 1); assert.equal(state.listings, reads);
});

test('concurrent invocations preserve the original context and can only add once', async () => {
  let entered, release;
  const ready = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const { page, port, state } = fixture({ readSnapshot(state) { if (state.snapshots === 1) { entered(); return gate; } return saved(); } });
  const first = prepareRuleQualifiedListingCart(page, port, OPTIONS);
  await ready;
  assert.equal((await prepareRuleQualifiedListingCart(page, port, OPTIONS)).code, 'identity_preparation_in_progress');
  release(saved());
  assert.equal((await first).code, 'cart_observed_after_add'); assert.equal(state.clicks, 1);
});

test('cooldown and authentication read failures fail closed without sleeping or retrying', async () => {
  const { page, port, state } = fixture({ readListing(state) {
    if (state.listings > 1) throw new Error('SECRET_AUTH_OR_COOLDOWN'); return listing();
  } });
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'identity_recheck_read_failed'); assert.equal(state.listings, 2); assert.equal(state.clicks, 0);
});

test('final API or DOM reads resolving after the overall deadline cannot cause delayed clicks', { timeout: 5_000 }, async () => {
  await Promise.all(['api', 'dom'].map(async phase => {
    let late;
    const pending = new Promise(resolve => { late = resolve; });
    const delayed = phase === 'api' ?
      { readListing(state) { return state.listings > 1 ? pending : listing(); } } :
      { readIdentity(state) { return state.identities > 1 ? pending : identity(); } };
    const { page, port, state } = fixture(delayed);
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(state.clicks, 0);
    assert.ok(['identity_recheck_timed_out', 'add_outcome_uncertain'].includes(result.code));
    late(phase === 'api' ? listing() : identity()); await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(state.clicks, 0);
  }));
});

test('uncertain actual click retains the gate and low-level attempt guards', { timeout: 5_000 }, async () => {
  let late;
  const pending = new Promise(resolve => { late = resolve; });
  const { page, port, state } = fixture({ click() { return pending; } });
  const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
  blocked(result); assert.equal(result.code, 'add_outcome_uncertain'); assert.equal(state.clicks, 1);
  assert.equal((await prepareRuleQualifiedListingCart(page, port, OPTIONS)).code, 'add_attempt_already_recorded');
  late(); await new Promise(resolve => setTimeout(resolve, 30)); assert.equal(state.clicks, 1);
});

test('unknown initial page, missing explicit identities/revision and unapproved addition never mutate', async () => {
  for (const options of [{}, { ...OPTIONS, expectedReleaseId: '0' }, { ...OPTIONS, expectedRevision: undefined },
    { ...OPTIONS, expectedRevision: '3' }, { ...OPTIONS, enableBuy: true }]) {
    const { page, port, state } = fixture();
    const result = await prepareRuleQualifiedListingCart(page, port, options);
    blocked(result); assert.equal(result.code, 'invalid_identity_gate_options'); assert.deepEqual(state.calls, []);
  }
  for (const url of [CART, 'https://www.discogs.com/shop/item/223', `${LISTING}?x=1`, 'https://evil.invalid/']) {
    const { page, port, state } = fixture({ url });
    const result = await prepareRuleQualifiedListingCart(page, port, OPTIONS);
    blocked(result); assert.equal(result.code, 'exact_listing_page_required'); assert.equal(state.snapshots, 0); assert.equal(state.clicks, 0);
  }
  const { page, port, state } = fixture();
  const result = await prepareRuleQualifiedListingCart(page, port, { ...OPTIONS, allowAddToCart: false });
  blocked(result); assert.equal(result.code, 'add_to_cart_not_authorized'); assert.equal(result.identity_preflight_passed, true); assert.equal(state.clicks, 0);
});


test('full record set is separate from matching release and VG+ grades', async () => {
  for (const evidence of [undefined, null, {status:'unknown'}, {status:'incomplete'},
    {status:'reported_complete',release_id:112,expected_media_count:2,verified:false},
    {status:'reported_complete',release_id:111,expected_media_count:NaN,verified:false},
    {status:'reported_complete',release_id:111,expected_media_count:2,verified:true}]) {
    const response=listing(); response.evaluation.media_completeness=evidence;
    response.listing.condition='Very Good Plus (VG+)';
    const {page,port,state}=fixture({listing:response});
    const result=await prepareRuleQualifiedListingCart(page,port,OPTIONS);
    assert.equal(result.code,evidence?.status==='incomplete'?'record_set_incomplete':'record_set_completeness_unverified');
    assert.equal(state.clicks,0); assert.equal(state.identities,0); blocked(result);
  }
});
test('seller completeness changing before click stops the add without retry', async () => {
  const {page,port,state}=fixture({readListing(state) {
    const response=listing(); if(state.listings>1) response.evaluation.media_completeness.status='incomplete'; return response;
  }});
  const result=await prepareRuleQualifiedListingCart(page,port,OPTIONS);
  assert.equal(result.code,'record_set_incomplete'); assert.equal(state.clicks,0); blocked(result);
});


test('ordinary fully read seller text need not explicitly claim every disc is included', async () => {
  const response=listing(); response.evaluation.media_completeness.status='no_disclosed_media_omission';
  response.evaluation.media_completeness.expected_media_count=1;
  const {page,port,state}=fixture({listing:response});
  const result=await prepareRuleQualifiedListingCart(page,port,OPTIONS);
  assert.equal(result.code,'cart_observed_after_add');assert.equal(state.clicks,1);blocked(result);
});
