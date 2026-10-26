/** Manual operator transport, not an autonomous browser/payment adapter.
 * Only metadata crosses stdio. Actual UI actions belong to the reviewed CUA
 * operator, which must check each deadline and one-use request before acting.
 */
import { constants } from 'node:fs';
import { open, readdir, lstat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { prepareFreshGuestCheckoutSession, guestCardScopeDigest, validateGuestCardInspection,
  revalidateGuestCardClick, GUEST_CARD_URL, GUEST_CARD_SELECTOR, GUEST_CARD_FENCE,
  GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE,
  GUEST_CARD_NAVIGATION_REOPEN_REVIEW, GUEST_CARD_NAVIGATION_REOPEN_FENCE } from './discogs_guest_card_prepare.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const LEGACY = [GUEST_CARD_FENCE, GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE,
  GUEST_CARD_NAVIGATION_REOPEN_REVIEW, GUEST_CARD_NAVIGATION_REOPEN_FENCE];
const sha = value => createHash('sha256').update(value).digest('hex');
const uuid = value => typeof value === 'string' && UUID.test(value);
const hash = value => typeof value === 'string' && HASH.test(value);
const num = value => typeof value === 'number' && Number.isFinite(value);
const shape = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const encoded = value => JSON.stringify(value) + '\n';

