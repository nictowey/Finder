'use strict';
// Source-only helper tests: no listener, browser, network or cart mutation.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, writeFile, readFile, rm, readdir, lstat } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID } = require('node:crypto');

const load = () => import('../scripts/discogs_hosted_cart_helper.mjs');
const stamp = () => Date.now();
let itemDigest;
function itemDetail() {
  return { schema: 'discogs_listing_detail_dom_v1', source: 'discogs_rendered_listing',
    status: 'observed', complete: true, purchase_allowed: false,
    release_id: 123, listing_id: '1002', seller_handle: 'Seller_1',
    available: true, cart_state: 'addable',
    url: 'https://www.discogs.com/sell/item/1002',
    observed_at: new Date().toISOString(), item: '5.00', media: 'VG+', sleeve: 'VG+',
    seller_percent: 99.5, feedback: 10, currency: null,
    seller_comments: 'Clean copy, plays great', seller_comments_supported: true,
    reported_shipping_text: '$4.95 shipping' };
}
before(async () => { itemDigest = (await load()).visibleItemDigest(itemDetail()); });
function request(now) {
  return { schema: 'discogs_hosted_cart_prepare_v1', action: 'prepare_cart_quote_only',
    purchase_allowed: false, item_digest: itemDigest,
    scope: { action: 'cart_quote_inspection', release_id: 123, listing_id: '1002',
      seller_handle: 'Seller_1', rules_revision: 7, idempotency_key: randomUUID(),
      observation_fingerprint: 'a'.repeat(64), issued_at: now / 1000 - 1,
      expires_at: now / 1000 + 120 } };
}
function fixturePage({ title = 'Item', seller = 'Seller_1' } = {}) {
  const calls = [];
  const url = 'https://www.discogs.com/sell/item/1002';
  const page = { url: () => { calls.push('url'); return url; },
    title: async () => { calls.push('title'); return title; },
    evaluate: async fn => {
      calls.push(fn.name);
      if (fn.name === 'collectListingReadinessDom') {
        return { password_input_present: false, challenge_marker_present: false };
      }
      if (fn.name === 'collectListingDetailDom') {
        return { ...itemDetail(), seller_handle: seller };
      }
      throw new Error('unsupported collector');
    },
    getByRole: () => { throw new Error('cart control must not be reached'); },
    goto: () => { throw new Error('navigation must not be reached'); },
  };
  return { context: { pages: () => { calls.push('pages'); return [page]; } }, calls };
}
async function withStore(run) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-cart-helper-'));
  const stopPath = join(directory, 'global-stop');
  await writeFile(stopPath, 'access_restricted\n', { mode: 0o600 });
  try { return await run({ directory, stopPath }); }
  finally { await rm(directory, { recursive: true, force: true }); }
}
async function ack(directory, bootId, scope, digest) {
  const path = join(directory, `operator-cart-prepare-${scope.idempotency_key}-${digest}-ack`);
  await writeFile(path,
    `operator_confirmed_cart_quote_inspection:${bootId}:${scope.idempotency_key}:${digest}\n`,
    { mode: 0o600 });
}

test('listener remains explicitly off by default', async () => {
  const { listenHostedCartHelper } = await load();
  await assert.rejects(listenHostedCartHelper({}), /cart_helper_disabled/u);
});

test('fixed request rejects extra controls, missing purchase-off and stale scope', async () => {
  const { parseCartPrepareRequest } = await load();
  const now = stamp();
  const good = request(now);
  assert.equal(parseCartPrepareRequest(good, now / 1000).scope.listing_id, '1002');
  assert.equal(parseCartPrepareRequest({ ...good, url: 'https://example.com' }, now / 1000), null);
  assert.equal(parseCartPrepareRequest({ ...good, purchase_allowed: true }, now / 1000), null);
  assert.equal(parseCartPrepareRequest({ ...good, scope: { ...good.scope, listing_id: '1003' },
    unknown: 1 }, now / 1000), null);
  assert.equal(parseCartPrepareRequest(good, now / 1000 + 121), null);
});

