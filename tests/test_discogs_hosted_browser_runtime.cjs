'use strict';
// Synthetic lifecycle only: no real browser, profile, site or credentials.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { EventEmitter } = require('node:events');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
let launchHostedReadOnlyBrowser, HOSTED_BROWSER_SHUTDOWN_MS, runHostedBrowser;
before(async () => {
  ({ launchHostedReadOnlyBrowser, HOSTED_BROWSER_SHUTDOWN_MS } =
    await import('../scripts/discogs_hosted_browser_runtime.mjs'));
  ({ runHostedBrowser } = await import('../scripts/sniper-browser-bridge-runner.mjs'));
});
const socketPath = '/tmp/sniper-read-only.sock';

function fixture({ connected = true, hangClose = false, closeOnStartup = false } = {}) {
  const calls = [];
  const browser = new EventEmitter();
  browser.isConnected = () => {
    calls.push('connected');
    if (closeOnStartup) {
      closeOnStartup = false;
      queueMicrotask(() => { connected = false; context.emit('close'); });
    }
    return connected;
  };
  const context = Object.assign(new EventEmitter(), {
    browser: () => browser,
    pages: () => [],
    close: async () => {
      calls.push('close');
      if (hangClose) await new Promise(() => {});
      connected = false; context.emit('close'); browser.emit('disconnected');
    },
  });
  const firefox = { launchPersistentContext: async (profile, options) => {
    calls.push(['launch', profile, options]); return context;
  } };
  return { firefox, context, browser, calls };
}

async function noSocket() {
  await assert.rejects(stat(socketPath), { code: 'ENOENT' });
}

for (const event of ['context_close', 'browser_disconnected']) {
  test(`${event} reports unavailable and closes existing read clients`, async () => {
    const item = fixture();
    const close = await launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath);
    const client = connect(socketPath);
    client.on('error', () => {});
    await new Promise(resolve => client.once('connect', resolve));
    const disconnected = new Promise(resolve => client.once('close', resolve));
    if (event === 'context_close') item.context.emit('close');
    else item.browser.emit('disconnected');
    assert.equal(await close.unavailable, 'browser_unavailable');
    assert.equal(close.isAvailable(), false);
    await close(); await disconnected; await noSocket();
    assert.equal(item.calls.filter(value => value === 'close').length, 1);
    assert.equal(item.context.listenerCount('close'), 0);
    assert.equal(item.browser.listenerCount('disconnected'), 0);
  });
}

for (const mode of ['already_disconnected', 'closes_during_startup']) {
  test(`${mode} never exposes a ready runtime`, async () => {
    const item = fixture(mode === 'already_disconnected' ?
      { connected: false } : { closeOnStartup: true });
    await assert.rejects(launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath),
      { message: 'browser_unavailable' });
    await noSocket();
    assert.equal(item.calls.filter(value => value === 'close').length, 1);
  });
}

test('orderly close is idempotent and does not report an unexpected failure', async () => {
  const item = fixture();
  const close = await launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath);
  let failed = false;
  close.unavailable.then(() => { failed = true; });
  assert.equal(close.isAvailable(), true);
  await Promise.all([close(), close()]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(failed, false);
  assert.equal(item.calls.filter(value => value === 'close').length, 1);
  await noSocket();
});

test('actual launcher attaches a disabled payment caller without reading the resident page or private policy', async () => {
  const item = fixture(); let pageReads = 0;
  item.context.pages = () => { pageReads++; throw new Error('No payment browser read'); };
  const close = await launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath);
  try {
    assert.equal(close.payment.paymentEnabled, false);
    for (const action of ['inspectReadiness', 'beginSecureEntry', 'resumeAfterEntry', 'attemptPayment']) {
      assert.equal((await close.payment[action]({})).code, 'hosted_payment_disabled');
    }
    assert.equal(pageReads, 0);
  } finally { await close(); }
});

test('launcher rejects payment callback/path overrides or live gate on the development socket before browser launch', async () => {
  for (const paymentRuntime of [{ liveGate: true }, { liveGate: false, policyPath: '/tmp/foreign' },
    { liveGate: false, readAuthorization() {} }, { liveGate: 'true' }]) {
    const item = fixture();
    await assert.rejects(launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath, { paymentRuntime }),
      { message: 'fixed_payment_runtime_configuration_required' });
    assert.equal(item.calls.length, 0);
  }
});

