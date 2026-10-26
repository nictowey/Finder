/** Dormant exact-item guest-card initialization. No card-data or payment API.
 * Trusted local producers must supply current, independently verified evidence;
 * an explicit merchant total suffices only for this preparation, never payment.
 * No browser, listener, persistent directory, credentials or live ports are created.
 */
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isAbsolute, join } from 'node:path';

export const GUEST_CARD_URL = 'https://www.discogs.com/marketplace/checkout';
export const GUEST_CARD_SELECTOR = '[role="link"][data-funding-source="card"][aria-label="Debit or Credit Card"]';
export const GUEST_CARD_FENCE = 'guest-card-1366950910-init.fence';
export const GUEST_CARD_MAX_PREPARATION_MS = 90_000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const SCOPE_KEYS = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller',
  'quantity', 'currency', 'max_total', 'rules_revision', 'quote_fingerprint',
  'destination_ref', 'terms_fingerprint', 'issued_at', 'expires_at'];
const EVIDENCE_KEYS = ['source', 'release_id', 'listing_id', 'seller', 'quantity',
  'item_count', 'seller_count', 'rules_revision', 'rules_current', 'criteria_satisfied',
  'already_owned', 'existing_order', 'uncertain_prior_action', 'duplicate_guard_verified',
  'approval_ref', 'authorization_current', 'authorization_revoked', 'currency',
  'item', 'shipping', 'tax', 'fees', 'total', 'tax_verified', 'fees_verified',
  'amount_basis', 'preparation_quote_verified', 'final_payment_quote_verified',
  'deferred_total_notice', 'shipping_selection_verified',
  'quote_fingerprint', 'destination_ref', 'destination_verified', 'terms_fingerprint',
  'terms_verified', 'observed_at', 'expires_at'];
const CONTROL_KEYS = ['count', 'visible', 'enabled', 'role', 'funding_source',
  'aria_label', 'aria_disabled', 'control_ref'];

