/** Dormant owner-scoped cart preparation service. Never places an order. */
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';
import { chmod, lstat, open, readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { collectListingDetailDom, collectListingReadinessDom } from './discogs_listing_detail_snapshot.mjs';
import { collectCartDom, inspectCurrentCart } from './discogs_cart_inspector.mjs';
import { prepareCurrentListingCart } from './discogs_cart_prepare.mjs';

// Future mount only: host /var/lib/sniper/cart-bridge -> container /cart-bridge.
// Production directory 2710 and socket 0660 are owned by browser UID 10001,
// group sniper 113. The web dispatcher needs that existing group; ack/used
// markers remain 0600 browser-owner only. No mount/group is configured here.
const SOCKET = '/cart-bridge/sniper-cart-prepare.sock';
const SHARED_GROUP = 113;
const ID = /^[1-9][0-9]{0,15}$/u;
const SELLER = /^[A-Za-z0-9_-]{1,64}$/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const MAX_REQUEST = 1024;
const MAX_RESPONSE = 4096;
const DEADLINE_MS = 30_000;
const CHALLENGE = /just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu;

function stopped(code) {
  return { schema: 'discogs_hosted_cart_prepare_v1', status: 'blocked', code,
    stop_required: true, purchase_allowed: false, order_submission: 'disabled' };
}

function keys(value, expected) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join('|') === [...expected].sort().join('|');
}

function canonical(scope) {
  return JSON.stringify(Object.fromEntries(Object.keys(scope).sort().map(key => [key, scope[key]])));
}

export function parseCartPrepareRequest(request, now) {
  if (!keys(request, ['schema', 'action', 'scope', 'item_digest', 'purchase_allowed']) ||
      request.schema !== 'discogs_hosted_cart_prepare_v1' ||
      request.action !== 'prepare_cart_quote_only' || request.purchase_allowed !== false ||
      typeof request.item_digest !== 'string' || !HASH.test(request.item_digest)) return null;
  const scope = request.scope;
  if (!keys(scope, ['action', 'release_id', 'listing_id', 'seller_handle', 'rules_revision',
    'idempotency_key', 'observation_fingerprint', 'issued_at', 'expires_at']) ||
      scope.action !== 'cart_quote_inspection' ||
      !Number.isSafeInteger(scope.release_id) || scope.release_id <= 0 ||
      !ID.test(scope.listing_id) || !Number.isSafeInteger(Number(scope.listing_id)) ||
      !SELLER.test(scope.seller_handle) ||
      !Number.isSafeInteger(scope.rules_revision) || scope.rules_revision < 0 ||
      !UUID.test(scope.idempotency_key) || !HASH.test(scope.observation_fingerprint) ||
      typeof scope.issued_at !== 'number' || typeof scope.expires_at !== 'number' ||
      !Number.isFinite(scope.issued_at) || !Number.isFinite(scope.expires_at) ||
      !(scope.issued_at <= now && now < scope.expires_at &&
        scope.expires_at - scope.issued_at > 0 && scope.expires_at - scope.issued_at <= 300)) return null;
  return Object.freeze({ scope: Object.freeze({ ...scope }), itemDigest: request.item_digest });
}

export function scopeDigest(scope, itemDigest) {
  if (typeof itemDigest !== 'string' || !HASH.test(itemDigest)) return null;
  return createHash('sha256').update(canonical({ scope, item_digest: itemDigest })).digest('hex');
}

