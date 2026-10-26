/** Fixed server port for the exact KC no-submit test. Uses an existing browser
 * context only; no launch, profile, network client, card entry, Pay or order API.
 * Enhanced summary alone is not exact item authority: fresh fixed cart/detail
 * continuity and complete current checkout checks remain mandatory.
 */
import { createHash, randomUUID } from 'node:crypto';
import { Readable, Writable } from 'node:stream';
import { collectListingDetailDom, collectListingReadinessDom } from './discogs_listing_detail_snapshot.mjs';
import { collectCartDom } from './discogs_cart_inspector.mjs';
import { inspectCurrentEnhancedCheckout } from './discogs_enhanced_checkout.mjs';
import * as checkoutOperator from './discogs_guest_checkout_operator.mjs';
import { guestCardScopeDigest, validateGuestCardInspection, revalidateGuestCardClick,
  GUEST_CARD_URL, GUEST_CARD_SELECTOR } from './discogs_guest_card_prepare.mjs';

export const HOSTED_GUEST_LEDGER = '/bridge/guest-card-actions';
export const HOSTED_GO_SELECTOR = 'button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]';
const CART = 'https://www.discogs.com/sell/cart/';
const active = new WeakSet(), uncertain = new WeakSet();
const pendingByContext = new WeakMap();
const readonlyProbeJobs = new Map();
let ledgerActive = false;
const stable = value => JSON.stringify(value);
const STOP = Symbol('fixed_guest_stop');
const fail = code => { throw Object.assign(new Error(code), { [STOP]: code }); };
const stopped = (code, extras = {}) => ({ status: 'blocked', code, form_visible: false,
  card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false,
  order_status: 'unverified', payment_status: 'unverified', ...extras });

/** Runtime waits after a returned cancellation/timeout before releasing its
 * common browser lease. Both raw DOM reads and dispatched actions are retained;
 * settlement never resumes a stopped flow or clears reconciliation requirements.
 */
function retainContextTask(context, task) {
  let state = pendingByContext.get(context);
  if (!state) {
    let resolve;
    const settled = new Promise(done => { resolve = done; });
    state = { tasks: new Set(), settled, resolve }; pendingByContext.set(context, state);
  }
  state.tasks.add(task);
  const release = () => {
    state.tasks.delete(task);
    if (state.tasks.size === 0) {
      if (pendingByContext.get(context) === state) pendingByContext.delete(context);
      state.resolve();
    }
  };
  task.then(release, release);
  return task;
}
export function waitForHostedGuestPreparationSettlement(context) {
  return pendingByContext.get(context)?.settled || Promise.resolve();
}

/** The existing operator owns all anchor, lock, chain and pending checks. */
async function ledgerHead(directory, request) {
  if (typeof checkoutOperator.inspectGuestCheckoutLedger !== 'function') fail('ledger_inspection_unavailable');
  const state = await checkoutOperator.inspectGuestCheckoutLedger({ directory });
  if (state?.no_submit_init_allowed !== true || !/^[0-9a-f]{64}$/u.test(state.current_head || '')) fail('prior_attempt_unresolved');
  // A recorded disposition is historical evidence. Its recording-time expiry
  // is not refreshed here; a new exact scope, authorization and later complete
  // order-history review are still required by the authoritative claim writer.
  if (state.init_allowed !== true && request.priorFormDisposition_sha256 !== state.current_head) fail('form_disposition_required');
  if (state.init_allowed === true && request.priorFormDisposition_sha256) fail('form_disposition_unverified');
  return state.current_head;
}

/** Grounded only in the captured exact-KC cart: one A / sole #text caption,
 * 7-inch then Single format suffix. The expected core comes from a fresh exact
 * release header inside this process; no request can supply it. Literal matching
 * preserves case, punctuation and core parentheses; no generic stripping.
 */
export function parseCapturedCartCaption(caption, expectedCore) {
  const normalize = value => value.normalize('NFC').replace(/\s+/gu, ' ').trim();
  if (typeof caption !== 'string' || typeof expectedCore !== 'string') return null;
  caption = normalize(caption); const core = normalize(expectedCore);
  if (!caption || caption.length > 300 || !core || core.length > 160) return null;
  const index = caption.indexOf(core);
  if (index < 3 || caption.indexOf(core, index + 1) !== -1 || caption.slice(index - 3, index) !== ' - ') return null;
  const artist = caption.slice(0, index - 3), suffix = caption.slice(index + core.length);
  if (!artist || artist !== artist.trim() || !suffix.startsWith(' (') || !suffix.endsWith(')')) return null;
  const formats = suffix.slice(2, -1).split(', ');
  // This is deliberately the observed cart grammar, not a general format parser.
  if (formats.length !== 2 || formats[0] !== '7"' || formats[1] !== 'Single') return null;
  return Object.freeze({ core, artist, formats: Object.freeze(formats),
    full_caption_digest: createHash('sha256').update(caption).digest('hex') });
}

export function checkoutCaptionMatchesCart(cart, artist, formats) {
  const known = new Set(['7"', '45 RPM', 'Single', 'Stereo']);
  if (!cart || !Array.isArray(cart.formats) || typeof artist !== 'string' || artist.normalize('NFC').replace(/\s+/gu, ' ').trim() !== cart.artist ||
      !Array.isArray(formats) || !formats.length || formats.some(value => !known.has(value)) ||
      new Set(formats).size !== formats.length) return false;
  let index = 0;
  for (const value of formats) if (value === cart.formats[index]) index++;
  return index === cart.formats.length;
}

function selectedCartShippingLabel(snapshot) {
  const methods = snapshot?.shipping_methods;
  // Mirror the existing inspector's bounded shape and exactly-one-selection
  // checks; the two labels below are observations, never a shipping edit.
  if (!methods || methods.selector_count !== 1 || methods.visible !== true || methods.options_over_limit !== false ||
      !Number.isInteger(methods.option_count) || methods.option_count < 1 || methods.option_count > 20 ||
      !Array.isArray(methods.options) || methods.options.length !== methods.option_count ||
      !Number.isInteger(methods.selected_index) || methods.selected_index < 0 || methods.selected_index >= methods.option_count ||
      !methods.options.every(option => option && typeof option.selected === 'boolean' && typeof option.disabled === 'boolean' &&
        (option.label === null || typeof option.label === 'string' && option.label.length > 0 && option.label.length <= 100 &&
          option.label.trim() === option.label && !/[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]/u.test(option.label))) ||
      methods.options.filter(option => option.selected).length !== 1) return null;
  const selected = methods.options[methods.selected_index];
  return selected.selected && !selected.disabled && ['Economy - Media Mail', 'Economy - Media Mail - $4.95'].includes(selected.label) ? selected.label : null;
}

/** Browser-side supplemental read. Only standard semantic links/text can supply
 * identity or explicit quantity. These are deliberately absent in the captured
 * enhanced row; no React keys, hidden app state, description guessing or caller
 * booleans qualify it. Address prefixes come from merchant bundle SHA256
 * 1933d4028b07f0194e350adc6d4608ac1a9364344ab5b52c0a3d26532f7fb27a.
 * Only a match against the ephemeral server-local approved address digest leaves
 * the browser. No raw address is returned or logged.
 */
