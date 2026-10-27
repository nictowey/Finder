'use strict';
// Local owner policy only: no browser, provider requests, address or credentials.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, writeFile, readFile, chmod, rm, symlink, readdir } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { randomUUID } = require('node:crypto');
const { EventEmitter } = require('node:events');
let createHostedGuestQualificationFixture, runHostedBrowser;
before(async () => {
  ({ createHostedGuestQualificationFixture } = await import('../scripts/discogs_hosted_guest_qualification.mjs'));
  ({ runHostedBrowser } = await import('../scripts/sniper-browser-bridge-runner.mjs'));
});
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'guest-qualification-')); await chmod(directory, 0o700);
  t.after(() => rm(directory, { recursive: true, force: true }));
  let clock = 1000;
  const policyPath = join(directory, 'guest-qualification.json');
  const policy = { schema: 'discogs_guest_qualification_policy_v1', job_id: randomUUID(), approval_ref: randomUUID(),
    issued_at: 1000, expires_at: 2800, max_calls: 2, action: 'probe_guest_evidence',
    scope: { release_id: '3422100', listing_id: '1366950910', seller: 'Mailman21842', quantity: 1,
      currency: 'USD', max_total: '5.05', approved_destination_ref: 'saved_shipping_address' }, destination_ack: null };
  const write = () => writeFile(policyPath, JSON.stringify(policy), { mode: 0o600 });
  const load = () => createHostedGuestQualificationFixture({ policyPath, now: () => clock });
  const inspect = { schema: 'discogs_guest_readonly_probe_v1', job_id: policy.job_id, phase: 'inspect' };
  return { directory, policyPath, policy, write, load, inspect, setTime: value => { clock = value; } };
}

test('absent policy disables qualification; exact inspect grant carries no destination digest or transaction rights', async t => {
  const f = await fixture(t); assert.equal(await f.load(), null);
  await f.write(); const issuer = await f.load();
  assert.equal(await issuer.accessGuard('prepare_guest_form', f.inspect), false);
  assert.equal(await issuer.accessGuard('probe_guest_evidence', f.inspect), true);
  const grant = await issuer.authorize(f.inspect);
  assert.equal(grant.schema, 'discogs_guest_readonly_authorization_v1');
  assert.equal(grant.listing_id, '1366950910'); assert.equal(grant.max_total, '5.05');
  assert.equal(Object.hasOwn(grant, 'destination_ack'), false);
  assert.equal(Object.hasOwn(grant, 'approved_destination_digest'), false);
  assert.equal(Object.hasOwn(grant, 'payment_allowed'), false);
  assert.equal(await issuer.authorize(f.inspect), null);
});

test('two immutable slots survive restart and same-job visual acknowledgement update', async t => {
  const f = await fixture(t); await f.write(); let issuer = await f.load();
  assert.ok(await issuer.authorize(f.inspect)); f.setTime(1020);
  const documentRef = randomUUID();
  f.policy.destination_ack = { source: 'operator_visual_saved_destination_match', document_ref: documentRef,
    approved_destination_ref: 'saved_shipping_address', observed_at: 1020 };
  await f.write(); issuer = await f.load();
  assert.equal(await issuer.accessGuard('probe_guest_evidence', f.inspect), false);
  const confirm = { schema: f.inspect.schema, job_id: f.policy.job_id, phase: 'confirm_saved_destination', document_ref: documentRef };
  assert.equal(await issuer.authorize({ ...confirm, document_ref: randomUUID() }), null);
  const grant = await issuer.authorize(confirm);
  assert.equal(grant.destination_ack.document_ref, documentRef);
  assert.equal(await (await f.load()).authorize(confirm), null);
  const files = await readdir(f.directory);
  assert.equal(files.filter(file => /-(?:1-inspect|2-confirm)\.json$/u.test(file)).length, 2);
  for (const file of files) assert.equal((await readFile(join(f.directory, file), 'utf8')).includes('address digest'), false);
});

