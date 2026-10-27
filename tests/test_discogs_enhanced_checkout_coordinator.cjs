'use strict';
// Executable offline rehearsal:
// node --test tests/test_discogs_enhanced_checkout_coordinator.cjs
// Actual temporary SQLite/Python ledger; all browser/provider/dispatch evidence
// below is intercepted synthetic metadata. No HTTP, payment or receipt DOM claim.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, rm, mkdir, copyFile, readdir, readFile } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID, createHash } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { pathToFileURL } = require('node:url');
let createEnhancedCheckoutCoordinator, createEnhancedCheckoutCoordinatorFixture,
  createBoundEnhancedOrderFencePort, describeEnhancedCheckoutReadiness, createEnhancedOrderLedgerFixturePort,
  attemptEnhancedPayment, KC_ORDER_SCOPE;
before(async () => {
  ({ createEnhancedCheckoutCoordinator, createEnhancedCheckoutCoordinatorFixture,
    createBoundEnhancedOrderFencePort, describeEnhancedCheckoutReadiness } = await import('../scripts/discogs_enhanced_checkout_coordinator.mjs'));
  ({ createEnhancedOrderLedgerFixturePort } = await import('../scripts/discogs_enhanced_order_ledger_port.mjs'));
  ({ attemptEnhancedPayment } = await import('../scripts/discogs_enhanced_payment_adapter.mjs'));
  ({ KC_ORDER_SCOPE } = await import('../scripts/discogs_merchant_order_evidence.mjs'));
});
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-coordinator-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, 'orders.sqlite3'), port = createEnhancedOrderLedgerFixturePort(path), now = Date.now() / 1000;
  const scope = { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100, listing_id: '1366950910',
    seller: 'Mailman21842', one_copy_limit: 1, currency: 'USD', max_total: '5.05', rules_revision: 4,
    quote_fingerprint: 'a'.repeat(64), destination_ref: randomUUID(), payment_ref: randomUUID(), terms_fingerprint: 'b'.repeat(64), expires_at: now + 180 };
  const money = amount => ({ amount, currency: 'USD' }), shippingRef = randomUUID();
  const quote = { source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id, release_id: scope.release_id,
    listing_id: scope.listing_id, seller: scope.seller, rules_revision: scope.rules_revision, quantity: 1, currency: 'USD',
    quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref, payment_ref: scope.payment_ref,
    final_quote_verified: true, all_payable_charges_included: true, payment_action: 'none', shipping_selection_verified: true,
    selected_shipping_ref: shippingRef, selected_shipping_label: 'Synthetic selected shipping', observed_at: now, expires_at: now + 120,
    charge_basis: 'itemized', item: money('0.10'), shipping: money('4.95'), tax: money('0.00'), fees: null, fees_absent_verified: true, total: money('5.05') };
  const fence = createBoundEnhancedOrderFencePort(scope, quote, { ledgerPort: port });
  return { port, path, scope, quote, fence, now, shippingRef };
}
function receipt(f, displayedStatus = 'Invoice Sent') {
  return { order_id: '123456-789012', release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842',
    total: '5.05', currency: 'USD', displayed_status: displayedStatus, observed_at: Date.now() / 1000, source: 'normal_order_page' };
}
function interceptedPaymentPorts(f, dispatch) {
  return { kind: 'trusted_production_enhanced_payment_v1',
    readCurrentState: async () => ({ source: 'current_guarded_candidate', intent_id: f.scope.intent_id,
      release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', rules_revision: f.scope.rules_revision,
      claim_state: 'cart_observed', owned: false, active: true, kill_switch: false, one_copy_limit: 1,
      intent_state: (await f.port.status()).ledger?.state || 'prepared' }),
    readFinalQuote: async () => structuredClone(f.quote),
    readDestination: async () => ({ source: 'verified_current_checkout_destination', current: true, selected_for_checkout: true,
      destination_ref: f.scope.destination_ref, selected_shipping_ref: f.shippingRef, approved_for_intent: f.scope.intent_id }),
    readPaymentMethod: async () => ({ source: 'verified_current_enhanced_payment_method', selected: true, method_kind: 'card',
      payment_ref: f.scope.payment_ref, approved_for_intent: f.scope.intent_id }),
    readTerms: async () => ({ source: 'owner_confirmed_current_cart_terms', current: true, fingerprint: f.scope.terms_fingerprint, approved_for_intent: f.scope.intent_id }),
    readAuthorization: async () => ({ source: 'owner_approved_exact_order', current: true, revoked: false, scope: { ...f.scope } }),
    readPayControl: async () => ({ source: 'verified_current_enhanced_pay_control', url: 'https://www.discogs.com/marketplace/checkout',
      control_ref: f.controlRef, control_fingerprint: 'c'.repeat(64), observed_label: 'Synthetic intercepted commit', visible: true, enabled: true,
      selector_verified: true, commit_semantics: 'creates_order_or_payment', observed_at: f.now, expires_at: f.now + 20 }),
    fencePossibleSubmit: f.fence.fencePossibleSubmit, dispatchObservedPay: dispatch };
}