export function visibleItemDigest(detail) {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail) ||
      detail.source !== 'discogs_rendered_listing' ||
      !Number.isSafeInteger(detail.release_id) || detail.release_id <= 0 ||
      typeof detail.listing_id !== 'string' || !ID.test(detail.listing_id) ||
      typeof detail.seller_handle !== 'string' || !SELLER.test(detail.seller_handle) ||
      typeof detail.item !== 'string' || !/^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u.test(detail.item) ||
      !['addable', 'already_in_cart', 'unknown'].includes(detail.cart_state) ||
      typeof detail.available !== 'boolean' ||
      typeof detail.seller_comments_supported !== 'boolean' ||
      (detail.seller_percent !== null &&
        (typeof detail.seller_percent !== 'number' || !Number.isFinite(detail.seller_percent) ||
         !Number.isSafeInteger(detail.seller_percent * 1_000_000))) ||
      (detail.feedback !== null && (!Number.isSafeInteger(detail.feedback) || detail.feedback < 0)) ||
      (detail.seller_comments !== null && typeof detail.seller_comments !== 'string') ||
      (detail.reported_shipping_text !== null && typeof detail.reported_shipping_text !== 'string') ||
      (detail.currency !== null && typeof detail.currency !== 'string')) return null;
  const projection = {
    release_id: detail.release_id, listing_id: detail.listing_id,
    seller_handle: detail.seller_handle, media: detail.media, sleeve: detail.sleeve,
    seller_percent_micro: detail.seller_percent === null ? null :
      detail.seller_percent * 1_000_000,
    feedback: detail.feedback, item: detail.item, currency: detail.currency,
    seller_comments: detail.seller_comments,
    seller_comments_supported: detail.seller_comments_supported,
    available: detail.available, cart_state: detail.cart_state,
    reported_shipping_text: detail.reported_shipping_text,
  };
  return createHash('sha256').update(canonical(projection)).digest('hex');
}

async function ownerFile(path, content = null) {
  const item = await lstat(path).catch(() => null);
  return Boolean(item?.isFile() && item.uid === process.getuid() &&
    (item.mode & 0o777) === 0o600 &&
    (content === null || await readFile(path, 'utf8') === content));
}

async function ownerDirectory(path) {
  const item = await lstat(path).catch(() => null);
  if (!item?.isDirectory() || item.uid !== process.getuid()) return false;
  return path === '/cart-bridge' ?
    item.gid === SHARED_GROUP && process.getgid() === SHARED_GROUP &&
      (item.mode & 0o7777) === 0o2710 :
    (item.mode & 0o7777) === 0o700;
}

async function syncParentDirectory(path) {
  const parent = await open(dirname(path), 'r');
  try { await parent.sync(); }
  finally { await parent.close(); }
}

/** Exclusive owner-only marker, durable before any possible browser effect. */
export async function durableExclusiveMarker(path, content, syncDirectory = syncParentDirectory) {
  const file = await open(path, 'wx', 0o600);
  try {
    await file.writeFile(content, 'utf8');
    await file.sync();
  } finally { await file.close(); }
  await syncDirectory(path);
}

function exactItemUrl(url, id) {
  return url === `https://www.discogs.com/sell/item/${id}` ||
    url === `https://www.discogs.com/shop/item/${id}`;
}

function stableItem(detail) {
  const { observed_at: _time, ...rest } = detail;
  return JSON.stringify(rest);
}

function itemMatches(detail, scope, url) {
  return detail?.schema === 'discogs_listing_detail_dom_v1' &&
    detail.source === 'discogs_rendered_listing' && detail.status === 'observed' &&
    detail.complete === true && detail.purchase_allowed === false &&
    detail.release_id === scope.release_id && detail.listing_id === scope.listing_id &&
    detail.seller_handle === scope.seller_handle && detail.available === true &&
    detail.cart_state === 'addable' && detail.url === url;
}