test('same-job policy rewrite cannot extend expiry, change scope, or reset budget', async t => {
  const f = await fixture(t); await f.write(); await f.load();
  f.policy.expires_at = 2801; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.expires_at = 2700; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.expires_at = 2800; f.policy.scope.max_total = '5.06'; await f.write();
  await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});

test('confirmation requires spent first inspect and fresh explicit visual attestation', async t => {
  const f = await fixture(t); f.policy.destination_ack = { source: 'operator_visual_saved_destination_match',
    document_ref: randomUUID(), approved_destination_ref: 'saved_shipping_address', observed_at: 1000 };
  await f.write(); const issuer = await f.load();
  const confirm = { schema: f.inspect.schema, job_id: f.policy.job_id, phase: 'confirm_saved_destination', document_ref: f.policy.destination_ack.document_ref };
  assert.equal(await issuer.authorize(confirm), null);
  f.policy.destination_ack.source = 'trust_current_page'; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.destination_ack.source = 'operator_visual_saved_destination_match'; await f.write(); f.setTime(1301);
  assert.equal(await issuer.authorize(confirm), null);
});

test('racing clients spend inspect only once; cancellation and revoked/expired policy fail closed', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load();
  const results = await Promise.all([issuer.authorize(f.inspect), issuer.authorize(f.inspect)]);
  assert.equal(results.filter(Boolean).length, 1);
  const cancelled = new AbortController(); cancelled.abort();
  assert.equal(await issuer.accessGuard('probe_guest_evidence', f.inspect, { signal: cancelled.signal }), false);
  assert.equal(await issuer.authorize(f.inspect, { signal: cancelled.signal }), null);
  await rm(f.policyPath); assert.equal(await issuer.accessGuard('probe_guest_evidence', f.inspect), false);
  await f.write(); f.setTime(2800); assert.equal(await issuer.authorize(f.inspect), null);
});

test('extra request fields, unsupported actions, broad modes and unsafe local files are rejected', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load();
  assert.equal(await issuer.authorize({ ...f.inspect, url: 'https://invalid/' }), null);
  assert.equal(await issuer.authorize({ ...f.inspect, job_id: randomUUID() }), null);
  f.policy.action = 'prepare_guest_form'; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.action = 'probe_guest_evidence'; f.policy.max_calls = 3; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.max_calls = 2; await f.write(); await chmod(f.policyPath, 0o644); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  await chmod(f.policyPath, 0o600); const target = join(f.directory, 'policy-target'); await writeFile(target, JSON.stringify(f.policy), { mode: 0o600 });
  await rm(f.policyPath); await symlink(target, f.policyPath); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});

test('truncated durable use marker or changed job identity never rearms a spent phase', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load(); assert.ok(await issuer.authorize(f.inspect));
  const slot = join(f.directory, `guest-qualification-${f.policy.job_id}-1-inspect.json`);
  await writeFile(slot, '{', { mode: 0o600 }); assert.equal(await (await f.load()).authorize(f.inspect), null);
  const identity = join(f.directory, `guest-qualification-${f.policy.job_id}-identity.json`);
  await writeFile(identity, '{', { mode: 0o600 }); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});

test('runner keeps a denied guest listener when startup policy is absent, then reloads a new policy without relaunch', async t => {
  const f = await fixture(t);
  const signals = new EventEmitter(); let launchArgs, launches = 0;
  const close = Object.assign(async () => {}, { isAvailable: () => true, unavailable: new Promise(() => {}) });
  const result = await runHostedBrowser({}, { signals, report: () => {}, reportError: () => {},
    loadQualification: f.load, startMemoryLogging: () => () => {},
    launch: async (...args) => {
      launches++; launchArgs = args;
      const guard = args[3].guestPreparation;
      assert.equal(await guard.accessGuard('probe_guest_evidence', f.inspect), false);
      assert.equal(await guard.authorize(f.inspect), null);
      await f.write();
      assert.equal(await guard.accessGuard('probe_guest_evidence', f.inspect), true);
      assert.ok(await guard.authorize(f.inspect));
      assert.equal(await guard.authorize(f.inspect), null);
      queueMicrotask(() => signals.emit('SIGTERM'));
      return close;
    } });
  assert.equal(result, 0); assert.equal(launches, 1);
  assert.equal(launchArgs.length, 4);
  assert.deepEqual(Object.keys(launchArgs[3]), ['guestPreparation']);
});

