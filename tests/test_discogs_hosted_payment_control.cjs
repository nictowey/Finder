'use strict';
// Real local Unix IPC with isolated in-memory runtime only. No browser or HTTP.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { mkdtemp, chmod, chown, writeFile, readFile, lstat, unlink, rm, symlink, link, rename } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { join } = require('node:path');
let listenHostedPaymentControl, listenHostedPaymentControlFixture, loadHostedPaymentModeFixture,
  sanitizeHostedPaymentControlRequest, sanitizeHostedPaymentControlResult;
before(async () => ({ listenHostedPaymentControl, listenHostedPaymentControlFixture, loadHostedPaymentModeFixture,
  sanitizeHostedPaymentControlRequest, sanitizeHostedPaymentControlResult } = await import('../scripts/discogs_hosted_payment_control.mjs')));
const off = { entryGate: false, liveGate: false, controlEnabled: false };
function entryScope() { return { schema: 'discogs_secure_entry_scope_v1', release_id: 3422100, listing_id: '1366950910',
  seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05', expected_guest_head: 'a'.repeat(64),
  expected_claim_sha256: 'b'.repeat(64), expires_at: Date.now() / 1000 + 120 }; }
function payScope() { return { intent_id: randomUUID(), approval_ref: randomUUID(), release_id: 3422100,
  listing_id: '1366950910', seller: 'Mailman21842', one_copy_limit: 1, currency: 'USD', max_total: '5.05',
  rules_revision: 1, quote_fingerprint: 'a'.repeat(64), destination_ref: randomUUID(), payment_ref: randomUUID(),
  terms_fingerprint: 'b'.repeat(64), expires_at: Date.now() / 1000 + 120 }; }
