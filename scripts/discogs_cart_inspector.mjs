/**
 * Read-only observation of an already authorized, already open Discogs cart.
 * No browser creation, session access, navigation, requests or checkout actions.
 * Text/DOM observations are NOT full listing, destination or final-quote verification.
 * This module does not authorize accessing a blocked browser or session.
 */
export const MAX_CART_TEXT_LENGTH = 100_000;
export const CART_READ_TIMEOUT_MS = 1_500;
const CART_READ_TIMEOUT = Symbol('cart_read_timeout');

async function readBeforeDeadline(operation, deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw CART_READ_TIMEOUT;
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => { timer = setTimeout(() => reject(CART_READ_TIMEOUT), remaining); }),
    ]);
  } finally { clearTimeout(timer); }
}

const CART_URL = /^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/?$/;
const UNRESOLVED_CHECKS = Object.freeze([
  'exact_listing_unverified',
  'release_id_unverified',
  'media_condition_unverified',
  'sleeve_condition_unverified',
  'seller_identity_unverified',
  'seller_rating_unverified',
  'quantity_unverified',
  'destination_unverified',
  'currency_unknown',
  'final_shipping_unverified',
  'tax_unverified',
  'fees_unverified',
  'final_total_unverified',
]);

function blocked(code, observations = []) {
  // Only internal codes leave this boundary. Never return input text or errors.
  return {
    status: 'blocked',
    code,
    observations,
    unresolved_checks: [...UNRESOLVED_CHECKS],
    actual_final_verification: 'unsupported',
    stop_required: true,
    purchase_allowed: false,
  };
}

function allowedUrl(url) {
  // Check the literal string, not a URL parser that normalizes ports, whitespace,
  // percent escapes or dot segments. `$` alone also matches before a final LF.
  return typeof url === 'string' && url.length < 80 &&
    !/[\s\x00-\x1f\x7f]/u.test(url) && CART_URL.test(url);
}

function optionsError(options) {
  if (options === null || typeof options !== 'object' || Array.isArray(options)) {
    return 'invalid_inspection_options';
  }
  if (Reflect.ownKeys(options).some(key => key !== 'expectedListingId')) {
    return 'invalid_inspection_options';
  }
  // Require a caller-selected ID, never a getter or an inherited default.
  const id = Object.getOwnPropertyDescriptor(options, 'expectedListingId')?.value;
  return typeof id === 'string' && /^[1-9][0-9]{0,15}$/u.test(id) &&
    !/\s/u.test(id) && Number.isSafeInteger(Number(id)) ? null : 'invalid_expected_listing_id';
}

/** Pure synthetic helper. A caller-selected ID is required, never evidence of a match. */
export function inspectCartText(url, text, options = {}) {
  if (!allowedUrl(url)) return blocked('current_url_not_allowed');
  const invalidOptions = optionsError(options);
  if (invalidOptions) return blocked(invalidOptions);
  if (typeof text !== 'string' || text.length === 0) return blocked('cart_text_unavailable');
  if (text.length > MAX_CART_TEXT_LENGTH) return blocked('cart_text_too_large');
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(text)) {
    return blocked('unsupported_cart_text');
  }

  const lines = text.split(/\r\n?|\n/u)
    .map(line => line.replace(/[\t \u00a0]+/gu, ' ').trim()).filter(Boolean);
  const observations = ['expected_listing_id_supplied'];
  const summaries = lines.filter(line => /^You have .* in your cart from .*\.$/u.test(line));
  if (summaries.length !== 1) return blocked('quantity_unknown', observations);
  const summary = /^You have ([1-9][0-9]{0,3}) items? in your cart from ([1-9][0-9]{0,3}) sellers?\.$/u.exec(summaries[0]);
  if (!summary) return blocked('quantity_unknown', observations);
  if (summary[1] !== '1' || summary[2] !== '1') {
    return blocked('unsupported_cart_quantity', observations);
  }
  observations.push('single_item_single_seller_summary_observed');

  // These are visible-text anchors, NOT verified selectors or checkout controls.
  const anchors = ['Shipping', 'Total', 'I agree to Buyer Policy and Seller Terms', 'Place order and pay now'];
  if (anchors.some(anchor => lines.filter(line => line === anchor).length !== 1)) {
    return blocked('unsupported_cart_shape', observations);
  }
  observations.push('final_action_text_observed', 'agreement_text_observed');

  const setup = lines.filter(line => line === 'Set up your shipping information').length;
  const missingAddress = lines.filter(line => line === "You haven't provided a shipping address yet.").length;
  if (setup !== 1 || missingAddress !== 1) {
    observations.push('shipping_address_unknown');
    return blocked('unsupported_address_shape', observations);
  }
  observations.push('shipping_address_missing');

  // Recognize only the observed unavailable-quote shape. Numeric subtotals, an
  // amount next to Total, currency symbols, seller text and titles are never proof
  // of final shipping, taxes, fees, currency, a release ID or seller reputation.
  const pendingShipping = lines[lines.indexOf('Shipping') + 1] === 'To be determined by seller';
  const pendingTotal = lines[lines.indexOf('Total') + 1] === 'To be determined by seller';
  observations.push(pendingShipping ? 'shipping_to_be_determined' : 'shipping_unknown');
  observations.push(pendingTotal ? 'total_to_be_determined' : 'total_unknown');
  if (!pendingShipping || !pendingTotal) return blocked('unsupported_quote_shape', observations);
  return blocked('missing_address_and_unknown_final_quote', observations);
}

