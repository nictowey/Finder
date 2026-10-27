'use strict';
// Synthetic lifecycle only: no real browser, profile, site or credentials.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { EventEmitter } = require('node:events');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
let launchHostedReadOnlyBrowser, HOSTED_BROWSER_SHUTDOWN_MS, runHostedBrowser,
  establishHostedBlankStartup, HOSTED_BROWSER_BLANK_STARTUP_MS, launchHostedBlankStartupFixtureBrowser;
before(async () => {
  ({ launchHostedReadOnlyBrowser, HOSTED_BROWSER_SHUTDOWN_MS,
    establishHostedBlankStartup, HOSTED_BROWSER_BLANK_STARTUP_MS, launchHostedBlankStartupFixtureBrowser } =
    await import('../scripts/discogs_hosted_browser_runtime.mjs'));
  ({ runHostedBrowser } = await import('../scripts/sniper-browser-bridge-runner.mjs'));
});
const socketPath = '/tmp/sniper-read-only.sock';

function blankFixture(count = 2, options = {}) {
  const calls = [], context = new EventEmitter();
  let pages = [];
  const privatePage = close => {
    const page = { close };
    for (const key of ['url', 'title', 'evaluate', 'screenshot', 'locator']) {
      Object.defineProperty(page, key, { get() {
        calls.push('forbidden_read'); throw new Error('private page read forbidden');
      } });
    }
    return page;
  };
  for (let i = 0; i < count; i++) {
    const page = privatePage(async settings => {
      calls.push(['retire', settings]);
      if (options.closeFails) throw new Error('private provider error');
      if (options.closeHangs) await new Promise(() => {});
      pages = pages.filter(item => item !== page);
      if (options.restoreDuringClose) {
        const restored = privatePage(async () => {});
        pages.push(restored); context.emit('page', restored);
      }
    });
    pages.push(page);
  }
  const blank = privatePage(async () => {});
  context.pages = () => [...pages];
  context.newPage = async () => {
    calls.push('new_blank'); pages.push(blank); context.emit('page', blank);
    if (options.extraPage) pages.push(privatePage(async () => {}));
    return blank;
  };
  const restore = () => {
    const page = privatePage(async () => {});
    pages.push(page); context.emit('page', page);
  };
  return { context, calls, blank, restore };
}

test('blank startup retires restored pages without contents and retains exactly the new controlled Page', async () => {
  for (const count of [0, 2, 16]) {
    const item = blankFixture(count);
    const guard = await establishHostedBlankStartup(item.context);
    assert.deepEqual(item.calls, [...Array.from({ length: count }, () =>
      ['retire', { runBeforeUnload: false }]), 'new_blank']);
    assert.deepEqual(item.context.pages(), [item.blank]);
    guard.verify();
    assert.equal(item.context.listenerCount('page'), 1);
    guard.dispose(); guard.dispose();
    assert.equal(item.context.listenerCount('page'), 0);
    assert.throws(guard.verify, { message: 'browser_blank_startup_unverified' });
  }
});

test('blank startup rejects close failure, excess pages and restoration without a private read', async () => {
  for (const [count, options] of [[17, {}], [1, { closeFails: true }],
    [1, { restoreDuringClose: true }], [1, { extraPage: true }]]) {
    const item = blankFixture(count, options);
    await assert.rejects(establishHostedBlankStartup(item.context),
      { message: 'browser_blank_startup_unverified' });
    assert.equal(item.context.listenerCount('page'), 0);
    if (count === 17 || options.closeFails || options.restoreDuringClose) {
      assert.ok(!item.calls.includes('new_blank'));
    }
  }
});

test('blank startup keeps its restoration guard active until listeners are ready', async () => {
  const item = blankFixture();
  const guard = await establishHostedBlankStartup(item.context);
  item.restore();
  assert.throws(guard.verify, { message: 'browser_blank_startup_unverified' });
  guard.dispose();
});

test('blank startup bounds a stalled retirement and cannot continue to newPage after timeout', async () => {
  const item = blankFixture(1, { closeHangs: true });
  const started = Date.now();
  await assert.rejects(establishHostedBlankStartup(item.context),
    { message: 'browser_blank_startup_unverified' });
  assert.ok(Date.now() - started >= HOSTED_BROWSER_BLANK_STARTUP_MS - 50);
  assert.ok(Date.now() - started < HOSTED_BROWSER_BLANK_STARTUP_MS + 1500);
  assert.deepEqual(item.calls, [['retire', { runBeforeUnload: false }]]);
  assert.equal(item.context.listenerCount('page'), 0);
});