test('missing acknowledgement never opens browser; consumed grant never replays', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now });
    assert.equal((await handle(input)).code, 'operator_acknowledgement_required');
    assert.deepEqual(fixture.calls, []);
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    assert.equal((await handle(input)).code, 'pre_cart_state_unverified');
    assert.ok(fixture.calls.includes('collectListingDetailDom'));
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal((await readFile(stopPath, 'utf8')), 'access_restricted\n');
    const used = (await readdir(directory)).filter(name => name.endsWith('-used'));
    assert.equal(used.length, 1);
    assert.match(await readFile(join(directory, used[0]), 'utf8'),
      new RegExp(`^${bootId}:${input.scope.idempotency_key}:`));
  });
});

test('challenge and changed seller consume scope and stop before controls', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  for (const [options, expected] of [[{ title: 'Just a moment - Cloudflare' }, 'access_restricted'],
    [{ seller: 'Different' }, 'listing_identity_or_state_unverified']]) {
    await withStore(async ({ directory, stopPath }) => {
      const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage(options);
      await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
      const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
        stopPath, clock: () => now });
      assert.equal((await handle(input)).code, expected);
      assert.equal((await readdir(directory)).filter(name => name.endsWith('-used')).length, 1);
      assert.equal(fixture.calls.includes('getByRole'), false);
    });
  }
});

test('unverified empty-cart hook cannot unlock a click', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: false, item_count: 1 }) });
    assert.equal((await handle(input)).code, 'pre_cart_state_unverified');
    assert.ok(!fixture.calls.includes('getByRole'));
  });
});

test('operator markers reject scope changes and expired acknowledgements', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    input.scope.seller_handle = 'Changed';
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now });
    assert.equal((await handle(input)).code, 'operator_acknowledgement_required');
    assert.deepEqual(fixture.calls, []);
  });
});

test('old operator acknowledgement cannot authorize a different item digest', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    input.item_digest = 'f'.repeat(64);
    const handle = createCartPrepareHandler({ context: fixture.context, bootId,
      directory, stopPath, clock: () => now });
    assert.equal((await handle(input)).code, 'operator_acknowledgement_required');
    assert.deepEqual(fixture.calls, []);
  });
});

test('changed visible item fields reject before cart inspection', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  for (const change of [
    detail => { detail.item = '6.00'; },
    detail => { detail.seller_comments = 'Scratched copy'; },
    detail => { detail.media = 'NM'; },
    detail => { detail.seller_percent = 98.0; },
  ]) {
    await withStore(async ({ directory, stopPath }) => {
      const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
      const page = fixture.context.pages()[0];
      const original = page.evaluate;
      page.evaluate = async fn => {
        const result = await original(fn);
        if (fn.name === 'collectListingDetailDom') change(result);
        return result;
      };
      await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
      const handle = createCartPrepareHandler({ context: fixture.context, bootId,
        directory, stopPath, clock: () => now });
      assert.equal((await handle(input)).code, 'listing_identity_or_state_unverified');
      assert.ok(!fixture.calls.includes('getByRole'));
    });
  }
});

test('changed item comment at final click recheck cannot dispatch click', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    const original = page.evaluate;
    let detailReads = 0, clicks = 0;
    page.evaluate = async fn => {
      const result = await original(fn);
      if (fn.name === 'collectListingDetailDom' && ++detailReads === 2) {
        result.seller_comments = 'Scratched copy';
      }
      return result;
    };
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { clicks++; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId,
      directory, stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest),
        cart_url: 'https://www.discogs.com/sell/cart/', observed_at_ms: now }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(clicks, 0);
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
  });
});

test('used marker survives a new helper and boot; one request is serialized', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), firstBoot = randomUUID(), input = request(now);
    let resumeTitle;
    const fixture = fixturePage();
    const page = fixture.context.pages()[0];
    page.title = () => new Promise(resolve => { resumeTitle = () => resolve('Item'); });
    await ack(directory, firstBoot, input.scope, scopeDigest(input.scope, input.item_digest));
    const first = createCartPrepareHandler({ context: fixture.context, bootId: firstBoot,
      directory, stopPath, clock: () => now });
    const inFlight = first(input);
    while (!resumeTitle) await new Promise(resolve => setImmediate(resolve));
    assert.equal((await first(input)).code, 'cart_helper_busy');
    resumeTitle();
    assert.equal((await inFlight).code, 'pre_cart_state_unverified');
    const secondBoot = randomUUID();
    await ack(directory, secondBoot, input.scope, scopeDigest(input.scope, input.item_digest));
    const second = createCartPrepareHandler({ context: fixture.context, bootId: secondBoot,
      directory, stopPath, clock: () => now });
    assert.equal((await second(input)).code, 'cart_outcome_uncertain');
  });
});

