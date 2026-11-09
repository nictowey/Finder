/** Merchant-order evidence boundary; no navigation, provider call or transaction.
 * No post-payment receipt DOM or buyer-order detail layout has been qualified.
 * Consequently the current-page reader cannot produce OrderPageEvidence yet.
 * Fresh operator-observed All/All zero history can be normalized separately;
 * it never clears a one-copy fence or proves a provider draft was cancelled.
 */
import { collectHostedRestrictionDom, classifyHostedRestriction } from './discogs_hosted_readonly.mjs';

export const MERCHANT_HISTORY_URL = 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50';
export const KC_ORDER_SCOPE = Object.freeze({ release_id: 3422100, listing_id: '1366950910',
  seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05' });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const ORDER_ID = /^[0-9]{1,20}-[0-9]{1,20}$/u;
const MAX_AGE_SECONDS = 300;
const MAX_READ_MS = 1500;
const active = new WeakSet();
const settlements = new WeakMap();

function settleIfFinished(page, entry) {
  if (!entry.finished || entry.tasks.size !== 0) return;
  if (settlements.get(page) === entry) {
    settlements.delete(page);
    active.delete(page);
  }
  entry.resolve();
}

function trackRead(page, entry, task) {
  entry.tasks.add(task);
  const finished = () => { entry.tasks.delete(task); settleIfFinished(page, entry); };
  task.then(finished, finished);
  return task;
}

/** A timeout/cancellation returns promptly but cannot cancel a dispatched Page
 * read. Shared browser owners must await settlement before releasing their
 * context lease. Reentry remains blocked until the reader and all raw reads end.
 */
export async function waitForMerchantOrderEvidenceSettlement(page) {
  const entry = settlements.get(page);
  if (entry) await entry.settled;
}

function exact(value, keys) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Reflect.ownKeys(value).length === keys.length &&
    keys.every(key => { const d = Object.getOwnPropertyDescriptor(value, key);
      return d?.enumerable === true && Object.hasOwn(d, 'value'); }));
}
const epoch = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const scopeValid = value => exact(value, Object.keys(KC_ORDER_SCOPE)) &&
  Object.entries(KC_ORDER_SCOPE).every(([key, wanted]) => value[key] === wanted);
const orderValid = value => value === null || typeof value === 'string' && ORDER_ID.test(value);

function result(code, extra = {}) {
  return { schema: 'discogs_merchant_order_evidence_v1', status: 'unverified', code,
    merchant_order_status: 'unverified', payment_status: 'unverified',
    order_page_evidence: null, expected_order_found: null, absence_verified: false,
    may_submit_again: false, purchase_allowed: false, ...extra };
}

/** Normalize the UI facts actually observed in the existing All/All zero view.
 * Input is a trusted operator's bounded observation, not a DOM capture or a
 * provider callback. Callers must preserve that provenance. No order is inferred.
 * An existing ledger receives no positive receipt from this result; recording a
 * missing lookup must preserve its submission_unknown/permanent one-copy slot.
 */
export function normalizeOperatorMerchantHistory(observation, {
  now = Date.now() / 1000, afterAttemptAt, expectedOrderId = null,
} = {}) {
  if (!epoch(now) || !epoch(afterAttemptAt) || afterAttemptAt > now || !orderValid(expectedOrderId)) {
    return result('merchant_lookup_context_unverified');
  }
  const keys = ['schema', 'source', 'scope', 'page_url', 'archived_filter', 'status_filter',
    'page_size', 'range_text', 'empty_text', 'pagination_present', 'restriction_present',
    'history_restore', 'document_ref', 'observed_at'];
  if (!exact(observation, keys) || observation.schema !== 'discogs_operator_history_observation_v1' ||
      observation.source !== 'operator_current_merchant_ui' || !scopeValid(observation.scope) ||
      typeof observation.document_ref !== 'string' || !UUID.test(observation.document_ref) ||
      !epoch(observation.observed_at)) return result('merchant_history_observation_unverified');
  if (!(afterAttemptAt <= observation.observed_at && observation.observed_at <= now &&
      now - observation.observed_at < MAX_AGE_SECONDS)) return result('merchant_history_stale_or_before_attempt');
  if (observation.restriction_present !== false) return result('merchant_access_unverified');
  if (observation.history_restore !== false) return result('merchant_history_restored_unverified');
  if (observation.page_url !== MERCHANT_HISTORY_URL || observation.archived_filter !== 'All' ||
      observation.status_filter !== 'All' || observation.page_size !== 50 ||
      observation.pagination_present !== false) return result('merchant_history_coverage_unverified');
  if (typeof observation.range_text !== 'string' || observation.range_text.length > 32 ||
      !/^0\s*[–-]\s*0\s+of\s+0$/u.test(observation.range_text.trim()) ||
      observation.empty_text !== 'No orders found') return result('merchant_history_empty_state_unverified');
  return result('zero_merchant_orders_as_observed', {
    status: 'observed', source: 'operator_current_merchant_ui',
    observation_kind: 'complete_zero_history', history_coverage: 'all_archived_and_statuses',
    observed_order_count: 0, observed_at: observation.observed_at,
    document_ref: observation.document_ref, autonomous_dom_verified: false,
    expected_order_id: expectedOrderId, expected_order_found: false,
    provider_draft_status: 'unverified', backend_order_timing_verified: false,
  });
}

