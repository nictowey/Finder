/** Staged launcher for the private hosted Firefox display. Do not use against the
 * logged-in profile until the owner approves programmatic read access to it.
 */
import { listenHostedReadOnly } from './discogs_hosted_readonly.mjs';
import { createHostedContextLease, listenHostedGuestPreparation, HOSTED_GUEST_SOCKET } from './discogs_hosted_guest_socket.mjs';
import { createHostedPaymentRuntime, createHostedPaymentRuntimeFixture } from './discogs_hosted_payment_runtime.mjs';
import { listenHostedPaymentControl, listenHostedPaymentControlFixture, HOSTED_PAYMENT_CONTROL_SOCKET } from './discogs_hosted_payment_control.mjs';

export const HOSTED_BROWSER_SHUTDOWN_MS = 2000;

async function boundedShutdown(operations) {
  let timer;
  try {
    const results = await Promise.race([
      Promise.allSettled(operations.map(operation => Promise.resolve().then(operation))),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), HOSTED_BROWSER_SHUTDOWN_MS); }),
    ]);
    if (!results || results.some(result => result.status === 'rejected')) {
      throw new Error('browser_shutdown_unavailable');
    }
  } finally { clearTimeout(timer); }
}

const paymentFixture = Symbol('isolated_payment_runtime_fixture');
export async function launchHostedReadOnlyBrowser(firefox, profilePath, socketPath, { guestPreparation = null,
  paymentRuntime = null, [paymentFixture]: fixture = null } = {}) {
  if (!firefox || typeof firefox.launchPersistentContext !== 'function' ||
      profilePath !== '/profile' || !['/tmp/sniper-read-only.sock', '/bridge/sniper-read-only.sock'].includes(socketPath)) {
    throw new TypeError('unsupported_hosted_browser_configuration');
  }
  if (guestPreparation !== null && (socketPath !== '/bridge/sniper-read-only.sock' ||
      typeof guestPreparation !== 'object' || Array.isArray(guestPreparation) ||
      Object.keys(guestPreparation).sort().join('|') !== 'accessGuard|authorize' ||
      typeof guestPreparation.authorize !== 'function' || typeof guestPreparation.accessGuard !== 'function')) {
    throw new TypeError('scoped_guest_configuration_required');
  }
  if (paymentRuntime !== null && (!paymentRuntime || typeof paymentRuntime !== 'object' || Array.isArray(paymentRuntime) ||
      !['liveGate', 'controlEnabled|entryGate|liveGate'].includes(Object.keys(paymentRuntime).sort().join('|')) ||
      typeof paymentRuntime.liveGate !== 'boolean' ||
      ['entryGate', 'controlEnabled'].some(key => Object.hasOwn(paymentRuntime, key) && typeof paymentRuntime[key] !== 'boolean') ||
      (paymentRuntime.liveGate || paymentRuntime.entryGate || paymentRuntime.controlEnabled) &&
        fixture === null && socketPath !== '/bridge/sniper-read-only.sock')) {
    throw new TypeError('fixed_payment_runtime_configuration_required');
  }
  const acquireContextLease = createHostedContextLease();
  const context = await firefox.launchPersistentContext(profilePath, {
    headless: false,
    acceptDownloads: false,
    noDefaultViewport: true,
    firefoxUserPrefs: { 'signon.rememberSignons': false },
  });
  let browser, closeSocket, closeGuestSocket, closePaymentSocket, payment, shutdown, closing = false, unavailable = false;
  let reportUnavailable;
  const unavailablePromise = new Promise(resolve => { reportUnavailable = resolve; });
  const close = () => {
    if (shutdown) return shutdown;
    closing = true;
    shutdown = boundedShutdown([
      () => closeSocket?.(),
      () => closeGuestSocket?.(),
      () => closePaymentSocket?.(),
      () => payment?.close(),
      () => context.close(),
    ]).finally(() => {
      context.off?.('close', browserUnavailable);
      browser?.off?.('disconnected', browserUnavailable);
    });
    return shutdown;
  };
  const browserUnavailable = () => {
    if (closing || unavailable) return;
    unavailable = true;
    reportUnavailable('browser_unavailable');
    // During socket startup, the caller closes the newly bound socket before
    // returning. Once ready, stop accepting/abort requests immediately.
    if (closeSocket) void close().catch(() => {});
  };
  const isAvailable = () => {
    if (closing || unavailable) return false;
    try {
      if (browser?.isConnected() !== true) browserUnavailable();
    } catch { browserUnavailable(); }
    return !closing && !unavailable;
  };
  Object.defineProperties(close, {
    unavailable: { value: unavailablePromise },
    isAvailable: { value: isAvailable },
    payment: { get: () => payment },
  });
  try {
    if (typeof context.on !== 'function' || typeof context.off !== 'function' ||
        typeof context.browser !== 'function') throw new Error('browser_unavailable');
    context.on('close', browserUnavailable);
    browser = context.browser();
    if (!browser || typeof browser.on !== 'function' || typeof browser.off !== 'function' ||
        typeof browser.isConnected !== 'function') throw new Error('browser_unavailable');
    browser.on('disconnected', browserUnavailable);
    if (!isAvailable()) throw new Error('browser_unavailable');
    // Same resident context and same lease as both read-only sockets. The
    // ordinary runner supplies no live gate; no payment RPC is exposed here.
    const paymentOptions = { context, acquireContextLease, liveGate: paymentRuntime?.liveGate === true,
      entryGate: paymentRuntime?.entryGate === true };
    payment = fixture === null ? createHostedPaymentRuntime(paymentOptions) :
      createHostedPaymentRuntimeFixture({ ...fixture, ...paymentOptions });
    if (paymentRuntime?.controlEnabled === true) {
      closePaymentSocket = fixture === null ?
        await listenHostedPaymentControl(payment, HOSTED_PAYMENT_CONTROL_SOCKET, { enabled: true }) :
        await listenHostedPaymentControlFixture(payment, `${fixture.guestLedgerDirectory}/payment-control.sock`, { enabled: true });
    }
    closeSocket = await listenHostedReadOnly(context, socketPath,
    socketPath === '/bridge/sniper-read-only.sock' ? {
      durableStopPath: '/bridge/access-stopped',
      operatorBootPath: '/bridge/operator-boot',
      operatorAckPath: '/bridge/operator-rocky-ack',
      operatorUsedPath: '/bridge/operator-rocky-used',
      operatorCartAckPath: '/bridge/operator-cart-ack',
      operatorCartUsedPath: '/bridge/operator-cart-used',
      acquireContextLease,
    } : { acquireContextLease });
    if (guestPreparation !== null) {
      if (!isAvailable()) throw new Error('browser_unavailable');
      closeGuestSocket = await listenHostedGuestPreparation(context, HOSTED_GUEST_SOCKET,
        { ...guestPreparation, acquireContextLease });
    }
    if (!isAvailable()) throw new Error('browser_unavailable');
    return close;
  } catch (error) {
    await close().catch(() => {});
    throw error;
  }
}

/** Only isolated metadata paths and an intercepted browser may be supplied by
 * Node's test runner. The production launcher has no factory/callback override. */
export function launchHostedPaymentRuntimeFixtureBrowser(firefox, profilePath, socketPath, options = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !options || typeof options !== 'object' || Array.isArray(options) ||
      !['fixture|liveGate', 'controlEnabled|entryGate|fixture|liveGate'].includes(Object.keys(options).sort().join('|')) ||
      typeof options.liveGate !== 'boolean' ||
      ['entryGate', 'controlEnabled'].some(key => Object.hasOwn(options, key) && typeof options[key] !== 'boolean')) {
    throw new TypeError('fixture_context_required');
  }
  return launchHostedReadOnlyBrowser(firefox, profilePath, socketPath,
    { paymentRuntime: { liveGate: options.liveGate, entryGate: options.entryGate === true,
      controlEnabled: options.controlEnabled === true }, [paymentFixture]: options.fixture });
}