function restrictedPage(page, scope, itemDigest, initial, signal, deadline, clock,
  state, beforeClick) {
  const check = () => {
    if (signal?.aborted || clock() >= deadline ||
        !(scope.issued_at <= clock() / 1000 && clock() / 1000 < scope.expires_at)) {
      throw new Error('cancelled');
    }
  };
  const checked = async operation => {
    check();
    const value = await operation();
    check();
    return value;
  };
  const wrapLocator = (locator, addLink = false) => ({
    count: () => checked(() => locator.count()),
    nth: index => {
      check();
      const item = locator.nth(index);
      return {
        isVisible: () => checked(() => item.isVisible()),
        isDisabled: () => checked(() => item.isDisabled()),
        getAttribute: name => checked(() => item.getAttribute(name)),
        click: async options => {
          check();
          if (!addLink || !exactItemUrl(page.url(), scope.listing_id) ||
              page.url() !== initial.url) throw new Error('unsupported_click');
          const title = await checked(() => page.title());
          if (CHALLENGE.test(title) || page.url() !== initial.url) throw new Error('access_restricted');
          const current = await checked(() => page.evaluate(collectListingDetailDom, {
            release_id: String(scope.release_id), listing_id: scope.listing_id,
          }));
          if (!itemMatches(current, scope, initial.url) ||
              visibleItemDigest(current) !== itemDigest ||
              stableItem(current) !== stableItem(initial.detail)) {
            throw new Error('listing_changed_before_add');
          }
          if (!options || typeof options.timeout !== 'number' ||
              !Number.isFinite(options.timeout)) throw new Error('unbounded_click');
          // Persist a helper-wide uncertainty fence before issuing the click.
          // A timed-out Playwright auto-wait may still dispatch after this call
          // returns; the fence remains until separate human reconciliation.
          await beforeClick();
          check();
          const remaining = Math.min(deadline - clock(), scope.expires_at * 1000 - clock());
          if (remaining <= 0) throw new Error('click_scope_expired');
          state.clickIssued = true;
          const result = await item.click({ timeout: Math.max(1, Math.min(options.timeout, remaining)) });
          check();
          return result;
        },
      };
    },
  });
  // The helper is the only code allowed to construct this page port. The
  // original page remains private and no arbitrary locator or evaluation leaks.
  return Object.freeze({
    url: () => { check(); return page.url(); },
    getByRole: (role, options) => {
      check();
      if (role === 'link' && options?.name === 'Add to Cart' && options.exact === true) {
        return wrapLocator(page.getByRole(role, options), true);
      }
      if (role === 'button' && options?.name === 'In Cart' && options.exact === true) {
        return wrapLocator(page.getByRole(role, options));
      }
      throw new Error('unsupported_control');
    },
    evaluate: (fn, id) => {
      check();
      if (fn !== collectCartDom || id !== scope.listing_id) throw new Error('unsupported_collector');
      return checked(() => page.evaluate(fn, id));
    },
    goto: (url, options) => {
      check();
      if (!/^https:\/\/www\.discogs\.com\/sell\/cart\/$/u.test(url)) {
        throw new Error('unsupported_navigation');
      }
      if (!state.clickIssued) throw new Error('add_not_issued');
      return checked(() => page.goto(url, options));
    },
  });
}