const BINDING_KEYS = ['intent_id', 'ready_ref', 'scope_digest', 'request_id', 'stage', 'deadline', 'from_document_ref'];
const PROOF_KEYS = ['observed_at', 'exact_scope', 'quote', 'destination', 'terms', 'no_card', 'normal'];
const TEMPLATE_KEYS = ['schema', 'scope', 'ready_ref', 'scope_digest', 'availability_deadline', 'refs', 'cart', 'checkout', 'card_control'];
function metadataOnly(value, seen = new Set()) {
  if (value === null || ['string', 'boolean'].includes(typeof value) || num(value)) return true;
  if (!value || typeof value !== 'object' || Array.isArray(value) || seen.has(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false;
  seen.add(value);
  return Reflect.ownKeys(value).every(key => typeof key === 'string' &&
    Object.getOwnPropertyDescriptor(value, key).enumerable &&
    Object.hasOwn(Object.getOwnPropertyDescriptor(value, key), 'value') && metadataOnly(value[key], seen));
}
function templatesFor(scope, readyRef, availabilityDeadline, refs) {
  const scopeHash = guestCardScopeDigest(scope, { now: scope?.issued_at });
  if (!scopeHash || !uuid(readyRef) || !shape(refs, ['cart', 'checkout', 'shipping', 'go', 'card']) ||
      !Object.values(refs).every(uuid) || refs.cart === refs.checkout || !num(availabilityDeadline) ||
      availabilityDeadline > scope.expires_at * 1000 || availabilityDeadline <= scope.issued_at * 1000) {
    throw new Error('operator_templates_unverified');
  }
  const evidence = { source: 'trusted_current_checkout', release_id: scope.release_id, listing_id: scope.listing_id,
    seller: scope.seller, quantity: 1, item_count: 1, seller_count: 1, rules_revision: scope.rules_revision,
    rules_current: true, criteria_satisfied: true, already_owned: false, existing_order: false,
    uncertain_prior_action: false, duplicate_guard_verified: true, approval_ref: scope.approval_ref,
    authorization_current: true, authorization_revoked: false, currency: 'USD', item: '0.10', shipping: '4.95',
    tax: null, tax_verified: false, fees: null, fees_verified: false, total: '5.05',
    amount_basis: 'explicit_merchant_checkout_total', preparation_quote_verified: true,
    final_payment_quote_verified: false, deferred_total_notice: false, shipping_selection_verified: true,
    quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref, destination_verified: true,
    terms_fingerprint: scope.terms_fingerprint, terms_verified: true, observed_at: null,
    expires_at: availabilityDeadline / 1000 };
  const page = cart => ({ schema: 'discogs_guest_session_page_v1', source: 'operator_verified_current_screen',
    url: cart ? 'https://www.discogs.com/sell/cart/' : GUEST_CARD_URL,
    document_ref: cart ? refs.cart : refs.checkout, access: 'normal', history_restore: false,
    resubmission_prompt: false, extra_agreement_required: false, selected_shipping_ref: refs.shipping,
    selected_shipping_label: 'Economy - Media Mail', go_to_checkout: cart ? {
      action_kind: 'normal_go_to_checkout', label: 'Go to Checkout', count: 1, visible: true, enabled: true,
      control_ref: refs.go } : null, evidence: { ...evidence } });
  return { schema: 'discogs_guest_operator_templates_v1', scope: { ...scope }, ready_ref: readyRef,
    scope_digest: scopeHash, availability_deadline: availabilityDeadline, refs: { ...refs },
    cart: page(true), checkout: page(false), card_control: { count: 1, visible: true, enabled: true,
      role: 'link', funding_source: 'card', aria_label: 'Debit or Credit Card', aria_disabled: 'false', control_ref: refs.card } };
}

/** Pure, serializable un-attested metadata. No readiness, file or UI effect. */
export function prepareOperatorAckTemplates(start, refs) {
  if (!metadataOnly(start) || !metadataOnly(refs) ||
      !shape(start, ['type', 'ready_ref', 'scope', 'userReady', 'priorFailure', 'expected_latest_hash']) ||
      start.type !== 'start' || !hash(start.expected_latest_hash)) throw new Error('operator_templates_unverified');
  return templatesFor(start.scope, start.ready_ref, Math.min(start.scope?.expires_at * 1000,
    start.userReady?.expires_at * 1000, start.priorFailure?.expires_at * 1000,
    (start.priorFailure?.order_history_observed_at + 300) * 1000), refs);
}

/** Pure ACK builder; every true flag is a fresh operator attestation, not an inference. */
export function buildOperatorAck(request, templates, operatorProof) {
  if (![request, templates, operatorProof].every(value => metadataOnly(value)) || !shape(templates, TEMPLATE_KEYS)) {
    throw new Error('operator_ack_unverified');
  }
  const expected = templatesFor(templates.scope, templates.ready_ref, templates.availability_deadline, templates.refs);
  if (encoded(expected) !== encoded(templates)) throw new Error('operator_templates_changed');
  const guest = request.stage === 'open_guest_card', go = request.stage === 'go_to_checkout';
  const extras = guest ? ['control_ref', 'inspection_fingerprint'] : go ? ['control_ref', 'cart_fingerprint'] : [];
  const now = Date.now() / 1000;
  if (!shape(request, ['type', ...BINDING_KEYS, ...extras]) || request.type !== 'operator_action' ||
      !['open_cart', 'go_to_checkout', 'open_guest_card'].includes(request.stage) || !uuid(request.request_id) ||
      request.intent_id !== templates.scope.intent_id || request.ready_ref !== templates.ready_ref ||
      request.scope_digest !== templates.scope_digest || !num(request.deadline) || Date.now() >= request.deadline ||
      request.deadline > templates.availability_deadline ||
      !shape(operatorProof, [...PROOF_KEYS, ...(guest ? ['outcome'] : [])]) ||
      !PROOF_KEYS.filter(key => key !== 'observed_at').every(key => operatorProof[key] === true) ||
      !num(operatorProof.observed_at) || operatorProof.observed_at > now || now - operatorProof.observed_at > 20 ||
      request.stage === 'open_cart' && request.from_document_ref !== null ||
      go && (request.from_document_ref !== templates.refs.cart || request.control_ref !== templates.refs.go || !hash(request.cart_fingerprint)) ||
      guest && (request.from_document_ref !== templates.refs.checkout || request.control_ref !== templates.refs.card || !hash(request.inspection_fingerprint))) {
    throw new Error('operator_ack_unverified');
  }
  const base = { type: 'operator_ack', ...Object.fromEntries(BINDING_KEYS.map(key => [key, request[key]])) };
  const page = value => ({ ...value, evidence: { ...value.evidence, observed_at: operatorProof.observed_at } });
  if (request.stage === 'open_cart') return { ...base, navigation: {
    action: 'visible_cart_control', document_ref: templates.refs.cart }, cart: page(templates.cart) };
  if (go) return { ...base, transition: { action: 'normal_go_to_checkout', control_ref: request.control_ref,
    from_document_ref: templates.refs.cart, to_document_ref: templates.refs.checkout,
    cart_fingerprint: request.cart_fingerprint }, checkout: page(templates.checkout), card_control: { ...templates.card_control } };
  if (!['guest_form_visible', 'SESSION_EXPIRED', 'unknown', 'access_restricted'].includes(operatorProof.outcome)) {
    throw new Error('operator_outcome_unverified');
  }
  return { ...base, outcome: operatorProof.outcome, observed_at: operatorProof.observed_at };
}

const CLAIM_KEYS = ['type', 'ready_ref', 'scope', 'userReady', 'priorFailure', 'expected_latest_hash', 'provenance', 'inspection'];
const TEST_CLAIM_KEYS = CLAIM_KEYS.filter(key => key !== 'userReady').concat('testAuthorization');
const RESOLVED_TEST_CLAIM_KEYS = TEST_CLAIM_KEYS.concat('priorNoDispatch');
const TEST_AUTH_KEYS = ['source', 'approval_ref', 'scope_digest', 'attempt_id', 'authorized_at', 'issued_at', 'expires_at',
  'card_entry_allowed', 'payment_allowed', 'final_submission_allowed'];
const NO_DISPATCH_KEYS = ['source', 'previous_claim_sha256', 'previous_outcome_sha256', 'request_id', 'actor_result',
  'clicked', 'observed_at', 'card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'];
const CLAIM_PROVENANCE_KEYS = ['source', 'navigation', 'scope_digest', 'cart_document_ref', 'checkout_document_ref',
  'cart_observed_at', 'checkout_observed_at', 'cart_scope_verified', 'go_to_checkout_verified',
  'history_restore', 'resubmission_prompt', 'extra_agreement_required'];
const FAILURE_KEYS = ['source', 'last_error_code', 'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
  'card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked', 'failure_observed_at', 'order_history_url',
  'order_history_archived', 'order_history_status', 'order_history_count', 'order_history_complete',
  'order_history_observed_at', 'reviewed_at', 'expires_at'];
const OUTCOMES = ['guest_form_visible', 'SESSION_EXPIRED', 'unknown', 'access_restricted'];
const CLAIM_STOP_REASONS = new Map([
  ...['authorization_unverified', 'test_authorization_unverified', 'scope_or_quote_unverified', 'provenance_unverified',
    'reconciliation_required', 'prior_attempt_unresolved', 'prior_no_dispatch_unverified', 'ready_replayed', 'claim_expired', 'claim_changed',
    'legacy_changed', 'operator_ledger_changed'].map(value => [value, value]),
  ['invalid_legacy_chain', 'legacy_changed'],
]);

function requireResolvedPreviousAttempt(state, definitiveNoDispatch = false) {
  const index = state.records.findLastIndex(item => item.value.kind === 'attempt');
  if (index < 0) return;
  const outcome = state.records[index + 1]?.value;
  if (outcome?.kind !== 'outcome' || !(outcome.payload.outcome === 'SESSION_EXPIRED' ||
      definitiveNoDispatch && outcome.payload.outcome === 'unknown')) {
    throw new Error('prior_attempt_unresolved');
  }
}

function validatePriorNoDispatch(proof, state, prior) {
  const index = state.records.findLastIndex(item => item.value.kind === 'attempt');
  const attempt = state.records[index], outcome = state.records[index + 1], permit = attempt?.value.payload.permit;
  if (!shape(proof, NO_DISPATCH_KEYS) || proof.source !== 'operator_reviewed_supported_cua_no_dispatch' ||
      proof.actor_result !== 'expired_before_effect' || proof.clicked !== false ||
      ['card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'].some(key => proof[key] !== false) ||
      attempt?.value.payload.mode !== 'current_checkout_claim' || outcome?.value.kind !== 'outcome' ||
      outcome.value.payload.outcome !== 'unknown' || proof.previous_claim_sha256 !== attempt.hash ||
      proof.previous_outcome_sha256 !== outcome.hash || !uuid(proof.request_id) || proof.request_id !== permit?.request_id ||
      !num(permit?.deadline) || !num(proof.observed_at) || proof.observed_at > Date.now() / 1000 ||
      proof.observed_at * 1000 < permit.deadline || proof.observed_at < attempt.value.recorded_at ||
      !num(outcome.value.payload.observed_at) || proof.observed_at > outcome.value.payload.observed_at ||
      proof.observed_at > outcome.value.recorded_at ||
      prior.order_history_observed_at <= Math.max(proof.observed_at, state.records.at(-1).value.recorded_at)) {
    throw new Error('prior_no_dispatch_unverified');
  }
  return true;
}

function validateCurrentClaim(value, state, anchor) {
  const now = Date.now() / 1000;
  if (!metadataOnly(value) || !(shape(value, CLAIM_KEYS) || shape(value, TEST_CLAIM_KEYS) ||
      shape(value, RESOLVED_TEST_CLAIM_KEYS)) || value.type !== 'claim_current_checkout' ||
      !uuid(value.ready_ref) || value.expected_latest_hash !== state.head) throw new Error('authorization_unverified');
  const { scope, userReady: ready, priorFailure: prior, provenance: p, inspection } = value;
  const scopeHash = guestCardScopeDigest(scope), parsed = validateGuestCardInspection(inspection, scope);
  if (!scopeHash || !parsed) throw new Error('scope_or_quote_unverified');
  let authorizationExpiresAt;
  const test = value.testAuthorization;
  if (test) {
    if (!shape(test, TEST_AUTH_KEYS) || test.source !== 'explicit_user_authorized_no_submit_test' ||
        test.approval_ref !== scope.approval_ref || test.scope_digest !== scopeHash || test.attempt_id !== scope.intent_id ||
        !['authorized_at', 'issued_at', 'expires_at'].every(key => num(test[key])) ||
        !(0 <= test.authorized_at && test.authorized_at <= test.issued_at && test.issued_at <= now && now < test.expires_at &&
          test.expires_at - test.issued_at <= 300) ||
        ['card_entry_allowed', 'payment_allowed', 'final_submission_allowed'].some(key => test[key] !== false)) {
      throw new Error('test_authorization_unverified');
    }
    authorizationExpiresAt = test.expires_at;
  } else {
    if (!shape(ready, ['source', 'ready', 'scope_digest', 'observed_at', 'expires_at']) ||
      ready.source !== 'explicit_current_user_ready' || ready.ready !== true || ready.scope_digest !== scopeHash ||
      !num(ready.observed_at) || !num(ready.expires_at) || !(ready.observed_at <= now && now < ready.expires_at) ||
      ready.expires_at - ready.observed_at > 300) throw new Error('authorization_unverified');
    authorizationExpiresAt = ready.expires_at;
  }
  if (!shape(prior, FAILURE_KEYS) ||
      prior.source !== 'operator_reviewed_normal_discogs_ui' || prior.last_error_code !== 'SESSION_EXPIRED' ||
      ['release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total'].some(key => prior[key] !== scope[key]) ||
      ['card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'].some(key => prior[key] !== false) ||
      prior.order_history_url !== 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' ||
      prior.order_history_archived !== 'All' || prior.order_history_status !== 'All' || prior.order_history_count !== 0 ||
      prior.order_history_complete !== true || !['failure_observed_at', 'order_history_observed_at', 'reviewed_at', 'expires_at']
        .every(key => num(prior[key])) || !(prior.failure_observed_at <= prior.order_history_observed_at &&
        prior.order_history_observed_at <= prior.reviewed_at && prior.reviewed_at <= now &&
        now - prior.order_history_observed_at < 300 && now < prior.expires_at &&
        prior.expires_at - prior.reviewed_at <= 300)) throw new Error('reconciliation_required');
  if (!shape(p, CLAIM_PROVENANCE_KEYS) ||
      p.source !== 'operator_verified_current_screen' || p.navigation !== 'normal_cart_to_fresh_checkout' ||
      p.scope_digest !== scopeHash || !uuid(p.cart_document_ref) || !uuid(p.checkout_document_ref) ||
      p.cart_document_ref === p.checkout_document_ref || p.cart_scope_verified !== true || p.go_to_checkout_verified !== true ||
      ['history_restore', 'resubmission_prompt', 'extra_agreement_required'].some(key => p[key] !== false) ||
      !num(p.cart_observed_at) || !num(p.checkout_observed_at) ||
      !(prior.order_history_observed_at <= p.cart_observed_at && p.cart_observed_at <= p.checkout_observed_at &&
        p.checkout_observed_at <= now && now - p.cart_observed_at < 300 && now - p.checkout_observed_at < 20) ||
      p.checkout_observed_at !== inspection.evidence.observed_at) throw new Error('provenance_unverified');
  let lastEffectAt = anchor.lastEffectAt;
  for (const item of state.records) {
    const { kind, payload, recorded_at } = item.value;
    if (kind === 'ready' && (payload.ready_ref === value.ready_ref || payload.intent_id === scope.intent_id)) throw new Error('ready_replayed');
    if (kind === 'attempt') lastEffectAt = Math.max(lastEffectAt, recorded_at);
    if (kind === 'outcome') lastEffectAt = Math.max(lastEffectAt, payload.observed_at || 0);
  }
  const definitiveNoDispatch = Object.hasOwn(value, 'priorNoDispatch') ? validatePriorNoDispatch(value.priorNoDispatch, state, prior) : false;
  requireResolvedPreviousAttempt(state, definitiveNoDispatch);
  if (!definitiveNoDispatch && prior.failure_observed_at < lastEffectAt || prior.order_history_observed_at <
      (state.records.at(-1)?.value.recorded_at || lastEffectAt)) throw new Error('reconciliation_required');
  const deadline = Math.min(scope.expires_at * 1000, authorizationExpiresAt * 1000, prior.expires_at * 1000,
    (prior.order_history_observed_at + 300) * 1000, parsed.expires_at * 1000,
    ...(test ? [] : [(p.checkout_observed_at + 20) * 1000]));
  if (Date.now() >= deadline) throw new Error('claim_expired');
  return { scopeHash, parsed, deadline };
}

/** One metadata JSON then exit. Neither command performs any browser action. */
export async function runCurrentCheckoutClaim({ mode = 'claim', directory = '/state',
  input = process.stdin, output = process.stdout } = {}) {
  let lock;
  const emit = value => new Promise((resolve, reject) => output.write(encoded(value), error => error ? reject(error) : resolve()));
  try {
    if (!['claim', 'outcome'].includes(mode)) throw new Error('invalid_mode');
    let text = '';
    for await (const chunk of input) {
      text += chunk.toString(); if (Buffer.byteLength(text) > 16_384) throw new Error('invalid_input');
    }
    const value = JSON.parse(text);
    if (!metadataOnly(value)) throw new Error('invalid_input');
    await privateDirectory(directory); lock = await lockDirectory(directory);
    const anchor = await anchors(directory), state = await ledger(directory, anchor);
    if (mode === 'outcome') {
      if (!shape(value, ['type', 'intent_id', 'ready_ref', 'scope_digest', 'claim_sha256', 'outcome', 'observed_at']) ||
          value.type !== 'record_claim_outcome' || !uuid(value.intent_id) || !uuid(value.ready_ref) ||
          !hash(value.scope_digest) || !hash(value.claim_sha256) || !OUTCOMES.includes(value.outcome) ||
          !num(value.observed_at) || value.observed_at > Date.now() / 1000) throw new Error('outcome_unverified');
      const index = state.records.findIndex(item => item.hash === value.claim_sha256), attempt = state.records[index]?.value;
      if (attempt?.kind !== 'attempt' || attempt.payload.mode !== 'current_checkout_claim' ||
          ['intent_id', 'ready_ref', 'scope_digest'].some(key => attempt.payload[key] !== value[key]) ||
          value.observed_at < attempt.recorded_at) throw new Error('claim_unverified');
      const existing = state.records[index + 1]?.value;
      if (existing?.kind === 'outcome' && existing.payload.attempt_sha256 === value.claim_sha256 &&
          existing.payload.outcome === value.outcome && existing.payload.observed_at === value.observed_at) {
        await emit({ type: 'claim_outcome_recorded', duplicate: true, claim_sha256: value.claim_sha256 }); return 0;
      }
      if (state.head !== value.claim_sha256) throw new Error('claim_not_pending');
      await append(directory, state, anchor, 'outcome', { intent_id: value.intent_id, ready_ref: value.ready_ref,
        scope_digest: value.scope_digest, attempt_sha256: value.claim_sha256, outcome: value.outcome, observed_at: value.observed_at });
      await emit({ type: 'claim_outcome_recorded', duplicate: false, claim_sha256: value.claim_sha256 }); return 0;
    }
    const { scopeHash, parsed, deadline } = validateCurrentClaim(value, state, anchor);
    const { scope, ready_ref: readyRef, provenance, inspection } = value;
    const base = { intent_id: scope.intent_id, ready_ref: readyRef, scope_digest: scopeHash };
    const check = () => { if (Date.now() >= deadline) throw new Error('claim_expired'); };
    const permit = { type: 'operator_action', ...base, request_id: randomUUID(), stage: 'open_guest_card', deadline,
      from_document_ref: provenance.checkout_document_ref, control_ref: parsed.control_ref,
      inspection_fingerprint: parsed.inspection_fingerprint };
    const binding = Object.freeze({ url: GUEST_CARD_URL, selector: GUEST_CARD_SELECTOR,
      control_ref: parsed.control_ref, scope_digest: scopeHash, intent_id: scope.intent_id,
      inspection_fingerprint: parsed.inspection_fingerprint, expected_scope: Object.freeze({ ...scope }) });
    const authorizationRecord = value.testAuthorization ? {
      authorization_source: value.testAuthorization.source, authorized_at: value.testAuthorization.authorized_at,
      testAuthorization: { ...value.testAuthorization } } : {
      ready_observed_at: value.userReady.observed_at, ready_expires_at: value.userReady.expires_at };
    check(); await append(directory, state, anchor, 'ready', { ...base, mode: 'current_checkout_claim', ...authorizationRecord,
      failure_observed_at: value.priorFailure.failure_observed_at, history_observed_at: value.priorFailure.order_history_observed_at });
    check(); const reviewHash = await append(directory, state, anchor, 'review', { ...base, mode: 'current_checkout_claim',
      latest_prior_hash: value.expected_latest_hash, legacy_files: anchor.manifest, provenance,
      ...(value.priorNoDispatch ? { priorNoDispatch: { ...value.priorNoDispatch } } : {}),
      failure_observed_at: value.priorFailure.failure_observed_at, history_observed_at: value.priorFailure.order_history_observed_at,
      no_card: true, no_pay: true, no_approval: true, no_capture: true });
    check(); const claimHash = await append(directory, state, anchor, 'attempt', { ...base, mode: 'current_checkout_claim',
      review_sha256: reviewHash, control_ref: parsed.control_ref, inspection_fingerprint: parsed.inspection_fingerprint, deadline, permit });
    check();
    const currentAnchor = await anchors(directory), current = await ledger(directory, currentAnchor);
    if (currentAnchor.hash !== anchor.hash || current.head !== claimHash || current.records.length !== state.records.length ||
        !revalidateGuestCardClick(binding, inspection)) throw new Error('claim_changed');
    check(); await emit({ type: 'checkout_claim_granted', claim_sha256: claimHash, permit,
      order_status: 'unverified', payment_status: 'unverified', autonomous_checkout: false,
      ...(value.testAuthorization ? { human_payment_ready: false, card_entry_allowed: false,
        payment_allowed: false, final_submission_allowed: false, requires_execution_revalidation: true,
        expected_scope: { ...scope } } : {}) }); return 0;
  } catch (error) {
    await emit({ type: 'claim_stopped', code: 'claim_blocked_or_uncertain',
      stop_reason: CLAIM_STOP_REASONS.get(error?.message) || 'unknown_internal_stop',
      order_status: 'unverified', payment_status: 'unverified' }).catch(() => {}); return 2;
  } finally { if (lock) await lock.close(); }
}

async function privateDirectory(directory) {
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      (info.mode & 0o7777) !== 0o700) throw new Error('private_state_required');
}
async function readRecord(directory, name) {
  const file = await open(join(directory, name), constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.size > 16_384) throw new Error('invalid_record');
    const bytes = await file.readFile();
    if (bytes.length > 16_384) throw new Error('invalid_record');
    return { name, value: JSON.parse(bytes), hash: sha(bytes) };
  } finally { await file.close(); }
}
async function anchors(directory) {
  const files = [];
  for (const name of LEGACY) files.push(await readRecord(directory, name));
  const [a, b, c, d, e] = files, original = a.value, recovery = b.value,
    retry = c.value, navigation = d.value, last = e.value;
  if (original.schema !== 'discogs_guest_card_init_fence_v1' ||
      original.listing_id !== '1366950910' || original.release_id !== 3422100 ||
      !uuid(original.intent_id) || !hash(original.scope_digest) || !num(original.created_at) ||
      recovery.schema !== 'discogs_guest_card_recovery_review_v1' ||
      recovery.original_fence_sha256 !== a.hash || recovery.original_intent_id !== original.intent_id ||
      recovery.original_scope_digest !== original.scope_digest ||
      retry.schema !== 'discogs_guest_card_recovery_attempt_v1' || retry.attempt !== 1 ||
      retry.original_fence_sha256 !== a.hash || retry.reconciliation_sha256 !== b.hash ||
      retry.intent_id !== recovery.retry_intent_id || retry.scope_digest !== recovery.retry_scope_digest ||
      navigation.schema !== 'discogs_guest_card_navigation_reopen_review_v1' ||
      navigation.original_fence_sha256 !== a.hash || navigation.original_intent_id !== original.intent_id ||
      navigation.original_scope_digest !== original.scope_digest ||
      navigation.recovery_review_sha256 !== b.hash || navigation.recovery_fence_sha256 !== c.hash ||
      navigation.recovery_intent_id !== retry.intent_id || navigation.recovery_scope_digest !== retry.scope_digest ||
      last.schema !== 'discogs_guest_card_navigation_reopen_attempt_v1' || last.attempt !== 1 ||
      last.original_fence_sha256 !== a.hash || last.recovery_review_sha256 !== b.hash ||
      last.recovery_fence_sha256 !== c.hash || last.reconciliation_sha256 !== d.hash ||
      last.intent_id !== navigation.reopen_intent_id || last.scope_digest !== navigation.reopen_scope_digest ||
      !uuid(retry.intent_id) || !hash(retry.scope_digest) || !uuid(last.intent_id) || !hash(last.scope_digest) ||
      !num(retry.created_at) || !num(last.created_at) ||
      !(original.created_at <= retry.created_at && retry.created_at <= last.created_at)) throw new Error('invalid_legacy_chain');
  const manifest = files.map(({ name, hash: digest }) => ({ name, sha256: digest }));
  return { manifest, hash: sha(encoded(manifest)), lastEffectAt: last.created_at };
}
async function lockDirectory(directory) {
  const file = await open(join(directory, 'guest-checkout-operator.lock'),
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600) throw new Error('invalid_lock');
    // dup FD3 shares the parent's open-file description: Node retains the lock
    // after this fixed Python process exits, until this FileHandle closes.
    const probe = spawnSync('python3', ['-c', 'import fcntl; fcntl.flock(3, fcntl.LOCK_EX | fcntl.LOCK_NB)'],
      { stdio: ['ignore', 'ignore', 'ignore', file.fd], timeout: 5000 });
    if (probe.status !== 0) throw new Error('operator_busy');
    return file;
  } catch (error) { await file.close(); throw error; }
}
async function ledger(directory, anchor) {
  const names = (await readdir(directory)).filter(name => name.startsWith('guest-checkout-') && name !== 'guest-checkout-operator.lock');
  if (names.some(name => !/^guest-checkout-[0-9]{6}\.json$/u.test(name)) || names.length > 999_999) throw new Error('invalid_ledger');
  names.sort(); const records = []; let head = anchor.hash, group = null;
  for (let i = 0; i < names.length; i++) {
    if (names[i] !== `guest-checkout-${String(i + 1).padStart(6, '0')}.json`) throw new Error('missing_ledger_record');
    const item = await readRecord(directory, names[i]), value = item.value, p = value.payload;
    if (!shape(value, ['schema', 'sequence', 'kind', 'previous_sha256', 'legacy_anchor_sha256', 'recorded_at', 'payload']) ||
        value.schema !== 'discogs_guest_operator_record_v1' || value.sequence !== i + 1 ||
        value.previous_sha256 !== head || value.legacy_anchor_sha256 !== anchor.hash || !num(value.recorded_at) ||
        !p || !uuid(p.intent_id) || !uuid(p.ready_ref) || !hash(p.scope_digest) ||
        !['ready', 'review', 'attempt', 'outcome'].includes(value.kind)) throw new Error('invalid_ledger');
    if (value.kind === 'ready') group = { intent: p.intent_id, ready: p.ready_ref, scope: p.scope_digest, kind: 'ready' };
    else {
      if (!group || group.intent !== p.intent_id || group.ready !== p.ready_ref || group.scope !== p.scope_digest ||
          ({ review: 'ready', attempt: 'review', outcome: 'attempt' })[value.kind] !== group.kind ||
          value.kind === 'attempt' && p.review_sha256 !== head ||
          value.kind === 'outcome' && p.attempt_sha256 !== head) throw new Error('invalid_ledger_transition');
      group.kind = value.kind;
    }
    records.push(item); head = item.hash;
  }
  return { records, head };
}
async function append(directory, state, anchor, kind, payload) {
  const currentAnchor = await anchors(directory);
  if (currentAnchor.hash !== anchor.hash) throw new Error('legacy_changed');
  const current = await ledger(directory, currentAnchor);
  if (current.head !== state.head || current.records.length !== state.records.length) {
    throw new Error('operator_ledger_changed');
  }
  const sequence = state.records.length + 1;
  const value = { schema: 'discogs_guest_operator_record_v1', sequence, kind,
    previous_sha256: state.head, legacy_anchor_sha256: anchor.hash, recorded_at: Date.now() / 1000, payload };
  const bytes = encoded(value), name = `guest-checkout-${String(sequence).padStart(6, '0')}.json`;
  const parent = await open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const file = await open(join(directory, name), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    try { await file.writeFile(bytes); await file.sync(); } finally { await file.close(); }
    await parent.sync();
  } finally { await parent.close(); }
  const item = { name, value, hash: sha(bytes) }; state.records.push(item); state.head = item.hash;
  return item.hash;
}