export async function collectHostedGuestDom({ stage, approvedDestinationDigest, privateDestinationSnapshot = false }) {
  const cart = stage === 'cart';
  const rendered = element => {
    if (!element?.isConnected) return false;
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || style.display === 'none' ||
          ['hidden', 'collapse'].includes(style.visibility) || style.contentVisibility === 'hidden' || style.opacity === '0') return false;
    }
    return true;
  };
  const visible = element => rendered(element) &&
    [...element.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
  const text = element => {
    if (!visible(element)) return '';
    // A rendered BR supplies a line boundary even with a zero-width rectangle.
    // Its own styles and every ancestor still have to be rendered.
    const walk = node => node.nodeType === 3 ? node.nodeValue : node.nodeType !== 1 ? '' :
      node.tagName === 'BR' ? rendered(node) ? ' ' : '' :
      visible(node) ? [...node.childNodes].map(walk).join('') : '';
    return walk(element).replace(/\s+/gu, ' ').trim();
  };
  const named = (root, tag, prefix) => [...root.querySelectorAll(tag)].filter(node =>
    [...node.classList].some(token => token.startsWith(prefix)));
  const titleText = value => value.normalize('NFC').replace(/\s+/gu, ' ').trim();
  const checkoutCoreTitle = heading => {
    // Retained merchant fRu source renders direct artist SPAN(s), its own
    // literal separator SPAN, the release.title SPAN, and a final format SPAN.
    // This narrow KC shape requires nonempty artists and format. The source
    // permits absent artists/empty format generally; those remain unsupported.
    const spans = [...heading.childNodes];
    if (spans.length < 4 || spans.some(node => node.nodeType !== 1 || node.tagName !== 'SPAN' ||
        !visible(node) || node.childNodes.length === 0 || [...node.childNodes].some(child => child.nodeType !== 3))) return null;
    const separators = spans.filter(node => node.textContent === ' - ');
    const separatorIndex = spans.length - 3;
    if (separators.length !== 1 || spans[separatorIndex] !== separators[0] || separatorIndex < 1) return null;
    const artists = spans.slice(0, separatorIndex).map(node => titleText(text(node)));
    if (artists.some(value => !value) || new Set(artists).size !== artists.length) return null;
    const core = titleText(text(spans[separatorIndex + 1]));
    const format = spans[separatorIndex + 2].textContent;
    if (!core || !format.startsWith(' (') || !format.endsWith(')') ||
        !titleText(format.slice(2, -1)) || format.slice(2, -1) !== format.slice(2, -1).trim()) return null;
    return { core, artist: titleText(spans.slice(0, separatorIndex).map(node => node.textContent).join('')),
      formats: format.slice(2, -1).split(', ') };
  };
  const digest = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256',
    new TextEncoder().encode(value)))].map(byte => byte.toString(16).padStart(2, '0')).join('');
  const out = { access: 'normal', ready: document.readyState === 'complete',
    listing: false, quantity: false, destination: false, title_digest: null, title_basis: null, title_core: null, title_artist: null, title_formats: null,
    cart_caption: null, cart_caption_digest: null,
    media: null, sleeve: null, item: null, item_row_count: 0, item_title_present: false,
    address_container_count: 0, address_line_count: null, address_present: false, agreements: false, existing_form: false, go: false };
  if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu.test(document.title) ||
      document.querySelector('input[type="password"],form[action*="/cdn-cgi/"],iframe[src*="challenges.cloudflare.com"],[id*="captcha" i],[class*="captcha" i]')) {
    out.access = 'access_restricted'; return out;
  }
  if (stage === 'detail') {
    const headings = [...document.querySelectorAll('main a.hifi-heading-link-large[href="https://www.discogs.com/release/3422100"]')].filter(visible);
    if (headings.length === 1 && text(headings[0])) {
      out.title_core = titleText(text(headings[0]));
      out.title_digest = await digest(out.title_core); out.title_basis = 'detail_exact_release_header_core';
    }
    return out;
  }
  out.agreements = [...document.querySelectorAll('input[type="checkbox"]')].some(visible);
  const fields = document.querySelectorAll('#card-fields-container');
  out.existing_form = [...fields].some(node => node.childElementCount > 0 && visible(node));
  const rows = cart ? document.querySelectorAll('tr.order_row') : named(document, 'li', '_checkout-sell-item__list-item_');
  let discoveryAttempted = false;
  const cartBinding = () => {
    const controls = document.querySelectorAll('button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]');
    const containers = document.querySelectorAll('[id^="cart-order-"].order_container');
    const selects = document.querySelectorAll('.order_shipping_fields select.shipping_method[name="shipping_method"]');
    // Diagnostic-only mirrors of every existing predicate below. These never
    // substitute for or relax the original short-circuit qualification gate.
    const candidateGo = controls.length === 1 ? controls[0] : null;
    const candidateContainer = containers.length === 1 ? containers[0] : null;
    const candidateRow = rows.length === 1 ? rows[0] : null;
    const candidateSelect = selects.length === 1 ? selects[0] : null;
    const candidateForm = candidateGo?.form || null;
    const candidateSummaries = candidateContainer?.querySelectorAll('.order_summary') || [];
    const count = nodes => Math.min(nodes.length, 2);
    const diagnostic = {
      schema: 'cart_binding_predicates_v1', go_count: count(controls), container_count: count(containers),
      row_count: count(rows), select_count: count(selects), scoped_summary_count: count(candidateSummaries),
      legacy_count: count(document.querySelectorAll('button.order_button.checkout_v1')),
      visible_address_wrapper_count: count([...document.querySelectorAll('[id^="shipping_address_"]')].filter(visible)),
      discovery_attempted: discoveryAttempted,
      go_type_submit: !!candidateGo && candidateGo.type === 'submit',
      // Informational only: a submit value is independent of its visible label.
      go_value_matches: !!candidateGo && candidateGo.value === 'Go to Checkout',
      go_text_matches: !!candidateGo && text(candidateGo) === 'Go to Checkout',
      go_visible: !!candidateGo && visible(candidateGo),
      go_not_disabled_property: !!candidateGo && !candidateGo.disabled,
      go_not_disabled_selector: !!candidateGo && !candidateGo.matches(':disabled'),
      go_not_aria_disabled: !!candidateGo && candidateGo.getAttribute('aria-disabled') !== 'true',
      form_present: !!candidateForm, form_current_document: !!candidateForm && candidateForm.ownerDocument === document,
      form_connected: !!candidateForm && candidateForm.isConnected,
      container_closest_form_matches: !!candidateForm && !!candidateContainer && candidateContainer.closest('form') === candidateForm,
      select_form_matches: !!candidateForm && !!candidateSelect && candidateSelect.form === candidateForm,
      form_contains_container: !!candidateForm && !!candidateContainer && candidateForm.contains(candidateContainer),
      form_contains_row: !!candidateForm && !!candidateRow && candidateForm.contains(candidateRow),
      form_contains_go: !!candidateForm && !!candidateGo && candidateForm.contains(candidateGo),
      form_contains_select: !!candidateForm && !!candidateSelect && candidateForm.contains(candidateSelect),
      form_contains_summary: !!candidateForm && candidateSummaries.length === 1 && candidateForm.contains(candidateSummaries[0]),
      container_contains_row: !!candidateContainer && !!candidateRow && candidateContainer.contains(candidateRow),
      container_contains_go: !!candidateContainer && !!candidateGo && candidateContainer.contains(candidateGo),
      container_contains_select: !!candidateContainer && !!candidateSelect && candidateContainer.contains(candidateSelect),
      container_visible: !!candidateContainer && visible(candidateContainer), row_visible: !!candidateRow && visible(candidateRow),
      select_visible: !!candidateSelect && visible(candidateSelect), summary_count_one: candidateSummaries.length === 1,
    };
    diagnostic.go_enabled = diagnostic.go_not_disabled_property && diagnostic.go_not_disabled_selector && diagnostic.go_not_aria_disabled;
    const predicates = [
      ['go_count_one', controls.length === 1], ['container_count_one', containers.length === 1],
      ['select_count_one', selects.length === 1], ['row_count_one', rows.length === 1],
      ...['form_present', 'form_connected', 'form_current_document', 'container_closest_form_matches', 'select_form_matches',
        'form_contains_container', 'form_contains_row', 'form_contains_go', 'form_contains_select',
        'container_contains_row', 'container_contains_go', 'container_contains_select', 'summary_count_one', 'form_contains_summary',
        'container_visible', 'row_visible', 'select_visible', 'go_visible', 'go_not_disabled_property', 'go_not_disabled_selector',
        'go_not_aria_disabled', 'go_type_submit', 'go_text_matches'].map(key => [key, diagnostic[key]]),
    ];
    diagnostic.first_failed = predicates.find(([, matches]) => !matches)?.[0] || 'none';
    out.cart_binding_diagnostic = diagnostic;
    if (controls.length !== 1 || containers.length !== 1 || selects.length !== 1 || rows.length !== 1) return null;
    const button = controls[0], container = containers[0], select = selects[0], form = button.form;
    const summaries = container.querySelectorAll('.order_summary');
    if (!form || !form.isConnected || form.ownerDocument !== document || container.closest('form') !== form ||
        select.form !== form || !form.contains(container) || !form.contains(rows[0]) || !form.contains(button) || !form.contains(select) ||
        !container.contains(rows[0]) || !container.contains(button) || !container.contains(select) ||
        summaries.length !== 1 || !form.contains(summaries[0]) || !visible(container) || !visible(rows[0]) || !visible(select) ||
        !visible(button) || button.disabled || button.matches(':disabled') || button.getAttribute('aria-disabled') === 'true' ||
        button.type !== 'submit' || text(button) !== 'Go to Checkout') return null;
    return { button, container, select, form };
  };
  const initialCartBinding = cart ? cartBinding() : null;
  if (cart) { discoveryAttempted = !!initialCartBinding; out.cart_binding_diagnostic.discovery_attempted = discoveryAttempted; }
  out.item_row_count = Math.min(rows.length, 2);
  if (rows.length === 1 && visible(rows[0])) {
    const row = rows[0];
    const links = [...row.querySelectorAll('a.item_link[href="/sell/item/1366950910"]')].filter(visible);
    out.listing = links.length === 1;
    const headings = cart ? links : named(row, 'h3', '_checkout-sell-item__title_');
    if (headings.length === 1 && visible(headings[0])) {
      if (cart) {
        // Actual capture 1e05b67: this A has exactly one plain text child. Keep
        // the entire caption, then parse it against the trusted detail core in
        // the server process. No structured/legacy caption fallback is allowed.
        if (headings[0].childNodes.length === 1 && headings[0].firstChild.nodeType === 3) {
          out.cart_caption = titleText(text(headings[0]));
          out.cart_caption_digest = await digest(out.cart_caption); out.item_title_present = true;
          out.title_digest = out.cart_caption_digest; out.title_basis = 'cart_captured_single_text_caption';
        }
      } else {
        const title = checkoutCoreTitle(headings[0]);
        if (title) {
          out.title_core = title.core; out.title_artist = title.artist; out.title_formats = title.formats;
          out.title_digest = await digest(title.core); out.item_title_present = true;
          out.title_basis = 'checkout_source_span_core';
        }
      }
    }
    const grades = { Poor: 'P', Fair: 'F', Good: 'G', 'Good Plus': 'G+', 'Very Good': 'VG',
      'Very Good Plus': 'VG+', 'Near Mint': 'NM', Mint: 'M', Generic: 'Generic' };
    const grade = value => (Object.hasOwn(grades, value) ? grades[value] : null) || Object.entries(grades).find(([label, token]) =>
      label !== 'Generic' && value === `${label} (${token})`)?.[1] ||
      (/^(?:P|F|G|G\+|VG|VG\+|NM|M)$/u.test(value) ? value : null);
    const conditionNodes = cart ? [] : named(row, 'div', '_checkout-sell-item__condition_');
    // Cart grade DOM remains uncaptured/unsupported. Never infer it from row text.
    const conditions = !cart && conditionNodes.length === 1 ? text(conditionNodes[0]) : '';
    const match = /(?:^| )Media(?: Condition)?\s*:\s*(.*?)\s+Sleeve(?: Condition)?\s*:\s*(.*?)(?= \$|$)/u.exec(conditions);
    if (match) { out.media = grade(match[1]); out.sleeve = grade(match[2]); }
    const prices = cart ? [] : named(row, 'div', '_checkout-sell-item__price_').filter(visible);
    if (prices.length === 1) out.item = /^\$(0\.10) USD$/u.exec(text(prices[0]))?.[1] || null;
  }
  // The observed cart sentence supplies the explicit quantity and seller count.
  // The enhanced Items (1) heading is continuity evidence only, never an ID.
  const declarations = [...document.querySelectorAll('h1,h2,p,span')].filter(visible).map(text);
  out.quantity = cart ? declarations.filter(value => /^You have [0-9]+ items? in your cart from [0-9]+ sellers?\.$/u.test(value)).length === 1 &&
    declarations.includes('You have 1 item in your cart from 1 seller.') :
    declarations.filter(value => /^Items \([0-9]+\)$/u.test(value)).length === 1 && declarations.includes('Items (1)');
  let addresses;
  if (cart) addresses = initialCartBinding ? [...document.querySelectorAll('[id^="shipping_address_"]')].filter(visible) : [];
  else {
    const containers = named(document, 'div', '_ships-to-address__container_');
    const content = containers.length === 1 ? named(containers[0], 'div', '_ships-to-address__content_') : [];
    addresses = content.length === 1 ? [...content[0].querySelectorAll('address')] : [];
  }
  out.address_container_count = Math.min(addresses.length, 2);
  if (addresses.length === 1 && visible(addresses[0]) && (!cart || initialCartBinding.container.contains(addresses[0]))) {
    const address = addresses[0], initialNodes = [...address.childNodes];
    const normalized = value => value.normalize('NFC').replace(/\s+/gu, ' ').trim();
    const readAddressLines = () => {
      if (cart) {
        // Actual 1ea2be8 capture: visible shipping-prefix DIV, header H2,
        // nested content DIVs; no ADDRESS element. Values below remain private.
        // Line semantics/bounds use synthetic normalization fixtures, not a
        // claim that the redacted capture disclosed any private line values.
        const lines = [], nodes = []; let line = '', length = 0;
        const flush = () => { const value = normalized(line); if (value) lines.push(value); line = ''; };
        const walk = (node, depth) => {
          if (depth > 8 || nodes.length >= 80) throw new Error('unsupported');
          nodes.push(node);
          if (node.nodeType === 3) {
            const raw = node.nodeValue || ''; length += raw.length;
            if (raw.length > 600 || length > 1_200) throw new Error('unsupported');
            line += raw; return;
          }
          if (node.nodeType !== 1) throw new Error('unsupported');
          if (!rendered(node)) return;
          if (node.tagName === 'H2' && text(node) === 'Your Shipping Address' &&
              [...node.childNodes].every(child => child.nodeType === 3)) return;
          if (node.tagName === 'A' && [...node.childNodes].every(child => child.nodeType === 3) &&
              (text(node) === 'Change your address' || node.matches('a.edit_shipping_address') && text(node) === 'Edit')) return;
          if (node.tagName === 'BR') { if (node.childNodes.length) throw new Error('unsupported'); flush(); return; }
          // ADDRESS remains a supported semantic container for existing offline
          // fixtures; the captured cart path requires only DIV/P/SPAN/BR.
          if (!['DIV', 'P', 'SPAN', 'ADDRESS'].includes(node.tagName) || !visible(node)) throw new Error('unsupported');
          const block = ['DIV', 'P', 'ADDRESS'].includes(node.tagName) || node.tagName === 'SPAN' && node.parentElement?.tagName === 'ADDRESS';
          if (block) flush();
          [...node.childNodes].forEach(child => walk(child, depth + 1));
          if (block) flush();
        };
        try {
          walk(address, 0); flush();
          return { lines, nodes };
        } catch { return null; }
      }
      const children = [...address.children];
      if (children.length >= 4 && children.length <= 6 && children.every(node => node.tagName === 'SPAN' &&
          node.children.length === 0 && visible(node) && [...node.classList].some(token => token.startsWith('_ships-to-address__line_'))) &&
          [...address.childNodes].every(node => node.nodeType === 1 || node.nodeType === 3 && !normalized(node.nodeValue || ''))) {
        return { lines: children.map(node => normalized(text(node))), nodes: [...address.childNodes] };
      }
      // The checkout keeps its separately source-derived direct-SPAN grammar.
      return null;
    };
    const initialAddress = readAddressLines(), lines = initialAddress?.lines;
    out.address_line_count = lines && lines.length <= 6 ? lines.length : null;
    out.address_present = !!lines && lines.length >= 4 && lines.length <= 6 &&
      lines.every(line => line.length > 0 && line.length <= 160) && lines.join('\n').length <= 600;
    if (out.address_present) {
      const addressText = lines.join('\n'), addressDigest = await digest(addressText);
      const currentAddress = readAddressLines(), currentLines = currentAddress?.lines;
      const currentBinding = cart ? cartBinding() : null;
      if (address.childNodes.length !== initialNodes.length || [...address.childNodes].some((node, index) => node !== initialNodes[index]) ||
          !currentLines || currentLines.join('\n') !== addressText || currentAddress.nodes.length !== initialAddress.nodes.length ||
          currentAddress.nodes.some((node, index) => node !== initialAddress.nodes[index]) ||
          cart && (!currentBinding || Object.keys(initialCartBinding).some(key => initialCartBinding[key] !== currentBinding[key]) ||
            [...document.querySelectorAll('[id^="shipping_address_"]')].filter(visible).length !== 1 || !currentBinding.container.contains(address))) {
        out.address_present = false; return out;
      }
      out.destination = addressDigest === approvedDestinationDigest;
      if (privateDestinationSnapshot) out.private_destination_snapshot = addressDigest;
    }
  }

  if (cart) out.go = !!cartBinding();
  return out;
}

