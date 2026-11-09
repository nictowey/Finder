/** Read only the already open enhanced checkout summary. No control access. */
const URL = 'https://www.discogs.com/marketplace/checkout';
const TIMEOUT_MS = 1_500;
const TIMEOUT = Symbol('enhanced_checkout_timeout');
const DOM_STOP_CODES = new Set([
  'dom_unavailable', 'url_unverified', 'page_not_ready', 'access_restricted',
  'seller_heading_unverified', 'summary_unverified', 'row_count_unverified',
  'totals_shape_unverified', 'totals_labels_unverified',
  'displayed_money_unverified', 'visible_total_inconsistent', 'dom_read_failed',
]);

function stopped(code) {
  return { schema: 'discogs_enhanced_checkout_observation_v1', status: 'blocked', code,
    purchase_allowed: false, final_quote_verified: false, listing_identity_verified: false };
}

/** Self-contained for page.evaluate. Never returns item text, address, or payment data. */
export function collectEnhancedCheckoutDom() {
  const stop = code => ({ schema: 'discogs_enhanced_checkout_dom_v1', status: 'blocked', code,
    purchase_allowed: false });
  try {
    if (typeof document === 'undefined' || typeof location === 'undefined') return stop('dom_unavailable');
    if (location.href !== 'https://www.discogs.com/marketplace/checkout') return stop('url_unverified');
    if (document.readyState !== 'complete') return stop('page_not_ready');
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu
      .test(document.title ?? '') || document.querySelector(
        'input[type="password"],form[action*="/cdn-cgi/"],iframe[src*="challenges.cloudflare.com"],' +
        '[id*="captcha" i],[class*="captcha" i]')) {
      return stop('access_restricted');
    }
    const visible = element => {
      if (!element?.isConnected) return false;
      for (let node = element; node; node = node.parentElement) {
        if (node.hidden || node.getAttribute('aria-hidden') === 'true') return false;
        const style = document.defaultView.getComputedStyle(node);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            style.contentVisibility === 'hidden' || style.opacity === '0') return false;
      }
      return [...element.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
    };
    const tokens = element => [...element.classList];
    const named = (root, tag, prefix) => [...root.querySelectorAll(tag)]
      .filter(node => visible(node) && tokens(node).some(token => token.startsWith(prefix)));
    const one = nodes => nodes.length === 1 ? nodes[0] : null;
    // The observed H1/DT/DD use direct text nodes. Hidden descendants may
    // otherwise inject textContent that was never displayed to the user.
    const text = element => {
      if (!element || !visible(element)) return null;
      const collect = node => {
        if (node.nodeType === 3) return node.nodeValue ?? '';
        if (node.nodeType !== 1 || !visible(node)) return '';
        return [...node.childNodes].map(collect).join('');
      };
      return collect(element).replace(/\s+/gu, ' ').trim();
    };
    const heading = one([...document.querySelectorAll('h1')].filter(visible));
    if (!heading || heading.children.length !== 0) return stop('seller_heading_unverified');
    const title = text(heading);
    const seller = /^Checkout from ([A-Za-z0-9][A-Za-z0-9_.-]{0,79})$/u.exec(title ?? '')?.[1];
    if (!seller) return stop('seller_heading_unverified');
    const content = one(named(document, 'div', '_checkout-summary__content_'));
    if (!content) return stop('summary_unverified');
    const list = one(named(content, 'ul', '_checkout-summary__unordered-list_'));
    const totals = one(named(content, 'dl', '_checkout-summary__totals_'));
    if (!list || !totals) return stop('summary_unverified');
    const rows = [...list.children];
    if (rows.length !== 1 || !visible(rows[0]) || rows[0].tagName !== 'LI' ||
        !tokens(rows[0]).some(token => token.startsWith('_checkout-sell-item__list-item_'))) {
      return stop('row_count_unverified');
    }
    const divisions = [...totals.children];
    if (divisions.length !== 3 || divisions.some(node => node.tagName !== 'DIV' || !visible(node))) {
      return stop('totals_shape_unverified');
    }
    const [subtotal, shipping, total] = divisions;
    if (![subtotal, shipping].every(node => tokens(node).some(token =>
      token.startsWith('_checkout-summary__subtotal_'))) ||
      !tokens(total).some(token => token.startsWith('_checkout-summary__total--color-red_'))) {
      return stop('totals_shape_unverified');
    }
    const row = node => {
      const labels = [...node.children].filter(child => child.tagName === 'DT');
      const values = [...node.children].filter(child => child.tagName === 'DD');
      if (labels.length !== 1 || values.length !== 1 || labels[0].children.length !== 0 ||
          values[0].children.length !== 0 || !visible(labels[0]) ||
          !visible(values[0]) || !tokens(values[0]).some(token =>
            token.startsWith('_checkout-summary__money-value_'))) return null;
      return { label: text(labels[0]), value: text(values[0]) };
    };
    const a = row(subtotal), b = row(shipping), c = row(total);
    const method = /^Shipping via (.{1,100})$/u.exec(b?.label ?? '')?.[1];
    if (!a || !b || !c || a.label !== 'Subtotal' || c.label !== 'Total' ||
        !method || method.trim() !== method ||
        /[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/u.test(method)) {
      return stop('totals_labels_unverified');
    }
    const money = value => {
      const match = /^\$((?:0|[1-9][0-9]{0,6})\.[0-9]{2}) USD$/u.exec(value ?? '');
      return match ? { amount: match[1], currency: 'USD' } : null;
    };
    const subtotalAmount = money(a.value), shippingAmount = money(b.value), totalAmount = money(c.value);
    if (!subtotalAmount || !shippingAmount || !totalAmount) return stop('displayed_money_unverified');
    const cents = value => {
      const [whole, fraction] = value.amount.split('.');
      return BigInt(whole) * 100n + BigInt(fraction);
    };
    if (cents(subtotalAmount) + cents(shippingAmount) !== cents(totalAmount)) {
      return stop('visible_total_inconsistent');
    }
    return { schema: 'discogs_enhanced_checkout_dom_v1', status: 'observed',
      source: 'visible_enhanced_checkout_dom', purchase_allowed: false,
      seller_handle: seller, visible_row_count: 1,
      displayed: { subtotal: subtotalAmount, shipping: shippingAmount, total: totalAmount },
      selected_shipping_label: method };
  } catch { return stop('dom_read_failed'); }
}

