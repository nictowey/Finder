/** Dormant one-click order boundary. Place order creates an order before payment.
 * No browser runtime, listener, quote producer, terms issuer or ledger port is
 * configured here. Never treats a button as a quote preview or triggers PayPal.
 */
import { inspectCurrentCartDom } from './discogs_cart_inspector.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const ID = /^[1-9][0-9]{0,15}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const CURRENCY = new Set(['USD', 'EUR', 'GBP', 'CAD', 'AUD']);
const MAX_MS = 30_000;
const CHALLENGE = /just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu;

function stop(code, intentId = null) {
  return { status: 'blocked', code, intent_id: intentId, order_submission: 'unavailable',
    payment_execution: 'unavailable', purchase_allowed: false, may_submit_again: false };
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
function cents(value) {
  if (typeof value !== 'string' || !MONEY.test(value)) return null;
  const [whole, fraction] = value.split('.');
  return BigInt(whole) * 100n + BigInt(fraction);
}

function exactScope(scope, now) {
  return scope && typeof scope === 'object' && !Array.isArray(scope) &&
    Object.keys(scope).sort().join('|') === [
      'approval_ref', 'currency', 'destination_ref', 'expires_at', 'intent_id',
      'listing_id', 'max_total', 'one_copy_limit', 'payment_ref', 'quote_fingerprint',
      'release_id', 'rules_revision', 'seller', 'terms_fingerprint',
    ].sort().join('|') &&
    [scope.intent_id, scope.approval_ref, scope.destination_ref, scope.payment_ref]
      .every(value => typeof value === 'string' && UUID.test(value)) &&
    Number.isSafeInteger(scope.release_id) && scope.release_id > 0 &&
    typeof scope.listing_id === 'string' && ID.test(scope.listing_id) &&
    Number.isSafeInteger(Number(scope.listing_id)) &&
    typeof scope.seller === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u.test(scope.seller) &&
    Number.isSafeInteger(scope.rules_revision) && scope.rules_revision >= 0 &&
    scope.one_copy_limit === 1 && typeof scope.one_copy_limit === 'number' &&
    CURRENCY.has(scope.currency) && cents(scope.max_total) !== null &&
    typeof scope.quote_fingerprint === 'string' && HASH.test(scope.quote_fingerprint) &&
    typeof scope.terms_fingerprint === 'string' && HASH.test(scope.terms_fingerprint) &&
    typeof scope.expires_at === 'number' && Number.isFinite(scope.expires_at) &&
    now < scope.expires_at && scope.expires_at - now <= 300;
}

function evidenceMatches(scope, state, quote, destination, terms, authorization, now, intentState) {
  if (!exactScope(scope, now) || !state || !quote || !destination || !terms || !authorization ||
      authorization.source !== 'owner_approved_exact_order' ||
      authorization.current !== true || authorization.revoked !== false ||
      canonical(authorization.scope) !== canonical(scope) ||
      state.source !== 'current_guarded_candidate' ||
      state.intent_id !== scope.intent_id ||
      state.release_id !== scope.release_id || state.listing_id !== scope.listing_id ||
      state.seller !== scope.seller || state.rules_revision !== scope.rules_revision ||
      state.claim_state !== 'cart_observed' || state.owned !== false ||
      state.active !== true || state.kill_switch !== false ||
      state.one_copy_limit !== 1 || state.intent_state !== intentState ||
      quote.source !== 'verified_noncommitting_final_quote' ||
      quote.intent_id !== scope.intent_id ||
      quote.final_quote_verified !== true || quote.all_payable_charges_included !== true ||
      quote.payment_action !== 'none' || quote.quantity !== 1 ||
      quote.shipping_selection_verified !== true ||
      typeof quote.selected_shipping_ref !== 'string' || !UUID.test(quote.selected_shipping_ref) ||
      typeof quote.selected_shipping_label !== 'string' ||
      quote.selected_shipping_label.length < 1 || quote.selected_shipping_label.length > 100 ||
      quote.release_id !== scope.release_id || quote.listing_id !== scope.listing_id ||
      quote.seller !== scope.seller || quote.rules_revision !== scope.rules_revision ||
      quote.currency !== scope.currency ||
      quote.destination_ref !== scope.destination_ref ||
      quote.payment_ref !== scope.payment_ref ||
      quote.quote_fingerprint !== scope.quote_fingerprint ||
      destination.source !== 'verified_current_checkout_destination' ||
      destination.current !== true || destination.selected_for_checkout !== true ||
      destination.destination_ref !== scope.destination_ref ||
      destination.selected_shipping_ref !== quote.selected_shipping_ref ||
      destination.approved_for_intent !== scope.intent_id ||
      !Number.isFinite(quote.observed_at) || !Number.isFinite(quote.expires_at) ||
      !(quote.observed_at <= now && now < quote.expires_at &&
        now - quote.observed_at <= 300 && quote.expires_at - quote.observed_at <= 300) ||
      terms.source !== 'owner_confirmed_current_cart_terms' ||
      terms.fingerprint !== scope.terms_fingerprint ||
      terms.approved_for_intent !== scope.intent_id || terms.current !== true) return false;
  const parts = [quote.item, quote.shipping, quote.tax, quote.fees, quote.total];
  if (parts.some(part => !part || part.currency !== scope.currency ||
      cents(part.amount) === null)) return false;
  const amounts = parts.map(part => cents(part.amount));
  return amounts[0] + amounts[1] + amounts[2] + amounts[3] === amounts[4] &&
    amounts[4] <= cents(scope.max_total);
}

function fixedCartUrl(url) {
  return url === 'https://www.discogs.com/sell/cart/' ||
    url === 'https://www.discogs.com/shop/cart/';
}

/** No injected ports exist in the running service. enableCommit defaults false. */
export async function commitCurrentOrder(page, ports, scope, {
  enableCommit = false, signal = null, clock = () => Date.now(), timeoutMs = MAX_MS,
} = {}) {
  if (enableCommit !== true) return stop('order_commit_disabled');
  if (!page || typeof page.url !== 'function' || typeof page.title !== 'function' ||
      typeof page.locator !== 'function' || typeof page.evaluate !== 'function' ||
      !ports || ['readCurrentState', 'readFinalQuote', 'readCurrentDestination', 'readCurrentTerms',
        'readAuthorization',
        'fencePossibleSubmit'].some(name => typeof ports[name] !== 'function') ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_MS) {
    return stop('trusted_order_ports_unavailable');
  }
  // Take an immutable primitive-only copy so a caller cannot retarget a live
  // attempt by mutating the authorization scope during an awaited operation.
  if (!exactScope(scope, clock() / 1000)) return stop('order_scope_unverified');
  scope = Object.freeze({ ...scope });
  const deadline = clock() + timeoutMs;
  const live = () => !signal?.aborted && clock() < deadline &&
    exactScope(scope, clock() / 1000);
  let fenced = false;
  const checked = async (operation, quoteExpiresAt = null) => {
    const quoteLive = () => quoteExpiresAt === null || clock() / 1000 < quoteExpiresAt;
    if (!live() || !quoteLive()) throw new Error('stopped');
    const remaining = Math.min(deadline - clock(), scope.expires_at * 1000 - clock(),
      quoteExpiresAt === null ? MAX_MS : quoteExpiresAt * 1000 - clock());
    if (remaining <= 0) throw new Error('stopped');
    let timer;
    let onAbort;
    const interruption = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('stopped')), remaining);
      if (signal) {
        onAbort = () => reject(new Error('stopped'));
        signal.addEventListener('abort', onAbort, { once: true });
      }
    });
    let value;
    // Invoke immediately after the guard. A queued microtask could otherwise
    // dispatch an operation after the abort signal changes.
    try { value = await Promise.race([operation(), interruption]); }
    finally {
      clearTimeout(timer);
      if (signal && onAbort) signal.removeEventListener('abort', onAbort);
    }
    if (!live() || !quoteLive()) throw new Error('stopped');
    return value;
  };
  const snapshot = async intentState => {
    const initial = await checked(() => page.url());
    if (!fixedCartUrl(initial) || CHALLENGE.test(await checked(() => page.title()))) {
      throw new Error('cart_or_access_unverified');
    }
    const state = await checked(() => ports.readCurrentState(scope));
    const quote = await checked(() => ports.readFinalQuote(scope));
    const destination = await checked(() => ports.readCurrentDestination(scope));
    const terms = await checked(() => ports.readCurrentTerms(scope));
    const authorization = await checked(() => ports.readAuthorization(scope));
    if (!evidenceMatches(scope, state, quote, destination, terms, authorization,
      clock() / 1000, intentState)) {
      throw new Error('order_evidence_unverified');
    }
    const cart = await checked(() => inspectCurrentCartDom({
      url: () => page.url(), evaluate: (...args) => page.evaluate(...args),
    }, { expectedListingId: scope.listing_id }));
    if (!cart?.observations?.includes('expected_listing_link_observed') ||
        !cart.observations.includes('single_cart_row_observed') ||
        await checked(() => page.url()) !== initial) throw new Error('cart_row_unverified');
    // Visible amounts are observations, never a final quote by themselves. A
    // trusted complete quote must agree with every available cart component.
    // An absent/hidden tax line is not evidence of zero tax; deferred tax stops.
    const observed = cart.observed_quote;
    const sameMoney = (a, b) => a?.amount === b?.amount && a?.currency === b?.currency;
    if (cart.observations.includes('tax_hidden_unknown') ||
        cart.observations.includes('displayed_currency_mismatch') ||
        !observed || observed.source !== 'visible_cart' ||
        !sameMoney(observed.subtotal, quote.item) ||
        !sameMoney(observed.shipping, quote.shipping) ||
        !sameMoney(observed.tax, quote.tax) ||
        !sameMoney(observed.total, quote.total) ||
        !cart.observed_shipping_method ||
        cart.observed_shipping_method.source !== 'visible_cart' ||
        cart.observed_shipping_method.selected_label !== quote.selected_shipping_label) {
      throw new Error('cart_quote_or_shipping_unverified');
    }
    if (!live()) throw new Error('stopped');
    const agreement = page.locator('input.agree_terms[name="agree_terms"]');
    const button = page.locator('button.order_button.checkout_v1');
    if (await checked(() => agreement.count()) !== 1 ||
        await checked(() => button.count()) !== 1 ||
        await checked(() => agreement.isVisible()) !== true ||
        await checked(() => agreement.isChecked()) !== true ||
        await checked(() => button.isVisible()) !== true ||
        await checked(() => button.isEnabled()) !== true ||
        await checked(() => button.getAttribute('type')) !== 'submit' ||
        (await checked(() => button.innerText())).trim() !== 'Place order and pay now' ||
        await checked(() => page.url()) !== initial) throw new Error('final_control_or_terms_unverified');
    return { state, quote, destination, terms, authorization, url: initial, button };
  };
  try {
    const first = await snapshot('prepared');
    const second = await snapshot('prepared');
    if (canonical(first.state) !== canonical(second.state) ||
        canonical(first.quote) !== canonical(second.quote) ||
        canonical(first.destination) !== canonical(second.destination) ||
        canonical(first.terms) !== canonical(second.terms) ||
        canonical(first.authorization) !== canonical(second.authorization) ||
        first.url !== second.url) return stop('order_context_changed', scope.intent_id);
    // The injected ledger port must durably change prepared -> submission_unknown.
    // A failed/uncertain fence never permits the committing control to be used.
    fenced = true; // A callback may persist the fence and then time out.
    const result = await checked(() => ports.fencePossibleSubmit(scope.intent_id));
    if (!result || result.intent_id !== scope.intent_id ||
        result.state !== 'submission_unknown' || result.may_submit_again !== false ||
        result.durable !== true) return stop('durable_order_fence_unverified', scope.intent_id);
    const final = await snapshot('submission_unknown');
    if (canonical({ ...first.state, intent_state: 'submission_unknown' }) !==
          canonical(final.state) ||
        canonical(first.quote) !== canonical(final.quote) ||
        canonical(first.destination) !== canonical(final.destination) ||
        canonical(first.terms) !== canonical(final.terms) ||
        canonical(first.authorization) !== canonical(final.authorization) ||
        first.url !== final.url ||
        !live()) return stop('submission_unknown', scope.intent_id);
    // Browser reads above may have awaited while the rule, claim, quote, terms
    // or owner grant changed. Check all trusted producers again, with state
    // deliberately last. No browser await follows this consequential guard.
    if (await checked(() => page.url()) !== final.url) {
      return stop('submission_unknown', scope.intent_id);
    }
    const freshQuote = await checked(() => ports.readFinalQuote(scope));
    const freshDestination = await checked(() => ports.readCurrentDestination(scope));
    const freshTerms = await checked(() => ports.readCurrentTerms(scope));
    const freshAuthorization = await checked(() => ports.readAuthorization(scope));
    const freshState = await checked(() => ports.readCurrentState(scope));
    if (!evidenceMatches(scope, freshState, freshQuote, freshDestination, freshTerms,
      freshAuthorization, clock() / 1000, 'submission_unknown') ||
        canonical(freshState) !== canonical(final.state) ||
        canonical(freshQuote) !== canonical(final.quote) ||
        canonical(freshDestination) !== canonical(final.destination) ||
        canonical(freshTerms) !== canonical(final.terms) ||
        canonical(freshAuthorization) !== canonical(final.authorization)) {
      return stop('submission_unknown', scope.intent_id);
    }
    // Once issued, Playwright may complete a click after timeout/disconnect.
    // No retry, navigation, payment action or confirmation is attempted here.
    const remaining = Math.min(deadline - clock(), scope.expires_at * 1000 - clock(),
      freshQuote.expires_at * 1000 - clock());
    if (remaining <= 0) return stop('submission_unknown', scope.intent_id);
    await checked(() => final.button.click({ timeout: Math.max(1, remaining) }),
      freshQuote.expires_at);
    return stop('submission_unknown', scope.intent_id);
  } catch {
    return stop(fenced ? 'submission_unknown' : 'order_preflight_unverified',
      typeof scope?.intent_id === 'string' ? scope.intent_id : null);
  }
}