/** No grants are issued here. An owner-approved, boot-bound ack must exist. */
export function createCartPrepareHandler({ context, bootId, directory, stopPath,
  verifyEmptyCart = null, clock = () => Date.now(), deadlineMs = DEADLINE_MS,
  persistMarker = durableExclusiveMarker } = {}) {
  if (typeof bootId !== 'string' || !UUID.test(bootId) ||
      typeof directory !== 'string' || typeof stopPath !== 'string' ||
      !Number.isSafeInteger(deadlineMs) || deadlineMs < 1 || deadlineMs > DEADLINE_MS ||
      typeof persistMarker !== 'function' ||
      directory === '/cart-bridge' && persistMarker !== durableExclusiveMarker) {
    throw new TypeError('cart_helper_configuration_invalid');
  }
  let busy = false;
  return async (request, signal) => {
    const parsed = parseCartPrepareRequest(request, clock() / 1000);
    if (!parsed) return stopped('scope_unverified');
    const { scope, itemDigest } = parsed;
    if (busy) return stopped('cart_helper_busy');
    busy = true;
    const deadline = clock() + deadlineMs;
    const live = () => !signal?.aborted && clock() < deadline &&
      scope.issued_at <= clock() / 1000 && clock() / 1000 < scope.expires_at;
    try {
      if (!await ownerDirectory(directory) ||
          !await ownerFile(stopPath, 'access_restricted\n')) return stopped('access_stop_unverified');
      const digest = scopeDigest(scope, itemDigest);
      const mutationLatch = join(directory, 'cart-mutation-reconciliation-required');
      if (await lstat(mutationLatch).catch(() => null)) {
        return stopped('cart_mutation_reconciliation_required');
      }
      const ack = join(directory, `operator-cart-prepare-${scope.idempotency_key}-${digest}-ack`);
      const used = join(directory, `operator-cart-prepare-${scope.idempotency_key}-used`);
      const expected = `operator_confirmed_cart_quote_inspection:${bootId}:` +
        `${scope.idempotency_key}:${digest}\n`;
      const ackInfo = await lstat(ack).catch(() => null);
      if (!ackInfo?.isFile() || ackInfo.uid !== process.getuid() ||
          (ackInfo.mode & 0o777) !== 0o600 ||
          clock() + 1000 < ackInfo.mtimeMs || clock() - ackInfo.mtimeMs > 300_000 ||
          await readFile(ack, 'utf8') !== expected || !live()) {
        return stopped('operator_acknowledgement_required');
      }
      // Exclusive durable marker is written before browser access or a possible click.
      await persistMarker(used, `${bootId}:${scope.idempotency_key}:${digest}\n`);
      if (!live() || !context || typeof context.pages !== 'function') {
        return stopped('browser_context_unavailable');
      }
      const pages = context.pages();
      if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
      const matches = pages.filter(page => page && typeof page.url === 'function' &&
        exactItemUrl(page.url(), scope.listing_id));
      if (matches.length !== 1) return stopped('exact_current_page_not_unique');
      const page = matches[0], url = page.url();
      if (!live() || typeof page.title !== 'function' || typeof page.evaluate !== 'function') {
        return stopped('listing_unavailable');
      }
      if (CHALLENGE.test(await page.title()) || !live() || page.url() !== url) {
        return stopped('access_restricted');
      }
      const readiness = await page.evaluate(collectListingReadinessDom);
      if (!live() || readiness?.password_input_present || readiness?.challenge_marker_present) {
        return stopped('access_restricted');
      }
      const detail = await page.evaluate(collectListingDetailDom, {
        release_id: String(scope.release_id), listing_id: scope.listing_id,
      });
      if (!live() || page.url() !== url || !itemMatches(detail, scope, url) ||
          visibleItemDigest(detail) !== itemDigest) {
        return stopped('listing_identity_or_state_unverified');
      }
      // The current cart inspector observes an expected row but cannot prove
      // an empty pre-add cart. Without a separately reviewed producer, stop.
      if (typeof verifyEmptyCart !== 'function') return stopped('pre_cart_state_unverified');
      const prior = await verifyEmptyCart({ context, scope });
      if (!live() || prior?.schema !== 'discogs_empty_cart_verified_v1' ||
          prior.empty !== true || prior.item_count !== 0 ||
          prior.seller_count !== 0 || prior.source !== 'visible_cart_dom' ||
          prior.scope_digest !== digest ||
          !['https://www.discogs.com/sell/cart/',
            'https://www.discogs.com/shop/cart/'].includes(prior.cart_url) ||
          typeof prior.observed_at_ms !== 'number' ||
          prior.observed_at_ms > clock() || clock() - prior.observed_at_ms > 5_000) {
        return stopped('pre_cart_state_unverified');
      }
      const state = { clickIssued: false };
      const beforeClick = async () => {
        if (!live()) throw new Error('click_scope_expired');
        await persistMarker(mutationLatch,
          `${bootId}:${scope.idempotency_key}:${digest}:attempting\n`);
        if (!live()) throw new Error('click_scope_expired');
      };
      const port = restrictedPage(page, scope, itemDigest, { detail, url }, signal, deadline, clock,
        state, beforeClick);
      const prepared = await prepareCurrentListingCart(port, {
        expectedListingId: scope.listing_id, allowAddToCart: true,
        timeoutMs: Math.max(5000, Math.min(30_000, deadline - clock())),
      });
      if (!live() || prepared?.code !== 'cart_observed_after_add' ||
          prepared.preparation_state !== 'cart_observed' ||
          !prepared.cart_inspection?.observations?.includes('expected_listing_link_observed') ||
          !prepared.cart_inspection?.observations?.includes('single_cart_row_observed')) {
        return stopped('cart_outcome_uncertain');
      }
      const summary = await inspectCurrentCart({
        url: () => {
          if (!live()) throw new Error('cancelled');
          return page.url();
        },
        locator: selector => {
          if (!live()) throw new Error('cancelled');
          if (selector !== 'body') throw new Error('unsupported_locator');
          return { innerText: async options => {
            if (!live()) throw new Error('cancelled');
            return page.locator(selector).innerText(options);
          } };
        },
      }, { expectedListingId: scope.listing_id });
      if (!live() || !summary?.observations?.includes('single_item_single_seller_summary_observed') ||
          page.url() !== 'https://www.discogs.com/sell/cart/') {
        return stopped('cart_outcome_uncertain');
      }
      return { schema: 'discogs_hosted_cart_prepare_v1', status: 'observed',
        code: 'cart_observed_after_add', scope, item_digest: itemDigest, stop_required: true,
        purchase_allowed: false, order_submission: 'disabled',
        cart: { status: 'observed', item_row_count: 1,
          listing_id: scope.listing_id, single_item_single_seller_summary_observed: true,
          identity_provenance: 'verified_pre_click_item_plus_post_add_cart_link',
          unrelated_changes: false } };
    } catch {
      return stopped('cart_outcome_uncertain');
    } finally { busy = false; }
  };
}

