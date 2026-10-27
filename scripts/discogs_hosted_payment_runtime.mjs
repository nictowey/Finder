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
import { inspectCurrentEnhancedPaymentQuote, waitForEnhancedPaymentQuoteSettlement } from './discogs_enhanced_payment_quote.mjs';
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
const ENTRY_SCOPE = ['schema', 'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
  'expected_guest_head', 'expected_claim_sha256', 'expires_at'];
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
function validEntryScope(scope, now) {
  return exact(scope, ENTRY_SCOPE) && scope.schema === 'discogs_secure_entry_scope_v1' &&
    scope.release_id === 3422100 && scope.listing_id === '1366950910' &&
    scope.seller === 'Mailman21842' && scope.quantity === 1 && scope.currency === 'USD' &&
    scope.max_total === '5.05' && HASH.test(scope.expected_guest_head || '') &&
    HASH.test(scope.expected_claim_sha256 || '') && Number.isFinite(scope.expires_at) &&
    now < scope.expires_at && scope.expires_at - now <= 300;
}
function publicEntryEvidence(report, scope) {
  const merchant = report?.merchant_displayed;
  return report?.final_quote_verified === false && report?.purchase_allowed !== true &&
    merchant?.seller_handle === scope.seller && merchant.subtotal?.amount === '0.10' &&
    merchant.shipping?.amount === '4.95' && merchant.total?.amount === '5.05' &&
    [merchant.subtotal, merchant.shipping, merchant.total].every(value => value?.currency === 'USD') &&
    merchant.selected_shipping_label === 'Economy - Media Mail' &&
    report.gates?.provider_dom_qualification === true &&
    report.gates?.provider_frame_and_document === true &&
    report.gates?.provider_explicit_currency === true &&
    report.gates?.provider_amount_matches_merchant === true &&
    report.current_layout_qualifications?.public_pay_control_verified === true &&
    report.guest_pay_metadata?.status === 'observed' &&
    report.guest_pay_metadata?.eligible_count === 1 &&
    report.guest_pay_metadata?.submit_count === 1 &&
    report.guest_pay_metadata?.submit_visible === true &&
    report.guest_pay_metadata?.submit_enabled === true &&
    report.guest_pay_metadata?.button_type_matches === true &&
    report.guest_pay_metadata?.same_document_verified === true &&
    report.guest_pay_metadata?.frame_associated === true &&
    /^Pay \$5\.05(?: USD)?$/u.test(report.guest_pay_metadata?.label || '');
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
function validConfirmation(value, scope, policy, entryRef, entryAt, entryCompletedAt, now) {
  return UUID.test(entryRef || '') && Number.isFinite(entryAt) && exact(value, CONFIRM) &&
    value.schema === 'discogs_enhanced_payment_confirmation_v1' &&
    value.source === 'owner_confirmed_current_checkout' && value.intent_id === scope.intent_id &&
    value.entry_ref === entryRef && UUID.test(value.entry_ref || '') &&
    Number.isFinite(value.confirmed_at) && Math.max(entryAt, entryCompletedAt ?? entryAt) <= value.confirmed_at &&
    value.confirmed_at <= now &&
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

function factory({ context, acquireContextLease, entryGate = false, liveGate = false, policyPath = HOSTED_PAYMENT_POLICY,
  confirmationPath = HOSTED_PAYMENT_CONFIRMATION, guestLedgerDirectory = GUEST_LEDGER,
  accessStopPath = ACCESS_STOP, orderLedgerPort = createEnhancedOrderLedgerPort(), fixture = false } = {}) {
  if (typeof liveGate !== 'boolean' || typeof entryGate !== 'boolean' || typeof acquireContextLease !== 'function' ||
      !context || (liveGate || entryGate) && typeof context.pages !== 'function')
    throw new TypeError('resident_payment_context_required');
  if (!fixture && (policyPath !== HOSTED_PAYMENT_POLICY || confirmationPath !== HOSTED_PAYMENT_CONFIRMATION ||
      guestLedgerDirectory !== GUEST_LEDGER || accessStopPath !== ACCESS_STOP)) throw new TypeError('fixed_payment_paths_required');
  if (![policyPath, confirmationPath, guestLedgerDirectory, accessStopPath].every(isAbsolute)) throw new TypeError('fixed_payment_paths_required');
  let page = null, fixed = null, documentHandle = null, entryRef = null, entryAt = null,
    entryReady = false, beginStarted = false, entryCompleting = false, resuming = false, attemptStarted = false,
    preheldRelease = null, entryRelease = null, entryMode = null, entryCompleted = false,
    entryScope = null, entryCompletedAt = null, closed = false;
  const enabled = () => liveGate && !closed;
  const entryEnabled = () => entryGate && !closed;
  const browserActive = () => (liveGate || entryGate) && !closed;
  const ownerPolicy = async scope => {
    if (!enabled()) throw new Error('payment_runtime_disabled');
    if (!validScope(scope, Date.now() / 1000)) throw new Error('exact_kc_scope_required');
    const policy = await ownerJson(policyPath);
    if (!await preservedAccessStop(accessStopPath)) throw new Error('access_history_unverified');
    const now = Date.now() / 1000;
    if (!enabled() || !validScope(scope, now) || !validPolicy(policy, scope, now))
      throw new Error('current_owner_authorization_required');
    if (entryMode === 'entry_only' && entryCompleted &&
        (policy.expected_guest_head !== entryScope.expected_guest_head ||
         policy.expected_claim_sha256 !== entryScope.expected_claim_sha256))
      throw new Error('entry_guest_claim_changed');
    if (entryMode === 'entry_only' && entryCompleted && policy.authorized_at < entryCompletedAt)
      throw new Error('post_entry_owner_authorization_required');
    return policy;
  };
  const guestFor = async (scope, expectedClaim, expectedHead) => {
    const guest = await inspectGuestCheckoutLedger({ directory: guestLedgerDirectory,
      expectedClaim });
    if (guest?.current_head !== expectedHead || guest.claim_verified !== true ||
        guest.state !== 'guest_form_observed_repeat_blocked') throw new Error('current_guest_claim_required');
    const count = guest.record_count;
    if (!Number.isSafeInteger(count) || count < 3) throw new Error('current_guest_claim_required');
    const claim = await pinnedRecord(guestLedgerDirectory, count - 2);
    const outcome = await pinnedRecord(guestLedgerDirectory, count - 1);
    const observation = await pinnedRecord(guestLedgerDirectory, count);
    const a = claim.value?.payload, b = observation.value?.payload;
    if (claim.hash !== expectedClaim || observation.hash !== expectedHead ||
        claim.value?.kind !== 'attempt' || a?.mode !== 'current_checkout_claim' ||
        outcome.value?.kind !== 'outcome' || observation.value?.kind !== 'form_observation' ||
        b?.previous_claim_sha256 !== claim.hash || b?.previous_outcome_sha256 !== outcome.hash ||
        a?.intent_id !== b?.intent_id || a?.ready_ref !== b?.ready_ref || a?.scope_digest !== b?.scope_digest ||
        b?.release_id !== scope.release_id || b?.listing_id !== scope.listing_id ||
        b?.seller !== scope.seller || b?.quantity !== (scope.one_copy_limit ?? scope.quantity) ||
        b?.currency !== scope.currency || b?.max_total !== scope.max_total) throw new Error('guest_claim_scope_unverified');
    const still = await inspectGuestCheckoutLedger({ directory: guestLedgerDirectory,
      expectedClaim });
    if (still?.current_head !== guest.current_head || still.claim_verified !== true ||
        still.state !== guest.state) throw new Error('guest_claim_changed');
    return guest;
  };
  // Identity and preserved-stop checks are metadata only. Completion uses the
  // original frozen scope: acknowledging finished entry cannot renew its grant
  // or grant any authority to read the page, initialize again, or pay.
  const entryBindingFor = async (scope, signal = null) => {
    if (!entryEnabled() || signal?.aborted)
      throw new Error('secure_entry_scope_unavailable');
    if (!await preservedAccessStop(accessStopPath)) throw new Error('access_history_unverified');
    const guest = await guestFor(scope, scope.expected_claim_sha256, scope.expected_guest_head);
    const stopRetained = await preservedAccessStop(accessStopPath);
    if (!entryEnabled() || signal?.aborted || !stopRetained)
      throw new Error('secure_entry_scope_changed');
    return guest;
  };
  const entryGuestFor = async (scope, signal = null) => {
    if (!validEntryScope(scope, Date.now() / 1000)) throw new Error('secure_entry_scope_unavailable');
    const guest = await entryBindingFor(scope, signal);
    if (!entryEnabled() || signal?.aborted || !validEntryScope(scope, Date.now() / 1000))
      throw new Error('secure_entry_scope_changed');
    return guest;
  };
  const policyFor = async scope => {
    const policy = await ownerPolicy(scope);
    const guest = await guestFor(scope, policy.expected_claim_sha256, policy.expected_guest_head);
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
    if (!browserActive() || !page || !documentHandle || page.url() !== CHECKOUT) return false;
    if (!browserActive()) return false;
    const pages = await context.pages();
    if (!browserActive() || !pages.includes(page)) return false;
    const same = await page.evaluate(expected => document === expected, documentHandle);
    return browserActive() && same === true;
  };
  const confirmationFor = async scope => {
    const policy = await ownerPolicy(scope);
    const confirmation = await ownerJson(confirmationPath);
    if (!validConfirmation(confirmation, scope, policy, entryRef, entryAt, entryCompletedAt, Date.now() / 1000) ||
        !await sameDocument()) throw new Error('current_post_entry_confirmation_required');
    const currentPolicy = await ownerPolicy(scope);
    if (!enabled() || canonical(currentPolicy) !== canonical(policy) ||
        !validConfirmation(confirmation, scope, currentPolicy, entryRef, entryAt, entryCompletedAt, Date.now() / 1000))
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
    if (!browserActive()) throw new Error('payment_runtime_disabled');
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
    inspectEntryReadiness: async (scope, { signal = null } = {}) => {
      if (!entryEnabled()) return stop('hosted_secure_entry_disabled');
      try {
        await entryGuestFor(scope, signal);
        return Object.freeze({ status: 'observed', code: 'anchored_guest_claim_observed',
          card_entry_allowed: false, payment_allowed: false, purchase_allowed: false,
          browser_evidence_pending: true });
      } catch { return stop('hosted_secure_entry_unavailable'); }
    },
    inspectReadiness: async scope => {
      if (!enabled()) return stop('hosted_payment_disabled');
      try { const { guest, order } = await policyFor(scope);
        return { ...stop('hosted_payment_authority_observed'), status: 'observed',
          guest_state: guest.state, real_order_state: order.ledger?.state ?? 'no_order_intent',
          test_mode: 'exact_kc_one_copy_only' };
      } catch { return stop('hosted_payment_authority_unavailable'); }
    },
    beginSecureEntry: async (scope, { signal = null } = {}) => {
      if (!entryEnabled() && !enabled()) return stop('hosted_payment_disabled');
      if (beginStarted) return stop('hosted_secure_entry_already_started');
      // Reserve before the first metadata await. A concurrent call cannot read
      // the page after this call hands the browser to secure card entry.
      beginStarted = true;
      let handoffReady = false;
      try {
        if (entryGate) {
          await entryGuestFor(scope, signal);
          if (!entryEnabled() || signal?.aborted) return stop('hosted_secure_entry_unavailable');
          preheldRelease = acquireContextLease();
          if (typeof preheldRelease !== 'function') return stop('hosted_browser_busy');
          await ensurePage();
          if (!entryEnabled() || signal?.aborted) return stop('hosted_secure_entry_unavailable');
          documentHandle = await page.evaluateHandle(() => document);
          if (!entryEnabled() || signal?.aborted) return stop('hosted_secure_entry_unavailable');
          if (!await sameDocument()) return stop('entry_document_unverified');
          const publicReport = await inspectCurrentEnhancedPaymentQuote(page, { signal });
          await waitForEnhancedPaymentQuoteSettlement(page);
          if (!entryEnabled() || signal?.aborted || !await sameDocument() ||
              !publicEntryEvidence(publicReport, scope)) return stop('public_entry_evidence_unverified');
          await entryGuestFor(scope, signal);
          if (!entryEnabled() || signal?.aborted) return stop('hosted_secure_entry_unavailable');
          entryRef = randomUUID(); entryAt = Date.now() / 1000;
          entryScope = Object.freeze({ ...scope }); entryMode = 'entry_only';
          entryRelease = preheldRelease; preheldRelease = null;
          handoffReady = true;
          return Object.freeze({ status: 'entry_ready', entry_ref: entryRef,
            entry_started_at: entryAt, entry_evidence: 'bound_public_guest_form_verified',
            reads_suspended: true, payment_allowed: false,
            purchase_allowed: false });
        }
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
        if (entryGate && !handoffReady && page) await waitForEnhancedPaymentQuoteSettlement(page);
        if (preheldRelease) { preheldRelease(); preheldRelease = null; }
        if (!handoffReady && fixed) closeFixedEnhancedPayPort(fixed);
        if (!handoffReady) {
          try { await documentHandle?.dispose?.(); } catch {}
          documentHandle = null;
        }
      }
    },
    completeSecureEntry: async (completion, { signal = null } = {}) => {
      if (!exact(completion, ['entry_ref', 'completed_at'])) return stop('secure_entry_completion_unverified');
      // Snapshot own primitive values before any await; never execute getters.
      const completionRef = Object.getOwnPropertyDescriptor(completion, 'entry_ref')?.value;
      const completedAt = Object.getOwnPropertyDescriptor(completion, 'completed_at')?.value;
      const reportedAt = Date.now() / 1000;
      if (!entryEnabled() || entryMode !== 'entry_only' || !entryRelease ||
          entryCompleted || entryCompleting ||
          completionRef !== entryRef || signal?.aborted ||
          !Number.isFinite(completedAt) || completedAt < entryAt ||
          completedAt > reportedAt || reportedAt - completedAt >= 60)
        return stop('secure_entry_completion_unverified');
      entryCompleting = true;
      try {
        await entryBindingFor(entryScope, signal);
        const verifiedAt = Date.now() / 1000;
        if (!entryEnabled() || signal?.aborted || completedAt > verifiedAt || verifiedAt - completedAt >= 60)
          return stop('secure_entry_completion_unverified');
        entryCompleted = true; entryCompletedAt = completedAt;
        entryRelease(); entryRelease = null;
        return Object.freeze({ status: 'observed', code: 'secure_entry_completed',
          entry_ref: entryRef, entry_started_at: entryAt,
          entry_completed_at: completedAt, payment_allowed: false,
          purchase_allowed: false });
      } catch { return stop('secure_entry_completion_unverified'); }
      finally { entryCompleting = false; }
    },
    resumeAfterEntry: async (scope, { signal = null } = {}) => {
      if (!enabled()) return stop('hosted_payment_disabled');
      if (!entryRef || entryReady || resuming || attemptStarted ||
          entryMode === 'entry_only' && !entryCompleted || entryMode !== 'entry_only' && !fixed)
        return stop('hosted_secure_entry_not_resumable');
      resuming = true;
      if (entryMode === 'entry_only') {
        try {
          const initial = await policyFor(scope);
          if (initial.order.ledger?.state === 'submission_unknown') return stop('hosted_prior_submit_uncertain');
          preheldRelease = acquireContextLease();
          if (typeof preheldRelease !== 'function') return stop('hosted_browser_busy');
          if (!await sameDocument()) return stop('entry_document_unverified');
          await confirmationFor(scope);
          const port = await ensurePort();
          if (!enabled() || signal?.aborted) return stop('hosted_payment_disabled');
          const begun = await beginFixedEnhancedPaySecureEntry(port, scope, { signal });
          if (begun?.status !== 'entry_ready' || !enabled()) return stop('hosted_post_entry_handoff_unavailable');
          const result = await resumeFixedEnhancedPayAfterEntry(port, scope, { signal });
          if (!enabled() || signal?.aborted) return stop('hosted_post_entry_handoff_unavailable');
          if (result?.code === 'fresh_post_entry_context_verified') entryReady = true;
          return result;
        } catch { return stop('hosted_post_entry_confirmation_unavailable'); }
        finally {
          if (preheldRelease) { preheldRelease(); preheldRelease = null; }
          if (!entryReady && fixed) closeFixedEnhancedPayPort(fixed);
          resuming = false;
        }
      }
      try { await confirmationFor(scope);
        const result = await resumeFixedEnhancedPayAfterEntry(fixed, scope, { signal });
        if (!enabled() || signal?.aborted) return stop('hosted_post_entry_confirmation_unavailable');
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
      if (page) await waitForEnhancedPaymentQuoteSettlement(page);
      entryRelease?.(); entryRelease = null;
      // A pending begin owns this lease until its own in-flight page read has
      // settled and its finally block releases it. Closing cannot release it
      // early while a driver call is still in progress.
      try { await documentHandle?.dispose?.(); } catch {}
      documentHandle = null;
    },
  });
}

export function createHostedPaymentRuntime(options = {}) {
  if (Object.keys(options).some(key => !['context', 'acquireContextLease', 'entryGate', 'liveGate'].includes(key)))
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
