/** Fixed observations inside an owner-approved persistent Firefox context.
 * This module does not launch a browser, navigate, log a session, mutate a cart,
 * accept an agreement, or expose Playwright's general control protocol.
 */
import { createServer } from 'node:net';
import { chmod, unlink } from 'node:fs/promises';
import { collectCartDom, inspectCurrentCartDom } from './discogs_cart_inspector.mjs';
import { collectReleaseMarketplaceDom } from './discogs_release_snapshot.mjs';

const ID = /^[1-9][0-9]{0,15}$/u;
const MAX_REQUEST_BYTES = 512;
const MAX_RESPONSE_BYTES = 262_144;
const READ_TIMEOUT_MS = 5_000;

function stopped(code) {
  return { status: 'blocked', code, source: 'hosted_browser_read_only',
    stop_required: true, purchase_allowed: false };
}

function requestShape(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) return null;
  const keys = Reflect.ownKeys(request);
  if (keys.length !== 2 || !keys.includes('action')) return null;
  const action = Object.getOwnPropertyDescriptor(request, 'action')?.value;
  const field = action === 'current_release' ? 'release_id' : action === 'current_cart' ? 'listing_id' : null;
  if (!field || !keys.includes(field)) return null;
  const id = Object.getOwnPropertyDescriptor(request, field)?.value;
  return typeof id === 'string' && ID.test(id) && Number.isSafeInteger(Number(id)) ? { action, id } : null;
}

function exactUrl(action, id, url) {
  if (typeof url !== 'string' || url.length > 100 || /[\s\x00-\x1f\x7f]/u.test(url)) return false;
  if (action === 'current_release') return url === `https://www.discogs.com/sell/release/${id}` ||
    url === `https://www.discogs.com/sell/release/${id}/`;
  return /^https:\/\/(?:www\.)?discogs\.com\/(?:sell|shop)\/cart\/?$/u.test(url);
}

function bounded(result) {
  try {
    return Buffer.byteLength(JSON.stringify(result), 'utf8') <= MAX_RESPONSE_BYTES ? result : stopped('observation_too_large');
  } catch { return stopped('observation_unserializable'); }
}

async function beforeDeadline(operation) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), READ_TIMEOUT_MS); }),
    ]);
  } finally { clearTimeout(timer); }
}

/** Observe an already-open exact page. No page goto, click, cookie or storage APIs. */
export async function observeCurrentHostedPage(context, request) {
  const input = requestShape(request);
  if (!input) return stopped('unsupported_read_request');
  if (!context || typeof context.pages !== 'function') return stopped('browser_context_unavailable');
  try {
    const pages = context.pages();
    if (!Array.isArray(pages) || pages.length > 16) return stopped('browser_pages_unavailable');
    const matching = pages.filter(page => page && typeof page.url === 'function' &&
      typeof page.evaluate === 'function' && exactUrl(input.action, input.id, page.url()));
    if (matching.length !== 1) return stopped('exact_current_page_not_unique');
    const page = matching[0], originalUrl = page.url();
    if (input.action === 'current_cart') {
      const restrictedPage = Object.freeze({
        url: () => page.url(),
        evaluate: (fn, id) => {
          if (fn !== collectCartDom || id !== input.id) throw new TypeError('unsupported_collector');
          return page.evaluate(fn, id);
        },
      });
      const cart = await beforeDeadline(() => inspectCurrentCartDom(restrictedPage, { expectedListingId: input.id }));
      if (page.url() !== originalUrl) return stopped('page_changed_during_read');
      return bounded({ ...cart, source: 'hosted_browser_read_only', stop_required: true, purchase_allowed: false });
    }
    const observation = await beforeDeadline(() => page.evaluate(collectReleaseMarketplaceDom, input.id));
    if (page.url() !== originalUrl) return stopped('page_changed_during_read');
    if (!observation || observation.source !== 'discogs_rendered_release' ||
        observation.release_id !== Number(input.id) || observation.purchase_allowed !== false ||
        !exactUrl(input.action, input.id, observation.url)) return stopped('release_observation_unverified');
    return bounded({ status: observation.status, source: 'hosted_browser_read_only',
      observation, stop_required: true, purchase_allowed: false });
  } catch { return stopped('hosted_read_failed'); }
}

/** Local Unix socket only; the caller must keep it inside a private container. */
export async function listenHostedReadOnly(context, socketPath) {
  if (typeof socketPath !== 'string' || !/^\/tmp\/[a-z][a-z0-9-]{0,47}\.sock$/u.test(socketPath)) {
    throw new TypeError('unsupported_socket_path');
  }
  let busy = false;
  const server = createServer(connection => {
    let input = Buffer.alloc(0), done = false;
    connection.setTimeout(READ_TIMEOUT_MS);
    const send = result => {
      if (done) return;
      done = true;
      connection.end(JSON.stringify(bounded(result)) + '\n');
    };
    connection.on('timeout', () => send(stopped('request_timed_out')));
    connection.on('error', () => { done = true; });
    connection.on('data', async chunk => {
      if (done) return;
      if (input.length + chunk.length > MAX_REQUEST_BYTES) return send(stopped('request_too_large'));
      input = Buffer.concat([input, chunk]);
      const newline = input.indexOf(10);
      if (newline < 0) return;
      if (newline !== input.length - 1 || busy) return send(stopped(busy ? 'read_in_progress' : 'unsupported_read_request'));
      let request;
      try { request = JSON.parse(input.subarray(0, newline).toString('utf8')); }
      catch { return send(stopped('unsupported_read_request')); }
      busy = true;
      try { send(await observeCurrentHostedPage(context, request)); }
      finally { busy = false; }
    });
  });
  const previousUmask = process.umask(0o077);
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(socketPath, () => { server.off('error', reject); resolve(); });
    });
    await chmod(socketPath, 0o600);
  } catch (error) {
    server.close();
    throw error;
  } finally { process.umask(previousUmask); }
  return async () => {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await unlink(socketPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
  };
}