/** Fixed current-page read; never navigates, selects a control, or uses payment. */
export async function inspectCurrentEnhancedCheckout(page, { signal = null, timeoutMs = TIMEOUT_MS } = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > TIMEOUT_MS ||
      !page || typeof page.url !== 'function' || typeof page.evaluate !== 'function') {
    return stopped('read_unavailable');
  }
  const deadline = Date.now() + timeoutMs;
  const bounded = async operation => {
    if (signal?.aborted) throw new Error('cancelled');
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw TIMEOUT;
    let timer, onAbort;
    const interruption = new Promise((_, reject) => {
      timer = setTimeout(() => reject(TIMEOUT), remaining);
      if (signal) {
        onAbort = () => reject(new Error('cancelled'));
        signal.addEventListener('abort', onAbort, { once: true });
      }
    });
    try {
      const result = await Promise.race([Promise.resolve().then(operation), interruption]);
      if (signal?.aborted) throw new Error('cancelled');
      if (Date.now() >= deadline) throw TIMEOUT;
      return result;
    } finally {
      clearTimeout(timer);
      if (signal && onAbort) signal.removeEventListener('abort', onAbort);
    }
  };
  try {
    const before = await bounded(() => page.url());
    if (before !== URL) return stopped('url_unverified');
    const snapshot = await bounded(() => page.evaluate(collectEnhancedCheckoutDom));
    const after = await bounded(() => page.url());
    if (after !== before) return stopped('url_changed');
    if (!snapshot || snapshot.schema !== 'discogs_enhanced_checkout_dom_v1' ||
        snapshot.status !== 'observed' || snapshot.source !== 'visible_enhanced_checkout_dom' ||
        snapshot.purchase_allowed !== false || snapshot.visible_row_count !== 1 ||
        typeof snapshot.seller_handle !== 'string' ||
        !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u.test(snapshot.seller_handle) ||
        typeof snapshot.selected_shipping_label !== 'string' ||
        snapshot.selected_shipping_label.length < 1 || snapshot.selected_shipping_label.length > 100 ||
        /[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/u.test(snapshot.selected_shipping_label) ||
        !snapshot.displayed || ['subtotal', 'shipping', 'total'].some(key =>
          !snapshot.displayed[key] || snapshot.displayed[key].currency !== 'USD' ||
          typeof snapshot.displayed[key].amount !== 'string' ||
          !/^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u.test(snapshot.displayed[key].amount))) {
      return stopped(snapshot?.status === 'blocked' && typeof snapshot.code === 'string' &&
        snapshot.code.length <= 48 && DOM_STOP_CODES.has(snapshot.code) ?
        snapshot.code : 'summary_unverified');
    }
    const cents = value => {
      const [whole, fraction] = value.split('.');
      return BigInt(whole) * 100n + BigInt(fraction);
    };
    if (cents(snapshot.displayed.subtotal.amount) + cents(snapshot.displayed.shipping.amount) !==
        cents(snapshot.displayed.total.amount)) return stopped('visible_total_inconsistent');
    return { schema: 'discogs_enhanced_checkout_observation_v1', status: 'observed',
      source: 'visible_enhanced_checkout_dom', purchase_allowed: false,
      final_quote_verified: false, legacy_checkout_compatible: false,
      listing_identity_verified: false, listing_id: null, release_id: null,
      seller_handle: snapshot.seller_handle, visible_row_count: 1,
      quantity: null, quantity_verified: false,
      displayed: {
        subtotal: { amount: snapshot.displayed.subtotal.amount, currency: 'USD' },
        shipping: { amount: snapshot.displayed.shipping.amount, currency: 'USD' },
        total: { amount: snapshot.displayed.total.amount, currency: 'USD' },
        tax: null, fees: null,
      },
      selected_shipping_label: snapshot.selected_shipping_label };
  } catch (error) {
    return stopped(error === TIMEOUT ? 'read_timeout' :
      signal?.aborted ? 'read_cancelled' : 'read_failed');
  }
}
