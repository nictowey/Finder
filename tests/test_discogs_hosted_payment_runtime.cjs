'use strict';
// No live site or payment. Files and SQLite are isolated in a private temp dir.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, writeFile, readFile, copyFile, lstat, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID, createHash } = require('node:crypto');
const { EventEmitter } = require('node:events');
const { connect } = require('node:net');
const { JSDOM } = require('jsdom');
let runtimeApi, fixedApi, orderApi, guestApi, guestCard, launcherApi;
let leaseApi;
before(async () => {
  runtimeApi = await import('../scripts/discogs_hosted_payment_runtime.mjs');
  fixedApi = await import('../scripts/discogs_enhanced_fixed_pay_port.mjs');
  orderApi = await import('../scripts/discogs_enhanced_order_ledger_port.mjs');
  guestApi = await import('../scripts/discogs_guest_checkout_operator.mjs');
  guestCard = await import('../scripts/discogs_guest_card_prepare.mjs');
  launcherApi = await import('../scripts/discogs_hosted_browser_runtime.mjs');
  leaseApi = await import('../scripts/discogs_hosted_guest_socket.mjs');
});
const sha = value => createHash('sha256').update(value).digest('hex');
const enc = value => JSON.stringify(value) + '\n';
const put = async (directory, name, value) => {
  const bytes = enc(value); await writeFile(join(directory, name), bytes, { flag: 'wx', mode: 0o600 }); return sha(bytes);
};
function socketExchange(path, value) {
  return new Promise((resolve, reject) => {
    let output = ''; const client = connect(path);
    client.on('connect', () => client.write(JSON.stringify(value) + '\n'));
    client.on('data', bytes => { output += bytes; });
    client.on('error', reject);
    client.on('end', () => { try { resolve(JSON.parse(output)); } catch (error) { reject(error); } });
  });
}

async function syntheticGuestHistory(directory) {
  const now = Date.now() / 1000, intent = randomUUID(), ready = randomUUID(), scope = 'a'.repeat(64);
  const original = { schema: 'discogs_guest_card_init_fence_v1', intent_id: randomUUID(), scope_digest: scope,
    release_id: 3422100, listing_id: '1366950910', created_at: now - 50 };
  const a = await put(directory, guestCard.GUEST_CARD_FENCE, original);
  const recovery = { schema: 'discogs_guest_card_recovery_review_v1', original_fence_sha256: a,
    original_intent_id: original.intent_id, original_scope_digest: scope,
    retry_intent_id: randomUUID(), retry_scope_digest: 'b'.repeat(64) };
  const b = await put(directory, guestCard.GUEST_CARD_RECOVERY_REVIEW, recovery);
  const retry = { schema: 'discogs_guest_card_recovery_attempt_v1', attempt: 1, original_fence_sha256: a,
    reconciliation_sha256: b, intent_id: recovery.retry_intent_id, scope_digest: recovery.retry_scope_digest,
    created_at: now - 49 };
  const c = await put(directory, guestCard.GUEST_CARD_RECOVERY_FENCE, retry);
  const navigation = { schema: 'discogs_guest_card_navigation_reopen_review_v1', original_fence_sha256: a,
    original_intent_id: original.intent_id, original_scope_digest: scope, recovery_review_sha256: b,
    recovery_fence_sha256: c, recovery_intent_id: retry.intent_id, recovery_scope_digest: retry.scope_digest,
    reopen_intent_id: randomUUID(), reopen_scope_digest: 'c'.repeat(64) };
  const d = await put(directory, guestCard.GUEST_CARD_NAVIGATION_REOPEN_REVIEW, navigation);
  await put(directory, guestCard.GUEST_CARD_NAVIGATION_REOPEN_FENCE, {
    schema: 'discogs_guest_card_navigation_reopen_attempt_v1', attempt: 1, original_fence_sha256: a,
    recovery_review_sha256: b, recovery_fence_sha256: c, reconciliation_sha256: d,
    intent_id: navigation.reopen_intent_id, scope_digest: navigation.reopen_scope_digest,
    created_at: now - 48 });
  const names = [guestCard.GUEST_CARD_FENCE, guestCard.GUEST_CARD_RECOVERY_REVIEW,
    guestCard.GUEST_CARD_RECOVERY_FENCE, guestCard.GUEST_CARD_NAVIGATION_REOPEN_REVIEW,
    guestCard.GUEST_CARD_NAVIGATION_REOPEN_FENCE];
  const manifest = await Promise.all(names.map(async name => ({ name, sha256: sha(await readFile(join(directory, name))) })));
  const anchor = sha(enc(manifest)); let head = anchor;
  const core = { intent_id: intent, ready_ref: ready, scope_digest: scope };
  const records = [
    ['ready', { ...core }],
    ['review', { ...core, provenance: { checkout_document_ref: randomUUID() } }],
    ['attempt', null], ['outcome', null], ['form_observation', null],
  ];
  let claim, outcome;
  for (let i = 0; i < records.length; i++) {
    const [kind, initial] = records[i], at = now - 20 + i;
    const payload = initial || (kind === 'attempt' ? { ...core, mode: 'current_checkout_claim', review_sha256: head } :
      kind === 'outcome' ? { ...core, outcome: 'unknown', attempt_sha256: claim, observed_at: at } :
        { type: 'record_form_observation', source: 'operator_verified_current_blank_form',
          previous_claim_sha256: claim, previous_outcome_sha256: outcome, ...core,
          release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, max_total: '5.05',
          checkout_document_ref: records[1][1].provenance.checkout_document_ref,
          current_document_ref: records[1][1].provenance.checkout_document_ref, document_continuity_verified: true,
          current_url: 'https://www.discogs.com/marketplace/checkout', access: 'normal', blank_form_visible: true,
          card_fields_blank: true, payment_controls_untouched: true, displayed_pay_amount: '5.05',
          displayed_merchant_total: '5.05', currency: 'USD', observed_at: at, expires_at: at + 60,
          merchant_order_status: 'unverified', payment_status: 'unverified', provider_draft_status: 'unverified' });
    const value = { schema: 'discogs_guest_operator_record_v1', sequence: i + 1, kind,
      previous_sha256: head, legacy_anchor_sha256: anchor, recorded_at: at, payload };
    head = await put(directory, `guest-checkout-${String(i + 1).padStart(6, '0')}.json`, value);
    if (kind === 'attempt') claim = head;
    if (kind === 'outcome') outcome = head;
  }
  return { head, claim };
}

