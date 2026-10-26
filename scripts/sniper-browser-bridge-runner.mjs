/** Fixed private bridge runner. A browser crash is not a healthy viewer.
 * No navigation, recovery, grant creation or browser restart occurs here.
 */
import { pathToFileURL } from 'node:url';
import { launchHostedReadOnlyBrowser } from './discogs_hosted_browser_runtime.mjs';
import { startBrowserMemoryLogging } from './discogs_browser_memory.mjs';
import { loadHostedGuestQualification } from './discogs_hosted_guest_qualification.mjs';

export async function runHostedBrowser(firefox, {
  launch = launchHostedReadOnlyBrowser,
  startMemoryLogging = startBrowserMemoryLogging,
  loadQualification = loadHostedGuestQualification,
  signals = process,
  report = message => console.log(message),
  reportError = message => console.error(message),
} = {}) {
  let stopping = false, stop;
  const requestedStop = new Promise(resolve => { stop = resolve; });
  const onStop = () => { stopping = true; stop('requested_stop'); };
  signals.once('SIGTERM', onStop);
  signals.once('SIGINT', onStop);
  let close, stopMemoryLogging;
  const cleanup = async () => {
    stopMemoryLogging?.(); stopMemoryLogging = null;
    if (close) await close();
  };
  try {
    const guestPreparation = await loadQualification();
    close = guestPreparation === null ?
      await launch(firefox, '/profile', '/bridge/sniper-read-only.sock') :
      await launch(firefox, '/profile', '/bridge/sniper-read-only.sock', { guestPreparation });
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
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { firefox } = await import('/app/tests/cloud_browser/node_modules/playwright/index.mjs');
    const code = await runHostedBrowser(firefox);
    // Also bounds shutdown if an unresponsive driver retained its handles.
    process.exit(code);
  } catch {
    console.error('browser_unavailable');
    process.exit(1);
  }
}