/**
 * The caller must supply an authorized existing Playwright-like page.
 * options.expectedListingId must be a positive safe-integer decimal string.
 * Only url() (sync or async) and locator('body').innerText({ timeout }) are invoked.
 * Timeout bounds our wait; it cannot cancel a nonconforming page implementation.
 */
export async function inspectCurrentCart(page, options = {}) {
  const invalidOptions = optionsError(options);
  if (invalidOptions) return blocked(invalidOptions);
  const deadline = Date.now() + CART_READ_TIMEOUT_MS;
  try {
    if (!page || typeof page.url !== 'function' || typeof page.locator !== 'function') {
      return blocked('unsupported_page_interface');
    }
    const initialUrl = await readBeforeDeadline(() => page.url(), deadline);
    if (!allowedUrl(initialUrl)) return blocked('current_url_not_allowed');
    const text = await readBeforeDeadline(
      () => page.locator('body').innerText({ timeout: CART_READ_TIMEOUT_MS }), deadline);
    // Recheck after the read; even a switch between the two allowed routes fails.
    if (await readBeforeDeadline(() => page.url(), deadline) !== initialUrl) return blocked('current_url_changed');
    return inspectCartText(initialUrl, text, options);
  } catch (error) {
    return blocked(error === CART_READ_TIMEOUT || error?.name === 'TimeoutError' ? 'cart_read_timeout' : 'cart_read_failed');
  }
}

/**
 * Serializable read-only DOM collector. Use page.evaluate(collectCartDom, id).
 * Keep this function self-contained: no imports, outer constants or closures.
 * Its bounded intermediate snapshot is not a quote or permission to purchase.
 */