/** Same-document final form association recheck on the bound visible Go handle. */
function revalidateHostedGoFormDom(element) {
  const rendered = node => {
    if (!node?.isConnected) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (current.hidden || current.getAttribute('aria-hidden') === 'true' || style.display === 'none' ||
          ['hidden', 'collapse'].includes(style.visibility) || style.contentVisibility === 'hidden' || style.opacity === '0') return false;
    }
    return true;
  };
  const visible = node => rendered(node) && [...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
  const text = node => node.nodeType === 3 ? node.nodeValue : node.nodeType !== 1 ? '' :
    node.tagName === 'BR' ? rendered(node) ? ' ' : '' : visible(node) ? [...node.childNodes].map(text).join('') : '';
  const controls = document.querySelectorAll('button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]');
  const containers = document.querySelectorAll('[id^="cart-order-"].order_container');
  const rows = document.querySelectorAll('tr.order_row');
  const selects = document.querySelectorAll('.order_shipping_fields select.shipping_method[name="shipping_method"]');
  if (!element.isConnected || element.ownerDocument !== document || controls.length !== 1 || controls[0] !== element ||
      containers.length !== 1 || rows.length !== 1 || selects.length !== 1 || !element.form) return false;
  const form = element.form, container = containers[0], select = selects[0];
  return form.isConnected && form.ownerDocument === document && container.closest('form') === form && select.form === form &&
    [container, rows[0], element, select].every(node => form.contains(node)) &&
    [rows[0], element, select].every(node => container.contains(node)) && container.querySelectorAll('.order_summary').length === 1 &&
    [container, rows[0], element, select].every(visible) &&
    !element.disabled && !element.matches(':disabled') && element.getAttribute('aria-disabled') !== 'true' &&
    element.type === 'submit' && text(element).replace(/\s+/gu, ' ').trim() === 'Go to Checkout';
}

/** Exact observed provider URL, with private query parameters discarded. */
export function isObservedCardFrame(url) {
  try { const parsed = new URL(url); return parsed.origin === 'https://www.paypal.com' &&
    parsed.pathname === '/smart/buttons' && !parsed.username && !parsed.password && !parsed.hash; }
  catch { return false; }
}

async function helperMessage(mode, directory, value) {
  let output = '';
  const sink = new Writable({ write(chunk, encoding, done) { output += chunk.toString(); done(); } });
  await checkoutOperator.runCurrentCheckoutClaim({ mode, directory, input: Readable.from([stable(value)]), output: sink });
  return JSON.parse(output);
}
function authorizationValid(request, authorization) {
  const now = Date.now() / 1000, s = request?.scope, t = request?.testAuthorization;
  const p = request?.priorFailure;
  const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join('|') === keys.sort().join('|');
  const priorKeys = ['source', 'last_error_code', 'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
    'card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked', 'failure_observed_at', 'order_history_url',
    'order_history_archived', 'order_history_status', 'order_history_count', 'order_history_complete', 'order_history_observed_at', 'reviewed_at', 'expires_at'];
  const reconciled = exactKeys(p, priorKeys) && p?.source === 'operator_reviewed_normal_discogs_ui' && p.last_error_code === 'SESSION_EXPIRED' &&
    ['release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total'].every(key => p[key] === s?.[key]) &&
    ['card_data_entered', 'pay_invoked', 'approval_invoked', 'capture_invoked'].every(key => p[key] === false) &&
    p.order_history_url === 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50' &&
    p.order_history_archived === 'All' && p.order_history_status === 'All' && p.order_history_count === 0 &&
    p.order_history_complete === true && ['failure_observed_at', 'order_history_observed_at', 'reviewed_at', 'expires_at'].every(key => Number.isFinite(p[key])) &&
    p.failure_observed_at <= p.order_history_observed_at && p.order_history_observed_at <= p.reviewed_at &&
    p.reviewed_at <= now && now - p.order_history_observed_at < 300 && now < p.expires_at && p.expires_at - p.reviewed_at <= 300;
  const digest = guestCardScopeDigest(s), uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
  return request && Object.keys(request).filter(key => key !== 'priorFormDisposition_sha256').sort().join('|') === ['job_id', 'ready_ref', 'scope', 'testAuthorization', 'priorFailure'].sort().join('|') &&
    (!Object.hasOwn(request, 'priorFormDisposition_sha256') || /^[0-9a-f]{64}$/u.test(request.priorFormDisposition_sha256)) &&
    authorization && digest && reconciled && uuid.test(request.job_id) && uuid.test(request.ready_ref) &&
    authorization.job_id === request.job_id && authorization.scope_digest === digest &&
    authorization.approved_destination_ref === s.destination_ref &&
    /^[0-9a-f]{64}$/u.test(authorization.approved_destination_digest) &&
    exactKeys(t, ['source', 'approval_ref', 'scope_digest', 'attempt_id', 'authorized_at', 'issued_at', 'expires_at',
      'card_entry_allowed', 'payment_allowed', 'final_submission_allowed']) && t?.source === 'explicit_user_authorized_no_submit_test' && t.approval_ref === s.approval_ref &&
    t.scope_digest === digest && t.attempt_id === s.intent_id &&
    t.card_entry_allowed === false && t.payment_allowed === false && t.final_submission_allowed === false &&
    ['authorized_at', 'issued_at', 'expires_at'].every(key => Number.isFinite(t[key])) && t.authorized_at >= 0 && t.authorized_at <= t.issued_at && t.issued_at <= now && now < t.expires_at &&
    t.expires_at - t.issued_at <= 300;
}

/** SAME-process browser port. options.authorization must come from the fixed
 * operation's local owner/scoped authorization issuer, never a request boolean.
 * ledgerDirectory is fixed in production; isolated fixtures use the test factory.
 */
