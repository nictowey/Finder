'use strict';
// All evidence and controls here are synthetic. No account, browser, or order.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let commitCurrentOrder;
before(async () => ({ commitCurrentOrder } = await import('../scripts/discogs_hosted_order_commit.mjs')));

const NOW = 1_800_000_000_000;
const UUID = '123e4567-e89b-42d3-a456-426614174000';
const scope = Object.freeze({ approval_ref: UUID, currency: 'USD', destination_ref: UUID,
  expires_at: NOW / 1000 + 120, intent_id: UUID, listing_id: '1366950910',
  max_total: '5.05', one_copy_limit: 1, payment_ref: UUID,
  quote_fingerprint: 'a'.repeat(64), release_id: 3422100, rules_revision: 2,
  seller: 'SyntheticSeller', terms_fingerprint: 'b'.repeat(64) });
const cash = amount => ({ amount, currency: 'USD' });
const cartDom = () => ({ schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
  form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
  listing_matches_expected: true, canonical_listing_href: '/sell/item/1366950910',
  quantity_control_count: 0, address_container_count: 1, edit_shipping_address_present: true,
  missing_address: false, summary_count: 1,
  subtotal: { count: 1, visible: true, label: 'Subtotal', value: '0.10 USD' },
  shipping: { count: 1, visible: true, label: 'Shipping', value: '4.95 USD' },
  tax: { count: 1, visible: true, label: 'Tax', value: '0.00 USD' },
  total: { count: 1, visible: true, label: 'Total', value: '5.05 USD' },
  shipping_methods: { selector_count: 1, visible: true, options_over_limit: false,
    option_count: 1, selected_index: 0,
    options: [{ selected: true, disabled: false, label: 'Standard shipping' }] },
  final_button_count: 1, final_button_visible: true, final_button_enabled: true,
  agreement_count: 1, agreement_visible: true,
  payment_control_count: 1, payment_control_visible: true });

function fixture({ quotePatch = {}, cartPatch = {}, destinationPatch = {}, statePatch = {}, termsPatch = {}, authorizationPatch = {},
  agreementChecked = true, clickError = null, onClick = null, onFence = null,
  onQuote = null, onButtonText = null } = {}) {
  const events = [];
  let intent = 'prepared', reads = 0;
  const state = () => ({ source: 'current_guarded_candidate', intent_id: scope.intent_id,
    release_id: scope.release_id,
    listing_id: scope.listing_id, seller: scope.seller, rules_revision: scope.rules_revision,
    claim_state: 'cart_observed', owned: false, active: true, kill_switch: false,
    one_copy_limit: 1, intent_state: intent, ...statePatch });
  const quote = () => ({ source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id,
    final_quote_verified: true, all_payable_charges_included: true, payment_action: 'none',
    shipping_selection_verified: true, selected_shipping_ref: UUID,
    selected_shipping_label: 'Standard shipping',
    quantity: 1, release_id: scope.release_id, listing_id: scope.listing_id,
    seller: scope.seller, rules_revision: scope.rules_revision, currency: scope.currency,
    destination_ref: scope.destination_ref, payment_ref: scope.payment_ref,
    quote_fingerprint: scope.quote_fingerprint, observed_at: NOW / 1000,
    expires_at: NOW / 1000 + 120, item: cash('0.10'), shipping: cash('4.95'),
    tax: cash('0.00'), fees: cash('0.00'), total: cash('5.05'), ...quotePatch });
  const terms = () => ({ source: 'owner_confirmed_current_cart_terms',
    fingerprint: scope.terms_fingerprint, approved_for_intent: scope.intent_id,
    current: true, ...termsPatch });
  const authorization = () => ({ source: 'owner_approved_exact_order', current: true,
    revoked: false, scope: { ...scope }, ...authorizationPatch });
  const destination = () => ({ source: 'verified_current_checkout_destination', current: true,
    selected_for_checkout: true, destination_ref: scope.destination_ref,
    selected_shipping_ref: UUID, approved_for_intent: scope.intent_id,
    ...destinationPatch });
  const agreement = { count: async () => 1, isVisible: async () => true,
    isChecked: async () => agreementChecked };
  const button = { count: async () => 1, isVisible: async () => true,
    isEnabled: async () => true, getAttribute: async () => 'submit',
    innerText: async () => { if (onButtonText) await onButtonText(); return 'Place order and pay now'; },
    click: async () => { events.push('click'); if (onClick) await onClick();
      if (clickError) throw clickError; } };
  const page = { url: async () => 'https://www.discogs.com/sell/cart/',
    title: async () => 'Your Cart - Discogs', evaluate: async () => ({ ...cartDom(), ...cartPatch }),
    locator: selector => {
      assert.ok(['input.agree_terms[name="agree_terms"]', 'button.order_button.checkout_v1'].includes(selector));
      return selector.startsWith('input') ? agreement : button;
    } };
  const ports = { readCurrentState: async () => state(),
    readFinalQuote: async () => { reads++; return onQuote ? onQuote(reads, quote()) : quote(); },
    readCurrentDestination: async () => destination(), readCurrentTerms: async () => terms(),
    readAuthorization: async () => authorization(),
    fencePossibleSubmit: async id => {
      events.push('fence'); assert.equal(id, scope.intent_id);
      intent = 'submission_unknown';
      if (onFence) return onFence();
      return { intent_id: id, state: intent, may_submit_again: false, durable: true };
    } };
  return { page, ports, events, reads: () => reads };
}

