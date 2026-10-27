/** Composition only: current read-only quote -> fixed merchant reader -> the
 * existing real-order ledger. No browser/runtime route, payment dispatcher,
 * new ledger, initialization or configuration switch is installed here.
 */
import { inspectCurrentEnhancedPaymentQuote, waitForEnhancedPaymentQuoteSettlement } from './discogs_enhanced_payment_quote.mjs';
import { attemptEnhancedPayment } from './discogs_enhanced_payment_adapter.mjs';
import { createEnhancedOrderLedgerPort } from './discogs_enhanced_order_ledger_port.mjs';
import * as merchantEvidence from './discogs_merchant_order_evidence.mjs';
const { readCurrentMerchantOrderEvidence, KC_ORDER_SCOPE } = merchantEvidence;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const ORDER_ID = /^[0-9]{1,20}-[0-9]{1,20}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const EXPECTED_KEYS = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller', 'quoted_total', 'currency',
  'quote_fingerprint', 'rules_revision', 'destination_ref', 'payment_ref', 'quote_expires_at'];
const SCOPE_KEYS = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller', 'one_copy_limit', 'currency', 'max_total',
  'rules_revision', 'quote_fingerprint', 'destination_ref', 'payment_ref', 'terms_fingerprint', 'expires_at'];
const QUOTE_KEYS = new Set(['source', 'intent_id', 'release_id', 'listing_id', 'seller', 'rules_revision', 'quantity', 'currency',
  'quote_fingerprint', 'destination_ref', 'payment_ref', 'final_quote_verified', 'all_payable_charges_included', 'payment_action',
  'shipping_selection_verified', 'selected_shipping_ref', 'selected_shipping_label', 'observed_at', 'expires_at',
  'item', 'shipping', 'tax', 'fees', 'fees_absent_verified', 'total']);
const PAGE_KEYS = ['order_id', 'release_id', 'listing_id', 'seller', 'total', 'currency', 'displayed_status', 'observed_at', 'source'];
const PAYMENT_STATUSES = new Set(['Invoice Sent', 'Payment Pending', 'Payment Received', 'In Progress', 'Shipped']);
const QUOTE_GAPS = new Set(['provider_dom_qualification', 'provider_frame_and_document', 'provider_explicit_currency',
  'provider_amount_matches_merchant', 'provider_final_all_in_total', 'final_tax_treatment', 'mandatory_charges_finality',
  'provider_shipping_selection', 'provider_delivery_binding', 'provider_terms']);