export async function prepareHostedGuestForm(context, request, options = {}) {
  if (!context || typeof context !== 'object' || !authorizationValid(request, options.authorization)) return stopped('scoped_authorization_required');
  if (active.has(context) || ledgerActive) return stopped('operation_busy');
  if (uncertain.has(context)) return stopped('navigation_requires_reconciliation');
  const directory = options[fixtureDirectory] || HOSTED_GUEST_LEDGER;
  if (options.ledgerDirectory && options.ledgerDirectory !== HOSTED_GUEST_LEDGER) return stopped('fixed_ledger_required');
  const authorization = Object.freeze({ ...options.authorization });
  request = JSON.parse(stable(request)); // Snapshot authorization/scope across every await.
  const scope = Object.freeze(request.scope), signal = options.signal;
  const deadline = Math.min(Date.now() + 90000, scope.expires_at * 1000, request.testAuthorization.expires_at * 1000);
  let grant = null, navigationIssued = false, cardIssued = false;
  const refs = { cart: randomUUID(), checkout: randomUUID(), card: randomUUID() };
  active.add(context); ledgerActive = true;
  // Promise.race only stops our continuation. A dispatched Playwright action or
  // durable claim can still settle later, so retain both flight guards until
  // every operation actually settles. Its completion never resumes the flow.
  const pending = new Set();
  let finished = false, released = false;
  const releaseIfSettled = () => {
    if (finished && pending.size === 0 && !released) {
      released = true; active.delete(context); ledgerActive = false;
    }
  };
  const check = () => {
    if (signal?.aborted) fail('cancelled');
    if (Date.now() >= deadline) fail('deadline_expired');
  };
  const bounded = async operation => {
    check(); let timer, onAbort;
    const interrupt = new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('deadline_expired'), { [STOP]: 'deadline_expired' })), deadline - Date.now());
      if (signal) { onAbort = () => reject(Object.assign(new Error('cancelled'), { [STOP]: 'cancelled' })); signal.addEventListener('abort', onAbort, { once: true }); }
    });
    const task = retainContextTask(context, Promise.resolve().then(() => { check(); return operation(); }));
    pending.add(task);
    const settled = () => { pending.delete(task); releaseIfSettled(); };
    task.then(settled, settled); // Observe both outcomes; never queue another UI action.
    try { const value = await Promise.race([task, interrupt]); check(); return value; }
    finally { clearTimeout(timer); if (onAbort) signal.removeEventListener('abort', onAbort); }
  };
  const extras = { initialization: 'not_dispatched' };
  try {
    const head = await bounded(() => ledgerHead(directory, request));
    const pages = await bounded(() => context.pages());
    if (!Array.isArray(pages)) fail('existing_cart_required');
    const cartPages = pages.filter(page => page.url() === CART);
    const detailPages = pages.filter(page => ['https://www.discogs.com/sell/item/1366950910', 'https://www.discogs.com/shop/item/1366950910'].includes(page.url()));
    if (cartPages.length !== 1) fail('single_existing_cart_required');
    if (detailPages.length !== 1) fail('listing_detail_required');
    if (pages.length !== 2) fail('unrelated_browser_page_present');
    const page = cartPages[0], detailPage = detailPages[0];
    // The inspector has its own read timeout. Retain its raw promises too, so
    // an inner timeout cannot release the runtime lease over a pending DOM read.
    const inspectorPage = {
      url: () => retainContextTask(context, Promise.resolve().then(() => { check(); return page.url(); })),
      evaluate: (...args) => retainContextTask(context, Promise.resolve().then(() => { check(); return page.evaluate(...args); })),
    };
    const detailUrl = detailPage.url();
    const detailDocument = await bounded(() => detailPage.evaluateHandle(() => document));
    const detailRead = async () => {
      if (detailPage.url() !== detailUrl || !await bounded(() => detailPage.evaluate(expected => document === expected, detailDocument))) fail('listing_detail_changed');
      const detail = await bounded(() => detailPage.evaluate(collectListingDetailDom, { release_id: '3422100', listing_id: '1366950910' }));
      const title = await bounded(() => detailPage.evaluate(collectHostedGuestDom, { stage: 'detail' }));
      if (title.access !== 'normal') fail('access_restricted');
      if (!title.ready || detail.status !== 'observed' || detail.complete !== true || detail.release_id !== scope.release_id ||
          detail.listing_id !== scope.listing_id || detail.seller_handle !== scope.seller || detail.item !== '0.10' ||
          detail.cart_state !== 'already_in_cart' || !title.title_digest) fail('listing_detail_unverified');
      return { title_digest: title.title_digest, core_title: title.title_core, media: detail.media, sleeve: detail.sleeve };
    };
    const detail = await detailRead();
    let cartTitle = null, cartShippingLabel = null;
    if (await bounded(() => page.url()) !== CART) fail('existing_cart_required');
    const cartDocument = await bounded(() => page.evaluateHandle(() => document));
    const supplemental = cart => bounded(() => page.evaluate(collectHostedGuestDom,
      { stage: cart ? 'cart' : 'checkout', approvedDestinationDigest: authorization.approved_destination_digest }));
    const qualify = (dom, cart) => {
      if (dom.access !== 'normal') fail('access_restricted');
      if (!dom.ready) fail('page_not_ready');
      if (dom.existing_form) fail('existing_guest_form_preserved');
      if (dom.agreements) fail('extra_agreement_required');
      const missing = ['quantity', 'destination'].filter(key => dom[key] !== true);
      if (cart && !dom.listing) missing.push('listing');
      if (cart) {
        const parsed = parseCapturedCartCaption(dom.cart_caption, detail.core_title);
        if (!parsed || parsed.full_caption_digest !== dom.cart_caption_digest) missing.push('title_continuity');
        else if (cartTitle && parsed.full_caption_digest !== cartTitle.full_caption_digest) fail('cart_document_changed');
        else cartTitle = parsed;
        // Only Go-to-Checkout eligibility defers the unknown cart conditions.
        // Fresh exact detail grades are retained; fresh checkout must match both
        // before any claim/card dispatch. Unknown cart grades never count as proof.
      } else {
        if (dom.title_digest !== detail.title_digest || !checkoutCaptionMatchesCart(cartTitle, dom.title_artist, dom.title_formats)) missing.push('title_continuity');
        if (dom.media !== detail.media || dom.sleeve !== detail.sleeve) missing.push('condition_continuity');
      }
      if (!cart && dom.item !== '0.10') missing.push('item_price_continuity');
      if (missing.length) { extras.missing_fields = missing; fail('visible_scope_evidence_missing'); }
    };
    const cartRead = async () => {
      if (await bounded(() => page.url()) !== CART ||
          !await bounded(() => page.evaluate(expected => document === expected, cartDocument))) fail('cart_document_changed');
      const raw = await bounded(() => page.evaluate(collectCartDom, scope.listing_id));
      const dom = await supplemental(true); qualify(dom, true);
      const selectedLabel = selectedCartShippingLabel(raw);
      if (cartShippingLabel && selectedLabel !== cartShippingLabel) fail('cart_document_changed');
      if (stable(await detailRead()) !== stable(detail)) fail('listing_detail_changed');
      // V2 leaves the raw private-address warning unknown. Only this current
      // supplemental address/digest observation establishes the destination.
      if (raw.code !== 'observed' || raw.form_count !== 1 || raw.order_container_count !== 1 || raw.item_row_count !== 1 ||
          raw.listing_matches_expected !== true || raw.canonical_listing_href !== '/sell/item/1366950910' ||
          !dom.go || raw.quantity_control_count !== 0 || raw.checkout_control_kind !== 'v2_go_to_checkout' ||
          raw.missing_address !== null || dom.address_present !== true || dom.destination !== true ||
          raw.subtotal?.value !== '$0.10 USD' || raw.shipping?.value !== '$4.95 USD' || raw.total?.value !== '$5.05 USD' ||
          !selectedLabel) fail('cart_scope_unverified');
      cartShippingLabel = selectedLabel;
      return dom;
    };
    await cartRead();
    const cartObservedAt = Date.now() / 1000;
    const go = await bounded(() => page.$(HOSTED_GO_SELECTOR));
    if (!go) fail('checkout_control_unverified');
    await cartRead();
    if (await bounded(() => ledgerHead(directory, request)) !== head) fail('ledger_changed');
    if (!await bounded(() => go.evaluate(revalidateHostedGoFormDom))) fail('checkout_control_changed');
    await bounded(() => {
      check(); navigationIssued = true;
      return go.click({ timeout: Math.max(1, deadline - Date.now()), noWaitAfter: true });
    });
    const afterNavigation = await bounded(() => page.url());
    if (![CART, GUEST_CARD_URL].includes(afterNavigation)) fail('checkout_redirected');
    await bounded(() => page.waitForURL(GUEST_CARD_URL, { timeout: Math.max(1, deadline - Date.now()), waitUntil: 'domcontentloaded' }));
    if (await bounded(() => page.url()) !== GUEST_CARD_URL) fail('checkout_redirected');
    const checkoutDocument = await bounded(() => page.evaluateHandle(() => document));
    if (await bounded(() => page.evaluate(expected => document === expected, cartDocument))) fail('fresh_checkout_required');
    let frame, card, frameDocument, frameElement;
    const checkoutRead = async () => {
      const currentPages = await bounded(() => context.pages());
      if (currentPages.length !== 2 || !currentPages.includes(page) || !currentPages.includes(detailPage)) fail('browser_page_set_changed');
      if (await bounded(() => page.url()) !== GUEST_CARD_URL ||
          !await bounded(() => page.evaluate(expected => document === expected, checkoutDocument))) fail('checkout_document_changed');
      const summary = await bounded(() => inspectCurrentEnhancedCheckout(inspectorPage, { signal }));
      const dom = await supplemental(false); qualify(dom, false);
      if (stable(await detailRead()) !== stable(detail)) fail('listing_detail_changed');
      if (summary.status !== 'observed' || summary.seller_handle !== scope.seller ||
          summary.displayed.subtotal.amount !== '0.10' || summary.displayed.shipping.amount !== '4.95' ||
          summary.displayed.total.amount !== '5.05' || summary.selected_shipping_label !== 'Economy - Media Mail') fail('checkout_scope_unverified');
      return summary;
    };
    await checkoutRead();
    const frames = (await bounded(() => page.frames())).filter(candidate => isObservedCardFrame(candidate.url()));
    if (frames.length !== 1) fail('provider_frame_ambiguous');
    frame = frames[0];
    if (frame.parentFrame() !== page.mainFrame()) fail('provider_frame_unassociated');
    frameElement = await bounded(() => frame.frameElement());
    if (!await bounded(() => frameElement.isVisible()) ||
        await bounded(() => frameElement.contentFrame()) !== frame ||
        await bounded(() => frameElement.ownerFrame()) !== page.mainFrame()) fail('provider_frame_unassociated');
    frameDocument = await bounded(() => frame.evaluateHandle(() => document));
    card = await bounded(() => frame.$(GUEST_CARD_SELECTOR));
    const cardRead = async () => {
      const providerState = await bounded(() => frame.evaluate(collectHostedGuestDom, { stage: 'provider' }));
      if (providerState.access !== 'normal') fail('access_restricted');
      if (!providerState.ready) fail('provider_not_ready');
      if (providerState.existing_form) fail('existing_guest_form_preserved');
      if (providerState.agreements) fail('extra_agreement_required');
      if (!isObservedCardFrame(frame.url()) || frame.parentFrame() !== page.mainFrame() ||
          !await bounded(() => frame.evaluate(expected => document === expected, frameDocument)) ||
          await bounded(() => frameElement.contentFrame()) !== frame || !await bounded(() => frameElement.isVisible())) fail('provider_frame_changed');
      if (!card || await bounded(() => frame.locator(GUEST_CARD_SELECTOR).count()) !== 1 ||
          !await bounded(() => card.isVisible()) || !await bounded(() => card.isEnabled()) ||
          await bounded(() => card.getAttribute('aria-disabled')) !== 'false' ||
          !await bounded(() => card.evaluate(element => element.isConnected && element.ownerDocument === document &&
            document.querySelector('[role="link"][data-funding-source="card"][aria-label="Debit or Credit Card"]') === element))) fail('card_control_changed');
    };
    await cardRead();
    const observedAt = Date.now() / 1000;
    const evidence = { source: 'trusted_current_checkout', release_id: scope.release_id, listing_id: scope.listing_id,
      seller: scope.seller, quantity: 1, item_count: 1, seller_count: 1, rules_revision: scope.rules_revision,
      rules_current: true, criteria_satisfied: true, already_owned: false, existing_order: false,
      uncertain_prior_action: false, duplicate_guard_verified: true, approval_ref: scope.approval_ref,
      authorization_current: true, authorization_revoked: false, currency: 'USD', item: '0.10', shipping: '4.95',
      tax: null, fees: null, tax_verified: false, fees_verified: false, total: '5.05',
      amount_basis: 'explicit_merchant_checkout_total', preparation_quote_verified: true,
      final_payment_quote_verified: false, deferred_total_notice: false, shipping_selection_verified: true,
      quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref, destination_verified: true,
      terms_fingerprint: scope.terms_fingerprint, terms_verified: true, observed_at: observedAt,
      expires_at: Math.min(scope.expires_at, observedAt + 300) };
    const inspection = { schema: 'discogs_guest_card_inspection_v1', url: GUEST_CARD_URL, access: 'normal',
      control: { count: 1, visible: true, enabled: true, role: 'link', funding_source: 'card',
        aria_label: 'Debit or Credit Card', aria_disabled: 'false', control_ref: refs.card }, evidence };
    if (!validateGuestCardInspection(inspection, scope)) fail('inspection_unverified');
    grant = await bounded(() => helperMessage('claim', directory, {
      type: 'claim_current_checkout', ready_ref: request.ready_ref, scope, testAuthorization: request.testAuthorization,
      priorFailure: request.priorFailure, expected_latest_hash: head, inspection,
      ...(request.priorFormDisposition_sha256 ? { priorFormDisposition_sha256: request.priorFormDisposition_sha256 } : {}),
      provenance: { source: 'fixed_server_dom_current_checkout', navigation: 'normal_cart_to_fresh_checkout',
        scope_digest: guestCardScopeDigest(scope), cart_document_ref: refs.cart, checkout_document_ref: refs.checkout,
        cart_observed_at: cartObservedAt, checkout_observed_at: observedAt, cart_scope_verified: true,
        go_to_checkout_verified: true, history_restore: false, resubmission_prompt: false, extra_agreement_required: false } }));
    if (grant.type !== 'checkout_claim_granted') fail(grant.stop_reason || 'claim_unverified');
    await checkoutRead(); await cardRead();
    if (Date.now() / 1000 - observedAt >= 20) fail('inspection_stale');
    const binding = Object.freeze({ url: GUEST_CARD_URL, selector: GUEST_CARD_SELECTOR, control_ref: refs.card,
      scope_digest: guestCardScopeDigest(scope), intent_id: scope.intent_id,
      inspection_fingerprint: grant.permit.inspection_fingerprint, expected_scope: scope });
    if (!revalidateGuestCardClick(binding, { ...inspection, evidence: { ...evidence, observed_at: Date.now() / 1000 } }) ||
        Date.now() >= grant.permit.deadline) fail('execution_revalidation_failed');
    await bounded(() => {
      check(); cardIssued = true;
      return card.click({ timeout: Math.max(1, Math.min(deadline, grant.permit.deadline) - Date.now()), noWaitAfter: true });
    });
    // No captured guest-field anatomy exists to qualify a successful form. A
    // completed initialization click is therefore durably unknown, never ready
    // for payment. Preserve the existing page for owner inspection.
    const outcome = await helperMessage('outcome', directory, { type: 'record_claim_outcome', intent_id: scope.intent_id,
      ready_ref: request.ready_ref, scope_digest: guestCardScopeDigest(scope), claim_sha256: grant.claim_sha256,
      outcome: 'unknown', observed_at: Date.now() / 1000 });
    if (outcome.type !== 'claim_outcome_recorded') fail('outcome_recording_uncertain');
    return stopped('guest_form_observation_required', { initialization: 'outcome_requires_reconciliation',
      checkout_navigation_issued: true, card_initialization_issued: true });
  } catch (error) {
    if (navigationIssued) uncertain.add(context);
    if (grant?.type === 'checkout_claim_granted') {
      await helperMessage('outcome', directory, { type: 'record_claim_outcome', intent_id: scope.intent_id,
        ready_ref: request.ready_ref, scope_digest: guestCardScopeDigest(scope, { now: scope.issued_at }),
        claim_sha256: grant.claim_sha256, outcome: 'unknown', observed_at: Date.now() / 1000 }).catch(() => {});
    }
    return stopped(error?.[STOP] || 'unknown_internal_stop', { ...extras,
      initialization: cardIssued || grant?.type === 'checkout_claim_granted' ? 'outcome_requires_reconciliation' : 'not_dispatched',
      checkout_navigation_issued: navigationIssued, card_initialization_issued: cardIssued });
  } finally { finished = true; releaseIfSettled(); }
}

