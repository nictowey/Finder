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
const DETAIL_CODES = new Set(['invalid_exact_identity', 'dom_unavailable', 'listing_url_mismatch',
  'listing_not_ready', 'listing_main_unavailable', 'listing_release_mismatch', 'listing_grades_unavailable',
  'listing_changed_during_read', 'listing_fields_unavailable', 'listing_dom_read_failed',
  'listing_detail_required', 'listing_detail_unverified']);
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
  'current_dom_evidence_only', 'probe_unverified', 'approved_saved_destination_bound',
  'readonly_authorization_required', 'probe_job_replayed', 'probe_reference_unverified',
  'probe_reference_expired', 'probe_document_changed', 'saved_destination_snapshot_changed',
  'saved_destination_unverified', 'probe_capacity_exceeded', 'single_current_merchant_page_required',
  'cart_structure_captured', 'cart_capture_unverified']);
const PUBLIC_TAGS = new Set(['A', 'SPAN', 'STRONG', 'B', 'EM', 'I', 'SMALL', 'BR', '#text']);
const GRADE_TEXT = new Set(['Media', 'Media:', 'Sleeve', 'Sleeve:', 'P', 'Poor', 'Poor (P)',
  'F', 'Fair', 'Fair (F)', 'G', 'Good', 'Good (G)', 'G+', 'Good Plus', 'Good Plus (G+)',
  'VG', 'Very Good', 'Very Good (VG)', 'VG+', 'Very Good Plus', 'Very Good Plus (VG+)',
  'NM', 'Near Mint', 'Near Mint (NM)', 'Near Mint (NM or M-)', 'M', 'Mint', 'Mint (M)', 'Generic']);
const GRADE_LABELS = ['Media', 'Media:', 'Media Condition', 'Media Condition:', 'Sleeve', 'Sleeve:', 'Sleeve Condition', 'Sleeve Condition:'];
const GRADE_VALUES = [...GRADE_TEXT].filter(value => !GRADE_LABELS.includes(value));
for (const label of GRADE_LABELS) GRADE_TEXT.add(label);
for (const label of GRADE_LABELS.filter(value => value.endsWith(':'))) {
  for (const grade of GRADE_VALUES) GRADE_TEXT.add(`${label} ${grade}`);
}
const boundedPublicText = (text, max) => typeof text === 'string' && text.length <= max &&
  !/[\u0000-\u001f\u007f]/u.test(text) ? text : null;