/** Explicit opt-in only; never called from the existing browser runtime. */
export async function listenHostedCartHelper(context, {
  enabled = false, socketPath = SOCKET, bootId, directory, stopPath, verifyEmptyCart = null,
} = {}) {
  if (enabled !== true) throw new TypeError('cart_helper_disabled');
  if (socketPath !== SOCKET || directory !== '/cart-bridge' ||
      !await ownerDirectory(directory)) throw new TypeError('owner_only_cart_socket_required');
  const handle = createCartPrepareHandler({ context, bootId, directory, stopPath, verifyEmptyCart });
  const server = createServer(connection => {
    let done = false, input = Buffer.alloc(0);
    const controller = new AbortController();
    connection.setTimeout(DEADLINE_MS, () => { controller.abort(); connection.destroy(); });
    connection.on('close', () => controller.abort());
    connection.on('error', () => controller.abort());
    connection.on('data', async chunk => {
      if (done) return;
      input = Buffer.concat([input, chunk]);
      if (input.length > MAX_REQUEST || input.includes(0) || input.at(-1) !== 10) {
        if (input.length <= MAX_REQUEST && input.at(-1) !== 10 && !input.includes(0)) return;
        done = true; connection.end(`${JSON.stringify(stopped('invalid_request'))}\n`); return;
      }
      done = true;
      let request;
      try { request = JSON.parse(input.subarray(0, -1).toString('utf8')); }
      catch { connection.end(`${JSON.stringify(stopped('invalid_request'))}\n`); return; }
      const result = await handle(request, controller.signal);
      const output = JSON.stringify(result);
      connection.end(`${Buffer.byteLength(output) <= MAX_RESPONSE ? output :
        JSON.stringify(stopped('response_too_large'))}\n`);
    });
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, resolve);
  });
  await chmod(socketPath, 0o660);
  const bound = await lstat(socketPath);
  if (!bound.isSocket() || bound.uid !== process.getuid() ||
      bound.gid !== SHARED_GROUP || (bound.mode & 0o777) !== 0o660) {
    await new Promise(resolve => server.close(resolve));
    throw new TypeError('private_cart_socket_required');
  }
  return server;
}