async function fixture(t, { viaLauncher = false } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-payment-runtime-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const { head, claim } = await syntheticGuestHistory(directory);
  const ledger = orderApi.createEnhancedOrderLedgerFixturePort(join(directory, 'orders.sqlite3'));
  assert.equal((await ledger.initialize()).status, 'initialized');
  await writeFile(join(directory, 'access-stopped'), 'access_restricted\n', { flag: 'wx', mode: 0o600 });
  const now = Date.now() / 1000, scope = { intent_id: randomUUID(), approval_ref: randomUUID(),
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', one_copy_limit: 1,
    currency: 'USD', max_total: '5.05', rules_revision: 0, destination_ref: randomUUID(),
    payment_ref: randomUUID(), terms_fingerprint: 'b'.repeat(64), expires_at: now + 180 };
  scope.quote_fingerprint = fixedApi.fixedEnhancedPayQuoteFingerprint(scope);
  const shippingRef = randomUUID();
  const policy = { schema: 'discogs_enhanced_payment_authorization_v1', source: 'owner_approved_exact_order',
    action: 'pay_one_exact_kc', mode: 'exact_kc_test_only', scope,
    expected_guest_head: head, expected_claim_sha256: claim, selected_shipping_ref: shippingRef,
    selected_shipping_label: 'Economy - Media Mail', authorized_at: now, expires_at: now + 120,
    revoked: false, test_active: true, kill_switch: false, already_owned: false };
  const policyPath = join(directory, 'enhanced-payment-authorization.json');
  await writeFile(policyPath, enc(policy), { flag: 'wx', mode: 0o600 });
  let browserCalls = 0;
  let currentPages = [];
  const browser = Object.assign(new EventEmitter(), { isConnected: () => true });
  const context = Object.assign(new EventEmitter(), { pages: () => { browserCalls++; return currentPages; },
    browser: () => browser, close: async () => { context.emit('close'); browser.emit('disconnected'); } });
  const isolated = { policyPath, confirmationPath: join(directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: directory, accessStopPath: join(directory, 'access-stopped'), orderLedgerPort: ledger };
  let close = null;
  const runtime = viaLauncher ? (close = await launcherApi.launchHostedPaymentRuntimeFixtureBrowser(
    { launchPersistentContext: async () => context }, '/profile', '/tmp/sniper-read-only.sock',
    { liveGate: true, fixture: isolated })).payment :
    runtimeApi.createHostedPaymentRuntimeFixture({ context, acquireContextLease: () => () => {},
      liveGate: true, ...isolated });
  t.after(() => close ? close() : runtime.close());
  return { directory, scope, policy, policyPath, ledger, runtime, context, shippingRef,
    setPages(value) { currentPages = value; }, get browserCalls() { return browserCalls; } };
}

function interceptedCheckout(context, t) {
  const html = `<h1>Checkout from Mailman21842</h1><div class="_checkout-summary__content_fixture">
    <ul class="_checkout-summary__unordered-list_fixture"><li class="_checkout-sell-item__list-item_fixture">Public item</li></ul>
    <dl class="_checkout-summary__totals_fixture">
    <div class="_checkout-summary__subtotal_fixture"><dt>Subtotal</dt><dd class="_checkout-summary__money-value_fixture">$0.10 USD</dd></div>
    <div class="_checkout-summary__subtotal_fixture"><dt>Shipping via Economy - Media Mail</dt><dd class="_checkout-summary__money-value_fixture">$4.95 USD</dd></div>
    <div class="_checkout-summary__total--color-red_fixture"><dt>Total</dt><dd class="_checkout-summary__money-value_fixture">$5.05 USD</dd></div></dl></div>`;
  const frames = [], doms = []; let clicks = 0, reads = 0;
  const convert = value => value?.__document !== undefined ? value.__document :
    value && typeof value === 'object' && !Array.isArray(value) ?
      Object.fromEntries(Object.entries(value).map(([key, item]) => [key, convert(item)])) : value;
  const handle = document => ({ __document: document, dispose: async () => {} });
  function frame(url, parent, body) {
    const dom = new JSDOM(`<html><body>${body}<input value="FORBIDDEN_CARD"></body></html>`,
      { url, runScripts: 'outside-only' }); doms.push(dom);
    dom.window.Element.prototype.getClientRects = () => [{ width: 100, height: 20 }];
    Object.defineProperty(dom.window.document, 'readyState', { get: () => 'complete' });
    Object.defineProperty(dom.window.HTMLInputElement.prototype, 'value', { get() { throw new Error('card_read_forbidden'); } });
    dom.window.fetch = () => { throw new Error('network_forbidden'); };
    let value;
    const element = { isVisible: async () => true, contentFrame: async () => value,
      ownerFrame: async () => parent, dispose: async () => {} };
    const controlHandle = node => { const control = {
      evaluate: async (fn, arg) => { reads++; return dom.window.eval(`(${fn.toString()})`)(node, convert(arg)); },
      click: async options => { assert.ok(options.timeout > 0); clicks++; }, dispose: async () => {} };
      return { asElement: () => control, dispose: async () => {} };
    };
    value = { url: () => { reads++; return url; }, parentFrame: () => { reads++; return parent; },
      frameElement: async () => { reads++; return element; },
      evaluateHandle: async (fn, arg) => { reads++;
        const result = dom.window.eval(`(${fn.toString()})`)(convert(arg));
        return result?.nodeType === 1 ? controlHandle(result) : handle(result);
      },
      evaluate: async (fn, arg) => { reads++; return dom.window.eval(`(${fn.toString()})`)(convert(arg)); },
      locator: selector => { assert.equal(selector, '#submit-button'); return {
        count: async () => dom.window.document.querySelectorAll(selector).length,
        elementHandle: async () => {
          const node = dom.window.document.querySelector(selector);
          return { evaluate: async (fn, arg) => dom.window.eval(`(${fn.toString()})`)(node, convert(arg)),
            click: async options => { assert.ok(options.timeout > 0); clicks++; }, dispose: async () => {} };
        },
      }; } };
    frames.push(value); return value;
  }
  const main = frame('https://www.discogs.com/marketplace/checkout', null, html);
  const buttons = frame('https://www.paypal.com/smart/buttons?opaque=fixture', main, '<div>Public wrapper</div>');
  frame('https://www.paypal.com/smart/fixture-guest?opaque=fixture', buttons,
    '<button id="submit-button" type="button">Pay $5.05</button>');
  const page = { ...main, context: () => context, mainFrame: () => main, frames: () => frames };
  t.after(() => doms.forEach(dom => dom.window.close()));
  return { page, get clicks() { return clicks; }, get reads() { return reads; } };
}

function confirmedMetadata(f, entryRef) {
  const confirmedAt = Date.now() / 1000;
  return { schema: 'discogs_enhanced_payment_confirmation_v1',
    source: 'owner_confirmed_current_checkout', intent_id: f.scope.intent_id, entry_ref: entryRef,
    confirmed_at: confirmedAt, expires_at: confirmedAt + 45, amount: '5.05', currency: 'USD',
    destination_ref: f.scope.destination_ref, destination_match: true,
    selected_shipping_ref: f.shippingRef, selected_shipping_label: 'Economy - Media Mail',
    method_kind: 'card', payment_ref: f.scope.payment_ref, terms_fingerprint: f.scope.terms_fingerprint,
    terms_verified: true, no_new_notice: true, conversion_notice: false, competing_currency: false,
    deferred_charges: false, all_payable_charges_included: true,
    tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total' };
}

test('disabled production runtime performs no browser, policy, guest-ledger or order-ledger read', async () => {
  let calls = 0; const never = new Proxy({}, { get() { calls++; throw new Error('disabled path touched browser'); } });
  const runtime = runtimeApi.createHostedPaymentRuntime({ context: never, acquireContextLease: () => () => {} });
  assert.equal((await runtime.inspectReadiness(null)).code, 'hosted_payment_disabled');
  assert.equal((await runtime.beginSecureEntry(null)).code, 'hosted_payment_disabled');
  assert.equal((await runtime.resumeAfterEntry(null)).code, 'hosted_payment_disabled');
  assert.equal((await runtime.attemptPayment(null)).code, 'hosted_payment_disabled');
  assert.equal(calls, 0); await runtime.close();
});

test('entry-only gate uses the anchored guest claim and visible public controls without payment authority', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  await rm(f.policyPath);
  const entry = runtimeApi.createHostedPaymentRuntimeFixture({
    context: f.context, acquireContextLease: leaseApi.createHostedContextLease(),
    entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: { status: () => { throw new Error('real_order_ledger_must_not_be_read_for_entry'); } },
  });
  t.after(() => entry.close());
  const scope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD',
    max_total: '5.05', expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  assert.equal((await entry.inspectEntryReadiness(scope)).code, 'anchored_guest_claim_observed');
  const beforeBeginReads = browser.reads, beforeBeginInventory = f.browserCalls;
  assert.equal((await entry.inspectEntryReadiness({ ...scope, max_total: '5.06' })).status, 'blocked');
  assert.equal((await entry.inspectEntryReadiness({ ...scope, expected_claim_sha256: 'f'.repeat(64) })).status, 'blocked');
  assert.equal(browser.reads, beforeBeginReads); assert.equal(f.browserCalls, beforeBeginInventory);
  const begun = await entry.beginSecureEntry(scope);
  assert.equal(begun.status, 'entry_ready', begun.code);
  assert.equal(begun.payment_allowed, false);
  const suspendedReads = browser.reads, suspendedInventory = f.browserCalls;
  assert.equal((await entry.attemptPayment(null)).code, 'hosted_payment_disabled');
  assert.equal((await entry.resumeAfterEntry(null)).code, 'hosted_payment_disabled');
  assert.equal((await entry.beginSecureEntry(scope)).code, 'hosted_secure_entry_already_started');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
  assert.equal((await entry.completeSecureEntry({ entry_ref: randomUUID(), completed_at: Date.now() / 1000 })).status, 'blocked');
  assert.equal((await entry.completeSecureEntry({ entry_ref: begun.entry_ref, completed_at: begun.entry_started_at - 1 })).status, 'blocked');
  assert.equal((await entry.completeSecureEntry({ entry_ref: begun.entry_ref, completed_at: Date.now() / 1000 + 60 })).status, 'blocked');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
  const done = await entry.completeSecureEntry({ entry_ref: begun.entry_ref, completed_at: Date.now() / 1000 });
  assert.equal(done.code, 'secure_entry_completed');
  assert.equal(done.entry_started_at, begun.entry_started_at);
  assert.equal(done.payment_allowed, false);
  assert.equal((await entry.completeSecureEntry({ entry_ref: begun.entry_ref, completed_at: Date.now() / 1000 })).status, 'blocked');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
  const restarted = runtimeApi.createHostedPaymentRuntimeFixture({
    context: f.context, acquireContextLease: leaseApi.createHostedContextLease(),
    entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger,
  });
  t.after(() => restarted.close());
  assert.equal((await restarted.completeSecureEntry({ entry_ref: begun.entry_ref,
    completed_at: Date.now() / 1000 })).status, 'blocked');
  assert.equal((await restarted.resumeAfterEntry(f.scope)).status, 'blocked');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
});

test('isolated launcher and fixed payment IPC hand off one public guest form without Pay authority', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]); await rm(f.policyPath);
  const close = await launcherApi.launchHostedPaymentRuntimeFixtureBrowser(
    { launchPersistentContext: async () => f.context }, '/profile', '/tmp/sniper-read-only.sock',
    { entryGate: true, liveGate: false, controlEnabled: true,
      fixture: { policyPath: f.policyPath,
        confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
        guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
        orderLedgerPort: { status: () => { throw new Error('entry_must_not_touch_order_ledger'); } } } });
  t.after(() => close());
  const paymentPath = join(f.directory, 'payment-control.sock');
  const request = (command, body) => ({ schema: 'discogs_hosted_payment_control_request_v1',
    command, request: body });
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const readiness = await socketExchange(paymentPath, request('inspect_readiness', { scope: entryScope }));
  assert.equal(readiness.code, 'anchored_guest_claim_observed');
  assert.equal(readiness.browser_evidence_pending, true);
  const begun = await socketExchange(paymentPath, request('begin_secure_entry', { scope: entryScope }));
  assert.equal(begun.status, 'entry_ready', begun.code);
  assert.equal(begun.reads_suspended, true); assert.equal(begun.payment_allowed, false);
  const readsAtEntry = browser.reads, inventoryAtEntry = f.browserCalls;
  const ordinary = await socketExchange('/tmp/sniper-read-only.sock',
    { action: 'current_cart', listing_id: '1366950910' });
  assert.equal(ordinary.code, 'read_in_progress');
  assert.equal(browser.reads, readsAtEntry); assert.equal(f.browserCalls, inventoryAtEntry);
  const complete = await socketExchange(paymentPath, request('complete_secure_entry',
    { entry_ref: begun.entry_ref, completed_at: Date.now() / 1000 }));
  assert.equal(complete.code, 'secure_entry_completed');
  const attempt = await socketExchange(paymentPath, request('attempt_payment', { scope: f.scope }));
  assert.equal(attempt.code, 'hosted_payment_disabled');
  assert.equal(browser.clicks, 0);
  assert.equal(browser.reads, readsAtEntry); assert.equal(f.browserCalls, inventoryAtEntry);
  await assert.rejects(readFile(f.policyPath), { code: 'ENOENT' });
  await assert.rejects(readFile(join(f.directory, 'enhanced-payment-confirmation.json')), { code: 'ENOENT' });
});