function record(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(descriptors).length === keys.length && keys.every(key =>
    descriptors[key]?.enumerable === true && Object.hasOwn(descriptors[key], 'value'));
}
function canonical(value) {
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function digest(value) { return createHash('sha256').update(canonical(value)).digest('hex'); }
function number(value) { return typeof value === 'number' && Number.isFinite(value); }
function uuid(value) { return typeof value === 'string' && UUID.test(value); }
function hash(value) { return typeof value === 'string' && HASH.test(value); }
function cents(value) {
  if (typeof value !== 'string' || !MONEY.test(value)) return null;
  const [whole, fraction] = value.split('.');
  return BigInt(whole) * 100n + BigInt(fraction);
}
function exactScope(scope, now) {
  return record(scope, SCOPE_KEYS) && uuid(scope.intent_id) && uuid(scope.approval_ref) &&
    uuid(scope.destination_ref) && scope.release_id === 3422100 &&
    scope.listing_id === '1366950910' && scope.seller === 'Mailman21842' &&
    scope.quantity === 1 && scope.currency === 'USD' && scope.max_total === '5.05' &&
    Number.isSafeInteger(scope.rules_revision) && scope.rules_revision >= 0 &&
    hash(scope.quote_fingerprint) && hash(scope.terms_fingerprint) &&
    number(scope.issued_at) && number(scope.expires_at) &&
    scope.issued_at <= now && now < scope.expires_at &&
    scope.expires_at - scope.issued_at > 0 && scope.expires_at - scope.issued_at <= 300;
}
function inspect(value, scope, now) {
  if (!record(value, ['schema', 'url', 'access', 'control', 'evidence']) ||
      value.schema !== 'discogs_guest_card_inspection_v1' || value.url !== GUEST_CARD_URL ||
      value.access !== 'normal' || !record(value.control, CONTROL_KEYS) ||
      !record(value.evidence, EVIDENCE_KEYS)) return null;
  const c = value.control, e = value.evidence;
  if (c.count !== 1 || c.visible !== true || c.enabled !== true || c.role !== 'link' ||
      c.funding_source !== 'card' || c.aria_label !== 'Debit or Credit Card' ||
      c.aria_disabled !== 'false' || !uuid(c.control_ref) ||
      e.source !== 'trusted_current_checkout' ||
      e.release_id !== scope.release_id || e.listing_id !== scope.listing_id ||
      e.seller !== scope.seller || e.quantity !== 1 || e.item_count !== 1 || e.seller_count !== 1 ||
      e.rules_revision !== scope.rules_revision || e.rules_current !== true ||
      e.criteria_satisfied !== true || e.already_owned !== false || e.existing_order !== false ||
      e.uncertain_prior_action !== false || e.duplicate_guard_verified !== true ||
      e.approval_ref !== scope.approval_ref || e.authorization_current !== true ||
      e.authorization_revoked !== false || e.currency !== scope.currency ||
      e.quote_fingerprint !== scope.quote_fingerprint ||
      e.amount_basis !== 'explicit_merchant_checkout_total' ||
      e.preparation_quote_verified !== true || e.final_payment_quote_verified !== false ||
      e.deferred_total_notice !== false || e.shipping_selection_verified !== true ||
      e.destination_ref !== scope.destination_ref || e.destination_verified !== true ||
      e.terms_fingerprint !== scope.terms_fingerprint || e.terms_verified !== true ||
      !number(e.observed_at) || !number(e.expires_at) || e.observed_at > now ||
      now - e.observed_at > 300 || !(now < e.expires_at && e.expires_at <= scope.expires_at) ||
      e.expires_at - e.observed_at > 300) return null;
  // Merchant aggregate evidence is preparation-only. Absent breakdowns stay
  // null/unverified: neither this gate nor its result manufacture zero tax/fees.
  const known = [cents(e.item), cents(e.shipping)], total = cents(e.total);
  for (const key of ['tax', 'fees']) {
    if (e[key] === null && e[key + '_verified'] === false) continue;
    const amount = cents(e[key]);
    if (amount === null || e[key + '_verified'] !== true) return null;
    known.push(amount);
  }
  if (known.some(amount => amount === null) || total === null ||
      e.item !== '0.10' || e.shipping !== '4.95' || e.total !== '5.05' ||
      known.reduce((sum, amount) => sum + amount, 0n) > total ||
      total > cents(scope.max_total)) return null;
  const stable = { ...e }; delete stable.observed_at;
  return Object.freeze({ ref: c.control_ref, expiresAt: e.expires_at * 1000,
    fingerprint: digest({ control: c, evidence: stable }) });
}

/** Pure validation for a future actual browser port; never grants access. */
export function validateGuestCardInspection(value, expectedScope, { now = Date.now() / 1000 } = {}) {
  if (!number(now) || !exactScope(expectedScope, now)) return null;
  const parsed = inspect(value, expectedScope, now);
  return parsed ? Object.freeze({ control_ref: parsed.ref,
    inspection_fingerprint: parsed.fingerprint, expires_at: parsed.expiresAt / 1000 }) : null;
}

/** A port must call this on fresh complete evidence immediately before dispatch.
 * This validates the immutable binding, not its caller or the durable fence.
 * Only prepareExactGuestCard orchestrates the fence and invokes that port.
 */
export function revalidateGuestCardClick(binding, currentInspection, { now = Date.now() / 1000 } = {}) {
  if (!record(binding, ['url', 'selector', 'control_ref', 'scope_digest', 'intent_id',
    'inspection_fingerprint', 'expected_scope']) || !Object.isFrozen(binding) ||
      !Object.isFrozen(binding.expected_scope) || binding.url !== GUEST_CARD_URL ||
      binding.selector !== GUEST_CARD_SELECTOR || !uuid(binding.control_ref) ||
      !hash(binding.scope_digest) || !hash(binding.inspection_fingerprint) ||
      !number(now) || !exactScope(binding.expected_scope, now) ||
      binding.intent_id !== binding.expected_scope.intent_id ||
      binding.scope_digest !== digest(binding.expected_scope)) return false;
  const parsed = validateGuestCardInspection(currentInspection, binding.expected_scope, { now });
  return Boolean(parsed && parsed.control_ref === binding.control_ref &&
    parsed.inspection_fingerprint === binding.inspection_fingerprint);
}
function stopped(code, fenced = false, formVisible = false) {
  return { schema: 'discogs_guest_card_prepare_v1', status: formVisible ? 'handoff_required' : 'blocked',
    code, initialization: fenced ? 'outcome_requires_reconciliation' : 'not_dispatched',
    form_visible: formVisible, order_status: 'unverified', payment_status: 'unverified',
    final_payment_quote_verified: false,
    payment_execution: 'not_implemented', purchase_allowed: false, may_initialize_again: false };
}

async function privateDirectory(directory) {
  if (typeof directory !== 'string' || !isAbsolute(directory)) throw new Error('private_directory_required');
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      (info.mode & 0o7777) !== 0o700) throw new Error('private_directory_required');
}
async function persistFence(directory, scope, now) {
  await privateDirectory(directory);
  const parent = await open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const file = await open(join(directory, GUEST_CARD_FENCE),
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    try {
      await file.writeFile(JSON.stringify({ schema: 'discogs_guest_card_init_fence_v1',
        intent_id: scope.intent_id, scope_digest: digest(scope), created_at: now,
        release_id: scope.release_id, listing_id: scope.listing_id,
        state: 'initialization_outcome_unknown' }) + '\n');
      await file.sync();
    } finally { await file.close(); }
    await parent.sync();
  } finally { await parent.close(); }
}