test('synthetic browser restart with merchant-retained item cannot add a second copy', async () => {
  const { createCartPrepareHandler, createCurrentEmptyCartVerifier, scopeDigest,
    visibleItemDigest } = await load();
  // The prior add happened through normal UI. This fresh helper directory has
  // no program mutation marker, and this boot receives a new scoped grant.
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    const inCart = { ...itemDetail(), cart_state: 'already_in_cart' };
    input.item_digest = visibleItemDigest(inCart);
    const originalEvaluate = page.evaluate;
    page.evaluate = fn => fn.name === 'collectListingDetailDom' ? inCart : originalEvaluate(fn);
    let clicks = 0;
    page.getByRole = () => { clicks++; throw new Error('duplicate cart control reached'); };
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now });
    assert.equal((await handle(input)).code, 'listing_identity_or_state_unverified');
    assert.equal(clicks, 0);
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
  });
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    let clicks = 0;
    page.getByRole = () => { clicks++; throw new Error('duplicate cart control reached'); };
    // The detail page can still say addable while the merchant cart already
    // contains this same item. Only grounded current-cart evidence may decide.
    const merchantCart = { url: () => 'https://www.discogs.com/sell/cart/',
      evaluate: async () => emptyCartDom({ status: 'blocked', code: 'cart_items_present',
        empty: false, item_count: 1, seller_count: 1 }) };
    const context = { pages: () => [page, merchantCart] };
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context, bootId, directory, stopPath,
      clock: () => now, verifyEmptyCart: createCurrentEmptyCartVerifier({ clock: () => now }) });
    assert.equal((await handle(input)).code, 'pre_cart_state_unverified');
    assert.equal(clicks, 0);
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
  });
});

test('a scoped click is issued once and uncertain outcome fences all later attempts', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    const originalUrl = page.url;
    let landed = null;
    page.url = () => landed ?? originalUrl();
    let actualClicks = 0;
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; landed = 'https://www.discogs.com/sell/cart/'; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest), cart_url: 'https://www.discogs.com/sell/cart/',
        observed_at_ms: now }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 1);
    assert.equal((await readFile(join(directory, 'cart-mutation-reconciliation-required'), 'utf8')),
      `${bootId}:${input.scope.idempotency_key}:${scopeDigest(input.scope, input.item_digest)}:attempting\n`);
    const other = request(now);
    other.scope.listing_id = '1003';
    assert.equal((await handle(other)).code, 'cart_mutation_reconciliation_required');
    assert.equal(actualClicks, 1);
  });
});

test('scope expiry during delayed locator work stops before any click', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = [stamp()], bootId = randomUUID(), input = request(now[0]), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    let actualClicks = 0;
    page.getByRole = () => ({
      count: async () => { now[0] += 121_000; return 1; },
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now[0],
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest), cart_url: 'https://www.discogs.com/sell/cart/',
        observed_at_ms: now[0] }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 0);
  });
});

test('client disconnect during a locator wait cannot dispatch a late click', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0], controller = new AbortController();
    let actualClicks = 0;
    page.getByRole = () => ({
      count: async () => { controller.abort(); return 1; },
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest), cart_url: 'https://www.discogs.com/sell/cart/',
        observed_at_ms: now }) });
    assert.equal((await handle(input, controller.signal)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 0);
  });
});

test('disconnect after one issued click retains global latch and prevents later operations', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0], controller = new AbortController();
    let actualClicks = 0, laterNavigations = 0;
    page.goto = () => { laterNavigations++; throw new Error('forbidden late navigation'); };
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; controller.abort(); } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest), cart_url: 'https://www.discogs.com/sell/cart/',
        observed_at_ms: now }) });
    assert.equal((await handle(input, controller.signal)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 1);
    assert.equal(laterNavigations, 0);
    assert.equal((await handle(request(now))).code, 'cart_mutation_reconciliation_required');
    assert.equal((await readFile(join(directory, 'cart-mutation-reconciliation-required'), 'utf8')),
      `${bootId}:${input.scope.idempotency_key}:${scopeDigest(input.scope, input.item_digest)}:attempting\n`);
  });
});