const fixtureDirectory = Symbol('isolated_fixture_ledger');
/** Private owner-only service wiring supplies a local authorization issuer.
 * Browser/request references cannot choose a URL, selector, directory or action.
 */
export function createHostedGuestPrepareOperation({ getContext, authorize, signal } = {}) {
  return async request => {
    try {
      if (typeof getContext !== 'function' || typeof authorize !== 'function') return stopped('scoped_authorization_required');
      const authorization = await authorize(request);
      if (!authorizationValid(request, authorization)) return stopped('scoped_authorization_required');
      return await prepareHostedGuestForm(await getContext(), request, { authorization, signal });
    } catch { return stopped('operation_unavailable'); }
  };
}
/** Offline fixtures only: no CLI/env override or production factory path. */
export function createHostedGuestPrepareFixture({ context, directory, authorization, signal } = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !directory?.startsWith('/')) throw new Error('test_context_required');
  return request => prepareHostedGuestForm(context, request, { authorization, signal, [fixtureDirectory]: directory });
}

/** Single fixed public-cart structure capture. No row text, HTML, address text,
 * account, arbitrary attributes, field values or alternative selectors leave it.
 * These nodes ground a later parser; this collector never qualifies the title.
 */
export function collectHostedCartStructureDom() {
  const stop = code => ({ status: 'blocked', code });
  try {
    if (location.href !== 'https://www.discogs.com/sell/cart/') return stop('cart_capture_unverified');
    if (document.readyState !== 'complete') return stop('page_not_ready');
    if (/just a moment|attention required|security check|captcha|cloudflare|sign in|log in/iu.test(document.title) ||
        document.querySelector('input[type="password"],form[action*="/cdn-cgi/"],iframe[src*="challenges.cloudflare.com"],[id*="captcha" i],[class*="captcha" i]')) return stop('access_restricted');
    const tags = new Set(['A', 'SPAN', 'STRONG', 'B', 'EM', 'I', 'SMALL', 'BR']);
    const normalize = value => value.normalize('NFC').replace(/\s+/gu, ' ').trim();
    const visible = element => {
      if (!element?.isConnected) return false;
      for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (node.hidden || node.getAttribute('aria-hidden') === 'true' || style.display === 'none' ||
            ['hidden', 'collapse'].includes(style.visibility) || style.contentVisibility === 'hidden' || style.opacity === '0') return false;
      }
      return element.tagName === 'BR' || [...element.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
    };
    const rows = document.querySelectorAll('tr.order_row');
    if (rows.length !== 1 || !visible(rows[0])) return stop('cart_capture_unverified');
    const row = rows[0], links = row.querySelectorAll('a.item_link');
    if (links.length !== 1 || !visible(links[0]) || links[0].getAttribute('href') !== '/sell/item/1366950910') return stop('cart_capture_unverified');
    const link = links[0], nodes = [], caption = [];
    let titleLength = 0;
    const titleNode = (node, path) => {
      if (path.length > 4 || path.some(index => index > 31) || nodes.length >= 24) throw new Error('unsupported');
      if (node.nodeType === 3) {
        const raw = node.nodeValue || '', text = normalize(raw); caption.push(raw);
        if (!text) return;
        titleLength += text.length;
        if (text.length > 160 || titleLength > 300) throw new Error('unsupported');
        nodes.push({ path, tag: '#text', text }); return;
      }
      if (node.nodeType !== 1 || !tags.has(node.tagName) || !visible(node)) throw new Error('unsupported');
      if (node.tagName === 'A' && !['/sell/item/1366950910', '/release/3422100'].includes(node.getAttribute('href'))) throw new Error('unsupported');
      nodes.push({ path, tag: node.tagName, text: '' });
      if (node.tagName === 'BR') caption.push(' ');
      [...node.childNodes].forEach((child, index) => titleNode(child, path.concat(index)));
    };
    [...link.childNodes].forEach((node, index) => titleNode(node, [index]));
    const title = normalize(caption.join(''));
    if (!title || title.length > 300) return stop('cart_capture_unverified');
    const grades = { Poor: 'P', Fair: 'F', Good: 'G', 'Good Plus': 'G+', 'Very Good': 'VG',
      'Very Good Plus': 'VG+', 'Near Mint': 'NM', Mint: 'M', Generic: 'Generic' };
    const gradeText = new Map(Object.entries(grades).flatMap(([label, grade]) =>
      grade === 'Generic' ? [[label, grade]] : [[label, grade], [grade, grade], [`${label} (${grade})`, grade]]));
    const labels = new Map(['Media', 'Sleeve'].flatMap(label =>
      [[label, label], [`${label}:`, label], [`${label} Condition`, label], [`${label} Condition:`, label]]));
    const token = raw => {
      const text = normalize(raw);
      if (labels.has(text)) return { text, label: labels.get(text), grade: null };
      if (gradeText.has(text)) return { text, label: null, grade: gradeText.get(text) };
      for (const [labelText, label] of labels) for (const [value, grade] of gradeText) {
        if (text === `${labelText} ${value}`) return { text, label, grade };
      }
      return null;
    };
    const rowPath = node => {
      const path = [];
      while (node !== row) {
        const parent = node.parentNode;
        if (!parent) throw new Error('unsupported');
        const index = [...parent.childNodes].indexOf(node); path.unshift(index); node = parent;
        if (path.length > 4 || index > 31) throw new Error('unsupported');
      }
      return path;
    };
    let gradeNodes = [];
    const cell = link.closest('td');
    // Discover only explicit plain-text label nodes in this product cell.
    // A candidate parent is accepted only if its entire small subtree contains
    // allowlisted labels/grades. Any price, comment or unknown content omits it.
    if (cell && cell.closest('tr.order_row') === row) {
      const candidates = [...cell.querySelectorAll('span,strong,b,em,i,small')].filter(node =>
        !link.contains(node) && !node.closest('address,[id^="shipping_address_"]') && visible(node) &&
        [...node.childNodes].every(child => child.nodeType === 3) && token(node.textContent)?.label);
      for (const parent of new Set(candidates.map(node => node.parentElement))) {
        try {
          const captured = [], foundLabels = new Set(); let length = 0;
          const captureGrade = node => {
            if (captured.length >= 12) throw new Error('unsupported');
            if (node.nodeType === 3) {
              if (!normalize(node.nodeValue || '')) return;
              const parsed = token(node.nodeValue || '');
              if (!parsed || parsed.text.length > 160 || (length += parsed.text.length) > 300) throw new Error('unsupported');
              if (parsed.label) foundLabels.add(parsed.label);
              captured.push({ path: rowPath(node), tag: '#text', ...parsed }); return;
            }
            if (node.nodeType !== 1 || !tags.has(node.tagName) || !visible(node) || node.tagName === 'A') throw new Error('unsupported');
            captured.push({ path: rowPath(node), tag: node.tagName, text: '', label: null, grade: null });
            [...node.childNodes].forEach(captureGrade);
          };
          [...parent.childNodes].forEach(captureGrade);
          if (foundLabels.has('Media') && foundLabels.has('Sleeve')) { gradeNodes = captured; break; }
        } catch { /* Unsupported grade structure remains unknown; title capture is still useful. */ }
      }
    }
    // Structural diagnostics only. Do not call collectCartDom here: that older
    // collector reads address-wrapper/tax text. These exact gate counts explain
    // its early return without inspecting private text or granting eligibility.
    let truncated = false;
    const cap = (items, limit) => { if (items.length > limit) truncated = true; return items.slice(0, limit); };
    const count = items => { if (items.length > 32) truncated = true; return Math.min(items.length, 32); };
    const layoutTags = new Set(['FORM', 'DIV', 'SECTION', 'MAIN', 'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TD', 'TH',
      'ADDRESS', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'P', 'SPAN', 'BR', 'UL', 'LI', 'A']);
    const tag = node => layoutTags.has(node?.tagName) ? node.tagName : 'OTHER';
    const classHints = ['legacy', 'order_container', 'order_row', 'order_summary', 'order_subtotal', 'order_total',
      'order_summary_value', 'order_shipping_amount', 'order_shipping_fields', 'shipping_method'];
    const hints = node => {
      const result = classHints.filter(name => node.classList?.contains(name));
      const id = node.getAttribute?.('id');
      if (id?.startsWith('shipping_address_')) result.push('shipping_address_prefix');
      if (id?.startsWith('cart-order-')) result.push('cart_order_prefix');
      return result;
    };
    const childTypes = node => cap([...node.childNodes], 16).map(child =>
      child.nodeType === 3 ? '#text' : child.nodeType === 8 ? '#comment' : child.nodeType === 1 ? tag(child) : 'OTHER');
    const shape = node => ({ tag: tag(node), hints: hints(node), child_types: childTypes(node) });
    const forms = [...document.querySelectorAll('form.legacy')];
    const containers = [...document.querySelectorAll('[id^="cart-order-"].order_container')];
    const container = containers.length === 1 ? containers[0] : null;
    const wrappers = [...document.querySelectorAll('[id^="shipping_address_"]')];
    const addresses = [...document.querySelectorAll('address')];
    const headingLabels = new Set(['Shipping Address', 'Shipping address', 'My Shipping Address', 'Your Shipping Address', 'Ship To', 'Ship to']);
    const headingFollowers = [];
    for (const heading of cap([...document.querySelectorAll('h1,h2,h3,h4,h5,h6,strong,b')], 32)) {
      // Only plain, bounded public heading labels; never aggregate descendants.
      if ([...addresses, ...wrappers, ...headingFollowers].some(region => region.contains(heading))) continue;
      const children = [...heading.childNodes];
      if (children.length !== 1 || children[0].nodeType !== 3 || !visible(heading)) continue;
      const raw = children[0].nodeValue || '';
      if (raw.length > 40 || !headingLabels.has(normalize(raw))) continue;
      let following = heading.nextElementSibling;
      const parent = heading.parentElement;
      // The observed public label can stand alone inside a plain wrapper.
      // Do not aggregate/read that wrapper's text or inspect unrelated siblings.
      if (!following && ['P', 'DIV'].includes(parent?.tagName) && parent.childNodes.length === 1 && parent.firstChild === heading) following = parent.nextElementSibling;
      if (following) headingFollowers.push(following);
    }
    const privateRegions = [...addresses, ...wrappers, ...headingFollowers];
    const privateNode = node => privateRegions.some(region => region === node || region.contains(node) || node.contains(region));
    const addressShapes = cap([
      ...addresses.map(node => ({ node, kind: 'address' })),
      ...wrappers.map(node => ({ node, kind: 'shipping_wrapper' })),
      ...headingFollowers.map(node => ({ node, kind: 'shipping_heading_following' })),
    ], 6).map(({ node, kind }) => ({ kind, ...shape(node), children: cap([...node.children], 8).map(shape) }));
    const ancestry = [];
    for (let node = row.parentElement; node && node !== document.body; node = node.parentElement) {
      if (ancestry.length === 8) { truncated = true; break; }
      ancestry.push({ tag: tag(node), hints: hints(node) });
    }
    const publicLabels = new Set(['Subtotal', 'Shipping', 'Total', 'Tax', 'Sales Tax', 'Plus applicable sales tax']);
    const summaryText = cell => {
      if (privateNode(cell) || !visible(cell)) return null;
      let value = '', visited = 0;
      const visit = node => {
        if (++visited > 12) throw new Error('unsupported');
        if (node.nodeType === 3) {
          const raw = node.nodeValue || '';
          if (raw.length > 100) throw new Error('unsupported');
          value += raw; if (value.length > 100) throw new Error('unsupported');
        } else if (node.nodeType !== 1 || !['SPAN', 'STRONG', 'B', 'EM', 'I', 'SMALL', 'BR'].includes(node.tagName) || !visible(node)) {
          throw new Error('unsupported');
        } else if (node.tagName === 'BR') value += ' ';
        else [...node.childNodes].forEach(visit);
      };
      try {
        [...cell.childNodes].forEach(visit); value = normalize(value);
        return publicLabels.has(value) || /^\$[0-9]{1,4}\.[0-9]{2} USD$/u.test(value) ? value : null;
      } catch { return null; }
    };
    // Stay in the exact product row's nearest form/container. Unknown tables
    // expose shape only; public text is limited to already-known summary nodes.
    const scope = row.closest('form') || container || row.closest('table');
    const allTables = scope ? [...scope.querySelectorAll('table')] : [];
    if (scope?.tagName === 'TABLE') allTables.unshift(scope);
    // HTML form/table reparenting can leave the already-known summary outside
    // the product's form. This fixed selector adds shape, never eligibility.
    const orderedTables = [...new Set([...document.querySelectorAll('table.order_summary'), ...allTables])];
    const tables = cap(orderedTables, 4).map(table => {
      const tableRows = [...table.querySelectorAll('tr')].filter(candidate => candidate.closest('table') === table);
      return { hints: hints(table), row_count: count(tableRows), rows: cap(tableRows, 8).map(tableRow => {
        const cells = [...tableRow.children].filter(cell => ['TD', 'TH'].includes(cell.tagName));
        const known = table.classList.contains('order_summary') || ['order_subtotal', 'order_total'].some(name => tableRow.classList.contains(name));
        return { hints: hints(tableRow), cell_count: count(cells), cells: cap(cells, 4).map(cell => ({
          ...shape(cell), public_text: known || cell.classList.contains('order_shipping_amount') || cell.classList.contains('order_summary_value') ? summaryText(cell) : null,
        })) };
      }) };
    });
    const goControls = [...document.querySelectorAll('button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]')];
    const goControl = goControls.length === 1 ? goControls[0] : null;
    const goForm = goControl?.form || null;
    const shippingSelects = [...document.querySelectorAll('.order_shipping_fields select.shipping_method[name="shipping_method"]')];
    const shippingSelect = shippingSelects.length === 1 ? shippingSelects[0] : null;
    const shippingForm = shippingSelect?.form || null;
    const options = shippingSelect ? [...shippingSelect.options] : [];
    const selectedIndex = shippingSelect?.selectedIndex;
    const cartLayout = { schema: 'discogs_cart_layout_v1', collector_gate: {
      code: 'not_run_private_text_guard', form_count: count(forms), order_container_count: count(containers),
      summary_count: count([...(container?.querySelectorAll('.order_summary') ?? [])]),
      address_wrapper_count: count([...(container?.querySelectorAll('[id^="shipping_address_"]') ?? [])].filter(visible)),
      address_count: count(addresses), form_contains_row: forms.length === 1 && forms[0].contains(row),
      container_contains_row: !!container?.contains(row), container_in_form: !!container && forms.length === 1 && container.closest('form.legacy') === forms[0],
      go_count: count(goControls), go_form_present: !!goForm, go_form_legacy: !!goForm?.classList.contains('legacy'),
      go_form_contains_row: !!goForm?.contains(row), go_in_container: !!container?.contains(goControl),
      shipping_select_count: count(shippingSelects), shipping_form_present: !!shippingForm,
      shipping_form_same_as_go: !!shippingForm && shippingForm === goForm, shipping_in_container: !!container?.contains(shippingSelect),
      selected_count: count(options.filter(option => option.selected)),
      selected_index: Number.isInteger(selectedIndex) && selectedIndex >= -1 && selectedIndex <= 31 ? selectedIndex : null,
      option_count: count(options),
    }, ancestry, tables, address_shapes: addressShapes, truncated };
    if (!row.isConnected || !link.isConnected || link.getAttribute('href') !== '/sell/item/1366950910' ||
        location.href !== 'https://www.discogs.com/sell/cart/') return stop('cart_capture_unverified');
    return { status: 'observed', code: 'cart_structure_captured', cart_structure: { row_count: 1,
      listing_href: '/sell/item/1366950910', title_link: { tag: 'A', text: title, nodes }, grade_nodes: gradeNodes }, cart_layout: cartLayout };
  } catch { return stop('cart_capture_unverified'); }
}

