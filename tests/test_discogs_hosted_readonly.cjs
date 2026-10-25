'use strict';
// Synthetic port tests: no browser profile, Discogs request, cart, or order access.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
let observeCurrentHostedPage, createReleaseNavigator, createListingNavigator,
  listenHostedReadOnly, privateBridgeDirectory, launchHostedReadOnlyBrowser,
  collectReleaseMarketplaceDom, collectListingDetailDom, collectCartDom;
before(async () => {
  ({ observeCurrentHostedPage, createReleaseNavigator, createListingNavigator,
    listenHostedReadOnly, privateBridgeDirectory } =
    await import('../scripts/discogs_hosted_readonly.mjs'));
  ({ launchHostedReadOnlyBrowser } = await import('../scripts/discogs_hosted_browser_runtime.mjs'));
  ({ collectReleaseMarketplaceDom } = await import('../scripts/discogs_release_snapshot.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
  ({ collectListingDetailDom } = await import('../scripts/discogs_listing_detail_snapshot.mjs'));
});

const releaseUrl = 'https://www.discogs.com/sell/release/111';
const cartUrl = 'https://www.discogs.com/sell/cart/';
const releaseRequest = { action: 'current_release', release_id: '111' };
const cartRequest = { action: 'current_cart', listing_id: '222' };
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
  result = observedRelease(), retryAfter = null, ready = true } = {}) {
  const calls = [];
  const page = new Proxy({
    goto(url, options) {
      calls.push(['goto', url, options]);
      return { status: () => status, headerValue: () => Promise.resolve(retryAfter) };
    },
    url: () => landed,
    title: async () => title,
    waitForSelector(selector) {
      calls.push(['ready', selector]);
      if (!ready) throw new Error('fixture not ready');
    },
    evaluate(fn, id) {
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
function itemFixture({ status = 200, title = 'Listing', landed = itemUrl,
  result = observedItem(), ready = true } = {}) {
  const calls = [];
  const page = new Proxy({
    goto(url, options) { calls.push(['goto', url, options]); return { status: () => status }; },
    url: () => landed,
    title: async () => title,
    waitForSelector(selector) { calls.push(['ready', selector]); if (!ready) throw Error('unready'); },
    evaluate(fn, expected) {
      calls.push(['evaluate', expected]);
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
  const browser = { launchPersistentContext: async (profile, options) => {
    calls.push(['launch', profile, options]);
    return { pages: () => [], close: async () => calls.push(['close']) };
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