test('reported click timeout remains uncertain and globally fenced', async () => {
  const { createCartPrepareHandler, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    let actualClicks = 0;
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; const error = new Error('timeout');
          error.name = 'TimeoutError'; throw error; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest), cart_url: 'https://www.discogs.com/sell/cart/',
        observed_at_ms: now }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 1);
    assert.equal((await handle(request(now))).code, 'cart_mutation_reconciliation_required');
  });
});

test('exclusive marker syncs after an owner-only write and cannot be replaced', async () => {
  const { durableExclusiveMarker } = await load();
  await withStore(async ({ directory }) => {
    const path = join(directory, 'durable-marker');
    let syncReached = false;
    await durableExclusiveMarker(path, 'bound\n', async marker => {
      syncReached = true;
      assert.equal(marker, path);
      assert.equal(await readFile(path, 'utf8'), 'bound\n');
      assert.equal((await lstat(path)).mode & 0o777, 0o600);
    });
    assert.equal(syncReached, true);
    await assert.rejects(durableExclusiveMarker(path, 'different\n'), { code: 'EEXIST' });
    assert.equal(await readFile(path, 'utf8'), 'bound\n');
  });
});

test('used-marker directory sync failure stops before inspecting browser', async () => {
  const { createCartPrepareHandler, scopeDigest, durableExclusiveMarker } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
      stopPath, clock: () => now,
      persistMarker: (path, content) => durableExclusiveMarker(path, content,
        async () => { throw new Error('directory sync failed'); }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.deepEqual(fixture.calls, []);
  });
});

test('global mutation marker sync failure cannot dispatch a click', async () => {
  const { createCartPrepareHandler, scopeDigest, durableExclusiveMarker } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    let actualClicks = 0;
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { actualClicks++; } }),
    });
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context: fixture.context, bootId,
      directory, stopPath, clock: () => now,
      persistMarker: (path, content) => path.endsWith('cart-mutation-reconciliation-required') ?
        durableExclusiveMarker(path, content, async () => { throw new Error('directory sync failed'); }) :
        durableExclusiveMarker(path, content),
      verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
        source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
        scope_digest: scopeDigest(input.scope, input.item_digest),
        cart_url: 'https://www.discogs.com/sell/cart/', observed_at_ms: now }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(actualClicks, 0);
    assert.equal((await lstat(join(directory, 'cart-mutation-reconciliation-required'))).mode & 0o777, 0o600);
  });
});

function emptyCartDom(changes = {}) {
  return { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
    source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
    purchase_allowed: false, ...changes };
}

test('empty-cart factory uses only existing page URL and the fixed DOM collector', async () => {
  const { createCurrentEmptyCartVerifier, scopeDigest } = await load();
  const { collectEmptyCartDom } = await import('../scripts/discogs_empty_cart.mjs');
  const now = stamp(), input = request(now), calls = [];
  const forbidden = () => { throw new Error('unexpected browser capability'); };
  const page = {
    url: () => { calls.push('url'); return 'https://www.discogs.com/sell/cart/'; },
    evaluate: async fn => {
      calls.push('evaluate'); assert.equal(fn, collectEmptyCartDom);
      return emptyCartDom({ scope_digest: 'f'.repeat(64), observed_at_ms: -1,
        cart_url: 'https://untrusted.invalid/', secret: 'must-not-return' });
    },
    goto: forbidden, getByRole: forbidden, title: forbidden, cookies: forbidden,
  };
  const context = { pages: () => { calls.push('pages'); return [page]; },
    newPage: forbidden, newContext: forbidden, cookies: forbidden };
  const verify = createCurrentEmptyCartVerifier({ clock: () => { calls.push('clock'); return now; } });
  const result = await verify({ context, scope: input.scope, itemDigest: input.item_digest });
  assert.deepEqual(result, { schema: 'discogs_empty_cart_verified_v1', source: 'visible_cart_dom',
    empty: true, item_count: 0, seller_count: 0,
    cart_url: 'https://www.discogs.com/sell/cart/',
    scope_digest: scopeDigest(input.scope, input.item_digest), document_age_ms: result.document_age_ms,
    observed_at_ms: now });
  assert.ok(result.document_age_ms >= 0 && result.document_age_ms <= 5_000);
  assert.equal(calls.filter(call => call === 'evaluate').length, 1);
  assert.equal(calls.at(-1), 'clock');
  assert.ok(calls.every(call => ['pages', 'url', 'evaluate', 'clock'].includes(call)));
});

