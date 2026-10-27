/** Fixed private bridge runner. A browser crash is not a healthy viewer.
 * No navigation, recovery, grant creation or browser restart occurs here.
 */
import { pathToFileURL } from 'node:url';
import { launchHostedReadOnlyBrowser } from './discogs_hosted_browser_runtime.mjs';
import { startBrowserMemoryLogging } from './discogs_browser_memory.mjs';
import { loadHostedGuestQualification } from './discogs_hosted_guest_qualification.mjs';
import { loadHostedPaymentMode } from './discogs_hosted_payment_control.mjs';

/** Per-process explicit opt-in, never a truthy environment coercion. */
export function parseRequestDiagnosticsMode(value) {
  if (value === undefined) return false;
  if (value === 'once') return true;
  throw new TypeError('invalid_request_diagnostics_mode');
}

export async function runHostedBrowser(firefox, {
  launch = launchHostedReadOnlyBrowser,
  startMemoryLogging = startBrowserMemoryLogging,
  loadQualification = loadHostedGuestQualification,
  loadPaymentMode = loadHostedPaymentMode,
  requestDiagnostics = false,
  signals = process,
  report = message => console.log(message),
  reportError = message => console.error(message),
} = {}) {
  let stopping = false, stop, diagnosticStopRequested = false, close, stopMemoryLogging;
  const requestedStop = new Promise(resolve => { stop = resolve; });
  const onStop = () => { stopping = true; stop('requested_stop'); };
  signals.once('SIGTERM', onStop);
  signals.once('SIGINT', onStop);
  const onDiagnosticStop = () => { diagnosticStopRequested = true; close?.stopRequestDiagnostics(); };
  if (requestDiagnostics === true) signals.on('SIGUSR2', onDiagnosticStop);
  const cleanup = async () => {
    stopMemoryLogging?.(); stopMemoryLogging = null;
    if (close) await close();
  };
  try {
    if (typeof requestDiagnostics !== 'boolean') throw new TypeError('request_diagnostics_boolean_required');
    const initialQualification = await loadQualification();
    const paymentMode = await loadPaymentMode();
    if (!paymentMode || typeof paymentMode !== 'object' || Array.isArray(paymentMode) ||
        Object.keys(paymentMode).sort().join('|') !== 'controlEnabled|entryGate|liveGate' ||
        Object.values(paymentMode).some(value => typeof value !== 'boolean') ||
        (paymentMode.entryGate || paymentMode.liveGate) && !paymentMode.controlEnabled) {
      throw new Error('invalid_payment_runtime_mode');
    }
    if (initialQualification !== null &&
        (!initialQualification || typeof initialQualification !== 'object' ||
          typeof initialQualification.accessGuard !== 'function' ||
          typeof initialQualification.authorize !== 'function')) {
      throw new Error('invalid_guest_qualification_policy');
    }
    // Keep the same browser and socket. A reviewed local policy can change
    // jobs while a guest form is open; each request must reload that policy.
    // Missing, expired, revoked or invalid policy fails closed. Grants keep
    // their own issuer's immutable identity/revalidation after authorization.
    const guestPreparation = Object.freeze({
      accessGuard: async (...args) => {
        try {
          const current = await loadQualification();
          return typeof current?.accessGuard === 'function' ?
            await current.accessGuard(...args) === true : false;
        } catch { return false; }
      },
      authorize: async (...args) => {
        try {
          const current = await loadQualification();
          return typeof current?.authorize === 'function' ?
            await current.authorize(...args) : null;
        } catch { return null; }
      },
    });
    const runtimeOptions = {};
    runtimeOptions.guestPreparation = guestPreparation;
    if (requestDiagnostics) runtimeOptions.requestDiagnostics = true;
    if (paymentMode.controlEnabled) runtimeOptions.paymentRuntime = paymentMode;
    close = Object.keys(runtimeOptions).length === 0 ?
      await launch(firefox, '/profile', '/bridge/sniper-read-only.sock') :
      await launch(firefox, '/profile', '/bridge/sniper-read-only.sock', runtimeOptions);
    if (diagnosticStopRequested) close.stopRequestDiagnostics();
    if (stopping) { await cleanup(); return 0; }
    if (!close.isAvailable()) throw new Error('browser_unavailable');
    stopMemoryLogging = startMemoryLogging({ report });
    report('private_browser_read_only_ready');
    const result = await Promise.race([requestedStop, close.unavailable]);
    await cleanup();
    if (result === 'browser_unavailable') {
      reportError('browser_unavailable');
      return 1;
    }
    return 0;
  } catch {
    await cleanup().catch(() => {});
    reportError('browser_unavailable');
    return 1;
  } finally {
    stopMemoryLogging?.();
    signals.off('SIGTERM', onStop);
    signals.off('SIGINT', onStop);
    signals.off('SIGUSR2', onDiagnosticStop);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { firefox } = await import('/app/tests/cloud_browser/node_modules/playwright/index.mjs');
    const requestDiagnostics = parseRequestDiagnosticsMode(process.env.SNIPER_BROWSER_REQUEST_DIAGNOSTICS);
    const code = await runHostedBrowser(firefox, { requestDiagnostics });
    // Also bounds shutdown if an unresponsive driver retained its handles.
    process.exit(code);
  } catch {
    console.error('browser_unavailable');
    process.exit(1);
  }
}
