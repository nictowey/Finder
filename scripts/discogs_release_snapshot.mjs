/** One read-only observation of an already-authorized rendered release marketplace page.
 * No navigation, network, session, scheduler, cart, notification or purchase capability.
 */
export function collectReleaseMarketplaceDom(expectedReleaseId, expectedPage = 1) {
  if (expectedReleaseId && typeof expectedReleaseId === 'object' && !Array.isArray(expectedReleaseId)) {
    expectedPage = expectedReleaseId.page;
    expectedReleaseId = expectedReleaseId.id;
  }
  const timestamp = new Date().toISOString();
  const base = { source: 'discogs_rendered_release', release_id: null, url: null,
    observed_at: timestamp, status: 'incomplete', complete: false,
    range_start: null, range_end: null, total: null, page: expectedPage, has_next: null, rows: [],
    assertions: { release_identity: false, listing_container: false, pagination: false, stable: false } };
  const stop = code => ({ ...base, error_code: code, purchase_allowed: false });
  const id = value => typeof value === 'string' && /^[1-9][0-9]{0,15}$/u.test(value) &&
    !/\s/u.test(value) && Number.isSafeInteger(Number(value));
  if (!id(expectedReleaseId)) return stop('invalid_release_id');
  if (!Number.isSafeInteger(expectedPage) || expectedPage < 1 || expectedPage > 100) return stop('invalid_page');
  base.release_id = Number(expectedReleaseId);
  if (typeof document === 'undefined' || typeof location === 'undefined') return stop('dom_unavailable');
  const expectedUrl = `https://www.discogs.com/sell/release/${expectedReleaseId}`;
  const initialUrl = location.href;
  const pageUrl = expectedPage === 1 ? expectedUrl : `${expectedUrl}?page=${expectedPage}`;
  if (initialUrl !== pageUrl && !(expectedPage === 1 && initialUrl === expectedUrl + '/')) return stop('release_url_mismatch');
  base.url = initialUrl;
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
    const text = (node, limit = 160) => {
      if (!node || !visible(node)) return null;
      const value = node.innerText?.trim().replace(/\s+/gu, ' ');
      return typeof value === 'string' && value.length > 0 && value.length <= limit ? value : null;
    };
    const one = (root, selector) => {
      const nodes = [...root.querySelectorAll(selector)].filter(visible);
      return nodes.length === 1 ? nodes[0] : null;
    };
    const pjax = one(document, '#pjax_container');
    const table = pjax && one(pjax, 'table.mpitems');
    if (!table || table.querySelectorAll('thead tr').length !== 1) return stop('listing_container_unavailable');
    if ([...pjax.querySelectorAll('[role="alert"],[aria-busy="true"]')].some(visible)) return stop('visible_error_or_loading');
    if (document.readyState === 'loading' || pjax.getAttribute('aria-busy') === 'true' || table.getAttribute('aria-busy') === 'true') return stop('page_loading');
    const countNodes = [...document.querySelectorAll('nav.pagination .pagination_total')];
    if (countNodes.length !== 2 || !countNodes.some(visible)) return stop('pagination_counts_unavailable');
    // Only these verified responsive pagination labels may be read while hidden.
    const countText = node => {
      const value = node.textContent?.trim().replace(/\s+/gu, ' ');
      return value && value.length <= 80 ? value : null;
    };
    const counts = countNodes.map(countText);
    if (!counts[0] || counts[0] !== counts[1]) return stop('pagination_counts_disagree');
    const count = /^(\d+)\s*[–-]\s*(\d+)\s+of\s+(\d+)$/u.exec(counts[0]);
    if (!count) return stop('unsupported_pagination_text');
    const [start, end, total] = count.slice(1).map(Number);
    const completeRange = expectedPage === 1 && ((total === 0 && start === 0 && end === 0) ||
      (total > 0 && start === 1 && end === total));
    const pageRows = total === 0 ? 0 : end - start + 1;
    const partialRange = total > 0 && start > 0 && end >= start && end <= total && !completeRange;
    if (![start, end, total].every(Number.isSafeInteger) || total > 10_000_000 ||
        pageRows > 250 || (!completeRange && !partialRange)) return stop('partial_page_unverified');
    const navigations = [...document.querySelectorAll('nav.pagination')];
    if (navigations.length !== 2) return stop('pagination_containers_unavailable');
    const pageSizes = [], nextLinks = [];
    for (const nav of navigations) {
      const forms = nav.querySelectorAll('form');
      const form = forms.length === 1 ? forms[0] : null;
      if (!form || ![`/sell/release/${expectedReleaseId}`, expectedUrl].includes(form.getAttribute('action'))) return stop('pagination_release_mismatch');
      const limits = form.querySelectorAll("select[name='limit']");
      const select = limits.length === 1 ? limits[0] : null;
      const selected = select?.selectedOptions;
      if (!selected || selected.length !== 1 || !/^[1-9][0-9]{0,2}$/u.test(selected[0].value)) return stop('page_size_unavailable');
      const size = Number(selected[0].value);
      if (size > 250 || size < pageRows) return stop('page_size_unavailable');
      pageSizes.push(size);
      // A complete page has no additional navigation. Partial-page controls are
      // never operated; only the displayed range establishes missing coverage.
      if (completeRange && nav.querySelectorAll('a,button').length) return stop('additional_or_unknown_page_control');
      const next = [...nav.querySelectorAll('a[rel="next"]')];
      if (next.length > 1) return stop('pagination_next_ambiguous');
      nextLinks.push(next.length ? next[0].getAttribute('href') : null);
    }
    if (pageSizes[0] !== pageSizes[1]) return stop('page_sizes_disagree');
    if (nextLinks[0] !== nextLinks[1]) return stop('pagination_next_disagree');
    const nextHref = `/sell/release/${expectedReleaseId}?page=${expectedPage + 1}`;
    if (nextLinks[0] && (end >= total || nextLinks[0] !== nextHref)) return stop('pagination_next_unverified');
    if (end < total && expectedPage >= 100) return stop('pagination_limit');
    const rawRows = [...table.querySelectorAll('tbody tr')].filter(visible);
    if (rawRows.length !== pageRows) return stop('row_count_mismatch');
    const seen = new Set(), rows = [];
    for (const row of rawRows) {
      const title = one(row, "td.item_description a.item_description_title[href^='/sell/item/']");
      const itemHref = title?.getAttribute('href');
      const match = typeof itemHref === 'string' && /^\/sell\/item\/([1-9][0-9]{0,15})$/u.exec(itemHref);
      if (!match || !id(match[1]) || seen.has(match[1])) return stop('invalid_or_duplicate_listing');
      seen.add(match[1]);
      // This verified responsive identity link can be hidden; only its bounded href is read.
      const releaseLinks = [...row.querySelectorAll("td.item_description a.item_release_link[href*='/release/']")];
      if (releaseLinks.length !== 1 || releaseLinks.some(link => {
        const href = link.getAttribute('href');
        const release = typeof href === 'string' && /^(?:https:\/\/www\.discogs\.com)?\/release\/([1-9][0-9]{0,15})(?:-[A-Za-z0-9-]+)?$/u.exec(href);
        return !release || release[1] !== expectedReleaseId;
      })) return stop('row_release_identity_mismatch');
      const priceNode = one(row, 'td.item_price span.price[data-currency]');
      const priceText = text(priceNode), currency = priceNode?.getAttribute('data-currency');
      const condition = one(row, 'p.item_condition');
      const sleeveNode = condition && one(condition, 'span.item_sleeve_condition');
      const grade = node => {
        if (!node || !visible(node)) return null;
        // Media/sleeve grade is a direct text node; nested help copy is not the grade.
        const direct = [...node.childNodes].filter(child => child.nodeType === 3)
          .map(child => child.textContent ?? '').join(' ').trim().replace(/\s+/gu, ' ');
        if (direct.length > 160) return null;
        const value = direct.replace(/^(?:Media|Sleeve):\s*/u, '');
        return ['Mint (M)', 'Near Mint (NM or M-)', 'Very Good Plus (VG+)', 'Very Good (VG)',
          'Good Plus (G+)', 'Good (G)', 'Fair (F)', 'Poor (P)', 'Generic', 'No Cover', 'Not Graded']
          .includes(value) ? value : null;
      };
      const mediaNodes = condition ? [...condition.children].filter(node =>
        node.tagName === 'SPAN' && !node.getAttribute('class') && visible(node) &&
        node.querySelectorAll('.has-tooltip').length === 1) : [];
      const media = mediaNodes.length === 1 ? grade(mediaNodes[0]) : null, sleeve = grade(sleeveNode);
      const sellerCell = one(row, 'td.seller_info');
      // Exact profile selector is verified before this collector is used live.
      const sellerNode = sellerCell && one(sellerCell, "a[href^='/seller/'][href$='/profile']");
      const sellerMatch = /^\/seller\/([A-Za-z0-9_-]{1,64})\/profile$/u.exec(
        sellerNode?.getAttribute('href') ?? '');
      const feedbackNode = sellerCell && one(sellerCell, 'a.section_link');
      const ratingContainer = feedbackNode?.closest('li');
      const ratings = ratingContainer && sellerCell.contains(ratingContainer) ?
        [...ratingContainer.querySelectorAll('strong')].filter(node => visible(node) && /^\d{1,3}(?:\.\d+)?%$/u.test(text(node) ?? '')) : [];
      // Only the verified seller-comment paragraph, never grade help or full row text.
      const commentNodes = row.querySelectorAll('td.item_description > p.hide_mobile:not(.label_and_cat)');
      const comment = commentNodes.length === 1 && visible(commentNodes[0]) ? commentNodes[0] : null;
      let description = null;
      if (comment && !comment.querySelector('a,button,[aria-expanded]')) {
        const style = document.defaultView.getComputedStyle(comment);
        const clipped = comment.scrollHeight > comment.clientHeight || comment.scrollWidth > comment.clientWidth ||
          ['hidden', 'clip'].includes(style.overflow) || ['hidden', 'clip'].includes(style.overflowY) ||
          style.textOverflow === 'ellipsis' || (style.webkitLineClamp && style.webkitLineClamp !== 'none' && style.webkitLineClamp !== '0');
        if (!clipped) description = text(comment, 2000);
      }
      const fields = {
        price: priceText && /^[A-Z]{3}$/u.test(currency ?? '') ? `${priceText} ${currency}` : null,
        media: media || null, sleeve, seller: text(sellerNode, 120),
        seller_handle: sellerMatch?.[1] ?? null,
        seller_rating: ratings.length === 1 ? text(ratings[0]) : null,
        seller_feedback: text(feedbackNode),
        shipping: text(one(row, 'span.item_shipping'), 240),
        seller_description: description,
      };
      if (['price', 'media', 'sleeve', 'seller', 'seller_rating', 'seller_feedback'].some(key => !fields[key] || /^(?:loading(?:\.\.\.)?|error|unavailable)$/iu.test(fields[key]))) return stop('row_fields_not_loaded');
      if (fields.price.length > 160 || fields.media.length > 160) return stop('row_fields_oversized');
      rows.push({ listing_id: match[1], url: `https://www.discogs.com/sell/item/${match[1]}`, fields });
    }
    const finalRows = [...table.querySelectorAll('tbody tr')].filter(visible);
    if (location.href !== initialUrl || countNodes.some((node, index) => !node.isConnected || countText(node) !== counts[index]) ||
        finalRows.length !== pageRows || finalRows.some((row, index) => {
          const title = one(row, "td.item_description a.item_description_title[href^='/sell/item/']");
          return row !== rawRows[index] || title?.getAttribute('href') !== `/sell/item/${rows[index].listing_id}`;
        })) return stop('observation_changed');
    return { ...base, status: completeRange ? 'observed' : 'incomplete',
      complete: completeRange, range_start: start, range_end: end, total,
      has_next: end < total, rows,
      ...(nextLinks[0] ? { next_url: expectedUrl + `?page=${expectedPage + 1}` } : {}),
      assertions: { release_identity: true, listing_container: true, pagination: true, stable: true },
      ...(partialRange ? { error_code: end < total ? 'additional_page_required' : 'page_fragment' } : {}),
      purchase_allowed: false };
  } catch { return stop('dom_read_failed'); }
}
