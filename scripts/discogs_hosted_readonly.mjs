/** Fixed observations inside an owner-approved persistent Firefox context.
 * Only observe_release may open a new tab at a canonical release URL. This module
 * never logs a session, mutates a cart, accepts an agreement, or exposes Playwright.
 */
import { createServer } from 'node:net';
import { chmod, unlink } from 'node:fs/promises';
import { collectCartDom, inspectCurrentCartDom } from './discogs_cart_inspector.mjs';
import { collectReleaseMarketplaceDom } from './discogs_release_snapshot.mjs';

const ID = /^[1-9][0-9]{0,15}$/u;
const MAX_REQUEST_BYTES = 512;
const MAX_RESPONSE_BYTES = 262_144;
const READ_TIMEOUT_MS = 5_000;
const NAVIGATION_TIMEOUT_MS = 12_000;
const READY_TIMEOUT_MS = 8_000;
const NAVIGATION_BUDGET_MS = 30_000;
const MIN_RELEASE_INTERVAL_MS = 15 * 60_000;
const MAX_RETRY_AFTER_MS = 24 * 60 * 60_000;

function stopped(code) {
  return { status: 'blocked', code, source: 'hosted_browser_read_only',
    stop_required: true, purchase_allowed: false };
}

function requestShape(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) return null;
  const keys = Reflect.ownKeys(request);
  if (keys.length !== 2 || !keys.includes('action')) return null;
  const action = Object.getOwnPropertyDescriptor(request, 'action')?.value;
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
    const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, input.id));
    if (page.url() !== originalUrl) return stopped('page_changed_during_read');
    if (!observation || observation.source !== 'discogs_rendered_release' ||
        observation.release_id !== Number(input.id) || observation.purchase_allowed !== false ||
        !exactUrl(input.action, input.id, observation.url)) return stopped('release_observation_unverified');
    return bounded({ status: observation.status, source: 'hosted_browser_read_only',
      observation, stop_required: true, purchase_allowed: false });
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

/** Local Unix socket only; the caller must keep it inside a private container. */
export async function listenHostedReadOnly(context, socketPath) {
  if (typeof socketPath !== 'string' || !/^\/tmp\/[a-z][a-z0-9-]{0,47}\.sock$/u.test(socketPath)) {
    throw new TypeError('unsupported_socket_path');
  }
  let busy = false;
  const navigateRelease = createReleaseNavigator(context);
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
      busy = true;
      const navigation = requestShape(request)?.action === 'observe_release';
      if (navigation) connection.setTimeout(NAVIGATION_BUDGET_MS);
      try { send(await (navigation ? navigateRelease(request, { signal: controller.signal }) :
        observeCurrentHostedPage(context, request))); }
      finally { busy = false; }
    });
  });
  const previousUmask = process.umask(0o077);
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(socketPath, () => { server.off('error', reject); resolve(); });
    });
    await chmod(socketPath, 0o600);
  } catch (error) {
    server.close();
    throw error;
  } finally { process.umask(previousUmask); }
  return async () => {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await unlink(socketPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
  };
}