test('an unresponsive browser close cannot retain the socket or wait indefinitely', async () => {
  const item = fixture({ hangClose: true });
  const close = await launchHostedReadOnlyBrowser(item.firefox, '/profile', socketPath);
  const started = Date.now();
  await assert.rejects(close(), { message: 'browser_shutdown_unavailable' });
  assert.ok(Date.now() - started >= HOSTED_BROWSER_SHUTDOWN_MS - 50);
  assert.ok(Date.now() - started < HOSTED_BROWSER_SHUTDOWN_MS + 1500);
  await noSocket();
});

function runnerFixture({ holdLaunch = false, closeFails = false } = {}) {
  const signals = new EventEmitter(), output = [], errors = [], calls = [];
  let fail, ready, launchReady;
  const unavailable = new Promise(resolve => { fail = resolve; });
  const launched = new Promise(resolve => { launchReady = resolve; });
  const hold = new Promise(resolve => { ready = resolve; });
  const close = Object.assign(async () => {
    calls.push('close'); if (closeFails) throw new Error('private driver details');
  }, { unavailable, isAvailable: () => true });
  const options = { signals, report: value => output.push(value),
    startMemoryLogging: ({ report }) => {
      assert.equal(typeof report, 'function'); calls.push('memory_start');
      return () => { calls.push('memory_stop'); };
    },
    reportError: value => errors.push(value), launch: async (...args) => {
      calls.push(args); launchReady(); if (holdLaunch) await hold; return close;
    } };
  return { options, signals, output, errors, calls, fail, launched, ready };
}

test('runner returns nonzero on browser loss with only a fixed diagnostic', async () => {
  const item = runnerFixture();
  const running = runHostedBrowser('firefox-port', item.options);
  await item.launched;
  item.fail('browser_unavailable');
  assert.equal(await running, 1);
  assert.deepEqual(item.output, ['private_browser_read_only_ready']);
  assert.deepEqual(item.errors, ['browser_unavailable']);
  assert.deepEqual(item.calls[0], ['firefox-port', '/profile', '/bridge/sniper-read-only.sock']);
  assert.equal(item.calls.filter(value => value === 'close').length, 1);
  assert.ok(item.calls.indexOf('memory_stop') < item.calls.indexOf('close'));
  assert.equal(item.signals.listenerCount('SIGTERM'), 0);
});

for (const signal of ['SIGTERM', 'SIGINT']) {
  test(`runner ${signal} stops normally without a failure diagnostic`, async () => {
    const item = runnerFixture();
    const running = runHostedBrowser('firefox-port', item.options);
    await item.launched;
    await new Promise(resolve => setImmediate(resolve));
    item.signals.emit(signal);
    assert.equal(await running, 0);
    assert.deepEqual(item.errors, []);
    assert.equal(item.calls.filter(value => value === 'close').length, 1);
    assert.ok(item.calls.indexOf('memory_stop') < item.calls.indexOf('close'));
    assert.equal(item.signals.listenerCount('SIGTERM'), 0);
    assert.equal(item.signals.listenerCount('SIGINT'), 0);
  });
}

test('a stop requested during startup is honored before reporting ready', async () => {
  const item = runnerFixture({ holdLaunch: true });
  const running = runHostedBrowser('firefox-port', item.options);
  await item.launched;
  item.signals.emit('SIGTERM'); item.ready();
  assert.equal(await running, 0);
  assert.deepEqual(item.output, []);
  assert.deepEqual(item.errors, []);
  assert.ok(!item.calls.includes('memory_start'));
});

test('runner startup and cleanup failures cannot log driver details or return success', async () => {
  const errors = [];
  assert.equal(await runHostedBrowser(null, { signals: new EventEmitter(),
    launch: async () => { throw new Error('private URL and session details'); },
    reportError: value => errors.push(value) }), 1);
  assert.deepEqual(errors, ['browser_unavailable']);
  const item = runnerFixture({ closeFails: true });
  const running = runHostedBrowser(null, item.options);
  await item.launched;
  await new Promise(resolve => setImmediate(resolve));
  item.signals.emit('SIGTERM');
  assert.equal(await running, 1);
  assert.deepEqual(item.errors, ['browser_unavailable']);
});