test('production coordinator cannot accept payment toggles/dispatchers and attempt remains disabled without ledger mutation', async () => {
  for (const config of [{ enablePay: true }, { ledgerPort: {} }, { dispatchObservedPay: () => { throw new Error('Forbidden'); } }]) {
    assert.throws(() => createEnhancedCheckoutCoordinator(config), /fixed_production_coordinator_configuration_required/);
  }
  const coordinator = createEnhancedCheckoutCoordinator(); assert.equal(coordinator.paymentEnabled, false);
  const result = await coordinator.attemptPayment({}, { enablePay: true });
  assert.equal(result.code, 'enhanced_payment_disabled'); assert.equal(result.may_submit_again, false);
});

test('current quote reader is composed into truthful missing-profile readiness; inspection never fences', async t => {
  const f = await fixture(t); let fences = 0;
  const coordinator = createEnhancedCheckoutCoordinatorFixture({ ledgerPort: { reconcile: f.port.reconcile, fencePossibleSubmit: () => { fences++; } },
    readQuote: async () => ({ schema: 'discogs_enhanced_payment_quote_v1', final_quote_verified: false,
      gates: { provider_dom_qualification: false, provider_delivery_binding: false },
      missing_evidence: ['provider_dom_qualification', 'provider_delivery_binding'] }) });
  const result = await coordinator.inspectPaymentReadiness({});
  assert.equal(result.payment_allowed, false); assert.equal(result.payment.code, 'enhanced_payment_disabled');
  assert.ok(result.missing_profiles.includes('provider_final_quote_dom'));
  assert.equal(result.missing_profiles.includes('merchant_positive_order_dom'), false);
  assert.ok(result.post_submit_verification.missing_profiles.includes('merchant_positive_order_dom'));
  assert.equal(result.post_submit_verification.current_order_status, 'not_checked');
  assert.equal(result.post_submit_verification.automatic_retry_allowed, false);
  assert.equal(fences, 0); assert.equal((await f.port.status()).status, 'blocked');
});

test('immutable fence binding rejects changed quote/scope and snapshots caller mutations before actual CLI', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal(Object.isFrozen(f.fence.expectedOrder), true);
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, { ...f.quote, total: { amount: '5.06', currency: 'USD' } }, f.scope)).code, 'immutable_order_binding_changed');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, { ...f.scope, payment_ref: randomUUID() })).code, 'immutable_order_binding_changed');
  assert.equal((await f.port.status()).ledger, null);
  const originalQuote = structuredClone(f.quote), originalScope = { ...f.scope };
  f.quote.total.amount = '9.99'; f.scope.payment_ref = randomUUID();
  const fenced = await f.fence.fencePossibleSubmit(originalScope.intent_id, originalQuote, originalScope);
  assert.equal(fenced.durable, true); assert.equal((await f.port.status()).ledger.state, 'submission_unknown');
});

test('unknown actual provider final quote cannot create an immutable order binding', async t => {
  const f = await fixture(t);
  assert.throws(() => createBoundEnhancedOrderFencePort(f.scope, { ...f.quote, final_quote_verified: false }, { ledgerPort: f.port }), /verified_final_quote_binding_required/);
  assert.equal((await f.port.status()).status, 'blocked');
});

