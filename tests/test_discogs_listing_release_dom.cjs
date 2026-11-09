'use strict';
// Synthetic listing DOM only: no browser connection, account, live listing or network.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');

let collectListingReleaseDom;
before(async () => {
  ({ collectListingReleaseDom } = await import('../scripts/discogs_rule_cart_gate.mjs'));
});

const LISTING_ID = '123456';
const RELEASE_ID = '234567';
const OTHER_RELEASE_ID = '345678';
const LISTING_URL = `https://www.discogs.com/shop/item/${LISTING_ID}`;
const RELEASE_URL = `https://www.discogs.com/release/${RELEASE_ID}`;
const EXPECTED = Object.freeze({ expectedListingId: LISTING_ID, expectedReleaseId: RELEASE_ID });
const HEADING = `<a class="hifi-heading-link" href="${RELEASE_URL}">Synthetic artist - Synthetic album</a>`;
const DETAILS = `<a href="${RELEASE_URL}">Release Details</a>`;
const MAIN = `<main>${HEADING}${DETAILS}</main>`;
const HTML = `<!doctype html><html><head>
  <link rel="canonical" href="${LISTING_URL}">
  <meta property="og:url" content="${LISTING_URL}">
  </head><body>${MAIN}</body></html>`;

function listingDom(html = HTML, url = LISTING_URL) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  const { window } = dom;
  const forbiddenCalls = [];
  const forbidden = label => () => {
    forbiddenCalls.push(label);
    throw new Error(`Forbidden ${label}`);
  };
  // jsdom has no layout or rendered innerText. Production must use actual browser values.
  window.Element.prototype.getClientRects = function () {
    if (this.hasAttribute('data-no-layout')) return [];
    if (this.hasAttribute('data-zero-width')) return [{ width: 0, height: 20 }];
    if (this.hasAttribute('data-zero-height')) return [{ width: 100, height: 0 }];
    return [{ width: 100, height: 20 }];
  };
  Object.defineProperty(window.HTMLElement.prototype, 'innerText', {
    configurable: true,
    get() { return this.textContent; },
    set: forbidden('innerText setter'),
  });
  for (const method of ['click', 'focus', 'blur']) {
    window.HTMLElement.prototype[method] = forbidden(method);
  }
  for (const method of ['submit', 'requestSubmit', 'reset']) {
    window.HTMLFormElement.prototype[method] = forbidden(method);
  }
  window.fetch = forbidden('fetch');
  window.XMLHttpRequest = forbidden('XMLHttpRequest');
  window.WebSocket = forbidden('WebSocket');
  window.navigator.sendBeacon = forbidden('sendBeacon');
  window.open = forbidden('window.open');
  const observer = new window.MutationObserver(() => {});
  observer.observe(window.document, { subtree: true, attributes: true, childList: true, characterData: true });
  return {
    dom,
    forbidden,
    collect(expected = EXPECTED, rawUrl) {
      // Ignore fixture construction. Nothing may change during the actual collection.
      observer.takeRecords();
      const beforeHtml = window.document.documentElement.outerHTML;
      const beforeUrl = window.location.href;
      const source = collectListingReleaseDom.toString();
      // A separate realm proves this collector does not close over module helpers.
      // Raw location fixtures prevent the URL implementation from normalizing malformed input.
      const result = rawUrl === undefined
        ? window.eval(`(${source})`)(expected)
        : window.eval(`(function (location, expected) { return (${source})(expected); })`)({ href: rawUrl }, expected);
      assert.deepEqual(observer.takeRecords(), [], 'Collector must not mutate the DOM');
      assert.equal(window.document.documentElement.outerHTML, beforeHtml);
      assert.equal(window.location.href, beforeUrl, 'Collector must not navigate');
      assert.deepEqual(forbiddenCalls, [], 'Collector must not attempt an action, setter or network request');
      return JSON.parse(JSON.stringify(result));
    },
    close() { observer.disconnect(); window.close(); },
  };
}

function observe(html = HTML, expected = EXPECTED, rawUrl) {
  const fixture = listingDom(html);
  try { return fixture.collect(expected, rawUrl); }
  finally { fixture.close(); }
}

function assertObservation(result) {
  assert.equal(result.schema, 'discogs_listing_release_dom_v1');
  assert.equal(result.code, 'observed');
  assert.equal(result.purchase_allowed, false);
}