export function collectCartDom(expectedListingId) {
  const fail = code => ({ schema: 'discogs_cart_dom_v1', code, purchase_allowed: false });
  if (typeof expectedListingId !== 'string' || !/^[1-9][0-9]{0,15}$/u.test(expectedListingId) ||
      /\s/u.test(expectedListingId) || !Number.isSafeInteger(Number(expectedListingId))) {
    return fail('invalid_expected_listing_id');
  }
  if (typeof document === 'undefined') return fail('dom_unavailable');
  const currentUrl = typeof location === 'undefined' ? null : location.href;
  if (typeof currentUrl !== 'string' || currentUrl.length >= 80 || /[\s\x00-\x1f\x7f]/u.test(currentUrl) ||
      !/^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/?$/u.test(currentUrl)) {
    return fail('current_url_not_allowed');
  }
  try {
    const count = nodes => Math.min(nodes.length, 2);
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
    const text = (element, limit = 100) => {
      if (!visible(element)) return null;
      // Rendered scoped text works in the authorized DOM evaluation interface;
      // never use whole-body text or unsupported TreeWalker APIs here.
      let value = element.innerText;
      if (typeof value !== 'string' || value.length > 2_000) return null;
      value = value.replace(/\s+/gu, ' ').trim();
      return value.length <= limit ? value : null;
    };
    const forms = document.querySelectorAll('form.legacy');
    const containers = document.querySelectorAll("[id^='cart-order-'].order_container");
    const result = {
      schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
      form_count: count(forms), order_container_count: count(containers),
    };
    if (forms.length !== 1 || containers.length !== 1 ||
        containers[0].closest('form.legacy') !== forms[0] || !visible(containers[0])) return result;
    const container = containers[0];
    const rows = container.querySelectorAll('tr.order_row');
    const links = rows.length === 1 ? rows[0].querySelectorAll("a.item_link[href^='/sell/item/']") : [];
    const href = links.length === 1 && visible(links[0]) ? links[0].getAttribute('href') : null;
    const canonical = typeof href === 'string' && /^\/sell\/item\/[1-9][0-9]{0,15}$/u.test(href) &&
      !/\s/u.test(href) && Number.isSafeInteger(Number(href.slice('/sell/item/'.length))) ? href : null;
    result.item_row_count = count(rows);
    result.listing_link_count = count(links);
    result.canonical_listing_href = canonical;
    result.listing_matches_expected = canonical === `/sell/item/${expectedListingId}`;
    // Absence of a quantity control does NOT establish the actual quantity.
    result.quantity_control_count = count(container.querySelectorAll('tr.order_row input, tr.order_row select'));
    const addresses = [...container.querySelectorAll("[id^='shipping_address_']")].filter(visible);
    result.address_container_count = count(addresses);
    result.missing_address = addresses.length === 1 &&
      (text(addresses[0], 500)?.includes("You haven't provided a shipping address yet.") ?? false);
    result.edit_shipping_address_present = addresses.length === 1 &&
      [...addresses[0].querySelectorAll('a.edit_shipping_address')].some(visible);
    const summaries = container.querySelectorAll('.order_summary');
    result.summary_count = count(summaries);
    const summary = summaries.length === 1 ? summaries[0] : null;
    const quote = nodes => {
      const row = nodes.length === 1 ? nodes[0].closest('tr') : null;
      const values = row?.querySelectorAll('td.order_summary_value') ?? [];
      const shown = !!row && values.length === 1 && visible(row) && visible(values[0]);
      return { count: count(nodes), visible: shown,
        label: shown ? text(row.querySelector('td')) : null,
        value: shown ? text(values[0]) : null };
    };
    result.subtotal = quote(summary?.querySelectorAll('tr.order_subtotal') ?? []);
    result.shipping = quote(summary?.querySelectorAll('td.order_shipping_amount') ?? []);
    result.total = quote(summary?.querySelectorAll('tr.order_total') ?? []);
    // Read only the generic tax label to find hidden rows; never read their values.
    const taxRows = [...(summary?.querySelectorAll('tr') ?? [])].filter(row => {
      const label = row.querySelector('td')?.textContent?.trim();
      return typeof label === 'string' && label.length <= 100 && /^(?:(?:sales )?tax(?:es)?|vat)$/iu.test(label);
    });
    result.tax = quote(taxRows);
    const buttons = container.querySelectorAll('button.order_button.checkout_v1');
    result.final_button_count = count(buttons);
    result.final_button_visible = buttons.length === 1 && visible(buttons[0]);
    result.final_button_enabled = buttons.length === 1 && !buttons[0].disabled &&
      !buttons[0].matches(':disabled') && buttons[0].getAttribute('aria-disabled') !== 'true';
    const agreements = container.querySelectorAll('input.agree_terms[name="agree_terms"]');
    result.agreement_count = count(agreements);
    result.agreement_visible = agreements.length === 1 && visible(agreements[0]);
    const payments = container.querySelectorAll('input.payment_method[name="payment_method"]');
    result.payment_control_count = count(payments);
    result.payment_control_visible = payments.length === 1 && visible(payments[0]);
    return result;
  } catch {
    return fail('dom_read_failed');
  }
}