/** Local-only injected ports; caller owns authorized UI and evidence producers.
 * inspectActualControl must perform no navigation or writes and return only the
 * minimized schema above. clickExactlyOnce must recheck the same literal control
 * binding with revalidateGuestCardClick on fresh complete evidence immediately
 * before its single dispatch, honor the supplied AbortSignal
 * and deadline, and never enter card data, accept terms, retry or submit payment.
 * Its response may attest only that a guest form is visibly open, not an order.
 * A preexisting owner0700 fence directory is required. No listener or directory
 * is installed here; live filesystem/port wiring requires separate review.
 */
export async function prepareExactGuestCard(ports, scope, {
  enablePreparation = false, fenceDirectory = null, signal = null,
  clock = () => Date.now(), timeoutMs = 30_000,
} = {}) {
  if (enablePreparation !== true) return stopped('guest_card_preparation_disabled');
  const now = clock();
  if (!number(now) || !exactScope(scope, now / 1000) ||
      !ports || typeof ports.inspectActualControl !== 'function' ||
      typeof ports.clickExactlyOnce !== 'function' ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > GUEST_CARD_MAX_PREPARATION_MS) {
    return stopped('guest_card_scope_or_ports_unverified');
  }
  scope = Object.freeze({ ...scope });
  const controller = new AbortController();
  const onAbort = () => controller.abort('cancelled');
  if (signal?.aborted) return stopped('guest_card_cancelled');
  signal?.addEventListener('abort', onAbort, { once: true });
  let deadline = Math.min(now + timeoutMs, scope.expires_at * 1000);
  let fenced = false, inspection;
  const live = () => !controller.signal.aborted && number(clock()) && clock() < deadline &&
    exactScope(scope, clock() / 1000);
  const checked = async operation => {
    if (!live()) throw new Error('cancelled');
    let timer, abort;
    const interruption = new Promise((_, reject) => {
      abort = () => reject(new Error('cancelled'));
      controller.signal.addEventListener('abort', abort, { once: true });
      timer = setTimeout(() => { controller.abort('timed_out'); }, Math.max(1, deadline - clock()));
    });
    try {
      const value = await Promise.race([operation(), interruption]);
      if (!live()) throw new Error('cancelled');
      return value;
    } finally {
      clearTimeout(timer); controller.signal.removeEventListener('abort', abort);
    }
  };
  const read = async () => {
    const value = await checked(() => ports.inspectActualControl({ signal: controller.signal, deadline }));
    const parsed = inspect(value, scope, clock() / 1000);
    if (!parsed) throw new Error('current_evidence_unverified');
    deadline = Math.min(deadline, parsed.expiresAt);
    return parsed;
  };
  try {
    await checked(() => privateDirectory(fenceDirectory));
    const existing = await checked(() => lstat(join(fenceDirectory, GUEST_CARD_FENCE)).catch(error => {
      if (error.code === 'ENOENT') return null;
      throw error;
    }));
    if (existing) return stopped('guest_card_initialization_already_fenced', true);
    inspection = await read();
    const second = await read();
    if (second.fingerprint !== inspection.fingerprint) return stopped('guest_card_context_changed');
    // Mark uncertainty before awaiting filesystem operations. A partial write or
    // timeout can still leave a permanent slot; never infer safe retry from it.
    fenced = true;
    try { await checked(() => persistFence(fenceDirectory, scope, clock() / 1000)); }
    catch (error) {
      if (error.code === 'EEXIST') return stopped('guest_card_initialization_already_fenced', true);
      throw error;
    }
    const final = await read();
    if (final.fingerprint !== inspection.fingerprint) return stopped('guest_card_initialization_uncertain', true);
    if (!live()) throw new Error('cancelled');
    const response = await checked(() => ports.clickExactlyOnce(Object.freeze({
      url: GUEST_CARD_URL, selector: GUEST_CARD_SELECTOR, control_ref: final.ref,
      scope_digest: digest(scope), intent_id: scope.intent_id,
      inspection_fingerprint: final.fingerprint, expected_scope: scope,
    }), { signal: controller.signal, deadline, timeoutMs: Math.max(1, deadline - clock()) }));
    if (record(response, ['schema', 'outcome']) && response.schema === 'discogs_guest_card_click_v1' &&
        response.outcome === 'guest_form_visible') {
      return stopped('guest_card_form_visible_order_unverified', true, true);
    }
    return stopped('guest_card_initialization_uncertain', true);
  } catch {
    controller.abort('stopped');
    return stopped(fenced ? 'guest_card_initialization_uncertain' : 'guest_card_preflight_unverified', fenced);
  } finally {
    controller.abort('finished');
    signal?.removeEventListener('abort', onAbort);
  }
}