const message = (command = 'inspect_readiness', request = { scope: entryScope() }) => ({ schema: 'discogs_hosted_payment_control_request_v1', command, request });
const wire = (command, request) => JSON.stringify(message(command, request)) + '\n';
function runtime(overrides = {}) {
  return { inspectEntryReadiness: async () => ({ status: 'observed', code: 'anchored_guest_claim_observed', browser_evidence_pending: true }),
    inspectReadiness: async () => ({ status: 'blocked', code: 'hosted_payment_disabled' }),
    beginSecureEntry: async () => ({ status: 'entry_ready', entry_ref: randomUUID(), entry_started_at: Date.now() / 1000,
      reads_suspended: true, payment_allowed: false }),
    completeSecureEntry: async value => ({ status: 'observed', code: 'secure_entry_completed', entry_ref: value.entry_ref,
      entry_started_at: value.completed_at - 1, entry_completed_at: value.completed_at }),
    resumeAfterEntry: async () => ({ status: 'blocked', code: 'hosted_payment_disabled' }),
    attemptPayment: async () => ({ status: 'blocked', code: 'hosted_payment_disabled' }), ...overrides };
}
async function directory() { const dir = await mkdtemp('/tmp/sniper-pctrl-'); await chmod(dir, 0o700); return dir; }
function exchange(path, data) {
  return new Promise((resolve, reject) => { let output = ''; const client = connect(path);
    client.on('connect', () => client.write(data)); client.on('error', reject); client.on('data', chunk => { output += chunk; });
    client.on('end', () => { try { resolve(JSON.parse(output)); } catch (error) { reject(error); } }); });
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

test('default disabled listener touches no runtime or socket and fixed production paths cannot be overridden', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock');
  try {
    const unseen = new Proxy({}, { get() { throw new Error('Disabled runtime must not be inspected'); } });
    const close = await listenHostedPaymentControl(unseen, path); await close(); await assert.rejects(lstat(path), { code: 'ENOENT' });
    await assert.rejects(listenHostedPaymentControl(runtime(), path, { enabled: true }), /fixed_payment_control_socket_required/);
    await assert.rejects(listenHostedPaymentControl(runtime(), undefined, { enabled: true, liveGate: true }), /fixed_payment_control_configuration_required/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('fixed mode loader fails closed and accepts only private exact three-state metadata', async () => {
  const dir = await directory(), path = join(dir, 'mode.json');
  const put = async value => { await writeFile(path, JSON.stringify(value), { mode: 0o600 }); await chmod(path, 0o600); };
  try {
    assert.deepEqual(await loadHostedPaymentModeFixture(path), off);
    for (const mode of ['off', 'secure_entry', 'payment']) {
      await put({ schema: 'discogs_hosted_payment_mode_v1', mode });
      assert.deepEqual(await loadHostedPaymentModeFixture(path), { entryGate: mode !== 'off', liveGate: mode === 'payment', controlEnabled: mode !== 'off' });
    }
    for (const value of [{ schema: 'discogs_hosted_payment_mode_v1', mode: 'payment', enablePay: true }, { schema: 'wrong', mode: 'payment' }, { schema: 'discogs_hosted_payment_mode_v1', mode: 'PUBLIC_SECRET' }]) {
      await put(value); assert.deepEqual(await loadHostedPaymentModeFixture(path), off);
    }
    await writeFile(path, 'x'.repeat(257)); assert.deepEqual(await loadHostedPaymentModeFixture(path), off);
    await put({ schema: 'discogs_hosted_payment_mode_v1', mode: 'secure_entry' }); await chmod(path, 0o644);
    assert.deepEqual(await loadHostedPaymentModeFixture(path), off); await chmod(path, 0o600);
    const hard = join(dir, 'hard'); await link(path, hard); assert.deepEqual(await loadHostedPaymentModeFixture(path), off); await unlink(hard);
    const target = join(dir, 'target'); await rename(path, target); await symlink(target, path); assert.deepEqual(await loadHostedPaymentModeFixture(path), off); await unlink(path);
    await rename(target, path); await chmod(dir, 0o755); assert.deepEqual(await loadHostedPaymentModeFixture(path), off); await chmod(dir, 0o700);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('socket accepts the exact existing2710 parent without changing it while mode-file parent stays0700', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), mode = join(dir, 'mode.json');
  try {
    // /tmp may inherit a group not held by this macOS test user. Set only
    // this disposable fixture to the current primary group before setgid.
    await chown(dir, process.getuid(), process.getgid());
    await chmod(dir, 0o2710); assert.equal((await lstat(dir)).mode & 0o7777, 0o2710);
    const close = await listenHostedPaymentControlFixture(runtime(), path, { enabled: true });
    try {
      assert.equal((await lstat(dir)).mode & 0o7777, 0o2710); assert.equal((await lstat(path)).mode & 0o7777, 0o600);
      assert.equal((await exchange(path, wire())).code, 'anchored_guest_claim_observed');
    } finally { await close(); }
    await writeFile(mode, JSON.stringify({ schema: 'discogs_hosted_payment_mode_v1', mode: 'secure_entry' }), { mode: 0o600 });
    assert.deepEqual(await loadHostedPaymentModeFixture(mode), off); //2710 exception is socket-only.
    for (const permissions of [0o2770, 0o2740, 0o2705, 0o2702, 0o775, 0o710]) {
      await chmod(dir, permissions);
      await assert.rejects(listenHostedPaymentControlFixture(runtime(), path, { enabled: true }), /owner_private_payment_directory_required/);
      await assert.rejects(lstat(path), { code: 'ENOENT' });
    }
  } finally { await chmod(dir, 0o700); await rm(dir, { recursive: true, force: true }); }
});

test('strict schemas reject inputs, credentials, enable flags, arbitrary paths and changed KC scope before invocation', async () => {
  const entry = message(), pay = message('attempt_payment', { scope: payScope() });
  assert.ok(sanitizeHostedPaymentControlRequest(entry)); assert.ok(sanitizeHostedPaymentControlRequest(pay));
  for (const mutate of [value => { value.url = 'https://private/'; }, value => { value.command = 'evaluate'; }, value => { value.request.cardNumber = 'SECRET'; },
    value => { value.request.scope.enablePay = true; }, value => { value.request.scope.seller = 'wrong'; }, value => { value.request.scope.quantity = 2; },
    value => { value.request.scope.currency = 'CAD'; }, value => { value.request.scope.max_total = '5.06'; }, value => { value.request.scope.expected_guest_head = 'bad'; },
    value => { value.request.scope.expires_at = Date.now() / 1000 + 301; }]) {
    const value = structuredClone(entry); mutate(value); assert.equal(sanitizeHostedPaymentControlRequest(value), null);
  }
  assert.equal(sanitizeHostedPaymentControlRequest(message('attempt_payment', entry.request)), null);
  assert.equal(sanitizeHostedPaymentControlRequest(message('begin_secure_entry', pay.request)), null);
  const getter = structuredClone(entry); Object.defineProperty(getter, 'command', { enumerable: true, get() { throw new Error('Getter forbidden'); } });
  assert.equal(sanitizeHostedPaymentControlRequest(getter), null);
});

test('owner-only socket dispatches only fixed runtime methods and projects public scalar results', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), calls = [], ref = randomUUID();
  const r = runtime({ inspectEntryReadiness: async (scope, options) => { calls.push('entry_inspect'); assert.ok(options.signal); assert.equal(scope.listing_id, '1366950910');
    return { status: 'observed', code: 'anchored_guest_claim_observed', browser_evidence_pending: true, address: 'TOP_SECRET', token: 'TOP_SECRET' }; },
    inspectReadiness: async () => { calls.push('pay_inspect'); return { status: 'blocked', code: 'hosted_payment_disabled' }; },
    beginSecureEntry: async () => { calls.push('begin'); return { status: 'entry_ready', entry_ref: ref, entry_started_at: Date.now() / 1000,
      reads_suspended: true, payment_allowed: false, raw_html: 'TOP_SECRET', input: 'TOP_SECRET' }; },
    completeSecureEntry: async value => { calls.push('complete'); return { status: 'observed', code: 'secure_entry_completed', entry_ref: value.entry_ref,
      entry_started_at: value.completed_at - 1, entry_completed_at: value.completed_at }; },
    resumeAfterEntry: async () => { calls.push('resume'); return { status: 'blocked', code: 'hosted_payment_disabled' }; },
    attemptPayment: async () => { calls.push('attempt'); return { status: 'blocked', code: 'hosted_payment_disabled' }; } });
  const close = await listenHostedPaymentControlFixture(r, path, { enabled: true });
  try {
    assert.equal((await lstat(path)).mode & 0o7777, 0o600);
    assert.equal((await exchange(path, wire('inspect_readiness', { scope: entryScope() }))).code, 'anchored_guest_claim_observed');
    await exchange(path, wire('inspect_readiness', { scope: payScope() }));
    const handoff = await exchange(path, wire('begin_secure_entry', { scope: entryScope() }));
    assert.equal(handoff.status, 'entry_ready'); assert.equal(handoff.entry_ref, ref); assert.equal(handoff.reads_suspended, true);
    assert.equal(JSON.stringify(handoff).includes('TOP_SECRET'), false);
    await exchange(path, wire('complete_secure_entry', { entry_ref: ref, completed_at: Date.now() / 1000 }));
    await exchange(path, wire('resume_after_entry', { scope: payScope() })); await exchange(path, wire('attempt_payment', { scope: payScope() }));
    assert.deepEqual(calls, ['entry_inspect', 'pay_inspect', 'begin', 'complete', 'resume', 'attempt']);
  } finally { await close(); await rm(dir, { recursive: true, force: true }); }
});

test('malformed, oversized and multiple framed requests never invoke runtime', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'); let calls = 0;
  const close = await listenHostedPaymentControlFixture(runtime({ inspectEntryReadiness: async () => { calls++; return {}; } }), path, { enabled: true });
  try {
    for (const data of ['notjson\n', wire() + '{}\n', 'x'.repeat(8193), JSON.stringify({ ...message(), enablePay: true }) + '\n'])
      assert.equal((await exchange(path, data)).status, 'blocked');
    assert.equal(calls, 0);
  } finally { await close(); await rm(dir, { recursive: true, force: true }); }
});

test('timeout suppresses late handoff and permanently fences this listener until reconciliation', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), entered = deferred(), finish = deferred(); let signal;
  const close = await listenHostedPaymentControlFixture(runtime({ beginSecureEntry: async (_, options) => { signal = options.signal; entered.resolve(); await finish.promise;
    return { status: 'entry_ready', entry_ref: randomUUID(), entry_started_at: Date.now() / 1000, reads_suspended: true, payment_allowed: false }; } }), path, { enabled: true, timeoutMs: 30 });
  try {
    const task = exchange(path, wire('begin_secure_entry', { scope: entryScope() })); await entered.promise;
    const value = await task; assert.equal(value.code, 'payment_control_request_timed_out'); assert.equal(value.requires_reconciliation, true);
    assert.equal(value.entry_ref, undefined); assert.equal(signal.aborted, true); finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(path, wire())).code, 'payment_control_reconciliation_required');
  } finally { finish.resolve(); await close(); await rm(dir, { recursive: true, force: true }); }
});