test('expired entry scope cannot complete a suspended secure-entry handoff', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]); await rm(f.policyPath);
  const acquireContextLease = leaseApi.createHostedContextLease();
  const runtime = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease, entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: { status: () => { throw new Error('entry_must_not_read_order_ledger'); } } });
  t.after(() => runtime.close());
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 2.5 };
  const begun = await runtime.beginSecureEntry(entryScope);
  assert.equal(begun.status, 'entry_ready', begun.code);
  const reads = browser.reads, inventory = f.browserCalls;
  await new Promise(resolve => setTimeout(resolve, Math.max(1,
    Math.ceil((entryScope.expires_at - Date.now() / 1000) * 1000) + 25)));
  assert.equal((await runtime.completeSecureEntry({ entry_ref: begun.entry_ref,
    completed_at: Date.now() / 1000 })).status, 'blocked');
  assert.equal((await runtime.resumeAfterEntry(f.scope)).status, 'blocked');
  assert.equal((await runtime.attemptPayment(f.scope)).status, 'blocked');
  assert.equal(browser.reads, reads); assert.equal(f.browserCalls, inventory);
  assert.equal(browser.clicks, 0);
  assert.equal(acquireContextLease(), null, 'expired in-memory handoff stays leased until explicit shutdown');
  await runtime.close();
  const released = acquireContextLease(); assert.equal(typeof released, 'function'); released();
});

