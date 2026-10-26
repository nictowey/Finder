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
export const GUEST_CARD_RECOVERY_REVIEW = 'guest-card-1366950910-recovery-review.json';
export const GUEST_CARD_RECOVERY_FENCE = 'guest-card-1366950910-recovery-attempt-1.fence';
export const GUEST_CARD_NAVIGATION_REOPEN_REVIEW = 'guest-card-1366950910-navigation-reopen-review.json';
export const GUEST_CARD_NAVIGATION_REOPEN_FENCE = 'guest-card-1366950910-navigation-reopen-attempt-1.fence';
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
const RECOVERY_KEYS = ['schema', 'source', 'authorization_ref', 'original_fence_sha256',
  'original_intent_id', 'original_scope_digest', 'retry_intent_id', 'retry_scope_digest',
  'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
  'terminal_error_code', 'guest_form_opened', 'card_data_entered', 'approval_invoked',
  'capture_invoked', 'failure_observed_at', 'order_history_url', 'order_history_archived',
  'order_history_status', 'order_history_count', 'order_history_complete',
  'order_history_observed_at', 'reviewed_at', 'expires_at', 'max_additional_attempts'];
const NAVIGATION_KEYS = ['schema', 'source', 'authorization_ref', 'original_fence_sha256',
  'original_intent_id', 'original_scope_digest', 'recovery_review_sha256', 'recovery_fence_sha256',
  'recovery_intent_id', 'recovery_scope_digest', 'reopen_intent_id', 'reopen_scope_digest',
  'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total', 'reason',
  'prior_form_visible', 'form_observed_at', 'navigation_kind', 'navigation_observed_at',
  'card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked',
  'current_form_present', 'current_checkout_normal', 'resubmission_prompt', 'challenge_present',
  'order_history_url', 'order_history_archived', 'order_history_status', 'order_history_count',
  'order_history_complete', 'order_history_observed_at', 'reviewed_at', 'expires_at',
  'max_additional_attempts'];

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

