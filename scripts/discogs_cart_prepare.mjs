/** Optional single UI add on a caller-authorized existing listing page. Never checkout. */
import { inspectCurrentCartDom } from './discogs_cart_inspector.mjs';

export const CART_PREPARE_TIMEOUT_MS = 2_000;
export const MAX_CART_PREPARE_TIMEOUT_MS = 30_000;
// Page-wide, process-local only. Not durable or cross-page; never a purchase ledger.
const attempts = new WeakMap();

function stopped(code, state = 'not_started', cart = null) {
  return { status: 'blocked', code, preparation_state: state, cart_inspection: cart,
    stop_required: true, purchase_allowed: false };
}

function preparationOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Reflect.ownKeys(options).some(key => !['expectedListingId', 'allowAddToCart', 'timeoutMs'].includes(key))) {
    return { error: 'invalid_preparation_options' };
  }
  const id = Object.getOwnPropertyDescriptor(options, 'expectedListingId')?.value;
  if (typeof id !== 'string' || !/^[1-9][0-9]{0,15}$/u.test(id) || /\s/u.test(id) ||
      !Number.isSafeInteger(Number(id))) return { error: 'invalid_expected_listing_id' };
  const permission = Object.getOwnPropertyDescriptor(options, 'allowAddToCart');
  if (permission && typeof permission.value !== 'boolean') return { error: 'invalid_preparation_options' };
  const timeout = Object.getOwnPropertyDescriptor(options, 'timeoutMs');
  if (timeout && (!Number.isInteger(timeout.value) || timeout.value < 5_000 || timeout.value > MAX_CART_PREPARE_TIMEOUT_MS)) {
    return { error: 'invalid_preparation_options' };
  }
  return { id, allowed: permission?.value === true, timeoutMs: timeout?.value ?? CART_PREPARE_TIMEOUT_MS };
}

function urlKind(url, id) {
  if (typeof url !== 'string' || url.length > 120 || /[\s\x00-\x1f\x7f]/u.test(url)) return null;
  if (/^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/?$/u.test(url)) return 'cart';
  const match = /^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/item\/([1-9][0-9]{0,15})$/u.exec(url);
  return match?.[1] === id ? 'listing' : null;
}

/**
 * With allowAddToCart:true, click one visible verified Add to Cart link once.
 * Only the verified post-add query URL may be navigated once to its same-origin
 * query-free /sell/cart/ page. No other navigation, removal or checkout action.
 * Any click error or unresolved outcome permanently prevents another attempt for
 * this page object in this module instance, even for another listing. Human review
 * is then needed. This does not prevent actions from other pages/processes.
 */