function assertFailure(result, code) {
  assert.deepEqual(result, { schema: 'discogs_listing_release_dom_v1', code, purchase_allowed: false });
}

test('serialized collector returns only the exact observed listing and release identity fields', () => {
  assert.deepEqual(observe(), {
    schema: 'discogs_listing_release_dom_v1', code: 'observed', purchase_allowed: false,
    current_url: LISTING_URL, page_listing_id: LISTING_ID, main_count: 1,
    heading_count: 1, details_count: 1, canonical_count: 1, og_count: 1,
    heading_release_id: RELEASE_ID, details_release_id: RELEASE_ID,
    canonical_matches_current: true, og_matches_current: true,
  });
});

test('unrelated release links inside or outside main cannot supply or confuse the identity selectors', () => {
  const other = `https://www.discogs.com/release/${OTHER_RELEASE_ID}`;
  const html = HTML.replace('</main>', `<a href="${other}">Recommended record</a>
    <a href="${other}" aria-label="Release Details">Other versions</a></main>`)
    .replace('</body>', `<nav><a class="hifi-heading-link" href="${other}">Other album</a>
    <a href="${other}">Release Details</a></nav></body>`);
  assert.deepEqual(observe(html), observe());
});

test('same album text with another release preserves the mismatching observed IDs, never the expected ID', () => {
  const otherUrl = `https://www.discogs.com/release/${OTHER_RELEASE_ID}`;
  const result = observe(HTML.replaceAll(RELEASE_URL, otherUrl));
  assertObservation(result);
  assert.equal(result.heading_release_id, OTHER_RELEASE_ID);
  assert.equal(result.details_release_id, OTHER_RELEASE_ID);
  assert.notEqual(result.heading_release_id, EXPECTED.expectedReleaseId);
  assert.equal(result.heading_count, 1);
  assert.equal(result.details_count, 1);
  // Qualification and mismatch rejection belong to the gate, not this observation collector.
  assert.equal(Object.hasOwn(result, 'identity_preflight_passed'), false);
});

test('disagreement between heading and details remains visible to the independent identity gate', () => {
  for (const selected of [HEADING, DETAILS]) {
    const result = observe(HTML.replace(selected, selected.replace(RELEASE_ID, OTHER_RELEASE_ID)));
    assertObservation(result);
    assert.equal(result.heading_release_id, selected === HEADING ? OTHER_RELEASE_ID : RELEASE_ID);
    assert.equal(result.details_release_id, selected === DETAILS ? OTHER_RELEASE_ID : RELEASE_ID);
    assert.notEqual(result.heading_release_id, result.details_release_id);
  }
});

test('master references and missing release hrefs never become release identities', () => {
  for (const replacement of [`https://www.discogs.com/master/${RELEASE_ID}`, '', '/master/234567']) {
    const result = observe(HTML.replaceAll(RELEASE_URL, replacement));
    assertObservation(result);
    assert.equal(result.heading_count, 0);
    assert.equal(result.details_count, 1);
    assert.equal(result.heading_release_id, null);
    assert.equal(result.details_release_id, null);
  }
  const result = observe(HTML.replaceAll(` href="${RELEASE_URL}"`, ''));
  assert.equal(result.heading_count, 0);
  assert.equal(result.heading_release_id, null);
  assert.equal(result.details_count, 1);
  assert.equal(result.details_release_id, null);
});