/** Pure digest for the operator's exact immutable retry scope, not authorization. */
export function guestCardScopeDigest(scope, { now = Date.now() / 1000 } = {}) {
  return number(now) && exactScope(scope, now) ? digest(scope) : null;
}
function checkoutEvidence(e, scope, now) {
  if (!record(e, EVIDENCE_KEYS) || e.source !== 'trusted_current_checkout' ||
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
  return { stable, expiresAt: e.expires_at * 1000 };
}
function inspect(value, scope, now) {
  if (!record(value, ['schema', 'url', 'access', 'control', 'evidence']) ||
      value.schema !== 'discogs_guest_card_inspection_v1' || value.url !== GUEST_CARD_URL ||
      value.access !== 'normal' || !record(value.control, CONTROL_KEYS)) return null;
  const c = value.control, verified = checkoutEvidence(value.evidence, scope, now);
  if (!verified || c.count !== 1 || c.visible !== true || c.enabled !== true || c.role !== 'link' ||
      c.funding_source !== 'card' || c.aria_label !== 'Debit or Credit Card' ||
      c.aria_disabled !== 'false' || !uuid(c.control_ref)) return null;
  return Object.freeze({ ref: c.control_ref, expiresAt: verified.expiresAt,
    fingerprint: digest({ control: c, evidence: verified.stable }) });
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
async function persistRecord(directory, filename, value) {
  await privateDirectory(directory);
  const parent = await open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const file = await open(join(directory, filename),
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    const content = JSON.stringify(value) + '\n';
    try {
      await file.writeFile(content);
      await file.sync();
    } finally { await file.close(); }
    await parent.sync();
    return createHash('sha256').update(content).digest('hex');
  } finally { await parent.close(); }
}
async function persistFence(directory, scope, now) {
  return persistRecord(directory, GUEST_CARD_FENCE, {
    schema: 'discogs_guest_card_init_fence_v1', intent_id: scope.intent_id,
    scope_digest: digest(scope), created_at: now, release_id: scope.release_id,
    listing_id: scope.listing_id, state: 'initialization_outcome_unknown',
  });
}

async function ownerRecord(directory, filename, maxBytes = 4096) {
  await privateDirectory(directory);
  const file = await open(join(directory, filename), constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() ||
        (info.mode & 0o7777) !== 0o600 || info.size > maxBytes) throw new Error('invalid_record');
    const bytes = await file.readFile();
    if (bytes.length > maxBytes) throw new Error('invalid_record');
    return { value: JSON.parse(bytes.toString('utf8')),
      hash: createHash('sha256').update(bytes).digest('hex') };
  } finally { await file.close(); }
}
function historicalReviewMatches(review, original) {
  const old = original.value;
  return record(old, ['schema', 'intent_id', 'scope_digest', 'created_at', 'release_id',
    'listing_id', 'state']) && old.schema === 'discogs_guest_card_init_fence_v1' &&
    uuid(old.intent_id) && hash(old.scope_digest) && number(old.created_at) &&
    old.release_id === 3422100 && old.listing_id === '1366950910' &&
    old.state === 'initialization_outcome_unknown' && record(review, RECOVERY_KEYS) &&
    review.schema === 'discogs_guest_card_recovery_review_v1' &&
    review.source === 'operator_reviewed_normal_discogs_ui' && uuid(review.authorization_ref) &&
    review.original_fence_sha256 === original.hash &&
    review.original_intent_id === old.intent_id && review.original_scope_digest === old.scope_digest &&
    uuid(review.retry_intent_id) && hash(review.retry_scope_digest) &&
    review.release_id === 3422100 && review.listing_id === '1366950910' &&
    review.seller === 'Mailman21842' && review.quantity === 1 && review.currency === 'USD' &&
    review.max_total === '5.05' && review.terminal_error_code === 'SESSION_EXPIRED' &&
    review.guest_form_opened === false && review.card_data_entered === false &&
    review.approval_invoked === false && review.capture_invoked === false &&
    review.order_history_url === 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' &&
    review.order_history_archived === 'All' && review.order_history_status === 'All' &&
    review.order_history_count === 0 && review.order_history_complete === true &&
    review.max_additional_attempts === 1 && number(review.failure_observed_at) &&
    number(review.order_history_observed_at) && number(review.reviewed_at) && number(review.expires_at) &&
    old.created_at <= review.failure_observed_at &&
    review.failure_observed_at <= review.order_history_observed_at &&
    review.order_history_observed_at <= review.reviewed_at &&
    review.reviewed_at - review.order_history_observed_at <= 300 &&
    review.reviewed_at < review.expires_at && review.expires_at - review.reviewed_at <= 300;
}
function recoveryMatches(review, original, scope, now) {
  return historicalReviewMatches(review, original) && review.authorization_ref === scope.approval_ref &&
    review.retry_intent_id === scope.intent_id && review.retry_scope_digest === digest(scope) &&
    review.reviewed_at <= now && now - review.order_history_observed_at <= 300 &&
    now < review.expires_at && review.expires_at <= scope.expires_at;
}
function historicalChainMatches(chain) {
  const { original, review, spent } = chain, attempt = spent.value;
  return historicalReviewMatches(review.value, original) &&
    record(attempt, ['schema', 'attempt', 'original_fence_sha256', 'reconciliation_sha256',
      'intent_id', 'scope_digest', 'created_at', 'state']) &&
    attempt.schema === 'discogs_guest_card_recovery_attempt_v1' && attempt.attempt === 1 &&
    attempt.original_fence_sha256 === original.hash && attempt.reconciliation_sha256 === review.hash &&
    attempt.intent_id === review.value.retry_intent_id && attempt.scope_digest === review.value.retry_scope_digest &&
    attempt.state === 'initialization_outcome_unknown' && number(attempt.created_at) &&
    review.value.reviewed_at <= attempt.created_at && attempt.created_at < review.value.expires_at &&
    attempt.created_at - review.value.order_history_observed_at <= 300;
}
function navigationMatches(review, chain, scope, now) {
  return historicalChainMatches(chain) && record(review, NAVIGATION_KEYS) &&
    review.schema === 'discogs_guest_card_navigation_reopen_review_v1' &&
    review.source === 'operator_reviewed_normal_discogs_ui' &&
    review.authorization_ref === scope.approval_ref && review.original_fence_sha256 === chain.original.hash &&
    review.original_intent_id === chain.original.value.intent_id &&
    review.original_scope_digest === chain.original.value.scope_digest &&
    review.recovery_review_sha256 === chain.review.hash && review.recovery_fence_sha256 === chain.spent.hash &&
    review.recovery_intent_id === chain.spent.value.intent_id &&
    review.recovery_scope_digest === chain.spent.value.scope_digest &&
    review.reopen_intent_id === scope.intent_id && review.reopen_scope_digest === digest(scope) &&
    review.release_id === scope.release_id && review.listing_id === scope.listing_id &&
    review.seller === scope.seller && review.quantity === 1 && review.currency === 'USD' &&
    review.max_total === '5.05' && review.reason === 'form_lost_by_same_tab_navigation' &&
    review.prior_form_visible === true && review.navigation_kind === 'same_tab_blank_then_history_back' &&
    review.card_data_entered === false && review.pay_invoked === false &&
    review.approval_invoked === false && review.capture_invoked === false &&
    review.current_form_present === false && review.current_checkout_normal === true &&
    review.resubmission_prompt === false && review.challenge_present === false &&
    review.order_history_url === 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' &&
    review.order_history_archived === 'All' && review.order_history_status === 'All' &&
    review.order_history_count === 0 && review.order_history_complete === true &&
    review.max_additional_attempts === 1 && number(review.form_observed_at) &&
    number(review.navigation_observed_at) && number(review.order_history_observed_at) &&
    number(review.reviewed_at) && number(review.expires_at) &&
    chain.spent.value.created_at <= review.form_observed_at &&
    review.form_observed_at <= review.navigation_observed_at &&
    review.navigation_observed_at <= review.order_history_observed_at &&
    review.order_history_observed_at <= review.reviewed_at && review.reviewed_at <= now &&
    now - review.order_history_observed_at <= 300 && now < review.expires_at &&
    review.expires_at - review.reviewed_at <= 300 && review.expires_at <= scope.expires_at;
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
 * recoveryReview is a separately authorized operator reconciliation, not an
 * automatic retry decision. It permits only the fixed first-recovery slot after
 * SESSION_EXPIRED and complete current all-status/all-archive zero-order review,
 * with no form/card/approval/capture. Original bytes are never rewritten. The
 * immutable review itself consumes that exceptional slot if execution crashes
 * before the additional attempt fence is written. A separately reviewed, fixed
 * navigationReopenReview may authorize one final reopening after the prior form
 * was lost by same-tab navigation with no card/Pay/approval/capture. It binds the
 * unchanged historical chain and fresh normal checkout/all-orders evidence; it
 * is mutually exclusive with recoveryReview and cannot chain or reset a slot.
 */
export async function prepareExactGuestCard(ports, scope, {
  enablePreparation = false, fenceDirectory = null, signal = null,
  clock = () => Date.now(), timeoutMs = 30_000, recoveryReview = null, navigationReopenReview = null,
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
  if (navigationReopenReview !== null) {
    if (recoveryReview !== null || !record(navigationReopenReview, NAVIGATION_KEYS)) {
      return stopped('guest_card_navigation_reopen_unverified');
    }
    navigationReopenReview = Object.freeze({ ...navigationReopenReview });
  }
  if (recoveryReview !== null) {
    if (!record(recoveryReview, RECOVERY_KEYS)) return stopped('guest_card_recovery_unverified');
    recoveryReview = Object.freeze({ ...recoveryReview });
  }
  const controller = new AbortController();
  const onAbort = () => controller.abort('cancelled');
  if (signal?.aborted) return stopped('guest_card_cancelled');
  signal?.addEventListener('abort', onAbort, { once: true });
  let deadline = Math.min(now + timeoutMs, scope.expires_at * 1000);
  let fenced = false, inspection, original = null, recoveryHashes = null, navigation = null;
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
  const recoveryCurrent = async () => {
    if (!original) return;
    if (navigation) {
      const chain = { original: await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_FENCE)),
        review: await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_REVIEW)),
        spent: await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_FENCE)) };
      const currentReview = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_NAVIGATION_REOPEN_REVIEW));
      const currentSpent = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_NAVIGATION_REOPEN_FENCE));
      if (chain.original.hash !== original.hash || chain.review.hash !== navigation.review.hash ||
          chain.spent.hash !== navigation.spent.hash || currentReview.hash !== recoveryHashes.review ||
          currentSpent.hash !== recoveryHashes.spent ||
          !navigationMatches(currentReview.value, chain, scope, clock() / 1000)) {
        throw new Error('navigation_reopen_changed');
      }
      return;
    }
    const old = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_FENCE));
    const review = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_REVIEW));
    const spent = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_FENCE));
    if (old.hash !== original.hash || review.hash !== recoveryHashes.review ||
        spent.hash !== recoveryHashes.spent ||
        !recoveryMatches(review.value, old, scope, clock() / 1000)) throw new Error('recovery_changed');
  };
  try {
    await checked(() => privateDirectory(fenceDirectory));
    const existing = await checked(() => lstat(join(fenceDirectory, GUEST_CARD_FENCE)).catch(error => {
      if (error.code === 'ENOENT') return null;
      throw error;
    }));
    if (existing) {
      if (!recoveryReview && !navigationReopenReview) return stopped('guest_card_initialization_already_fenced', true);
      original = await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_FENCE));
      if (navigationReopenReview) {
        navigation = { original,
          review: await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_REVIEW)),
          spent: await checked(() => ownerRecord(fenceDirectory, GUEST_CARD_RECOVERY_FENCE)) };
        if (!navigationMatches(navigationReopenReview, navigation, scope, clock() / 1000)) {
          return stopped('guest_card_navigation_reopen_unverified', true);
        }
      } else if (!recoveryMatches(recoveryReview, original, scope, clock() / 1000)) {
        return stopped('guest_card_recovery_unverified', true);
      }
      const currentReview = navigationReopenReview || recoveryReview;
      deadline = Math.min(deadline, currentReview.expires_at * 1000,
        (currentReview.order_history_observed_at + 300) * 1000);
      const slots = navigation ? [GUEST_CARD_NAVIGATION_REOPEN_REVIEW, GUEST_CARD_NAVIGATION_REOPEN_FENCE] :
        [GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE];
      for (const name of slots) {
        if (await checked(() => lstat(join(fenceDirectory, name)).catch(error => {
          if (error.code === 'ENOENT') return null;
          throw error;
        }))) return stopped(navigation ? 'guest_card_navigation_reopen_already_fenced' :
          'guest_card_recovery_already_fenced', true);
      }
    } else if (recoveryReview || navigationReopenReview) return stopped(navigationReopenReview ?
      'guest_card_navigation_reopen_unverified' : 'guest_card_recovery_unverified');
    inspection = await read();
    const second = await read();
    if (second.fingerprint !== inspection.fingerprint) return stopped('guest_card_context_changed');
    // Mark uncertainty before awaiting filesystem operations. A partial write or
    // timeout can still leave a permanent slot; never infer safe retry from it.
    fenced = true;
    try {
      if (navigation) {
        const reviewHash = await checked(() => persistRecord(fenceDirectory,
          GUEST_CARD_NAVIGATION_REOPEN_REVIEW, navigationReopenReview));
        const spentHash = await checked(() => persistRecord(fenceDirectory, GUEST_CARD_NAVIGATION_REOPEN_FENCE, {
          schema: 'discogs_guest_card_navigation_reopen_attempt_v1', attempt: 1,
          original_fence_sha256: original.hash, recovery_review_sha256: navigation.review.hash,
          recovery_fence_sha256: navigation.spent.hash, reconciliation_sha256: reviewHash,
          intent_id: scope.intent_id, scope_digest: digest(scope),
          created_at: clock() / 1000, state: 'initialization_outcome_unknown',
        }));
        recoveryHashes = { review: reviewHash, spent: spentHash };
        await recoveryCurrent();
      } else if (original) {
        const reviewHash = await checked(() => persistRecord(fenceDirectory,
          GUEST_CARD_RECOVERY_REVIEW, recoveryReview));
        const spentHash = await checked(() => persistRecord(fenceDirectory, GUEST_CARD_RECOVERY_FENCE, {
          schema: 'discogs_guest_card_recovery_attempt_v1', attempt: 1,
          original_fence_sha256: original.hash, reconciliation_sha256: reviewHash,
          intent_id: scope.intent_id, scope_digest: digest(scope),
          created_at: clock() / 1000, state: 'initialization_outcome_unknown',
        }));
        recoveryHashes = { review: reviewHash, spent: spentHash };
        await recoveryCurrent();
      } else await checked(() => persistFence(fenceDirectory, scope, clock() / 1000));
    }
    catch (error) {
      if (error.code === 'EEXIST') return stopped(navigation ? 'guest_card_navigation_reopen_already_fenced' :
        original ? 'guest_card_recovery_already_fenced' :
        'guest_card_initialization_already_fenced', true);
      throw error;
    }
    const final = await read();
    if (final.fingerprint !== inspection.fingerprint) return stopped('guest_card_initialization_uncertain', true);
    await recoveryCurrent();
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