test('offline CLI rehearsal: validated synthetic quote -> actual SQLite fence before intercepted dispatch -> unpaid/pending/paid receipts', async t => {
  const f = await fixture(t); f.controlRef = randomUUID(); assert.equal((await f.port.initialize()).status, 'initialized');
  let dispatches = 0;
  const ports = interceptedPaymentPorts(f, async () => { assert.equal((await f.port.status()).ledger.state, 'submission_unknown'); dispatches++; });
  const outcome = await attemptEnhancedPayment(f.scope, ports, { enablePay: true });
  assert.equal(outcome.code, 'submission_unknown'); assert.equal(dispatches, 1);
  for (const [displayed, paymentState] of [['Invoice Sent', 'unpaid'], ['Payment Pending', 'pending'], ['Payment Received', 'paid']]) {
    const positive = receipt(f, displayed);
    const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: f.port,
      readMerchant: async (page, scope) => { assert.deepEqual(scope, KC_ORDER_SCOPE); return { status: 'observed', order_page_evidence: positive }; } });
    const reconciled = await coordinator.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now });
    assert.equal(reconciled.positive_order_evidence_recorded, true); assert.equal(reconciled.ledger.state, 'order_seen');
    assert.equal(reconciled.ledger.payment_state, paymentState); assert.equal(reconciled.may_submit_again, false);
  }
  const restarted = createEnhancedOrderLedgerFixturePort(f.path);
  assert.equal((await restarted.status()).ledger.payment_state, 'paid');
  assert.equal((await attemptEnhancedPayment(f.scope, ports, { enablePay: true })).code, 'payment_preflight_unverified');
  assert.equal(dispatches, 1); assert.equal((await restarted.status()).ledger.may_submit_again, false);
});

test('actual unqualified merchant reader is composed with null lookup preserving unknown, including after restart', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).durable, true);
  const restarted = createEnhancedOrderLedgerFixturePort(f.path);
  const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: restarted });
  const page = { url: () => 'https://www.discogs.com/marketplace/checkout', title: () => { throw new Error('Not a receipt'); }, evaluate: () => { throw new Error('Not a receipt'); } };
  const result = await coordinator.reconcileCurrentMerchantOrder(page, { afterAttemptAt: f.now });
  assert.equal(result.lookup_evidence, 'no_qualified_positive_order_evidence'); assert.equal(result.ledger.state, 'submission_unknown');
  assert.equal(result.absence_verified, false); assert.equal(result.provider_draft_status, 'unverified');
});

test('zero history, malformed/stale/provider receipt evidence cannot clear real submission uncertainty', async t => {
  const f = await fixture(t); assert.equal((await f.port.initialize()).status, 'initialized');
  assert.equal((await f.fence.fencePossibleSubmit(f.scope.intent_id, f.quote, f.scope)).durable, true);
  for (const report of [
    { status: 'observed', observed_order_count: 0, order_page_evidence: null },
    { status: 'unverified', order_page_evidence: receipt(f) },
    { status: 'observed', order_page_evidence: { ...receipt(f), source: 'provider_sdk_callback' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), order_id: 'SDK_TOKEN' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), listing_id: '3526572746' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), total: '5.06' } },
    { status: 'observed', order_page_evidence: { ...receipt(f), observed_at: f.now - 1 } },
    { status: 'observed', order_page_evidence: { ...receipt(f), address: 'TOP_SECRET' } },
  ]) {
    const coordinator = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort: f.port, readMerchant: async () => report });
    const result = await coordinator.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now });
    assert.equal(result.positive_order_evidence_recorded, false); assert.equal(result.ledger.state, 'submission_unknown');
    assert.equal(result.ledger.payment_state, 'unknown'); assert.equal(JSON.stringify(result).includes('TOP_SECRET'), false);
  }
});

test('cancellation and missing immutable intent stop reconciliation before ledger calls', async t => {
  const f = await fixture(t); let lookups = 0;
  const ledgerPort = { reconcile: async () => { lookups++; throw Error('Forbidden'); } };
  const cancelled = new AbortController(); cancelled.abort();
  const absent = createEnhancedCheckoutCoordinatorFixture({ ledgerPort });
  assert.equal((await absent.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now })).code, 'merchant_reconciliation_context_required');
  const present = createEnhancedCheckoutCoordinatorFixture({ expectedOrder: f.fence.expectedOrder, ledgerPort });
  assert.equal((await present.reconcileCurrentMerchantOrder({}, { afterAttemptAt: f.now, signal: cancelled.signal })).code, 'merchant_reconciliation_cancelled');
  assert.equal(lookups, 0);
});