/** Distinct read-only authority. No prior failure, quote fingerprint, transaction
 * intent or payment-ready claim is required or accepted on this surface.
 */
function readonlyAuthorizationValid(request, authorization) {
  const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join('|') === expected.sort().join('|');
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value);
  const now = Date.now() / 1000, confirm = request?.phase === 'confirm_saved_destination', capture = request?.phase === 'capture_cart_structure';
  if (!keys(request, confirm ? ['schema', 'job_id', 'phase', 'document_ref'] : ['schema', 'job_id', 'phase']) ||
      request.schema !== 'discogs_guest_readonly_probe_v1' || !uuid(request.job_id) ||
      !['inspect', 'confirm_saved_destination', 'capture_cart_structure'].includes(request.phase) || confirm && !uuid(request.document_ref) ||
      !keys(authorization, ['schema', 'job_id', 'issued_at', 'expires_at', 'release_id', 'listing_id', 'seller',
        'quantity', 'currency', 'max_total', 'approved_destination_ref', ...(capture ? ['mode'] : []), ...(authorization?.destination_ack ? ['destination_ack'] : [])]) ||
      authorization.schema !== 'discogs_guest_readonly_authorization_v1' || authorization.job_id !== request.job_id ||
      authorization.release_id !== '3422100' || authorization.listing_id !== '1366950910' ||
      authorization.seller !== 'Mailman21842' || authorization.quantity !== 1 ||
      authorization.currency !== 'USD' || authorization.max_total !== '5.05' ||
      authorization.approved_destination_ref !== 'saved_shipping_address' ||
      !Number.isFinite(authorization.issued_at) || !Number.isFinite(authorization.expires_at) ||
      !(0 <= authorization.issued_at && authorization.issued_at <= now && now < authorization.expires_at &&
        authorization.expires_at - authorization.issued_at <= 1800)) return false;
  if (capture) return authorization.mode === 'capture_cart_structure' && !Object.hasOwn(authorization, 'destination_ack');
  if (!confirm) return true;
  const ack = authorization.destination_ack;
  return keys(ack, ['document_ref', 'source', 'approved_destination_ref', 'observed_at']) &&
    ack.document_ref === request.document_ref && ack.source === 'operator_visual_saved_destination_match' &&
    ack.approved_destination_ref === 'saved_shipping_address' && Number.isFinite(ack.observed_at) &&
    ack.observed_at <= now && now - ack.observed_at <= 60;
}

/** Read-only existing fixed Cart/Checkout probe. One inspect and one explicit
 * confirmation per job; runtime additionally persists its fixed call budget.
 * Initial address presence never approves a destination. A fresh operator visual
 * acknowledgement must name the opaque reference, then the same live document
 * and unchanged private snapshot are checked before an ephemeral match is set.
 * References expire within 300 seconds and are lost on process restart.
 */