test('release hrefs are literal absolute same-origin URLs, with no normalization or URL repair', () => {
  const invalid = [
    `/release/${RELEASE_ID}`, `//www.discogs.com/release/${RELEASE_ID}`,
    `https://discogs.com/release/${RELEASE_ID}`, `http://www.discogs.com/release/${RELEASE_ID}`,
    `https://evil.invalid/release/${RELEASE_ID}`, `https://www.discogs.com.evil.invalid/release/${RELEASE_ID}`,
    `https://api.discogs.com/release/${RELEASE_ID}`, `https://WWW.DISCOGS.COM/release/${RELEASE_ID}`,
    `https://www.discogs.com:443/release/${RELEASE_ID}`, `https://www.discogs.com:8443/release/${RELEASE_ID}`,
    `https://user@www.discogs.com/release/${RELEASE_ID}`, `https://user:secret@www.discogs.com/release/${RELEASE_ID}`,
    `${RELEASE_URL}?`, `${RELEASE_URL}?tracking=1`, `${RELEASE_URL}#`, `${RELEASE_URL}#details`,
    `${RELEASE_URL}/`, `${RELEASE_URL}-Synthetic-Album`, `${RELEASE_URL}/../${RELEASE_ID}`,
    `https://www.discogs.com/other/../release/${RELEASE_ID}`,
    `https://www.discogs.com/%72elease/${RELEASE_ID}`, `https://www.discogs.com/release/%32${RELEASE_ID.slice(1)}`,
    `https://www.discogs.com/release/${RELEASE_ID}%2f`, `https://www.discogs.com/release/0${RELEASE_ID}`,
    'https://www.discogs.com/release/0', 'https://www.discogs.com/release/-1',
    'https://www.discogs.com/release/9007199254740992', 'https://www.discogs.com/release/12345678901234567',
    ` ${RELEASE_URL}`, `${RELEASE_URL} `, `${RELEASE_URL}\n`, `${RELEASE_URL}\t`,
    `https:\\www.discogs.com\\release\\${RELEASE_ID}`, `${RELEASE_URL}${'x'.repeat(101)}`,
  ];
  for (const href of invalid) {
    const fixture = listingDom();
    try {
      const links = fixture.dom.window.document.querySelectorAll('main a');
      for (const link of links) link.setAttribute('href', href);
      const result = fixture.collect();
      assertObservation(result);
      assert.equal(result.heading_release_id, null, href);
      assert.equal(result.details_release_id, null, href);
    } finally { fixture.close(); }
  }
});

test('valid IDs are preserved through the maximum safe integer without numeric coercion', () => {
  for (const release of ['1', '9007199254740991']) {
    const result = observe(HTML.replaceAll(RELEASE_URL, `https://www.discogs.com/release/${release}`),
      { ...EXPECTED, expectedReleaseId: release });
    assert.equal(result.heading_release_id, release);
    assert.equal(result.details_release_id, release);
  }
});

test('both supported listing paths require matching exact canonical and og URLs', () => {
  for (const route of ['shop', 'sell']) {
    const url = `https://www.discogs.com/${route}/item/${LISTING_ID}`;
    const fixture = listingDom(HTML.replaceAll(LISTING_URL, url), url);
    try {
      const result = fixture.collect();
      assertObservation(result);
      assert.equal(result.current_url, url);
      assert.equal(result.page_listing_id, LISTING_ID);
      assert.equal(result.canonical_matches_current, true);
      assert.equal(result.og_matches_current, true);
    } finally { fixture.close(); }
  }
});

test('both literal Discogs origins are supported but release links must match the exact current origin', () => {
  for (const host of ['www.discogs.com', 'discogs.com']) {
    const otherHost = host === 'www.discogs.com' ? 'discogs.com' : 'www.discogs.com';
    const url = `https://${host}/shop/item/${LISTING_ID}`;
    const fixture = listingDom(HTML.replaceAll('www.discogs.com', host), url);
    try {
      const valid = fixture.collect();
      assertObservation(valid);
      assert.equal(valid.heading_release_id, RELEASE_ID);
      assert.equal(valid.details_release_id, RELEASE_ID);
      assert.equal(valid.canonical_matches_current, true);
      assert.equal(valid.og_matches_current, true);
      for (const link of fixture.dom.window.document.querySelectorAll('main a')) {
        link.setAttribute('href', `https://${otherHost}/release/${RELEASE_ID}`);
      }
      const mismatched = fixture.collect();
      assertObservation(mismatched);
      assert.equal(mismatched.heading_count, 1);
      assert.equal(mismatched.details_count, 1);
      assert.equal(mismatched.heading_release_id, null);
      assert.equal(mismatched.details_release_id, null);
    } finally { fixture.close(); }
  }
});