test('empty-cart factory rejects missing, duplicate and noncanonical current carts before DOM', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now);
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  let evaluations = 0;
  const cart = url => ({ url: () => url, evaluate: async () => { evaluations++; return emptyCartDom(); } });
  const canonical = 'https://www.discogs.com/sell/cart/';
  for (const pages of [[], [cart(canonical), cart(canonical)],
    [cart(`${canonical}?add=1002`)], [cart(canonical.slice(0, -1))],
    Array.from({ length: 17 }, () => cart(canonical)), null]) {
    assert.equal(await verify({ context: { pages: () => pages },
      scope: input.scope, itemDigest: input.item_digest }), null);
  }
  assert.equal(evaluations, 0);
});

test('empty-cart factory rejects changed URL and unverified DOM observations', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now), canonical = 'https://www.discogs.com/sell/cart/';
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  for (const changes of [{ empty: false }, { item_count: 1 }, { seller_count: 1 },
    { source: 'offline_fixture' }, { purchase_allowed: true }, { status: 'blocked' }]) {
    const page = { url: () => canonical, evaluate: async () => emptyCartDom(changes) };
    assert.equal(await verify({ context: { pages: () => [page] },
      scope: input.scope, itemDigest: input.item_digest }), null);
  }
  let url = canonical;
  const changed = { url: () => url, evaluate: async () => {
    url = 'https://www.discogs.com/sell/item/1002'; return emptyCartDom();
  } };
  assert.equal(await verify({ context: { pages: () => [changed] },
    scope: input.scope, itemDigest: input.item_digest }), null);
});

test('empty-cart factory forwards cancellation before and during its DOM read', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now), controller = new AbortController(), calls = [];
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  const page = { url: () => { calls.push('url'); return 'https://www.discogs.com/sell/cart/'; },
    evaluate: async () => { calls.push('evaluate'); controller.abort(); return emptyCartDom(); } };
  assert.equal(await verify({ context: { pages: () => [page] },
    scope: input.scope, itemDigest: input.item_digest, signal: controller.signal }), null);
  assert.equal(calls.at(-1), 'evaluate');
  assert.ok(calls.slice(0, -1).every(call => call === 'url'));
  const after = [...calls];
  assert.equal(await verify({ context: { pages: () => { throw new Error('aborted read'); } },
    scope: input.scope, itemDigest: input.item_digest, signal: controller.signal }), null);
  assert.deepEqual(calls, after);
});

test('a second canonical cart appearing during DOM inspection invalidates empty-cart proof', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now), canonical = 'https://www.discogs.com/sell/cart/';
  const duplicate = { url: () => canonical, evaluate: async () => { throw new Error('ambiguous cart'); } };
  const pages = [];
  pages.push({ url: () => canonical, evaluate: async () => {
    pages.push(duplicate); return emptyCartDom();
  } });
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  assert.equal(await verify({ context: { pages: () => [...pages] },
    scope: input.scope, itemDigest: input.item_digest }), null);
});

test('replacing the inspected cart with another canonical cart invalidates its proof', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now), canonical = 'https://www.discogs.com/sell/cart/';
  const replacement = { url: () => canonical,
    evaluate: async () => { throw new Error('replacement was never inspected'); } };
  let pages;
  const original = { url: () => canonical,
    evaluate: async () => { pages = [replacement]; return emptyCartDom(); } };
  pages = [original];
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  assert.equal(await verify({ context: { pages: () => [...pages] },
    scope: input.scope, itemDigest: input.item_digest }), null);
});