/** Actual stdio CLI; directory override is for isolated child-process tests only.
 * The executable entry point below always uses the fixed /state mount.
 */
export async function runGuestCheckoutOperator({ directory = '/state', input = process.stdin, output = process.stdout } = {}) {
  const lines = createInterface({ input, crlfDelay: Infinity }), iterator = lines[Symbol.asyncIterator]();
  const abort = new AbortController(); lines.once('close', () => abort.abort('disconnected'));
  const emit = value => new Promise((resolve, reject) => output.write(encoded(value), error => error ? reject(error) : resolve()));
  const receive = async () => {
    const line = await iterator.next();
    if (line.done || line.value.length > 16_384) throw new Error('invalid_operator_input');
    return JSON.parse(line.value);
  };
  let lock, state, anchor, scope, start, scopeHash, deadline, attemptHash = null, outcomeWritten = false;
  const check = () => {
    if (abort.signal.aborted || !num(deadline) || Date.now() >= deadline ||
        guestCardScopeDigest(scope) !== scopeHash || start.userReady.ready !== true) throw new Error('operator_stopped');
  };
  const verifyAnchors = async () => {
    const currentAnchor = await anchors(directory);
    if (currentAnchor.hash !== anchor.hash) throw new Error('legacy_changed');
    const current = await ledger(directory, currentAnchor);
    if (current.head !== state.head || current.records.length !== state.records.length) {
      throw new Error('operator_ledger_changed');
    }
  };
  const envelope = (stage, requestId, from, stageDeadline) => ({ intent_id: scope.intent_id,
    ready_ref: start.ready_ref, scope_digest: scopeHash, request_id: requestId, stage, deadline: stageDeadline,
    from_document_ref: from });
  const exchange = async (stage, from, keys, guard = null, control = {}, maximumDeadline = Infinity) => {
    check(); if (guard && !guard()) throw new Error('operator_stopped');
    await verifyAnchors(); check(); if (guard && !guard()) throw new Error('operator_stopped');
    const binding = envelope(stage, randomUUID(), from, Math.min(deadline, Date.now() + 120_000, maximumDeadline));
    const checkStage = () => { check(); if (Date.now() >= binding.deadline) throw new Error('operator_stage_expired'); };
    checkStage(); await emit({ type: 'operator_action', ...binding, ...control }); checkStage();
    let timer;
    try {
      const ack = await Promise.race([receive(), new Promise((_, reject) => {
        timer = setTimeout(() => { abort.abort('timed_out'); reject(new Error('operator_stopped')); }, Math.max(1, binding.deadline - Date.now()));
      })]);
      checkStage();
      if (!shape(ack, ['type', ...Object.keys(binding), ...keys]) || ack.type !== 'operator_ack' ||
          Object.keys(binding).some(key => ack[key] !== binding[key])) throw new Error('operator_ack_unverified');
      await verifyAnchors(); checkStage();
      return ack;
    } finally { clearTimeout(timer); }
  };
  try {
    await privateDirectory(directory); lock = await lockDirectory(directory);
    anchor = await anchors(directory); state = await ledger(directory, anchor);
    await emit({ type: 'waiting_for_user_ready', latest_hash: state.head,
      legacy_anchor_sha256: anchor.hash, mode: 'operator_assisted_no_card_entry' });
    start = await receive();
    if (!shape(start, ['type', 'ready_ref', 'scope', 'userReady', 'priorFailure', 'expected_latest_hash']) ||
        start.type !== 'start' || !uuid(start.ready_ref) || start.expected_latest_hash !== state.head) throw new Error('invalid_start');
    requireResolvedPreviousAttempt(state);
    scope = start.scope; scopeHash = guestCardScopeDigest(scope);
    const prior = start.priorFailure, ready = start.userReady;
    if (!scopeHash || !ready || !prior || state.records.some(item =>
      item.value.kind === 'ready' && (item.value.payload.ready_ref === start.ready_ref || item.value.payload.intent_id === scope.intent_id))) throw new Error('ready_replayed');
    let lastEffectAt = anchor.lastEffectAt;
    for (const item of state.records) {
      if (item.value.kind === 'attempt') lastEffectAt = Math.max(lastEffectAt, item.value.recorded_at);
      if (item.value.kind === 'outcome') lastEffectAt = Math.max(lastEffectAt, item.value.payload.observed_at || 0);
    }
    if (!(num(prior.failure_observed_at) && prior.failure_observed_at >= lastEffectAt &&
        num(prior.order_history_observed_at) && prior.order_history_observed_at >=
          (state.records.at(-1)?.value.recorded_at || lastEffectAt))) throw new Error('reconciliation_required');
    deadline = Math.min(scope.expires_at * 1000, ready.expires_at * 1000,
      prior.expires_at * 1000, (prior.order_history_observed_at + 300) * 1000);
    let cartAck, checkoutAck, readyConsumed = false;
    const base = () => ({ intent_id: scope.intent_id, ready_ref: start.ready_ref, scope_digest: scopeHash });
    const ports = {
      openCartViaVisibleControl: async options => {
        deadline = Math.min(deadline, options.deadline); check();
        if (!options.revalidateBeforeDispatch()) throw new Error('operator_stopped');
        await append(directory, state, anchor, 'ready', { ...base(),
          ready_observed_at: ready.observed_at, ready_expires_at: ready.expires_at,
          failure_observed_at: prior.failure_observed_at, history_observed_at: prior.order_history_observed_at });
        readyConsumed = true; check();
        cartAck = await exchange('open_cart', null, ['navigation', 'cart'], options.revalidateBeforeDispatch);
        if (cartAck.cart?.source !== 'operator_verified_current_screen') throw new Error('operator_screen_unverified');
        return cartAck.navigation;
      },
      inspectCurrentCart: async () => cartAck.cart,
      clickGoToCheckout: async (binding, options) => {
        deadline = Math.min(deadline, options.deadline); check();
        checkoutAck = await exchange('go_to_checkout', binding.from_document_ref,
          ['transition', 'checkout', 'card_control'], options.revalidateBeforeDispatch,
          { control_ref: binding.control_ref, cart_fingerprint: binding.cart_fingerprint });
        if (checkoutAck.checkout?.source !== 'operator_verified_current_screen') throw new Error('operator_screen_unverified');
        if (checkoutAck.transition?.cart_fingerprint !== binding.cart_fingerprint ||
            checkoutAck.transition?.control_ref !== binding.control_ref) throw new Error('transition_unverified');
        return checkoutAck.transition;
      },
      inspectFreshCheckout: async () => checkoutAck.checkout,
    };
    const fresh = await prepareFreshGuestCheckoutSession(ports, scope,
      { userReady: ready, priorFailure: prior, signal: abort.signal,
        timeoutMs: Math.max(1, Math.floor(deadline - Date.now())) });
    check();
    if (fresh.status !== 'prepared_review_only' || !readyConsumed) throw new Error('fresh_checkout_unverified');
    deadline = Math.min(deadline, fresh.provenance.expires_at * 1000); check();
    const inspection = { schema: 'discogs_guest_card_inspection_v1', url: GUEST_CARD_URL,
      access: checkoutAck.checkout.access, control: checkoutAck.card_control, evidence: checkoutAck.checkout.evidence };
    const parsed = validateGuestCardInspection(inspection, scope);
    if (!parsed) throw new Error('guest_control_unverified');
    deadline = Math.min(deadline, parsed.expires_at * 1000); check();
    const binding = Object.freeze({ url: GUEST_CARD_URL, selector: GUEST_CARD_SELECTOR,
      control_ref: parsed.control_ref, scope_digest: scopeHash, intent_id: scope.intent_id,
      inspection_fingerprint: parsed.inspection_fingerprint, expected_scope: Object.freeze({ ...scope }) });
    const reviewHash = await append(directory, state, anchor, 'review', { ...base(),
      latest_prior_hash: start.expected_latest_hash, legacy_files: anchor.manifest,
      failure_observed_at: prior.failure_observed_at, history_observed_at: prior.order_history_observed_at,
      no_card: true, no_pay: true, no_approval: true, no_capture: true, provenance: fresh.provenance });
    check(); await verifyAnchors(); check();
    const guestDeadline = Math.min(deadline, Date.now() + 120_000,
      (checkoutAck.checkout.evidence.observed_at + 20) * 1000);
    if (Date.now() >= guestDeadline) throw new Error('guest_observation_expired');
    attemptHash = await append(directory, state, anchor, 'attempt', { ...base(), review_sha256: reviewHash,
      control_ref: parsed.control_ref, inspection_fingerprint: parsed.inspection_fingerprint, deadline: guestDeadline });
    check(); await verifyAnchors(); check();
    if (!revalidateGuestCardClick(binding, inspection)) throw new Error('guest_control_changed');
    const ack = await exchange('open_guest_card', fresh.provenance.checkout_document_ref,
      ['outcome', 'observed_at'], null,
      { control_ref: parsed.control_ref, inspection_fingerprint: parsed.inspection_fingerprint }, guestDeadline);
    if (!['guest_form_visible', 'SESSION_EXPIRED', 'unknown', 'access_restricted'].includes(ack.outcome) ||
        !num(ack.observed_at) || ack.observed_at > Date.now() / 1000 ||
        ack.observed_at < state.records.at(-1).value.recorded_at) throw new Error('outcome_unverified');
    await append(directory, state, anchor, 'outcome', { ...base(), attempt_sha256: attemptHash,
      outcome: ack.outcome, observed_at: ack.observed_at }); outcomeWritten = true; check();
    await emit({ type: 'finished', code: 'operator_outcome_recorded', outcome: ack.outcome,
      order_status: 'unverified', payment_status: 'unverified', autonomous_checkout: false });
    return 0;
  } catch {
    if (attemptHash && !outcomeWritten) {
      await append(directory, state, anchor, 'outcome', { intent_id: scope.intent_id,
        ready_ref: start.ready_ref, scope_digest: scopeHash, attempt_sha256: attemptHash,
        outcome: 'unknown', observed_at: Date.now() / 1000 }).catch(() => {});
    }
    await emit({ type: 'stopped', code: 'operator_blocked_or_uncertain',
      order_status: 'unverified', payment_status: 'unverified' }).catch(() => {});
    return 2;
  } finally { abort.abort('finished'); lines.close(); if (lock) await lock.close(); }
}