test('invalid or different current listing URLs stop before any DOM selector is read', () => {
  const invalid = [
    null, false, 123456, {}, '',
    `https://www.discogs.com/shop/item/${OTHER_RELEASE_ID}`, RELEASE_URL,
    `http://www.discogs.com/shop/item/${LISTING_ID}`, `https://evil.invalid/shop/item/${LISTING_ID}`,
    `https://www.discogs.com.evil.invalid/shop/item/${LISTING_ID}`,
    `https://www.discogs.com:443/shop/item/${LISTING_ID}`, `https://www.discogs.com:8443/shop/item/${LISTING_ID}`,
    `https://user@www.discogs.com/shop/item/${LISTING_ID}`, `https://user:secret@www.discogs.com/shop/item/${LISTING_ID}`,
    `${LISTING_URL}?`, `${LISTING_URL}?x=1`, `${LISTING_URL}#`, `${LISTING_URL}#fragment`, `${LISTING_URL}/`,
    `https://www.discogs.com/shop/../shop/item/${LISTING_ID}`,
    `https://www.discogs.com/%73hop/item/${LISTING_ID}`, `https://www.discogs.com/shop/item/%31${LISTING_ID.slice(1)}`,
    `https://www.discogs.com/shop/item/0${LISTING_ID}`, 'https://www.discogs.com/shop/item/0',
    `//www.discogs.com/shop/item/${LISTING_ID}`, `https://WWW.DISCOGS.COM/shop/item/${LISTING_ID}`,
    ` ${LISTING_URL}`, `${LISTING_URL}\n`, `${LISTING_URL}\t`, `${LISTING_URL}\u007f`,
    `${LISTING_URL}${'1'.repeat(121)}`,
  ];
  const fixture = listingDom();
  try {
    fixture.dom.window.document.querySelectorAll = fixture.forbidden('DOM read before valid current URL');
    for (const url of invalid) assertFailure(fixture.collect(EXPECTED, url), 'current_listing_not_allowed');
  } finally { fixture.close(); }
});

test('invalid expected IDs fail before reading the DOM and cannot be guessed from the page', () => {
  const invalidIds = [undefined, null, 123456, 0, false, {}, [], '', '0', '-1', '01', '1.0', '1e3',
    ' 123456', '123456 ', '123456\n', '9007199254740992', '12345678901234567'];
  const fixture = listingDom();
  try {
    fixture.dom.window.document.querySelectorAll = fixture.forbidden('DOM read before valid expected IDs');
    for (const input of [null, false, {}, [], ...invalidIds.flatMap(id => [
      { ...EXPECTED, expectedReleaseId: id }, { ...EXPECTED, expectedListingId: id },
    ])]) assertFailure(fixture.collect(input), 'invalid_expected_identity');
  } finally { fixture.close(); }
});

test('missing document or location returns a bounded unavailable observation', () => {
  const source = collectListingReleaseDom.toString();
  for (const [document, location] of [[undefined, { href: LISTING_URL }], [{}, undefined]]) {
    const result = new Function('document', 'location', 'expected', `return (${source})(expected);`)(document, location, EXPECTED);
    assertFailure(result, 'dom_unavailable');
  }
});

test('zero or multiple visible mains are ambiguous and counts are capped at two', () => {
  for (const [replacement, count] of [['', 0], [MAIN + MAIN, 2], [MAIN.repeat(5), 2]]) {
    const result = observe(HTML.replace(MAIN, replacement));
    assertObservation(result);
    assert.equal(result.main_count, count);
    assert.equal(Object.hasOwn(result, 'heading_release_id'), false);
    assert.equal(Object.hasOwn(result, 'details_release_id'), false);
  }
});

test('hidden main elements never count as the visible identity scope', () => {
  for (const attribute of ['hidden', 'aria-hidden="true"', 'style="display:none"',
    'style="visibility:hidden"', 'style="visibility:collapse"', 'style="opacity:0"',
    'style="content-visibility:hidden"', 'data-no-layout', 'data-zero-width', 'data-zero-height']) {
    const hiddenMain = MAIN.replace('<main>', `<main ${attribute}>`);
    assert.equal(observe(HTML.replace(MAIN, hiddenMain)).main_count, 0, attribute);
    assert.deepEqual(observe(HTML.replace(MAIN, MAIN + hiddenMain)), observe(), attribute);
  }
});

test('hidden ancestors also suppress main, heading and details visibility', () => {
  for (const attribute of ['hidden', 'aria-hidden="true"', 'style="display:none"',
    'style="visibility:hidden"', 'style="visibility:collapse"', 'style="opacity:0"',
    'style="content-visibility:hidden"']) {
    assert.equal(observe(HTML.replace(MAIN, `<section ${attribute}>${MAIN}</section>`)).main_count, 0, attribute);
    const result = observe(HTML.replace(HEADING + DETAILS, `<section ${attribute}>${HEADING}${DETAILS}</section>`));
    assert.equal(result.main_count, 1, attribute);
    assert.equal(result.heading_count, 0, attribute);
    assert.equal(result.details_count, 0, attribute);
    assert.equal(result.heading_release_id, null);
    assert.equal(result.details_release_id, null);
  }
});