function publicNode(node, grade = false) {
  if (!node || !PUBLIC_TAGS.has(node.tag) || !Array.isArray(node.path) || node.path.length > 4 ||
      node.path.some(index => !Number.isInteger(index) || index < 0 || index > 31)) return null;
  const text = boundedPublicText(node.text, 160);
  if (text === null || grade && !GRADE_TEXT.has(text) &&
      !(text === '' && node.label === null && node.grade === null)) return null;
  const out = { path: [...node.path], tag: node.tag, text };
  if (grade) {
    if (!['Media', 'Sleeve', null].includes(node.label) ||
        !['P', 'F', 'G', 'G+', 'VG', 'VG+', 'NM', 'M', 'Generic', null].includes(node.grade)) return null;
    out.label = node.label; out.grade = node.grade;
  }
  return out;
}
function sanitizedCartStructure(value) {
  if (value?.row_count !== 1 || value.listing_href !== '/sell/item/1366950910' ||
      value.title_link?.tag !== 'A' || boundedPublicText(value.title_link.text, 300) === null ||
      !Array.isArray(value.title_link.nodes) || value.title_link.nodes.length > 24 ||
      !Array.isArray(value.grade_nodes) || value.grade_nodes.length > 12) return null;
  const title = value.title_link.nodes.map(node => publicNode(node));
  const grades = value.grade_nodes.map(node => publicNode(node, true));
  if (title.some(node => node === null) || grades.some(node => node === null)) return null;
  return { row_count: 1, listing_href: '/sell/item/1366950910',
    title_link: { tag: 'A', text: value.title_link.text, nodes: title }, grade_nodes: grades };
}
function safeResult(value, { captureAllowed = false } = {}) {
  const result = stop(CODES.has(value?.code) ? value.code : 'operation_unavailable');
  if (value?.status === 'observed' && ['current_dom_evidence_only', 'approved_saved_destination_bound'].includes(value.code)) result.status = 'observed';
  if (value?.status === 'observed' && value.code === 'cart_structure_captured') {
    const structure = captureAllowed ? sanitizedCartStructure(value.cart_structure) : null;
    if (!structure) return stop('cart_capture_unverified');
    result.status = 'observed'; result.cart_structure = structure;
  }
  for (const key of ['checkout_navigation_issued', 'card_initialization_issued']) {
    if (typeof value?.[key] === 'boolean') result[key] = value[key];
  }
  if (['not_dispatched', 'outcome_requires_reconciliation'].includes(value?.initialization)) result.initialization = value.initialization;
  if (Array.isArray(value?.missing_fields)) result.missing_fields = value.missing_fields.filter(key => MISSING.has(key));
  if (result.status === 'observed' && value.observed && typeof value.observed === 'object') {
    result.observed = Object.fromEntries([...OBSERVED].filter(key => typeof value.observed[key] === 'boolean').map(key => [key, value.observed[key]]));
    if (value.observed.destination_match === null) result.observed.destination_match = null;
  }
  if (result.status === 'observed') {
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value.document_ref || '')) result.document_ref = value.document_ref;
    if (['cart', 'enhanced_checkout'].includes(value.merchant_route)) result.merchant_route = value.merchant_route;
    for (const key of ['address_present', 'destination_binding_available', 'destination_match']) {
      if (typeof value[key] === 'boolean' || key === 'destination_match' && value[key] === null) result[key] = value[key];
    }
    if (value.structure && typeof value.structure === 'object') {
      result.structure = {};
      for (const key of ['item_row_count', 'address_container_count']) {
        if ([0, 1, 2].includes(value.structure[key])) result.structure[key] = value.structure[key];
      }
      if (typeof value.structure.item_title_present === 'boolean') result.structure.item_title_present = value.structure.item_title_present;
      const grades = ['P', 'F', 'G', 'G+', 'VG', 'VG+', 'NM', 'M', null];
      if (grades.includes(value.structure.media_grade)) result.structure.media_grade = value.structure.media_grade;
      if ([...grades, 'Generic'].includes(value.structure.sleeve_grade)) result.structure.sleeve_grade = value.structure.sleeve_grade;
      const lines = value.structure.address_line_count;
      if (lines === null || Number.isInteger(lines) && lines >= 0 && lines <= 6) result.structure.address_line_count = lines;
    }
    if (value.detail_diagnostic && typeof value.detail_diagnostic === 'object') {
      const detail = value.detail_diagnostic;
      result.detail_diagnostic = {
        error_code: detail.error_code === null ? null : DETAIL_CODES.has(detail.error_code) ? detail.error_code : 'listing_detail_unverified',
      };
      if (['missing', 'ambiguous', 'observed', 'incomplete'].includes(detail.status)) result.detail_diagnostic.status = detail.status;
      if (['loading', 'interactive', 'complete', 'unknown'].includes(detail.ready_state)) result.detail_diagnostic.ready_state = detail.ready_state;
      for (const key of ['main_count', 'release_link_count', 'grade_count']) {
        if ([0, 1, 2, null].includes(detail[key])) result.detail_diagnostic[key] = detail[key];
      }
    }
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
        if (!controller.signal.aborted && !closing) send(safeResult(result, { captureAllowed: message.action === 'probe_guest_evidence' &&
          message.request.phase === 'capture_cart_structure' && authorization?.mode === 'capture_cart_structure' }));
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