test('runner starts with expired policy denied and accepts only a fresh reviewed job later', async t => {
  const f = await fixture(t); await f.write();
  assert.ok(await (await f.load()).authorize(f.inspect));
  f.setTime(2800); assert.equal(await f.load(), null);
  const signals = new EventEmitter(); let launches = 0;
  const close = Object.assign(async () => {}, { isAvailable: () => true, unavailable: new Promise(() => {}) });
  const result = await runHostedBrowser({}, { signals, report: () => {}, reportError: () => {},
    loadQualification: f.load, startMemoryLogging: () => () => {},
    launch: async (_firefox, _profile, _socket, { guestPreparation }) => {
      launches++;
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', f.inspect), false);
      assert.equal(await guestPreparation.authorize(f.inspect), null);
      f.policy.job_id = randomUUID(); f.policy.issued_at = 2800; f.policy.expires_at = 3000;
      await f.write();
      const next = { ...f.inspect, job_id: f.policy.job_id };
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', f.inspect), false);
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', next), true);
      assert.ok(await guestPreparation.authorize(next));
      assert.equal(await guestPreparation.authorize(next), null);
      queueMicrotask(() => signals.emit('SIGTERM'));
      return close;
    } });
  assert.equal(result, 0); assert.equal(launches, 1);
});

test('malformed nonnull startup issuer never launches a browser or socket', async () => {
  for (const invalid of [undefined, {}, { accessGuard: async () => true },
    { authorize: async () => null }]) {
    const signals = new EventEmitter(); let launches = 0, errors = 0;
    const result = await runHostedBrowser({}, { signals, report: () => {},
      reportError: () => { errors++; }, loadQualification: async () => invalid,
      startMemoryLogging: () => () => {}, launch: async () => { launches++; throw new Error('unexpected_launch'); } });
    assert.equal(result, 1); assert.equal(launches, 0); assert.equal(errors, 1);
  }
});

test('runner reloads each request on one browser: new reviewed job works; changed, revoked and expired policy deny', async t => {
  const f = await fixture(t); await f.write();
  const signals = new EventEmitter(); let launches = 0;
  const close = Object.assign(async () => {}, { isAvailable: () => true, unavailable: new Promise(() => {}) });
  const result = await runHostedBrowser({}, { signals, report: () => {}, reportError: () => {},
    loadQualification: f.load, startMemoryLogging: () => () => {},
    launch: async (_firefox, _profile, _socket, { guestPreparation }) => {
      launches++;
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', f.inspect), true);
      assert.ok(await guestPreparation.authorize(f.inspect));
      assert.equal(await guestPreparation.authorize(f.inspect), null); // Spent old receipt.
      f.policy.job_id = randomUUID(); await f.write();
      const next = { ...f.inspect, job_id: f.policy.job_id };
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', f.inspect), false);
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', next), true);
      f.policy.expires_at = 2801; await f.write(); // Same-job identity mutation.
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', next), false);
      assert.equal(await guestPreparation.authorize(next), null);
      f.policy.expires_at = 2800; await f.write();
      assert.ok(await guestPreparation.authorize(next));
      assert.equal(await guestPreparation.authorize(next), null);
      await rm(f.policyPath); // Revocation cannot reuse either receipt.
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', next), false);
      assert.equal(await guestPreparation.authorize(next), null);
      await f.write(); f.setTime(2800);
      assert.equal(await guestPreparation.accessGuard('probe_guest_evidence', next), false);
      assert.equal(await guestPreparation.authorize(next), null);
      queueMicrotask(() => signals.emit('SIGTERM'));
      return close;
    } });
  assert.equal(result, 0); assert.equal(launches, 1);
});


test('valid expired policy disables qualification without rearming or preventing viewer launch', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load(); assert.ok(await issuer.authorize(f.inspect));
  const prior = await readdir(f.directory); f.setTime(2800);
  assert.equal(await f.load(), null); assert.deepEqual(await readdir(f.directory), prior);
});

