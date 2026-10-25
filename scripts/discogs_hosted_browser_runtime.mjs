/** Staged launcher for the private hosted Firefox display. Do not use against the
 * logged-in profile until the owner approves programmatic read access to it.
 */
import { listenHostedReadOnly } from './discogs_hosted_readonly.mjs';

export async function launchHostedReadOnlyBrowser(firefox, profilePath, socketPath) {
  if (!firefox || typeof firefox.launchPersistentContext !== 'function' ||
      profilePath !== '/profile' || !['/tmp/sniper-read-only.sock', '/bridge/sniper-read-only.sock'].includes(socketPath)) {
    throw new TypeError('unsupported_hosted_browser_configuration');
  }
  const context = await firefox.launchPersistentContext(profilePath, {
    headless: false,
    acceptDownloads: false,
    noDefaultViewport: true,
    firefoxUserPrefs: { 'signon.rememberSignons': false },
  });
  let closeSocket;
  try { closeSocket = await listenHostedReadOnly(context, socketPath); }
  catch (error) { await context.close(); throw error; }
  return async () => {
    try { await closeSocket(); }
    finally { await context.close(); }
  };
}
