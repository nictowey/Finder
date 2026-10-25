/** Fixed observations inside an owner-approved persistent Firefox context.
 * Only fixed release and item reads may open a new tab at canonical URLs. This module
 * never logs a session, mutates a cart, accepts an agreement, or exposes Playwright.
 */
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { chmod, readFile, stat, unlink, writeFile } from 'node:fs/promises';
import { collectCartDom, inspectCurrentCartDom } from './discogs_cart_inspector.mjs';
import { collectReleaseMarketplaceDom } from './discogs_release_snapshot.mjs';
import { collectListingDetailDom, collectListingReadinessDom } from './discogs_listing_detail_snapshot.mjs';

const ID = /^[1-9][0-9]{0,15}$/u;
const MAX_REQUEST_BYTES = 512;
const MAX_RESPONSE_BYTES = 262_144;
const READ_TIMEOUT_MS = 5_000;
const NAVIGATION_TIMEOUT_MS = 12_000;
const READY_TIMEOUT_MS = 8_000;
const NAVIGATION_BUDGET_MS = 30_000;
const MIN_RELEASE_INTERVAL_MS = 15 * 60_000;
const MAX_RETRY_AFTER_MS = 24 * 60 * 60_000;
const SHARED_SOCKET = '/bridge/sniper-read-only.sock';
const SHARED_GROUP = 113;
const OPERATOR_RELEASE = '4204639';

export function privateBridgeDirectory(directory, uid = process.getuid()) {
  return Boolean(directory?.isDirectory?.() && directory.uid === uid &&
    directory.gid === SHARED_GROUP && (directory.mode & 0o7777) === 0o2710);
}

function stopped(code) {
  return { status: 'blocked', code, source: 'hosted_browser_read_only',
    stop_required: true, purchase_allowed: false };
}

function requestShape(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) return null;
  const keys = Reflect.ownKeys(request);
  if (!keys.includes('action')) return null;
  const action = Object.getOwnPropertyDescriptor(request, 'action')?.value;
  if (['current_listing', 'observe_listing'].includes(action)) {
    if (keys.length !== 3 || !keys.includes('release_id') || !keys.includes('listing_id')) return null;
    const release = Object.getOwnPropertyDescriptor(request, 'release_id')?.value;
    const listing = Object.getOwnPropertyDescriptor(request, 'listing_id')?.value;
    return typeof release === 'string' && ID.test(release) && Number.isSafeInteger(Number(release)) &&
      typeof listing === 'string' && ID.test(listing) && Number.isSafeInteger(Number(listing)) ?
      { action, id: listing, release } : null;
  }
  if (keys.length !== 2) return null;
  const field = ['current_release', 'observe_release'].includes(action) ? 'release_id' :
    action === 'current_cart' ? 'listing_id' : null;
  if (!field || !keys.includes(field)) return null;
  const id = Object.getOwnPropertyDescriptor(request, field)?.value;
  return typeof id === 'string' && ID.test(id) && Number.isSafeInteger(Number(id)) ? { action, id } : null;
}

function exactUrl(action, id, url) {
  if (typeof url !== 'string' || url.length > 100 || /[\s\x00-\x1f\x7f]/u.test(url)) return false;
  if (['current_release', 'observe_release'].includes(action)) return url === `https://www.discogs.com/sell/release/${id}` ||
    url === `https://www.discogs.com/sell/release/${id}/`;
  if (['current_listing', 'observe_listing'].includes(action)) return url === `https://www.discogs.com/sell/item/${id}` ||
    url === `https://www.discogs.com/shop/item/${id}`;
  return /^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/?$/u.test(url);
}

function bounded(result) {
  try {
    return Buffer.byteLength(JSON.stringify(result), 'utf8') <= MAX_RESPONSE_BYTES ? result : stopped('observation_too_large');
  } catch { return stopped('observation_unserializable'); }
}