/** Diagnostic parser only: no state directory, lock, readiness or UI dispatch.
 * Reports sizes/types, never payload values. This branch cannot call the runner.
 */
export async function checkOperatorTransport(input = process.stdin, output = process.stdout) {
  const lines = createInterface({ input, crlfDelay: Infinity });
  let frames = 0;
  try {
    for await (const line of lines) {
      const bytes = Buffer.byteLength(line);
      if (bytes > 16_384) throw new Error('oversized_frame');
      const value = JSON.parse(line);
      if (shape(value, ['type']) && value.type === 'transport_finish') {
        output.write(encoded({ type: 'transport_complete', frames, no_action: true }));
        return 0;
      }
      const start = shape(value, ['type', 'ready_ref', 'scope', 'userReady', 'priorFailure', 'expected_latest_hash']) && value.type === 'start';
      const ack = value.type === 'operator_ack' && ['intent_id', 'ready_ref', 'scope_digest', 'request_id', 'stage', 'deadline', 'from_document_ref']
        .every(key => Object.hasOwn(value, key));
      if (!start && !ack) throw new Error('invalid_frame');
      frames++;
      output.write(encoded({ type: 'transport_frame', frame: frames, received_bytes: bytes,
        parsed_type: value.type, no_action: true }));
    }
    return 2;
  } catch {
    output.write(encoded({ type: 'transport_rejected', frames, no_action: true }));
    return 2;
  } finally { lines.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length === 3 && ['--claim-current-checkout', '--record-claim-outcome'].includes(process.argv[2])) {
    process.exit(await runCurrentCheckoutClaim({ mode: process.argv[2] === '--claim-current-checkout' ? 'claim' : 'outcome' }));
  }
  if (process.argv.length === 3 && process.argv[2] === '--transport-check') {
    process.exit(await checkOperatorTransport());
  }
  if (process.argv.slice(2).some(value => value !== '--waiting-ready') || process.argv.length > 3) process.exit(2);
  process.exit(await runGuestCheckoutOperator());
}
