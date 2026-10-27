/** Dormant enhanced-checkout payment boundary.
 *
 * Nothing wires this to a browser or a payment method. The current enhanced
 * checkout collector reports an observed subtotal/total, not a final payable
 * quote or a qualified Pay control. A future trusted production port must
 * supply those independently. This module never reads or enters card data.
 * The existing real-order ledger must persist submission_unknown BEFORE the
 * port may issue the one committing action. A completed click is still unknown.
 */

const URL = 'https://www.discogs.com/marketplace/checkout';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const MAX_MS = 30_000;
const active = new Set();
const PORTS = ['readCurrentState', 'readFinalQuote', 'readDestination', 'readPaymentMethod',
  'readTerms', 'readAuthorization', 'readPayControl', 'fencePossibleSubmit', 'dispatchObservedPay'];
const SCOPE_KEYS = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller',
  'one_copy_limit', 'currency', 'max_total', 'rules_revision', 'quote_fingerprint',
  'destination_ref', 'payment_ref', 'terms_fingerprint', 'expires_at'];
const CONTROL_KEYS = ['source', 'url', 'control_ref', 'control_fingerprint', 'observed_label',
  'visible', 'enabled', 'selector_verified', 'commit_semantics', 'observed_at', 'expires_at'];

const record = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` :
  value && typeof value === 'object' ?
    `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` :
    JSON.stringify(value);
const cents = value => typeof value === 'string' && MONEY.test(value) ?
  BigInt(value.split('.')[0]) * 100n + BigInt(value.split('.')[1]) : null;
const stopped = (code, intentId = null) => Object.freeze({ status: 'blocked', code,
  intent_id: intentId, order_status: 'unverified', payment_status: 'unverified',
  purchase_allowed: false, may_submit_again: false });

function scopeValid(scope, now) {
  return record(scope, SCOPE_KEYS) &&
    [scope.intent_id, scope.approval_ref, scope.destination_ref, scope.payment_ref].every(value => UUID.test(value || '')) &&
    scope.release_id === 3422100 && scope.listing_id === '1366950910' &&
    scope.seller === 'Mailman21842' && scope.one_copy_limit === 1 &&
    scope.currency === 'USD' && scope.max_total === '5.05' &&
    Number.isSafeInteger(scope.rules_revision) && scope.rules_revision >= 0 &&
    HASH.test(scope.quote_fingerprint || '') && HASH.test(scope.terms_fingerprint || '') &&
    Number.isFinite(scope.expires_at) && now < scope.expires_at && scope.expires_at - now <= 300;
}

function evidenceValid(scope, evidence, now, intentState) {
  if (!scopeValid(scope, now)) return false;
  const { state, quote, destination, paymentMethod, terms, authorization, control } = evidence;
  if (!state || state.source !== 'current_guarded_candidate' || state.intent_id !== scope.intent_id ||
      state.release_id !== scope.release_id || state.listing_id !== scope.listing_id ||
      state.seller !== scope.seller || state.rules_revision !== scope.rules_revision ||
      state.claim_state !== 'cart_observed' || state.owned !== false || state.active !== true ||
      state.kill_switch !== false || state.one_copy_limit !== 1 || state.intent_state !== intentState ||
      !quote || quote.source !== 'verified_noncommitting_final_quote' ||
      quote.intent_id !== scope.intent_id || quote.release_id !== scope.release_id ||
      quote.listing_id !== scope.listing_id || quote.seller !== scope.seller ||
      quote.rules_revision !== scope.rules_revision || quote.quantity !== 1 ||
      quote.currency !== scope.currency || quote.quote_fingerprint !== scope.quote_fingerprint ||
      quote.destination_ref !== scope.destination_ref || quote.payment_ref !== scope.payment_ref ||
      quote.final_quote_verified !== true || quote.all_payable_charges_included !== true ||
      quote.payment_action !== 'none' || quote.shipping_selection_verified !== true ||
      !UUID.test(quote.selected_shipping_ref || '') ||
      typeof quote.selected_shipping_label !== 'string' || quote.selected_shipping_label.length < 1 ||
      quote.selected_shipping_label.length > 100 ||
      !Number.isFinite(quote.observed_at) || !Number.isFinite(quote.expires_at) ||
      quote.observed_at > now || now >= quote.expires_at || now - quote.observed_at > 300 ||
      quote.expires_at - quote.observed_at > 300 || quote.expires_at > scope.expires_at ||
      !destination || destination.source !== 'verified_current_checkout_destination' ||
      destination.current !== true || destination.selected_for_checkout !== true ||
      destination.destination_ref !== scope.destination_ref ||
      destination.selected_shipping_ref !== quote.selected_shipping_ref ||
      destination.approved_for_intent !== scope.intent_id ||
      !record(paymentMethod, ['source', 'selected', 'method_kind', 'payment_ref', 'approved_for_intent']) ||
      paymentMethod.source !== 'verified_current_enhanced_payment_method' ||
      paymentMethod.selected !== true || paymentMethod.method_kind !== 'card' ||
      paymentMethod.payment_ref !== scope.payment_ref ||
      paymentMethod.approved_for_intent !== scope.intent_id ||
      !terms || terms.source !== 'owner_confirmed_current_cart_terms' ||
      terms.current !== true || terms.fingerprint !== scope.terms_fingerprint ||
      terms.approved_for_intent !== scope.intent_id ||
      !authorization || authorization.source !== 'owner_approved_exact_order' ||
      authorization.current !== true || authorization.revoked !== false ||
      canonical(authorization.scope) !== canonical(scope) ||
      !record(control, CONTROL_KEYS) || control.source !== 'verified_current_enhanced_pay_control' ||
      control.url !== URL || !UUID.test(control.control_ref || '') ||
      !HASH.test(control.control_fingerprint || '') ||
      typeof control.observed_label !== 'string' || control.observed_label.length < 1 ||
      control.observed_label.length > 100 || control.visible !== true || control.enabled !== true ||
      control.selector_verified !== true || control.commit_semantics !== 'creates_order_or_payment' ||
      !Number.isFinite(control.observed_at) || !Number.isFinite(control.expires_at) ||
      control.observed_at > now || now >= control.expires_at || now - control.observed_at > 20 ||
      control.expires_at > quote.expires_at) return false;
  // A verified complete final quote may establish that there is no separate
  // buyer-fee line. Missing/unknown fees without that explicit evidence stop.
  const feeAbsent = quote.fees === null && quote.fees_absent_verified === true;
  const parts = [quote.item, quote.shipping, quote.tax, quote.total,
    ...(feeAbsent ? [] : [quote.fees])];
  if (parts.some(part => !record(part, ['amount', 'currency']) ||
      part.currency !== scope.currency || cents(part.amount) === null)) return false;
  const amounts = [cents(quote.item.amount), cents(quote.shipping.amount),
    cents(quote.tax.amount), feeAbsent ? 0n : cents(quote.fees.amount), cents(quote.total.amount)];
  return amounts[0] + amounts[1] + amounts[2] + amounts[3] === amounts[4] &&
    amounts[4] <= cents(scope.max_total);
}

/** A future trusted port must qualify the actual observed Pay control and must
 * dispatch ONLY that bound control. No selector or payment method is inferred
 * here. The installed service supplies no such port; even enablePay:true is
 * test-only until the actual final-quote and control producers are verified.
 * fencePossibleSubmit must map to the durable DiscogsOrderLedger transition.
 */
export async function attemptEnhancedPayment(scope, ports, {
  enablePay = false, signal = null, clock = () => Date.now(), timeoutMs = MAX_MS,
} = {}) {
  if (enablePay !== true || process.env.NODE_TEST_CONTEXT === undefined) {
    return stopped('enhanced_payment_disabled');
  }
  if (!scopeValid(scope, clock() / 1000)) return stopped('exact_payment_scope_required');
  if (!ports || ports.kind !== 'trusted_production_enhanced_payment_v1' ||
      PORTS.some(name => typeof ports[name] !== 'function') ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_MS) {
    return stopped('trusted_payment_ports_unavailable');
  }
  scope = Object.freeze({ ...scope });
  if (active.has(scope.intent_id)) return stopped('payment_attempt_busy', scope.intent_id);
  active.add(scope.intent_id);
  const deadline = clock() + timeoutMs;
  let fenceAttempted = false;
  const pending = new Set();
  let finished = false;
  const releaseIfSettled = () => {
    if (finished && pending.size === 0) active.delete(scope.intent_id);
  };
  const live = expiry => !signal?.aborted && clock() < deadline &&
    scopeValid(scope, clock() / 1000) && (expiry === null || clock() / 1000 < expiry);
  const checked = async (operation, expiry = null) => {
    if (!live(expiry)) throw new Error('stopped');
    const remaining = Math.min(deadline, scope.expires_at * 1000,
      expiry === null ? Infinity : expiry * 1000) - clock();
    if (remaining <= 0) throw new Error('stopped');
    let timer, onAbort;
    const interrupted = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('stopped')), remaining);
      if (signal) {
        onAbort = () => reject(new Error('stopped'));
        signal.addEventListener('abort', onAbort, { once: true });
      }
    });
    try {
      // Promise.race stops our continuation, not a previously issued port
      // operation. Retain the per-intent lease until that operation settles.
      const task = Promise.resolve(operation());
      pending.add(task);
      const settled = () => { pending.delete(task); releaseIfSettled(); };
      task.then(settled, settled);
      const value = await Promise.race([task, interrupted]);
      if (!live(expiry)) throw new Error('stopped');
      return value;
    } finally {
      clearTimeout(timer);
      if (onAbort) signal.removeEventListener('abort', onAbort);
    }
  };
  const snapshot = async intentState => {
    const evidence = {
      state: await checked(() => ports.readCurrentState(scope)),
      quote: await checked(() => ports.readFinalQuote(scope)),
      destination: await checked(() => ports.readDestination(scope)),
      paymentMethod: await checked(() => ports.readPaymentMethod(scope)),
      terms: await checked(() => ports.readTerms(scope)),
      authorization: await checked(() => ports.readAuthorization(scope)),
      control: await checked(() => ports.readPayControl(scope)),
    };
    if (!evidenceValid(scope, evidence, clock() / 1000, intentState)) throw new Error('evidence_unverified');
    return evidence;
  };
  try {
    const first = await snapshot('prepared');
    const second = await snapshot('prepared');
    if (canonical(first) !== canonical(second)) return stopped('payment_context_changed', scope.intent_id);
    // A callback can durably fence and then fail to return. From this point an
    // uncertain result is permanent; neither this call nor a restart may retry.
    fenceAttempted = true;
    const fence = await checked(() => ports.fencePossibleSubmit(scope.intent_id, first.quote, scope));
    if (!fence || fence.intent_id !== scope.intent_id || fence.state !== 'submission_unknown' ||
        fence.may_submit_again !== false || fence.durable !== true) {
      return stopped('durable_order_fence_unverified', scope.intent_id);
    }
    const after = await snapshot('submission_unknown');
    if (canonical({ ...first, state: { ...first.state, intent_state: 'submission_unknown' } }) !==
        canonical(after)) return stopped('submission_unknown', scope.intent_id);
    // Recheck the exact evidence after all earlier browser/ledger awaits. The
    // state producer is last, directly before the one dispatch port call.
    const fresh = {
      quote: await checked(() => ports.readFinalQuote(scope)),
      destination: await checked(() => ports.readDestination(scope)),
      paymentMethod: await checked(() => ports.readPaymentMethod(scope)),
      terms: await checked(() => ports.readTerms(scope)),
      authorization: await checked(() => ports.readAuthorization(scope)),
      control: await checked(() => ports.readPayControl(scope)),
      state: await checked(() => ports.readCurrentState(scope)),
    };
    if (!evidenceValid(scope, fresh, clock() / 1000, 'submission_unknown') ||
        canonical(fresh) !== canonical(after)) return stopped('submission_unknown', scope.intent_id);
    const expiry = Math.min(fresh.quote.expires_at, fresh.control.expires_at);
    if (!live(expiry)) return stopped('submission_unknown', scope.intent_id);
    // The port may complete a dispatched browser action after timeout or abort.
    // Never infer payment success, inspect a result as confirmation, or retry.
    await checked(() => {
      const current = clock();
      const remaining = Math.min(deadline, scope.expires_at * 1000,
        fresh.quote.expires_at * 1000, fresh.control.expires_at * 1000) - current;
      if (signal?.aborted || remaining <= 0 ||
          !scopeValid(scope, current / 1000)) throw new Error('dispatch_boundary_expired');
      return ports.dispatchObservedPay(Object.freeze({ scope,
        control_ref: fresh.control.control_ref,
        control_fingerprint: fresh.control.control_fingerprint,
        timeout_ms: remaining, signal,
      }));
    }, expiry);
    return stopped('submission_unknown', scope.intent_id);
  } catch {
    return stopped(fenceAttempted ? 'submission_unknown' : 'payment_preflight_unverified', scope.intent_id);
  } finally {
    finished = true;
    releaseIfSettled();
  }
}