async function beforeDeadline(operation) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), READ_TIMEOUT_MS); }),
    ]);
  } finally { clearTimeout(timer); }
}

/** Observe an already-open exact page. No page goto, click, cookie or storage APIs. */
export async function observeCurrentHostedPage(context, request) {
  const input = requestShape(request);
  if (!input) return stopped('unsupported_read_request');
  if (!context || typeof context.pages !== 'function') return stopped('browser_context_unavailable');
  try {
    const pages = context.pages();
    if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
    const matching = pages.filter(page => page && typeof page.url === 'function' &&
      typeof page.evaluate === 'function' && exactUrl(input.action, input.id, page.url()));
    if (matching.length !== 1) return stopped('exact_current_page_not_unique');
    const page = matching[0], originalUrl = page.url();
    if (input.action === 'current_cart') {
      const restrictedPage = Object.freeze({
        url: () => page.url(),
        evaluate: (fn, id) => {
          if (fn !== collectCartDom || id !== input.id) throw new TypeError('unsupported_collector');
          return page.evaluate(fn, id);
        },
      });
      const cart = await beforeDeadline(() => inspectCurrentCartDom(restrictedPage, { expectedListingId: input.id }));
      if (page.url() !== originalUrl) return stopped('page_changed_during_read');
      return bounded({ ...cart, source: 'hosted_browser_read_only', stop_required: true, purchase_allowed: false });
    }
    if (input.action === 'current_listing') {
      const observation = await beforeDeadline(() => page.evaluate(collectListingDetailDom,
        { release_id: input.release, listing_id: input.id }));
      if (page.url() !== originalUrl || !exactUrl(input.action, input.id, observation?.url)) {
        return stopped('page_changed_during_read');
      }
      if (observation?.source !== 'discogs_rendered_listing' ||
          observation.release_id !== Number(input.release) || observation.listing_id !== input.id ||
          observation.purchase_allowed !== false) return stopped('listing_observation_unverified');
      if (observation.status !== 'observed' || observation.complete !== true) {
        return bounded({ ...stopped(observation.error_code || 'incomplete_listing_page'), status: 'incomplete' });
      }
      return bounded({ status: 'observed', source: 'hosted_browser_read_only', observation,
        stop_required: true, purchase_allowed: false });
    }
    const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, input.id));
    if (page.url() !== originalUrl) return stopped('page_changed_during_read');
    if (!observation || observation.source !== 'discogs_rendered_release' ||
        observation.release_id !== Number(input.id) || observation.purchase_allowed !== false ||
        !exactUrl(input.action, input.id, observation.url)) return stopped('release_observation_unverified');
    return bounded({ status: observation.status, source: 'hosted_browser_read_only',
      observation, stop_required: true, purchase_allowed: false });
  } catch { return stopped('hosted_read_failed'); }
}

/** An operator-confirmed exception for the already-open Rocky marketplace only.
 * The socket's ordinary access stop remains set before and after this call.
 */
async function observeOperatorCurrentRelease(context) {
  if (!context || typeof context.pages !== 'function') return stopped('browser_context_unavailable');
  try {
    const pages = context.pages();
    if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
    const matches = pages.filter(page => page && typeof page.url === 'function' &&
      typeof page.title === 'function' && typeof page.evaluate === 'function' &&
      exactUrl('current_release', OPERATOR_RELEASE, page.url()));
    if (matches.length !== 1) return stopped('exact_current_page_not_unique');
    const page = matches[0], url = page.url();
    const title = await beforeDeadline(() => page.title());
    if (page.url() !== url) return stopped('page_changed_during_read');
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu.test(title)) {
      return stopped('access_restricted');
    }
    const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, OPERATOR_RELEASE));
    if (page.url() !== url || !exactUrl('current_release', OPERATOR_RELEASE, observation?.url)) {
      return stopped('page_changed_during_read');
    }
    if (observation?.source !== 'discogs_rendered_release' ||
        observation.release_id !== Number(OPERATOR_RELEASE) || observation.purchase_allowed !== false ||
        observation.status !== 'observed' || observation.complete !== true || observation.has_next !== false) {
      return stopped(observation?.error_code || 'release_observation_unverified');
    }
    return bounded({ status: 'observed', source: 'hosted_browser_read_only', observation,
      stop_required: true, purchase_allowed: false });
  } catch { return stopped('hosted_read_failed'); }
}