/** Bounded read of a current merchant page using only existing read primitives.
 * This does NOT navigate to history, inspect frames, read provider tokens, accept
 * supplied receipt fields, or run guessed detail selectors. The known URL alone
 * cannot qualify a history DOM; unsupported layouts remain explicit below.
 */
export async function readCurrentMerchantOrderEvidence(page, scope, {
  signal = null, timeoutMs = MAX_READ_MS,
} = {}) {
  if (!scopeValid(scope)) return result('merchant_order_scope_unverified');
  if (!page || typeof page.url !== 'function' || typeof page.title !== 'function' ||
      typeof page.evaluate !== 'function' || !Number.isSafeInteger(timeoutMs) ||
      timeoutMs < 1 || timeoutMs > MAX_READ_MS) return result('merchant_reader_unavailable');
  if (active.has(page)) return result('merchant_read_in_progress');
  if (signal?.aborted) return result('merchant_read_cancelled');
  active.add(page);
  const entry = { tasks: new Set(), finished: false, resolve: null, settled: null };
  entry.settled = new Promise(resolve => { entry.resolve = resolve; });
  settlements.set(page, entry);
  const deadline = Date.now() + timeoutMs;
  const TIMEOUT = Symbol('merchant_read_timeout');
  const CANCELLED = Symbol('merchant_read_cancelled');
  const bounded = async work => {
    if (signal?.aborted) throw CANCELLED;
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw TIMEOUT;
    let timer, abort;
    const interrupt = new Promise((_, reject) => {
      timer = setTimeout(() => reject(TIMEOUT), remaining);
      if (signal) { abort = () => reject(CANCELLED); signal.addEventListener('abort', abort, { once: true }); }
    });
    try {
      // Recheck immediately before dispatch: a queued task must not start a
      // browser read after cancellation/deadline won the public race.
      const task = trackRead(page, entry, Promise.resolve().then(() => {
        if (signal?.aborted) throw CANCELLED;
        if (Date.now() >= deadline) throw TIMEOUT;
        return work();
      }));
      const value = await Promise.race([task, interrupt]);
      if (signal?.aborted) throw CANCELLED;
      if (Date.now() >= deadline) throw TIMEOUT;
      return value;
    } finally { clearTimeout(timer); if (abort) signal.removeEventListener('abort', abort); }
  };
  try {
    const before = await bounded(() => page.url());
    // No buyer-detail URL grammar has been captured. Cart, enhanced checkout and
    // provider URLs cannot become receipt sources by passing an SDK identifier.
    if (before !== MERCHANT_HISTORY_URL) return result('merchant_receipt_layout_unqualified');
    const title = await bounded(() => page.title());
    const first = classifyHostedRestriction({ title, url: before });
    if (first) return result('merchant_access_restricted', {
      restriction_kind: first.restriction_kind, restriction_cause: first.restriction_cause });
    const hints = await bounded(() => page.evaluate(collectHostedRestrictionDom));
    const after = await bounded(() => page.url());
    const afterTitle = await bounded(() => page.title());
    if (after !== before) return result('merchant_page_changed');
    const restriction = classifyHostedRestriction({ title: afterTitle, url: after, dom: hints });
    if (restriction) return result('merchant_access_restricted', {
      restriction_kind: restriction.restriction_kind, restriction_cause: restriction.restriction_cause });
    // Only the URL, displayed count/filter labels and empty text were retained;
    // their DOM associations have not been captured. Do not invent selectors or
    // report a complete/zero result from arbitrary page text.
    return result('merchant_history_layout_unqualified');
  } catch (error) {
    return result(error === CANCELLED ? 'merchant_read_cancelled' :
      error === TIMEOUT ? 'merchant_read_timeout' : 'merchant_read_failed');
  } finally {
    entry.finished = true;
    settleIfFinished(page, entry);
  }
}