const READY_KEYS = ['source', 'ready', 'scope_digest', 'observed_at', 'expires_at'];
const FAILURE_KEYS = ['source', 'last_error_code', 'release_id', 'listing_id', 'seller',
  'quantity', 'currency', 'max_total', 'card_data_entered', 'pay_invoked', 'approval_invoked',
  'capture_invoked', 'failure_observed_at', 'order_history_url', 'order_history_archived',
  'order_history_status', 'order_history_count', 'order_history_complete',
  'order_history_observed_at', 'reviewed_at', 'expires_at'];
const SESSION_PAGE_KEYS = ['schema', 'source', 'url', 'document_ref', 'access', 'history_restore',
  'resubmission_prompt', 'extra_agreement_required', 'selected_shipping_ref',
  'selected_shipping_label', 'go_to_checkout', 'evidence'];
let freshCheckoutBusy = false;

/** Offline coordinator only. The injected normal-UI ports are not installed.
 * It opens the visible cart, then its verified visible Go to Checkout control;
 * no SDK/payment control, direct provider request, card data or fence is used.
 * Ports must await their action, honor signal/deadline, and verify the literal
 * semantic control/current document before dispatch. Their document refs must
 * represent actually observed documents, not invented fresh provenance.
 * ALL mutating ports (openCartViaVisibleControl and clickGoToCheckout) MUST call
 * options.revalidateBeforeDispatch() immediately before their normal UI effect,
 * after any await. False means no effect: the guard also aborts their signal.
 * userReady is an explicit current human-readiness token bound to this scope;
 * changing/revoking it or aborting the signal stops subsequent actions.
 */
