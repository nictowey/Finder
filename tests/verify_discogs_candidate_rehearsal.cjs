'use strict';
// Executed only with a disposable Python-generated synthetic candidate bundle.
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const { rehearseCurrentCart } = await import('../scripts/discogs_no_order_rehearsal.mjs');
  const { collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs');
  const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  assert.equal(data.purchase_allowed, false);
  const calls = [];
  const quote = (label, value) => ({ count: 1, visible: true, label, value });
  const listing = data.listing.listing.listing_id;
  const cart = {
    schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: `/sell/item/${listing}`, listing_matches_expected: true,
    quantity_control_count: 0, address_container_count: 1, missing_address: false,
    edit_shipping_address_present: true, summary_count: 1,
    subtotal: quote('Subtotal', '$40.00 USD'), shipping: quote('Shipping', '$5.00 USD'),
    tax: quote('Tax', '$5.00 USD'), total: quote('Total', '$50.00 USD'),
    final_button_count: 1, final_button_visible: true, final_button_enabled: true,
    agreement_count: 1, agreement_visible: true, payment_control_count: 1,
    payment_control_visible: true,
  };
  const page = new Proxy({
    url() { calls.push('url'); return 'https://www.discogs.com/sell/cart/'; },
    evaluate(fn, id) {
      calls.push('cart_dom');
      assert.equal(fn, collectCartDom); assert.equal(id, listing);
      return cart;
    },
  }, { get(target, name) { assert.ok(['url', 'evaluate'].includes(name)); return target[name]; } });
  const port = {
    readSnapshot() { calls.push('snapshot'); return structuredClone(data.snapshot); },
    readListing(input) {
      calls.push('listing');
      assert.deepEqual(input, { release_id: 123, listing });
      return structuredClone(data.listing);
    },
    readCartClaim(input) {
      calls.push('claim');
      assert.deepEqual(input, {
        release_id: 123, listing, rules_revision: data.snapshot.revision,
        idempotency_key: data.claim.idempotency_key,
      });
      return structuredClone(data.claim);
    },
  };
  const result = await rehearseCurrentCart(page, port, {
    expectedReleaseId: '123', expectedListingId: listing,
    expectedRevision: data.snapshot.revision, idempotencyKey: data.claim.idempotency_key,
  });
  assert.equal(result.code, 'final_delivered_quote_unverified');
  assert.equal(result.purchase_allowed, false);
  assert.equal(result.order_submission, 'disabled');
  assert.deepEqual(result.observed_total, { currency: 'USD', amount: '50.00', verified: false });
  assert.deepEqual(calls, ['snapshot', 'listing', 'url', 'cart_dom', 'url', 'snapshot', 'claim']);
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
