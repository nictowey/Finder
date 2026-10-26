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
  // Blocker diagnostics contain only internal codes, never raw text or errors.
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
    const containers = document.querySelectorAll("[id^='cart-order-'].order_container");
    const container = containers.length === 1 ? containers[0] : null;
    const orderForm = container?.closest('form') ?? null;
    const legacyButtons = document.querySelectorAll('button.order_button.checkout_v1');
    const goButtons = document.querySelectorAll('button.order_button.checkout_v2[name="Action.ProceedCommerceCheckout"]');
    const methods = document.querySelectorAll('.order_shipping_fields select.shipping_method[name="shipping_method"]');
    const result = {
      schema: 'discogs_cart_dom_v1', code: 'observed', purchase_allowed: false,
      form_count: orderForm ? 1 : 0, order_container_count: count(containers),
    };
    if (!orderForm || !container || !orderForm.contains(container) || !visible(container)) return result;
    // Retain legacy read-only diagnostics even when its final control is
    // missing or malformed; the classifier below will block that control.
    const legacy = goButtons.length === 0 && [...legacyButtons].every(button =>
      container.contains(button) && button.form === orderForm);
    const v2 = goButtons.length === 1 && legacyButtons.length === 0 && methods.length === 1 &&
      container.contains(goButtons[0]) && goButtons[0].form === orderForm &&
      container.contains(methods[0]) && methods[0].form === orderForm &&
      visible(goButtons[0]) && goButtons[0].type === 'submit' &&
      goButtons[0].value === 'Go to Checkout' && text(goButtons[0]) === 'Go to Checkout';
    if (!legacy && !v2) return result;
    if (v2 && methods.length !== 1 || methods.length === 1 &&
        (!container.contains(methods[0]) || methods[0].form !== orderForm)) return result;
    result.checkout_control_kind = v2 ? 'v2_go_to_checkout' : 'legacy_place_order';
    const rows = container.querySelectorAll('tr.order_row');
    if (rows.length !== 1 || document.querySelectorAll('tr.order_row').length !== 1 ||
        !orderForm.contains(rows[0]) || !visible(rows[0])) return result;
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
    // V2 contains private address text with a different edit structure. Do
    // not read that text or infer destination from the wrapper's presence.
    result.missing_address = v2 ? null : addresses.length === 1 &&
      (text(addresses[0], 500)?.includes("You haven't provided a shipping address yet.") ?? false);
    result.edit_shipping_address_present = v2 ? false : addresses.length === 1 &&
      [...addresses[0].querySelectorAll('a.edit_shipping_address')].some(visible);
    const summaries = container.querySelectorAll('.order_summary');
    result.summary_count = count(summaries);
    const summary = summaries.length === 1 ? summaries[0] : null;
    if (!summary || document.querySelectorAll('.order_summary').length !== 1 ||
        !orderForm.contains(summary) || !visible(summary)) return result;
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
    // Read only tax/deferred-tax labels to find hidden rows; never read their values.
    const taxRows = [...(summary?.querySelectorAll('tr') ?? [])].filter(row => {
      const label = row.querySelector('td')?.textContent?.trim();
      return typeof label === 'string' && label.length <= 100 &&
        /^(?:(?:sales )?tax(?:es)?|vat|plus applicable sales tax)$/iu.test(label);
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
    result.navigation_button_count = count(goButtons);
    result.navigation_button_visible = goButtons.length === 1 && visible(goButtons[0]);
    result.navigation_button_enabled = goButtons.length === 1 && !goButtons[0].disabled &&
      !goButtons[0].matches(':disabled') && goButtons[0].getAttribute('aria-disabled') !== 'true';
    const shownMethod = methods.length === 1 && visible(methods[0]);
    const methodOptions = shownMethod ? methods[0].options : [];
    result.shipping_methods = {
      selector_count: count(methods), visible: shownMethod,
      option_count: Math.min(methodOptions.length, 20), options_over_limit: methodOptions.length > 20,
      selected_index: shownMethod && Number.isInteger(methods[0].selectedIndex) &&
        methods[0].selectedIndex >= -1 && methods[0].selectedIndex < 20 ? methods[0].selectedIndex : null,
      options: methodOptions.length <= 20 ? [...methodOptions].map(option => {
        const label = typeof option.label === 'string' ? option.label.replace(/\s+/gu, ' ').trim() : null;
        // DOM properties reflect current selection; attributes may be stale.
        // Never read, persist or return an option value, and never select one.
        return { label: label && label.length <= 100 ? label : null,
          selected: option.selected === true, disabled: option.disabled === true };
      }) : [],
    };
    return result;
  } catch {
    return fail('dom_read_failed');
  }
}

