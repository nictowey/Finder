/** Dormant exact-KC payment composition. No launcher enables this module yet.
 * Only fixed owner-readable metadata, the full existing guest ledger, the
 * existing real-order ledger, and the resident checkout page can supply facts.
 * No card values, addresses, cookies, alternate pages or payment API are read.
 */
import { constants } from 'node:fs';
import { open, lstat } from 'node:fs/promises';
import { dirname, isAbsolute } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { inspectGuestCheckoutLedger } from './discogs_guest_checkout_operator.mjs';
import { createEnhancedOrderLedgerPort } from './discogs_enhanced_order_ledger_port.mjs';
import { inspectCurrentEnhancedPaymentQuote } from './discogs_enhanced_payment_quote.mjs';
import { createEnhancedFixedPayPort, createEnhancedFixedPayFixturePort,
  fixedEnhancedPayQuoteFingerprint, beginFixedEnhancedPaySecureEntry,
  resumeFixedEnhancedPayAfterEntry, closeFixedEnhancedPayPort,
  waitForFixedEnhancedPayPortSettlement } from './discogs_enhanced_fixed_pay_port.mjs';
import { attemptEnhancedPayment } from './discogs_enhanced_payment_adapter.mjs';

export const HOSTED_PAYMENT_POLICY = '/bridge/guest-card-actions/enhanced-payment-authorization.json';
export const HOSTED_PAYMENT_CONFIRMATION = '/bridge/guest-card-actions/enhanced-payment-confirmation.json';
const GUEST_LEDGER = '/bridge/guest-card-actions';
const ACCESS_STOP = '/bridge/access-stopped';
const CHECKOUT = 'https://www.discogs.com/marketplace/checkout';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const SCOPE = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller', 'one_copy_limit', 'currency',
  'max_total', 'rules_revision', 'quote_fingerprint', 'destination_ref', 'payment_ref', 'terms_fingerprint', 'expires_at'];
const POLICY = ['schema', 'source', 'action', 'mode', 'scope', 'expected_guest_head', 'expected_claim_sha256',
  'selected_shipping_ref', 'selected_shipping_label', 'authorized_at', 'expires_at', 'revoked',
  'test_active', 'kill_switch', 'already_owned'];
const CONFIRM = ['schema', 'source', 'intent_id', 'entry_ref', 'confirmed_at', 'expires_at', 'amount', 'currency',
  'destination_ref', 'destination_match', 'selected_shipping_ref', 'selected_shipping_label', 'method_kind',
  'payment_ref', 'terms_fingerprint', 'terms_verified', 'no_new_notice', 'conversion_notice',
  'competing_currency', 'deferred_charges', 'all_payable_charges_included', 'tax_treatment', 'fees_treatment'];