const STATES = new Set(['prepared', 'submission_unknown', 'order_seen', 'identity_conflict']);
const record = (value, keys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const fields = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(fields).length === keys.length && keys.every(key => fields[key]?.enumerable && Object.hasOwn(fields[key], 'value'));
};
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ?
  `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const cents = amount => typeof amount === 'string' && MONEY.test(amount) ? BigInt(amount.split('.')[0]) * 100n + BigInt(amount.split('.')[1]) : null;
const stopped = code => ({ schema: 'discogs_enhanced_checkout_coordinator_v1', status: 'blocked', code,
  payment_allowed: false, purchase_allowed: false, final_submission_allowed: false, may_submit_again: false });
const immutable = value => {
  if (value && typeof value === 'object') { for (const item of Object.values(value)) immutable(item); Object.freeze(value); }
  return value;
};
function expectedValid(value) {
  return record(value, EXPECTED_KEYS) && [value.intent_id, value.approval_ref, value.destination_ref, value.payment_ref].every(id => UUID.test(id || '')) &&
    value.release_id === 3422100 && value.listing_id === '1366950910' && value.seller === 'Mailman21842' && value.currency === 'USD' &&
    cents(value.quoted_total) !== null && cents(value.quoted_total) <= 505n && HASH.test(value.quote_fingerprint || '') &&
    Number.isSafeInteger(value.rules_revision) && value.rules_revision >= 0 && finite(value.quote_expires_at);
}
function expectedSnapshot(expected) {
  if (!expectedValid(expected)) throw new TypeError('immutable_expected_order_required');
  return Object.freeze({ ...expected });
}
function safeBindingSnapshot(scope, quote) {
  if (!record(scope, SCOPE_KEYS) || !quote || typeof quote !== 'object' || Array.isArray(quote) ||
      Object.keys(quote).some(key => !QUOTE_KEYS.has(key)) || !record(quote, Object.keys(quote)) ||
      quote.source !== 'verified_noncommitting_final_quote' || quote.final_quote_verified !== true ||
      !record(quote.total, ['amount', 'currency'])) throw new TypeError('verified_final_quote_binding_required');
  for (const key of Object.keys(quote)) {
    const value = quote[key];
    if (['item', 'shipping', 'tax', 'fees', 'total'].includes(key)) {
      if (value !== null && (!record(value, ['amount', 'currency']) || typeof value.amount !== 'string' ||
          typeof value.currency !== 'string')) throw new TypeError('verified_final_quote_binding_required');
    } else if (!['string', 'number', 'boolean'].includes(typeof value) && value !== null) throw new TypeError('verified_final_quote_binding_required');
  }
  if (Object.values(scope).some(value => !['string', 'number'].includes(typeof value))) throw new TypeError('verified_final_quote_binding_required');
  return { scope: immutable(structuredClone(scope)), quote: immutable(structuredClone(quote)) };
}

/** Bind the existing ledger's authoritative validator to immutable current
 * scope/quote metadata. This is not authorization or ledger initialization.
 * Any later mutation is rejected before the actual fixed CLI can fence.
 */
export function createBoundEnhancedOrderFencePort(scope, quote, options = {}) {
  if (!record(options, []) && !record(options, ['ledgerPort'])) throw new TypeError('fixed_fence_port_configuration_required');
  if (Object.hasOwn(options, 'ledgerPort') && process.env.NODE_TEST_CONTEXT === undefined) throw new TypeError('fixture_ledger_override_forbidden');
  const ledgerPort = options.ledgerPort ?? createEnhancedOrderLedgerPort();
  if (!ledgerPort || typeof ledgerPort.fencePossibleSubmit !== 'function') throw new TypeError('existing_order_ledger_port_required');
  const bound = safeBindingSnapshot(scope, quote);
  const expectedOrder = expectedSnapshot({ intent_id: bound.scope.intent_id, approval_ref: bound.scope.approval_ref,
    release_id: bound.scope.release_id, listing_id: bound.scope.listing_id, seller: bound.scope.seller,
    quoted_total: bound.quote.total.amount, currency: bound.scope.currency, quote_fingerprint: bound.scope.quote_fingerprint,
    rules_revision: bound.scope.rules_revision, destination_ref: bound.scope.destination_ref, payment_ref: bound.scope.payment_ref,
    quote_expires_at: bound.quote.expires_at });
  return Object.freeze({ expectedOrder,
    fencePossibleSubmit: async (intentId, currentQuote, currentScope) => {
      try {
        const current = safeBindingSnapshot(currentScope, currentQuote);
        if (intentId !== expectedOrder.intent_id || canonical(current) !== canonical(bound)) return stopped('immutable_order_binding_changed');
        // Existing CLI validates all quote/currency/tax/fee/scope gates against
        // wall time, persists prepared metadata then atomically fences unknown.
        return await ledgerPort.fencePossibleSubmit(intentId, bound.quote, bound.scope);
      } catch { return stopped('immutable_order_binding_unverified'); }
    },
  });
}
function positiveEvidence(report, expected, { now, afterAttemptAt, expectedOrderId }) {
  const value = report?.status === 'observed' ? report.order_page_evidence : null;
  if (!record(value, PAGE_KEYS) || !ORDER_ID.test(value.order_id || '') ||
      expectedOrderId !== null && value.order_id !== expectedOrderId || value.source !== 'normal_order_page' ||
      value.release_id !== expected.release_id || value.listing_id !== expected.listing_id ||
      typeof value.seller !== 'string' || value.seller.toLowerCase() !== expected.seller.toLowerCase() ||
      value.total !== expected.quoted_total || value.currency !== expected.currency ||
      !PAYMENT_STATUSES.has(value.displayed_status) || !finite(value.observed_at) ||
      value.observed_at < afterAttemptAt || value.observed_at > now || now - value.observed_at >= 300) return null;
  return Object.freeze({ ...value });
}
function ledgerReport(result, expected) {
  const row = result?.status === 'observed' && result.durable === true ? result.ledger : null;
  if (!row || row.intent_id !== expected.intent_id || row.release_id !== expected.release_id || !STATES.has(row.state) ||
      ![null, undefined].includes(row.order_id) && !ORDER_ID.test(row.order_id || '') ||
      !['unknown', 'unpaid', 'pending', 'paid'].includes(row.payment_state) || typeof row.review_required !== 'boolean' || row.may_submit_again !== false) return null;
  return { intent_id: row.intent_id, release_id: row.release_id, state: row.state, order_id: row.order_id ?? null,
    payment_state: row.payment_state, review_required: row.review_required, may_submit_again: false, purchase_allowed: false };
}
function buildCoordinator({ expectedOrder = null, ledgerPort, readQuote, readMerchant }) {
  const expected = expectedOrder === null ? null : expectedSnapshot(expectedOrder);
  if (!ledgerPort || typeof ledgerPort.reconcile !== 'function') throw new TypeError('existing_order_ledger_port_required');
  return Object.freeze({
    paymentEnabled: false,
    inspectPaymentReadiness: async (page, { signal = null, destinationMatcher = null } = {}) => {
      try {
        const quote = await readQuote(page, { signal, destinationMatcher });
        if (signal?.aborted) return stopped('enhanced_checkout_inspection_cancelled');
        const missingEvidence = Array.isArray(quote?.missing_evidence) ? quote.missing_evidence.filter(key => QUOTE_GAPS.has(key)) : [];
        return { ...stopped('enhanced_checkout_readiness_report'), status: 'observed', quote,
          missing_evidence: missingEvidence, missing_profiles: [
            ...(!quote?.gates?.provider_dom_qualification ? ['provider_final_quote_dom'] : []),
            ...(!quote?.gates?.provider_frame_and_document ? ['provider_quote_frame_association'] : []),
            ...(!quote?.gates?.provider_delivery_binding ? ['provider_delivery_destination'] : []),
            ...(!quote?.gates?.provider_terms ? ['provider_terms'] : []),
            'verified_pay_control', 'verified_selected_payment_method', 'merchant_positive_order_dom'],
          payment: await attemptEnhancedPayment(null, null, { enablePay: false, signal }) };
      } catch { return stopped('enhanced_checkout_inspection_unavailable'); }
    },
    /** Production has no enable flag, control port or dispatcher parameter. */
    attemptPayment: (scope, { signal = null } = {}) => attemptEnhancedPayment(scope, null, { enablePay: false, signal }),
    reconcileCurrentMerchantOrder: async (page, { signal = null, afterAttemptAt, expectedOrderId = null } = {}) => {
      if (!expected || !finite(afterAttemptAt) || afterAttemptAt > Date.now() / 1000 ||
          expectedOrderId !== null && !ORDER_ID.test(expectedOrderId || '')) return stopped('merchant_reconciliation_context_required');
      if (signal?.aborted) return stopped('merchant_reconciliation_cancelled');
      try {
        // Fixed production reader returns null until real receipt DOM is
        // qualified. Neither a zero history nor a provider token becomes proof.
        const report = await readMerchant(page, KC_ORDER_SCOPE, { signal });
        if (signal?.aborted) return stopped('merchant_reconciliation_cancelled');
        const evidence = positiveEvidence(report, expected, { now: Date.now() / 1000, afterAttemptAt, expectedOrderId });
        const recorded = await ledgerPort.reconcile(expected.intent_id, evidence);
        if (signal?.aborted) return stopped('merchant_reconciliation_cancelled');
        const ledger = ledgerReport(recorded, expected);
        if (!ledger) return stopped('merchant_reconciliation_ledger_unverified');
        return { ...stopped('merchant_reconciliation_recorded'), status: 'observed', ledger,
          positive_order_evidence_recorded: evidence !== null && ledger.state === 'order_seen',
          lookup_evidence: evidence === null ? 'no_qualified_positive_order_evidence' : 'normalized_normal_order_page',
          absence_verified: false, provider_draft_status: 'unverified' };
      } catch { return stopped('merchant_reconciliation_unavailable'); }
    },
  });
}
export function createEnhancedCheckoutCoordinator(options = {}) {
  if (!record(options, []) && !record(options, ['expectedOrder'])) throw new TypeError('fixed_production_coordinator_configuration_required');
  const expectedOrder = options.expectedOrder ?? null;
  return buildCoordinator({ expectedOrder, ledgerPort: createEnhancedOrderLedgerPort(),
    readQuote: inspectCurrentEnhancedPaymentQuote, readMerchant: readCurrentMerchantOrderEvidence });
}
/** Isolated ports only. No production path, dispatcher or enable flag override. */
export function createEnhancedCheckoutCoordinatorFixture({ expectedOrder = null, ledgerPort,
  readQuote = inspectCurrentEnhancedPaymentQuote, readMerchant = readCurrentMerchantOrderEvidence } = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined) throw new Error('fixture_context_required');
  if (typeof readQuote !== 'function' || typeof readMerchant !== 'function') throw new TypeError('fixed_readers_required');
  return buildCoordinator({ expectedOrder, ledgerPort, readQuote, readMerchant });
}

/** The existing context owner calls this before releasing its shared lease.
 * Cancellation responses may precede actual browser-read settlement.
 */
export async function waitForEnhancedCheckoutCoordinatorSettlement(page) {
  await Promise.all([waitForEnhancedPaymentQuoteSettlement(page),
    typeof merchantEvidence.waitForMerchantOrderEvidenceSettlement === 'function' ?
      merchantEvidence.waitForMerchantOrderEvidenceSettlement(page) : Promise.resolve()]);
}
