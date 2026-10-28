'use strict';
// Offline synthetic operator observations and mocked read ports only. The All/All
// zero labels/URL are grounded in recorded UI observations; no receipt markup,
// buyer-order selector or live positive order parsing is claimed by these tests.
const assert = require('node:assert/strict');
const { before, test } = require('node:test');
let api, collectHostedRestrictionDom;
before(async () => {
  api = await import('../scripts/discogs_merchant_order_evidence.mjs');
  ({ collectHostedRestrictionDom } = await import('../scripts/discogs_hosted_readonly.mjs'));
});
const NOW = 1000;
const documentRef = '12345678-1234-4123-8123-123456789abc';
const options = () => ({ now: NOW, afterAttemptAt: NOW - 10 });
const observed = changes => ({ schema: 'discogs_operator_history_observation_v1',
  source: 'operator_current_merchant_ui', scope: { ...api.KC_ORDER_SCOPE },
  page_url: api.MERCHANT_HISTORY_URL, archived_filter: 'All', status_filter: 'All',
  page_size: 50, range_text: '0 - 0 of 0', empty_text: 'No orders found',
  pagination_present: false, restriction_present: false, history_restore: false,
  document_ref: documentRef, observed_at: NOW - 1, ...changes });
function assertNoReceipt(result) {
  assert.equal(result.order_page_evidence, null);
  assert.equal(result.may_submit_again, false);
  assert.equal(result.purchase_allowed, false);
  assert.equal(result.merchant_order_status, 'unverified');
  assert.equal(result.payment_status, 'unverified');
  assert.equal(result.absence_verified, false);
}
function pageFixture({ url, title = 'Purchases', hints = {}, evaluate, onUrl } = {}) {
  const calls = [];
  const page = new Proxy({
    url() { calls.push('url'); return onUrl ? onUrl(calls) : url ?? api.MERCHANT_HISTORY_URL; },
    title() { calls.push('title'); return title; },
    evaluate(fn) { calls.push('evaluate'); assert.equal(fn, collectHostedRestrictionDom);
      return evaluate ? evaluate() : { ...hints }; },
  }, { get(target, key) {
    assert.ok(['url', 'title', 'evaluate'].includes(key), `Forbidden action: ${String(key)}`);
    return target[key];
  } });
  return { page, calls };
}

test('fresh exact All/All zero operator view is reporting only, not an order absence capability', () => {
  for (const range_text of ['0 - 0 of 0', '0–0 of 0']) {
    const result = api.normalizeOperatorMerchantHistory(observed({ range_text }), options());
    assert.equal(result.status, 'observed');
    assert.equal(result.code, 'zero_merchant_orders_as_observed');
    assert.equal(result.observed_order_count, 0);
    assert.equal(result.history_coverage, 'all_archived_and_statuses');
    assert.equal(result.expected_order_found, false);
    assert.equal(result.autonomous_dom_verified, false);
    assert.equal(result.provider_draft_status, 'unverified');
    assert.equal(result.backend_order_timing_verified, false);
    assertNoReceipt(result);
  }
});

test('exact real order lookup does not accept a provider/session identifier', () => {
  const result = api.normalizeOperatorMerchantHistory(observed(), {
    ...options(), expectedOrderId: '123456-789012' });
  assert.equal(result.expected_order_id, '123456-789012');
  assert.equal(result.expected_order_found, false);
  assertNoReceipt(result);
  for (const expectedOrderId of ['5O190127TN364715T', 'SDK_TOKEN', '123456', '', {}, true]) {
    const bad = api.normalizeOperatorMerchantHistory(observed(), { ...options(), expectedOrderId });
    assert.equal(bad.code, 'merchant_lookup_context_unverified');
    assertNoReceipt(bad);
  }
});

test('scope changes, quantity or money ambiguity cannot normalize the exact KC history context', () => {
  for (const patch of [{ release_id: 7609839 }, { listing_id: '3526572746' },
    { seller: 'OtherSeller' }, { quantity: 2 }, { quantity: '1' },
    { currency: 'CAD' }, { max_total: '5.06' }, { total: '5.05' }]) {
    const result = api.normalizeOperatorMerchantHistory(observed({ scope: { ...api.KC_ORDER_SCOPE, ...patch } }), options());
    assert.equal(result.code, 'merchant_history_observation_unverified');
    assertNoReceipt(result);
  }
});

test('history age, future timestamp and observations before the attempted action fail closed', () => {
  for (const observed_at of [NOW + 1, NOW - 11, NaN, '999']) {
    const result = api.normalizeOperatorMerchantHistory(observed({ observed_at }), options());
    assert.equal(result.status, 'unverified'); assertNoReceipt(result);
  }
  const stale = api.normalizeOperatorMerchantHistory(observed({ observed_at: NOW - 300 }), {
    now: NOW, afterAttemptAt: NOW - 400 });
  assert.equal(stale.code, 'merchant_history_stale_or_before_attempt');
  for (const context of [{ now: NaN, afterAttemptAt: 0 }, { now: NOW },
    { now: NOW, afterAttemptAt: NOW + 1 }]) {
    assert.equal(api.normalizeOperatorMerchantHistory(observed(), context).code,
      'merchant_lookup_context_unverified');
  }
});