test('browser disconnect while entry reads are suspended closes control and invalidates its token', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]); await rm(f.policyPath);
  const close = await launcherApi.launchHostedPaymentRuntimeFixtureBrowser(
    { launchPersistentContext: async () => f.context }, '/profile', '/tmp/sniper-read-only.sock',
    { entryGate: true, liveGate: false, controlEnabled: true,
      fixture: { policyPath: f.policyPath,
        confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
        guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
        orderLedgerPort: { status: () => { throw new Error('entry_must_not_read_order_ledger'); } } } });
  t.after(() => close());
  const socket = join(f.directory, 'payment-control.sock');
  const scope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const begun = await socketExchange(socket, { schema: 'discogs_hosted_payment_control_request_v1',
    command: 'begin_secure_entry', request: { scope } });
  assert.equal(begun.status, 'entry_ready', begun.code);
  const reads = browser.reads, inventory = f.browserCalls;
  f.context.browser().emit('disconnected');
  assert.equal(await close.unavailable, 'browser_unavailable');
  await close();
  assert.equal((await close.payment.completeSecureEntry({ entry_ref: begun.entry_ref,
    completed_at: Date.now() / 1000 })).status, 'blocked');
  assert.equal((await close.payment.attemptPayment(f.scope)).status, 'blocked');
  await assert.rejects(lstat(socket), { code: 'ENOENT' });
  assert.equal(browser.reads, reads); assert.equal(f.browserCalls, inventory);
  assert.equal(browser.clicks, 0);
});