export async function prepareFreshGuestCheckoutSession(ports, scope, {
  userReady, priorFailure, signal = null, clock = () => Date.now(), timeoutMs = 30_000,
} = {}) {
  const stop = code => ({ schema: 'discogs_fresh_guest_checkout_v1', status: 'blocked', code,
    initialization_allowed: false, payment_form_verified: false, purchase_allowed: false, may_retry: false });
  const now = clock() / 1000;
  if (freshCheckoutBusy) return stop('fresh_checkout_in_progress');
  if (!number(now) || !exactScope(scope, now) || !record(userReady, READY_KEYS) ||
      userReady.source !== 'explicit_current_user_ready' || userReady.ready !== true ||
      userReady.scope_digest !== digest(scope) || !number(userReady.observed_at) ||
      !number(userReady.expires_at) || !(userReady.observed_at <= now && now < userReady.expires_at) ||
      userReady.expires_at - userReady.observed_at > 180 || !record(priorFailure, FAILURE_KEYS) ||
      priorFailure.source !== 'operator_reviewed_normal_discogs_ui' ||
      priorFailure.last_error_code !== 'SESSION_EXPIRED' ||
      ['release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total'].some(key => priorFailure[key] !== scope[key]) ||
      ['card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'].some(key => priorFailure[key] !== false) ||
      priorFailure.order_history_url !== 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' ||
      priorFailure.order_history_archived !== 'All' || priorFailure.order_history_status !== 'All' ||
      priorFailure.order_history_count !== 0 || priorFailure.order_history_complete !== true ||
      !['failure_observed_at', 'order_history_observed_at', 'reviewed_at', 'expires_at'].every(key => number(priorFailure[key])) ||
      !(priorFailure.failure_observed_at <= priorFailure.order_history_observed_at &&
        priorFailure.order_history_observed_at <= priorFailure.reviewed_at && priorFailure.reviewed_at <= now &&
        now - priorFailure.order_history_observed_at < 300 && now < priorFailure.expires_at &&
        priorFailure.expires_at - priorFailure.reviewed_at <= 300) ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 90_000 ||
      !ports || ['openCartViaVisibleControl', 'inspectCurrentCart', 'clickGoToCheckout', 'inspectFreshCheckout']
        .some(key => typeof ports[key] !== 'function')) return stop('fresh_checkout_preconditions_unverified');
  scope = Object.freeze({ ...scope }); priorFailure = Object.freeze({ ...priorFailure });
  const readiness = canonical(userReady), controller = new AbortController(), pending = new Set();
  let deadline = Math.min(clock() + timeoutMs, scope.expires_at * 1000, userReady.expires_at * 1000,
    priorFailure.expires_at * 1000, (priorFailure.order_history_observed_at + 300) * 1000);
  const cancel = () => controller.abort('cancelled');
  const live = () => !signal?.aborted && !controller.signal.aborted && clock() < deadline &&
    record(userReady, READY_KEYS) && canonical(userReady) === readiness;
  const checked = async operation => {
    if (!live()) throw new Error('stopped');
    let timer, onAbort;
    const interrupted = new Promise((_, reject) => {
      onAbort = () => reject(new Error('stopped'));
      controller.signal.addEventListener('abort', onAbort, { once: true });
      timer = setTimeout(() => controller.abort('timed_out'), Math.max(1, deadline - clock()));
    });
    try {
      const work = Promise.resolve(operation()); pending.add(work);
      work.then(() => pending.delete(work), () => pending.delete(work));
      const value = await Promise.race([work, interrupted]);
      if (!live()) throw new Error('stopped');
      return value;
    } finally { clearTimeout(timer); controller.signal.removeEventListener('abort', onAbort); }
  };
  const revalidateBeforeDispatch = () => {
    if (live()) return true;
    controller.abort('readiness_or_deadline_lost');
    return false;
  };
  const options = () => ({ signal: controller.signal, deadline, expected_scope: scope,
    revalidateBeforeDispatch });
  const page = (value, url, documentRef, cart) => {
    if (!record(value, SESSION_PAGE_KEYS) || value.schema !== 'discogs_guest_session_page_v1' ||
        !['trusted_normal_current_dom', 'operator_verified_current_screen'].includes(value.source) || value.url !== url ||
        value.document_ref !== documentRef || value.access !== 'normal' || value.history_restore !== false ||
        value.resubmission_prompt !== false || value.extra_agreement_required !== false ||
        !uuid(value.selected_shipping_ref) || typeof value.selected_shipping_label !== 'string' ||
        value.selected_shipping_label.trim().length < 1 || value.selected_shipping_label.length > 100) throw new Error('unverified_page');
    const evidence = checkoutEvidence(value.evidence, scope, clock() / 1000), control = value.go_to_checkout;
    if (!evidence || (cart ? !record(control, ['action_kind', 'label', 'count', 'visible', 'enabled', 'control_ref']) ||
        control.action_kind !== 'normal_go_to_checkout' || control.label !== 'Go to Checkout' ||
        control.count !== 1 || control.visible !== true || control.enabled !== true || !uuid(control.control_ref) :
        control !== null)) throw new Error('unverified_cart_or_checkout');
    deadline = Math.min(deadline, evidence.expiresAt);
    return { ...value, fingerprint: digest({ ...value, evidence: evidence.stable }) };
  };
  freshCheckoutBusy = true; signal?.addEventListener('abort', cancel, { once: true });
  try {
    const opened = await checked(() => ports.openCartViaVisibleControl(options()));
    if (!record(opened, ['action', 'document_ref']) || opened.action !== 'visible_cart_control' ||
        !uuid(opened.document_ref)) throw new Error('cart_navigation_unverified');
    const cart = page(await checked(() => ports.inspectCurrentCart(options())),
      'https://www.discogs.com/sell/cart/', opened.document_ref, true);
    const binding = Object.freeze({ action_kind: 'normal_go_to_checkout', label: 'Go to Checkout',
      control_ref: cart.go_to_checkout.control_ref, from_document_ref: cart.document_ref,
      cart_fingerprint: cart.fingerprint, expected_scope: scope });
    const transition = await checked(() => ports.clickGoToCheckout(binding, options()));
    if (!record(transition, ['action', 'control_ref', 'from_document_ref', 'to_document_ref', 'cart_fingerprint']) ||
        transition.action !== 'normal_go_to_checkout' || transition.control_ref !== binding.control_ref ||
        transition.from_document_ref !== cart.document_ref || transition.cart_fingerprint !== cart.fingerprint ||
        !uuid(transition.to_document_ref) || transition.to_document_ref === cart.document_ref) throw new Error('fresh_navigation_unverified');
    const checkout = page(await checked(() => ports.inspectFreshCheckout(options())),
      GUEST_CARD_URL, transition.to_document_ref, false);
    if (checkout.source !== cart.source || checkout.selected_shipping_ref !== cart.selected_shipping_ref ||
        checkout.selected_shipping_label !== cart.selected_shipping_label || !live()) throw new Error('checkout_changed');
    return { ...stop('fresh_checkout_provenance_observed'), status: 'prepared_review_only',
      provenance: { source: 'normal_cart_to_fresh_checkout', observation_basis: cart.source, scope_digest: digest(scope),
        cart_document_ref: cart.document_ref, checkout_document_ref: checkout.document_ref,
        observed_at: clock() / 1000, expires_at: deadline / 1000 } };
  } catch { return stop('fresh_checkout_preparation_stopped'); }
  finally {
    controller.abort('finished'); signal?.removeEventListener('abort', cancel);
    if (pending.size) void Promise.allSettled([...pending]).then(() => { freshCheckoutBusy = false; });
    else freshCheckoutBusy = false;
  }
}