test('identity links must be visible, with hidden duplicates excluded rather than trusted', () => {
  for (const selected of [HEADING, DETAILS]) {
    const key = selected === HEADING ? 'heading' : 'details';
    for (const attribute of ['hidden', 'aria-hidden="true"', 'style="display:none"',
      'style="visibility:hidden"', 'style="visibility:collapse"', 'style="opacity:0"',
      'style="content-visibility:hidden"', 'data-no-layout', 'data-zero-width', 'data-zero-height']) {
      const hiddenLink = selected.replace('<a ', `<a ${attribute} `);
      const result = observe(HTML.replace(selected, hiddenLink));
      assert.equal(result[`${key}_count`], 0, attribute);
      assert.equal(result[`${key}_release_id`], null, attribute);
      assert.deepEqual(observe(HTML.replace(selected, selected + hiddenLink)), observe(), attribute);
    }
  }
});

test('multiple visible headings or Release Details anchors are ambiguous even when their URLs agree', () => {
  for (const selected of [HEADING, DETAILS]) {
    const key = selected === HEADING ? 'heading' : 'details';
    for (const count of [2, 3]) {
      const result = observe(HTML.replace(selected, selected.repeat(count)));
      assertObservation(result);
      assert.equal(result[`${key}_count`], 2);
      assert.equal(result[`${key}_release_id`], null);
    }
  }
});

test('heading requires the exact observed anchor class and release-containing raw href', () => {
  for (const replacement of [HEADING.replace('hifi-heading-link', 'other-heading-link'),
    HEADING.replace('<a ', '<span ').replace('</a>', '</span>'),
    HEADING.replace(RELEASE_URL, 'https://www.discogs.com/master/234567'), '']) {
    const result = observe(HTML.replace(HEADING, replacement));
    assert.equal(result.heading_count, 0);
    assert.equal(result.heading_release_id, null);
    assert.equal(result.details_release_id, RELEASE_ID);
  }
});

test('details identification uses exact rendered innerText, not textContent, title or accessibility labels', () => {
  for (const text of ['Release details', 'RELEASE DETAILS', ' Release Details', 'Release Details ',
    'Release Details\n', 'Release\nDetails', 'Release Details extra', 'View Release Details', '']) {
    const fixture = listingDom();
    try {
      const details = fixture.dom.window.document.querySelector('main a:last-child');
      Object.defineProperty(details, 'innerText', { get() { return text; } });
      const result = fixture.collect();
      assert.equal(details.textContent, 'Release Details');
      assert.equal(result.details_count, 0, JSON.stringify(text));
      assert.equal(result.details_release_id, null);
    } finally { fixture.close(); }
  }
  const fixture = listingDom(HTML.replace(DETAILS, `<a href="${RELEASE_URL}" title="Other title">Other source text</a>`));
  try {
    Object.defineProperty(fixture.dom.window.document.querySelector('main a:last-child'), 'innerText',
      { get() { return 'Release Details'; } });
    assert.equal(fixture.collect().details_release_id, RELEASE_ID);
  } finally { fixture.close(); }
});

test('canonical and og metadata must each occur once, including duplicates hidden outside main', () => {
  for (const [markup, countKey, matchKey] of [
    [`<link rel="canonical" href="${LISTING_URL}">`, 'canonical_count', 'canonical_matches_current'],
    [`<meta property="og:url" content="${LISTING_URL}">`, 'og_count', 'og_matches_current'],
  ]) {
    for (const count of [0, 2, 4]) {
      const result = observe(HTML.replace(markup, markup.repeat(count)));
      assertObservation(result);
      assert.equal(result[countKey], Math.min(count, 2));
      assert.equal(result[matchKey], false);
    }
    const result = observe(HTML.replace('</body>', `<section hidden>${markup}</section></body>`));
    assert.equal(result[countKey], 2);
    assert.equal(result[matchKey], false);
  }
});