function retryAfterMillis(value) {
  if (typeof value !== 'string' || value.length > 128) return 0;
  if (/^[0-9]{1,5}$/u.test(value)) return Math.min(Number(value) * 1000, MAX_RETRY_AFTER_MS);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.min(Math.max(date - Date.now(), 0), MAX_RETRY_AFTER_MS) : 0;
}

/** One canonical release load, with a private tab closed after its observation.
 * This is an on-demand primitive, not a scheduler or a permission to poll.
 */
export function createReleaseNavigator(context, { now = () => Date.now() } = {}) {
  let nextAllowedAt = 0, accessStopped = false, active = false;
  return async (request, { signal } = {}) => {
    const input = requestShape(request);
    if (!input || input.action !== 'observe_release') return stopped('unsupported_read_request');
    if (accessStopped) return stopped('access_restricted');
    if (active) return stopped('read_in_progress');
    const currentTime = now();
    if (currentTime < nextAllowedAt) return { ...stopped('release_cooldown'),
      cooldown_seconds: Math.ceil((nextAllowedAt - currentTime) / 1000) };
    if (!context || typeof context.newPage !== 'function') return stopped('browser_context_unavailable');
    active = true;
    nextAllowedAt = currentTime + MIN_RELEASE_INTERVAL_MS;
    let page;
    const cancel = () => { if (page) void page.close().catch(() => {}); };
    signal?.addEventListener('abort', cancel, { once: true });
    try {
      if (signal?.aborted) return stopped('read_cancelled');
      page = await context.newPage();
      if (signal?.aborted) return stopped('read_cancelled');
      const url = `https://www.discogs.com/sell/release/${input.id}`;
      const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
      if (signal?.aborted) return stopped('read_cancelled');
      if (!response || typeof response.status !== 'function') return stopped('http_response_unavailable');
      const status = response.status();
      if ([429, 503].includes(status)) {
        const retryAfter = typeof response.headerValue === 'function' ?
          await Promise.resolve(response.headerValue('retry-after')).catch(() => null) : null;
        nextAllowedAt = Math.max(nextAllowedAt, now() + Math.max(MIN_RELEASE_INTERVAL_MS,
          retryAfterMillis(retryAfter)));
        return { ...stopped(status === 429 ? 'rate_limited' : 'service_unavailable'),
          cooldown_seconds: Math.ceil((nextAllowedAt - now()) / 1000) };
      }
      if (status === 401 || status === 403) {
        accessStopped = true;
        return stopped(status === 401 ? 'auth_required' : 'access_restricted');
      }
      if (status !== 200) return stopped('unexpected_http_status');
      const landed = page.url();
      if (!exactUrl(input.action, input.id, landed)) {
        if (/\/(?:login|signin|cdn-cgi)(?:\/|\?|$)/iu.test(landed)) accessStopped = true;
        return stopped(accessStopped ? 'access_restricted' : 'unexpected_redirect');
      }
      const title = await page.title();
      if (/just a moment|attention required|security check|captcha|cloudflare/iu.test(title)) {
        accessStopped = true;
        return stopped('access_restricted');
      }
      try {
        await page.waitForSelector('#pjax_container table.mpitems',
          { state: 'attached', timeout: READY_TIMEOUT_MS });
        await page.waitForSelector('nav.pagination .pagination_total',
          { state: 'attached', timeout: 3_000 });
      } catch { return stopped('marketplace_not_ready'); }
      if (signal?.aborted) return stopped('read_cancelled');
      const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, input.id));
      if (signal?.aborted) return stopped('read_cancelled');
      if (page.url() !== landed || !exactUrl(input.action, input.id, observation?.url)) {
        return stopped('page_changed_during_read');
      }
      if (observation.source !== 'discogs_rendered_release' ||
          observation.release_id !== Number(input.id) || observation.purchase_allowed !== false) {
        return stopped('release_observation_unverified');
      }
      if (observation.status !== 'observed' || observation.complete !== true ||
          observation.has_next !== false) {
        return bounded({ ...stopped(observation.error_code || 'incomplete_release_page'),
          status: 'incomplete' });
      }
      return bounded({ status: 'observed', source: 'hosted_browser_read_only', observation,
        stop_required: true, purchase_allowed: false });
    } catch { return stopped(signal?.aborted ? 'read_cancelled' : 'navigation_or_read_failed'); }
    finally {
      signal?.removeEventListener('abort', cancel);
      if (page) await page.close().catch(() => {});
      active = false;
    }
  };
}

