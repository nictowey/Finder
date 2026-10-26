/** Fixed observations inside an owner-approved persistent Firefox context.
 * Only fixed release and item reads may open a new tab at canonical URLs. This module
 * never logs a session, mutates a cart, accepts an agreement, or exposes Playwright.
 */
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { chmod, lstat, readFile, rename, stat, unlink, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
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
const RELEASE_PAGINATION_BUDGET_MS = 90_000;
const MIN_RELEASE_INTERVAL_MS = 15 * 60_000;
const MAX_RETRY_AFTER_MS = 24 * 60 * 60_000;
const SHARED_SOCKET = '/bridge/sniper-read-only.sock';
const SHARED_GROUP = 113;
const OPERATOR_RELEASE = '4204639';
const OPERATOR_CART_LISTING = '3526572746';
const CART_GRANT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const MONITOR_TEST_RELEASES = new Set(['28720177', '5520534', '13053315']);
const MONITOR_TRIAL_TTL_MS = 60 * 60_000;
const MONITOR_ACK_TTL_MS = 5 * 60_000;
const MONITOR_LEASE_TTL_MS = 2 * 60 * 60_000;
const MONITOR_LEASE_MAX_ATTEMPTS = 6;

export function privateBridgeDirectory(directory, uid = process.getuid()) {
  return Boolean(directory?.isDirectory?.() && directory.uid === uid &&
    directory.gid === SHARED_GROUP && (directory.mode & 0o7777) === 0o2710);
}

function stopped(code) {
  return { status: 'blocked', code, source: 'hosted_browser_read_only',
    stop_required: true, purchase_allowed: false };
}

function restricted(cause, code = 'access_restricted') {
  return { ...stopped(code), restriction_cause: cause };
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

function reportablePartialRelease(observation) {
  return observation?.status === 'incomplete' && observation.complete === false &&
    observation.error_code === 'additional_page_required' && observation.has_next === true &&
    observation.page === 1 && observation.range_start === 1 &&
    Number.isSafeInteger(observation.range_end) && observation.range_end > 0 &&
    Number.isSafeInteger(observation.total) && observation.total > observation.range_end &&
    Array.isArray(observation.rows) && observation.rows.length === observation.range_end &&
    observation.assertions?.release_identity === true &&
    observation.assertions?.listing_container === true &&
    observation.assertions?.pagination === true && observation.assertions?.stable === true;
}

function partialReleaseReport(observation) {
  return bounded({ status: 'incomplete', code: 'additional_page_required',
    source: 'hosted_browser_read_only', observation, stop_required: true,
    purchase_allowed: false });
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
    if (reportablePartialRelease(observation)) return partialReleaseReport(observation);
    if (observation.status !== 'observed' || observation.complete !== true || observation.has_next !== false) {
      return stopped(observation.error_code || 'incomplete_release_page');
    }
    return bounded({ status: 'observed', source: 'hosted_browser_read_only',
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
        observation.release_id !== Number(OPERATOR_RELEASE) || observation.purchase_allowed !== false) {
      return stopped(observation?.error_code || 'release_observation_unverified');
    }
    if (reportablePartialRelease(observation)) return partialReleaseReport(observation);
    if (observation.status !== 'observed' || observation.complete !== true || observation.has_next !== false) {
      return stopped(observation.error_code || 'release_observation_unverified');
    }
    return bounded({ status: 'observed', source: 'hosted_browser_read_only', observation,
      stop_required: true, purchase_allowed: false });
  } catch { return stopped('hosted_read_failed'); }
}

/** Verify a user-opened normal release page without navigation or session access. */
async function verifyCurrentMonitorPage(context, releaseId, signal) {
  if (signal?.aborted) return stopped('read_cancelled');
  if (!context || typeof context.pages !== 'function') return stopped('browser_context_unavailable');
  try {
    const pages = context.pages();
    if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
    const matching = pages.filter(page => page && typeof page.url === 'function' &&
      typeof page.title === 'function' && typeof page.evaluate === 'function' &&
      exactUrl('current_release', releaseId, page.url()));
    if (matching.length !== 1) return stopped('exact_current_page_not_unique');
    const page = matching[0], url = page.url();
    const title = await beforeDeadline(() => page.title());
    if (signal?.aborted) return stopped('read_cancelled');
    if (page.url() !== url) return stopped('page_changed_during_read');
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu.test(title)) {
      return restricted('user_opened_challenge_or_login');
    }
    const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, releaseId));
    if (signal?.aborted) return stopped('read_cancelled');
    if (page.url() !== url || !exactUrl('current_release', releaseId, observation?.url) ||
        observation?.source !== 'discogs_rendered_release' ||
        observation.release_id !== Number(releaseId) || observation.purchase_allowed !== false ||
        !['release_identity', 'listing_container', 'pagination', 'stable'].every(
          key => observation.assertions?.[key] === true) ||
        !(observation.status === 'observed' && observation.complete === true ||
          reportablePartialRelease(observation))) return stopped('normal_page_unverified');
    return { status: 'verified', source: 'hosted_browser_read_only', release_id: Number(releaseId),
      complete: observation.complete, stop_required: true, purchase_allowed: false };
  } catch { return stopped('normal_page_unverified'); }
}

