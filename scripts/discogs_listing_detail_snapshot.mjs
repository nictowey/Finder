/** Fixed, inert read of one visible Discogs item page. No browser APIs or actions. */
export function collectListingDetailDom(expected) {
  const base = { schema: 'discogs_listing_detail_dom_v1', source: 'discogs_rendered_listing',
    status: 'incomplete', complete: false, release_id: null, listing_id: null, url: null,
    observed_at: new Date().toISOString(), media: null, sleeve: null, seller_percent: null,
    feedback: null, item: null, currency: null, reported_shipping_text: null,
    seller_comments: null, seller_comments_supported: false, available: false,
    cart_state: 'unknown',
    purchase_allowed: false };
  const stop = code => ({ ...base, error_code: code });
  const valid = value => typeof value === 'string' && /^[1-9][0-9]{0,15}$/u.test(value) &&
    Number.isSafeInteger(Number(value));
  if (!expected || !valid(expected.release_id) || !valid(expected.listing_id) ||
      Reflect.ownKeys(expected).length !== 2) return stop('invalid_exact_identity');
  if (typeof document === 'undefined' || typeof location === 'undefined') return stop('dom_unavailable');
  const original = location.href;
  if (![ `https://www.discogs.com/shop/item/${expected.listing_id}`,
    `https://www.discogs.com/sell/item/${expected.listing_id}` ].includes(original)) {
    return stop('listing_url_mismatch');
  }
  base.url = original;
  base.release_id = Number(expected.release_id);
  base.listing_id = expected.listing_id;
  try {
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
    const text = (node, max = 160) => {
      if (!visible(node)) return null;
      const value = node.innerText?.trim().replace(/\s+/gu, ' ');
      return typeof value === 'string' && value.length <= max ? value : null;
    };
    const one = (root, selector) => {
      const nodes = [...root.querySelectorAll(selector)].filter(visible);
      return nodes.length === 1 ? nodes[0] : null;
    };
    if (document.readyState === 'loading') return stop('listing_not_ready');
    const main = one(document, 'main');
    if (!main) return stop('listing_main_unavailable');
    const header = one(main, "a.hifi-heading-link-large[href*='/release/']");
    const href = header?.getAttribute('href');
    if (!href || !new RegExp(`^https://www\\.discogs\\.com/release/${expected.release_id}$`, 'u').test(href)) {
      return stop('listing_release_mismatch');
    }
    const gradeDisplay = one(main, 'span.hifi-grade-chip-display');
    const parts = gradeDisplay ? [...gradeDisplay.children].map(element => text(element, 40)) : [];
    if (parts.length !== 5 || parts[0] !== 'Media' || parts[2] !== '|' || parts[3] !== 'Sleeve') {
      return stop('listing_grades_unavailable');
    }
    const grades = { Poor: 'P', Fair: 'F', Good: 'G', 'Good Plus': 'G+',
      'Very Good': 'VG', 'Very Good Plus': 'VG+', 'Near Mint': 'NM', Mint: 'M' };
    base.media = grades[parts[1]] ?? null;
    base.sleeve = grades[parts[4]] ?? null;
    const details = one(main, '[data-testid="item-details"]');
    const comment = details && one(details, 'p.break-words.whitespace-pre-line');
    if (comment && comment.children.length === 0) {
      const style = document.defaultView.getComputedStyle(comment);
      const clipped = comment.scrollHeight > comment.clientHeight || comment.scrollWidth > comment.clientWidth ||
        ['hidden', 'clip'].includes(style.overflow) || ['hidden', 'clip'].includes(style.overflowY) ||
        style.textOverflow === 'ellipsis' || (style.webkitLineClamp && !['none', '0'].includes(style.webkitLineClamp));
      const content = comment.innerText;
      if (!clipped && typeof content === 'string' && content.length <= 4096) {
        base.seller_comments = content.trim();
        base.seller_comments_supported = true;
      }
    }
    const rating = [...main.querySelectorAll('p.hifi-body-kind-heavy')]
      .filter(visible).map(element => text(element, 12)).filter(value => /^\d{1,3}(?:\.\d+)?%$/u.test(value ?? ''));
    if (rating.length === 1 && Number(rating[0].slice(0, -1)) <= 100) {
      base.seller_percent = Number(rating[0].slice(0, -1));
    }
    const feedback = [...main.querySelectorAll("a[href*='/sell/seller_feedback/']")]
      .filter(visible).map(element => text(element, 60))
      .map(value => /^View all ([0-9]{1,3}(?:,[0-9]{3})*) reviews$/u.exec(value ?? ''))
      .filter(Boolean);
    if (feedback.length === 1) base.feedback = Number(feedback[0][1].replaceAll(',', ''));
    const prices = [...main.querySelectorAll('h2.hifi-heading-large')]
      .filter(visible).map(element => text(element, 32));
    if (prices.length === 1 && /^\$[0-9]{1,7}(?:\.[0-9]{2})$/u.test(prices[0] ?? '')) {
      base.item = prices[0].slice(1);
    }
    // A dollar sign and displayed shipping do not establish currency or a payable quote.
    const shipping = [...main.querySelectorAll('p')].filter(visible)
      .filter(element => text(element, 16) === 'shipping')
      .map(element => text(element.previousElementSibling, 32))
      .filter(value => /^\$[0-9]{1,7}(?:\.[0-9]{2})$/u.test(value ?? ''));
    if (shipping.length && shipping.every(value => value === shipping[0])) {
      base.reported_shipping_text = `${shipping[0]} shipping`;
    }
    const controls = [...main.querySelectorAll('button,a')].filter(visible);
    const addable = controls.filter(element => text(element, 30) === 'Add to Cart');
    const inCart = controls.filter(element => text(element, 30) === 'In Cart' &&
      element.matches('button:disabled'));
    if (addable.length === 1 && inCart.length === 0) base.cart_state = 'addable';
    if (inCart.length === 1 && addable.length === 0) base.cart_state = 'already_in_cart';
    base.available = base.cart_state !== 'unknown';
    if (location.href !== original || header.getAttribute('href') !== href ||
        details && !details.isConnected) return stop('listing_changed_during_read');
    if (!base.media || !base.sleeve || base.seller_percent === null ||
        base.feedback === null || !base.item || !base.available) {
      return stop('listing_fields_unavailable');
    }
    return { ...base, status: 'observed', complete: true };
  } catch { return stop('listing_dom_read_failed'); }
}