/** One fixed item-page load. No recurrence, cart controls, or arbitrary URL input. */
export function createListingNavigator(context, { now = () => Date.now() } = {}) {
  let nextAllowedAt = 0, accessStopped = false, active = false;
  return async (request, { signal } = {}) => {
    const input = requestShape(request);
    if (!input || input.action !== 'observe_listing') return stopped('unsupported_read_request');
    if (accessStopped) return stopped('access_restricted');
    if (active) return stopped('read_in_progress');
    const currentTime = now();
    if (currentTime < nextAllowedAt) return { ...stopped('listing_cooldown'),
      cooldown_seconds: Math.ceil((nextAllowedAt - currentTime) / 1000) };
    if (!context || typeof context.newPage !== 'function') return stopped('browser_context_unavailable');
    active = true;
    nextAllowedAt = currentTime + MIN_RELEASE_INTERVAL_MS;
    let page;
    const cancel = () => { if (page) void page.close().catch(() => {}); };
    signal?.addEventListener('abort', cancel, { once: true });
    try {
      if (signal?.aborted) return stopped('read_cancelled');
      page = await context.newPage();
      if (signal?.aborted) return stopped('read_cancelled');
      const response = await page.goto(`https://www.discogs.com/sell/item/${input.id}`,
        { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
      if (signal?.aborted) return stopped('read_cancelled');
      if (!response || typeof response.status !== 'function') return stopped('http_response_unavailable');
      const status = response.status();
      if ([429, 503].includes(status)) {
        const retryAfter = typeof response.headerValue === 'function' ?
          await Promise.resolve(response.headerValue('retry-after')).catch(() => null) : null;
        nextAllowedAt = Math.max(nextAllowedAt, now() + Math.max(MIN_RELEASE_INTERVAL_MS,
          retryAfterMillis(retryAfter)));
        return { ...stopped(status === 429 ? 'rate_limited' : 'service_unavailable'),
          cooldown_seconds: Math.ceil((nextAllowedAt - now()) / 1000) };
      }
      if (status === 401 || status === 403) {
        accessStopped = true;
        return stopped(status === 401 ? 'auth_required' : 'access_restricted');
      }
      if (status !== 200) return stopped('unexpected_http_status');
      const landed = page.url();
      if (!exactUrl(input.action, input.id, landed)) {
        if (/\/(?:login|signin|cdn-cgi)(?:\/|\?|$)/iu.test(landed)) accessStopped = true;
        return stopped(accessStopped ? 'access_restricted' : 'unexpected_redirect');
      }
      const title = await page.title();
      if (/just a moment|attention required|security check|captcha|cloudflare/iu.test(title)) {
        accessStopped = true;
        return stopped('access_restricted');
      }
      try {
        await page.waitForSelector('main [data-testid="item-details"]',
          { state: 'attached', timeout: READY_TIMEOUT_MS });
        await page.waitForSelector('main span.hifi-grade-chip-display',
          { state: 'attached', timeout: READY_TIMEOUT_MS });
      } catch {
        const diagnostic = await beforeDeadline(() => page.evaluate(collectListingReadinessDom))
          .catch(() => null);
        if (diagnostic?.password_input_present || diagnostic?.challenge_marker_present) {
          accessStopped = true;
          return stopped('access_restricted');
        }
        return bounded({ ...stopped('listing_not_ready'), diagnostic });
      }
      if (signal?.aborted) return stopped('read_cancelled');
      const observation = await beforeDeadline(() => page.evaluate(collectListingDetailDom,
        { release_id: input.release, listing_id: input.id }));
      if (signal?.aborted) return stopped('read_cancelled');
      if (page.url() !== landed || !exactUrl(input.action, input.id, observation?.url)) {
        return stopped('page_changed_during_read');
      }
      if (observation?.source !== 'discogs_rendered_listing' ||
          observation.release_id !== Number(input.release) || observation.listing_id !== input.id ||
          observation.purchase_allowed !== false) return stopped('listing_observation_unverified');
      if (observation.status !== 'observed' || observation.complete !== true) {
        return bounded({ ...stopped(observation.error_code || 'incomplete_listing_page'), status: 'incomplete' });
      }
      return bounded({ status: 'observed', source: 'hosted_browser_read_only', observation,
        stop_required: true, purchase_allowed: false });
    } catch { return stopped(signal?.aborted ? 'read_cancelled' : 'navigation_or_read_failed'); }
    finally {
      signal?.removeEventListener('abort', cancel);
      if (page) await page.close().catch(() => {});
      active = false;
    }
  };
}

/** Local Unix socket only; the caller must keep it inside a private container. */
export async function listenHostedReadOnly(context, socketPath, {
  durableStopPath = null, operatorBootPath = null, operatorAckPath = null, operatorUsedPath = null,
} = {}) {
  const shared = socketPath === SHARED_SOCKET;
  if (typeof socketPath !== 'string' || (!shared && !/^\/tmp\/[a-z][a-z0-9-]{0,47}\.sock$/u.test(socketPath))) {
    throw new TypeError('unsupported_socket_path');
  }
  if (shared) {
    const directory = await stat('/bridge');
    if (!privateBridgeDirectory(directory)) {
      throw new TypeError('private_bridge_directory_required');
    }
  }
  if ([durableStopPath, operatorBootPath, operatorAckPath, operatorUsedPath].some(Boolean) &&
      (!durableStopPath || !operatorBootPath || !operatorAckPath || !operatorUsedPath)) {
    throw new TypeError('incomplete_operator_recovery_configuration');
  }
  if (durableStopPath) {
    // The old process is stopped. A missing marker on first upgrade must fail closed.
    await writeFile(durableStopPath, 'access_restricted\n', { flag: 'wx', mode: 0o600 })
      .catch(error => { if (error.code !== 'EEXIST') throw error; });
    const marker = await stat(durableStopPath);
    if (!marker.isFile() || marker.uid !== process.getuid() || (marker.mode & 0o777) !== 0o600 ||
        await readFile(durableStopPath, 'utf8') !== 'access_restricted\n') {
      throw new TypeError('durable_access_stop_required');
    }
  }
  const bootId = durableStopPath ? randomUUID() : null;
  if (operatorBootPath) await writeFile(operatorBootPath, `${bootId}\n`, { mode: 0o600 });
  let busy = false;
  const navigateRelease = createReleaseNavigator(context);
  const navigateListing = createListingNavigator(context);
  let accessStopped = Boolean(durableStopPath);
  let restrictedUntil = 0;
  const server = createServer(connection => {
    let input = Buffer.alloc(0), done = false;
    const controller = new AbortController();
    connection.setTimeout(READ_TIMEOUT_MS);
    const send = result => {
      if (done) return;
      done = true;
      connection.end(JSON.stringify(bounded(result)) + '\n');
    };
    connection.on('timeout', () => { controller.abort(); send(stopped('request_timed_out')); });
    connection.on('close', () => { done = true; controller.abort(); });
    connection.on('error', () => { done = true; controller.abort(); });
    connection.on('data', async chunk => {
      if (done) return;
      if (input.length + chunk.length > MAX_REQUEST_BYTES) return send(stopped('request_too_large'));
      input = Buffer.concat([input, chunk]);
      const newline = input.indexOf(10);
      if (newline < 0) return;
      if (newline !== input.length - 1 || busy) return send(stopped(busy ? 'read_in_progress' : 'unsupported_read_request'));
      let request;
      try { request = JSON.parse(input.subarray(0, newline).toString('utf8')); }
      catch { return send(stopped('unsupported_read_request')); }
      if (request?.action === 'operator_current_release') {
        if (!durableStopPath || !accessStopped || busy ||
            Reflect.ownKeys(request).length !== 2 || request.release_id !== OPERATOR_RELEASE) {
          return send(stopped('unsupported_operator_recovery'));
        }
        busy = true;
        try {
          const ack = await stat(operatorAckPath).catch(() => null);
          if (!ack?.isFile() || ack.uid !== process.getuid() ||
              (ack.mode & 0o777) !== 0o600 ||
              await readFile(operatorAckPath, 'utf8') !==
                `operator_confirmed_current_release:${OPERATOR_RELEASE}:${bootId}\n`) {
            return send(stopped('operator_acknowledgement_required'));
          }
          // Exclusive creation consumes the sole grant before any browser inspection.
          await writeFile(operatorUsedPath, 'consumed\n', { flag: 'wx', mode: 0o600 });
          return send(await observeOperatorCurrentRelease(context));
        } catch {
          return send(stopped('operator_recovery_unavailable'));
        } finally { busy = false; }
      }
      if (accessStopped) return send(stopped('access_restricted'));
      if (Date.now() < restrictedUntil) return send({ ...stopped('rate_limited'),
        cooldown_seconds: Math.ceil((restrictedUntil - Date.now()) / 1000) });
      busy = true;
      const action = requestShape(request)?.action;
      const navigation = ['observe_release', 'observe_listing'].includes(action);
      if (navigation) connection.setTimeout(NAVIGATION_BUDGET_MS);
      try {
        const result = await (action === 'observe_release' ? navigateRelease(request, { signal: controller.signal }) :
          action === 'observe_listing' ? navigateListing(request, { signal: controller.signal }) :
            observeCurrentHostedPage(context, request));
        if (['auth_required', 'access_restricted'].includes(result?.code)) accessStopped = true;
        if (['rate_limited', 'service_unavailable'].includes(result?.code) &&
            Number.isSafeInteger(result.cooldown_seconds) && result.cooldown_seconds > 0) {
          restrictedUntil = Math.max(restrictedUntil, Date.now() + result.cooldown_seconds * 1000);
        }
        send(result);
      }
      finally { busy = false; }
    });
  });
  const previousUmask = process.umask(0o077);
  let bound = false;
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(socketPath, () => { bound = true; server.off('error', reject); resolve(); });
    });
    if (shared) {
      const socket = await stat(socketPath);
      if (!socket.isSocket() || socket.uid !== process.getuid() || socket.gid !== SHARED_GROUP) {
        throw new TypeError('private_bridge_socket_required');
      }
    }
    await chmod(socketPath, shared ? 0o660 : 0o600);
  } catch (error) {
    server.close();
    if (bound) await unlink(socketPath).catch(unlinkError => {
      if (unlinkError.code !== 'ENOENT') throw unlinkError;
    });
    throw error;
  } finally { process.umask(previousUmask); }
  return async () => {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await unlink(socketPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
  };
}
