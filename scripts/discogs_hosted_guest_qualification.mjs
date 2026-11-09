/** Fixed local qualification issuer. No credentials or browser calls. Ordinary
 * jobs remain read-only; the distinct owner-only prepare policy admits one
 * no-submit initialization after genuine inspect/confirmation/reconciliation.
 * Policy absence keeps the socket off; no mode permits card entry or payment.
 */
import { constants } from 'node:fs';
import { open, lstat } from 'node:fs/promises';
import { dirname, join, isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';

export const HOSTED_QUALIFICATION_POLICY = '/bridge/guest-qualification.json';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const SCOPE = Object.freeze({ release_id: '3422100', listing_id: '1366950910', seller: 'Mailman21842',
  quantity: 1, currency: 'USD', max_total: '5.05', approved_destination_ref: 'saved_shipping_address' });
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const invalid = () => { throw new Error('invalid_guest_qualification_policy'); };
async function privateJson(path, { optional = false } = {}) {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 || info.nlink !== 1 || info.size > 8192) invalid();
    const source = await file.readFile('utf8');
    if (Buffer.byteLength(source) > 8192) invalid();
    return JSON.parse(source);
  } catch (error) { if (optional && error.code === 'ENOENT') return null; invalid(); }
  finally { await file?.close(); }
}
const capturePolicy = policy => policy?.schema === 'discogs_guest_cart_capture_policy_v1';
const preflightPolicy = policy => policy?.schema === 'discogs_guest_cart_preflight_policy_v1';
const preparePolicy = policy => policy?.schema === 'discogs_guest_prepare_policy_v1';
const paymentInspectionPolicy = policy => policy?.schema === 'discogs_guest_payment_inspection_policy_v1';
const HASH = /^[0-9a-f]{64}$/u;
const PRIOR_FAILURE_KEYS = ['source', 'last_error_code', 'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
  'card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked', 'failure_observed_at', 'order_history_url',
  'order_history_archived', 'order_history_status', 'order_history_count', 'order_history_complete', 'order_history_observed_at', 'reviewed_at', 'expires_at'];
const PREPARE_KEYS = ['expected_ledger_head', 'intent_id', 'ready_ref', 'destination_ref', 'authorized_at',
  'prior_form_disposition_sha256', 'prior_failure'];