test('production launcher fails before any listener when restored page retirement cannot be proved', async () => {
  const item = fixture();
  let retired = 0;
  item.context.pages = () => [{ close: async settings => {
    assert.deepEqual(settings, { runBeforeUnload: false }); retired++;
    throw new Error('private checkout contents must not escape');
  } }];
  item.context.newPage = async () => { throw new Error('must not create'); };
  await assert.rejects(launchHostedReadOnlyBrowser(item.firefox, '/profile', '/bridge/sniper-read-only.sock'),
    { message: 'browser_blank_startup_unverified' });
  assert.equal(retired, 1);
  assert.equal(item.calls.filter(value => value === 'close').length, 1);
  const preferences = item.calls.find(value => Array.isArray(value))[2].firefoxUserPrefs;
  assert.deepEqual(preferences, { 'signon.rememberSignons': false,
    'browser.startup.page': 0, 'browser.sessionstore.resume_session_once': false,
    'browser.sessionstore.resume_from_crash': false, 'browser.sessionstore.max_resumed_crashes': 0 });
  assert.equal(item.context.listenerCount('page'), 0);
});

test('actual read IPC stays excluded while a restored page appears during listener startup', async () => {
  const net = require('node:net');
  const { syncBuiltinESMExports } = require('node:module');
  const originalCreateServer = net.createServer;
  const item = fixture(), blank = blankFixture(1);
  item.context.pages = blank.context.pages;
  item.context.newPage = blank.context.newPage;
  blank.context.on('page', page => item.context.emit('page', page));
  let reply, requests = 0;
  net.createServer = (...args) => {
    const server = originalCreateServer(...args), originalListen = server.listen;
    server.listen = function (...listenArgs) {
      const ready = listenArgs.pop();
      return originalListen.call(this, ...listenArgs, () => {
        // Delay the actual listener-ready callback until a real client has
        // exercised the bound socket, reproducing the awaited startup window.
        blank.restore();
        const client = connect(socketPath);
        let data = '';
        client.setEncoding('utf8');
        client.on('connect', () => { requests++; client.write('{}\n'); });
        client.on('data', chunk => { data += chunk; });
        client.on('end', () => { reply = JSON.parse(data); ready(); });
        client.on('error', () => { client.destroy(); ready(); });
      });
    };
    return server;
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(launchHostedBlankStartupFixtureBrowser(item.firefox),
      { message: 'browser_blank_startup_unverified' });
    assert.equal(requests, 1);
    assert.equal(reply.code, 'read_in_progress');
    assert.ok(!blank.calls.includes('forbidden_read'));
    assert.equal(item.calls.filter(value => value === 'close').length, 1);
    assert.equal(item.context.listenerCount('page'), 0);
    await noSocket();
  } finally {
    net.createServer = originalCreateServer;
    syncBuiltinESMExports();
  }
});

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
    { liveGate: false, readAuthorization() {} }, { liveGate: 'true' },
    { liveGate: false, entryGate: true, controlEnabled: true },
    { liveGate: false, entryGate: 'true', controlEnabled: false }]) {
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
    loadPaymentMode: async () => ({ entryGate: false, liveGate: false, controlEnabled: false }),
    startMemoryLogging: ({ report }) => {
      assert.equal(typeof report, 'function'); calls.push('memory_start');
      return () => { calls.push('memory_stop'); };
    },
    reportError: value => errors.push(value), launch: async (...args) => {
      calls.push(args); launchReady(); if (holdLaunch) await hold; return close;
    } };
  return { options, signals, output, errors, calls, fail, launched, ready };
}

test('runner wires explicit owner entry-only mode without enabling payment', async () => {
  const item = runnerFixture();
  item.options.loadPaymentMode = async () => ({ entryGate: true, liveGate: false, controlEnabled: true });
  const running = runHostedBrowser('firefox-port', item.options);
  await item.launched;
  item.signals.emit('SIGTERM');
  assert.equal(await running, 0);
  assert.deepEqual(item.calls[0], ['firefox-port', '/profile', '/bridge/sniper-read-only.sock', {
    paymentRuntime: { entryGate: true, liveGate: false, controlEnabled: true },
  }]);
  assert.deepEqual(item.errors, []);
});

test('runner rejects invalid payment mode before opening a browser', async () => {
  for (const mode of [null, { entryGate: true, liveGate: false, controlEnabled: false },
    { entryGate: false, liveGate: false, controlEnabled: true, dispatch() {} }]) {
    const item = runnerFixture(); item.options.loadPaymentMode = async () => mode;
    assert.equal(await runHostedBrowser('firefox-port', item.options), 1);
    assert.equal(item.calls.length, 0);
    assert.deepEqual(item.errors, ['browser_unavailable']);
  }
});

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
