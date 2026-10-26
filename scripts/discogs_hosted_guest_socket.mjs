/** Owner-only fixed no-submit IPC in the existing Firefox process. Disabled
 * unless its local owner supplies both scoped authorization and access review.
 * This does not clear, write or reinterpret the ordinary-reader access stop.
 */
import { createServer } from 'node:net';
import { chmod, lstat, stat, unlink } from 'node:fs/promises';
import { dirname } from 'node:path';
import * as guestPreparation from './discogs_hosted_guest_prepare.mjs';

export const HOSTED_GUEST_SOCKET = '/bridge/sniper-guest-prepare.sock';
const MAX_REQUEST_BYTES = 16384;
const stop = code => ({ status: 'blocked', code, form_visible: false,
  card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false,
  order_status: 'unverified', payment_status: 'unverified' });

/** Synchronous try-lock shared by every operation using this browser context. */
export function createHostedContextLease() {
  let held = false;
  return () => {
    if (held) return null;
    held = true; let released = false;
    return () => { if (!released) { released = true; held = false; } };
  };
}

const OBSERVED = new Set(['summary_supported', 'seller_matches', 'exact_preparation_amounts',
  'quantity_declaration', 'destination_match', 'listing_detail_supported', 'title_continuity',
  'condition_continuity', 'item_price_continuity', 'provider_frame_associated',
  'card_control_unique_visible_enabled', 'existing_guest_form']);
const MISSING = new Set(['quantity', 'destination', 'listing', 'title_continuity',
  'condition_continuity', 'item_price_continuity', ...OBSERVED]);
const CODES = new Set(['scoped_authorization_required', 'prior_attempt_unresolved', 'form_disposition_required',
  'form_disposition_unverified', 'ledger_inspection_unavailable', 'operation_busy', 'navigation_requires_reconciliation',
  'fixed_ledger_required', 'cancelled', 'deadline_expired', 'existing_cart_required', 'single_existing_cart_required',
  'listing_detail_required', 'unrelated_browser_page_present', 'listing_detail_changed', 'access_restricted',
  'listing_detail_unverified', 'page_not_ready', 'existing_guest_form_preserved', 'extra_agreement_required',
  'visible_scope_evidence_missing', 'cart_document_changed', 'cart_scope_unverified', 'checkout_control_unverified',
  'ledger_changed', 'checkout_control_changed', 'checkout_redirected', 'fresh_checkout_required', 'browser_page_set_changed',
  'checkout_document_changed', 'checkout_scope_unverified', 'provider_frame_ambiguous', 'provider_frame_unassociated',
  'provider_not_ready', 'provider_frame_changed', 'card_control_changed', 'inspection_unverified', 'claim_unverified',
  'inspection_stale', 'execution_revalidation_failed', 'outcome_recording_uncertain', 'unknown_internal_stop',
  'guest_form_observation_required', 'operation_unavailable', 'probe_deadline_expired', 'single_current_checkout_required',
  'current_dom_evidence_only', 'probe_unverified']);
function safeResult(value) {
  const result = stop(CODES.has(value?.code) ? value.code : 'operation_unavailable');
  if (value?.status === 'observed' && value.code === 'current_dom_evidence_only') result.status = 'observed';
  for (const key of ['checkout_navigation_issued', 'card_initialization_issued']) {
    if (typeof value?.[key] === 'boolean') result[key] = value[key];
  }
  if (['not_dispatched', 'outcome_requires_reconciliation'].includes(value?.initialization)) result.initialization = value.initialization;
  if (Array.isArray(value?.missing_fields)) result.missing_fields = value.missing_fields.filter(key => MISSING.has(key));
  if (result.status === 'observed' && value.observed && typeof value.observed === 'object') {
    result.observed = Object.fromEntries([...OBSERVED].filter(key => typeof value.observed[key] === 'boolean').map(key => [key, value.observed[key]]));
  }
  return result;
}