/** Inspect a bounded collector snapshot; never return its hrefs or quote text. */
export function inspectCartDomSnapshot(url, snapshot, options = {}) {
  if (!allowedUrl(url)) return blocked('current_url_not_allowed');
  const invalidOptions = optionsError(options);
  if (invalidOptions) return blocked(invalidOptions);
  if (!snapshot || snapshot.schema !== 'discogs_cart_dom_v1' || snapshot.purchase_allowed !== false ||
      snapshot.code !== 'observed') return blocked('unsupported_cart_dom');
  const observations = ['expected_listing_id_supplied'];
  if (snapshot.form_count !== 1 || snapshot.order_container_count !== 1) {
    return blocked('unsupported_cart_dom', observations);
  }
  if (snapshot.item_row_count !== 1) return blocked('unsupported_cart_quantity', observations);
  if (snapshot.listing_link_count !== 1 || snapshot.listing_matches_expected !== true ||
      snapshot.canonical_listing_href !== `/sell/item/${options.expectedListingId}`) {
    return blocked('expected_listing_not_observed', observations);
  }
  observations.push('expected_listing_link_observed', 'single_cart_row_observed');
  if (snapshot.quantity_control_count !== 0) return blocked('unsupported_quantity_controls', observations);
  observations.push('quantity_controls_absent_not_verification');
  if (snapshot.address_container_count !== 1 || snapshot.edit_shipping_address_present !== true ||
      typeof snapshot.missing_address !== 'boolean') return blocked('unsupported_address_shape', observations);
  observations.push(snapshot.missing_address ? 'shipping_address_missing' : 'shipping_address_unknown');
  if (snapshot.summary_count !== 1) return blocked('unsupported_quote_shape', observations);
  const validQuote = quote => quote && Number.isInteger(quote.count) && quote.count >= 0 && quote.count <= 2 &&
    typeof quote.visible === 'boolean' && ['label', 'value'].every(key => quote[key] === null ||
      (typeof quote[key] === 'string' && quote[key].length <= 100));
  if (!['subtotal', 'shipping', 'tax', 'total'].every(key => validQuote(snapshot[key]))) {
    return blocked('unsupported_quote_shape', observations);
  }
  observations.push(snapshot.tax.count === 1 && !snapshot.tax.visible ? 'tax_hidden_unknown' : 'tax_unverified');
  if (snapshot.final_button_count !== 1 || typeof snapshot.final_button_enabled !== 'boolean' ||
      snapshot.final_button_visible !== true || snapshot.agreement_count !== 1 ||
      snapshot.agreement_visible !== true || snapshot.payment_control_count !== 1 ||
      typeof snapshot.payment_control_visible !== 'boolean') return blocked('unsupported_checkout_controls', observations);
  observations.push(snapshot.final_button_enabled ? 'final_button_enabled_not_permission' : 'final_button_disabled');
  observations.push('agreement_control_observed', 'payment_control_observed');
  const shipping = snapshot.shipping, total = snapshot.total;
  if (shipping.count !== 1 || !shipping.visible || shipping.label !== 'Shipping' ||
      total.count !== 1 || !total.visible || total.label !== 'Total') {
    return blocked('unsupported_quote_shape', observations);
  }
  const shippingPending = shipping.value === 'To be determined by seller';
  const totalPending = total.value === 'To be determined by seller';
  observations.push(shippingPending ? 'shipping_to_be_determined' : 'shipping_unknown');
  observations.push(totalPending ? 'total_to_be_determined' : 'total_unknown');
  return blocked(snapshot.missing_address && shippingPending && totalPending ?
    'missing_address_and_unknown_final_quote' : 'final_quote_unverified', observations);
}

/** Only reads sync/async page.url() and page.evaluate(collectCartDom, expectedListingId). */
export async function inspectCurrentCartDom(page, options = {}) {
  const invalidOptions = optionsError(options);
  if (invalidOptions) return blocked(invalidOptions);
  const deadline = Date.now() + CART_READ_TIMEOUT_MS;
  try {
    if (!page || typeof page.url !== 'function' || typeof page.evaluate !== 'function') {
      return blocked('unsupported_page_interface');
    }
    const initialUrl = await readBeforeDeadline(() => page.url(), deadline);
    if (!allowedUrl(initialUrl)) return blocked('current_url_not_allowed');
    const snapshot = await readBeforeDeadline(
      () => page.evaluate(collectCartDom, options.expectedListingId), deadline);
    if (await readBeforeDeadline(() => page.url(), deadline) !== initialUrl) return blocked('current_url_changed');
    return inspectCartDomSnapshot(initialUrl, snapshot, options);
  } catch (error) {
    return blocked(error === CART_READ_TIMEOUT || error?.name === 'TimeoutError' ? 'cart_read_timeout' : 'cart_read_failed');
  }
}