test('production coordinator invokes the actual fixed quote producer and never promotes merchant aggregate to final quote', async () => {
  const { collectEnhancedPaymentQuoteDom } = await import('../scripts/discogs_enhanced_payment_quote.mjs');
  const { waitForEnhancedCheckoutCoordinatorSettlement } = await import('../scripts/discogs_enhanced_checkout_coordinator.mjs');
  const calls = [], handle = { dispose: async () => calls.push('dispose') };
  const page = { url: () => 'https://www.discogs.com/marketplace/checkout', evaluateHandle: async () => handle,
    evaluate: async (collector, argument) => {
      if (collector !== collectEnhancedPaymentQuoteDom) { assert.equal(argument, handle); return true; }
      calls.push('fixed_quote_collector'); return { schema: 'discogs_enhanced_checkout_dom_v1', status: 'observed', source: 'visible_enhanced_checkout_dom',
        purchase_allowed: false, seller_handle: 'ExampleSeller', visible_row_count: 1,
        displayed: { subtotal: { amount: '0.10', currency: 'USD' }, shipping: { amount: '4.95', currency: 'USD' }, total: { amount: '5.05', currency: 'USD' } },
        selected_shipping_label: 'Economy - Media Mail' };
    }, frames: () => [], mainFrame: () => ({}) };
  const coordinator = createEnhancedCheckoutCoordinator();
  const result = await coordinator.inspectPaymentReadiness(page); await waitForEnhancedCheckoutCoordinatorSettlement(page);
  assert.equal(result.quote.merchant_displayed.total.amount, '5.05'); assert.equal(result.quote.final_quote_verified, false);
  assert.equal(result.quote.amount_basis, 'merchant_aggregate_preparation_only'); assert.equal(result.payment.code, 'enhanced_payment_disabled');
  assert.ok(calls.includes('fixed_quote_collector')); assert.ok(calls.includes('dispose'));
});


test('packaged-layout non-test subprocess imports the exact dependency closure and keeps payment disabled without effects', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-coordinator-package-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const modules = [
    'discogs_enhanced_checkout_coordinator.mjs', 'discogs_enhanced_payment_quote.mjs',
    'discogs_guest_pay_ancestry.mjs',
    'discogs_enhanced_payment_adapter.mjs', 'discogs_enhanced_order_ledger_port.mjs',
    'discogs_enhanced_fixed_pay_port.mjs',
    'discogs_merchant_order_evidence.mjs', 'discogs_enhanced_checkout.mjs',
    'discogs_hosted_readonly.mjs', 'discogs_cart_inspector.mjs',
    'discogs_release_snapshot.mjs', 'discogs_listing_detail_snapshot.mjs',
  ];
  const copiedFiles = [...modules.map(name => `scripts/${name}`),
    'scripts/discogs_order_ledger_port.py', 'src/finder/discogs_order_reconciliation.py'];
  assert.equal(modules.length, 12);
  const dockerfile = await readFile(join(__dirname, '../deployment/HostedBrowserBridge.Dockerfile'), 'utf8');
  for (const relative of copiedFiles) assert.ok(dockerfile.includes(`COPY ${relative} /app/${relative}\n`), relative);
  for (const relative of copiedFiles) {
    const target = join(directory, relative);
    await mkdir(join(target, '..'), { recursive: true });
    await copyFile(join(__dirname, '..', relative), target);
  }
  const snapshot = async () => {
    const files = [];
    const walk = async relative => {
      for (const entry of await readdir(join(directory, relative), { withFileTypes: true })) {
        const path = relative ? `${relative}/${entry.name}` : entry.name;
        if (entry.isDirectory()) await walk(path);
        else { assert.equal(entry.isFile(), true); files.push([path,
          createHash('sha256').update(await readFile(join(directory, path))).digest('hex')]); }
      }
    };
    await walk(''); return files.sort(([a], [b]) => a.localeCompare(b));
  };
  const before = await snapshot();
  assert.deepEqual(before.map(([name]) => name).sort(), [...copiedFiles].sort());
  const source = `
    import assert from 'node:assert/strict';
    import childProcess from 'node:child_process';
    import net from 'node:net';
    import fs from 'node:fs/promises';
    import { syncBuiltinESMExports } from 'node:module';
    assert.equal(process.env.NODE_TEST_CONTEXT, undefined);
    let externalEffects = 0;
    const forbidden = () => { externalEffects++; throw new Error('forbidden_external_effect'); };
    for (const key of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) childProcess[key] = forbidden;
    for (const key of ['createServer', 'connect', 'createConnection']) net[key] = forbidden;
    for (const key of ['writeFile', 'appendFile', 'mkdir', 'unlink', 'rename', 'chmod', 'open']) fs[key] = forbidden;
    globalThis.fetch = forbidden;
    Object.defineProperty(globalThis, 'document', { get: forbidden });
    Object.defineProperty(globalThis, 'window', { get: forbidden });
    syncBuiltinESMExports();
    const module = await import(process.argv[1]);
    const coordinator = module.createEnhancedCheckoutCoordinator();
    assert.equal(coordinator.paymentEnabled, false);
    const result = await coordinator.attemptPayment({}, { enablePay: true, dispatchObservedPay: forbidden });
    assert.equal(result.code, 'enhanced_payment_disabled');
    assert.equal(result.purchase_allowed, false);
    assert.equal(result.may_submit_again, false);
    assert.throws(() => module.createEnhancedCheckoutCoordinator({ enablePay: true }), /fixed_production_coordinator_configuration_required/);
    assert.throws(() => module.createEnhancedCheckoutCoordinatorFixture({}), /fixture_context_required/);
    assert.equal(externalEffects, 0);
    console.log(JSON.stringify({ payment_code: result.code, payment_enabled: coordinator.paymentEnabled, external_effects: externalEffects }));
  `;
  const { stdout, stderr } = await promisify(execFile)(process.execPath,
    ['--input-type=module', '--eval', source, pathToFileURL(join(directory, 'scripts/discogs_enhanced_checkout_coordinator.mjs')).href],
    { cwd: directory, env: { PATH: process.env.PATH || '', TMPDIR: process.env.TMPDIR || tmpdir() }, timeout: 5000, maxBuffer: 8192 });
  assert.equal(stderr, '');
  assert.deepEqual(JSON.parse(stdout), { payment_code: 'enhanced_payment_disabled', payment_enabled: false, external_effects: 0 });
  assert.deepEqual(await snapshot(), before); // No ledger/journal/runtime/config or source mutation.
});