test('other checkout or mutation-result cart pages invalidate an old empty tab', async () => {
  const { createCurrentEmptyCartVerifier } = await load();
  const now = stamp(), input = request(now), canonical = 'https://www.discogs.com/sell/cart/';
  const verify = createCurrentEmptyCartVerifier({ clock: () => now });
  for (const url of [canonical + '?add=1002&ev=', 'https://www.discogs.com/shop/cart/',
    'https://www.discogs.com/marketplace/checkout']) {
    let evaluations = 0;
    const cart = { url: () => canonical, evaluate: async () => { evaluations++; return emptyCartDom(); } };
    const other = { url: () => url };
    assert.equal(await verify({ context: { pages: () => [cart, other] },
      scope: input.scope, itemDigest: input.item_digest }), null);
    assert.equal(evaluations, 0);
    let pages = [cart];
    cart.evaluate = async () => { pages.push(other); return emptyCartDom(); };
    assert.equal(await verify({ context: { pages: () => pages },
      scope: input.scope, itemDigest: input.item_digest }), null);
  }
});

test('nonempty cart at the second grounded check stops before the durable click latch', async () => {
  const { createCartPrepareHandler, createCurrentEmptyCartVerifier, scopeDigest } = await load();
  await withStore(async ({ directory, stopPath }) => {
    const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
    const page = fixture.context.pages()[0];
    let clicks = 0, cartReads = 0;
    page.getByRole = (role, options) => ({
      count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
      nth: () => ({ isVisible: async () => true, isDisabled: async () => false,
        getAttribute: async () => '/sell/cart/?add=1002&ev=',
        click: async () => { clicks++; } }),
    });
    const cart = { url: () => 'https://www.discogs.com/sell/cart/',
      evaluate: async () => ++cartReads === 1 ? emptyCartDom() :
        emptyCartDom({ status: 'blocked', empty: false, item_count: 1 }) };
    const context = { pages: () => [page, cart] };
    await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
    const handle = createCartPrepareHandler({ context, bootId, directory, stopPath,
      clock: () => now, verifyEmptyCart: createCurrentEmptyCartVerifier({ clock: () => now }) });
    assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
    assert.equal(cartReads, 2);
    assert.equal(clicks, 0);
    assert.equal((await readdir(directory)).includes('cart-mutation-reconciliation-required'), false);
  });
});

for (const change of ['href_absolute', 'href_other_item', 'url', 'visibility', 'disabled']) {
  test(`post-fsync ${change} change blocks click and retains the mutation latch`, async () => {
    const { createCartPrepareHandler, scopeDigest, durableExclusiveMarker } = await load();
    await withStore(async ({ directory, stopPath }) => {
      const now = stamp(), bootId = randomUUID(), input = request(now), fixture = fixturePage();
      const page = fixture.context.pages()[0];
      let href = '/sell/cart/?add=1002&ev=', url = page.url(), visible = true, disabled = false;
      let clicks = 0, synchronized = false;
      page.url = () => url;
      page.getByRole = (role, options) => ({
        count: async () => role === 'link' && options.name === 'Add to Cart' ? 1 : 0,
        nth: () => ({ isVisible: async () => visible, isDisabled: async () => disabled,
          getAttribute: async () => href, click: async () => { clicks++; } }),
      });
      await ack(directory, bootId, input.scope, scopeDigest(input.scope, input.item_digest));
      const handle = createCartPrepareHandler({ context: fixture.context, bootId, directory,
        stopPath, clock: () => now,
        persistMarker: async (path, content) => {
          await durableExclusiveMarker(path, content);
          if (path.endsWith('cart-mutation-reconciliation-required')) {
            synchronized = true;
            if (change === 'href_absolute') href = 'https://www.discogs.com/sell/cart/?add=1002&ev=';
            if (change === 'href_other_item') href = '/sell/cart/?add=1003&ev=';
            if (change === 'url') url = 'https://www.discogs.com/sell/item/1003';
            if (change === 'visibility') visible = false;
            if (change === 'disabled') disabled = true;
          }
        },
        verifyEmptyCart: async () => ({ schema: 'discogs_empty_cart_verified_v1',
          source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0, document_age_ms: 0,
          scope_digest: scopeDigest(input.scope, input.item_digest),
          cart_url: 'https://www.discogs.com/sell/cart/', observed_at_ms: now }) });
      assert.equal((await handle(input)).code, 'cart_outcome_uncertain');
      assert.equal(synchronized, true);
      assert.equal(clicks, 0);
      assert.equal((await readFile(join(directory, 'cart-mutation-reconciliation-required'), 'utf8')),
        `${bootId}:${input.scope.idempotency_key}:${scopeDigest(input.scope, input.item_digest)}:attempting\n`);
    });
  });
}
