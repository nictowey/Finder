/**
 * Read-only observation of an already authorized, already open Discogs cart.
 * No browser creation, session access, navigation, requests or checkout actions.
 * Text recognition is NOT DOM, listing, destination, or final-quote verification.
 * This module does not authorize accessing a blocked browser or session.
 */
export const MAX_CART_TEXT_LENGTH = 100_000;
export const CART_READ_TIMEOUT_MS = 1_500;

const CART_URL = /^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/$/;
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
 * Only url() and locator('body').innerText({ timeout }) may be invoked.
 * Timeout bounds our wait; it cannot cancel a nonconforming page implementation.
 */
export async function inspectCurrentCart(page, options = {}) {
  const invalidOptions = optionsError(options);
  if (invalidOptions) return blocked(invalidOptions);
  let timer;
  const timedOut = Symbol('read_timeout');
  try {
    if (!page || typeof page.url !== 'function' || typeof page.locator !== 'function') {
      return blocked('unsupported_page_interface');
    }
    const initialUrl = page.url();
    if (!allowedUrl(initialUrl)) return blocked('current_url_not_allowed');
    const text = await Promise.race([
      Promise.resolve().then(() => page.locator('body').innerText({ timeout: CART_READ_TIMEOUT_MS })),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(timedOut), CART_READ_TIMEOUT_MS);
      }),
    ]);
    // Recheck after the read; even a switch between the two allowed routes fails.
    if (page.url() !== initialUrl) return blocked('current_url_changed');
    return inspectCartText(initialUrl, text, options);
  } catch (error) {
    return blocked(error === timedOut || error?.name === 'TimeoutError' ? 'cart_read_timeout' : 'cart_read_failed');
  } finally {
    clearTimeout(timer);
  }
}
