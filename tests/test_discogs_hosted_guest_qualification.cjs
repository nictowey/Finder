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

test('runner supplies valid local policy callbacks to existing runtime and keeps absent policy disabled', async () => {
  for (const guestPreparation of [null, { authorize: () => null, accessGuard: () => false }]) {
    const signals = new EventEmitter(); let launchArgs;
    const close = Object.assign(async () => {}, { isAvailable: () => true, unavailable: new Promise(() => {}) });
    const result = runHostedBrowser({}, { signals, report: () => {}, reportError: () => {},
      loadQualification: async () => guestPreparation, startMemoryLogging: () => () => {},
      launch: async (...args) => { launchArgs = args; queueMicrotask(() => signals.emit('SIGTERM')); return close; } });
    assert.equal(await result, 0); assert.equal(launchArgs[3]?.guestPreparation ?? null, guestPreparation);
  }
});


test('valid expired policy disables qualification without rearming or preventing viewer launch', async t => {
  const f = await fixture(t); await f.write(); const issuer = await f.load(); assert.ok(await issuer.authorize(f.inspect));
  const prior = await readdir(f.directory); f.setTime(2800);
  assert.equal(await f.load(), null); assert.deepEqual(await readdir(f.directory), prior);
});