export async function listenHostedGuestPreparation(context, socketPath, {
  authorize, accessGuard, acquireContextLease, timeoutMs = 95000,
  // Injectable fixed operations are available only in isolated Node test runs.
  fixtureOperations = null,
} = {}) {
  const fixturePath = process.env.NODE_TEST_CONTEXT !== undefined && /^\/tmp\/sniper-guest-test-[a-z0-9-]{1,48}\.sock$/u.test(socketPath || '');
  if (socketPath !== HOSTED_GUEST_SOCKET && !fixturePath) throw new TypeError('unsupported_guest_socket_path');
  if (typeof authorize !== 'function' || typeof accessGuard !== 'function' || typeof acquireContextLease !== 'function') {
    throw new TypeError('scoped_guest_configuration_required');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 95000) throw new TypeError('invalid_guest_timeout');
  if (fixtureOperations && !fixturePath) throw new TypeError('fixture_operations_forbidden');
  const directory = await (fixturePath ? stat(dirname(socketPath)) : lstat(dirname(socketPath)));
  if (!directory.isDirectory() || directory.isSymbolicLink() || !fixturePath &&
      (directory.uid !== process.getuid() || (directory.mode & 0o7777) !== 0o2710 || directory.gid !== 113)) {
    throw new TypeError('private_bridge_directory_required');
  }
  const operations = fixtureOperations || { prepare_guest_form: guestPreparation.prepareHostedGuestForm, probe_guest_evidence: guestPreparation.probeCurrentGuestPreparationEvidence };
  let closing = false, closePromise;
  const connections = new Map();
  const server = createServer(connection => {
    if (closing) { connection.destroy(); return; }
    const controller = new AbortController();
    connections.set(connection, controller);
    let input = Buffer.alloc(0), started = false, done = false;
    const send = value => { if (!done && !connection.destroyed) { done = true; connection.end(JSON.stringify(value) + '\n'); } };
    connection.setTimeout(timeoutMs);
    connection.on('timeout', () => { controller.abort('request_timed_out'); send(stop('request_timed_out')); });
    connection.on('end', () => { done = true; controller.abort('request_disconnected'); });
    connection.on('close', () => { done = true; controller.abort('request_disconnected'); connections.delete(connection); });
    connection.on('error', () => { done = true; controller.abort('request_disconnected'); });
    connection.on('data', async chunk => {
      if (done || started) return;
      if (input.length + chunk.length > MAX_REQUEST_BYTES) return send(stop('request_too_large'));
      input = Buffer.concat([input, chunk]);
      const newline = input.indexOf(10);
      if (newline < 0) return;
      started = true;
      if (newline !== input.length - 1) return send(stop('unsupported_guest_request'));
      let message;
      try { message = JSON.parse(input.subarray(0, newline).toString('utf8')); } catch { return send(stop('unsupported_guest_request')); }
      if (!message || typeof message !== 'object' || Array.isArray(message) ||
          Object.keys(message).sort().join('|') !== 'action|request' ||
          !['prepare_guest_form', 'probe_guest_evidence'].includes(message.action) ||
          !message.request || typeof message.request !== 'object' || Array.isArray(message.request)) return send(stop('unsupported_guest_request'));
      const release = acquireContextLease();
      if (typeof release !== 'function') return send(stop('read_in_progress'));
      try {
        if (controller.signal.aborted || closing) return;
        // The original stop remains intact. A trusted local reviewer must allow
        // this precise action/request separately; a client cannot grant access.
        if (await accessGuard(message.action, message.request, { signal: controller.signal }) !== true) return send(stop('scoped_access_required'));
        if (controller.signal.aborted || closing) return;
        const authorization = await authorize(message.request, { signal: controller.signal });
        if (controller.signal.aborted || closing) return;
        const result = await operations[message.action](context, message.request, { authorization, signal: controller.signal });
        if (!controller.signal.aborted && !closing) send(safeResult(result));
      } catch { if (!controller.signal.aborted) send(stop('operation_unavailable')); }
      finally {
        // Cancellation can return before a dispatched Playwright call settles.
        // Keep readers and other preparation clients out until the actual call
        // has completed; the module retains its uncertainty fence meanwhile.
        const waitForSettlement = fixtureOperations ? (fixtureOperations.waitForSettlement || (() => Promise.resolve())) :
          guestPreparation.waitForHostedGuestPreparationSettlement;
        if (typeof waitForSettlement !== 'function') return; // Hold the lease if settlement cannot be proven.
        await waitForSettlement(context);
        release();
      }
    });
  });
  let bound = false;
  const old = process.umask(0o077);
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, () => { bound = true; server.off('error', reject); resolve(); }); });
    const socket = await lstat(socketPath);
    if (!socket.isSocket() || socket.uid !== process.getuid()) throw new TypeError('owner_guest_socket_required');
    await chmod(socketPath, 0o600);
  } catch (error) {
    server.close();
    if (bound) await unlink(socketPath).catch(() => {});
    throw error;
  } finally { process.umask(old); }
  return () => {
    if (closePromise) return closePromise;
    closing = true;
    for (const [connection, controller] of connections) { controller.abort('service_closing'); connection.destroy(); }
    closePromise = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
      .then(() => unlink(socketPath).catch(error => { if (error.code !== 'ENOENT') throw error; }));
    return closePromise;
  };
}