test('entry-only completion later binds the original token and document to independently authorized Pay', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  const policyBytes = enc(f.policy);
  await rm(f.policyPath);
  const runtime = runtimeApi.createHostedPaymentRuntimeFixture({
    context: f.context, acquireContextLease: leaseApi.createHostedContextLease(),
    entryGate: true, liveGate: true, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger,
  });
  t.after(() => runtime.close());
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD',
    max_total: '5.05', expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const begun = await runtime.beginSecureEntry(entryScope);
  assert.equal(begun.status, 'entry_ready', begun.code);
  const noPolicyReads = browser.reads, noPolicyInventory = f.browserCalls;
  assert.equal((await runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
  assert.equal((await runtime.resumeAfterEntry(f.scope)).code, 'hosted_secure_entry_not_resumable');
  assert.equal(browser.reads, noPolicyReads); assert.equal(f.browserCalls, noPolicyInventory);
  const completed = await runtime.completeSecureEntry({ entry_ref: begun.entry_ref, completed_at: Date.now() / 1000 });
  assert.equal(completed.code, 'secure_entry_completed');
  await writeFile(f.policyPath, policyBytes, { flag: 'wx', mode: 0o600 });
  await writeFile(join(f.directory, 'enhanced-payment-confirmation.json'), enc(confirmedMetadata(f, begun.entry_ref)),
    { flag: 'wx', mode: 0o600 });
  const resumed = await runtime.resumeAfterEntry(f.scope);
  assert.equal(resumed.code, 'fresh_post_entry_context_verified', resumed.code);
  const attempted = await runtime.attemptPayment(f.scope);
  assert.equal(attempted.code, 'submission_unknown', attempted.code);
  assert.equal(browser.clicks, 1);
  assert.equal((await f.ledger.status()).ledger.state, 'submission_unknown');
});

test('entry-only Pay confirmation must be newer than the actual secure-entry completion', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]); await rm(f.policyPath);
  const runtime = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease: leaseApi.createHostedContextLease(), entryGate: true, liveGate: true,
    policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  t.after(() => runtime.close());
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const begun = await runtime.beginSecureEntry(entryScope);
  assert.equal(begun.status, 'entry_ready', begun.code);
  const premature = confirmedMetadata(f, begun.entry_ref);
  assert.ok(premature.confirmed_at >= begun.entry_started_at);
  await new Promise(resolve => setTimeout(resolve, 25));
  const completed = await runtime.completeSecureEntry({ entry_ref: begun.entry_ref,
    completed_at: Date.now() / 1000 });
  assert.equal(completed.code, 'secure_entry_completed');
  assert.ok(premature.confirmed_at < completed.entry_completed_at);
  await writeFile(f.policyPath, enc(f.policy), { flag: 'wx', mode: 0o600 });
  await writeFile(join(f.directory, 'enhanced-payment-confirmation.json'), enc(premature),
    { flag: 'wx', mode: 0o600 });
  assert.equal((await runtime.resumeAfterEntry(f.scope)).status, 'blocked');
  assert.equal(browser.clicks, 0);
  assert.equal((await f.ledger.status()).ledger, null);
});