test('metadata is compared byte-for-byte with the current listing URL, not normalized or substituted', () => {
  const alternatives = ['', `/shop/item/${LISTING_ID}`, `https://discogs.com/shop/item/${LISTING_ID}`,
    `https://www.discogs.com/sell/item/${LISTING_ID}`, `https://www.discogs.com/shop/item/${OTHER_RELEASE_ID}`,
    `${LISTING_URL}?`, `${LISTING_URL}#`, `${LISTING_URL}/`, `${LISTING_URL}\n`, ` ${LISTING_URL}`,
    `https://www.discogs.com:443/shop/item/${LISTING_ID}`,
    `https://user@www.discogs.com/shop/item/${LISTING_ID}`, `https://www.discogs.com/%73hop/item/${LISTING_ID}`];
  for (const [selector, attribute, key] of [
    ['link[rel="canonical"]', 'href', 'canonical_matches_current'],
    ['meta[property="og:url"]', 'content', 'og_matches_current'],
  ]) {
    const fixture = listingDom();
    try {
      const element = fixture.dom.window.document.querySelector(selector);
      for (const value of alternatives) {
        element.setAttribute(attribute, value);
        const result = fixture.collect();
        assertObservation(result);
        assert.equal(result[key], false, value);
      }
      element.removeAttribute(attribute);
      assert.equal(fixture.collect()[key], false);
    } finally { fixture.close(); }
  }
});

test('the supported main anchor count is bounded and oversized DOM observations contain no page data', () => {
  const filler = '<a href="/artist/1">Synthetic unrelated artist</a>';
  const bounded = observe(HTML.replace('</main>', `${filler.repeat(498)}</main>`));
  assertObservation(bounded);
  assert.equal(bounded.heading_release_id, RELEASE_ID);
  assert.equal(bounded.details_release_id, RELEASE_ID);
  assertFailure(observe(HTML.replace('</main>', `${filler.repeat(499)}</main>`)), 'unsupported_identity_shape');
});

test('DOM read failures return sanitized failure codes without exception text or partial page contents', () => {
  for (const operation of ['query', 'visibility', 'rects', 'innerText']) {
    const fixture = listingDom();
    try {
      const throwPrivate = () => { throw new Error('SECRET_ACCOUNT_ADDRESS_AND_SESSION'); };
      if (operation === 'query') fixture.dom.window.document.querySelectorAll = throwPrivate;
      if (operation === 'visibility') fixture.dom.window.getComputedStyle = throwPrivate;
      if (operation === 'rects') fixture.dom.window.Element.prototype.getClientRects = throwPrivate;
      if (operation === 'innerText') Object.defineProperty(fixture.dom.window.HTMLElement.prototype, 'innerText', { get: throwPrivate });
      assertFailure(fixture.collect(), 'identity_dom_read_failed');
    } finally { fixture.close(); }
  }
});

test('collection never reads private account or form values, invokes setters, or leaks unrelated text', () => {
  const privateHtml = HTML.replace('</main>', `
    <aside>SECRET_ACCOUNT_ADDRESS Synthetic seller name</aside>
    <form><input name="password" value="SECRET_PASSWORD">
      <input type="checkbox" checked name="agreement">
      <select name="payment"><option selected value="SECRET_PAYMENT">SECRET_CARD</option></select>
      <button type="submit">Place order and pay now</button></form></main>`);
  const fixture = listingDom(privateHtml);
  try {
    const { window } = fixture.dom;
    for (const [object, key] of [[window.document, 'cookie'], [window, 'localStorage'], [window, 'sessionStorage'],
      [window.document.body, 'innerText'], [window.document.body, 'textContent']]) {
      Object.defineProperty(object, key, { get: fixture.forbidden(`private ${key} read`), set: fixture.forbidden(`${key} setter`) });
    }
    for (const input of window.document.querySelectorAll('input, select, option')) {
      for (const key of ['value', 'checked', 'selected', 'selectedIndex']) {
        Object.defineProperty(input, key, { get: fixture.forbidden(`private ${key} read`), set: fixture.forbidden(`${key} setter`) });
      }
    }
    for (const link of window.document.querySelectorAll('a, link')) {
      Object.defineProperty(link, 'href', { get: fixture.forbidden('normalized href read'), set: fixture.forbidden('href setter') });
    }
    for (const method of ['setAttribute', 'removeAttribute', 'appendChild', 'removeChild', 'replaceChild']) {
      const proto = ['setAttribute', 'removeAttribute'].includes(method) ? window.Element.prototype : window.Node.prototype;
      proto[method] = fixture.forbidden(method);
    }
    const result = fixture.collect();
    assert.deepEqual(result, observe());
    assert.ok(!/SECRET_|Synthetic|password|payment|seller/iu.test(JSON.stringify(result)));
  } finally { fixture.close(); }
});
