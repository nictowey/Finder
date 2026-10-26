'use strict';
// Actual private IPC and fake context only: no browser, profile, site or grants.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
let listenHostedGuestPreparation, createHostedContextLease, listenHostedReadOnly;
before(async () => {
  ({ listenHostedGuestPreparation, createHostedContextLease } = await import('../scripts/discogs_hosted_guest_socket.mjs'));
  ({ listenHostedReadOnly } = await import('../scripts/discogs_hosted_readonly.mjs'));
});
const path = () => `/tmp/sniper-guest-test-${randomUUID()}.sock`;
const request = action => JSON.stringify({ action, request: { fixture: true } }) + '\n';
function exchange(socketPath, data) {
  return new Promise((resolve, reject) => {
    const client = connect(socketPath); let output = '';
    client.on('connect', () => client.write(data)); client.on('error', reject);
    client.on('data', chunk => { output += chunk; });
    client.on('end', () => resolve(JSON.parse(output)));
  });
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function options(overrides = {}) {
  return { authorize: async () => ({ fixture: true }), accessGuard: async () => true,
    acquireContextLease: createHostedContextLease(), fixtureOperations: {
      prepare_guest_form: async () => ({ status: 'blocked', code: 'prior_attempt_unresolved' }),
      probe_guest_evidence: async () => ({ status: 'observed', code: 'current_dom_evidence_only', observed: { destination_match: false } }),
    }, ...overrides };
}

test('fixed IPC is owner-only, narrow and redacts arbitrary operation output', async () => {
  const socket = path(), config = options();
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'current_dom_evidence_only',
    observed: { destination_match: true, address: 'must not escape' }, token: 'must not escape', final_submission_allowed: true });
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    assert.equal((await stat(socket)).mode & 0o777, 0o600);
    const result = await exchange(socket, request('probe_guest_evidence'));
    assert.deepEqual(result.observed, { destination_match: true });
    assert.equal(result.final_submission_allowed, false);
    assert.equal(JSON.stringify(result).includes('must not escape'), false);
    assert.equal((await exchange(socket, request('navigate'))).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, JSON.stringify({ action: 'probe_guest_evidence', request: {}, url: 'https://invalid/' }) + '\n')).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, request('probe_guest_evidence') + '{}\n')).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, 'x'.repeat(16385))).code, 'request_too_large');
  } finally { await close(); }
  await assert.rejects(stat(socket), { code: 'ENOENT' });
});

test('trusted scope/access callbacks required; denied scope never reaches browser operation', async () => {
  const socket = path(); let calls = 0;
  await assert.rejects(listenHostedGuestPreparation({}, socket, {}), /scoped_guest_configuration_required/);
  await assert.rejects(listenHostedGuestPreparation({}, '/tmp/unapproved.sock', options()), /unsupported_guest_socket_path/);
  const config = options({ accessGuard: async () => false });
  config.fixtureOperations.prepare_guest_form = async () => { calls++; throw new Error('should not run'); };
  const close = await listenHostedGuestPreparation({}, socket, config);
  try { assert.equal((await exchange(socket, request('prepare_guest_form'))).code, 'scoped_access_required'); assert.equal(calls, 0); }
  finally { await close(); }
});