function observedPayMetadata(changes = {}) {
  return { schema: 'discogs_guest_pay_metadata_v1', status: 'observed', paypal_origin_count: 1,
    candidate_count: 1, associated_count: 1, eligible_count: 1, unsupported_count: 0, truncated: false,
    submit_count: 1, submit_visible: true, submit_enabled: true, button_type_matches: true,
    label: 'Pay $5.05', amount: '5.05', currency: null, location: 'paypal_child',
    same_document_verified: true, frame_associated: true, ...changes };
}

test('observed public Pay metadata is independent of missing final quote, delivery and dispatch bindings', async () => {
  let effects = 0;
  const coordinator = createEnhancedCheckoutCoordinatorFixture({ ledgerPort: {
    reconcile: async () => { effects++; throw Error('must not reconcile during readiness'); } },
  readMerchant: async () => { effects++; throw Error('no pre-submit receipt requirement'); },
  readQuote: async () => ({ schema: 'discogs_enhanced_payment_quote_v1', final_quote_verified: false,
    gates: { provider_dom_qualification: false, provider_frame_and_document: false,
      provider_delivery_binding: false, provider_terms: false }, guest_pay_metadata: observedPayMetadata() }) });
  const report = await coordinator.inspectPaymentReadiness({});
  assert.deepEqual(report.public_pay_control, { metadata_status: 'observed', enabled: true,
    dispatch_binding: 'unconfigured' });
  assert.equal(report.quote.guest_pay_metadata.currency, null);
  assert.equal(report.quote.final_quote_verified, false);
  assert.equal(report.missing_profiles.includes('verified_pay_control'), false);
  for (const key of ['provider_final_quote_dom', 'provider_delivery_destination',
    'bound_pay_dispatch_port', 'verified_selected_payment_method']) assert.ok(report.missing_profiles.includes(key));
  assert.equal(report.missing_profiles.includes('merchant_positive_order_dom'), false);
  assert.deepEqual(report.post_submit_verification, {
    phase: 'after_possible_submission', current_order_status: 'not_checked', current_payment_status: 'not_checked',
    receipt_source_qualification: 'unqualified', missing_profiles: ['merchant_positive_order_dom'],
    uncertain_outcome_policy: 'retain_submission_unknown_no_retry', automatic_retry_allowed: false });
  assert.equal(report.payment_allowed, false); assert.equal(report.final_submission_allowed, false);
  assert.equal(report.payment.code, 'enhanced_payment_disabled'); assert.equal(effects, 0);
});

