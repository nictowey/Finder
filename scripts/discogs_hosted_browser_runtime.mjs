/** Staged launcher for the private hosted Firefox display. Do not use against the
 * logged-in profile until the owner approves programmatic read access to it.
 */
import { listenHostedReadOnly } from './discogs_hosted_readonly.mjs';
import { createHostedContextLease, listenHostedGuestPreparation, HOSTED_GUEST_SOCKET } from './discogs_hosted_guest_socket.mjs';

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

export async function launchHostedReadOnlyBrowser(firefox, profilePath, socketPath, { guestPreparation = null } = {}) {
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
  const acquireContextLease = createHostedContextLease();
  const context = await firefox.launchPersistentContext(profilePath, {
    headless: false,
    acceptDownloads: false,
    noDefaultViewport: true,
    firefoxUserPrefs: { 'signon.rememberSignons': false },
  });
  let browser, closeSocket, closeGuestSocket, shutdown, closing = false, unavailable = false;
  let reportUnavailable;
  const unavailablePromise = new Promise(resolve => { reportUnavailable = resolve; });
  const close = () => {
    if (shutdown) return shutdown;
    closing = true;
    shutdown = boundedShutdown([
      () => closeSocket?.(),
      () => closeGuestSocket?.(),
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