test('separate cart capture policy admits only one exact read and survives restart/races without old job reuse', async t => {
  const f = await fixture(t); await f.write(); const first = await f.load(); assert.ok(await first.authorize(f.inspect));
  const priorFiles = await readdir(f.directory);
  f.policy.schema = 'discogs_guest_cart_capture_policy_v1'; f.policy.max_calls = 1; await f.write();
  await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.job_id = randomUUID(); await f.write(); const issuer = await f.load();
  const capture = { schema: f.inspect.schema, job_id: f.policy.job_id, phase: 'capture_cart_structure' };
  assert.equal(await issuer.authorize({ ...capture, phase: 'inspect' }), null);
  assert.equal(await issuer.authorize({ ...capture, phase: 'confirm_saved_destination', document_ref: randomUUID() }), null);
  assert.equal(await issuer.accessGuard('prepare_guest_form', capture), false);
  const controller = new AbortController(); controller.abort();
  assert.equal(await issuer.authorize(capture, { signal: controller.signal }), null);
  const attempts = await Promise.all([issuer.authorize(capture), issuer.authorize(capture)]);
  const grant = attempts.find(Boolean); assert.equal(attempts.filter(Boolean).length, 1);
  assert.equal(grant.mode, 'capture_cart_structure'); assert.equal(grant.listing_id, '1366950910');
  assert.equal(Object.hasOwn(grant, 'destination_ack'), false);
  assert.equal(await (await f.load()).authorize(capture), null);
  const after = await readdir(f.directory); assert.ok(priorFiles.every(file => after.includes(file)));
  f.policy.max_calls = 2; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.max_calls = 1; f.policy.destination_ack = { source: 'operator_visual_saved_destination_match', document_ref: randomUUID(),
    approved_destination_ref: 'saved_shipping_address', observed_at: 1000 };
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});

async function preflightFixture(t) {
  const f = await fixture(t);
  f.policy.schema = 'discogs_guest_cart_preflight_policy_v1'; f.policy.action = 'cart_checkout_preflight';
  f.policy.max_calls = 3; f.policy.expected_ledger_head = 'a'.repeat(64);
  await f.write(); const issuer = await f.load(); const documentRef = randomUUID();
  const nav = { schema: 'discogs_guest_checkout_preflight_v1', job_id: f.policy.job_id,
    phase: 'navigate_checkout_preflight', document_ref: documentRef };
  const confirm = { schema: f.inspect.schema, job_id: f.policy.job_id, phase: 'confirm_saved_destination', document_ref: documentRef };
  const acknowledge = async () => {
    f.setTime(1020); f.policy.destination_ack = { source: 'operator_visual_saved_destination_match', document_ref: documentRef,
      approved_destination_ref: 'saved_shipping_address', observed_at: 1020 }; await f.write();
  };
  return { ...f, issuer, nav, confirm, acknowledge };
}

test('new preflight job requires ordered inspect and exact acknowledged confirmation before its one durable navigation authorization', async t => {
  const f = await preflightFixture(t);
  assert.equal(await f.issuer.authorize(f.nav), null);
  assert.equal(await f.issuer.accessGuard('cart_checkout_preflight', f.inspect), false);
  assert.equal(await f.issuer.accessGuard('probe_guest_evidence', f.inspect), true);
  const inspect = await f.issuer.authorize(f.inspect);
  assert.equal(inspect.schema, 'discogs_guest_readonly_authorization_v1'); assert.equal(Object.hasOwn(inspect, 'mode'), false);
  await f.acknowledge(); assert.equal(await f.issuer.authorize(f.nav), null);
  const confirm = await f.issuer.authorize(f.confirm);
  assert.equal(confirm.schema, 'discogs_guest_readonly_authorization_v1');
  assert.equal(await f.issuer.accessGuard('probe_guest_evidence', f.nav), false);
  assert.equal(await f.issuer.accessGuard('prepare_guest_form', f.nav), false);
  assert.equal(await f.issuer.accessGuard('cart_checkout_preflight', f.nav), true);
  const grants = await Promise.all([f.issuer.authorize(f.nav), f.issuer.authorize(f.nav)]);
  assert.equal(grants.filter(Boolean).length, 1);
  const grant = grants.find(Boolean);
  assert.equal(grant.schema, 'discogs_guest_preflight_authorization_v1'); assert.equal(grant.mode, 'cart_checkout_preflight');
  assert.equal(grant.expected_ledger_head, f.policy.expected_ledger_head);
  assert.equal(Object.hasOwn(grant, 'priorFailure'), false); assert.equal(Object.hasOwn(grant, 'testAuthorization'), false);
  assert.equal(await (await f.load()).authorize(f.nav), null);
  const receipt = JSON.parse(await readFile(join(f.directory, `guest-qualification-${f.policy.job_id}-4-checkout-preflight.json`), 'utf8'));
  assert.equal(receipt.document_ref, f.nav.document_ref); assert.equal(receipt.destination_ack_observed_at, 1020);
});

