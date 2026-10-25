/** Fixed private browser runner. No public debugger, checkout or arbitrary command API. */
import { firefox } from '/app/tests/cloud_browser/node_modules/playwright/index.mjs';
import { launchHostedReadOnlyBrowser } from './discogs_hosted_browser_runtime.mjs';

const close = await launchHostedReadOnlyBrowser(
  firefox, '/profile', '/bridge/sniper-read-only.sock');
console.log('private_browser_read_only_ready');
await new Promise(resolve => {
  process.once('SIGTERM', resolve);
  process.once('SIGINT', resolve);
});
await close();