test('entry-only Pay policy cannot switch to another valid same-item guest claim', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]); await rm(f.policyPath);
  const runtime = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease: leaseApi.createHostedContextLease(), entryGate: true, liveGate: true,
    policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  t.after(() => runtime.close());
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const begun = await runtime.beginSecureEntry(entryScope);
  assert.equal(begun.status, 'entry_ready', begun.code);
  assert.equal((await runtime.completeSecureEntry({ entry_ref: begun.entry_ref,
    completed_at: Date.now() / 1000 })).code, 'secure_entry_completed');
  // Replace only isolated synthetic fixture bytes to model a second independently
  // valid same-item chain; a live ledger is append-only and never rewritten.
  const alternate = await mkdtemp(join(tmpdir(), 'sniper-alternate-guest-fixture-'));
  t.after(() => rm(alternate, { recursive: true, force: true }));
  const next = await syntheticGuestHistory(alternate);
  const names = [guestCard.GUEST_CARD_FENCE, guestCard.GUEST_CARD_RECOVERY_REVIEW,
    guestCard.GUEST_CARD_RECOVERY_FENCE, guestCard.GUEST_CARD_NAVIGATION_REOPEN_REVIEW,
    guestCard.GUEST_CARD_NAVIGATION_REOPEN_FENCE,
    ...Array.from({ length: 5 }, (_, i) => `guest-checkout-${String(i + 1).padStart(6, '0')}.json`)];
  for (const name of names) await copyFile(join(alternate, name), join(f.directory, name));
  assert.equal((await guestApi.inspectGuestCheckoutLedger({ directory: f.directory,
    expectedClaim: next.claim })).claim_verified, true);
  assert.notEqual(next.claim, entryScope.expected_claim_sha256);
  const nextPolicy = { ...f.policy, expected_guest_head: next.head,
    expected_claim_sha256: next.claim };
  await writeFile(f.policyPath, enc(nextPolicy), { flag: 'wx', mode: 0o600 });
  await writeFile(join(f.directory, 'enhanced-payment-confirmation.json'),
    enc(confirmedMetadata(f, begun.entry_ref)), { flag: 'wx', mode: 0o600 });
  const reads = browser.reads;
  assert.equal((await runtime.resumeAfterEntry(f.scope)).status, 'blocked');
  assert.equal(browser.reads, reads);
  assert.equal(browser.clicks, 0);
});

test('entry-only cancellation and close during a pending document read cannot hand off or release its lease early', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  const entryScope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD',
    max_total: '5.05', expected_guest_head: f.policy.expected_guest_head,
    expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
  const acquireContextLease = leaseApi.createHostedContextLease();
  const cancelled = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease, entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  const controller = new AbortController(); controller.abort();
  assert.equal((await cancelled.beginSecureEntry(entryScope, { signal: controller.signal })).status, 'blocked');
  assert.equal(browser.reads, 0); assert.equal(f.browserCalls, 0);
  await cancelled.close();

  let witnessStarted, releaseWitness, disposed = 0;
  const started = new Promise(resolve => { witnessStarted = resolve; });
  const pending = new Promise(resolve => { releaseWitness = resolve; });
  browser.page.evaluateHandle = async () => {
    witnessStarted(); await pending;
    return { dispose: async () => { disposed++; } };
  };
  const entry = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease, entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  t.after(() => entry.close());
  const beginning = entry.beginSecureEntry(entryScope);
  await started;
  await entry.close();
  assert.equal(acquireContextLease(), null, 'pending page read must retain shared lease');
  const readsAtClose = browser.reads, inventoryAtClose = f.browserCalls;
  releaseWitness();
  assert.equal((await beginning).status, 'blocked');
  assert.equal(browser.reads, readsAtClose); assert.equal(f.browserCalls, inventoryAtClose);
  assert.equal(disposed, 1);
  const released = acquireContextLease(); assert.equal(typeof released, 'function'); released();

  let abortStarted, releaseAbortWitness, abortedDisposed = 0;
  const abortReadStarted = new Promise(resolve => { abortStarted = resolve; });
  const pendingAbortRead = new Promise(resolve => { releaseAbortWitness = resolve; });
  browser.page.evaluateHandle = async () => {
    abortStarted(); await pendingAbortRead;
    return { dispose: async () => { abortedDisposed++; } };
  };
  const controllerDuringRead = new AbortController();
  const entryAfterAbort = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease, entryGate: true, liveGate: false, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  t.after(() => entryAfterAbort.close());
  const abortedBeginning = entryAfterAbort.beginSecureEntry(entryScope,
    { signal: controllerDuringRead.signal });
  await abortReadStarted;
  controllerDuringRead.abort();
  const readsAtAbort = browser.reads, inventoryAtAbort = f.browserCalls;
  releaseAbortWitness();
  assert.equal((await abortedBeginning).status, 'blocked');
  assert.equal(browser.reads, readsAtAbort); assert.equal(f.browserCalls, inventoryAtAbort);
  assert.equal(abortedDisposed, 1);
});

test('entry-only public amount or bound Pay control drift blocks before card handoff', async t => {
  for (const change of ['merchant_total', 'public_pay_label']) {
    const f = await fixture(t), browser = interceptedCheckout(f.context, t);
    f.setPages([browser.page]);
    if (change === 'merchant_total') {
      await browser.page.evaluate(() => { document.querySelector('._checkout-summary__total--color-red_fixture dd').textContent = '$5.06 USD'; });
    } else {
      await browser.page.frames()[2].evaluate(() => { document.querySelector('#submit-button').textContent = 'Pay $5.06'; });
    }
    const scope = { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100,
      listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD',
      max_total: '5.05', expected_guest_head: f.policy.expected_guest_head,
      expected_claim_sha256: f.policy.expected_claim_sha256, expires_at: Date.now() / 1000 + 180 };
    const entry = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
      acquireContextLease: leaseApi.createHostedContextLease(), entryGate: true, liveGate: false,
      policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
      guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
      orderLedgerPort: f.ledger });
    t.after(() => entry.close());
    assert.equal((await entry.beginSecureEntry(scope)).status, 'blocked', change);
    assert.equal((await entry.completeSecureEntry({ entry_ref: randomUUID(),
      completed_at: Date.now() / 1000 })).status, 'blocked');
  }
});