// Displayed money is not a final quote. Keep exact decimal strings, never floats,
// and match the existing rule editor's currencies and seven-digit amount bound.
function displayedMoney(value) {
  if (typeof value !== 'string' || value.length > 100 || value.trim() !== value ||
      /[\x00-\x1f\x7f]/u.test(value)) return null;
  const symbols = { USD: ['$', 'US$'], EUR: ['€'], GBP: ['£'], CAD: ['$', 'CA$', 'C$'], AUD: ['$', 'AU$', 'A$'] };
  let currency = null, body = value;
  const prefix = /^(USD|EUR|GBP|CAD|AUD) (.+)$/u.exec(value);
  const suffix = /^(.+?) ?(USD|EUR|GBP|CAD|AUD)$/u.exec(value);
  if (prefix) { currency = prefix[1]; body = prefix[2]; }
  else if (suffix) { currency = suffix[2]; body = suffix[1]; }
  const match = /^(US\$|CA\$|C\$|AU\$|A\$|\$|€|£)? ?((?:0|[1-9][0-9]{0,6}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2})\.[0-9]{2})$/u.exec(body);
  if (!match) return null;
  const symbol = match[1];
  if (!currency) currency = { 'US$': 'USD', '€': 'EUR', '£': 'GBP', 'CA$': 'CAD', 'C$': 'CAD', 'AU$': 'AUD', 'A$': 'AUD' }[symbol];
  if (!currency || (symbol && !symbols[currency].includes(symbol))) return null;
  const amount = match[2].replace(/,/gu, '');
  return amount.split('.')[0].length <= 7 ? { amount, currency } : null;
}

function observedShippingMethod(methods) {
  if (!methods || methods.selector_count !== 1 || methods.visible !== true || methods.options_over_limit !== false ||
      !Number.isInteger(methods.option_count) || methods.option_count < 1 || methods.option_count > 20 ||
      !Array.isArray(methods.options) || methods.options.length !== methods.option_count ||
      !Number.isInteger(methods.selected_index) || methods.selected_index < 0 || methods.selected_index >= methods.option_count) return null;
  if (!methods.options.every(option => option && typeof option.selected === 'boolean' && typeof option.disabled === 'boolean' &&
      (option.label === null || (typeof option.label === 'string' && option.label.length > 0 && option.label.length <= 100 &&
        option.label.trim() === option.label && !/[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]/u.test(option.label)))) ||
      methods.options.filter(option => option.selected).length !== 1) return null;
  const selected = methods.options[methods.selected_index];
  return selected.selected && !selected.disabled ? selected.label : null;
}