test('disconnect cancels work, keeps second invocation out and cannot return a late entry reference', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), entered = deferred(), finish = deferred(); let aborted = false, calls = 0;
  const close = await listenHostedPaymentControlFixture(runtime({ beginSecureEntry: async (_, { signal }) => { calls++; entered.resolve(); await finish.promise; aborted = signal.aborted;
    return { status: 'entry_ready', entry_ref: randomUUID(), entry_started_at: Date.now() / 1000, reads_suspended: true, payment_allowed: false }; } }), path, { enabled: true });
  try {
    const client = connect(path); client.on('error', () => {}); await new Promise(resolve => client.once('connect', resolve));
    client.write(wire('begin_secure_entry', { scope: entryScope() })); await entered.promise;
    assert.equal((await exchange(path, wire())).code, 'payment_control_busy'); client.destroy(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal((await exchange(path, wire())).code, 'payment_control_reconciliation_required'); finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(aborted, true); assert.equal(calls, 1);
  } finally { finish.resolve(); await close(); await rm(dir, { recursive: true, force: true }); }
});

test('preexisting foreign or stale path is never unlinked; clean close permits a new listener', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock');
  try {
    await writeFile(path, 'FOREIGN', { mode: 0o600 }); await assert.rejects(listenHostedPaymentControlFixture(runtime(), path, { enabled: true }), /payment_control_path_exists/);
    assert.equal((await lstat(path)).isFile(), true); await unlink(path);
    const one = await listenHostedPaymentControlFixture(runtime(), path, { enabled: true });
    await assert.rejects(listenHostedPaymentControlFixture(runtime(), path, { enabled: true }), /payment_control_path_exists/); await one();
    const two = await listenHostedPaymentControlFixture(runtime(), path, { enabled: true }); await two();
    await assert.rejects(lstat(path), { code: 'ENOENT' });
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('late extra frame cancels the started method, never invokes another command and requires reconciliation', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), entered = deferred(), finish = deferred(); let calls = 0, signal;
  const close = await listenHostedPaymentControlFixture(runtime({ beginSecureEntry: async (_, options) => {
    calls++; signal = options.signal; entered.resolve(); await finish.promise; return { status: 'entry_ready', entry_ref: randomUUID(),
      entry_started_at: Date.now() / 1000, reads_suspended: true, payment_allowed: false };
  } }), path, { enabled: true });
  try {
    const client = connect(path); let output = ''; client.on('error', () => {}); client.on('data', value => { output += value; });
    const ended = new Promise(resolve => client.on('end', resolve)); await new Promise(resolve => client.once('connect', resolve));
    client.write(wire('begin_secure_entry', { scope: entryScope() })); await entered.promise; client.write('{}\n'); await ended;
    const result = JSON.parse(output); assert.equal(result.code, 'unsupported_payment_request'); assert.equal(result.requires_reconciliation, true);
    assert.equal(signal.aborted, true); finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(path, wire())).code, 'payment_control_reconciliation_required'); assert.equal(calls, 1);
  } finally { finish.resolve(); await close(); await rm(dir, { recursive: true, force: true }); }
});