export async function prepareCurrentListingCart(page, options = {}) {
  const input = preparationOptions(options);
  if (input.error) return stopped(input.error);
  if (!page || !['object', 'function'].includes(typeof page)) return stopped('unsupported_page_interface');
  let record, deadline, clicked = false, ownsLock = false;
  const timeout = Symbol('prepare_timeout');
  const bounded = async operation => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw timeout;
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(operation),
        new Promise((_, reject) => { timer = setTimeout(() => reject(timeout), remaining); }),
      ]);
    } finally { clearTimeout(timer); }
  };
  try {
    if (typeof page.url !== 'function') return stopped('unsupported_page_interface');
    deadline = Date.now() + input.timeoutMs;
    const initialUrl = await bounded(() => page.url());
    const kind = urlKind(initialUrl, input.id);
    if (!kind) return stopped('current_url_not_allowed');
    record = attempts.get(page);
    if (record?.inFlight) return stopped('preparation_in_progress', 'in_flight');
    if (kind === 'listing' && record?.attempted) return stopped('add_attempt_already_recorded', 'add_outcome_requires_review');
    if (!record) { record = { inFlight: false, attempted: false }; attempts.set(page, record); }
    record.inFlight = true;
    ownsLock = true;
    if (kind === 'cart') {
      const cart = await bounded(() => inspectCurrentCartDom(page, { expectedListingId: input.id }));
      const matches = cart.observations.includes('expected_listing_link_observed');
      return stopped(matches ? 'cart_already_contains_expected_listing' : 'cart_requires_review',
        matches ? 'cart_observed' : 'cart_requires_review', cart);
    }
    if (!input.allowed) return stopped('add_to_cart_not_authorized');
    if (typeof page.getByRole !== 'function' || typeof page.evaluate !== 'function') {
      return stopped('unsupported_page_interface');
    }
    const visibleMatches = async locator => {
      const size = await bounded(() => locator.count());
      if (!Number.isInteger(size) || size < 0 || size > 2) return null;
      const visible = [];
      for (let index = 0; index < size; index++) {
        const item = locator.nth(index);
        if (await bounded(() => item.isVisible())) visible.push(item);
      }
      return visible;
    };
    const inCart = await visibleMatches(page.getByRole('button', { name: 'In Cart', exact: true }));
    if (!inCart || inCart.length > 1) return stopped('unsupported_listing_controls');
    if (inCart.length === 1) {
      const disabled = await bounded(() => inCart[0].isDisabled());
      return stopped(disabled === true ? 'listing_reports_in_cart_unverified' : 'unsupported_listing_controls',
        disabled === true ? 'already_in_cart_unverified' : 'not_started');
    }
    const links = await visibleMatches(page.getByRole('link', { name: 'Add to Cart', exact: true }));
    if (!links || links.length !== 1) return stopped('unsupported_add_link_shape');
    const link = links[0];
    const expectedHref = `/sell/cart/?add=${input.id}&ev=`;
    if (await bounded(() => link.getAttribute('href')) !== expectedHref) return stopped('unexpected_add_link');
    if (await bounded(() => page.url()) !== initialUrl || !await bounded(() => link.isVisible()) ||
        await bounded(() => link.getAttribute('href')) !== expectedHref) return stopped('listing_changed_before_add');
    if (await bounded(() => page.url()) !== initialUrl) return stopped('listing_changed_before_add');
    // Record BEFORE invoking click. Even an exception/timeout may mean it happened.
    if (deadline <= Date.now()) throw timeout;
    record.attempted = true;
    clicked = true;
    await bounded(() => link.click({ timeout: Math.max(1, deadline - Date.now()) }));
    let currentUrl = await bounded(() => page.url());
    while (currentUrl === initialUrl) {
      await bounded(() => new Promise(resolve => setTimeout(resolve, 25)));
      currentUrl = await bounded(() => page.url());
    }
    const origin = /^https:\/\/(?:www\.)?discogs\.com/u.exec(initialUrl)[0];
    if (currentUrl === `${origin}/sell/cart/?add=${input.id}&ev=`) {
      // Never replay or reload the mutating add URL. Exactly one normal browser
      // navigation to a fixed query-free cart is allowed, only after this click.
      if (typeof page.goto !== 'function') return stopped('add_outcome_unconfirmed', 'add_outcome_requires_review');
      const canonical = `${origin}/sell/cart/`;
      await bounded(() => page.goto(canonical, {
        timeout: Math.max(1, deadline - Date.now()),
      }));
      currentUrl = await bounded(() => page.url());
      if (currentUrl !== canonical) return stopped('add_outcome_unconfirmed', 'add_outcome_requires_review');
    }
    if (urlKind(currentUrl, input.id) !== 'cart') return stopped('add_outcome_unconfirmed', 'add_outcome_requires_review');
    const cart = await bounded(() => inspectCurrentCartDom(page, { expectedListingId: input.id }));
    const matches = cart.observations.includes('expected_listing_link_observed');
    return stopped(matches ? 'cart_observed_after_add' : 'add_outcome_unconfirmed',
      matches ? 'cart_observed' : 'add_outcome_requires_review', cart);
  } catch (error) {
    if (clicked) return stopped('add_outcome_uncertain', 'add_outcome_requires_review');
    return stopped(error === timeout || error?.name === 'TimeoutError' ? 'preparation_read_timeout' : 'preparation_read_failed');
  } finally {
    // Release only this call's read lock. The attempted guard is never cleared,
    // including after timeout/uncertainty; later calls may only inspect the cart.
    if (ownsLock) record.inFlight = false;
  }
}