const run = (f, options = {}) => commitCurrentOrder(f.page, f.ports, scope,
  { enableCommit: true, clock: () => NOW, ...options });

test('default unavailable makes no reads or clicks', async () => {
  const f = fixture();
  assert.equal((await commitCurrentOrder(f.page, f.ports, scope, { clock: () => NOW })).code,
    'order_commit_disabled');
  assert.deepEqual(f.events, []); assert.equal(f.reads(), 0);
});

test('synthetic explicit ISO cart and trusted matching evidence fence before one intercepted click', async () => {
  const f = fixture(); const result = await run(f);
  assert.equal(result.code, 'submission_unknown');
  assert.deepEqual(f.events, ['fence', 'click']);
  assert.equal(result.may_submit_again, false);
  assert.equal(result.purchase_allowed, false);
});

test('unknown tax, changed quote, revoked authorization, unchecked terms block before fence', async () => {
  for (const options of [
    { quotePatch: { tax: null } },
    { quotePatch: { total: cash('5.06') } },
    { quotePatch: { destination_ref: 'other' } },
    { quotePatch: { intent_id: 'other' } },
    { quotePatch: { shipping_selection_verified: false } },
    { quotePatch: { selected_shipping_label: 'Express shipping' } },
    { destinationPatch: { destination_ref: 'other' } },
    { authorizationPatch: { revoked: true } },
    { termsPatch: { current: false } },
    { agreementChecked: false },
    { onQuote: (read, quote) => read > 1 ? { ...quote, shipping: cash('4.96'), total: cash('5.06') } : quote },
  ]) {
    const f = fixture(options); const result = await run(f);
    assert.notEqual(result.code, 'submission_unknown');
    assert.deepEqual(f.events, []);
  }
});

test('mismatched displayed cart, unknown ISO, deferred tax and unknown method stop before fence', async () => {
  for (const cartPatch of [
    { subtotal: { count: 1, visible: true, label: 'Subtotal', value: '0.11 USD' } },
    { shipping: { count: 1, visible: true, label: 'Shipping', value: '4.96 USD' } },
    { total: { count: 1, visible: true, label: 'Total', value: '5.06 USD' } },
    { subtotal: { count: 1, visible: true, label: 'Subtotal', value: '$0.10' } },
    { tax: { count: 1, visible: false, label: null, value: null } },
    { tax: { count: 0, visible: false, label: null, value: null } },
    { shipping_methods: { selector_count: 0, visible: false, options_over_limit: false,
      option_count: 0, selected_index: null, options: [] } },
  ]) {
    const f = fixture({ cartPatch });
    assert.equal((await run(f)).code, 'order_preflight_unverified');
    assert.deepEqual(f.events, []);
  }
});

