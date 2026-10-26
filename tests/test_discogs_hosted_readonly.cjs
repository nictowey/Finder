'use strict';
// Synthetic port tests: no browser profile, Discogs request, cart, or order access.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { stat, mkdtemp, writeFile, readFile, readdir, rm, utimes } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { dirname, join } = require('node:path');
const { randomUUID } = require('node:crypto');
const { EventEmitter } = require('node:events');
let observeCurrentHostedPage, createReleaseNavigator, createListingNavigator,
  listenHostedReadOnly, privateBridgeDirectory, launchHostedReadOnlyBrowser,
  collectHostedRestrictionDom, classifyHostedRestriction,
  collectReleaseMarketplaceDom, collectListingDetailDom, collectListingReadinessDom, collectCartDom;
before(async () => {
  ({ observeCurrentHostedPage, createReleaseNavigator, createListingNavigator,
    listenHostedReadOnly, privateBridgeDirectory, collectHostedRestrictionDom, classifyHostedRestriction } =
    await import('../scripts/discogs_hosted_readonly.mjs'));
  ({ launchHostedReadOnlyBrowser } = await import('../scripts/discogs_hosted_browser_runtime.mjs'));
  ({ collectReleaseMarketplaceDom } = await import('../scripts/discogs_release_snapshot.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
  ({ collectListingDetailDom, collectListingReadinessDom } =
    await import('../scripts/discogs_listing_detail_snapshot.mjs'));
});

const releaseUrl = 'https://www.discogs.com/sell/release/111';
const cartUrl = 'https://www.discogs.com/sell/cart/';
const releaseRequest = { action: 'current_release', release_id: '111' };
const cartRequest = { action: 'current_cart', listing_id: '222' };
const cartGrantId = 'a0c12345-1234-4abc-8def-123456789abc';
const navigateRequest = { action: 'observe_release', release_id: '111' };
const itemUrl = 'https://www.discogs.com/shop/item/222';
const itemRequest = { action: 'observe_listing', release_id: '111', listing_id: '222' };
const observedItem = () => ({ schema: 'discogs_listing_detail_dom_v1', source: 'discogs_rendered_listing',
  status: 'observed', complete: true, release_id: 111, listing_id: '222', url: itemUrl,
  observed_at: new Date().toISOString(), media: 'M', sleeve: 'NM', seller_percent: 100,
  feedback: 10020, item: '199.99', currency: null, reported_shipping_text: '$0.00 shipping',
  seller_comments: 'No skips.', seller_comments_supported: true, available: true,
  cart_state: 'already_in_cart',
  purchase_allowed: false });
const observedRelease = () => ({ source: 'discogs_rendered_release', release_id: 111,
  url: releaseUrl, status: 'observed', complete: true, has_next: false,
  total: 0, rows: [], purchase_allowed: false });
const partialRelease = () => ({ ...observedRelease(), status: 'incomplete', complete: false,
  page: 1, range_start: 1, range_end: 1, total: 2, has_next: true,
  rows: [{ listing_id: '222', url: 'https://www.discogs.com/sell/item/222', fields: {
    price: '$20.00 USD', media: 'Very Good Plus (VG+)', sleeve: 'Very Good Plus (VG+)',
    seller: 'Synthetic seller', seller_rating: '100.0%', seller_feedback: '100 ratings',
    shipping: '+$5.00 shipping', seller_description: null } }],
  assertions: { release_identity: true, listing_container: true, pagination: true, stable: true },
  error_code: 'additional_page_required' });
function fixture(url = releaseUrl, response = observedRelease()) {
  const calls = [];
  const page = new Proxy({
    url() { calls.push('url'); return url; },
    async evaluate(fn, arg) {
      calls.push('evaluate');
      assert.ok([collectReleaseMarketplaceDom, collectCartDom].includes(fn));
      assert.equal(arg, fn === collectReleaseMarketplaceDom ? '111' : '222');
      return structuredClone(response);
    },
  }, { get(target, key) {
    assert.ok(['url', 'evaluate'].includes(key), `Forbidden browser action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({ pages() { calls.push('pages'); return [page]; } }, { get(target, key) {
    assert.equal(key, 'pages', `Forbidden context action: ${String(key)}`); return target[key];
  } });
  return { context, page, calls };
}
function navigationFixture({ status = 200, title = 'Marketplace', landed = releaseUrl,
  result = observedRelease(), retryAfter = null, ready = true, restrictionDom = null } = {}) {
  const calls = [];
  const page = new Proxy({
    goto(url, options) {
      calls.push(['goto', url, options]);
      return { status: () => status, headerValue: () => Promise.resolve(retryAfter) };
    },
    url: () => landed,
    title: async () => typeof title === 'function' ? title() : title,
    waitForSelector(selector) {
      calls.push(['ready', selector]);
      if (!ready) throw new Error('fixture not ready');
    },
    evaluate(fn, id) {
      if (fn === collectHostedRestrictionDom) { calls.push(['restriction']); return structuredClone(restrictionDom); }
      calls.push(['evaluate', id]);
      assert.equal(fn, collectReleaseMarketplaceDom);
      assert.equal(id, '111');
      return structuredClone(result);
    },
    close: async () => calls.push(['close']),
  }, { get(target, key) {
    if (key === 'then') return undefined;
    assert.ok(['goto', 'url', 'title', 'waitForSelector', 'evaluate', 'close'].includes(key),
      `Forbidden navigation action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({ newPage: async () => { calls.push(['newPage']); return page; } },
    { get(target, key) { assert.equal(key, 'newPage'); return target[key]; } });
  return { context, calls };
}
function pagedNavigationFixture({ secondTotal = 2, secondId = '333', secondStatus = 200,
  secondTitle = 'Marketplace', secondLanded = null, onFirstEvaluate = () => {},
  secondRestrictionDom = null, secondReady = true } = {}) {
  const calls = []; let current = releaseUrl, index = 0;
  const first = { ...partialRelease(), observed_at: new Date().toISOString(),
    next_url: releaseUrl + '?page=2' };
  const second = { ...first, url: releaseUrl + '?page=2', page: 2,
    range_start: 2, range_end: 2, total: secondTotal, has_next: false,
    next_url: undefined, error_code: 'page_fragment',
    rows: [{ ...first.rows[0], listing_id: secondId,
      url: `https://www.discogs.com/sell/item/${secondId}` }] };
  const page = new Proxy({
    goto(url, options) { calls.push(['goto', url, options]); index++;
      current = secondLanded && index === 2 ? secondLanded : url;
      return { status: () => index === 2 ? secondStatus : 200 }; },
    url: () => current,
    title: async () => index === 2 ? secondTitle : 'Marketplace',
    waitForSelector(selector) { calls.push(['ready', selector]);
      if (index === 2 && !secondReady) throw Error('unready'); },
    evaluate(fn, arg) {
      if (fn === collectHostedRestrictionDom) { calls.push(['restriction']); return structuredClone(secondRestrictionDom); }
      calls.push(['evaluate', arg]); assert.equal(fn, collectReleaseMarketplaceDom);
      if (index === 1) onFirstEvaluate();
      return structuredClone(index === 1 ? first : second); },
    close: async () => calls.push(['close']),
  }, { get(target, key) {
    if (key === 'then') return undefined;
    assert.ok(['goto', 'url', 'title', 'waitForSelector', 'evaluate', 'close'].includes(key));
    return target[key];
  } });
  return { context: { newPage: async () => page }, calls };
}
function itemFixture({ status = 200, title = 'Listing', landed = itemUrl,
  result = observedItem(), ready = true, diagnostic = null, restrictionDom = null } = {}) {
  const calls = [];
  const page = new Proxy({
    goto(url, options) { calls.push(['goto', url, options]); return { status: () => status }; },
    url: () => landed,
    title: async () => typeof title === 'function' ? title() : title,
    waitForSelector(selector) { calls.push(['ready', selector]); if (!ready) throw Error('unready'); },
    evaluate(fn, expected) {
      if (fn === collectHostedRestrictionDom) { calls.push(['restriction']); return structuredClone(restrictionDom); }
      calls.push(['evaluate', expected]);
      if (fn === collectListingReadinessDom) return structuredClone(diagnostic);
      assert.equal(fn, collectListingDetailDom);
      assert.deepEqual(expected, { release_id: '111', listing_id: '222' });
      return structuredClone(result);
    },
    close: async () => calls.push(['close']),
  }, { get(target, key) {
    if (key === 'then') return undefined;
    assert.ok(['goto', 'url', 'title', 'waitForSelector', 'evaluate', 'close'].includes(key),
      `Forbidden listing action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({
    pages: () => [page],
    newPage: async () => { calls.push(['newPage']); return page; },
  }, { get(target, key) {
    assert.ok(['pages', 'newPage'].includes(key), `Forbidden context action: ${String(key)}`);
    return target[key];
  } });
  return { context, calls };
}
function stopped(result, code) {
  assert.equal(result.status, 'blocked'); assert.equal(result.code, code);
  assert.equal(result.source, 'hosted_browser_read_only');
  assert.equal(result.stop_required, true); assert.equal(result.purchase_allowed, false);
}
test('fixed current release observation uses only exact open page and unchanged collector', async () => {
  const { context, calls } = fixture();
  const result = await observeCurrentHostedPage(context, releaseRequest);
  assert.equal(result.status, 'observed'); assert.equal(result.purchase_allowed, false);
  assert.equal(result.observation.total, 0); assert.equal(result.observation.release_id, 111);
  assert.deepEqual(calls, ['pages', 'url', 'url', 'evaluate', 'url']);
});

test('validated partial current release is report-only, never a complete baseline', async () => {
  const { context, calls } = fixture(releaseUrl, partialRelease());
  const result = await observeCurrentHostedPage(context, releaseRequest);
  assert.equal(result.status, 'incomplete'); assert.equal(result.code, 'additional_page_required');
  assert.equal(result.observation.rows[0].listing_id, '222');
  assert.equal(result.observation.complete, false); assert.equal(result.purchase_allowed, false);
  assert.deepEqual(calls, ['pages', 'url', 'url', 'evaluate', 'url']);
  const unverified = fixture(releaseUrl, { ...partialRelease(), assertions: {
    ...partialRelease().assertions, stable: false } });
  stopped(await observeCurrentHostedPage(unverified.context, releaseRequest),
    'additional_page_required');
});

test('item readiness timeout reports bounded structure and latches actual restriction', async () => {
  const shape = { ready_state: 'complete', main_count: 1, details_count: 0,
    grade_count: 0, release_link_count: 1, password_input_present: false,
    challenge_marker_present: false, sold_control_present: false,
    testids: ['new-item-panel'], hifi_classes: ['hifi-heading-large'] };
  const unready = itemFixture({ ready: false, diagnostic: shape });
  const result = await createListingNavigator(unready.context)(itemRequest);
  stopped(result, 'listing_not_ready');
  assert.deepEqual(result.diagnostic, shape);
  assert.deepEqual(unready.calls.at(-1), ['close']);
  const restricted = itemFixture({ ready: false, diagnostic: {
    ...shape, challenge_marker_present: true } });
  const navigator = createListingNavigator(restricted.context);
  stopped(await navigator(itemRequest), 'access_restricted');
  stopped(await navigator(itemRequest), 'access_restricted');
  assert.equal(restricted.calls.filter(call => call[0] === 'goto').length, 1);
});

test('unsupported actions, extra fields, wrong page, or duplicated pages never evaluate', async () => {
  const { context, page, calls } = fixture();
  for (const request of [{ ...releaseRequest, navigate: true }, { action: 'order', release_id: '111' },
    { action: 'current_release', release_id: '0111' }, { action: 'current_cart', listing_id: '222', click: true }]) {
    stopped(await observeCurrentHostedPage(context, request), 'unsupported_read_request');
  }
  assert.deepEqual(calls, []);
  const wrong = fixture('https://www.discogs.com/sell/release/112');
  stopped(await observeCurrentHostedPage(wrong.context, releaseRequest), 'exact_current_page_not_unique');
  assert.ok(!wrong.calls.includes('evaluate'));
  const duplicates = { pages: () => [page, page] };
  stopped(await observeCurrentHostedPage(duplicates, releaseRequest), 'exact_current_page_not_unique');
});

test('cart read is tied to an exact listing ID and never reports purchase permission', async () => {
  const { context, calls } = fixture(cartUrl, { schema: 'discogs_cart_dom_v1', code: 'unverified', purchase_allowed: false });
  const result = await observeCurrentHostedPage(context, cartRequest);
  stopped(result, 'unsupported_cart_dom');
  assert.ok(calls.includes('evaluate'));
  const wrong = fixture(releaseUrl);
  stopped(await observeCurrentHostedPage(wrong.context, cartRequest), 'exact_current_page_not_unique');
  assert.ok(!wrong.calls.includes('evaluate'));
});

test('browser read exceptions and spoofed collector identity are sanitized', async () => {
  const broken = { pages: () => [{ url: () => releaseUrl, evaluate: () => { throw Error('SECRET_COOKIE'); } }] };
  const result = await observeCurrentHostedPage(broken, releaseRequest);
  stopped(result, 'hosted_read_failed'); assert.ok(!JSON.stringify(result).includes('SECRET_COOKIE'));
  const spoofed = fixture(releaseUrl, { ...observedRelease(), purchase_allowed: true });
  stopped(await observeCurrentHostedPage(spoofed.context, releaseRequest), 'release_observation_unverified');
});

test('already-open exact item page is read without navigation or cart controls', async () => {
  const { context, calls } = itemFixture();
  const result = await observeCurrentHostedPage(context,
    { action: 'current_listing', release_id: '111', listing_id: '222' });
  assert.equal(result.status, 'observed');
  assert.equal(result.observation.item, '199.99');
  assert.equal(result.observation.currency, null);
  assert.equal(result.purchase_allowed, false);
  assert.ok(!calls.some(call => Array.isArray(call) && call[0] === 'goto'));
  stopped(await observeCurrentHostedPage(context,
    { action: 'current_listing', release_id: '111', listing_id: '222', add: true }),
  'unsupported_read_request');
});

test('one fixed item navigation closes its tab and stops on redirect or access restriction', async () => {
  let now = 1_000_000;
  const { context, calls } = itemFixture();
  const navigate = createListingNavigator(context, { now: () => now });
  const result = await navigate(itemRequest);
  assert.equal(result.status, 'observed'); assert.equal(result.purchase_allowed, false);
  assert.deepEqual(calls[1], ['goto', 'https://www.discogs.com/sell/item/222',
    { waitUntil: 'domcontentloaded', timeout: 12000 }]);
  assert.equal(calls.filter(call => call[0] === 'evaluate').length, 1);
  assert.deepEqual(calls.at(-1), ['close']);
  stopped(await navigate(itemRequest), 'listing_cooldown');
  now += 15 * 60_000;
  assert.equal((await navigate(itemRequest)).status, 'observed');
  const redirected = itemFixture({ landed: 'https://www.discogs.com/shop/cart/' });
  stopped(await createListingNavigator(redirected.context)(itemRequest), 'unexpected_redirect');
  assert.ok(!redirected.calls.some(call => call[0] === 'evaluate'));
  const denied = itemFixture({ status: 403 });
  const blocked = createListingNavigator(denied.context);
  stopped(await blocked(itemRequest), 'access_restricted');
  stopped(await blocked(itemRequest), 'access_restricted');
  assert.equal(denied.calls.filter(call => call[0] === 'goto').length, 1);
});

test('wrong item release and incomplete detail never return a qualified observation', async () => {
  const mismatch = itemFixture({ result: { ...observedItem(), release_id: 112 } });
  stopped(await createListingNavigator(mismatch.context)(itemRequest), 'listing_observation_unverified');
  const incomplete = itemFixture({ result: { ...observedItem(), status: 'incomplete',
    complete: false, error_code: 'listing_fields_unavailable' } });
  const result = await createListingNavigator(incomplete.context)(itemRequest);
  assert.equal(result.status, 'incomplete'); assert.equal(result.code, 'listing_fields_unavailable');
  assert.ok(!('observation' in result));
  for (const request of [{ ...itemRequest, url: itemUrl },
    { action: 'observe_listing', release_id: '111', listing_id: '0222' }]) {
    stopped(await createListingNavigator(itemFixture().context)(request), 'unsupported_read_request');
  }
});

async function sendSocket(path, message) {
  return await new Promise((resolve, reject) => {
    const socket = connect(path), chunks = [];
    socket.on('connect', () => socket.write(message));
    socket.on('data', data => chunks.push(data));
    socket.on('end', () => resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))));
    socket.on('error', reject);
  });
}

function operatorFixture({ url = 'https://www.discogs.com/sell/release/4204639',
  title = 'Rocky marketplace', changed = false, complete = true, reportPartial = false,
  hold = null } = {}) {
  const calls = [];
  const page = new Proxy({
    url() { calls.push('url'); return changed && calls.includes('evaluate') ?
      'https://www.discogs.com/sell/release/4204640' : url; },
    title() { calls.push('title'); return title; },
    async evaluate(fn, id) {
      calls.push('evaluate');
      assert.equal(fn, collectReleaseMarketplaceDom); assert.equal(id, '4204639');
      if (hold) await hold;
      return { source: 'discogs_rendered_release', release_id: 4204639, url,
        status: complete ? 'observed' : 'incomplete', complete, page: 1,
        has_next: !complete, range_start: 1, range_end: complete || reportPartial ? 2 : null,
        total: complete ? 2 : reportPartial ? 3 : null,
        rows: complete || reportPartial ? [{ listing_id: '1' }, { listing_id: '2' }] : [],
        assertions: { release_identity: true, listing_container: true, pagination: true, stable: true },
        ...(reportPartial ? { error_code: 'additional_page_required' } : {}),
        purchase_allowed: false };
    },
  }, { get(target, key) {
    assert.ok(['url', 'title', 'evaluate'].includes(key), `Forbidden page action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({ pages() { calls.push('pages'); return [page]; } }, { get(target, key) {
    assert.equal(key, 'pages', `Forbidden context action: ${String(key)}`); return target[key];
  } });
  return { context, calls };
}

async function operatorSocket(context, directory, extras = {}) {
  const path = `/tmp/sniper-test-${randomUUID().replaceAll('-', '')}.sock`;
  const options = { durableStopPath: join(directory, 'access-stopped'),
    operatorBootPath: join(directory, 'operator-boot'),
    operatorAckPath: join(directory, 'operator-rocky-ack'),
    operatorUsedPath: join(directory, 'operator-rocky-used'),
    operatorCartAckPath: join(directory, 'operator-cart-ack'),
    operatorCartUsedPath: join(directory, 'operator-cart-used'), ...extras };
  return { path, options, close: await listenHostedReadOnly(context, path, options) };
}
async function writeOperatorAck(socket) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(socket.options.operatorAckPath,
    `operator_confirmed_current_release:4204639:${bootId}\n`, { mode: 0o600 });
}
async function writeCartAck(socket) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(socket.options.operatorCartAckPath,
    `operator_confirmed_current_cart:3526572746:${bootId}\n`, { mode: 0o600 });
}

function monitorFixture({ currentId = '28720177', currentTitle = 'Discogs marketplace',
  trialStatus = 200, trialRetryAfter = null, hold = null, currentHold = null,
  onCurrentEvaluate = () => {}, onTrialGoto = () => {} } = {}) {
  const calls = [];
  const currentUrl = `https://www.discogs.com/sell/release/${currentId}`;
  const snapshot = (id, url) => ({ source: 'discogs_rendered_release', release_id: Number(id),
    url, observed_at: new Date().toISOString(), status: 'observed', complete: true,
    page: 1, range_start: 0, range_end: 0, total: 0, has_next: false, rows: [],
    assertions: { release_identity: true, listing_container: true, pagination: true, stable: true },
    purchase_allowed: false });
  const current = {
    url: () => { calls.push('current_url'); return currentUrl; },
    title: async () => { calls.push('current_title'); return currentTitle; },
    evaluate: async (fn, id) => { calls.push('current_evaluate'); onCurrentEvaluate();
      assert.equal(fn, collectReleaseMarketplaceDom); assert.equal(id, currentId);
      if (currentHold) await currentHold;
      return snapshot(id, currentUrl); },
  };
  const context = { pages: () => [current], newPage: async () => {
    let url = null;
    return {
      goto: async (next) => { calls.push(['goto', next]); url = next; onTrialGoto();
        if (hold) await hold;
        return { status: () => trialStatus,
          headerValue: () => Promise.resolve(trialRetryAfter) }; },
      url: () => url,
      title: async () => 'Discogs marketplace',
      waitForSelector: async () => {},
      evaluate: async (fn, id) => { calls.push(['trial_evaluate', id]);
        assert.equal(fn, collectReleaseMarketplaceDom);
        return snapshot(id, url); },
      close: async () => { calls.push('trial_close'); },
    };
  } };
  return { context, calls };
}

async function writeMonitorAck(socket, directory, grantId, releaseId) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(join(directory, `operator-monitor-grant-${grantId}-ack`),
    `operator_confirmed_monitor_release:${releaseId}:${grantId}:${bootId}\n`, { mode: 0o600 });
}
async function writeMonitorLeaseAck(socket, directory, grantId, releaseId) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(join(directory, `operator-monitor-lease-grant-${grantId}-ack`),
    `operator_confirmed_monitor_lease:${releaseId}:${grantId}:${bootId}\n`, { mode: 0o600 });
}
async function waitForMonitorEvent(directory, event, grantId) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const names = (await readdir(directory)).filter(name => name.startsWith('operator-monitor-event-'));
    for (const name of names) {
      const record = JSON.parse(await readFile(join(directory, name), 'utf8'));
      if (record.event === event && record.grant_id === grantId) return record;
    }
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(`Missing ${event} audit event`);
}
async function writeCartGrantAck(socket, grantId = cartGrantId) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(join(dirname(socket.options.operatorCartAckPath), `operator-cart-grant-${grantId}-ack`),
    `operator_confirmed_current_cart:3526572746:${grantId}:${bootId}\n`, { mode: 0o600 });
}
function cartSnapshot() {
  const quote = (count, visible, label, value) => ({ count, visible, label, value });
  return { schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
    form_count: 1, order_container_count: 1, item_row_count: 1, listing_link_count: 1,
    canonical_listing_href: '/sell/item/3526572746', listing_matches_expected: true,
    quantity_control_count: 0, address_container_count: 1, edit_shipping_address_present: true,
    missing_address: false, summary_count: 1,
    subtotal: quote(1, true, 'Subtotal', '$199.99 USD'),
    shipping: quote(1, true, 'Shipping', '$5.85 USD'),
    tax: quote(2, false, null, null), total: quote(1, true, 'Total', '$205.84 USD'),
    final_button_count: 1, final_button_visible: true, final_button_enabled: true,
    agreement_count: 1, agreement_visible: true,
    payment_control_count: 1, payment_control_visible: true,
    shipping_methods: { selector_count: 1, visible: true, option_count: 1,
      options_over_limit: false, selected_index: 0,
      options: [{ label: 'Standard - USPS - $5.85', selected: true, disabled: false }] } };
}
function operatorCartFixture({ url = cartUrl, title = 'Shopping Cart', changed = false,
  snapshot = cartSnapshot(), hold = null, duplicate = false } = {}) {
  const calls = [];
  const page = new Proxy({
    url() { calls.push('url'); return changed && calls.includes('evaluate') ?
      'https://www.discogs.com/sell/release/4204639' : url; },
    title() { calls.push('title'); return title; },
    async evaluate(fn, id) {
      calls.push('evaluate'); assert.equal(fn, collectCartDom);
      assert.equal(id, '3526572746'); if (hold) await hold;
      return structuredClone(snapshot);
    },
  }, { get(target, key) {
    assert.ok(['url', 'title', 'evaluate'].includes(key), `Forbidden cart action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({ pages() { calls.push('pages'); return duplicate ? [page, page] : [page]; } },
    { get(target, key) { assert.equal(key, 'pages', `Forbidden context action: ${String(key)}`); return target[key]; } });
  return { context, calls };
}

function operatorListingFixture({ url = 'https://www.discogs.com/sell/item/1366950910',
  title = 'Item for sale', seller = 'Mailman21842', changed = false,
  challenge = false, hold = null, duplicate = false } = {}) {
  const calls = [];
  const page = new Proxy({
    url() { calls.push('url'); return changed && calls.includes('detail') ?
      'https://www.discogs.com/sell/item/1366950911' : url; },
    async title() { calls.push('title'); return title; },
    async evaluate(fn, expected) {
      if (fn === collectListingReadinessDom) {
        calls.push('readiness');
        return { main_count: 1, details_count: 1, grade_count: 1,
          challenge_marker_present: challenge, password_input_present: false };
      }
      assert.equal(fn, collectListingDetailDom);
      assert.deepEqual(expected, { release_id: '3422100', listing_id: '1366950910' });
      calls.push('detail');
      if (hold) await hold;
      return { ...observedItem(), release_id: 3422100, listing_id: '1366950910',
        url, seller_handle: seller };
    },
  }, { get(target, key) {
    assert.ok(['url', 'title', 'evaluate'].includes(key), `Forbidden listing action: ${String(key)}`);
    return target[key];
  } });
  const context = new Proxy({ pages() { calls.push('pages'); return duplicate ? [page, page] : [page]; } },
    { get(target, key) { assert.equal(key, 'pages'); return target[key]; } });
  return { context, calls };
}

async function writeListingGrantAck(socket, directory, grantId) {
  const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
  await writeFile(join(directory, `operator-listing-grant-${grantId}-ack`),
    `operator_confirmed_current_listing:3422100:1366950910:${grantId}:${bootId}\n`,
    { mode: 0o600 });
}

test('monitor recovery needs a fresh acknowledged normal page, then three bounded trials and review', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  const fixture = monitorFixture(); let now = Date.now();
  const grantId = randomUUID();
  const verify = JSON.stringify({ action: 'operator_monitor_verify', release_id: '28720177',
    grant_id: grantId }) + '\n';
  const trial = release_id => JSON.stringify({ action: 'operator_monitor_trial', grant_id: grantId,
    release_id }) + '\n';
  try {
    const socket = await operatorSocket(fixture.context, directory, { clock: () => now });
    try {
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'observe_release',
        release_id: '28720177' }) + '\n'), 'access_restricted');
      stopped(await sendSocket(socket.path, verify), 'operator_acknowledgement_required');
      assert.equal(fixture.calls.length, 0);
      await writeMonitorAck(socket, directory, grantId, '28720177');
      now = Date.now();
      const verified = await sendSocket(socket.path, verify);
      assert.equal(verified.status, 'verified'); assert.equal(verified.purchase_allowed, false);
      assert.equal(fixture.calls.filter(value => value === 'current_evaluate').length, 1);
      assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 0);
      stopped(await sendSocket(socket.path, verify), 'unsupported_monitor_recovery');
      for (const [index, id] of ['28720177', '5520534', '13053315'].entries()) {
        if (index > 0) now += 15 * 60_000 + 1;
        const result = await sendSocket(socket.path, trial(id));
        assert.equal(result.status, 'observed');
        assert.equal(result.recovery_review_required, true);
        assert.equal(result.purchase_allowed, false);
        if (index === 0) stopped(await sendSocket(socket.path, trial('5520534')),
          'release_cooldown');
      }
      stopped(await sendSocket(socket.path, trial('28720177')), 'monitor_trial_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'observe_release',
        release_id: '28720177' }) + '\n'), 'access_restricted');
      assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 3);
      const names = await readdir(directory);
      assert.ok(names.includes('access-stopped'));
      assert.ok(names.includes(`operator-monitor-grant-${grantId}-used`));
      const audit = names.filter(name => name.startsWith('operator-monitor-event-'));
      assert.equal(audit.length, 9);
      assert.ok((await Promise.all(audit.map(name => readFile(join(directory, name), 'utf8'))))
        .some(value => JSON.parse(value).event === 'trials_complete_review_required'));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('monitor recovery consumes failed verification and preserves the durable stop', async () => {
  for (const [fixtureOptions, expected] of [
    [{ currentId: '5520534' }, 'exact_current_page_not_unique'],
    [{ currentTitle: 'Just a moment... Cloudflare' }, 'access_restricted'],
  ]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
    const fixture = monitorFixture(fixtureOptions), grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory);
      try {
        await writeMonitorAck(socket, directory, grantId, '28720177');
        const verify = JSON.stringify({ action: 'operator_monitor_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n';
        const result = await sendSocket(socket.path, verify);
        stopped(result, expected);
        if (expected === 'access_restricted') {
          assert.equal(result.restriction_cause, 'user_opened_challenge_or_login');
        }
        stopped(await sendSocket(socket.path, verify), 'monitor_recovery_unavailable');
        stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
          grant_id: grantId, release_id: '28720177' }) + '\n'), 'monitor_trial_unavailable');
        assert.ok((await readdir(directory)).includes('access-stopped'));
        assert.ok(!fixture.calls.some(value => Array.isArray(value) && value[0] === 'goto'));
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('monitor trial stops and records exact future HTTP denial without clearing history', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  const fixture = monitorFixture({ trialStatus: 403 }), grantId = randomUUID();
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeMonitorAck(socket, directory, grantId, '28720177');
      assert.equal((await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_verify',
        grant_id: grantId, release_id: '28720177' }) + '\n')).status, 'verified');
      const result = await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
        grant_id: grantId, release_id: '28720177' }) + '\n');
      stopped(result, 'access_restricted'); assert.equal(result.restriction_cause, 'http_403');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
        grant_id: grantId, release_id: '5520534' }) + '\n'), 'monitor_trial_unavailable');
      const audit = (await readdir(directory)).filter(name => name.startsWith('operator-monitor-event-'));
      assert.ok((await Promise.all(audit.map(name => readFile(join(directory, name), 'utf8'))))
        .some(value => JSON.parse(value).cause === 'http_403'));
      assert.ok((await readdir(directory)).includes('access-stopped'));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('monitor acknowledgement expires, rejects stale boots and never authorizes other IDs', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  const fixture = monitorFixture(), grantId = randomUUID();
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      const ackPath = join(directory, `operator-monitor-grant-${grantId}-ack`);
      const request = JSON.stringify({ action: 'operator_monitor_verify', grant_id: grantId,
        release_id: '28720177' }) + '\n';
      await writeFile(ackPath,
        `operator_confirmed_monitor_release:28720177:${grantId}:${randomUUID()}\n`,
        { mode: 0o600 });
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      assert.equal(fixture.calls.length, 0);
      const bootId = (await readFile(socket.options.operatorBootPath, 'utf8')).trim();
      await writeFile(ackPath,
        `operator_confirmed_monitor_release:28720177:${grantId}:${bootId}\n`);
      const stale = new Date(Date.now() - 6 * 60_000);
      await utimes(ackPath, stale, stale);
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_verify',
        grant_id: grantId, release_id: '4204639' }) + '\n'), 'unsupported_monitor_recovery');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_verify',
        grant_id: grantId, release_id: '28720177', extra: true }) + '\n'),
      'unsupported_monitor_recovery');
      assert.equal(fixture.calls.length, 0);
      assert.ok(!(await readdir(directory)).some(name => name.endsWith('-used')));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('expired monitor trial is audited and cannot be reused', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  const fixture = monitorFixture(), grantId = randomUUID(); let now = Date.now();
  try {
    const socket = await operatorSocket(fixture.context, directory, { clock: () => now });
    try {
      await writeMonitorAck(socket, directory, grantId, '28720177');
      now = Date.now();
      assert.equal((await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_verify',
        grant_id: grantId, release_id: '28720177' }) + '\n')).status, 'verified');
      now += 60 * 60_000 + 1;
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
        grant_id: grantId, release_id: '28720177' }) + '\n'), 'monitor_trial_unavailable');
      assert.ok(!fixture.calls.some(value => Array.isArray(value) && value[0] === 'goto'));
      const audit = (await readdir(directory)).filter(name => name.startsWith('operator-monitor-event-'));
      assert.ok((await Promise.all(audit.map(name => readFile(join(directory, name), 'utf8'))))
        .some(value => JSON.parse(value).cause === 'expired'));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('browser restart retains the consumed monitor grant and durable stop', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  const fixture = monitorFixture(), grantId = randomUUID();
  try {
    const first = await operatorSocket(fixture.context, directory);
    await writeMonitorAck(first, directory, grantId, '28720177');
    const verify = JSON.stringify({ action: 'operator_monitor_verify', grant_id: grantId,
      release_id: '28720177' }) + '\n';
    assert.equal((await sendSocket(first.path, verify)).status, 'verified');
    await first.close();
    const restarted = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(restarted.path, verify), 'operator_acknowledgement_required');
      await writeMonitorAck(restarted, directory, grantId, '28720177');
      stopped(await sendSocket(restarted.path, verify), 'monitor_recovery_unavailable');
      stopped(await sendSocket(restarted.path, JSON.stringify({ action: 'operator_monitor_trial',
        grant_id: grantId, release_id: '28720177' }) + '\n'), 'monitor_trial_unavailable');
      stopped(await sendSocket(restarted.path, JSON.stringify({ action: 'observe_release',
        release_id: '28720177' }) + '\n'), 'access_restricted');
      assert.ok((await readdir(directory)).includes(`operator-monitor-grant-${grantId}-used`));
    } finally { await restarted.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('concurrent monitor trials cannot spend the same scoped release twice', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
  let release;
  const fixture = monitorFixture({ hold: new Promise(resolve => { release = resolve; }) });
  const grantId = randomUUID();
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeMonitorAck(socket, directory, grantId, '28720177');
      assert.equal((await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_verify',
        grant_id: grantId, release_id: '28720177' }) + '\n')).status, 'verified');
      const request = JSON.stringify({ action: 'operator_monitor_trial', grant_id: grantId,
        release_id: '28720177' }) + '\n';
      const first = sendSocket(socket.path, request);
      await new Promise(resolve => setImmediate(resolve));
      stopped(await sendSocket(socket.path, request), 'read_in_progress');
      release();
      assert.equal((await first).status, 'observed');
      stopped(await sendSocket(socket.path, request), 'monitor_trial_unavailable');
      assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 1);
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('timed-out or disconnected verification cannot activate a late monitor trial', async () => {
  for (const mode of ['timeout', 'disconnect']) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
    let release, entered;
    const currentHold = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    const fixture = monitorFixture({ currentHold, onCurrentEvaluate: () => entered() });
    const grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory,
        { monitorVerifyTimeoutMs: 30 });
      try {
        await writeMonitorAck(socket, directory, grantId, '28720177');
        const verify = JSON.stringify({ action: 'operator_monitor_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n';
        if (mode === 'timeout') {
          const pending = sendSocket(socket.path, verify);
          await started;
          stopped(await pending, 'request_timed_out');
        } else {
          const client = connect(socket.path);
          await new Promise(resolve => client.once('connect', resolve));
          client.write(verify);
          await started;
          const closed = new Promise(resolve => client.once('close', resolve));
          client.destroy();
          await closed;
        }
        release();
        const audit = await waitForMonitorEvent(directory, 'verification_stopped', grantId);
        assert.equal(audit.cause, mode === 'timeout' ? 'request_timed_out' : 'request_disconnected');
        stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
          grant_id: grantId, release_id: '28720177' }) + '\n'), 'monitor_trial_unavailable');
        assert.ok(!fixture.calls.some(value => Array.isArray(value) && value[0] === 'goto'));
        assert.ok((await readdir(directory)).includes('access-stopped'));
      } finally { release(); await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('timed-out or disconnected trial loses all remaining scoped reads', async () => {
  for (const mode of ['timeout', 'disconnect']) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-monitor-'));
    let release, entered;
    const hold = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    const fixture = monitorFixture({ hold, onTrialGoto: () => entered() });
    const grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory,
        { monitorTrialTimeoutMs: 30 });
      try {
        await writeMonitorAck(socket, directory, grantId, '28720177');
        assert.equal((await sendSocket(socket.path, JSON.stringify({
          action: 'operator_monitor_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n')).status, 'verified');
        const trial = JSON.stringify({ action: 'operator_monitor_trial', grant_id: grantId,
          release_id: '28720177' }) + '\n';
        if (mode === 'timeout') {
          const pending = sendSocket(socket.path, trial);
          await started;
          stopped(await pending, 'request_timed_out');
        } else {
          const client = connect(socket.path);
          await new Promise(resolve => client.once('connect', resolve));
          client.write(trial);
          await started;
          const closed = new Promise(resolve => client.once('close', resolve));
          client.destroy();
          await closed;
        }
        release();
        const audit = await waitForMonitorEvent(directory, 'trial_stopped', grantId);
        assert.equal(audit.cause, mode === 'timeout' ? 'request_timed_out' : 'request_disconnected');
        stopped(await sendSocket(socket.path, JSON.stringify({ action: 'operator_monitor_trial',
          grant_id: grantId, release_id: '5520534' }) + '\n'), 'monitor_trial_unavailable');
        assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 1);
        assert.ok((await readdir(directory)).includes('access-stopped'));
      } finally { release(); await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('read-only monitor lease needs a fresh current-page ack, is capped, and leaves old stop', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
  const fixture = monitorFixture(); let now = Date.now(); const grantId = randomUUID();
  const verify = JSON.stringify({ action: 'operator_monitor_lease_verify', grant_id: grantId,
    release_id: '28720177' }) + '\n';
  const read = id => JSON.stringify({ action: 'operator_monitor_lease_read', release_id: id }) + '\n';
  try {
    const socket = await operatorSocket(fixture.context, directory, { clock: () => now });
    try {
      stopped(await sendSocket(socket.path, verify), 'operator_acknowledgement_required');
      await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
      assert.equal((await sendSocket(socket.path, verify)).status, 'verified');
      stopped(await sendSocket(socket.path, verify), 'monitor_lease_already_active');
      stopped(await sendSocket(socket.path, read('4204639')), 'unsupported_monitor_lease');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'observe_release',
        release_id: '28720177' }) + '\n'), 'access_restricted');
      for (let index = 0; index < 6; index++) {
        if (index) now += 15 * 60_000 + 1;
        const result = await sendSocket(socket.path, read(['28720177', '5520534',
          '13053315'][index % 3]));
        assert.equal(result.status, 'observed');
        assert.equal(result.monitor_lease_remaining, 5 - index);
        if (index === 0) stopped(await sendSocket(socket.path, read('5520534')),
          'monitor_lease_cooldown');
      }
      stopped(await sendSocket(socket.path, read('28720177')), 'monitor_lease_unavailable');
      stopped(await sendSocket(socket.path, verify), 'operator_acknowledgement_required');
      const state = JSON.parse(await readFile(join(directory, 'operator-monitor-lease.json'), 'utf8'));
      assert.equal(state.attempts, 6); assert.equal(state.revoked, true);
      assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 6);
      assert.ok((await readdir(directory)).includes('access-stopped'));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('monitor lease retains its slot across browser restart and requires fresh verification', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
  const fixture = monitorFixture(); let now = Date.now(); const firstGrant = randomUUID();
  const read = JSON.stringify({ action: 'operator_monitor_lease_read',
    release_id: '28720177' }) + '\n';
  try {
    const first = await operatorSocket(fixture.context, directory, { clock: () => now });
    await writeMonitorLeaseAck(first, directory, firstGrant, '28720177');
    assert.equal((await sendSocket(first.path, JSON.stringify({
      action: 'operator_monitor_lease_verify', grant_id: firstGrant,
      release_id: '28720177' }) + '\n')).status, 'verified');
    assert.equal((await sendSocket(first.path, read)).status, 'observed');
    await first.close();
    const restarted = await operatorSocket(fixture.context, directory, { clock: () => now });
    try {
      stopped(await sendSocket(restarted.path, read), 'monitor_lease_unavailable');
      stopped(await sendSocket(restarted.path, JSON.stringify({
        action: 'operator_monitor_lease_revoke' }) + '\n'), 'monitor_lease_stopped');
      const nextGrant = randomUUID();
      await writeMonitorLeaseAck(restarted, directory, nextGrant, '28720177');
      assert.equal((await sendSocket(restarted.path, JSON.stringify({
        action: 'operator_monitor_lease_verify', grant_id: nextGrant,
        release_id: '28720177' }) + '\n')).status, 'verified');
      stopped(await sendSocket(restarted.path, read), 'monitor_lease_cooldown');
      now += 15 * 60_000 + 1;
      assert.equal((await sendSocket(restarted.path, read)).status, 'observed');
      assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 2);
    } finally { await restarted.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('wrong or challenged current page consumes lease grant without enabling reads', async () => {
  for (const options of [{ currentId: '5520534' },
    { currentTitle: 'Just a moment... Cloudflare' }]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
    const fixture = monitorFixture(options), grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory);
      try {
        await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
        const verify = JSON.stringify({ action: 'operator_monitor_lease_verify',
          grant_id: grantId, release_id: '28720177' }) + '\n';
        stopped(await sendSocket(socket.path, verify), options.currentId ?
          'exact_current_page_not_unique' : 'access_restricted');
        stopped(await sendSocket(socket.path, verify), 'monitor_lease_unavailable');
        stopped(await sendSocket(socket.path, JSON.stringify({
          action: 'operator_monitor_lease_read', release_id: '28720177' }) + '\n'),
        'monitor_lease_unavailable');
        assert.ok(!fixture.calls.some(value => Array.isArray(value) && value[0] === 'goto'));
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('monitor lease stops on denial, uncertainty, and concurrent attempts', async () => {
  for (const mode of ['denial', 'timeout', 'disconnect']) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
    let release, entered;
    const hold = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    const fixture = monitorFixture({ trialStatus: mode === 'denial' ? 403 : 200,
      hold: mode === 'denial' ? null : hold, onTrialGoto: () => entered() });
    const grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory,
        { monitorTrialTimeoutMs: 30 });
      try {
        await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
        assert.equal((await sendSocket(socket.path, JSON.stringify({
          action: 'operator_monitor_lease_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n')).status, 'verified');
        const read = JSON.stringify({ action: 'operator_monitor_lease_read',
          release_id: '28720177' }) + '\n';
        if (mode === 'denial') {
          stopped(await sendSocket(socket.path, read), 'access_restricted');
        } else if (mode === 'timeout') {
          const pending = sendSocket(socket.path, read);
          await started;
          stopped(await sendSocket(socket.path, read), 'read_in_progress');
          stopped(await pending, 'request_timed_out');
        } else {
          const client = connect(socket.path);
          await new Promise(resolve => client.once('connect', resolve));
          client.write(read);
          await started;
          const closed = new Promise(resolve => client.once('close', resolve));
          client.destroy(); await closed;
        }
        release();
        const audit = await waitForMonitorEvent(directory, 'lease_stopped', grantId);
        assert.equal(audit.cause, mode === 'denial' ? 'http_403' :
          mode === 'timeout' ? 'request_timed_out' : 'request_disconnected');
        stopped(await sendSocket(socket.path, read), 'monitor_lease_unavailable');
        assert.ok((await readdir(directory)).includes('access-stopped'));
      } finally { release(); await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('disconnect or timeout while saving a rate-limit slot revokes the lease', async () => {
  for (const mode of ['timeout', 'disconnect']) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
    let release, entered, writes = 0;
    const hold = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    const fixture = monitorFixture({ trialStatus: 429, trialRetryAfter: '3600' });
    const grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory, {
        monitorTrialTimeoutMs: 30,
        leaseWriteHook: async () => { if (++writes === 3) { entered(); await hold; } },
      });
      try {
        await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
        assert.equal((await sendSocket(socket.path, JSON.stringify({
          action: 'operator_monitor_lease_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n')).status, 'verified');
        const read = JSON.stringify({ action: 'operator_monitor_lease_read',
          release_id: '28720177' }) + '\n';
        if (mode === 'timeout') {
          const pending = sendSocket(socket.path, read);
          await started;
          stopped(await pending, 'request_timed_out');
        } else {
          const client = connect(socket.path);
          await new Promise(resolve => client.once('connect', resolve));
          client.write(read); await started;
          const closed = new Promise(resolve => client.once('close', resolve));
          client.destroy(); await closed;
        }
        release();
        const audit = await waitForMonitorEvent(directory, 'lease_stopped', grantId);
        assert.equal(audit.cause, mode === 'timeout' ?
          'request_timed_out' : 'request_disconnected');
        const state = JSON.parse(await readFile(join(directory, 'operator-monitor-lease.json'), 'utf8'));
        assert.equal(state.revoked, true); assert.equal(state.attempts, 1);
        assert.ok(state.next_allowed_at > Date.now() + 30 * 60_000);
        stopped(await sendSocket(socket.path, read), 'monitor_lease_unavailable');
        assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 1);
      } finally { release(); await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('monitor lease honors Retry-After and expires without another read', async () => {
  for (const trialStatus of [429, 503]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
    const fixture = monitorFixture({ trialStatus, trialRetryAfter: '3600' });
    let now = Date.now(); const grantId = randomUUID();
    try {
      const socket = await operatorSocket(fixture.context, directory, { clock: () => now });
      try {
        await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
        assert.equal((await sendSocket(socket.path, JSON.stringify({
          action: 'operator_monitor_lease_verify', grant_id: grantId,
          release_id: '28720177' }) + '\n')).status, 'verified');
        const read = JSON.stringify({ action: 'operator_monitor_lease_read',
          release_id: '28720177' }) + '\n';
        stopped(await sendSocket(socket.path, read), trialStatus === 429 ?
          'rate_limited' : 'service_unavailable');
        now += 15 * 60_000 + 1;
        stopped(await sendSocket(socket.path, read), 'monitor_lease_cooldown');
        now += 2 * 60 * 60_000;
        stopped(await sendSocket(socket.path, read), 'monitor_lease_unavailable');
        assert.equal(fixture.calls.filter(value => Array.isArray(value) && value[0] === 'goto').length, 1);
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('an in-flight read cannot complete after the bounded lease expires', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-lease-'));
  let now = Date.now();
  const fixture = monitorFixture({ onTrialGoto: () => { now += 2 * 60 * 60_000 + 1; } });
  const grantId = randomUUID();
  try {
    const socket = await operatorSocket(fixture.context, directory, { clock: () => now });
    try {
      await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
      assert.equal((await sendSocket(socket.path, JSON.stringify({
        action: 'operator_monitor_lease_verify', grant_id: grantId,
        release_id: '28720177' }) + '\n')).status, 'verified');
      const read = JSON.stringify({ action: 'operator_monitor_lease_read',
        release_id: '28720177' }) + '\n';
      stopped(await sendSocket(socket.path, read), 'monitor_lease_unavailable');
      const state = JSON.parse(await readFile(join(directory, 'operator-monitor-lease.json'), 'utf8'));
      assert.equal(state.attempts, 1); assert.equal(state.revoked, true);
      stopped(await sendSocket(socket.path, read), 'monitor_lease_unavailable');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('one-use current cart recovery reads exact visible row and preserves global stop', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  const fixture = operatorCartFixture();
  const request = JSON.stringify({ action: 'operator_current_cart', listing_id: '3526572746' }) + '\n';
  try {
    let socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      assert.deepEqual(fixture.calls, []);
      await writeCartAck(socket);
      const result = await sendSocket(socket.path, request);
      stopped(result, 'final_quote_unverified');
      assert.equal(result.observed_quote.total.amount, '205.84');
      assert.equal(result.observed_quote.tax, null);
      assert.ok(result.observations.includes('tax_hidden_unknown'));
      stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify(cartRequest) + '\n'), 'access_restricted');
      stopped(await sendSocket(socket.path,
        JSON.stringify({ action: 'operator_current_release', release_id: '4204639' }) + '\n'),
      'operator_acknowledgement_required');
      assert.ok(!fixture.calls.some(call => ['goto', 'click', 'newPage'].includes(call)));
    } finally { await socket.close(); }
    socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      stopped(await sendSocket(socket.path, JSON.stringify(cartRequest) + '\n'), 'access_restricted');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a scoped cart grant is single-use and never clears the consumed legacy grant', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  const fixture = operatorCartFixture();
  const legacy = JSON.stringify({ action: 'operator_current_cart', listing_id: '3526572746' }) + '\n';
  const scoped = JSON.stringify({ action: 'operator_current_cart', listing_id: '3526572746',
    grant_id: cartGrantId }) + '\n';
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeCartAck(socket);
      stopped(await sendSocket(socket.path, legacy), 'final_quote_unverified');
      stopped(await sendSocket(socket.path, scoped), 'operator_acknowledgement_required');
      await writeCartGrantAck(socket);
      stopped(await sendSocket(socket.path, scoped), 'final_quote_unverified');
      assert.equal((await readFile(join(directory, 'operator-cart-used'), 'utf8')), 'consumed\n');
      assert.equal((await readFile(join(directory, `operator-cart-grant-${cartGrantId}-used`), 'utf8')),
        'consumed\n');
      stopped(await sendSocket(socket.path, scoped), 'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, legacy), 'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify(cartRequest) + '\n'), 'access_restricted');
      assert.equal(fixture.calls.filter(call => call === 'evaluate').length, 2);
      assert.ok(!fixture.calls.some(call => ['goto', 'click', 'newPage'].includes(call)));
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('one-use exact item grant reads the existing page without navigation or cart writes', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-item-grant-'));
  const fixture = operatorListingFixture();
  const grantId = randomUUID();
  const request = { action: 'operator_current_listing', release_id: '3422100',
    listing_id: '1366950910', grant_id: grantId };
  try {
    let socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_acknowledgement_required');
      assert.deepEqual(fixture.calls, []);
      await writeListingGrantAck(socket, directory, grantId);
      const result = await sendSocket(socket.path, JSON.stringify(request) + '\n');
      assert.equal(result.status, 'observed');
      assert.equal(result.observation.release_id, 3422100);
      assert.equal(result.observation.listing_id, '1366950910');
      assert.equal(result.observation.seller_handle, 'Mailman21842');
      assert.equal(result.purchase_allowed, false);
      assert.equal((await readFile(join(directory, `operator-listing-grant-${grantId}-used`),
        'utf8')), 'consumed\n');
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify({ action: 'current_listing',
        release_id: '3422100', listing_id: '1366950910' }) + '\n'), 'access_restricted');
      assert.ok(!fixture.calls.some(call => ['goto', 'click', 'newPage'].includes(call)));
    } finally { await socket.close(); }
    socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_acknowledgement_required');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('item grant rejects wrong scope, seller, page and challenge without retry', async () => {
  for (const [config, code] of [
    [{}, 'observed'],
    [{ seller: 'DifferentSeller' }, 'listing_seller_identity_unverified'],
    [{ url: 'https://www.discogs.com/sell/item/1366950911' }, 'exact_current_page_not_unique'],
    [{ changed: true }, 'page_changed_during_read'],
    [{ challenge: true }, 'access_restricted'],
    [{ title: 'Just a moment... Cloudflare' }, 'access_restricted'],
    [{ duplicate: true }, 'exact_current_page_not_unique'],
  ]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-item-grant-'));
    const fixture = operatorListingFixture(config);
    const grantId = randomUUID();
    const request = { action: 'operator_current_listing', release_id: '3422100',
      listing_id: '1366950910', grant_id: grantId };
    try {
      const socket = await operatorSocket(fixture.context, directory);
      try {
        await writeListingGrantAck(socket, directory, grantId);
        for (const wrong of [{ ...request, listing_id: '1366950911' },
          { ...request, release_id: '3422101' }, { ...request, grant_id: '../x' },
          { ...request, click: true }]) {
          stopped(await sendSocket(socket.path, JSON.stringify(wrong) + '\n'),
            'unsupported_operator_recovery');
        }
        const result = await sendSocket(socket.path, JSON.stringify(request) + '\n');
        if (code === 'observed') assert.equal(result.status, 'observed');
        else stopped(result, code);
        stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
          'operator_recovery_unavailable');
        assert.ok(!fixture.calls.some(call => ['goto', 'click', 'newPage'].includes(call)));
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('item timeout consumes grant and cannot yield late success', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-item-grant-'));
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  const fixture = operatorListingFixture({ hold });
  const grantId = randomUUID();
  const request = { action: 'operator_current_listing', release_id: '3422100',
    listing_id: '1366950910', grant_id: grantId };
  try {
    const socket = await operatorSocket(fixture.context, directory,
      { operatorListingTimeoutMs: 20 });
    try {
      await writeListingGrantAck(socket, directory, grantId);
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'request_timed_out');
      release();
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_recovery_unavailable');
      assert.equal((await readFile(join(directory, `operator-listing-grant-${grantId}-used`),
        'utf8')), 'consumed\n');
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('item disconnect consumes grant and stale acknowledgement never starts a read', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-item-grant-'));
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  const fixture = operatorListingFixture({ hold });
  const grantId = randomUUID();
  const request = { action: 'operator_current_listing', release_id: '3422100',
    listing_id: '1366950910', grant_id: grantId };
  const usedPath = join(directory, `operator-listing-grant-${grantId}-used`);
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeListingGrantAck(socket, directory, grantId);
      const ackPath = join(directory, `operator-listing-grant-${grantId}-ack`);
      const stale = new Date(Date.now() - 6 * 60_000);
      await utimes(ackPath, stale, stale);
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_acknowledgement_required');
      assert.deepEqual(fixture.calls, []);
      await writeListingGrantAck(socket, directory, grantId);
      const client = connect(socket.path);
      await new Promise(resolve => client.once('connect', resolve));
      client.write(JSON.stringify(request) + '\n');
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await stat(usedPath).catch(() => null)) break;
        await new Promise(resolve => setTimeout(resolve, 5));
      }
      assert.ok(await stat(usedPath));
      client.destroy();
      release();
      await new Promise(resolve => setTimeout(resolve, 10));
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_recovery_unavailable');
      assert.equal(fixture.calls.filter(call => call === 'detail').length, 1);
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('concurrent exact-item grant requests cannot spend the same read twice', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-item-grant-'));
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  const fixture = operatorListingFixture({ hold });
  const grantId = randomUUID();
  const request = JSON.stringify({ action: 'operator_current_listing',
    release_id: '3422100', listing_id: '1366950910', grant_id: grantId }) + '\n';
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeListingGrantAck(socket, directory, grantId);
      const first = sendSocket(socket.path, request);
      for (let attempt = 0; attempt < 100; attempt++) {
        if (fixture.calls.includes('detail')) break;
        await new Promise(resolve => setTimeout(resolve, 5));
      }
      assert.ok(fixture.calls.includes('detail'));
      stopped(await sendSocket(socket.path, request), 'read_in_progress');
      release();
      assert.equal((await first).status, 'observed');
      stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
      assert.equal(fixture.calls.filter(call => call === 'detail').length, 1);
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('scoped cart grants reject wrong scope, stale boot, malformed IDs and changed pages', async () => {
  for (const [config, code] of [
    [{}, 'final_quote_unverified'],
    [{ title: 'Just a moment... Cloudflare' }, 'access_restricted'],
    [{ snapshot: { ...cartSnapshot(), listing_matches_expected: false } }, 'expected_listing_not_observed'],
  ]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
    const fixture = operatorCartFixture(config);
    const scoped = { action: 'operator_current_cart', listing_id: '3526572746', grant_id: cartGrantId };
    try {
      let socket = await operatorSocket(fixture.context, directory);
      try {
        await writeCartGrantAck(socket);
        stopped(await sendSocket(socket.path, JSON.stringify({ ...scoped, listing_id: '999' }) + '\n'),
          'unsupported_operator_recovery');
        stopped(await sendSocket(socket.path, JSON.stringify({ ...scoped, grant_id: '../x' }) + '\n'),
          'unsupported_operator_recovery');
        stopped(await sendSocket(socket.path, JSON.stringify({ ...scoped, extra: true }) + '\n'),
          'unsupported_operator_recovery');
        stopped(await sendSocket(socket.path, JSON.stringify(scoped) + '\n'), code);
        stopped(await sendSocket(socket.path, JSON.stringify(scoped) + '\n'),
          'operator_recovery_unavailable');
      } finally { await socket.close(); }
      socket = await operatorSocket(fixture.context, directory);
      try {
        const otherGrant = { ...scoped, grant_id: 'b0c12345-1234-4abc-8def-123456789abc' };
        stopped(await sendSocket(socket.path, JSON.stringify(otherGrant) + '\n'),
          'operator_acknowledgement_required');
        stopped(await sendSocket(socket.path, JSON.stringify(cartRequest) + '\n'), 'access_restricted');
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('cart exception consumes on wrong item, multiple rows, duplicate pages, challenge and page change', async () => {
  for (const [config, code] of [
    [{ url: releaseUrl }, 'exact_current_page_not_unique'],
    [{ duplicate: true }, 'exact_current_page_not_unique'],
    [{ title: 'Just a moment... Cloudflare' }, 'access_restricted'],
    [{ changed: true }, 'page_changed_during_read'],
    [{ snapshot: { ...cartSnapshot(), listing_matches_expected: false } }, 'expected_listing_not_observed'],
    [{ snapshot: { ...cartSnapshot(), item_row_count: 2 } }, 'unsupported_cart_quantity'],
  ]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
    const fixture = operatorCartFixture(config);
    try {
      const socket = await operatorSocket(fixture.context, directory);
      try {
        await writeCartAck(socket);
        const request = JSON.stringify({ action: 'operator_current_cart', listing_id: '3526572746' }) + '\n';
        stopped(await sendSocket(socket.path, request), code);
        stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('cart acknowledgement must be current and exact; concurrent calls cannot replay', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  const fixture = operatorCartFixture({ hold });
  const request = { action: 'operator_current_cart', listing_id: '3526572746' };
  try {
    await writeFile(join(directory, 'operator-cart-ack'),
      'operator_confirmed_current_cart:3526572746:old-boot\n', { mode: 0o600 });
    const socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_acknowledgement_required');
      await writeCartAck(socket);
      stopped(await sendSocket(socket.path, JSON.stringify({ ...request, navigate: true }) + '\n'),
        'unsupported_operator_recovery');
      const first = sendSocket(socket.path, JSON.stringify(request) + '\n');
      await new Promise(resolve => setImmediate(resolve));
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'), 'read_in_progress');
      release();
      stopped(await first, 'final_quote_unverified');
      assert.equal(fixture.calls.filter(call => call === 'evaluate').length, 1);
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('operator recovery is one-use, preserves stop across restart and never navigates', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  const fixture = operatorFixture();
  const request = JSON.stringify({ action: 'operator_current_release', release_id: '4204639' }) + '\n';
  try {
    let socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, JSON.stringify(releaseRequest) + '\n'), 'access_restricted');
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      assert.deepEqual(fixture.calls, []);
      await writeOperatorAck(socket);
      const result = await sendSocket(socket.path, request);
      assert.equal(result.status, 'observed'); assert.equal(result.observation.total, 2);
      assert.equal(result.purchase_allowed, false);
      stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify(releaseRequest) + '\n'), 'access_restricted');
      assert.deepEqual(fixture.calls, ['pages', 'url', 'url', 'title', 'url', 'evaluate', 'url']);
    } finally { await socket.close(); }
    socket = await operatorSocket(fixture.context, directory);
    try {
      stopped(await sendSocket(socket.path, request), 'operator_acknowledgement_required');
      stopped(await sendSocket(socket.path, JSON.stringify(releaseRequest) + '\n'), 'access_restricted');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('operator recovery consumes on wrong page, challenge, changed page, and incomplete page', async () => {
  for (const [config, code] of [
    [{ url: 'https://www.discogs.com/sell/release/4204640' }, 'exact_current_page_not_unique'],
    [{ title: 'Just a moment... Cloudflare' }, 'access_restricted'],
    [{ changed: true }, 'page_changed_during_read'],
    [{ complete: false }, 'release_observation_unverified'],
  ]) {
    const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
    const fixture = operatorFixture(config);
    try {
      const socket = await operatorSocket(fixture.context, directory);
      try {
        await writeOperatorAck(socket);
        const request = JSON.stringify({ action: 'operator_current_release', release_id: '4204639' }) + '\n';
        stopped(await sendSocket(socket.path, request), code);
        stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
        assert.ok(!fixture.calls.some(call => ['goto', 'newPage', 'click'].includes(call)));
      } finally { await socket.close(); }
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('one-use operator recovery reports verified partial rows without unlocking future reads', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  const fixture = operatorFixture({ complete: false, reportPartial: true });
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeOperatorAck(socket);
      const request = JSON.stringify({ action: 'operator_current_release', release_id: '4204639' }) + '\n';
      const result = await sendSocket(socket.path, request);
      assert.equal(result.status, 'incomplete'); assert.equal(result.code, 'additional_page_required');
      assert.deepEqual(result.observation.rows.map(row => row.listing_id), ['1', '2']);
      assert.equal(result.observation.complete, false); assert.equal(result.purchase_allowed, false);
      stopped(await sendSocket(socket.path, request), 'operator_recovery_unavailable');
      stopped(await sendSocket(socket.path, JSON.stringify(releaseRequest) + '\n'), 'access_restricted');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('concurrent operator requests cannot replay one acknowledgement', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  const fixture = operatorFixture({ hold });
  try {
    const socket = await operatorSocket(fixture.context, directory);
    try {
      await writeOperatorAck(socket);
      const request = JSON.stringify({ action: 'operator_current_release', release_id: '4204639' }) + '\n';
      const first = sendSocket(socket.path, request);
      await new Promise(resolve => setImmediate(resolve));
      stopped(await sendSocket(socket.path, request), 'read_in_progress');
      release();
      assert.equal((await first).status, 'observed');
      assert.equal(fixture.calls.filter(call => call === 'evaluate').length, 1);
    } finally { release(); await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('operator acknowledgement must be new and exact; extra request fields cannot read', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-'));
  const fixture = operatorFixture();
  const ackPath = join(directory, 'operator-rocky-ack');
  try {
    await writeFile(ackPath, 'operator_confirmed_current_release:4204639:old-boot\n',
      { mode: 0o600 });
    const socket = await operatorSocket(fixture.context, directory);
    try {
      const request = { action: 'operator_current_release', release_id: '4204639' };
      stopped(await sendSocket(socket.path, JSON.stringify(request) + '\n'),
        'operator_acknowledgement_required');
      await writeOperatorAck(socket);
      stopped(await sendSocket(socket.path, JSON.stringify({ ...request, navigate: true }) + '\n'),
        'unsupported_operator_recovery');
      assert.deepEqual(fixture.calls, []);
      assert.equal((await sendSocket(socket.path, JSON.stringify(request) + '\n')).status, 'observed');
    } finally { await socket.close(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
test('Unix socket is owner-only, one fixed JSON request, and has no network listener', async () => {
  const { context } = fixture();
  const path = `/tmp/sniper-test-${randomUUID().replaceAll('-', '')}.sock`;
  const close = await listenHostedReadOnly(context, path);
  try {
    assert.equal((await stat(path)).mode & 0o777, 0o600);
    await assert.rejects(listenHostedReadOnly(context, path), /EADDRINUSE/u);
    const result = await sendSocket(path, JSON.stringify(releaseRequest) + '\n');
    assert.equal(result.status, 'observed'); assert.equal(result.purchase_allowed, false);
    stopped(await sendSocket(path, JSON.stringify({ ...releaseRequest, cookies: true }) + '\n'),
      'unsupported_read_request');
    stopped(await sendSocket(path, 'x'.repeat(600) + '\n'), 'request_too_large');
  } finally { await close(); }
});

test('shared browser socket refuses an absent or untrusted bridge directory', async () => {
  const directory = { isDirectory: () => true, uid: 10001, gid: 113, mode: 0o42710 };
  assert.equal(privateBridgeDirectory(directory, 10001), true);
  assert.equal(privateBridgeDirectory({ ...directory, gid: 10001 }, 10001), false);
  assert.equal(privateBridgeDirectory({ ...directory, mode: 0o42730 }, 10001), false);
  await assert.rejects(listenHostedReadOnly(fixture().context,
    '/bridge/sniper-read-only.sock'), /ENOENT|private_bridge_directory_required/u);
});

test('staged launcher uses only approved profile and fixed socket, then closes cleanly', async () => {
  const calls = [];
  const runningBrowser = Object.assign(new EventEmitter(), { isConnected: () => true });
  const browser = { launchPersistentContext: async (profile, options) => {
    calls.push(['launch', profile, options]);
    return Object.assign(new EventEmitter(), { pages: () => [],
      browser: () => runningBrowser, close: async () => calls.push(['close']) });
  } };
  await assert.rejects(launchHostedReadOnlyBrowser(browser, '/another-profile',
    '/tmp/sniper-read-only.sock'), /unsupported_hosted_browser_configuration/);
  assert.deepEqual(calls, []);
  const close = await launchHostedReadOnlyBrowser(browser, '/profile', '/tmp/sniper-read-only.sock');
  try {
    assert.equal(calls[0][1], '/profile');
    assert.deepEqual(calls[0][2], { headless: false, acceptDownloads: false,
      noDefaultViewport: true, firefoxUserPrefs: { 'signon.rememberSignons': false } });
    assert.equal((await stat('/tmp/sniper-read-only.sock')).mode & 0o777, 0o600);
  } finally { await close(); }
  assert.deepEqual(calls.at(-1), ['close']);
});

test('socket shutdown aborts a pending lease write, closes idle clients and preserves stop/history', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-shutdown-'));
  let release, entered, writes = 0;
  const hold = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const fixture = monitorFixture();
  const grantId = randomUUID();
  let socket, client, idle;
  try {
    socket = await operatorSocket(fixture.context, directory, {
      leaseWriteHook: async () => { if (++writes === 1) { entered(); await hold; } },
    });
    await writeMonitorLeaseAck(socket, directory, grantId, '28720177');
    client = connect(socket.path); idle = connect(socket.path);
    await Promise.all([client, idle].map(connection =>
      new Promise(resolve => connection.once('connect', resolve))));
    client.write(JSON.stringify({ action: 'operator_monitor_lease_verify', grant_id: grantId,
      release_id: '28720177' }) + '\n');
    await started;
    const disconnected = Promise.all([client, idle].map(connection =>
      new Promise(resolve => connection.once('close', resolve))));
    let deadline;
    try {
      await Promise.race([socket.close(), new Promise((_, reject) => {
        deadline = setTimeout(() => reject(new Error('shutdown_waited_for_client')), 500);
      })]);
      await disconnected;
    } finally { clearTimeout(deadline); }
    await assert.rejects(stat(socket.path), { code: 'ENOENT' });
    release();
    const event = await waitForMonitorEvent(directory, 'lease_stopped', grantId);
    assert.equal(event.cause, 'request_disconnected');
    const state = JSON.parse(await readFile(join(directory, 'operator-monitor-lease.json'), 'utf8'));
    assert.equal(state.revoked, true);
    assert.equal(await readFile(socket.options.durableStopPath, 'utf8'), 'access_restricted\n');
    assert.equal(await readFile(join(directory, `operator-monitor-lease-grant-${grantId}-used`), 'utf8'),
      'consumed\n');
    assert.ok(!fixture.calls.some(value => Array.isArray(value) && value[0] === 'goto'));
    await socket.close();
  } finally {
    release(); client?.destroy(); idle?.destroy();
    if (socket) await socket.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('one exact release request uses canonical URL, fixed collector, closes tab, then cools down', async () => {
  let time = 1_000_000;
  const { context, calls } = navigationFixture();
  const navigate = createReleaseNavigator(context, { now: () => time });
  const result = await navigate(navigateRequest);
  assert.equal(result.status, 'observed');
  assert.equal(result.observation.complete, true);
  assert.equal(result.purchase_allowed, false);
  assert.deepEqual(calls[1], ['goto', releaseUrl, { waitUntil: 'domcontentloaded', timeout: 12000 }]);
  assert.equal(calls.filter(call => call[0] === 'evaluate').length, 1);
  assert.deepEqual(calls.at(-1), ['close']);
  stopped(await navigate(navigateRequest), 'release_cooldown');
  assert.equal(calls.filter(call => call[0] === 'newPage').length, 1);
  time += 15 * 60_000;
  assert.equal((await navigate(navigateRequest)).status, 'observed');
});

test('invalid or expanded navigation requests are blocked before opening a page', async () => {
  const { context, calls } = navigationFixture();
  const navigate = createReleaseNavigator(context);
  for (const request of [{ action: 'observe_release', release_id: '0111' },
    { ...navigateRequest, url: 'https://elsewhere.test/' },
    { action: 'observe_release', release_id: '111/../../cart' }]) {
    stopped(await navigate(request), 'unsupported_read_request');
  }
  assert.deepEqual(calls, []);
});

test('partial pagination and unready page stop without returning incomplete rows', async () => {
  const partial = navigationFixture({ result: { ...observedRelease(), status: 'incomplete',
    complete: false, has_next: true, error_code: 'additional_page_required', rows: [] } });
  const first = await createReleaseNavigator(partial.context)(navigateRequest);
  assert.equal(first.status, 'incomplete');
  assert.equal(first.code, 'additional_page_required');
  assert.equal(first.purchase_allowed, false);
  assert.equal(first.stop_required, true);
  assert.ok(!('observation' in first));
  assert.deepEqual(partial.calls.at(-1), ['close']);
  const unready = navigationFixture({ ready: false });
  stopped(await createReleaseNavigator(unready.context)(navigateRequest), 'marketplace_not_ready');
  assert.ok(!unready.calls.some(call => call[0] === 'evaluate'));
});

test('fixed navigation may report verified partial rows but cannot mark them complete', async () => {
  const { context, calls } = navigationFixture({ result: partialRelease() });
  const result = await createReleaseNavigator(context)(navigateRequest);
  assert.equal(result.status, 'incomplete'); assert.equal(result.code, 'additional_page_required');
  assert.equal(result.observation.rows[0].listing_id, '222');
  assert.equal(result.observation.complete, false); assert.equal(result.purchase_allowed, false);
  assert.equal(calls.filter(call => call[0] === 'goto').length, 1);
});

test('bounded next-link traversal returns only a complete same-release page set', async () => {
  const fixture = pagedNavigationFixture();
  const result = await createReleaseNavigator(fixture.context)(navigateRequest);
  assert.equal(result.status, 'observed'); assert.equal(result.purchase_allowed, false);
  assert.deepEqual(result.pages.map(value => [value.page, value.range_start, value.range_end]),
    [[1, 1, 1], [2, 2, 2]]);
  assert.deepEqual(fixture.calls.filter(call => call[0] === 'goto').map(call => call[1]),
    [releaseUrl, releaseUrl + '?page=2']);
  assert.deepEqual(fixture.calls.at(-1), ['close']);
  for (const [config, code] of [
    [{ secondId: '222' }, 'pagination_changed'],
    [{ secondTotal: 3 }, 'pagination_changed'],
    [{ secondLanded: 'https://www.discogs.com/sell/release/112?page=2' }, 'unexpected_redirect'],
    [{ secondStatus: 403 }, 'access_restricted'],
    [{ secondTitle: 'Just a moment... Cloudflare' }, 'access_restricted'],
  ]) {
    const blockedFixture = pagedNavigationFixture(config);
    stopped(await createReleaseNavigator(blockedFixture.context)(navigateRequest), code);
    assert.deepEqual(blockedFixture.calls.at(-1), ['close']);
  }
});

test('a lease deadline stops before requesting the next marketplace page', async () => {
  let now = 1_000_000;
  const fixture = pagedNavigationFixture({ onFirstEvaluate: () => { now += 101; } });
  const navigate = createReleaseNavigator(fixture.context, { now: () => now });
  stopped(await navigate(navigateRequest, { deadline: now + 100 }), 'monitor_lease_unavailable');
  assert.deepEqual(fixture.calls.filter(call => call[0] === 'goto').map(call => call[1]),
    [releaseUrl]);
  assert.deepEqual(fixture.calls.at(-1), ['close']);
});

test('429 honors Retry-After without retrying and 403 or challenge latches access stop', async () => {
  let time = 1_000_000;
  const rate = navigationFixture({ status: 429, retryAfter: '3600' });
  const limited = createReleaseNavigator(rate.context, { now: () => time });
  stopped(await limited(navigateRequest), 'rate_limited');
  time += 15 * 60_000;
  stopped(await limited(navigateRequest), 'release_cooldown');
  assert.equal(rate.calls.filter(call => call[0] === 'goto').length, 1);
  const denied = navigationFixture({ status: 403 });
  const blocked = createReleaseNavigator(denied.context);
  stopped(await blocked(navigateRequest), 'access_restricted');
  stopped(await blocked(navigateRequest), 'access_restricted');
  assert.equal(denied.calls.filter(call => call[0] === 'goto').length, 1);
  const challenge = navigationFixture({ title: 'Just a moment...' });
  stopped(await createReleaseNavigator(challenge.context)(navigateRequest), 'access_restricted');
  assert.ok(!challenge.calls.some(call => call[0] === 'evaluate'));
});

test('future release access restrictions report only a bounded cause code', async () => {
  for (const [config, code, cause] of [
    [{ status: 401 }, 'auth_required', 'http_401'],
    [{ status: 403 }, 'access_restricted', 'http_403'],
    [{ landed: 'https://www.discogs.com/cdn-cgi/challenge' }, 'access_restricted',
      'challenge_redirect'],
    [{ landed: 'https://www.discogs.com/login' }, 'access_restricted', 'login_redirect'],
    [{ title: 'Just a moment... Cloudflare' }, 'access_restricted', 'challenge_title'],
  ]) {
    const fixture = navigationFixture(config);
    const result = await createReleaseNavigator(fixture.context)(navigateRequest);
    stopped(result, code);
    assert.equal(result.restriction_cause, cause);
    assert.ok(!JSON.stringify(result).includes('https://'));
  }
});

test('cancellation closes navigation tab and concurrent attempt does not overlap', async () => {
  let resolveGoto;
  const calls = [];
  const page = { goto: () => new Promise(resolve => { resolveGoto = resolve; }),
    close: async () => calls.push('close') };
  const context = { newPage: async () => { calls.push('newPage'); return page; } };
  const navigate = createReleaseNavigator(context);
  const controller = new AbortController();
  const pending = navigate(navigateRequest, { signal: controller.signal });
  await new Promise(resolve => setImmediate(resolve));
  stopped(await navigate(navigateRequest), 'read_in_progress');
  controller.abort();
  resolveGoto(null);
  stopped(await pending, 'read_cancelled');
  assert.deepEqual(calls, ['newPage', 'close', 'close']);
});

test('owner-only socket dispatches one canonical navigation and refuses immediate repeat', async () => {
  const { context, calls } = navigationFixture();
  const path = `/tmp/sniper-test-${randomUUID().replaceAll('-', '')}.sock`;
  const close = await listenHostedReadOnly(context, path);
  try {
    const first = await sendSocket(path, JSON.stringify(navigateRequest) + '\n');
    assert.equal(first.status, 'observed');
    assert.equal(first.purchase_allowed, false);
    stopped(await sendSocket(path, JSON.stringify(navigateRequest) + '\n'), 'release_cooldown');
    assert.equal(calls.filter(call => call[0] === 'goto').length, 1);
  } finally { await close(); }
});

test('socket shares access-denial and rate-limit stops across release and item actions', async () => {
  for (const [status, code] of [[429, 'rate_limited'], [403, 'access_restricted']]) {
    const { context, calls } = itemFixture({ status });
    const path = `/tmp/sniper-test-${randomUUID().replaceAll('-', '')}.sock`;
    const close = await listenHostedReadOnly(context, path);
    try {
      stopped(await sendSocket(path, JSON.stringify(itemRequest) + '\n'), code);
      stopped(await sendSocket(path, JSON.stringify(navigateRequest) + '\n'), code);
      assert.equal(calls.filter(call => call[0] === 'newPage').length, 1);
    } finally { await close(); }
  }
});

test('shared classifier exposes fixed kinds and causes without provider text or URLs', () => {
  const cases = [
    [{ status: 401 }, 'auth_required', 'login_required', 'http_401'],
    [{ status: 403 }, 'access_restricted', 'explicit_access_denied', 'http_403'],
    [{ status: 503 }, 'service_unavailable', 'service_unavailable', 'http_503'],
    [{ status: 429 }, 'rate_limited', 'rate_limited', 'http_429'],
    [{ status: 503, title: 'Just a moment... Cloudflare' },
      'access_restricted', 'human_verification_required', 'challenge_title'],
    [{ status: 429, dom: { challenge_marker_present: true } },
      'access_restricted', 'human_verification_required', 'challenge_marker'],
    [{ url: 'https://www.discogs.com/login?secret=never-echo' },
      'access_restricted', 'login_required', 'login_redirect'],
    [{ title: 'Sign in to Discogs' }, 'access_restricted', 'login_required', 'login_marker'],
    [{ dom: { password_input_present: true } },
      'access_restricted', 'login_required', 'login_marker'],
    [{ title: 'You have been blocked.' },
      'access_restricted', 'explicit_access_denied', 'explicit_denial'],
    [{ status: 403, dom: { challenge_marker_present: true, password_input_present: true } },
      'access_restricted', 'human_verification_required', 'challenge_marker'],
  ];
  for (const [input, code, kind, cause] of cases) {
    const result = classifyHostedRestriction({ title: 'SECRET PROVIDER TEXT', ...input });
    stopped(result, code);
    assert.equal(result.restriction_kind, kind); assert.equal(result.restriction_cause, cause);
    assert.equal(JSON.stringify(result).includes('SECRET'), false);
    assert.equal(JSON.stringify(result).includes('https://'), false);
    assert.equal(JSON.stringify(result).includes('never-echo'), false);
  }
  assert.equal(classifyHostedRestriction({ status: 200, title: 'Marketplace',
    dom: { challenge_marker_present: 'true', password_input_present: 1 } }), null);
});

test('restriction DOM probe returns booleans only and ignores hidden challenge widgets', () => {
  const { JSDOM } = require('jsdom');
  const probes = [
    ['<h1>You have been blocked.</h1>', 'explicit_denial_present'],
    ['<input type="password" value="SECRET PASSWORD">', 'password_input_present'],
    ['<iframe src="https://challenges.cloudflare.com/SECRET"></iframe>', 'challenge_marker_present'],
    ['<div class="CAPTCHA">Verification</div>', 'challenge_marker_present'],
    ['<div hidden><div class="captcha">Hidden widget</div></div>', null],
    ['<p>SECRET STREET ADDRESS</p><script>SECRET SESSION</script>', null],
  ];
  for (const [html, expected] of probes) {
    const dom = new JSDOM(`<!doctype html><body>${html}</body>`, { runScripts: 'outside-only' });
    try {
      dom.window.Element.prototype.getClientRects = () => [{ width: 100, height: 20 }];
      Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
        get() { return this.textContent; }, configurable: true });
      dom.window.fetch = () => { throw Error('Forbidden network'); };
      dom.window.HTMLElement.prototype.click = () => { throw Error('Forbidden click'); };
      const result = JSON.parse(JSON.stringify(dom.window.eval(`(${collectHostedRestrictionDom.toString()})()`)));
      assert.deepEqual(Object.keys(result).sort(),
        ['challenge_marker_present', 'explicit_denial_present', 'password_input_present']);
      assert.equal(Object.values(result).every(value => typeof value === 'boolean'), true);
      assert.equal(Object.values(result).filter(Boolean).length, expected ? 1 : 0);
      if (expected) assert.equal(result[expected], true);
      assert.equal(JSON.stringify(result).includes('SECRET'), false);
    } finally { dom.window.close(); }
  }
});

test('delayed same-URL release challenge or login is classified after readiness timeout and latched', async () => {
  for (const [config, kind, cause] of [
    [{ restrictionDom: { challenge_marker_present: true } }, 'human_verification_required', 'challenge_marker'],
    [{ restrictionDom: { password_input_present: true } }, 'login_required', 'login_marker'],
    [{ restrictionDom: { explicit_denial_present: true } }, 'explicit_access_denied', 'explicit_denial'],
    [{ title: (() => { let n = 0; return () => ++n === 1 ? 'Marketplace' : 'Sign in to Discogs'; })() },
      'login_required', 'login_marker'],
  ]) {
    const f = navigationFixture({ ready: false, ...config });
    const navigate = createReleaseNavigator(f.context);
    const result = await navigate(navigateRequest);
    stopped(result, 'access_restricted');
    assert.equal(result.restriction_kind, kind); assert.equal(result.restriction_cause, cause);
    stopped(await navigate(navigateRequest), 'access_restricted');
    assert.equal(f.calls.filter(call => call[0] === 'goto').length, 1);
    assert.equal(f.calls.some(call => call[0] === 'evaluate'), false);
  }
  const f = navigationFixture({ ready: false });
  const unknown = await createReleaseNavigator(f.context)(navigateRequest);
  stopped(unknown, 'marketplace_not_ready');
  assert.equal(unknown.restriction_kind, 'unknown_readiness');
});

test('DOM challenge beats HTTP429 and503 without retry for release and item navigation', async () => {
  for (const status of [429, 503]) {
    for (const [make, request] of [[navigationFixture, navigateRequest], [itemFixture, itemRequest]]) {
      const f = make({ status, restrictionDom: { challenge_marker_present: true } });
      const navigate = make === navigationFixture ? createReleaseNavigator(f.context) :
        createListingNavigator(f.context);
      const result = await navigate(request);
      stopped(result, 'access_restricted');
      assert.equal(result.restriction_kind, 'human_verification_required');
      stopped(await navigate(request), 'access_restricted');
      assert.equal(f.calls.filter(call => call[0] === 'goto').length, 1);
      assert.equal(f.calls.some(call => call[0] === 'ready'), false);
      assert.deepEqual(f.calls.at(-1), ['close']);
    }
  }
});

test('pagination uses the same challenge classifier and never requests a further page', async () => {
  const f = pagedNavigationFixture({ secondStatus: 503,
    secondRestrictionDom: { challenge_marker_present: true } });
  const result = await createReleaseNavigator(f.context)(navigateRequest);
  stopped(result, 'access_restricted');
  assert.equal(result.restriction_kind, 'human_verification_required');
  assert.equal(f.calls.filter(call => call[0] === 'goto').length, 2);
  assert.equal(f.calls.filter(call => call[0] === 'evaluate').length, 1);
});

test('item denial and delayed login retain distinct causes with compatible public stops', async () => {
  const denied = await createListingNavigator(itemFixture({ status: 403 }).context)(itemRequest);
  stopped(denied, 'access_restricted');
  assert.equal(denied.restriction_cause, 'http_403');
  assert.equal(denied.restriction_kind, 'explicit_access_denied');
  const f = itemFixture({ ready: false, diagnostic: { password_input_present: true } });
  const result = await createListingNavigator(f.context)(itemRequest);
  stopped(result, 'access_restricted');
  assert.equal(result.restriction_cause, 'login_marker');
  assert.equal(result.restriction_kind, 'login_required');
});

test('missing title evidence cannot qualify a page or hide a definite HTTP denial', async () => {
  for (const status of [200, 403]) {
    let observations = 0, navigations = 0;
    const page = { goto: async () => { navigations++; return { status: () => status }; },
      url: () => releaseUrl, title: async () => { throw Error('SECRET TITLE FAILURE'); },
      waitForSelector: async () => {}, close: async () => {},
      evaluate: async () => { observations++; return observedRelease(); } };
    const navigator = createReleaseNavigator({ newPage: async () => page });
    const result = await navigator(navigateRequest);
    stopped(result, status === 403 ? 'access_restricted' : 'navigation_or_read_failed');
    assert.equal(observations, 0); assert.equal(JSON.stringify(result).includes('SECRET'), false);
    if (status === 403) {
      assert.equal(result.restriction_cause, 'http_403');
      stopped(await navigator(navigateRequest), 'access_restricted');
      assert.equal(navigations, 1);
    }
  }
});