const exact = (value, keys) => !!value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ?
  `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const stop = code => Object.freeze({ status: 'blocked', code, payment_allowed: false,
  purchase_allowed: false, may_submit_again: false });

async function ownerJson(path) {
  const directory = await lstat(dirname(path));
  if (!directory.isDirectory() || directory.isSymbolicLink() || directory.uid !== process.getuid() ||
      (directory.mode & 0o7777) !== 0o700) throw new Error('private_policy_directory_required');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.nlink !== 1 || info.size < 2 || info.size > 8192) throw new Error('private_policy_file_required');
    const bytes = await file.readFile();
    if (bytes.length > 8192) throw new Error('private_policy_file_required');
    return JSON.parse(bytes.toString('utf8'));
  } finally { await file.close(); }
}
async function preservedAccessStop(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.nlink !== 1 || info.size !== Buffer.byteLength('access_restricted\n')) return false;
    return (await file.readFile('utf8')) === 'access_restricted\n';
  } finally { await file.close(); }
}
async function pinnedRecord(directory, sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 1 || sequence > 999999) throw new Error('guest_record_index_unverified');
  const path = `${directory}/guest-checkout-${String(sequence).padStart(6, '0')}.json`;
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.nlink !== 1 || info.size < 2 || info.size > 16384) throw new Error('guest_record_unverified');
    const bytes = await file.readFile();
    if (bytes.length > 16384) throw new Error('guest_record_unverified');
    return { value: JSON.parse(bytes.toString('utf8')),
      hash: createHash('sha256').update(bytes).digest('hex') };
  } finally { await file.close(); }
}

function validScope(scope, now) {
  return exact(scope, SCOPE) && [scope.intent_id, scope.approval_ref, scope.destination_ref, scope.payment_ref]
    .every(value => UUID.test(value || '')) && scope.release_id === 3422100 &&
    scope.listing_id === '1366950910' && scope.seller === 'Mailman21842' && scope.one_copy_limit === 1 &&
    scope.currency === 'USD' && scope.max_total === '5.05' && Number.isSafeInteger(scope.rules_revision) &&
    scope.rules_revision >= 0 && HASH.test(scope.terms_fingerprint || '') &&
    scope.quote_fingerprint === fixedEnhancedPayQuoteFingerprint(scope) &&
    Number.isFinite(scope.expires_at) && now < scope.expires_at && scope.expires_at - now <= 300;
}
function validPolicy(policy, scope, now) {
  return exact(policy, POLICY) && policy.schema === 'discogs_enhanced_payment_authorization_v1' &&
    policy.source === 'owner_approved_exact_order' && policy.action === 'pay_one_exact_kc' &&
    policy.mode === 'exact_kc_test_only' && canonical(policy.scope) === canonical(scope) &&
    HASH.test(policy.expected_guest_head || '') && HASH.test(policy.expected_claim_sha256 || '') &&
    UUID.test(policy.selected_shipping_ref || '') && policy.selected_shipping_label === 'Economy - Media Mail' &&
    Number.isFinite(policy.authorized_at) && policy.authorized_at <= now && now - policy.authorized_at < 300 &&
    Number.isFinite(policy.expires_at) && now < policy.expires_at && policy.expires_at <= scope.expires_at &&
    policy.expires_at - policy.authorized_at <= 300 && policy.revoked === false &&
    policy.test_active === true && policy.kill_switch === false && policy.already_owned === false;
}
function validConfirmation(value, scope, policy, entryRef, entryAt, now) {
  return UUID.test(entryRef || '') && Number.isFinite(entryAt) && exact(value, CONFIRM) &&
    value.schema === 'discogs_enhanced_payment_confirmation_v1' &&
    value.source === 'owner_confirmed_current_checkout' && value.intent_id === scope.intent_id &&
    value.entry_ref === entryRef && UUID.test(value.entry_ref || '') &&
    Number.isFinite(value.confirmed_at) && entryAt <= value.confirmed_at && value.confirmed_at <= now &&
    now - value.confirmed_at < 60 && Number.isFinite(value.expires_at) && now < value.expires_at &&
    value.expires_at <= Math.min(policy.expires_at, value.confirmed_at + 60) &&
    value.amount === '5.05' && value.currency === 'USD' && value.destination_ref === scope.destination_ref &&
    value.destination_match === true && value.selected_shipping_ref === policy.selected_shipping_ref &&
    value.selected_shipping_label === policy.selected_shipping_label && value.method_kind === 'card' &&
    value.payment_ref === scope.payment_ref && value.terms_fingerprint === scope.terms_fingerprint &&
    value.terms_verified === true && value.no_new_notice === true && value.conversion_notice === false &&
    value.competing_currency === false && value.deferred_charges === false &&
    value.all_payable_charges_included === true && value.tax_treatment === 'included_in_final_total' &&
    value.fees_treatment === 'included_in_final_total';
}

function factory({ context, acquireContextLease, liveGate = false, policyPath = HOSTED_PAYMENT_POLICY,
  confirmationPath = HOSTED_PAYMENT_CONFIRMATION, guestLedgerDirectory = GUEST_LEDGER,
  accessStopPath = ACCESS_STOP, orderLedgerPort = createEnhancedOrderLedgerPort(), fixture = false } = {}) {
  if (typeof liveGate !== 'boolean' || typeof acquireContextLease !== 'function' ||
      !context || liveGate && typeof context.pages !== 'function') throw new TypeError('resident_payment_context_required');
  if (!fixture && (policyPath !== HOSTED_PAYMENT_POLICY || confirmationPath !== HOSTED_PAYMENT_CONFIRMATION ||
      guestLedgerDirectory !== GUEST_LEDGER || accessStopPath !== ACCESS_STOP)) throw new TypeError('fixed_payment_paths_required');
  if (![policyPath, confirmationPath, guestLedgerDirectory, accessStopPath].every(isAbsolute)) throw new TypeError('fixed_payment_paths_required');
  let page = null, fixed = null, documentHandle = null, entryRef = null, entryAt = null,
    entryReady = false, beginStarted = false, resuming = false, attemptStarted = false,
    preheldRelease = null, closed = false;
  const enabled = () => liveGate && !closed;
  const ownerPolicy = async scope => {
    if (!enabled()) throw new Error('payment_runtime_disabled');
    if (!validScope(scope, Date.now() / 1000)) throw new Error('exact_kc_scope_required');
    const policy = await ownerJson(policyPath);
    if (!await preservedAccessStop(accessStopPath)) throw new Error('access_history_unverified');
    const now = Date.now() / 1000;
    if (!enabled() || !validScope(scope, now) || !validPolicy(policy, scope, now))
      throw new Error('current_owner_authorization_required');
    return policy;
  };
  const policyFor = async scope => {
    const policy = await ownerPolicy(scope);
    const guest = await inspectGuestCheckoutLedger({ directory: guestLedgerDirectory,
      expectedClaim: policy.expected_claim_sha256 });
    if (guest?.current_head !== policy.expected_guest_head || guest.claim_verified !== true ||
        guest.state !== 'guest_form_observed_repeat_blocked') throw new Error('current_guest_claim_required');
    const count = guest.record_count;
    if (!Number.isSafeInteger(count) || count < 3) throw new Error('current_guest_claim_required');
    const claim = await pinnedRecord(guestLedgerDirectory, count - 2);
    const outcome = await pinnedRecord(guestLedgerDirectory, count - 1);
    const observation = await pinnedRecord(guestLedgerDirectory, count);
    const a = claim.value?.payload, b = observation.value?.payload;
    if (claim.hash !== policy.expected_claim_sha256 || observation.hash !== policy.expected_guest_head ||
        claim.value?.kind !== 'attempt' || a?.mode !== 'current_checkout_claim' ||
        outcome.value?.kind !== 'outcome' || observation.value?.kind !== 'form_observation' ||
        b?.previous_claim_sha256 !== claim.hash || b?.previous_outcome_sha256 !== outcome.hash ||
        a?.intent_id !== b?.intent_id || a?.ready_ref !== b?.ready_ref || a?.scope_digest !== b?.scope_digest ||
        b?.release_id !== scope.release_id || b?.listing_id !== scope.listing_id ||
        b?.seller !== scope.seller || b?.quantity !== scope.one_copy_limit ||
        b?.currency !== scope.currency || b?.max_total !== scope.max_total) throw new Error('guest_claim_scope_unverified');
    const still = await inspectGuestCheckoutLedger({ directory: guestLedgerDirectory,
      expectedClaim: policy.expected_claim_sha256 });
    if (still?.current_head !== guest.current_head || still.claim_verified !== true ||
        still.state !== guest.state) throw new Error('guest_claim_changed');
    const order = await orderLedgerPort.status();
    if (order?.status !== 'observed' || order.durable !== true || order.ledger &&
        (order.ledger.intent_id !== scope.intent_id || !['prepared', 'submission_unknown'].includes(order.ledger.state))) {
      throw new Error('real_order_ledger_unavailable_or_conflicted');
    }
    const currentPolicy = await ownerPolicy(scope);
    if (!enabled() || canonical(currentPolicy) !== canonical(policy)) throw new Error('owner_authorization_changed');
    return { policy, guest, order };
  };
  const sameDocument = async () => {
    if (!enabled() || !page || !documentHandle || page.url() !== CHECKOUT) return false;
    if (!enabled()) return false;
    const pages = await context.pages();
    if (!enabled() || !pages.includes(page)) return false;
    const same = await page.evaluate(expected => document === expected, documentHandle);
    return enabled() && same === true;
  };
  const confirmationFor = async scope => {
    const policy = await ownerPolicy(scope);
    const confirmation = await ownerJson(confirmationPath);
    if (!validConfirmation(confirmation, scope, policy, entryRef, entryAt, Date.now() / 1000) ||
        !await sameDocument()) throw new Error('current_post_entry_confirmation_required');
    const currentPolicy = await ownerPolicy(scope);
    if (!enabled() || canonical(currentPolicy) !== canonical(policy) ||
        !validConfirmation(confirmation, scope, currentPolicy, entryRef, entryAt, Date.now() / 1000))
      throw new Error('current_post_entry_confirmation_required');
    return { policy, confirmation };
  };
  const evidencePorts = {
    readCurrentState: async scope => {
      const { policy, order } = await policyFor(scope);
      const state = order.ledger?.state ?? 'prepared';
      return { source: 'current_guarded_candidate', intent_id: scope.intent_id, release_id: scope.release_id,
        listing_id: scope.listing_id, seller: scope.seller, rules_revision: scope.rules_revision,
        claim_state: 'cart_observed', owned: policy.already_owned, active: policy.test_active,
        kill_switch: policy.kill_switch, one_copy_limit: 1, intent_state: state };
    },
    readAuthorization: async scope => {
      await policyFor(scope);
      return { source: 'owner_approved_exact_order', current: true, revoked: false,
        scope: { ...scope } };
    },
    readDestination: async scope => {
      const { policy } = await confirmationFor(scope);
      return { source: 'verified_current_checkout_destination', current: true, selected_for_checkout: true,
        destination_ref: scope.destination_ref, selected_shipping_ref: policy.selected_shipping_ref,
        approved_for_intent: scope.intent_id };
    },
    readPaymentMethod: async scope => {
      await confirmationFor(scope);
      return { source: 'verified_current_enhanced_payment_method', selected: true, method_kind: 'card',
        payment_ref: scope.payment_ref, approved_for_intent: scope.intent_id };
    },
    readTerms: async scope => {
      await confirmationFor(scope);
      return { source: 'owner_confirmed_current_cart_terms', current: true,
        fingerprint: scope.terms_fingerprint, approved_for_intent: scope.intent_id };
    },
    readVerifiedQuoteReport: async scope => {
      await confirmationFor(scope);
      return inspectCurrentEnhancedPaymentQuote(page, {
        destinationMatcher: async request => {
          const { confirmation } = await confirmationFor(scope);
          if (request.page !== page || request.observed_at < confirmation.confirmed_at) return null;
          return { source: 'trusted_same_document_destination_binding', document_ref: request.document_ref,
            destination_ref: confirmation.destination_ref, match: true };
        },
        finalQuoteMatcher: async request => {
          const { confirmation } = await confirmationFor(scope);
          if (request.observed_at < confirmation.confirmed_at || request.merchant_total?.amount !== confirmation.amount ||
              request.merchant_total?.currency !== confirmation.currency ||
              request.selected_shipping_label !== confirmation.selected_shipping_label ||
              request.pay_amount !== confirmation.amount) return null;
          // observed_at belongs to this fresh DOM read. The user's genuine
          // confirmation timestamp is never refreshed by another read.
          return { schema: 'discogs_guest_post_entry_confirmation_v1', source: 'trusted_same_document_post_entry_confirmation',
            merchant_document_ref: request.merchant_document_ref, provider_document_ref: request.provider_document_ref,
            observed_at: request.observed_at, expires_at: Math.min(request.expires_at, confirmation.expires_at),
            entry_confirmed_at: confirmation.confirmed_at, entry_complete: true,
            final_total: { amount: confirmation.amount, currency: confirmation.currency },
            all_payable_charges_included: confirmation.all_payable_charges_included,
            tax_treatment: confirmation.tax_treatment, fees_treatment: confirmation.fees_treatment,
            deferred_charges: confirmation.deferred_charges, competing_currency: confirmation.competing_currency,
            conversion_notice: confirmation.conversion_notice, new_notice: !confirmation.no_new_notice,
            destination_ref: confirmation.destination_ref, destination_match: confirmation.destination_match,
            shipping_selection_verified: true, terms_verified: confirmation.terms_verified,
            terms_fingerprint: confirmation.terms_fingerprint };
        },
      });
    },
  };
  const ensurePage = async () => {
    if (page) return page;
    const pages = await context.pages();
    if (!enabled()) throw new Error('payment_runtime_disabled');
    const matches = pages.filter(candidate => candidate.url() === CHECKOUT);
    if (matches.length !== 1) throw new Error('unique_resident_checkout_required');
    page = matches[0]; return page;
  };
  const ensurePort = async () => {
    await ensurePage();
    const transferredLease = () => {
      if (preheldRelease) {
        const release = preheldRelease; preheldRelease = null; return release;
      }
      return acquireContextLease();
    };
    if (!fixed) fixed = fixture ? createEnhancedFixedPayFixturePort({ page, context, acquireContextLease: transferredLease,
      evidencePorts, orderFencePort: orderLedgerPort, liveGate }) : createEnhancedFixedPayPort({ page, context,
      acquireContextLease: transferredLease, evidencePorts, liveGate });
    return fixed;
  };
  return Object.freeze({
    paymentEnabled: false,
    inspectReadiness: async scope => {
      if (!enabled()) return stop('hosted_payment_disabled');
      try { const { guest, order } = await policyFor(scope);
        return { ...stop('hosted_payment_authority_observed'), status: 'observed',
          guest_state: guest.state, real_order_state: order.ledger?.state ?? 'no_order_intent',
          test_mode: 'exact_kc_one_copy_only' };
      } catch { return stop('hosted_payment_authority_unavailable'); }
    },
    beginSecureEntry: async (scope, { signal = null } = {}) => {
      if (!enabled()) return stop('hosted_payment_disabled');
      if (beginStarted) return stop('hosted_secure_entry_already_started');
      // Reserve before the first metadata await. A concurrent call cannot read
      // the page after this call hands the browser to secure card entry.
      beginStarted = true;
      let handoffReady = false;
      try {
        const initial = await policyFor(scope);
        if (!enabled()) return stop('hosted_payment_disabled');
        if (initial.order.ledger?.state === 'submission_unknown') return stop('hosted_prior_submit_uncertain');
        // Initial inventory and document capture use the very same browser
        // lease that the fixed Pay port retains during secure entry. Transfer
        // its release function once; never acquire a second competing lease.
        preheldRelease = acquireContextLease();
        if (typeof preheldRelease !== 'function') return stop('hosted_browser_busy');
        const port = await ensurePort();
        if (!enabled()) return stop('hosted_payment_disabled');
        // Capture our private document witness before the secure-entry handoff.
        // Once entry_ready returns, no browser read is issued until resume.
        documentHandle = await page.evaluateHandle(() => document);
        if (!await sameDocument()) return stop('entry_document_unverified');
        const current = await policyFor(scope);
        if (!enabled()) return stop('hosted_payment_disabled');
        if (current.order.ledger?.state === 'submission_unknown') return stop('hosted_prior_submit_uncertain');
        const result = await beginFixedEnhancedPaySecureEntry(port, scope, { signal });
        if (!enabled()) return stop('hosted_payment_disabled');
        if (result?.status !== 'entry_ready') return result;
        entryRef = randomUUID(); entryAt = Date.now() / 1000;
        handoffReady = true;
        return Object.freeze({ ...result, entry_ref: entryRef });
      } catch { return stop('hosted_secure_entry_unavailable'); }
      finally {
        if (preheldRelease) { preheldRelease(); preheldRelease = null; }
        if (!handoffReady && fixed) closeFixedEnhancedPayPort(fixed);
        if (!handoffReady) {
          try { await documentHandle?.dispose?.(); } catch {}
          documentHandle = null;
        }
      }
    },
    resumeAfterEntry: async (scope, { signal = null } = {}) => {
      if (!enabled()) return stop('hosted_payment_disabled');
      if (!entryRef || !fixed || entryReady || resuming || attemptStarted)
        return stop('hosted_secure_entry_not_resumable');
      resuming = true;
      try { await confirmationFor(scope);
        const result = await resumeFixedEnhancedPayAfterEntry(fixed, scope, { signal });
        if (result?.code === 'fresh_post_entry_context_verified') entryReady = true;
        return result;
      }
      catch { return stop('hosted_post_entry_confirmation_unavailable'); }
      finally { resuming = false; }
    },
    attemptPayment: async (scope, { signal = null } = {}) => {
      if (!enabled() || !fixed || !entryRef || !entryReady || attemptStarted)
        return stop('hosted_payment_disabled');
      // The adapter closes its fixed port after an attempt. Reserve now so a
      // second caller cannot perform document reads once that lease releases.
      attemptStarted = true; entryReady = false;
      try { await confirmationFor(scope); return await attemptEnhancedPayment(scope, fixed,
        { enablePay: true, liveGate: true, signal }); }
      catch { return stop('hosted_payment_outcome_uncertain'); }
    },
    close: async () => {
      closed = true;
      if (fixed) { closeFixedEnhancedPayPort(fixed); await waitForFixedEnhancedPayPortSettlement(fixed); }
      try { await documentHandle?.dispose?.(); } catch {}
      documentHandle = null;
    },
  });
}

export function createHostedPaymentRuntime(options = {}) {
  if (Object.keys(options).some(key => !['context', 'acquireContextLease', 'liveGate'].includes(key)))
    throw new TypeError('fixed_runtime_configuration_required');
  return factory(options);
}
/** Isolated temporary files and intercepted browser only; never a live path. */
export function createHostedPaymentRuntimeFixture(options = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || ![options.policyPath, options.confirmationPath,
    options.guestLedgerDirectory, options.accessStopPath].every(path => isAbsolute(path || '')) ||
    !options.orderLedgerPort) throw new TypeError('fixture_context_required');
  return factory({ ...options, fixture: true });
}