export async function probeCurrentGuestPreparationEvidence(context, request, { authorization, signal } = {}) {
  if (!context || typeof context !== 'object' || !readonlyAuthorizationValid(request, authorization)) return stopped('readonly_authorization_required');
  request = JSON.parse(stable(request)); authorization = JSON.parse(stable(authorization));
  const now = Date.now() / 1000, confirming = request.phase === 'confirm_saved_destination', capturing = request.phase === 'capture_cart_structure';
  let job = readonlyProbeJobs.get(request.job_id);
  if (confirming) {
    if (!job || job.context !== context || job.document_ref !== request.document_ref) return stopped('probe_reference_unverified');
    if (now >= job.reference_expires_at) return stopped('probe_reference_expired');
    if (job.confirmation_consumed) return stopped('probe_job_replayed');
    if (authorization.destination_ack.observed_at < job.reference_created_at) return stopped('readonly_authorization_required');
    job.confirmation_consumed = true; // Consume before any await, including concurrent confirmation.
  } else {
    if (job) return stopped('probe_job_replayed');
    for (const [id, prior] of readonlyProbeJobs) if (now >= prior.authorization_expires_at) readonlyProbeJobs.delete(id);
    if (readonlyProbeJobs.size >= 64) return stopped('probe_capacity_exceeded');
    job = { context, authorization_expires_at: authorization.expires_at, confirmation_consumed: false };
    readonlyProbeJobs.set(request.job_id, job);
  }
  const deadline = Math.min(Date.now() + 1500, authorization.expires_at * 1000,
    ...(confirming ? [job.reference_expires_at * 1000, (authorization.destination_ack.observed_at + 60) * 1000] : []));
  const read = async operation => {
    if (signal?.aborted) fail('cancelled');
    if (Date.now() >= deadline) fail('probe_deadline_expired');
    let timer;
    try {
      const task = retainContextTask(context, Promise.resolve().then(() => {
        if (signal?.aborted) fail('cancelled');
        if (Date.now() >= deadline) fail('probe_deadline_expired');
        return operation();
      }));
      const value = await Promise.race([task, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error('probe_deadline_expired'), { [STOP]: 'probe_deadline_expired' })), deadline - Date.now());
      })]);
      if (signal?.aborted) fail('cancelled');
      if (Date.now() >= deadline) fail('probe_deadline_expired');
      return value;
    } finally { clearTimeout(timer); }
  };
  try {
    const pages = await read(() => context.pages());
    const merchantPages = pages.filter(page => capturing ? page.url() === CART : [CART, GUEST_CARD_URL].includes(page.url()));
    if (merchantPages.length !== 1) fail('single_current_merchant_page_required');
    const page = merchantPages[0], url = page.url(), cart = url === CART;
    if (confirming && (job.page !== page || job.url !== url ||
        !await read(() => page.evaluate(expected => document === expected, job.document_handle)))) fail('probe_document_changed');
    const documentHandle = confirming ? job.document_handle : await read(() => page.evaluateHandle(() => document));
    if (capturing) {
      const snapshot = await read(() => page.evaluate(collectHostedCartStructureDom));
      if (snapshot?.status !== 'observed' || snapshot.code !== 'cart_structure_captured') {
        fail(['access_restricted', 'page_not_ready'].includes(snapshot?.code) ? snapshot.code : 'cart_capture_unverified');
      }
      if (page.url() !== CART || !await read(() => page.evaluate(expected => document === expected, documentHandle))) fail('probe_document_changed');
      return { ...snapshot, initialization_allowed: false, card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false };
    }
    const inspectorPage = {
      url: () => retainContextTask(context, Promise.resolve().then(() => {
        if (signal?.aborted) fail('cancelled');
        if (Date.now() >= deadline) fail('probe_deadline_expired');
        return page.url();
      })),
      evaluate: (...args) => retainContextTask(context, Promise.resolve().then(() => {
        if (signal?.aborted) fail('cancelled');
        if (Date.now() >= deadline) fail('probe_deadline_expired');
        return page.evaluate(...args);
      })),
    };
    const summary = cart ? null : await read(() => inspectCurrentEnhancedCheckout(inspectorPage, { signal }));
    const cartDom = cart ? await read(() => page.evaluate(collectCartDom, '1366950910')) : null;
    const dom = await read(() => page.evaluate(collectHostedGuestDom, {
      stage: cart ? 'cart' : 'checkout', privateDestinationSnapshot: true }));
    if (dom.access !== 'normal' || summary?.code === 'access_restricted') fail('access_restricted');
    if (confirming && (!job.private_destination_snapshot || !dom.address_present)) fail('saved_destination_unverified');
    if (confirming && dom.private_destination_snapshot !== job.private_destination_snapshot) fail('saved_destination_snapshot_changed');
    const detailPages = pages.filter(candidate => ['https://www.discogs.com/sell/item/1366950910',
      'https://www.discogs.com/shop/item/1366950910'].includes(candidate.url()));
    let detail = null, title = null, readiness = null;
    if (detailPages.length === 1) {
      readiness = await read(() => detailPages[0].evaluate(collectListingReadinessDom));
      detail = await read(() => detailPages[0].evaluate(collectListingDetailDom, { release_id: '3422100', listing_id: '1366950910' }));
      title = await read(() => detailPages[0].evaluate(collectHostedGuestDom, { stage: 'detail' }));
      if (title.access !== 'normal') fail('access_restricted');
    }
    const cartCaption = cart && title?.ready === true && detail?.status === 'observed' ?
      parseCapturedCartCaption(dom.cart_caption, title.title_core) : null;
    const observed = {
      summary_supported: cart ? cartDom?.code === 'observed' && cartDom.form_count === 1 && cartDom.order_container_count === 1 && cartDom.summary_count === 1 : summary?.status === 'observed',
      seller_matches: cart ? detail?.seller_handle === authorization.seller && dom.listing === true : summary?.status === 'observed' && summary.seller_handle === authorization.seller,
      exact_preparation_amounts: cart ? cartDom?.subtotal?.value === '$0.10 USD' && cartDom?.shipping?.value === '$4.95 USD' && cartDom?.total?.value === '$5.05 USD' :
        summary?.status === 'observed' && summary.displayed.subtotal.amount === '0.10' && summary.displayed.shipping.amount === '4.95' && summary.displayed.total.amount === '5.05',
      quantity_declaration: dom.quantity === true,
      destination_match: confirming ? true : null,
      listing_detail_supported: title?.ready === true && detail?.status === 'observed' && detail?.release_id === Number(authorization.release_id) &&
        detail?.listing_id === authorization.listing_id && detail?.seller_handle === authorization.seller,
      title_continuity: title?.ready === true && detail?.status === 'observed' && !!title.title_digest &&
        (cart ? !!cartCaption && cartCaption.full_caption_digest === dom.cart_caption_digest : dom.title_digest === title.title_digest),
      condition_continuity: title?.ready === true && detail?.status === 'observed' && dom.media === detail.media && dom.sleeve === detail.sleeve,
      item_price_continuity: cart ? cartDom?.subtotal?.value === '$0.10 USD' : dom.item === '0.10',
      provider_frame_associated: false, card_control_unique_visible_enabled: false,
      existing_guest_form: dom.existing_form === true,
    };
    const detailErrors = new Set(['dom_unavailable', 'invalid_exact_identity', 'listing_changed_during_read',
      'listing_dom_read_failed', 'listing_fields_unavailable', 'listing_grades_unavailable',
      'listing_main_unavailable', 'listing_not_ready', 'listing_release_mismatch', 'listing_url_mismatch']);
    const boundedCount = value => Number.isSafeInteger(value) && value >= 0 ? Math.min(value, 2) : null;
    const detailDiagnostic = {
      status: detailPages.length === 0 ? 'missing' : detailPages.length > 1 ? 'ambiguous' : detail?.status === 'observed' ? 'observed' : 'incomplete',
      error_code: detailPages.length === 0 ? 'listing_detail_required' : detail?.status === 'observed' ? null :
        detailErrors.has(detail?.error_code) ? detail.error_code : 'listing_detail_unverified',
      ready_state: ['loading', 'interactive', 'complete'].includes(readiness?.ready_state) ? readiness.ready_state : 'unknown',
      main_count: boundedCount(readiness?.main_count), release_link_count: boundedCount(readiness?.release_link_count),
      grade_count: boundedCount(readiness?.grade_count),
    };
    const readerCounts = cartDom?.binding_counts;
    const readerCountKeys = ['legacy_count', 'go_count', 'select_count', 'container_count', 'global_row_count', 'global_summary_count'];
    const readerGates = ['owner_form_missing', 'owner_form_mismatch', 'control_binding_mismatch', 'shipping_form_mismatch',
      'row_binding_mismatch', 'summary_binding_mismatch', 'complete'];
    const cartReaderDiagnostic = cart && readerCounts && Object.keys(readerCounts).sort().join('|') === readerCountKeys.sort().join('|') &&
      readerCountKeys.every(key => Number.isInteger(readerCounts[key]) && readerCounts[key] >= 0 && readerCounts[key] <= 2) &&
      readerGates.includes(cartDom.binding_gate) && cartDom.code === 'observed' ? {
        schema: 'cart_reader_predicates_v1', code: cartDom.code, first_failed: cartDom.binding_gate, counts: readerCounts,
      } : null;
    const frames = (await read(() => page.frames())).filter(frame => isObservedCardFrame(frame.url()));
    if (frames.length === 1 && frames[0].parentFrame() === page.mainFrame()) {
      const frame = frames[0], element = await read(() => frame.frameElement());
      observed.provider_frame_associated = await read(() => element.isVisible()) &&
        await read(() => element.contentFrame()) === frame && await read(() => element.ownerFrame()) === page.mainFrame();
      if (observed.provider_frame_associated) {
        const provider = await read(() => frame.evaluate(collectHostedGuestDom, { stage: 'provider' }));
        if (provider.access !== 'normal') fail('access_restricted');
        observed.existing_guest_form ||= provider.existing_form === true;
        if (await read(() => frame.locator(GUEST_CARD_SELECTOR).count()) === 1) {
          const card = await read(() => frame.$(GUEST_CARD_SELECTOR));
          observed.card_control_unique_visible_enabled = !!card && await read(() => card.isVisible()) &&
            await read(() => card.isEnabled()) && await read(() => card.getAttribute('aria-disabled')) === 'false';
        }
      }
    }
    const finalSnapshot = await read(() => page.evaluate(collectHostedGuestDom, {
      stage: cart ? 'cart' : 'checkout', privateDestinationSnapshot: true }));
    if (finalSnapshot.access !== 'normal') fail('access_restricted');
    if (finalSnapshot.private_destination_snapshot !== dom.private_destination_snapshot) fail('saved_destination_snapshot_changed');
    if (page.url() !== url || !await read(() => page.evaluate(expected => document === expected, documentHandle))) fail('probe_document_changed');
    // A document ref is an opaque observation handle, not destination approval.
    if (!confirming) {
      job.page = page; job.url = url; job.document_handle = documentHandle; job.document_ref = randomUUID();
      job.reference_created_at = Date.now() / 1000;
      job.reference_expires_at = Math.min(authorization.expires_at, job.reference_created_at + 300);
      job.private_destination_snapshot = dom.private_destination_snapshot || null;
    } else {
      const bindingAt = Date.now() / 1000;
      if (bindingAt >= job.reference_expires_at) fail('probe_reference_expired');
      if (bindingAt >= authorization.destination_ack.observed_at + 60 || bindingAt >= authorization.expires_at) fail('readonly_authorization_required');
      job.approved_destination_digest = dom.private_destination_snapshot;
    }
    return { status: 'observed', code: confirming ? 'approved_saved_destination_bound' : 'current_dom_evidence_only', observed,
      document_ref: job.document_ref, merchant_route: cart ? 'cart' : 'enhanced_checkout',
      address_present: dom.address_present === true, destination_binding_available: confirming,
      destination_match: confirming ? true : null,
      detail_diagnostic: detailDiagnostic,
      ...(cart ? { cart_binding_diagnostic: dom.cart_binding_diagnostic } : {}),
      ...(cartReaderDiagnostic ? { cart_reader_diagnostic: cartReaderDiagnostic } : {}),
      structure: { item_row_count: dom.item_row_count, item_title_present: dom.item_title_present,
        media_grade: dom.media, sleeve_grade: dom.sleeve, address_container_count: dom.address_container_count,
        address_line_count: dom.address_line_count },
      missing_fields: Object.keys(observed).filter(key => key !== 'existing_guest_form' && !observed[key]),
      normal_cart_transition_verified: false, initialization_allowed: false,
      source: 'current_visible_dom', final_quote_verified: false,
      card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false };
  } catch (error) { return stopped(error?.[STOP] || 'probe_unverified'); }
}