test('preflight confirmation pins exact acknowledgement; changed ack, cancellation, expiry and broad request cannot authorize navigation', async t => {
  const f = await preflightFixture(t); assert.ok(await f.issuer.authorize(f.inspect)); await f.acknowledge();
  assert.ok(await f.issuer.authorize(f.confirm));
  assert.equal(await f.issuer.authorize({ ...f.nav, priorFailure: {} }), null);
  assert.equal(await f.issuer.authorize({ ...f.nav, document_ref: randomUUID() }), null);
  const original = { ...f.policy.destination_ack }; f.policy.destination_ack.document_ref = randomUUID(); await f.write();
  assert.equal(await f.issuer.authorize({ ...f.nav, document_ref: f.policy.destination_ack.document_ref }), null);
  f.policy.destination_ack = original; await f.write();
  const controller = new AbortController(); controller.abort(); assert.equal(await f.issuer.authorize(f.nav, { signal: controller.signal }), null);
  f.setTime(1081); assert.equal(await f.issuer.authorize(f.nav), null);
  const files = await readdir(f.directory); assert.equal(files.some(file => file.endsWith('-4-checkout-preflight.json')), false);
});

test('preflight ledger head and mode are immutable, and older read-only/capture policies cannot authorize navigation', async t => {
  const f = await preflightFixture(t);
  f.policy.expected_ledger_head = 'b'.repeat(64); await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  delete f.policy.expected_ledger_head; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  const old = await fixture(t); await old.write(); const oldIssuer = await old.load();
  assert.equal(await oldIssuer.authorize({ ...f.nav, job_id: old.policy.job_id }), null);
  assert.equal(await oldIssuer.accessGuard('cart_checkout_preflight', { ...f.nav, job_id: old.policy.job_id }), false);
  old.policy.schema = 'discogs_guest_cart_capture_policy_v1'; old.policy.job_id = randomUUID(); old.policy.max_calls = 1; await old.write();
  assert.equal(await (await old.load()).authorize({ ...f.nav, job_id: old.policy.job_id }), null);
});

async function prepareFixture(t) {
  const f = await fixture(t);
  f.policy.schema = 'discogs_guest_prepare_policy_v1'; f.policy.action = 'prepare_guest_form';
  f.policy.max_calls = 3; f.policy.expires_at = 1200;
  Object.assign(f.policy, {
    expected_ledger_head: 'a'.repeat(64), intent_id: randomUUID(), ready_ref: randomUUID(),
    destination_ref: randomUUID(), authorized_at: 850, prior_form_disposition_sha256: 'b'.repeat(64),
    prior_failure: { source: 'operator_reviewed_normal_discogs_ui', last_error_code: 'SESSION_EXPIRED',
      release_id: Number(f.policy.scope.release_id), listing_id: f.policy.scope.listing_id,
      seller: f.policy.scope.seller, quantity: f.policy.scope.quantity,
      currency: f.policy.scope.currency, max_total: f.policy.scope.max_total,
      card_data_entered: false, pay_invoked: false, approval_invoked: false, capture_invoked: false,
      failure_observed_at: 900, order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50',
      order_history_archived: 'All', order_history_status: 'All', order_history_count: 0,
      order_history_complete: true, order_history_observed_at: 950, reviewed_at: 980, expires_at: 1220 },
  });
  await f.write(); const issuer = await f.load(); const documentRef = randomUUID();
  const prepare = { schema: 'discogs_guest_prepare_request_v1', job_id: f.policy.job_id,
    phase: 'prepare_guest_form', document_ref: documentRef };
  const confirm = { schema: f.inspect.schema, job_id: f.policy.job_id,
    phase: 'confirm_saved_destination', document_ref: documentRef };
  const acknowledge = async () => {
    f.setTime(1020); f.policy.destination_ack = { source: 'operator_visual_saved_destination_match',
      document_ref: documentRef, approved_destination_ref: 'saved_shipping_address', observed_at: 1020 };
    await f.write();
  };
  return { ...f, issuer, prepare, confirm, acknowledge };
}