test('foreign path replacement is preserved and close retains owned socket until pending runtime work settles', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), saved = join(dir, 'owned.sock');
  const close = await listenHostedPaymentControlFixture(runtime(), path, { enabled: true });
  try {
    await rename(path, saved); await writeFile(path, 'FOREIGN', { mode: 0o600 });
    await assert.rejects(close(), /payment_control_socket_replaced/); assert.equal(await readFile(path, 'utf8'), 'FOREIGN');
    await unlink(path); await rename(saved, path); await close();
  } finally { await rm(dir, { recursive: true, force: true }); }
  const d = await directory(), p = join(d, 'payment-control.sock'), entered = deferred(), finish = deferred();
  const stop = await listenHostedPaymentControlFixture(runtime({ inspectEntryReadiness: async () => { entered.resolve(); await finish.promise;
    return { status: 'blocked', code: 'hosted_payment_disabled' }; } }), p, { enabled: true });
  let client;
  try {
    client = connect(p); client.on('error', () => {}); await new Promise(resolve => client.once('connect', resolve)); client.write(wire()); await entered.promise;
    let closed = false; const pendingClose = stop().then(() => { closed = true; }); await new Promise(resolve => setImmediate(resolve));
    assert.equal(closed, false); assert.equal((await lstat(p)).isSocket(), true);
    await assert.rejects(listenHostedPaymentControlFixture(runtime(), p, { enabled: true }), /payment_control_path_exists/);
    finish.resolve(); await pendingClose; assert.equal(closed, true);
  } finally { finish.resolve(); client?.destroy(); await stop(); await rm(d, { recursive: true, force: true }); }
});