test('exact private policy is checked against the real guest chain and real SQLite status without browser use', async t => {
  const f = await fixture(t);
  const guest = await guestApi.inspectGuestCheckoutLedger({ directory: f.directory,
    expectedClaim: f.policy.expected_claim_sha256 });
  assert.equal(guest.claim_verified, true); assert.equal(guest.current_head, f.policy.expected_guest_head);
  assert.equal((await f.ledger.status()).ledger, null);
  const ready = await f.runtime.inspectReadiness(f.scope);
  assert.equal(ready.status, 'observed'); assert.equal(ready.real_order_state, 'no_order_intent');
  assert.equal(ready.payment_allowed, false); assert.equal(f.browserCalls, 0);
  f.policy.revoked = true; await writeFile(f.policyPath, enc(f.policy));
  assert.equal((await f.runtime.inspectReadiness(f.scope)).code, 'hosted_payment_authority_unavailable');
  assert.equal(f.browserCalls, 0);
});

test('a changed guest head or malformed private policy blocks before selecting a page', async t => {
  const f = await fixture(t);
  f.policy.expected_guest_head = 'f'.repeat(64); await writeFile(f.policyPath, enc(f.policy));
  assert.equal((await f.runtime.beginSecureEntry(f.scope)).code, 'hosted_secure_entry_unavailable');
  assert.equal(f.browserCalls, 0);
  f.policy.expected_guest_head = 'x'; await writeFile(f.policyPath, enc(f.policy));
  assert.equal((await f.runtime.inspectReadiness(f.scope)).code, 'hosted_payment_authority_unavailable');
  assert.equal(f.browserCalls, 0);
});