test('preparation is default off and requires the ordered same-document inspect and confirmation before one durable receipt', async t => {
  const f = await prepareFixture(t);
  assert.ok(f.policy.authorized_at < f.policy.prior_failure.reviewed_at);
  assert.equal(typeof f.policy.prior_failure.release_id, 'number');
  assert.equal(await f.issuer.accessGuard('prepare_guest_form', f.prepare), false);
  assert.equal(await f.issuer.authorize(f.prepare), null);
  assert.equal(await f.issuer.authorize({ ...f.prepare, quote_hash: 'c'.repeat(64) }), null);
  assert.ok(await f.issuer.authorize(f.inspect)); await f.acknowledge();
  assert.equal(await f.issuer.authorize(f.prepare), null);
  assert.ok(await f.issuer.authorize(f.confirm));
  assert.equal(await f.issuer.authorize({ ...f.prepare, document_ref: randomUUID() }), null);
  assert.equal(await f.issuer.accessGuard('prepare_guest_form', f.prepare), true);
  const results = await Promise.all([f.issuer.authorize(f.prepare), f.issuer.authorize(f.prepare)]);
  assert.equal(results.filter(Boolean).length, 1);
  const grant = results.find(Boolean);
  assert.equal(grant.schema, 'discogs_guest_prepare_authorization_v1');
  assert.equal(grant.mode, 'prepare_guest_form'); assert.equal(grant.intent_id, f.policy.intent_id);
  assert.equal(grant.approval_ref, f.policy.approval_ref);
  assert.equal(grant.destination_ack.document_ref, f.prepare.document_ref);
  for (const key of ['approved_destination_digest', 'destination_digest', 'quote_hash', 'raw_address', 'card_data']) {
    assert.equal(Object.hasOwn(grant, key), false);
  }
  assert.equal(typeof grant.revalidate, 'function'); assert.equal(await grant.revalidate(), true);
  assert.equal(await (await f.load()).authorize(f.prepare), null);
  const receipt = JSON.parse(await readFile(join(f.directory, `guest-qualification-${f.policy.job_id}-5-prepare.json`), 'utf8'));
  assert.equal(receipt.document_ref, f.prepare.document_ref);
  assert.equal(receipt.destination_ack_observed_at, 1020);
});

test('prepare grant revalidation rejects cancellation, expiry, revocation and changed acknowledgement after consumption', async t => {
  const f = await prepareFixture(t); assert.ok(await f.issuer.authorize(f.inspect)); await f.acknowledge();
  assert.ok(await f.issuer.authorize(f.confirm)); const grant = await f.issuer.authorize(f.prepare);
  assert.ok(grant); assert.equal(await grant.revalidate(), true);
  const cancelled = new AbortController(); cancelled.abort();
  assert.equal(await grant.revalidate({ signal: cancelled.signal }), false);
  f.policy.destination_ack.observed_at = 1021; await f.write();
  assert.equal(await grant.revalidate(), false);
  f.policy.destination_ack.observed_at = 1020; await f.write();
  assert.equal(await grant.revalidate(), true);
  await rm(f.policyPath); assert.equal(await grant.revalidate(), false);
  await f.write(); f.setTime(1200); assert.equal(await grant.revalidate(), false);
});

