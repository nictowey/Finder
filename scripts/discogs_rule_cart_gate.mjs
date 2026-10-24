/** Saved-release identity gate only. No transport, login, session setup or purchasing. */
import { prepareCurrentListingCart, CART_PREPARE_TIMEOUT_MS, MAX_CART_PREPARE_TIMEOUT_MS } from './discogs_cart_prepare.mjs';

const guardedPages = new WeakMap();
const DEADLINE = Symbol('identity_gate_deadline');
const ID = /^[1-9][0-9]{0,15}$/u;
const validId = value => typeof value === 'string' && ID.test(value) && !/\s/u.test(value) && Number.isSafeInteger(Number(value));
const revision = value => Number.isSafeInteger(value) && value >= 0;

function result(code, context = null, preparation = null) {
  return {
    ...(preparation ?? { status: 'blocked', code, preparation_state: 'blocked_before_add', cart_inspection: null }),
    qualification_scope: 'saved_release_identity_only',
    identity_preflight_passed: context?.preflightPassed === true,
    identity_rechecked_before_click: context?.rechecked === true,
    purchase_eligibility: 'unsupported', stop_required: true, purchase_allowed: false,
  };
}

function optionsValue(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Reflect.ownKeys(options).some(key => !['expectedReleaseId', 'expectedListingId', 'expectedRevision', 'allowAddToCart', 'timeoutMs'].includes(key))) return null;
  const own = key => Object.getOwnPropertyDescriptor(options, key)?.value;
  const release = own('expectedReleaseId'), listing = own('expectedListingId'), savedRevision = own('expectedRevision');
  if (!validId(release) || !validId(listing) || !revision(savedRevision)) return null;
  const permission = Object.getOwnPropertyDescriptor(options, 'allowAddToCart');
  const timeout = Object.getOwnPropertyDescriptor(options, 'timeoutMs');
  if (permission && typeof permission.value !== 'boolean') return null;
  if (timeout && (!Number.isInteger(timeout.value) || timeout.value < 5_000 || timeout.value > MAX_CART_PREPARE_TIMEOUT_MS)) return null;
  return { release, listing, savedRevision, allowed: permission?.value === true,
    timeoutMs: timeout?.value ?? CART_PREPARE_TIMEOUT_MS, customTimeout: !!timeout };
}

function listingUrl(url, listing) {
  return typeof url === 'string' && url.length <= 120 && !/[\s\x00-\x1f\x7f]/u.test(url) &&
    /^https:\/\/(?:www\.)?discogs\.com\/(?:shop|sell)\/item\/[1-9][0-9]{0,15}$/u.test(url) && url.endsWith(`/item/${listing}`);
}

/** Serializable, read-only collector using only the independently observed identity selectors. */
export function collectListingReleaseDom(expected) {
  const fail = code => ({ schema: 'discogs_listing_release_dom_v1', code, purchase_allowed: false });
  const validId = value => typeof value === 'string' && /^[1-9][0-9]{0,15}$/u.test(value) &&
    !/\s/u.test(value) && Number.isSafeInteger(Number(value));
  if (!expected || !validId(expected.expectedReleaseId) || !validId(expected.expectedListingId)) return fail('invalid_expected_identity');
  if (typeof document === 'undefined' || typeof location === 'undefined') return fail('dom_unavailable');
  const current = location.href;
  const match = typeof current === 'string' && current.length <= 120 && !/[\s\x00-\x1f\x7f]/u.test(current) &&
    /^(https:\/\/(?:www\.)?discogs\.com)\/(?:shop|sell)\/item\/([1-9][0-9]{0,15})$/u.exec(current);
  if (!match || match[2] !== expected.expectedListingId) return fail('current_listing_not_allowed');
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
    const releaseId = href => {
      if (typeof href !== 'string' || href.length > 100 || /[\s\x00-\x1f\x7f]/u.test(href)) return null;
      const parsed = /^(https:\/\/(?:www\.)?discogs\.com)\/release\/([1-9][0-9]{0,15})$/u.exec(href);
      return parsed && parsed[1] === match[1] && validId(parsed[2]) ? parsed[2] : null;
    };
    const mains = [...document.querySelectorAll('main')].filter(visible);
    const base = { schema: 'discogs_listing_release_dom_v1', code: 'observed', purchase_allowed: false,
      current_url: current, page_listing_id: match[2], main_count: Math.min(mains.length, 2) };
    if (mains.length !== 1) return base;
    const headings = [...mains[0].querySelectorAll("a.hifi-heading-link[href*='/release/']")].filter(visible);
    const anchors = mains[0].querySelectorAll('a');
    if (anchors.length > 500) return fail('unsupported_identity_shape');
    const details = [...anchors].filter(anchor => visible(anchor) && anchor.innerText === 'Release Details');
    const canonical = document.querySelectorAll('link[rel="canonical"]');
    const og = document.querySelectorAll('meta[property="og:url"]');
    return { ...base, heading_count: Math.min(headings.length, 2), details_count: Math.min(details.length, 2),
      canonical_count: Math.min(canonical.length, 2), og_count: Math.min(og.length, 2),
      heading_release_id: headings.length === 1 ? releaseId(headings[0].getAttribute('href')) : null,
      details_release_id: details.length === 1 ? releaseId(details[0].getAttribute('href')) : null,
      canonical_matches_current: canonical.length === 1 && canonical[0].getAttribute('href') === current,
      og_matches_current: og.length === 1 && og[0].getAttribute('content') === current,
    };
  } catch { return fail('identity_dom_read_failed'); }
}