/**
 * Interpret a bounded snapshot as static codes plus normalized displayed money
 * and a bounded selected-method label. Never return raw hrefs, addresses or errors.
 */
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
  if (snapshot.checkout_control_kind === 'v2_go_to_checkout') {
    if (snapshot.address_container_count !== 1 || snapshot.missing_address !== null ||
        snapshot.edit_shipping_address_present !== false) return blocked('unsupported_address_shape', observations);
    observations.push('shipping_address_unverified');
  } else {
    if (snapshot.address_container_count !== 1 || snapshot.edit_shipping_address_present !== true ||
        typeof snapshot.missing_address !== 'boolean') return blocked('unsupported_address_shape', observations);
    observations.push(snapshot.missing_address ? 'shipping_address_missing' : 'address_warning_absent_destination_unverified');
  }
  if (snapshot.summary_count !== 1) return blocked('unsupported_quote_shape', observations);
  const validQuote = quote => quote && Number.isInteger(quote.count) && quote.count >= 0 && quote.count <= 2 &&
    typeof quote.visible === 'boolean' && ['label', 'value'].every(key => quote[key] === null ||
      (typeof quote[key] === 'string' && quote[key].length <= 100));
  if (!['subtotal', 'shipping', 'tax', 'total'].every(key => validQuote(snapshot[key]))) {
    return blocked('unsupported_quote_shape', observations);
  }
  observations.push(snapshot.tax.count > 0 && !snapshot.tax.visible ? 'tax_hidden_unknown' : 'tax_unverified');
  if (snapshot.checkout_control_kind === 'v2_go_to_checkout') {
    if (snapshot.navigation_button_count !== 1 || snapshot.navigation_button_visible !== true ||
        typeof snapshot.navigation_button_enabled !== 'boolean' ||
        snapshot.final_button_count !== 0) return blocked('unsupported_checkout_controls', observations);
    observations.push('checkout_navigation_observed_not_order_control');
  } else if (snapshot.checkout_control_kind === 'legacy_place_order') {
    if (snapshot.final_button_count !== 1 || typeof snapshot.final_button_enabled !== 'boolean' ||
        snapshot.final_button_visible !== true || snapshot.agreement_count !== 1 ||
        snapshot.agreement_visible !== true || snapshot.payment_control_count !== 1 ||
        typeof snapshot.payment_control_visible !== 'boolean') return blocked('unsupported_checkout_controls', observations);
    observations.push(snapshot.final_button_enabled ? 'final_button_enabled_not_permission' : 'final_button_disabled');
    observations.push('agreement_control_observed', 'payment_control_observed');
  } else return blocked('unsupported_checkout_controls', observations);
  const shipping = snapshot.shipping, total = snapshot.total;
  if (shipping.count !== 1 || !shipping.visible || shipping.label !== 'Shipping' ||
      total.count !== 1 || !total.visible || total.label !== 'Total') {
    return blocked('unsupported_quote_shape', observations);
  }
  const shippingPending = shipping.value === 'To be determined by seller';
  const totalPending = total.value === 'To be determined by seller';
  const observed = { subtotal: null, shipping: null, tax: null, fees: null, total: null };
  for (const [key, label] of [['subtotal', /^Subtotal$/u], ['shipping', /^Shipping$/u],
    ['tax', /^(?:(?:sales )?tax(?:es)?|vat)$/iu], ['total', /^Total$/u]]) {
    const quote = snapshot[key];
    if (quote.count === 1 && quote.visible && label.test(quote.label)) observed[key] = displayedMoney(quote.value);
  }
  const currencies = new Set(Object.values(observed).filter(Boolean).map(money => money.currency));
  if (currencies.size > 1) {
    for (const key of Object.keys(observed)) observed[key] = null;
    observations.push('displayed_currency_mismatch');
  }
  if (observed.subtotal) observations.push('subtotal_amount_observed_not_verified');
  observations.push(shippingPending ? 'shipping_to_be_determined' : observed.shipping ? 'shipping_amount_observed_not_verified' : 'shipping_unknown');
  observations.push(totalPending ? 'total_to_be_determined' : observed.total ? 'total_amount_observed_not_verified' : 'total_unknown');
  const selectedLabel = observedShippingMethod(snapshot.shipping_methods);
  observations.push(selectedLabel ? 'shipping_method_selection_observed_not_verified' : 'shipping_method_unknown');
  const result = blocked(snapshot.missing_address && shippingPending && totalPending ?
    'missing_address_and_unknown_final_quote' : 'final_quote_unverified', observations);
  if (observed.subtotal && observed.shipping && observed.total) {
    result.unresolved_checks = result.unresolved_checks.map(code => code === 'currency_unknown' ? 'currency_unverified' : code);
  }
  result.observed_quote = { source: 'visible_cart', final_quote_verified: false, ...observed };
  result.observed_shipping_method = { source: 'visible_cart', selected_label: selectedLabel, selection_verified: false };
  return result;
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
