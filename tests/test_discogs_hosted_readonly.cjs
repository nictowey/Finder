'use strict';
// Synthetic port tests: no browser profile, Discogs request, cart, or order access.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
let observeCurrentHostedPage, listenHostedReadOnly, launchHostedReadOnlyBrowser,
  collectReleaseMarketplaceDom, collectCartDom;
before(async () => {
  ({ observeCurrentHostedPage, listenHostedReadOnly } = await import('../scripts/discogs_hosted_readonly.mjs'));
  ({ launchHostedReadOnlyBrowser } = await import('../scripts/discogs_hosted_browser_runtime.mjs'));
  ({ collectReleaseMarketplaceDom } = await import('../scripts/discogs_release_snapshot.mjs'));
  ({ collectCartDom } = await import('../scripts/discogs_cart_inspector.mjs'));
});

const releaseUrl = 'https://www.discogs.com/sell/release/111';
const cartUrl = 'https://www.discogs.com/sell/cart/';
const releaseRequest = { action: 'current_release', release_id: '111' };
const cartRequest = { action: 'current_cart', listing_id: '222' };
const observedRelease = () => ({ source: 'discogs_rendered_release', release_id: 111,
  url: releaseUrl, status: 'observed', complete: true, total: 0, rows: [], purchase_allowed: false });
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
    const result = await sendSocket(path, JSON.stringify(releaseRequest) + '\n');
    assert.equal(result.status, 'observed'); assert.equal(result.purchase_allowed, false);
    stopped(await sendSocket(path, JSON.stringify({ ...releaseRequest, cookies: true }) + '\n'),
      'unsupported_read_request');
    stopped(await sendSocket(path, 'x'.repeat(600) + '\n'), 'request_too_large');
  } finally { await close(); }
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