function snapshotCode(snapshot, context) {
  if (!snapshot || !revision(snapshot.revision) || !revision(snapshot.control_revision) ||
      !Array.isArray(snapshot.targets) || snapshot.targets.length > 1_000 ||
      !Array.isArray(snapshot.owned) || snapshot.owned.length > 10_000 ||
      !snapshot.owned.every(id => Number.isSafeInteger(id) && id > 0) || typeof snapshot.kill_switch !== 'boolean') return 'saved_state_unavailable';
  if (snapshot.revision !== context.input.savedRevision) return 'saved_revision_changed';
  if (snapshot.kill_switch !== false) return 'kill_switch_active';
  if (context.controlRevision !== undefined && snapshot.control_revision !== context.controlRevision) return 'control_revision_changed';
  const targets = snapshot.targets.filter(target => target && target.release_id === Number(context.input.release));
  if (targets.length !== 1) return 'release_not_uniquely_saved';
  if (targets[0].enabled !== true) return 'saved_release_paused';
  if (snapshot.owned.includes(Number(context.input.release))) return 'saved_release_owned';
  if (context.controlRevision === undefined) context.controlRevision = snapshot.control_revision;
  return null;
}

async function checkIdentity(context, finalCheck) {
  const { input, page, port } = context;
  let code = snapshotCode(await context.read(() => port.readSnapshot()), context);
  if (code) return code;
  const response = await context.read(() => port.readListing({ release_id: Number(input.release), listing: input.listing }));
  if (!response?.listing || response.listing.listing_id !== input.listing || response.listing.release_id !== Number(input.release)) return 'api_listing_release_mismatch';
  if (response.listing.available !== true) return 'listing_availability_unverified';
  if (response.evaluation?.rules_revision !== input.savedRevision) return 'listing_rules_revision_changed';
  const dom = await context.read(() => page.evaluate(collectListingReleaseDom, {
    expectedReleaseId: input.release, expectedListingId: input.listing,
  }));
  if (!dom || dom.schema !== 'discogs_listing_release_dom_v1' || dom.code !== 'observed' || dom.purchase_allowed !== false ||
      dom.current_url !== context.initialUrl || dom.page_listing_id !== input.listing || dom.main_count !== 1 ||
      dom.heading_count !== 1 || dom.details_count !== 1 || dom.canonical_count !== 1 || dom.og_count !== 1 ||
      dom.heading_release_id !== input.release || dom.details_release_id !== input.release ||
      dom.canonical_matches_current !== true || dom.og_matches_current !== true) return 'page_release_identity_unverified';
  // Check controls once more after remote listing/DOM reads. Still not an atomic transaction.
  if (finalCheck) {
    code = snapshotCode(await context.read(() => port.readSnapshot()), context);
    if (code) return code;
  }
  return await context.read(() => page.url()) === context.initialUrl ? null : 'listing_navigation_changed';
}