test('actual private readers and current DOM can rehearse one fenced Pay without network or card input', async t => {
  const f = await fixture(t, { viaLauncher: true }), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  assert.equal((await f.runtime.resumeAfterEntry(f.scope)).code, 'hosted_secure_entry_not_resumable');
  assert.equal(browser.reads, 0);
  const begun = await f.runtime.beginSecureEntry(f.scope);
  assert.equal(begun.status, 'entry_ready', begun.code); assert.match(begun.entry_ref, /^[0-9a-f-]{36}$/);
  const suspendedReads = browser.reads, suspendedInventory = f.browserCalls;
  assert.equal((await f.runtime.beginSecureEntry(f.scope)).code, 'hosted_secure_entry_already_started');
  assert.equal((await f.runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
  const confirmation = confirmedMetadata(f, begun.entry_ref);
  await writeFile(join(f.directory, 'enhanced-payment-confirmation.json'), enc(confirmation),
    { flag: 'wx', mode: 0o600 });
  const firstResume = f.runtime.resumeAfterEntry(f.scope);
  const readsAtResume = browser.reads, inventoryAtResume = f.browserCalls;
  assert.equal((await f.runtime.resumeAfterEntry(f.scope)).code, 'hosted_secure_entry_not_resumable');
  assert.equal(browser.reads, readsAtResume); assert.equal(f.browserCalls, inventoryAtResume);
  const resumed = await firstResume;
  assert.equal(resumed.code, 'fresh_post_entry_context_verified');
  const resumedReads = browser.reads, resumedInventory = f.browserCalls;
  assert.equal((await f.runtime.resumeAfterEntry(f.scope)).code, 'hosted_secure_entry_not_resumable');
  assert.equal(browser.reads, resumedReads); assert.equal(f.browserCalls, resumedInventory);
  const firstAttempt = f.runtime.attemptPayment(f.scope);
  const readsAtAttempt = browser.reads, inventoryAtAttempt = f.browserCalls;
  assert.equal((await f.runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
  assert.equal(browser.reads, readsAtAttempt); assert.equal(f.browserCalls, inventoryAtAttempt);
  const attempted = await firstAttempt;
  assert.equal(attempted.code, 'submission_unknown'); assert.equal(browser.clicks, 1);
  assert.equal((await f.ledger.status()).ledger.state, 'submission_unknown');
  const afterAttemptReads = browser.reads, afterAttemptInventory = f.browserCalls;
  assert.equal((await f.runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
  assert.equal(browser.reads, afterAttemptReads); assert.equal(f.browserCalls, afterAttemptInventory);
  assert.equal((await f.runtime.resumeAfterEntry(f.scope)).code, 'hosted_secure_entry_not_resumable');
  assert.equal(browser.reads, afterAttemptReads); assert.equal(f.browserCalls, afterAttemptInventory);
  assert.equal(browser.clicks, 1);
  // A new process has no in-memory entry state, but the durable unknown fence
  // must stop it before it even inventories the resident browser pages.
  let restartedBrowserReads = 0;
  const restarted = runtimeApi.createHostedPaymentRuntimeFixture({
    context: { pages: () => { restartedBrowserReads++; return [browser.page]; } },
    acquireContextLease: () => () => {}, liveGate: true,
    policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger,
  });
  t.after(() => restarted.close());
  assert.equal((await restarted.beginSecureEntry(f.scope)).code, 'hosted_prior_submit_uncertain');
  assert.equal(restartedBrowserReads, 0);
});

test('policy expiry or revocation during durable metadata reads blocks before browser handoff', async t => {
  for (const change of ['expire', 'revoke']) {
    const f = await fixture(t);
    const delayedPort = { status: async () => {
      const status = await f.ledger.status();
      if (change === 'expire') {
        f.policy.expires_at = Date.now() / 1000 + 0.001;
        await writeFile(f.policyPath, enc(f.policy));
        await new Promise(resolve => setTimeout(resolve, 15));
      } else {
        f.policy.revoked = true;
        await writeFile(f.policyPath, enc(f.policy));
      }
      return status;
    } };
    let browserReads = 0;
    const guarded = runtimeApi.createHostedPaymentRuntimeFixture({
      context: { pages: () => { browserReads++; return []; } }, acquireContextLease: () => () => {},
      liveGate: true, policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
      guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
      orderLedgerPort: delayedPort,
    });
    t.after(() => guarded.close());
    assert.equal((await guarded.beginSecureEntry(f.scope)).code, 'hosted_secure_entry_unavailable');
    assert.equal(browserReads, 0);
  }
});

test('a held shared browser lease prevents every initial page read', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  const acquireContextLease = leaseApi.createHostedContextLease();
  const release = acquireContextLease();
  const guarded = runtimeApi.createHostedPaymentRuntimeFixture({ context: f.context,
    acquireContextLease, liveGate: true, policyPath: f.policyPath,
    confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: f.ledger });
  t.after(() => guarded.close());
  assert.equal((await guarded.beginSecureEntry(f.scope)).code, 'hosted_browser_busy');
  assert.equal(f.browserCalls, 0); assert.equal(browser.reads, 0);
  release();
});

test('concurrent begin is reserved before its first await and cannot read during secure entry', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  const first = f.runtime.beginSecureEntry(f.scope);
  assert.equal((await f.runtime.beginSecureEntry(f.scope)).code, 'hosted_secure_entry_already_started');
  assert.equal(f.browserCalls, 0); assert.equal(browser.reads, 0);
  assert.equal((await first).status, 'entry_ready');
  const suspendedReads = browser.reads, suspendedInventory = f.browserCalls;
  assert.equal((await f.runtime.beginSecureEntry(f.scope)).code, 'hosted_secure_entry_already_started');
  assert.equal(browser.reads, suspendedReads); assert.equal(f.browserCalls, suspendedInventory);
});

test('closing while durable metadata is pending cannot hand off or read the browser', async t => {
  const f = await fixture(t);
  let entered, continueStatus;
  const statusEntered = new Promise(resolve => { entered = resolve; });
  const blockedStatus = new Promise(resolve => { continueStatus = resolve; });
  const guarded = runtimeApi.createHostedPaymentRuntimeFixture({
    context: f.context, acquireContextLease: leaseApi.createHostedContextLease(), liveGate: true,
    policyPath: f.policyPath, confirmationPath: join(f.directory, 'enhanced-payment-confirmation.json'),
    guestLedgerDirectory: f.directory, accessStopPath: join(f.directory, 'access-stopped'),
    orderLedgerPort: { status: async () => { entered(); await blockedStatus; return f.ledger.status(); } },
  });
  const beginning = guarded.beginSecureEntry(f.scope);
  await statusEntered;
  await guarded.close();
  continueStatus();
  assert.equal((await beginning).code, 'hosted_secure_entry_unavailable');
  assert.equal(f.browserCalls, 0);
});

test('a document witness arriving after close is disposed without another browser read', async t => {
  const f = await fixture(t), browser = interceptedCheckout(f.context, t);
  f.setPages([browser.page]);
  let witnessStarted, releaseWitness, disposed = 0;
  const started = new Promise(resolve => { witnessStarted = resolve; });
  const pending = new Promise(resolve => { releaseWitness = resolve; });
  browser.page.evaluateHandle = async () => {
    witnessStarted(); await pending;
    return { dispose: async () => { disposed++; } };
  };
  const beginning = f.runtime.beginSecureEntry(f.scope);
  await started;
  await f.runtime.close();
  const readsAtClose = browser.reads, inventoryAtClose = f.browserCalls;
  releaseWitness();
  assert.equal((await beginning).code, 'entry_document_unverified');
  assert.equal(browser.reads, readsAtClose);
  assert.equal(f.browserCalls, inventoryAtClose);
  assert.equal(disposed, 1);
  assert.equal((await f.runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
});

test('changed amount, destination, method, terms, notice, expiry, or revoked policy cannot resume or click', async t => {
  for (const change of [
    (f, c) => { c.amount = '5.06'; },
    (f, c) => { c.destination_ref = randomUUID(); },
    (f, c) => { c.selected_shipping_ref = randomUUID(); },
    (f, c) => { c.method_kind = 'paypal'; },
    (f, c) => { c.terms_fingerprint = 'c'.repeat(64); },
    (f, c) => { c.no_new_notice = false; },
    (f, c) => { c.confirmed_at -= 61; c.expires_at = c.confirmed_at + 45; },
    (f, c) => { f.policy.revoked = true; },
  ]) {
    const f = await fixture(t), browser = interceptedCheckout(f.context, t);
    f.setPages([browser.page]);
    const begun = await f.runtime.beginSecureEntry(f.scope);
    assert.equal(begun.status, 'entry_ready', begun.code);
    const confirmation = confirmedMetadata(f, begun.entry_ref); change(f, confirmation);
    await writeFile(join(f.directory, 'enhanced-payment-confirmation.json'), enc(confirmation),
      { flag: 'wx', mode: 0o600 });
    if (f.policy.revoked) await writeFile(f.policyPath, enc(f.policy));
    assert.equal((await f.runtime.resumeAfterEntry(f.scope)).code, 'hosted_post_entry_confirmation_unavailable');
    assert.equal((await f.runtime.attemptPayment(f.scope)).code, 'hosted_payment_disabled');
    assert.equal(browser.clicks, 0); assert.equal((await f.ledger.status()).ledger, null);
  }
});