test('filtered, paginated, restored, challenged or nonzero history never means no order', () => {
  for (const patch of [{ archived_filter: 'Not Archived' }, { status_filter: 'Invoice Sent' },
    { page_size: 25 }, { pagination_present: true }, { history_restore: true },
    { restriction_present: true }, { range_text: '1 - 1 of 1' }, { empty_text: 'No matching orders' },
    { page_url: 'https://www.discogs.com/sell/purchases' }]) {
    const result = api.normalizeOperatorMerchantHistory(observed(patch), options());
    assert.equal(result.status, 'unverified'); assert.equal(result.expected_order_found, null);
    assertNoReceipt(result);
  }
});

test('provider, cart and arbitrary extra payload fields are neither normalized nor echoed', () => {
  for (const patch of [{ provider_order_id: 'SECRET_TOKEN' }, { address: 'SECRET ADDRESS' },
    { source: 'provider_sdk_callback' }, { source: 'visible_cart' }, { order_id: '123456-789012' }]) {
    const result = api.normalizeOperatorMerchantHistory(observed(patch), options());
    assert.equal(result.status, 'unverified'); assertNoReceipt(result);
    assert.equal(JSON.stringify(result).includes('SECRET'), false);
  }
  const hostile = observed(); let getterCalls = 0;
  Object.defineProperty(hostile, 'source', { enumerable: true, get() { getterCalls++; throw Error('SECRET'); } });
  assert.equal(api.normalizeOperatorMerchantHistory(hostile, options()).status, 'unverified');
  assert.equal(getterCalls, 0);
});

test('current history URL is not promoted to verified zero history or a receipt without captured DOM', async () => {
  const f = pageFixture({ hints: { order_id: '123456-789012', payment_status: 'paid',
    range_text: '0 - 0 of 0' } });
  const result = await api.readCurrentMerchantOrderEvidence(f.page, { ...api.KC_ORDER_SCOPE });
  assert.equal(result.code, 'merchant_history_layout_unqualified');
  assert.equal(result.expected_order_found, null); assertNoReceipt(result);
  assert.deepEqual(f.calls, ['url', 'title', 'evaluate', 'url', 'title']);
});

test('cart, provider, enhanced checkout and synthetic receipt URLs are unsupported before DOM access', async () => {
  for (const url of ['https://www.discogs.com/sell/cart/', 'https://www.discogs.com/marketplace/checkout',
    'https://www.paypal.com/smart/buttons?SECRET=TOKEN',
    'https://www.discogs.com/receipt-fixture/123456-789012']) {
    const f = pageFixture({ url });
    const result = await api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE);
    assert.equal(result.code, 'merchant_receipt_layout_unqualified'); assertNoReceipt(result);
    assert.deepEqual(f.calls, ['url']);
    assert.equal(JSON.stringify(result).includes('SECRET'), false);
  }
});

test('same-page challenge, denial, login and changed URL stop without exposing provider text', async () => {
  for (const [config, kind] of [[{ title: 'Just a moment... Cloudflare' }, 'human_verification_required'],
    [{ hints: { password_input_present: true } }, 'login_required'],
    [{ hints: { explicit_denial_present: true } }, 'explicit_access_denied']]) {
    const f = pageFixture(config);
    const result = await api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE);
    assert.equal(result.code, 'merchant_access_restricted');
    assert.equal(result.restriction_kind, kind); assertNoReceipt(result);
    assert.equal(JSON.stringify(result).includes('Cloudflare'), false);
  }
  let reads = 0;
  const f = pageFixture({ onUrl: () => ++reads === 1 ? api.MERCHANT_HISTORY_URL :
    'https://www.discogs.com/login?SECRET=TOKEN' });
  const result = await api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE);
  assert.equal(result.code, 'merchant_page_changed'); assertNoReceipt(result);
  assert.equal(JSON.stringify(result).includes('SECRET'), false);
});