/** One read of the already-open exact cart. No navigation or control interaction. */
async function observeOperatorCurrentCart(context) {
  if (!context || typeof context.pages !== 'function') return stopped('browser_context_unavailable');
  try {
    const pages = context.pages();
    if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
    const matches = pages.filter(page => page && typeof page.url === 'function' &&
      typeof page.title === 'function' && typeof page.evaluate === 'function' &&
      exactUrl('current_cart', OPERATOR_CART_LISTING, page.url()));
    if (matches.length !== 1) return stopped('exact_current_page_not_unique');
    const page = matches[0], url = page.url();
    const title = await beforeDeadline(() => page.title());
    if (page.url() !== url) return stopped('page_changed_during_read');
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu.test(title)) {
      return stopped('access_restricted');
    }
    const restrictedPage = Object.freeze({
      url: () => page.url(),
      evaluate: (fn, id) => {
        if (fn !== collectCartDom || id !== OPERATOR_CART_LISTING) throw new TypeError('unsupported_collector');
        return page.evaluate(fn, id);
      },
    });
    const result = await beforeDeadline(() => inspectCurrentCartDom(restrictedPage,
      { expectedListingId: OPERATOR_CART_LISTING }));
    if (page.url() !== url) return stopped('page_changed_during_read');
    if (result?.purchase_allowed !== false || result.status !== 'blocked' ||
        !['final_quote_unverified', 'missing_address_and_unknown_final_quote'].includes(result.code) ||
        !result.observations?.includes('expected_listing_link_observed') ||
        !result.observations?.includes('single_cart_row_observed')) {
      return stopped(result?.code || 'cart_observation_unverified');
    }
    return bounded({ ...result, source: 'hosted_browser_read_only', stop_required: true, purchase_allowed: false });
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
  return async (request, { signal, deadline } = {}) => {
    const input = requestShape(request);
    if (!input || input.action !== 'observe_release') return stopped('unsupported_read_request');
    if (deadline !== undefined && (!Number.isSafeInteger(deadline) || deadline < 0)) {
      return stopped('unsupported_read_request');
    }
    const leaseExpired = () => deadline !== undefined && now() >= deadline;
    if (accessStopped) return stopped('access_restricted');
    if (active) return stopped('read_in_progress');
    if (leaseExpired()) return stopped('monitor_lease_unavailable');
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
      if (leaseExpired()) return stopped('monitor_lease_unavailable');
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
        return restricted(status === 401 ? 'http_401' : 'http_403',
          status === 401 ? 'auth_required' : 'access_restricted');
      }
      if (status !== 200) return stopped('unexpected_http_status');
      const landed = page.url();
      if (!exactUrl(input.action, input.id, landed)) {
        if (/\/(?:login|signin|cdn-cgi)(?:\/|\?|$)/iu.test(landed)) accessStopped = true;
        return accessStopped ? restricted(/\/cdn-cgi(?:\/|\?|$)/iu.test(landed) ?
          'challenge_redirect' : 'login_redirect') : stopped('unexpected_redirect');
      }
      const title = await page.title();
      if (/just a moment|attention required|security check|captcha|cloudflare/iu.test(title)) {
        accessStopped = true;
        return restricted('challenge_title');
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
      if (reportablePartialRelease(observation)) {
        if (observation.total > 250 || !observation.next_url) return partialReleaseReport(observation);
        const asPage = value => ({ source: value.source, purchase_allowed: false,
          release_id: value.release_id, url: value.url, observed_at: value.observed_at,
          page: value.page, range_start: value.range_start, range_end: value.range_end,
          total: value.total, has_next: value.has_next, rows: value.rows,
          assertions: value.assertions });
        const pages = [asPage(observation)], seen = new Set(observation.rows.map(row => row.listing_id));
        let previous = observation;
        while (previous.has_next) {
          const expectedPage = pages.length + 1;
          const nextUrl = `https://www.discogs.com/sell/release/${input.id}?page=${expectedPage}`;
          if (expectedPage > 100 || previous.next_url !== nextUrl || signal?.aborted) {
            return stopped('pagination_next_unverified');
          }
          if (leaseExpired()) return stopped('monitor_lease_unavailable');
          const nextResponse = await page.goto(nextUrl,
            { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
          if (!nextResponse || typeof nextResponse.status !== 'function') return stopped('http_response_unavailable');
          const nextStatus = nextResponse.status();
          if ([401, 403].includes(nextStatus)) {
            accessStopped = true;
            return restricted(nextStatus === 401 ? 'http_401' : 'http_403',
              nextStatus === 401 ? 'auth_required' : 'access_restricted');
          }
          if ([429, 503].includes(nextStatus)) {
            const retryAfter = typeof nextResponse.headerValue === 'function' ?
              await Promise.resolve(nextResponse.headerValue('retry-after')).catch(() => null) : null;
            nextAllowedAt = Math.max(nextAllowedAt, now() + Math.max(MIN_RELEASE_INTERVAL_MS,
              retryAfterMillis(retryAfter)));
            return { ...stopped(nextStatus === 429 ? 'rate_limited' : 'service_unavailable'),
              cooldown_seconds: Math.ceil((nextAllowedAt - now()) / 1000) };
          }
          if (nextStatus !== 200) return stopped('unexpected_http_status');
          if (page.url() !== nextUrl || signal?.aborted) {
            if (/\/(?:login|signin|cdn-cgi)(?:\/|\?|$)/iu.test(page.url())) accessStopped = true;
            return accessStopped ? restricted(/\/cdn-cgi(?:\/|\?|$)/iu.test(page.url()) ?
              'challenge_redirect' : 'login_redirect') : stopped('unexpected_redirect');
          }
          const nextTitle = await page.title();
          if (/just a moment|attention required|security check|captcha|cloudflare/iu.test(nextTitle)) {
            accessStopped = true;
            return restricted('challenge_title');
          }
          try {
            await page.waitForSelector('#pjax_container table.mpitems',
              { state: 'attached', timeout: READY_TIMEOUT_MS });
            await page.waitForSelector('nav.pagination .pagination_total',
              { state: 'attached', timeout: 3_000 });
          } catch { return stopped('marketplace_not_ready'); }
          const next = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom,
            { id: input.id, page: expectedPage }));
          if (signal?.aborted) return stopped('read_cancelled');
          if (page.url() !== nextUrl || next?.url !== nextUrl ||
              next.source !== 'discogs_rendered_release' || next.purchase_allowed !== false ||
              next.status !== 'incomplete' || next.complete !== false ||
              next.release_id !== Number(input.id) || next.page !== expectedPage ||
              next.total !== observation.total || next.range_start !== previous.range_end + 1 ||
              !Number.isSafeInteger(next.range_end) || next.range_end > next.total ||
              next.has_next !== (next.range_end < next.total) ||
              !Array.isArray(next.rows) || next.rows.length !== next.range_end - next.range_start + 1 ||
              !['release_identity', 'listing_container', 'pagination', 'stable'].every(
                key => next.assertions?.[key] === true) ||
              next.rows.some(row => seen.has(row.listing_id) || !ID.test(row.listing_id) ||
                row.url !== `https://www.discogs.com/sell/item/${row.listing_id}`)) {
            return stopped('pagination_changed');
          }
          for (const row of next.rows) seen.add(row.listing_id);
          pages.push(asPage(next));
          previous = next;
        }
        if (previous.range_end !== observation.total || seen.size !== observation.total) {
          return stopped('pagination_incomplete');
        }
        return bounded({ status: 'observed', source: 'hosted_browser_read_only', pages,
          stop_required: true, purchase_allowed: false });
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
  operatorCartAckPath = null, operatorCartUsedPath = null,
  clock = () => Date.now(),
  monitorVerifyTimeoutMs = READ_TIMEOUT_MS,
  monitorTrialTimeoutMs = RELEASE_PAGINATION_BUDGET_MS,
  leaseWriteHook = null,
} = {}) {
  if (!Number.isSafeInteger(monitorVerifyTimeoutMs) || monitorVerifyTimeoutMs < 1 ||
      monitorVerifyTimeoutMs > READ_TIMEOUT_MS ||
      !Number.isSafeInteger(monitorTrialTimeoutMs) || monitorTrialTimeoutMs < 1 ||
      monitorTrialTimeoutMs > RELEASE_PAGINATION_BUDGET_MS) {
    throw new TypeError('invalid_monitor_timeout');
  }
  if (leaseWriteHook !== null && typeof leaseWriteHook !== 'function') {
    throw new TypeError('invalid_lease_write_hook');
  }
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
  if ([operatorCartAckPath, operatorCartUsedPath].some(Boolean) &&
      (!durableStopPath || !operatorBootPath || !operatorCartAckPath || !operatorCartUsedPath)) {
    throw new TypeError('incomplete_cart_recovery_configuration');
  }
  if (operatorCartAckPath && dirname(operatorCartAckPath) !== dirname(operatorCartUsedPath)) {
    throw new TypeError('cart_grant_directory_mismatch');
  }
  if (shared && (!operatorCartAckPath || !operatorCartUsedPath)) {
    throw new TypeError('cart_recovery_configuration_required');
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
  const navigateRelease = createReleaseNavigator(context, { now: clock });
  const navigateListing = createListingNavigator(context);
  let accessStopped = Boolean(durableStopPath);
  let restrictedUntil = 0;
  let monitorTrial = null;
  let monitorRecoveryBusy = false;
  const monitorDirectory = durableStopPath ? dirname(durableStopPath) : null;
  const monitorLeasePath = monitorDirectory ? join(monitorDirectory, 'operator-monitor-lease.json') : null;
  const monitorAudit = async (grantId, event, releaseId, cause = null) => {
    const allowed = new Set(['verified', 'verification_stopped', 'trial_started', 'trial_observed',
      'trial_stopped', 'trials_complete_review_required', 'lease_armed',
      'lease_read_started', 'lease_observed', 'lease_stopped']);
    if (!allowed.has(event) || (cause !== null &&
        !/^[a-z0-9_]{1,48}$/u.test(cause))) throw new TypeError('invalid_monitor_audit');
    const record = { event, release_id: releaseId, grant_id: grantId, cause,
      time: new Date(clock()).toISOString(), purchase_allowed: false };
    await writeFile(join(monitorDirectory, `operator-monitor-event-${randomUUID()}`),
      JSON.stringify(record) + '\n', { flag: 'wx', mode: 0o600 });
  };
  const readMonitorLease = async () => {
    const info = await lstat(monitorLeasePath).catch(error => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    if (!info) return null;
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 ||
        info.size > 1024) throw new TypeError('invalid_monitor_lease');
    const state = JSON.parse(await readFile(monitorLeasePath, 'utf8'));
    if (!state || typeof state !== 'object' || Array.isArray(state) ||
        !CART_GRANT_ID.test(state.grant_id) || !MONITOR_TEST_RELEASES.has(state.verified_release) ||
        typeof state.boot_id !== 'string' || !CART_GRANT_ID.test(state.boot_id) ||
        !Number.isSafeInteger(state.expires_at) || !Number.isSafeInteger(state.next_allowed_at) ||
        !Number.isSafeInteger(state.attempts) || state.attempts < 0 ||
        state.attempts > MONITOR_LEASE_MAX_ATTEMPTS || typeof state.revoked !== 'boolean') {
      throw new TypeError('invalid_monitor_lease');
    }
    return state;
  };
  const writeMonitorLease = async state => {
    const temporary = join(monitorDirectory, `operator-monitor-lease-${randomUUID()}.tmp`);
    await writeFile(temporary, JSON.stringify(state) + '\n', { flag: 'wx', mode: 0o600 });
    try {
      if (leaseWriteHook) await leaseWriteHook(state);
      await rename(temporary, monitorLeasePath);
    }
    finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
  };
  const revokeMonitorLease = async (state, cause) => {
    if (state.revoked) return;
    await writeMonitorLease({ ...state, revoked: true });
    await monitorAudit(state.grant_id, 'lease_stopped', state.verified_release, cause);
  };
  const activeMonitorLease = async () => {
    const state = await readMonitorLease();
    return Boolean(state && !state.revoked && clock() < state.expires_at &&
      state.attempts < MONITOR_LEASE_MAX_ATTEMPTS);
  };
  const server = createServer(connection => {
    let input = Buffer.alloc(0), done = false;
    let activeMonitorTrial = null;
    const controller = new AbortController();
    const monitorAbortCause = () => controller.signal.reason === 'request_timed_out' ?
      'request_timed_out' : controller.signal.reason === 'request_disconnected' ?
        'request_disconnected' : 'read_cancelled';
    connection.setTimeout(READ_TIMEOUT_MS);
    const send = result => {
      if (done) return;
      done = true;
      connection.end(JSON.stringify(bounded(result)) + '\n');
    };
    const invalidateActiveTrial = () => {
      if (activeMonitorTrial && monitorTrial === activeMonitorTrial) monitorTrial = null;
    };
    connection.on('timeout', () => {
      controller.abort('request_timed_out');
      invalidateActiveTrial();
      send(stopped('request_timed_out'));
    });
    connection.on('close', () => {
      done = true;
      controller.abort('request_disconnected');
      invalidateActiveTrial();
    });
    connection.on('error', () => {
      done = true;
      controller.abort('request_disconnected');
      invalidateActiveTrial();
    });
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
      if (monitorTrial && clock() >= monitorTrial.expires) {
        try {
          await monitorAudit(monitorTrial.grantId, 'trial_stopped',
            monitorTrial.verifiedRelease, 'expired');
          monitorTrial = null;
        } catch { return send(stopped('monitor_recovery_unavailable')); }
      }
      if (request?.action === 'operator_monitor_verify') {
        const keys = Reflect.ownKeys(request);
        const grantId = Object.getOwnPropertyDescriptor(request, 'grant_id')?.value;
        const releaseId = Object.getOwnPropertyDescriptor(request, 'release_id')?.value;
        if (!durableStopPath || !accessStopped || busy || monitorRecoveryBusy || keys.length !== 3 ||
            !keys.includes('grant_id') || !keys.includes('release_id') ||
            typeof grantId !== 'string' || !CART_GRANT_ID.test(grantId) ||
            !MONITOR_TEST_RELEASES.has(releaseId) || monitorTrial) {
          return send(stopped('unsupported_monitor_recovery'));
        }
        try {
          if (await activeMonitorLease()) return send(stopped('unsupported_monitor_recovery'));
        } catch { return send(stopped('monitor_recovery_unavailable')); }
        if (busy || monitorRecoveryBusy || monitorTrial) return send(stopped('read_in_progress'));
        busy = true;
        monitorRecoveryBusy = true;
        connection.setTimeout(monitorVerifyTimeoutMs);
        try {
          const ackPath = join(monitorDirectory, `operator-monitor-grant-${grantId}-ack`);
          const usedPath = join(monitorDirectory, `operator-monitor-grant-${grantId}-used`);
          const ack = await lstat(ackPath).catch(() => null);
          if (!ack?.isFile() || ack.uid !== process.getuid() ||
              (ack.mode & 0o777) !== 0o600 ||
              clock() + 1_000 < ack.mtimeMs || clock() - ack.mtimeMs > MONITOR_ACK_TTL_MS ||
              await readFile(ackPath, 'utf8') !==
                `operator_confirmed_monitor_release:${releaseId}:${grantId}:${bootId}\n`) {
            return send(stopped('operator_acknowledgement_required'));
          }
          await writeFile(usedPath, 'consumed\n', { flag: 'wx', mode: 0o600 });
          const verified = await verifyCurrentMonitorPage(context, releaseId, controller.signal);
          if (controller.signal.aborted || done) {
            await monitorAudit(grantId, 'verification_stopped', releaseId,
              monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          if (verified.status !== 'verified') {
            await monitorAudit(grantId, 'verification_stopped', releaseId,
              verified.restriction_cause || verified.code);
            return send(verified);
          }
          await monitorAudit(grantId, 'verified', releaseId);
          if (controller.signal.aborted || done) {
            await monitorAudit(grantId, 'verification_stopped', releaseId,
              monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          monitorTrial = { grantId, verifiedRelease: releaseId,
            pending: new Set(MONITOR_TEST_RELEASES),
            expires: clock() + MONITOR_TRIAL_TTL_MS };
          return send(verified);
        } catch { return send(stopped('monitor_recovery_unavailable')); }
        finally { busy = false; monitorRecoveryBusy = false; }
      }
      if (request?.action === 'operator_monitor_trial') {
        const keys = Reflect.ownKeys(request);
        const grantId = Object.getOwnPropertyDescriptor(request, 'grant_id')?.value;
        const releaseId = Object.getOwnPropertyDescriptor(request, 'release_id')?.value;
        if (!durableStopPath || !accessStopped || busy || monitorRecoveryBusy || keys.length !== 3 ||
            !keys.includes('grant_id') || !keys.includes('release_id') ||
            typeof grantId !== 'string' || !CART_GRANT_ID.test(grantId) ||
            !MONITOR_TEST_RELEASES.has(releaseId) || monitorTrial?.grantId !== grantId ||
            !monitorTrial.pending.has(releaseId) || clock() >= monitorTrial.expires) {
          return send(stopped('monitor_trial_unavailable'));
        }
        busy = true;
        monitorRecoveryBusy = true;
        activeMonitorTrial = monitorTrial;
        connection.setTimeout(monitorTrialTimeoutMs);
        try {
          await monitorAudit(grantId, 'trial_started', releaseId);
          if (controller.signal.aborted || done) {
            monitorTrial = null;
            await monitorAudit(grantId, 'trial_stopped', releaseId, monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          const result = await navigateRelease({ action: 'observe_release', release_id: releaseId },
            { signal: controller.signal });
          if (controller.signal.aborted || done || monitorTrial !== activeMonitorTrial) {
            monitorTrial = null;
            await monitorAudit(grantId, 'trial_stopped', releaseId, monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          if (result?.code === 'release_cooldown') return send(result);
          monitorTrial.pending.delete(releaseId);
          if (result.status !== 'observed' || result.purchase_allowed !== false) {
            monitorTrial = null;
            await monitorAudit(grantId, 'trial_stopped', releaseId,
              result.restriction_cause || result.code || 'unverified_result');
            if (['auth_required', 'access_restricted'].includes(result.code)) accessStopped = true;
            return send(result);
          }
          await monitorAudit(grantId, 'trial_observed', releaseId);
          if (controller.signal.aborted || done || monitorTrial !== activeMonitorTrial) {
            monitorTrial = null;
            await monitorAudit(grantId, 'trial_stopped', releaseId, monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          if (monitorTrial.pending.size === 0) {
            monitorTrial = null;
            await monitorAudit(grantId, 'trials_complete_review_required', releaseId);
            return send({ ...result, recovery_review_required: true });
          }
          return send({ ...result, recovery_review_required: true });
        } catch {
          monitorTrial = null;
          return send(stopped('monitor_trial_unavailable'));
        } finally { busy = false; monitorRecoveryBusy = false; activeMonitorTrial = null; }
      }
      if (request?.action === 'operator_monitor_lease_verify') {
        const keys = Reflect.ownKeys(request);
        const grantId = Object.getOwnPropertyDescriptor(request, 'grant_id')?.value;
        const releaseId = Object.getOwnPropertyDescriptor(request, 'release_id')?.value;
        if (!durableStopPath || !accessStopped || busy || monitorRecoveryBusy || monitorTrial ||
            keys.length !== 3 || !keys.includes('grant_id') || !keys.includes('release_id') ||
            typeof grantId !== 'string' || !CART_GRANT_ID.test(grantId) ||
            !MONITOR_TEST_RELEASES.has(releaseId)) {
          return send(stopped('unsupported_monitor_lease'));
        }
        busy = true;
        monitorRecoveryBusy = true;
        connection.setTimeout(monitorVerifyTimeoutMs);
        try {
          const previous = await readMonitorLease();
          if (previous && !previous.revoked && clock() < previous.expires_at &&
              previous.attempts < MONITOR_LEASE_MAX_ATTEMPTS) {
            return send(stopped('monitor_lease_already_active'));
          }
          const ackPath = join(monitorDirectory, `operator-monitor-lease-grant-${grantId}-ack`);
          const usedPath = join(monitorDirectory, `operator-monitor-lease-grant-${grantId}-used`);
          const ack = await lstat(ackPath).catch(() => null);
          if (!ack?.isFile() || ack.uid !== process.getuid() ||
              (ack.mode & 0o777) !== 0o600 || clock() + 1_000 < ack.mtimeMs ||
              clock() - ack.mtimeMs > MONITOR_ACK_TTL_MS ||
              await readFile(ackPath, 'utf8') !==
                `operator_confirmed_monitor_lease:${releaseId}:${grantId}:${bootId}\n`) {
            return send(stopped('operator_acknowledgement_required'));
          }
          await writeFile(usedPath, 'consumed\n', { flag: 'wx', mode: 0o600 });
          const verified = await verifyCurrentMonitorPage(context, releaseId, controller.signal);
          if (controller.signal.aborted || done) {
            await monitorAudit(grantId, 'verification_stopped', releaseId, monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          if (verified.status !== 'verified') {
            await monitorAudit(grantId, 'verification_stopped', releaseId,
              verified.restriction_cause || verified.code);
            return send(verified);
          }
          const lease = { grant_id: grantId, verified_release: releaseId, boot_id: bootId,
            expires_at: clock() + MONITOR_LEASE_TTL_MS,
            next_allowed_at: Math.max(clock(), previous?.next_allowed_at || 0),
            attempts: 0, revoked: false };
          if (controller.signal.aborted || done) return send(stopped('read_cancelled'));
          await monitorAudit(grantId, 'lease_armed', releaseId);
          if (controller.signal.aborted || done) return send(stopped('read_cancelled'));
          await writeMonitorLease(lease);
          if (controller.signal.aborted || done) {
            await revokeMonitorLease(lease, monitorAbortCause());
            return send(stopped('read_cancelled'));
          }
          return send({ ...verified, monitor_lease_expires_at: lease.expires_at,
            monitor_lease_remaining: MONITOR_LEASE_MAX_ATTEMPTS });
        } catch { return send(stopped('monitor_lease_unavailable')); }
        finally { busy = false; monitorRecoveryBusy = false; }
      }
      if (request?.action === 'operator_monitor_lease_revoke') {
        if (!durableStopPath || busy || monitorRecoveryBusy ||
            Reflect.ownKeys(request).length !== 1) return send(stopped('unsupported_monitor_lease'));
        busy = true;
        monitorRecoveryBusy = true;
        try {
          const lease = await readMonitorLease();
          if (!lease) return send(stopped('monitor_lease_unavailable'));
          await revokeMonitorLease(lease, 'operator_revoked');
          return send(stopped('monitor_lease_stopped'));
        } catch { return send(stopped('monitor_lease_unavailable')); }
        finally { busy = false; monitorRecoveryBusy = false; }
      }
      if (request?.action === 'operator_monitor_lease_read') {
        const keys = Reflect.ownKeys(request);
        const releaseId = Object.getOwnPropertyDescriptor(request, 'release_id')?.value;
        if (!durableStopPath || !accessStopped || busy || monitorRecoveryBusy || monitorTrial ||
            keys.length !== 2 || !keys.includes('release_id') ||
            !MONITOR_TEST_RELEASES.has(releaseId)) {
          return send(stopped('unsupported_monitor_lease'));
        }
        busy = true;
        monitorRecoveryBusy = true;
        connection.setTimeout(monitorTrialTimeoutMs);
        let attempted = null;
        try {
          const lease = await readMonitorLease();
          if (!lease || lease.revoked || lease.boot_id !== bootId) {
            return send(stopped('monitor_lease_unavailable'));
          }
          if (clock() >= lease.expires_at || lease.attempts >= MONITOR_LEASE_MAX_ATTEMPTS) {
            await revokeMonitorLease(lease, clock() >= lease.expires_at ? 'expired' : 'cap_reached');
            return send(stopped('monitor_lease_unavailable'));
          }
          if (clock() < lease.next_allowed_at) {
            return send({ ...stopped('monitor_lease_cooldown'),
              cooldown_seconds: Math.ceil((lease.next_allowed_at - clock()) / 1000) });
          }
          attempted = { ...lease, attempts: lease.attempts + 1,
            next_allowed_at: clock() + MIN_RELEASE_INTERVAL_MS };
          // Persist the request slot before opening a browser tab. A crash loses a slot.
          await writeMonitorLease(attempted);
          await monitorAudit(lease.grant_id, 'lease_read_started', releaseId);
          if (controller.signal.aborted || done) {
            await revokeMonitorLease(attempted, monitorAbortCause());
            return send(stopped('monitor_lease_stopped'));
          }
          const result = await navigateRelease({ action: 'observe_release', release_id: releaseId },
            { signal: controller.signal, deadline: attempted.expires_at });
          if (controller.signal.aborted || done) {
            await revokeMonitorLease(attempted, monitorAbortCause());
            return send(stopped('monitor_lease_stopped'));
          }
          if (clock() >= attempted.expires_at) {
            await revokeMonitorLease(attempted, 'expired');
            return send(stopped('monitor_lease_unavailable'));
          }
          if (['rate_limited', 'service_unavailable', 'release_cooldown'].includes(result?.code)) {
            const delay = Number.isSafeInteger(result.cooldown_seconds) &&
              result.cooldown_seconds > 0 ? result.cooldown_seconds * 1000 : 0;
            attempted = { ...attempted,
              next_allowed_at: Math.max(attempted.next_allowed_at, clock() + delay) };
            await writeMonitorLease(attempted);
            if (controller.signal.aborted || done) {
              await revokeMonitorLease(attempted, monitorAbortCause());
              return send(stopped('monitor_lease_stopped'));
            }
            if (clock() >= attempted.expires_at) {
              await revokeMonitorLease(attempted, 'expired');
              return send(stopped('monitor_lease_unavailable'));
            }
            return send(result);
          }
          if (result?.status !== 'observed' || result.purchase_allowed !== false) {
            await revokeMonitorLease(attempted, result?.restriction_cause ||
              result?.code || 'unverified_result');
            return send(result || stopped('monitor_lease_stopped'));
          }
          await monitorAudit(lease.grant_id, 'lease_observed', releaseId);
          if (controller.signal.aborted || done) {
            await revokeMonitorLease(attempted, monitorAbortCause());
            return send(stopped('monitor_lease_stopped'));
          }
          if (clock() >= attempted.expires_at) {
            await revokeMonitorLease(attempted, 'expired');
            return send(stopped('monitor_lease_unavailable'));
          }
          return send({ ...result, monitor_lease_remaining:
            MONITOR_LEASE_MAX_ATTEMPTS - attempted.attempts });
        } catch {
          if (attempted) await revokeMonitorLease(attempted, 'uncertain_result').catch(() => {});
          return send(stopped('monitor_lease_stopped'));
        } finally { busy = false; monitorRecoveryBusy = false; }
      }
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
      if (request?.action === 'operator_current_cart') {
        const keys = Reflect.ownKeys(request);
        const scoped = keys.includes('grant_id');
        const grantId = Object.getOwnPropertyDescriptor(request, 'grant_id')?.value;
        if (!durableStopPath || !accessStopped || busy || !operatorCartAckPath || !operatorCartUsedPath ||
            keys.length !== (scoped ? 3 : 2) || request.listing_id !== OPERATOR_CART_LISTING ||
            (scoped && (typeof grantId !== 'string' || !CART_GRANT_ID.test(grantId)))) {
          return send(stopped('unsupported_operator_recovery'));
        }
        const directory = dirname(operatorCartAckPath);
        const ackPath = scoped ? join(directory, `operator-cart-grant-${grantId}-ack`) : operatorCartAckPath;
        const usedPath = scoped ? join(directory, `operator-cart-grant-${grantId}-used`) : operatorCartUsedPath;
        const expectedAck = scoped ?
          `operator_confirmed_current_cart:${OPERATOR_CART_LISTING}:${grantId}:${bootId}\n` :
          `operator_confirmed_current_cart:${OPERATOR_CART_LISTING}:${bootId}\n`;
        busy = true;
        try {
          const ack = await lstat(ackPath).catch(() => null);
          if (!ack?.isFile() || ack.uid !== process.getuid() ||
              (ack.mode & 0o777) !== 0o600 ||
              await readFile(ackPath, 'utf8') !== expectedAck) {
            return send(stopped('operator_acknowledgement_required'));
          }
          // Consume before looking at the browser; an uncertain read cannot replay.
          await writeFile(usedPath, 'consumed\n', { flag: 'wx', mode: 0o600 });
          return send(await observeOperatorCurrentCart(context));
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
      if (navigation) connection.setTimeout(action === 'observe_release' ?
        RELEASE_PAGINATION_BUDGET_MS : NAVIGATION_BUDGET_MS);
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
