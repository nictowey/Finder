/** Explicit port for an already authorized supported local-browser tab. No setup or tool calls. */
import { collectCartDom } from './discogs_cart_inspector.mjs';
import { MAX_CART_PREPARE_TIMEOUT_MS } from './discogs_cart_prepare.mjs';
import { collectListingReleaseDom } from './discogs_rule_cart_gate.mjs';

// Repeated wrapping of the SAME tab object cannot reset preparation's page guard.
// This is not durable across module/process instances or alternate tab objects.
const pages = new WeakMap();

function boolean(value) {
  if (typeof value !== 'boolean') throw new TypeError('unsupported_browser_boolean');
  return value;
}

function timeout(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Reflect.ownKeys(options).length !== 1 || !Object.hasOwn(options, 'timeout')) {
    throw new TypeError('unsupported_browser_timeout');
  }
  const value = Object.getOwnPropertyDescriptor(options, 'timeout')?.value;
  if (!Number.isInteger(value) || value < 1 || value > MAX_CART_PREPARE_TIMEOUT_MS) {
    throw new TypeError('unsupported_browser_timeout');
  }
  return value;
}

/**
 * Maps only the verified API surface. Caller keeps and reuses the same tab object.
 * Local goto has no Playwright waitUntil guarantee. Preparation must separately
 * verify the current URL and DOM after it returns; this adapter invents no wait.
 */
export function createSupportedLocalCartPage(tab) {
  if (!tab || !['object', 'function'].includes(typeof tab)) throw new TypeError('unsupported_browser_tab');
  if (pages.has(tab)) return pages.get(tab);
  const playwright = tab.playwright;
  if (typeof tab.url !== 'function' || typeof tab.goto !== 'function' ||
      !playwright || typeof playwright.evaluate !== 'function' || typeof playwright.getByRole !== 'function') {
    throw new TypeError('unsupported_browser_tab');
  }
  const element = (locator, role) => Object.freeze({
    async isVisible() { return boolean(await locator.isVisible()); },
    async isDisabled() {
      if (role !== 'button') throw new TypeError('unsupported_browser_operation');
      return !boolean(await locator.isEnabled());
    },
    getAttribute(name) {
      if (role !== 'link' || name !== 'href') throw new TypeError('unsupported_browser_operation');
      return locator.getAttribute('href');
    },
    click(options) {
      if (role !== 'link') throw new TypeError('unsupported_browser_operation');
      return locator.click({ timeoutMs: timeout(options) });
    },
  });
  const page = Object.freeze({
    url() { return tab.url(); },
    evaluate(fn, arg) {
      if (fn !== collectCartDom && fn !== collectListingReleaseDom) throw new TypeError('unsupported_browser_evaluation');
      return playwright.evaluate(fn, arg);
    },
    getByRole(role, query) {
      if (!query || query.exact !== true || Reflect.ownKeys(query).length !== 2 ||
          !((role === 'button' && query.name === 'In Cart') || (role === 'link' && query.name === 'Add to Cart'))) {
        throw new TypeError('unsupported_browser_locator');
      }
      const locator = playwright.getByRole(role, { name: query.name, exact: true });
      return Object.freeze({
        count() { return locator.count(); },
        nth(index) {
          if (!Number.isInteger(index) || index < 0 || index > 1) throw new TypeError('unsupported_browser_locator');
          return element(locator.nth(index), role);
        },
      });
    },
    goto(url, options) {
      timeout(options);
      if (typeof url !== 'string' || /\s/u.test(url) || !/^https:\/\/(?:www\.)?discogs\.com\/sell\/cart\/$/u.test(url)) {
        throw new TypeError('unsupported_browser_navigation');
      }
      return tab.goto(url);
    },
  });
  pages.set(tab, page);
  return page;
}