test('cancellation, timeout, concurrency and exceptions cannot produce late receipt evidence', async () => {
  const cancelled = new AbortController(); cancelled.abort();
  const untouched = pageFixture();
  assert.equal((await api.readCurrentMerchantOrderEvidence(untouched.page, api.KC_ORDER_SCOPE,
    { signal: cancelled.signal })).code, 'merchant_read_cancelled');
  assert.deepEqual(untouched.calls, []);
  let release;
  const f = pageFixture({ evaluate: () => new Promise(resolve => { release = resolve; }) });
  const pending = api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE, { timeoutMs: 10 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE)).code,
    'merchant_read_in_progress');
  const timeout = await pending;
  assert.equal(timeout.code, 'merchant_read_timeout'); assertNoReceipt(timeout);
  assert.equal((await api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE)).code,
    'merchant_read_in_progress');
  release({ order_id: '123456-789012' });
  await api.waitForMerchantOrderEvidenceSettlement(f.page);
  assertNoReceipt(timeout);
  const bad = pageFixture({ evaluate: () => { throw Error('SECRET COOKIE'); } });
  const result = await api.readCurrentMerchantOrderEvidence(bad.page, api.KC_ORDER_SCOPE);
  assert.equal(result.code, 'merchant_read_failed'); assertNoReceipt(result);
  assert.equal(JSON.stringify(result).includes('SECRET'), false);
});

test('raw URL, title and DOM reads retain the page lease after cancellation or timeout', async () => {
  for (const mode of ['cancel', 'timeout']) {
    for (const stage of ['url', 'title', 'evaluate']) {
      let resolveRead, blockedOnce = false;
      const calls = [];
      const controller = new AbortController();
      const values = { url: api.MERCHANT_HISTORY_URL, title: 'Purchases', evaluate: {} };
      const read = name => {
        calls.push(name);
        if (stage === name && !blockedOnce) {
          blockedOnce = true;
          return new Promise(resolve => { resolveRead = resolve; });
        }
        return values[name];
      };
      const page = { url: () => read('url'), title: () => read('title'), evaluate: fn => {
        assert.equal(fn, collectHostedRestrictionDom); return read('evaluate'); } };
      const pending = api.readCurrentMerchantOrderEvidence(page, api.KC_ORDER_SCOPE, {
        signal: controller.signal, timeoutMs: mode === 'timeout' ? 10 : 1000 });
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(typeof resolveRead, 'function', `${mode}/${stage} reached intercepted read`);
      if (mode === 'cancel') controller.abort();
      const stopped = await pending;
      assert.equal(stopped.code, mode === 'cancel' ? 'merchant_read_cancelled' : 'merchant_read_timeout');
      assertNoReceipt(stopped);
      const beforeSettlement = [...calls];
      assert.equal((await api.readCurrentMerchantOrderEvidence(page, api.KC_ORDER_SCOPE)).code,
        'merchant_read_in_progress');
      assert.deepEqual(calls, beforeSettlement);
      let settled = false;
      const settlement = api.waitForMerchantOrderEvidenceSettlement(page).then(() => { settled = true; });
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(settled, false, `${mode}/${stage} raw read still owns lease`);
      resolveRead(values[stage]);
      await settlement;
      assert.equal(settled, true);
      assert.deepEqual(calls, beforeSettlement, 'late result must not start the next read');
      const fresh = await api.readCurrentMerchantOrderEvidence(page, api.KC_ORDER_SCOPE);
      assert.equal(fresh.code, 'merchant_history_layout_unqualified');
      await api.waitForMerchantOrderEvidenceSettlement(page);
      assertNoReceipt(stopped);
    }
  }
});

test('rejected late DOM read is drained without an unhandled error or another read', async () => {
  let rejectRead;
  const controller = new AbortController();
  const f = pageFixture({ evaluate: () => new Promise((_, reject) => { rejectRead = reject; }) });
  const pending = api.readCurrentMerchantOrderEvidence(f.page, api.KC_ORDER_SCOPE,
    { signal: controller.signal });
  await new Promise(resolve => setImmediate(resolve));
  controller.abort();
  const cancelled = await pending;
  assert.equal(cancelled.code, 'merchant_read_cancelled');
  const calls = [...f.calls];
  rejectRead(Error('SECRET RAW BROWSER FAILURE'));
  await api.waitForMerchantOrderEvidenceSettlement(f.page);
  assert.deepEqual(f.calls, calls);
  assertNoReceipt(cancelled);
  assert.equal(JSON.stringify(cancelled).includes('SECRET'), false);
});

test('abort during a synchronous read prevents dispatch of the next primitive', async () => {
  const controller = new AbortController();
  const calls = [];
  const page = { url: () => { calls.push('url'); controller.abort(); return api.MERCHANT_HISTORY_URL; },
    title: () => { calls.push('title'); return 'Purchases'; }, evaluate: () => { calls.push('evaluate'); return {}; } };
  const cancelled = await api.readCurrentMerchantOrderEvidence(page, api.KC_ORDER_SCOPE,
    { signal: controller.signal });
  await api.waitForMerchantOrderEvidenceSettlement(page);
  assert.equal(cancelled.code, 'merchant_read_cancelled');
  assert.deepEqual(calls, ['url']);
  assertNoReceipt(cancelled);
});
