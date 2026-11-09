/** Fixed, read-only observation of a recently created, displayed empty cart.
 * Document age is not merchant-state freshness or an atomic cart transaction.
 */
const CART_URL = 'https://www.discogs.com/sell/cart/';
const READ_TIMEOUT_MS = 1_500;
const MAX_DOCUMENT_AGE_MS = 5_000;
const TIMEOUT = Symbol('empty_cart_timeout');

function blocked(code) {
  return { schema: 'discogs_empty_cart_dom_v1', status: 'blocked', code,
    source: 'visible_cart_dom', empty: false, purchase_allowed: false };
}

/** Self-contained for page.evaluate; never reads account details or takes action. */
export function collectEmptyCartDom() {
  const stop = code => ({ schema: 'discogs_empty_cart_dom_v1', status: 'blocked', code,
    source: 'visible_cart_dom', empty: false, purchase_allowed: false });
  if (typeof document === 'undefined' || typeof location === 'undefined') return stop('dom_unavailable');
  if (location.href !== 'https://www.discogs.com/sell/cart/') return stop('cart_url_unverified');
  if (document.readyState !== 'complete') return stop('cart_not_ready');
  try {
    // A repeated DOM read must not make an old cart document look newly loaded.
    // performance.now() retains document lifetime through an old BFCache restore.
    const documentAge = () => {
      try {
        const timing = document.defaultView?.performance;
        if (typeof timing?.now !== 'function') return null;
        const age = timing.now();
        return typeof age === 'number' && Number.isFinite(age) && age >= 0 ? age : null;
      } catch { return null; }
    };
    const initialAge = documentAge();
    if (initialAge === null) return stop('cart_document_age_unverified');
    if (initialAge > 5_000) return stop('cart_document_stale');
    const visible = (element, allowZeroSize = false) => {
      if (!element?.isConnected) return false;
      for (let node = element; node; node = node.parentElement) {
        if (node.hidden || node.getAttribute('aria-hidden') === 'true') return false;
        const style = document.defaultView.getComputedStyle(node);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            style.contentVisibility === 'hidden' || style.opacity === '0') return false;
      }
      // A genuinely empty .orders container can have zero height.
      return allowZeroSize || [...element.getClientRects()]
        .some(rect => rect.width > 0 && rect.height > 0);
    };
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu
      .test(document.title ?? '') || document.querySelector(
        'input[type="password"],iframe[src*="challenges.cloudflare.com"],form[action*="/cdn-cgi/"],' +
        '[id*="captcha" i],[class*="captcha" i]')) return stop('access_restricted');
    const roots = document.querySelectorAll('div#page.cart_page.aside_right.with_tabs');
    const mains = document.querySelectorAll('div#page_content[role="main"]');
    const headings = mains.length === 1 ? mains[0].querySelectorAll('h1') : [];
    if (roots.length !== 1 || mains.length !== 1 || headings.length !== 1 ||
        !roots[0].contains(mains[0]) || !mains[0].contains(headings[0]) ||
        !visible(roots[0]) || !visible(mains[0]) || !visible(headings[0]) ||
        headings[0].children.length !== 0 || headings[0].textContent?.trim() !== 'Your cart is empty') {
      return stop('empty_cart_heading_unverified');
    }
    const orders = document.querySelectorAll('div.orders');
    if (orders.length !== 1 || !mains[0].contains(orders[0]) || !visible(orders[0], true) ||
        orders[0].children.length !== 0 || orders[0].textContent?.trim() !== '') {
      return stop('empty_orders_unverified');
    }
    if (document.querySelector(
      '[id^="cart-order-"].order_container,tr.order_row,a.item_link[href^="/sell/item/"]')) {
      return stop('cart_items_present');
    }
    const finalAge = documentAge();
    if (finalAge === null || finalAge < initialAge) return stop('cart_document_age_unverified');
    if (finalAge > 5_000) return stop('cart_document_stale');
    return { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
      source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
      document_age_ms: finalAge, purchase_allowed: false };
  } catch { return stop('empty_cart_dom_read_failed'); }
}

async function bounded(operation, deadline, signal) {
  if (signal?.aborted) throw new Error('cancelled');
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw TIMEOUT;
  let timer;
  try {
    const result = await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => { timer = setTimeout(() => reject(TIMEOUT), remaining); }),
    ]);
    if (signal?.aborted) throw new Error('cancelled');
    return result;
  } finally { clearTimeout(timer); }
}

/** Existing authorized page only: two URL reads and one fixed DOM evaluation. */
export async function inspectCurrentEmptyCart(page, { signal, timeoutMs = READ_TIMEOUT_MS } = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > READ_TIMEOUT_MS) {
    return blocked('invalid_read_options');
  }
  if (!page || typeof page.url !== 'function' || typeof page.evaluate !== 'function') {
    return blocked('unsupported_page_interface');
  }
  const deadline = Date.now() + timeoutMs;
  try {
    const initial = await bounded(() => page.url(), deadline, signal);
    if (initial !== CART_URL) return blocked('cart_url_unverified');
    const readStartedAt = Date.now();
    const result = await bounded(() => page.evaluate(collectEmptyCartDom), deadline, signal);
    const final = await bounded(() => page.url(), deadline, signal);
    if (final !== initial) return blocked('cart_url_changed');
    if (result?.schema !== 'discogs_empty_cart_dom_v1' ||
        result.status !== 'verified_empty' || result.empty !== true ||
        result.source !== 'visible_cart_dom' || result.item_count !== 0 ||
        result.seller_count !== 0 || result.purchase_allowed !== false ||
        typeof result.document_age_ms !== 'number' ||
        !Number.isFinite(result.document_age_ms) || result.document_age_ms < 0 ||
        result.document_age_ms > MAX_DOCUMENT_AGE_MS) {
      return blocked('empty_cart_unverified');
    }
    // Conservatively include the whole bounded read, including transport and
    // post-read URL validation. No refresh or navigation is performed here.
    const elapsed = Date.now() - readStartedAt;
    const documentAgeMs = result.document_age_ms + elapsed;
    if (elapsed < 0 || documentAgeMs > MAX_DOCUMENT_AGE_MS) {
      return blocked('cart_document_stale');
    }
    // URL, scope digest and observation time are attached by the trusted caller,
    // after its own current-state checks. DOM data cannot supply those bindings.
    return { schema: 'discogs_empty_cart_dom_v1', status: 'verified_empty',
      source: 'visible_cart_dom', empty: true, item_count: 0, seller_count: 0,
      cart_url: CART_URL, document_age_ms: documentAgeMs, purchase_allowed: false };
  } catch (error) {
    return blocked(error === TIMEOUT ? 'empty_cart_timeout' :
      signal?.aborted ? 'empty_cart_cancelled' : 'empty_cart_read_failed');
  }
}