test('prepare policy pins every reconciliation field and rejects stale or invented failure evidence', async t => {
  const f = await prepareFixture(t);
  const original = structuredClone(f.policy);
  const changes = [
    ['expected_ledger_head', 'c'.repeat(64)], ['intent_id', randomUUID()], ['ready_ref', randomUUID()],
    ['destination_ref', randomUUID()], ['authorized_at', 849], ['prior_form_disposition_sha256', 'd'.repeat(64)],
    ['prior_failure', { ...original.prior_failure, order_history_count: 1 }],
  ];
  for (const [key, value] of changes) {
    f.policy[key] = value; await f.write();
    if (key === 'prior_failure') await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
    else {
      // New identity cannot replace an existing same-job identity, even when structurally valid.
      await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
    }
    f.policy[key] = original[key];
  }
  f.policy.prior_failure.pay_invoked = true; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.prior_failure = structuredClone(original.prior_failure);
  f.policy.prior_failure.release_id = String(original.prior_failure.release_id);
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.prior_failure = structuredClone(original.prior_failure);
  f.policy.expires_at = 1250; await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.expires_at = 1200; f.policy.prior_failure.order_history_observed_at = 899;
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});

test('ordinary and preflight policies cannot authorize preparation, and a consumed or cancelled attempt never rearms', async t => {
  const old = await fixture(t); await old.write(); const ordinary = await old.load();
  const wrong = { schema: 'discogs_guest_prepare_request_v1', job_id: old.policy.job_id,
    phase: 'prepare_guest_form', document_ref: randomUUID() };
  assert.equal(await ordinary.accessGuard('prepare_guest_form', wrong), false);
  assert.equal(await ordinary.authorize(wrong), null);
  const f = await prepareFixture(t); assert.ok(await f.issuer.authorize(f.inspect)); await f.acknowledge();
  assert.ok(await f.issuer.authorize(f.confirm));
  const cancelled = new AbortController(); cancelled.abort();
  assert.equal(await f.issuer.authorize(f.prepare, { signal: cancelled.signal }), null);
  assert.ok(await f.issuer.authorize(f.prepare));
  assert.equal(await f.issuer.authorize(f.prepare), null);
  assert.equal(await (await f.load()).authorize(f.prepare), null);
});

async function paymentInspectionFixture(t) {
  const f = await fixture(t);
  f.policy.schema = 'discogs_guest_payment_inspection_policy_v1';
  f.policy.action = 'inspect_enhanced_payment'; f.policy.max_calls = 2;
  f.policy.expires_at = 1200; f.policy.expected_ledger_head = 'c'.repeat(64);
  f.policy.claim_sha256 = 'd'.repeat(64);
  await f.write();
  const issuer = await f.load();
  const request = { schema: 'discogs_guest_payment_inspection_v1', job_id: f.policy.job_id,
    phase: 'inspect_payment_readiness', document_ref: randomUUID(), claim_sha256: f.policy.claim_sha256 };
  return { ...f, issuer, request };
}

test('distinct payment inspection policy issues standard read-only inspect, then one exact durable payment-read grant', async t => {
  const f = await paymentInspectionFixture(t);
  assert.equal(await f.issuer.authorize(f.request), null);
  assert.equal(await f.issuer.accessGuard('inspect_enhanced_payment', f.request), false);
  const initial = await f.issuer.authorize(f.inspect);
  assert.deepEqual(Object.keys(initial).sort(), ['schema', 'job_id', 'issued_at', 'expires_at',
    'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
    'approved_destination_ref'].sort());
  assert.equal(initial.schema, 'discogs_guest_readonly_authorization_v1');
  assert.equal(await f.issuer.accessGuard('probe_guest_evidence', f.request), false);
  assert.equal(await f.issuer.accessGuard('inspect_enhanced_payment', f.request), true);
  const attempts = await Promise.all([f.issuer.authorize(f.request), f.issuer.authorize(f.request)]);
  assert.equal(attempts.filter(Boolean).length, 1);
  const grant = attempts.find(Boolean);
  assert.equal(grant.schema, 'discogs_guest_payment_inspection_authorization_v1');
  assert.equal(grant.mode, 'inspect_enhanced_payment');
  assert.equal(grant.expected_ledger_head, f.policy.expected_ledger_head);
  assert.equal(grant.claim_sha256, f.policy.claim_sha256);
  assert.equal(Object.hasOwn(grant, 'destination_ack'), false);
  for (const key of ['payment_allowed', 'purchase_allowed', 'approval_ref', 'approved_destination_digest']) {
    assert.equal(Object.hasOwn(grant, key), false);
  }
  assert.equal(await grant.revalidate(), true);
  assert.equal(await (await f.load()).authorize(f.request), null);
  const receipt = JSON.parse(await readFile(join(f.directory,
    `guest-qualification-${f.policy.job_id}-6-payment-inspection.json`), 'utf8'));
  assert.equal(receipt.document_ref, f.request.document_ref);
  assert.equal(receipt.claim_sha256, f.request.claim_sha256);
});