function validPriorFailure(prior, policy, now, allowExpired) {
  return exact(prior, PRIOR_FAILURE_KEYS) && prior.source === 'operator_reviewed_normal_discogs_ui' &&
    prior.last_error_code === 'SESSION_EXPIRED' &&
    prior.release_id === Number(policy.scope.release_id) &&
    ['listing_id', 'seller', 'quantity', 'currency', 'max_total'].every(key => prior[key] === policy.scope[key]) &&
    ['card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'].every(key => prior[key] === false) &&
    prior.order_history_url === 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' &&
    prior.order_history_archived === 'All' && prior.order_history_status === 'All' && prior.order_history_count === 0 &&
    prior.order_history_complete === true &&
    ['failure_observed_at', 'order_history_observed_at', 'reviewed_at', 'expires_at'].every(key => Number.isFinite(prior[key])) &&
    prior.failure_observed_at <= prior.order_history_observed_at && prior.order_history_observed_at <= prior.reviewed_at &&
    prior.reviewed_at <= policy.issued_at && prior.reviewed_at <= now &&
    prior.expires_at > prior.reviewed_at && prior.expires_at - prior.reviewed_at <= 300 &&
    (allowExpired || now - prior.order_history_observed_at < 300 && now < prior.expires_at);
}
function validatePolicy(policy, now, { allowExpired = false } = {}) {
  if (!exact(policy, ['schema', 'job_id', 'approval_ref', 'issued_at', 'expires_at', 'max_calls', 'action', 'scope', 'destination_ack',
    ...(paymentInspectionPolicy(policy) ? ['expected_ledger_head', 'claim_sha256'] :
      preflightPolicy(policy) ? ['expected_ledger_head'] : preparePolicy(policy) ? PREPARE_KEYS : [])]) ||
      !['discogs_guest_qualification_policy_v1', 'discogs_guest_cart_capture_policy_v1', 'discogs_guest_cart_preflight_policy_v1', 'discogs_guest_prepare_policy_v1', 'discogs_guest_payment_inspection_policy_v1'].includes(policy.schema) || !UUID.test(policy.job_id) || !UUID.test(policy.approval_ref) ||
      policy.action !== (paymentInspectionPolicy(policy) ? 'inspect_enhanced_payment' : preparePolicy(policy) ? 'prepare_guest_form' : preflightPolicy(policy) ? 'cart_checkout_preflight' : 'probe_guest_evidence') ||
      policy.max_calls !== (paymentInspectionPolicy(policy) ? 2 : preparePolicy(policy) || preflightPolicy(policy) ? 3 : capturePolicy(policy) ? 1 : 2) ||
      !Number.isFinite(policy.issued_at) || !Number.isFinite(policy.expires_at) || policy.issued_at < 0 ||
      policy.issued_at > now || !allowExpired && now >= policy.expires_at || policy.expires_at <= policy.issued_at ||
      policy.expires_at - policy.issued_at > (preparePolicy(policy) || paymentInspectionPolicy(policy) ? 300 : 1800) || !exact(policy.scope, Object.keys(SCOPE)) ||
      Object.entries(SCOPE).some(([key, value]) => policy.scope[key] !== value)) invalid();
  if (preflightPolicy(policy) && !HASH.test(policy.expected_ledger_head || '')) invalid();
  if (paymentInspectionPolicy(policy) && (!HASH.test(policy.expected_ledger_head || '') ||
      !HASH.test(policy.claim_sha256 || ''))) invalid();
  if (preparePolicy(policy) && (!HASH.test(policy.expected_ledger_head || '') ||
      !HASH.test(policy.prior_form_disposition_sha256 || '') ||
      ![policy.intent_id, policy.ready_ref, policy.destination_ref].every(value => UUID.test(value || '')) ||
      !Number.isFinite(policy.authorized_at) || policy.authorized_at < 0 || policy.authorized_at > policy.issued_at ||
      !validPriorFailure(policy.prior_failure, policy, now, allowExpired) ||
      policy.expires_at > policy.prior_failure.expires_at ||
      policy.expires_at > policy.prior_failure.order_history_observed_at + 300)) invalid();
  const ack = policy.destination_ack;
  if ((capturePolicy(policy) || paymentInspectionPolicy(policy)) && ack !== null) invalid();
  if (ack !== null && (!exact(ack, ['document_ref', 'source', 'approved_destination_ref', 'observed_at']) ||
      !UUID.test(ack.document_ref) || ack.source !== 'operator_visual_saved_destination_match' ||
      ack.approved_destination_ref !== SCOPE.approved_destination_ref || !Number.isFinite(ack.observed_at) ||
      ack.observed_at < policy.issued_at || ack.observed_at > now || !allowExpired && now - ack.observed_at > 60)) invalid();
  return policy;
}
function identity(policy) {
  return { schema: policy.schema, job_id: policy.job_id, approval_ref: policy.approval_ref,
    issued_at: policy.issued_at, expires_at: policy.expires_at, max_calls: policy.max_calls,
    action: policy.action, scope: SCOPE,
    ...(preflightPolicy(policy) ? { expected_ledger_head: policy.expected_ledger_head } : {}),
    ...(paymentInspectionPolicy(policy) ? { expected_ledger_head: policy.expected_ledger_head,
      claim_sha256: policy.claim_sha256 } : {}),
    ...(preparePolicy(policy) ? Object.fromEntries(PREPARE_KEYS.map(key => [key, policy[key]])) : {}) };
}
function requestAllowed(request, policy) {
  const navigating = request?.phase === 'navigate_checkout_preflight';
  const preparing = request?.phase === 'prepare_guest_form';
  const paymentInspection = request?.phase === 'inspect_payment_readiness';
  if (paymentInspection) return paymentInspectionPolicy(policy) &&
    exact(request, ['schema', 'job_id', 'phase', 'document_ref', 'claim_sha256']) &&
    request.schema === 'discogs_guest_payment_inspection_v1' && request.job_id === policy.job_id &&
    UUID.test(request.document_ref || '') && request.claim_sha256 === policy.claim_sha256;
  if (!exact(request, ['confirm_saved_destination', 'navigate_checkout_preflight', 'prepare_guest_form'].includes(request?.phase) ?
    ['schema', 'job_id', 'phase', 'document_ref'] : ['schema', 'job_id', 'phase']) ||
      request.schema !== (preparing ? 'discogs_guest_prepare_request_v1' : navigating ? 'discogs_guest_checkout_preflight_v1' : 'discogs_guest_readonly_probe_v1') || request.job_id !== policy.job_id) return false;
  if (capturePolicy(policy)) return request.phase === 'capture_cart_structure';
  if (preparing) return preparePolicy(policy) && policy.destination_ack !== null &&
    request.document_ref === policy.destination_ack.document_ref;
  if (navigating) return preflightPolicy(policy) && policy.destination_ack !== null &&
    request.document_ref === policy.destination_ack.document_ref;
  return request.phase === 'inspect' ? policy.destination_ack === null :
    request.phase === 'confirm_saved_destination' && policy.destination_ack !== null &&
      request.document_ref === policy.destination_ack.document_ref;
}
async function exclusiveJson(path, value) {
  let file;
  try {
    file = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await file.writeFile(JSON.stringify(value) + '\n'); await file.sync();
  } finally { await file?.close(); }
  // A persisted slot must survive a process crash/restart before browser access.
  const directory = await open(dirname(path), constants.O_RDONLY);
  try { await directory.sync(); } finally { await directory.close(); }
}
async function createIssuer(policyPath, now, fixture) {
  const directory = dirname(policyPath), info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      (fixture ? (info.mode & 0o777) !== 0o700 : (info.mode & 0o7777) !== 0o2710 || info.gid !== 113)) invalid();
  const first = await privateJson(policyPath, { optional: true });
  if (first === null) return null;
  validatePolicy(first, now(), { allowExpired: true });
  if (now() >= first.expires_at) return null; // Expiry disables capability; it must not strand the viewer.
  const original = identity(first), identityDigest = hash(original);
  const prefix = join(directory, `guest-qualification-${first.job_id}`);
  const identityPath = `${prefix}-identity.json`;
  const existing = await privateJson(identityPath, { optional: true });
  if (existing === null) {
    try { await exclusiveJson(identityPath, { identity_sha256: identityDigest }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  if ((await privateJson(identityPath))?.identity_sha256 !== identityDigest) invalid();
  const load = async () => {
    const policy = validatePolicy(await privateJson(policyPath), now());
    if (hash(identity(policy)) !== identityDigest) invalid();
    return policy;
  };
  const slot = phase => `${prefix}-${phase === 'inspect_payment_readiness' ? '6-payment-inspection' : phase === 'prepare_guest_form' ? '5-prepare' : phase === 'navigate_checkout_preflight' ? '4-checkout-preflight' : phase === 'capture_cart_structure' ? '3-cart-capture' : phase === 'inspect' ? '1-inspect' : '2-confirm'}.json`;
  const validReceipt = receipt => {
    const bound = (preflightPolicy(first) || preparePolicy(first)) &&
      ['confirm_saved_destination', 'navigate_checkout_preflight', 'prepare_guest_form'].includes(receipt?.phase);
    const paymentBound = paymentInspectionPolicy(first) && receipt?.phase === 'inspect_payment_readiness';
    return exact(receipt, ['schema', 'job_id', 'identity_sha256', 'phase', 'consumed_at',
      ...(bound ? ['document_ref', 'destination_ack_observed_at'] : []),
      ...(paymentBound ? ['document_ref', 'claim_sha256'] : [])]) &&
      receipt.schema === 'discogs_guest_qualification_use_v1' && receipt.job_id === first.job_id &&
      receipt.identity_sha256 === identityDigest && ['inspect', 'confirm_saved_destination', 'capture_cart_structure', 'navigate_checkout_preflight', 'prepare_guest_form', 'inspect_payment_readiness'].includes(receipt.phase) &&
      Number.isFinite(receipt.consumed_at) && receipt.consumed_at >= first.issued_at && receipt.consumed_at < first.expires_at &&
      (!bound || UUID.test(receipt.document_ref) && Number.isFinite(receipt.destination_ack_observed_at) &&
        receipt.destination_ack_observed_at >= first.issued_at && receipt.destination_ack_observed_at <= receipt.consumed_at) &&
      (!paymentBound || UUID.test(receipt.document_ref) && receipt.claim_sha256 === first.claim_sha256);
  };
  const allowed = async request => {
    const policy = await load();
    if (!requestAllowed(request, policy) || await privateJson(slot(request.phase), { optional: true }) !== null) return null;
    if (request.phase === 'confirm_saved_destination') {
      const previous = await privateJson(slot('inspect'), { optional: true });
      if (!validReceipt(previous) || previous.phase !== 'inspect' || policy.destination_ack.observed_at < previous.consumed_at) return null;
    }
    if (['navigate_checkout_preflight', 'prepare_guest_form'].includes(request.phase)) {
      const inspected = await privateJson(slot('inspect'), { optional: true });
      const confirmed = await privateJson(slot('confirm_saved_destination'), { optional: true });
      if (!validReceipt(inspected) || inspected.phase !== 'inspect' || !validReceipt(confirmed) ||
          confirmed.phase !== 'confirm_saved_destination' || confirmed.consumed_at < inspected.consumed_at ||
          policy.destination_ack.observed_at < inspected.consumed_at || confirmed.consumed_at < policy.destination_ack.observed_at ||
          confirmed.document_ref !== request.document_ref || confirmed.destination_ack_observed_at !== policy.destination_ack.observed_at) return null;
    }
    if (request.phase === 'inspect_payment_readiness') {
      const inspected = await privateJson(slot('inspect'), { optional: true });
      if (!validReceipt(inspected) || inspected.phase !== 'inspect' || inspected.consumed_at > now()) return null;
    }
    return policy;
  };
  return Object.freeze({
    accessGuard: async (action, request, { signal } = {}) => {
      const expectedAction = request?.phase === 'prepare_guest_form' ? 'prepare_guest_form' :
        request?.phase === 'inspect_payment_readiness' ? 'inspect_enhanced_payment' :
        request?.phase === 'navigate_checkout_preflight' ? 'cart_checkout_preflight' : 'probe_guest_evidence';
      if (action !== expectedAction || signal?.aborted) return false;
      try { return !!await allowed(request) && !signal?.aborted; } catch { return false; }
    },
    authorize: async (request, { signal } = {}) => {
      if (signal?.aborted) return null;
      try {
        const policy = await allowed(request);
        if (!policy || signal?.aborted) return null;
        const consumedAt = now();
        validatePolicy(policy, consumedAt);
        await exclusiveJson(slot(request.phase), { schema: 'discogs_guest_qualification_use_v1', job_id: policy.job_id,
          identity_sha256: identityDigest, phase: request.phase, consumed_at: consumedAt,
          ...((preflightPolicy(policy) || preparePolicy(policy)) && request.phase !== 'inspect' ? { document_ref: request.document_ref,
            destination_ack_observed_at: policy.destination_ack.observed_at } : {}),
          ...(paymentInspectionPolicy(policy) && request.phase === 'inspect_payment_readiness' ? {
            document_ref: request.document_ref, claim_sha256: request.claim_sha256 } : {}) });
        // Recheck revocation and an operator's same-job policy update after the
        // durable write, before returning any browser authorization.
        const current = await load();
        if (!requestAllowed(request, current) || signal?.aborted ||
            policy.destination_ack?.observed_at !== current.destination_ack?.observed_at) return null; // Spent safely; never rearm after uncertainty.
        const prepareRevalidate = async ({ signal: laterSignal } = {}) => {
          if (laterSignal?.aborted) return false;
          try {
            const fresh = await load();
            if (!requestAllowed(request, fresh) || laterSignal?.aborted ||
                fresh.destination_ack?.observed_at !== policy.destination_ack?.observed_at) return false;
            const inspected = await privateJson(slot('inspect'), { optional: true });
            const confirmed = await privateJson(slot('confirm_saved_destination'), { optional: true });
            const prepared = await privateJson(slot('prepare_guest_form'), { optional: true });
            return !laterSignal?.aborted && validReceipt(inspected) && inspected.phase === 'inspect' &&
              validReceipt(confirmed) && confirmed.phase === 'confirm_saved_destination' &&
              validReceipt(prepared) && prepared.phase === 'prepare_guest_form' &&
              confirmed.consumed_at >= inspected.consumed_at && prepared.consumed_at >= confirmed.consumed_at &&
              fresh.destination_ack.observed_at >= inspected.consumed_at &&
              confirmed.consumed_at >= fresh.destination_ack.observed_at &&
              [confirmed, prepared].every(receipt => receipt.document_ref === request.document_ref &&
                receipt.destination_ack_observed_at === fresh.destination_ack.observed_at);
          } catch { return false; }
        };
        const paymentRevalidate = async ({ signal: laterSignal } = {}) => {
          if (laterSignal?.aborted) return false;
          try {
            const fresh = await load();
            if (!requestAllowed(request, fresh) || laterSignal?.aborted) return false;
            const inspected = await privateJson(slot('inspect'), { optional: true });
            const payment = await privateJson(slot('inspect_payment_readiness'), { optional: true });
            return !laterSignal?.aborted && validReceipt(inspected) && inspected.phase === 'inspect' &&
              validReceipt(payment) && payment.phase === 'inspect_payment_readiness' &&
              payment.consumed_at >= inspected.consumed_at &&
              payment.document_ref === request.document_ref && payment.claim_sha256 === request.claim_sha256;
          } catch { return false; }
        };
        if (request.phase === 'inspect_payment_readiness' &&
            !await paymentRevalidate({ signal })) return null;
        return Object.freeze({ schema: request.phase === 'inspect_payment_readiness' ? 'discogs_guest_payment_inspection_authorization_v1' :
          request.phase === 'prepare_guest_form' ? 'discogs_guest_prepare_authorization_v1' :
          request.phase === 'navigate_checkout_preflight' ? 'discogs_guest_preflight_authorization_v1' : 'discogs_guest_readonly_authorization_v1', job_id: policy.job_id,
          issued_at: policy.issued_at, expires_at: policy.expires_at, ...SCOPE,
          ...(capturePolicy(policy) ? { mode: 'capture_cart_structure' } : {}),
          ...(request.phase === 'navigate_checkout_preflight' ? { mode: 'cart_checkout_preflight',
            expected_ledger_head: policy.expected_ledger_head } : {}),
          ...(request.phase === 'prepare_guest_form' ? {
            mode: 'prepare_guest_form', approval_ref: policy.approval_ref,
            ...Object.fromEntries(PREPARE_KEYS.map(key => [key, key === 'prior_failure' ?
              Object.freeze({ ...policy[key] }) : policy[key]])), revalidate: prepareRevalidate,
          } : {}),
          ...(request.phase === 'inspect_payment_readiness' ? {
            mode: 'inspect_enhanced_payment', expected_ledger_head: policy.expected_ledger_head,
            claim_sha256: policy.claim_sha256, revalidate: paymentRevalidate,
          } : {}),
          ...(policy.destination_ack ? { destination_ack: Object.freeze({ ...policy.destination_ack }) } : {}) });
      } catch { return null; }
    },
  });
}
export async function loadHostedGuestQualification() {
  try { return await createIssuer(HOSTED_QUALIFICATION_POLICY, () => Date.now() / 1000, false); }
  catch (error) { if (error.code === 'ENOENT') return null; throw new Error('invalid_guest_qualification_policy'); }
}
/** Isolated local policy fixtures only; production has no path/env override. */
export async function createHostedGuestQualificationFixture({ policyPath, now = () => Date.now() / 1000 } = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !isAbsolute(policyPath || '')) throw new Error('fixture_context_required');
  return createIssuer(policyPath, now, true);
}