/** Separately authorized, one-use normal Cart -> fresh Checkout preflight.
 * No transaction claim, card control, initialization, Pay or order operation.
 */
export async function preflightHostedGuestCheckout(context, request, options = {}) {
  const authorization = options.authorization;
  const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join('|') === keys.sort().join('|');
  if (!context || !exact(request, ['schema', 'job_id', 'phase', 'document_ref']) ||
      request.schema !== 'discogs_guest_checkout_preflight_v1' || request.phase !== 'navigate_checkout_preflight' ||
      authorization?.schema !== 'discogs_guest_preflight_authorization_v1' || authorization.mode !== 'cart_checkout_preflight' ||
      !/^[0-9a-f]{64}$/u.test(authorization.expected_ledger_head || '')) return stopped('preflight_authorization_required');
  const { mode, expected_ledger_head: expectedHead, ...readonly } = authorization;
  if (!readonlyAuthorizationValid({ schema: 'discogs_guest_readonly_probe_v1', job_id: request.job_id,
    phase: 'confirm_saved_destination', document_ref: request.document_ref },
  { ...readonly, schema: 'discogs_guest_readonly_authorization_v1' })) return stopped('preflight_authorization_required');
  request = JSON.parse(stable(request));
  const auth = JSON.parse(stable(authorization)), signal = options.signal;
  const job = readonlyProbeJobs.get(request.job_id), now = Date.now() / 1000;
  if (!job || job.context !== context || job.document_ref !== request.document_ref || job.url !== CART ||
      !job.approved_destination_digest || job.reference_expires_at <= now ||
      auth.destination_ack.observed_at < job.reference_created_at) return stopped('preflight_confirmation_required');
  if (job.navigation_consumed) return stopped('probe_job_replayed');
  if (active.has(context) || ledgerActive) return stopped('operation_busy');
  if (uncertain.has(context)) return stopped('navigation_requires_reconciliation');
  job.navigation_consumed = true;
  const directory = options[fixtureDirectory] || HOSTED_GUEST_LEDGER;
  const deadline = Math.min(Date.now() + 30000, auth.expires_at * 1000, job.reference_expires_at * 1000,
    (auth.destination_ack.observed_at + 60) * 1000);
  const pending = new Set(); let finished = false, released = false, navigationIssued = false;
  let diagnostics = {};
  active.add(context); ledgerActive = true;
  const release = () => { if (finished && !pending.size && !released) { released = true; active.delete(context); ledgerActive = false; } };
  const check = () => { if (signal?.aborted) fail('cancelled'); if (Date.now() >= deadline) fail('deadline_expired'); };
  const track = operation => {
    const task = retainContextTask(context, Promise.resolve().then(() => { check(); return operation(); }));
    pending.add(task); const settled = () => { pending.delete(task); release(); }; task.then(settled, settled); return task;
  };
  const read = async operation => {
    check(); let timer, onAbort;
    const task = track(operation);
    const interruption = new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('deadline_expired'), { [STOP]: 'deadline_expired' })), deadline - Date.now());
      if (signal) { onAbort = () => reject(Object.assign(new Error('cancelled'), { [STOP]: 'cancelled' })); signal.addEventListener('abort', onAbort, { once: true }); }
    });
    try { const value = await Promise.race([task, interruption]); check(); return value; }
    finally { clearTimeout(timer); if (onAbort) signal.removeEventListener('abort', onAbort); }
  };
  const ledgerGuard = async () => {
    const state = await read(() => checkoutOperator.inspectGuestCheckoutLedger({ directory }));
    if (!['no_prior_attempt', 'known_session_expired', 'reviewed_form_disposition'].includes(state?.state) ||
        state.current_head !== expectedHead) fail('preflight_ledger_unverified');
  };
  try {
    await ledgerGuard(); // Distinct navigation disposition gate; never an init grant.
    const pages = await read(() => context.pages()), page = job.page;
    const details = pages.filter(candidate => ['https://www.discogs.com/sell/item/1366950910', 'https://www.discogs.com/shop/item/1366950910'].includes(candidate.url()));
    if (pages.length !== 2 || !pages.includes(page) || details.length !== 1) fail('listing_detail_required');
    const detailPage = details[0], detailUrl = detailPage.url(), detailDocument = await read(() => detailPage.evaluateHandle(() => document));
    const detailRead = async () => {
      if (detailPage.url() !== detailUrl || !await read(() => detailPage.evaluate(doc => document === doc, detailDocument))) fail('listing_detail_changed');
      const detail = await read(() => detailPage.evaluate(collectListingDetailDom, { release_id: '3422100', listing_id: '1366950910' }));
      const title = await read(() => detailPage.evaluate(collectHostedGuestDom, { stage: 'detail' }));
      if (title.access !== 'normal') fail('access_restricted');
      if (!title.ready || detail.status !== 'observed' || !detail.complete || detail.release_id !== 3422100 ||
          detail.listing_id !== '1366950910' || detail.seller_handle !== 'Mailman21842' || detail.item !== '0.10' ||
          detail.cart_state !== 'already_in_cart' || !title.title_core || !title.title_digest) fail('listing_detail_unverified');
      return { core: title.title_core, title_digest: title.title_digest, media: detail.media, sleeve: detail.sleeve };
    };
    const detail = await detailRead(); let cartTitle, cartShippingLabel;
    const basic = dom => {
      diagnostics = { structure: { address_container_count: dom.address_container_count, address_line_count: dom.address_line_count } };
      if (dom.access !== 'normal') fail('access_restricted');
      if (!dom.ready) fail('page_not_ready');
      if (dom.existing_form) fail('existing_guest_form_preserved');
      if (dom.agreements) fail('extra_agreement_required');
      const missing = ['quantity', 'destination'].filter(key => dom[key] !== true);
      if (missing.length) { diagnostics.missing_fields = missing; fail('visible_scope_evidence_missing'); }
    };
    const cartRead = async () => {
      if (page.url() !== CART || !await read(() => page.evaluate(doc => document === doc, job.document_handle))) fail('cart_document_changed');
      const raw = await read(() => page.evaluate(collectCartDom, '1366950910'));
      const dom = await read(() => page.evaluate(collectHostedGuestDom, { stage: 'cart', approvedDestinationDigest: job.approved_destination_digest }));
      basic(dom);
      const selectedLabel = selectedCartShippingLabel(raw);
      if (cartShippingLabel && selectedLabel !== cartShippingLabel) fail('cart_document_changed');
      const parsed = parseCapturedCartCaption(dom.cart_caption, detail.core);
      if (!parsed || parsed.full_caption_digest !== dom.cart_caption_digest) { diagnostics.missing_fields = ['title_continuity']; fail('visible_scope_evidence_missing'); }
      if (cartTitle && parsed.full_caption_digest !== cartTitle.full_caption_digest) fail('cart_document_changed');
      if (raw.code !== 'observed' || raw.form_count !== 1 || raw.order_container_count !== 1 || raw.item_row_count !== 1 ||
          raw.listing_matches_expected !== true || raw.canonical_listing_href !== '/sell/item/1366950910' || !dom.listing || !dom.go ||
          raw.quantity_control_count !== 0 || raw.checkout_control_kind !== 'v2_go_to_checkout' || raw.missing_address !== null ||
          dom.address_present !== true || dom.destination !== true || raw.subtotal?.value !== '$0.10 USD' ||
          raw.shipping?.value !== '$4.95 USD' || raw.total?.value !== '$5.05 USD' ||
          !selectedLabel) fail('cart_scope_unverified');
      if (stable(await detailRead()) !== stable(detail)) fail('listing_detail_changed');
      cartShippingLabel = selectedLabel;
      cartTitle = parsed; // Cart grades remain unknown; no row-text inference.
    };
    await cartRead(); const go = await read(() => page.$(HOSTED_GO_SELECTOR));
    if (!go) fail('checkout_control_unverified');
    await cartRead(); await ledgerGuard();
    if (!await read(() => go.evaluate(revalidateHostedGoFormDom))) fail('checkout_control_changed');
    // A document navigation response distinguishes normal navigation from a
    // same-document URL edit. This fixed wait is registered before the click.
    const navigation = track(() => page.waitForNavigation({ url: GUEST_CARD_URL, waitUntil: 'load', timeout: Math.max(1, deadline - Date.now()) }));
    await read(() => { check(); navigationIssued = true; return go.click({ timeout: Math.max(1, deadline - Date.now()), noWaitAfter: true }); });
    const response = await read(() => navigation);
    if (!response || response.url() !== GUEST_CARD_URL || page.url() !== GUEST_CARD_URL) fail('fresh_checkout_required');
    const checkoutDocument = await read(() => page.evaluateHandle(() => document));
    const inspectorPage = { url: () => track(() => page.url()), evaluate: (...args) => track(() => page.evaluate(...args)) };
    const verifyCheckout = async () => {
      const dom = await read(() => page.evaluate(collectHostedGuestDom, { stage: 'checkout', approvedDestinationDigest: job.approved_destination_digest }));
      basic(dom);
      const summary = await read(() => inspectCurrentEnhancedCheckout(inspectorPage, { signal }));
      if (dom.title_digest !== detail.title_digest || !checkoutCaptionMatchesCart(cartTitle, dom.title_artist, dom.title_formats)) {
        diagnostics.missing_fields = ['title_continuity']; fail('visible_scope_evidence_missing');
      }
      if (dom.media !== detail.media || dom.sleeve !== detail.sleeve) { diagnostics.missing_fields = ['condition_continuity']; fail('visible_scope_evidence_missing'); }
      if (summary.status !== 'observed' || summary.seller_handle !== 'Mailman21842' || summary.displayed.subtotal.amount !== '0.10' ||
          summary.displayed.shipping.amount !== '4.95' || summary.displayed.total.amount !== '5.05' ||
          summary.selected_shipping_label !== 'Economy - Media Mail' || dom.item !== '0.10') fail('checkout_scope_unverified');
    };
    await verifyCheckout();
    if (stable(await detailRead()) !== stable(detail)) fail('listing_detail_changed');
    await ledgerGuard();
    await verifyCheckout();
    if (page.url() !== GUEST_CARD_URL || !await read(() => page.evaluate(doc => document === doc, checkoutDocument))) fail('checkout_document_changed');
    return { status: 'observed', code: 'cart_checkout_preflight_observed', merchant_route: 'enhanced_checkout',
      normal_cart_transition_verified: true, cart_conditions_verified: false, checkout_conditions_verified: true,
      destination_binding_available: true, destination_match: true, initialization_allowed: false,
      card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false,
      order_status: 'unverified', payment_status: 'unverified' };
  } catch (error) {
    if (navigationIssued) uncertain.add(context);
    return stopped(error?.[STOP] || 'unknown_internal_stop', { ...diagnostics, checkout_navigation_issued: navigationIssued,
      card_initialization_issued: false, initialization: 'not_dispatched', normal_cart_transition_verified: false });
  } finally { finished = true; release(); }
}
export const preflightCurrentGuestCheckout = preflightHostedGuestCheckout;
/** Isolated fixture only; production always inspects the existing fixed ledger. */
export function createHostedGuestPreflightFixture({ context, directory, authorization, signal } = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !directory?.startsWith('/')) throw new Error('test_context_required');
  return request => preflightHostedGuestCheckout(context, request, { authorization, signal, [fixtureDirectory]: directory });
}