test('a stale client entry reference does not complete a new runtime session after listener restart', async () => {
  const dir = await directory(), path = join(dir, 'payment-control.sock'), oldRef = randomUUID(), currentRef = randomUUID();
  const first = await listenHostedPaymentControlFixture(runtime({ beginSecureEntry: async () => ({ status: 'entry_ready', entry_ref: oldRef,
    entry_started_at: Date.now() / 1000, reads_suspended: true, payment_allowed: false }) }), path, { enabled: true });
  try { assert.equal((await exchange(path, wire('begin_secure_entry', { scope: entryScope() }))).entry_ref, oldRef); } finally { await first(); }
  const second = await listenHostedPaymentControlFixture(runtime({ completeSecureEntry: async value => value.entry_ref === currentRef ?
    { status: 'observed', code: 'secure_entry_completed', entry_ref: currentRef, entry_started_at: value.completed_at - 1, entry_completed_at: value.completed_at } :
    { status: 'blocked', code: 'secure_entry_completion_unverified' } }), path, { enabled: true });
  try {
    const stale = await exchange(path, wire('complete_secure_entry', { entry_ref: oldRef, completed_at: Date.now() / 1000 }));
    assert.equal(stale.code, 'secure_entry_completion_unverified'); assert.equal(stale.entry_ref, undefined);
    const actual = await exchange(path, wire('complete_secure_entry', { entry_ref: currentRef, completed_at: Date.now() / 1000 }));
    assert.equal(actual.code, 'secure_entry_completed'); assert.equal(actual.entry_ref, currentRef);
  } finally { await second(); await rm(dir, { recursive: true, force: true }); }
});

test('public projection keeps observed effect facts but rejects malformed or mismatched handoff/completion fields', () => {
  const uncertain = sanitizeHostedPaymentControlResult({ status: 'observed', code: 'secure_entry_completed', pay_issued: true, cardNumber: 'SECRET' }, 'complete_secure_entry');
  for (const effect of ['checkout_navigation_issued','navigation_issued','card_initialization_issued','card_data_entered','pay_invoked','payment_invoked','approval_invoked','capture_invoked']) {
    const report = sanitizeHostedPaymentControlResult({ status: 'blocked', code: 'hosted_payment_disabled', [effect]: true }, 'inspect_readiness');
    assert.equal(report.requires_reconciliation, true, effect); assert.equal(report[effect], true, effect);
  }
  assert.equal(uncertain.requires_reconciliation, true); assert.equal(uncertain.pay_issued, true); assert.equal(JSON.stringify(uncertain).includes('SECRET'), false);
  assert.equal(sanitizeHostedPaymentControlResult({ status: 'blocked', code: 'submission_unknown' }, 'attempt_payment').requires_reconciliation, true);
  assert.equal(sanitizeHostedPaymentControlResult({ status: 'blocked', code: 'PRIVATE_ERROR' }, 'begin_secure_entry').requires_reconciliation, true);
  assert.equal(sanitizeHostedPaymentControlResult({ status: 'entry_ready', entry_ref: 'bad', payment_allowed: false }, 'begin_secure_entry').status, 'blocked');
  const now = Date.now() / 1000, ref = randomUUID();
  assert.equal(sanitizeHostedPaymentControlResult({ status: 'observed', code: 'secure_entry_completed', entry_ref: ref,
    entry_started_at: now - 2, entry_completed_at: now - 1 }, 'complete_secure_entry', { request: { entry_ref: randomUUID() } }).requires_reconciliation, true);
});