test('shared context lease blocks reader while preparation owns context, then releases after completion', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const config = options({ acquireContextLease });
  config.fixtureOperations.prepare_guest_form = async () => { entered.resolve(); await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const closeGuest = await listenHostedGuestPreparation({}, guest, config);
  const closeReader = await listenHostedReadOnly({ pages: () => [] }, reader, { acquireContextLease });
  try {
    const first = exchange(guest, request('prepare_guest_form')); await entered.promise;
    assert.equal((await exchange(reader, '{"action":"current_release","release_id":"111"}\n')).code, 'read_in_progress');
    assert.equal((await exchange(guest, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await first;
    assert.equal((await exchange(reader, '{"action":"current_release","release_id":"111"}\n')).code, 'exact_current_page_not_unique');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('reader lease blocks preparation until actual reader work settles', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise;
    return { source: 'discogs_rendered_release', release_id: 111, url: 'https://www.discogs.com/sell/release/111', status: 'observed', complete: true, has_next: false, total: 0, rows: [] };
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  try {
    const first = exchange(reader, '{"action":"current_release","release_id":"111"}\n'); await entered.promise;
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await first;
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('disconnected operation receives cancellation and retains lease until it settles', async () => {
  const socket = path(), entered = deferred(), finish = deferred(), closed = deferred(); let observedSignal;
  const acquireContextLease = createHostedContextLease();
  const config = options({ acquireContextLease });
  config.fixtureOperations.prepare_guest_form = async (context, value, { signal }) => { observedSignal = signal; entered.resolve(); await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const close = await listenHostedGuestPreparation({}, socket, config);
  const client = connect(socket); client.on('error', () => {}); client.on('close', closed.resolve);
  client.on('connect', () => client.write(request('prepare_guest_form')));
  try {
    await entered.promise;
    const aborted = new Promise(resolve => observedSignal.addEventListener('abort', resolve, { once: true }));
    client.destroy(); await closed.promise; await aborted;
    assert.equal(observedSignal.aborted, true);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'current_dom_evidence_only');
  } finally { finish.resolve(); await close(); }
});

test('timeout aborts but cannot free a still-running operation', async () => {
  const socket = path(), finish = deferred(); let signal;
  const config = options({ timeoutMs: 20 });
  config.fixtureOperations.prepare_guest_form = async (context, value, opts) => { signal = opts.signal; await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    assert.equal((await exchange(socket, request('prepare_guest_form'))).code, 'request_timed_out');
    assert.equal(signal.aborted, true);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve();
  } finally { finish.resolve(); await close(); }
});

// The collector outlives its disconnected client; the shared context remains
// unavailable until actual DOM read completion, even if the response is gone.
test('disconnected readonly collector cannot release preparation lease early', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise; return {};
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  const client = connect(reader); client.on('error', () => {});
  client.on('connect', () => client.write('{"action":"current_release","release_id":"111"}\n'));
  try {
    await entered.promise; client.destroy();
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('readonly deadline does not release a still-running underlying collector', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise; return {};
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  try {
    const result = exchange(reader, '{"action":"current_release","release_id":"111"}\n');
    await entered.promise;
    assert.ok(['request_timed_out', 'observation_unavailable'].includes((await result).code));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('launcher rejects missing or expanded guest configuration before browser launch', async () => {
  const { launchHostedReadOnlyBrowser } = await import('../scripts/discogs_hosted_browser_runtime.mjs');
  let calls = 0;
  const browser = { launchPersistentContext: async () => { calls++; } };
  for (const config of [{}, { authorize: () => ({}) }, { authorize: () => ({}), accessGuard: () => true, url: 'https://invalid/' }]) {
    await assert.rejects(launchHostedReadOnlyBrowser(browser, '/profile', '/bridge/sniper-read-only.sock', { guestPreparation: config }), /scoped_guest_configuration_required/);
  }
  assert.equal(calls, 0);
});


test('operation returning on cancellation still retains lease until module settlement hook finishes', async () => {
  const socket = path(), entered = deferred(), finish = deferred(), operationReturned = deferred();
  const config = options({ timeoutMs: 20 });
  config.fixtureOperations.prepare_guest_form = async (context, value, { signal }) => {
    entered.resolve(); await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
    operationReturned.resolve(); return { code: 'cancelled' };
  };
  config.fixtureOperations.waitForSettlement = async () => finish.promise;
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    const result = exchange(socket, request('prepare_guest_form')); await entered.promise;
    assert.equal((await result).code, 'request_timed_out'); await operationReturned.promise;
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'current_dom_evidence_only');
  } finally { finish.resolve(); await close(); }
});