test('fence uncertainty and click failure never retry', async () => {
  const fenced = fixture({ onFence: () => { throw new Error('connection lost after ledger write'); } });
  assert.equal((await run(fenced)).code, 'submission_unknown');
  assert.deepEqual(fenced.events, ['fence']);
  const clicked = fixture({ clickError: new Error('unknown browser outcome') });
  assert.equal((await run(clicked)).code, 'submission_unknown');
  assert.deepEqual(clicked.events, ['fence', 'click']);
});

test('revoked rule or quote after durable fence prevents the click and stays unknown', async () => {
  const changed = fixture({ onQuote: (read, quote) => read === 3 ?
    { ...quote, quote_fingerprint: 'c'.repeat(64) } : quote });
  assert.equal((await run(changed)).code, 'submission_unknown');
  assert.deepEqual(changed.events, ['fence']);
  let reads = 0;
  const rule = fixture();
  const original = rule.ports.readCurrentState;
  rule.ports.readCurrentState = async () => {
    reads++;
    const state = await original();
    return reads === 3 ? { ...state, rules_revision: scope.rules_revision + 1 } : state;
  };
  assert.equal((await run(rule)).code, 'submission_unknown');
  assert.deepEqual(rule.events, ['fence']);
});

test('pause and revision changes at the last browser await are caught by the final guard', async () => {
  for (const patch of [{ active: false }, { kill_switch: true },
    { rules_revision: scope.rules_revision + 1 }, { claim_state: 'revoked' }]) {
    let buttonReads = 0, changed = false;
    const f = fixture({ onButtonText: () => { if (++buttonReads === 3) changed = true; } });
    const original = f.ports.readCurrentState;
    f.ports.readCurrentState = async () => ({ ...await original(), ...(changed ? patch : {}) });
    assert.equal((await run(f)).code, 'submission_unknown');
    assert.deepEqual(f.events, ['fence']);
  }
});

test('quote expiry during the final browser await blocks click while owner scope remains valid', async () => {
  let now = NOW, buttonReads = 0;
  const f = fixture({ quotePatch: { expires_at: NOW / 1000 + 1 },
    onButtonText: () => { if (++buttonReads === 3) now = NOW + 1500; } });
  assert.equal((await run(f, { clock: () => now })).code, 'submission_unknown');
  assert.deepEqual(f.events, ['fence']);
});

test('revoked owner authorization after fencing prevents the final click', async () => {
  const f = fixture();
  const original = f.ports.readAuthorization;
  f.ports.readAuthorization = async () => ({ ...await original(),
    revoked: f.events.includes('fence') });
  assert.equal((await run(f)).code, 'submission_unknown');
  assert.deepEqual(f.events, ['fence']);
});

test('expired or altered exact order grant cannot reach the ledger fence', async () => {
  const expired = fixture();
  assert.equal((await run(expired, { clock: () => NOW + 121_000 })).code,
    'order_scope_unverified');
  assert.deepEqual(expired.events, []);
  const different = fixture({ authorizationPatch: { scope: { ...scope, listing_id: '1366950911' } } });
  assert.equal((await run(different)).code, 'order_preflight_unverified');
  assert.deepEqual(different.events, []);
});

test('abort while evidence read is pending stops without ledger fence', async () => {
  const controller = new AbortController();
  const f = fixture({ onQuote: () => { controller.abort(); return new Promise(() => {}); } });
  const result = await run(f, { signal: controller.signal, timeoutMs: 200 });
  assert.equal(result.code, 'order_preflight_unverified');
  assert.deepEqual(f.events, []);
});

test('abort after dispatch leaves the durable fence and does not attempt another click', async () => {
  const controller = new AbortController();
  const f = fixture({ onClick: () => { controller.abort(); return new Promise(() => {}); } });
  const result = await run(f, { signal: controller.signal, timeoutMs: 200 });
  assert.equal(result.code, 'submission_unknown');
  assert.deepEqual(f.events, ['fence', 'click']);
  assert.equal(result.may_submit_again, false);
});