test('disabled or malformed Pay metadata never satisfies the pre-submit control evidence gap', () => {
  const disabled = describeEnhancedCheckoutReadiness({ guest_pay_metadata: observedPayMetadata({ submit_enabled: false }) });
  assert.deepEqual(disabled.public_pay_control, { metadata_status: 'observed', enabled: false,
    dispatch_binding: 'unconfigured' });
  assert.ok(disabled.missing_profiles.includes('verified_pay_control'));
  for (const metadata of [undefined, null,
    observedPayMetadata({ submit_count: 2 }), observedPayMetadata({ frame_associated: false }),
    observedPayMetadata({ amount: '5.06' }), observedPayMetadata({ currency: 'USD' }),
    observedPayMetadata({ address: 'PRIVATE UNEXPECTED FIELD' })]) {
    const report = describeEnhancedCheckoutReadiness({ guest_pay_metadata: metadata });
    assert.equal(report.public_pay_control.metadata_status, 'unverified');
    assert.equal(report.public_pay_control.enabled, null);
    assert.ok(report.missing_profiles.includes('verified_pay_control'));
    assert.ok(report.missing_profiles.includes('bound_pay_dispatch_port'));
    assert.equal(JSON.stringify(report).includes('PRIVATE'), false);
  }
});

test('even asserted complete quote gates do not require a prior positive receipt or configure dispatch', () => {
  const gates = Object.fromEntries(['provider_dom_qualification', 'provider_frame_and_document', 'provider_explicit_currency',
    'provider_amount_matches_merchant', 'provider_final_all_in_total', 'final_tax_treatment', 'mandatory_charges_finality',
    'provider_shipping_selection', 'provider_delivery_binding', 'provider_terms'].map(key => [key, true]));
  const report = describeEnhancedCheckoutReadiness({ gates, final_quote_verified: true,
    guest_pay_metadata: observedPayMetadata() });
  assert.deepEqual(report.missing_evidence, []);
  assert.deepEqual(report.missing_profiles, ['bound_pay_dispatch_port', 'verified_selected_payment_method']);
  assert.deepEqual(report.post_submit_verification.missing_profiles, ['merchant_positive_order_dom']);
  assert.equal(report.public_pay_control.dispatch_binding, 'unconfigured');
  assert.equal(report.post_submit_verification.automatic_retry_allowed, false);
});

test('readiness derives missing evidence from strict gates, never from a claimed empty missing list', () => {
  const report = describeEnhancedCheckoutReadiness({ gates: { provider_dom_qualification: 'true' }, missing_evidence: [] });
  assert.ok(report.missing_evidence.includes('provider_dom_qualification'));
  assert.ok(report.missing_profiles.includes('provider_final_quote_dom'));
  assert.equal(report.missing_profiles.includes('merchant_positive_order_dom'), false);
});

test('aggregate-inclusive metadata remains immutable through the existing authoritative ledger fence', async t => {
  const f = await fixture(t);
  assert.equal((await f.port.initialize()).status, 'initialized');
  const quote = { ...f.quote, charge_basis: 'aggregate_inclusive', tax: null, fees: null, fees_absent_verified: false,
    tax_treatment: 'included_in_final_total', fees_treatment: 'included_in_final_total',
    deferred_charges: false, aggregate_total_verified: true };
  const fence = createBoundEnhancedOrderFencePort(f.scope, quote, { ledgerPort: f.port });
  for (const patch of [{ tax_treatment: 'unknown' }, { fees_treatment: 'unknown' },
    { deferred_charges: true }, { aggregate_total_verified: false }]) {
    const result = await fence.fencePossibleSubmit(f.scope.intent_id, { ...quote, ...patch }, f.scope);
    assert.equal(result.code, 'immutable_order_binding_changed');
  }
  assert.equal((await f.port.status()).ledger, null);
  const accepted = await fence.fencePossibleSubmit(f.scope.intent_id, quote, f.scope);
  assert.equal(accepted.state, 'submission_unknown'); assert.equal(accepted.durable, true);
  assert.equal((await f.port.status()).ledger.payment_state, 'unknown');
});