function makeGuardedPage(record, page) {
  const context = () => {
    if (!record.context?.active || Date.now() >= record.context.deadline) throw DEADLINE;
    return record.context;
  };
  return Object.freeze({
    url() { const c = context(); return c.read(() => page.url()); },
    evaluate(fn, arg) { const c = context(); return c.read(() => page.evaluate(fn, arg)); },
    goto(url, options) {
      const c = context();
      return c.read(() => page.goto(url, { timeout: Math.min(options.timeout, Math.max(1, c.deadline - Date.now())) }));
    },
    getByRole(role, query) {
      context(); const locator = page.getByRole(role, query);
      return {
        count() { const c = context(); return c.read(() => locator.count()); },
        nth(index) {
          context(); const item = locator.nth(index); let observedHref;
          return {
            isVisible() { const c = context(); return c.read(() => item.isVisible()); },
            isDisabled() { const c = context(); return c.read(() => item.isDisabled()); },
            async getAttribute(name) {
              const c = context(); const value = await c.read(() => item.getAttribute(name));
              if (name === 'href') observedHref = value;
              return value;
            },
            async click(options) {
              const c = context(); const href = observedHref;
              try {
                if (role !== 'link' || query.name !== 'Add to Cart' || query.exact !== true) c.gateFailure = 'unsupported_guarded_action';
                else c.gateFailure = await checkIdentity(c, true);
                if (!c.gateFailure && (await c.read(() => item.getAttribute('href')) !== href ||
                    await c.read(() => item.isVisible()) !== true || await c.read(() => page.url()) !== c.initialUrl)) {
                  c.gateFailure = 'listing_changed_at_click_gate';
                }
                if (c.gateFailure) throw new Error('identity_gate_rejected');
                context(); c.rechecked = true; record.attempted = true; c.mutationStarted = true;
                return await c.read(() => item.click({ timeout: Math.min(options.timeout, Math.max(1, c.deadline - Date.now())) }));
              } catch (error) {
                if (!c.mutationStarted && !c.gateFailure) c.gateFailure = error === DEADLINE ? 'identity_recheck_timed_out' : 'identity_recheck_read_failed';
                throw error;
              }
            },
          };
        },
      };
    },
  });
}

/** Explicit caller-supplied authenticated reads; no fetch, credentials or server transport. */
export async function prepareRuleQualifiedListingCart(page, authenticatedDataPort, options = {}) {
  let input;
  try { input = optionsValue(options); } catch { return result('invalid_identity_gate_options'); }
  if (!input) return result('invalid_identity_gate_options');
  if (!page || !['object', 'function'].includes(typeof page) || typeof page.url !== 'function' ||
      typeof page.evaluate !== 'function' || !authenticatedDataPort || typeof authenticatedDataPort.readSnapshot !== 'function' ||
      typeof authenticatedDataPort.readListing !== 'function') return result('authenticated_read_port_required');
  let record = guardedPages.get(page);
  if (record?.busy) return result('identity_preparation_in_progress');
  if (record?.port !== undefined && record.port !== authenticatedDataPort) return result('authenticated_data_port_changed');
  if (record?.attempted) return result('add_attempt_already_recorded');
  if (!record) {
    record = { port: authenticatedDataPort, busy: false, attempted: false, context: null };
    record.wrapper = makeGuardedPage(record, page); guardedPages.set(page, record);
  }
  const c = { input, page, port: authenticatedDataPort, active: true,
    deadline: Date.now() + input.timeoutMs, initialUrl: null, preflightPassed: false, rechecked: false, mutationStarted: false, gateFailure: null };
  c.read = async operation => {
    if (!c.active || Date.now() >= c.deadline) throw DEADLINE;
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(() => { if (!c.active || Date.now() >= c.deadline) throw DEADLINE; return operation(); }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(DEADLINE), Math.max(1, c.deadline - Date.now())); }),
      ]);
    } finally { clearTimeout(timer); }
  };
  record.busy = true; record.context = c;
  try {
    c.initialUrl = await c.read(() => page.url());
    if (!listingUrl(c.initialUrl, input.listing)) return result('exact_listing_page_required', c);
    const code = await checkIdentity(c, false);
    if (code) return result(code, c);
    c.preflightPassed = true;
    const lowOptions = { expectedListingId: input.listing, allowAddToCart: input.allowed };
    if (input.customTimeout) lowOptions.timeoutMs = input.timeoutMs;
    const preparation = await prepareCurrentListingCart(record.wrapper, lowOptions);
    if (c.gateFailure && !c.mutationStarted) return result(c.gateFailure, c);
    return result(preparation.code, c, preparation);
  } catch (error) {
    return result(c.mutationStarted ? 'add_outcome_uncertain' : error === DEADLINE ? 'identity_gate_timed_out' : 'identity_read_failed', c);
  } finally {
    c.active = false; record.busy = false;
  }
}