test('payment inspection rejects malformed claim/document, cancellation, replay and changed receipt', async t => {
  const f = await paymentInspectionFixture(t); assert.ok(await f.issuer.authorize(f.inspect));
  assert.equal(await f.issuer.authorize({ ...f.request, claim_sha256: 'e'.repeat(64) }), null);
  assert.equal(await f.issuer.authorize({ ...f.request, document_ref: 'not-a-uuid' }), null);
  assert.equal(await f.issuer.authorize({ ...f.request, arbitrary: true }), null);
  const controller = new AbortController(); controller.abort();
  assert.equal(await f.issuer.authorize(f.request, { signal: controller.signal }), null);
  const grant = await f.issuer.authorize(f.request); assert.ok(grant);
  assert.equal(await f.issuer.authorize({ ...f.request, document_ref: randomUUID() }), null);
  assert.equal(await grant.revalidate({ signal: controller.signal }), false);
  const slot = join(f.directory, `guest-qualification-${f.policy.job_id}-6-payment-inspection.json`);
  const receipt = JSON.parse(await readFile(slot, 'utf8'));
  await writeFile(slot, JSON.stringify({ ...receipt, document_ref: randomUUID() }), { mode: 0o600 });
  assert.equal(await grant.revalidate(), false);
});

test('payment inspection head/claim and null acknowledgement are immutable; revocation and expiry stop revalidation', async t => {
  const f = await paymentInspectionFixture(t); assert.ok(await f.issuer.authorize(f.inspect));
  f.policy.claim_sha256 = 'e'.repeat(64); await f.write();
  await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.claim_sha256 = 'd'.repeat(64); f.policy.expected_ledger_head = 'e'.repeat(64);
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.expected_ledger_head = 'c'.repeat(64);
  f.policy.destination_ack = { source: 'operator_visual_saved_destination_match',
    document_ref: randomUUID(), approved_destination_ref: 'saved_shipping_address', observed_at: 1000 };
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
  f.policy.destination_ack = null; await f.write();
  const grant = await f.issuer.authorize(f.request); assert.ok(grant);
  await rm(f.policyPath); assert.equal(await grant.revalidate(), false);
  await f.write(); f.setTime(1200); assert.equal(await grant.revalidate(), false);
});

test('older policies cannot gain payment inspection by changing phase or action', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load();
  const request = { schema: 'discogs_guest_payment_inspection_v1', job_id: f.policy.job_id,
    phase: 'inspect_payment_readiness', document_ref: randomUUID(), claim_sha256: 'd'.repeat(64) };
  assert.equal(await issuer.accessGuard('inspect_enhanced_payment', request), false);
  assert.equal(await issuer.authorize(request), null);
  f.policy.schema = 'discogs_guest_payment_inspection_policy_v1';
  f.policy.action = 'inspect_enhanced_payment'; f.policy.expires_at = 1200;
  f.policy.expected_ledger_head = 'c'.repeat(64); f.policy.claim_sha256 = 'd'.repeat(64);
  await f.write(); await assert.rejects(f.load(), /invalid_guest_qualification_policy/);
});
