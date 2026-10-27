/** Fixed owner-only local runtime control. No browser/controller, credential,
 * payment policy or confirmation is created here. Missing mode remains OFF. */
import { createServer } from 'node:net';
import { constants } from 'node:fs';
import { open, lstat, chmod, unlink } from 'node:fs/promises';
import { dirname, isAbsolute, resolve, basename } from 'node:path';

export const HOSTED_PAYMENT_CONTROL_SOCKET = '/bridge/sniper-payment-control.sock';
export const HOSTED_PAYMENT_MODE = '/bridge/guest-card-actions/enhanced-runtime-mode.json';
const OFF = Object.freeze({ entryGate: false, liveGate: false, controlEnabled: false });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const COMMANDS = Object.freeze({ inspect_readiness: 'inspectReadiness', begin_secure_entry: 'beginSecureEntry',
  complete_secure_entry: 'completeSecureEntry', resume_after_entry: 'resumeAfterEntry', attempt_payment: 'attemptPayment' });
const DEADLINES = Object.freeze({ inspect_readiness: 10_000, begin_secure_entry: 15_000,
  complete_secure_entry: 5000, resume_after_entry: 15_000, attempt_payment: 35_000 });
const ENTRY_KEYS = ['schema', 'release_id', 'listing_id', 'seller', 'quantity', 'currency', 'max_total',
  'expected_guest_head', 'expected_claim_sha256', 'expires_at'];
const PAY_KEYS = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller', 'one_copy_limit', 'currency',
  'max_total', 'rules_revision', 'quote_fingerprint', 'destination_ref', 'payment_ref', 'terms_fingerprint', 'expires_at'];
const CODES = new Set(['hosted_payment_disabled', 'hosted_payment_authority_observed', 'hosted_payment_authority_unavailable',
  'hosted_secure_entry_disabled', 'hosted_secure_entry_already_started', 'hosted_secure_entry_not_resumable',
  'hosted_secure_entry_unavailable', 'hosted_browser_busy', 'hosted_prior_submit_uncertain', 'entry_document_unverified',
  'public_entry_evidence_unverified', 'anchored_guest_claim_observed', 'secure_entry_completed', 'secure_entry_completion_unverified',
  'hosted_post_entry_confirmation_unavailable', 'hosted_post_entry_handoff_unavailable', 'hosted_payment_outcome_uncertain',
  'secure_entry_unavailable', 'secure_entry_not_active', 'fresh_post_entry_context_verified', 'fresh_post_entry_evidence_required',
  'fixed_pay_disabled_or_busy', 'fixed_pay_disabled_or_entry_incomplete', 'fixed_pay_dispatch_unavailable',
  'fixed_pay_fence_unavailable', 'fixed_control_changed', 'fixed_quote_binding_changed', 'durable_order_fence_unverified',
  'enhanced_payment_disabled', 'exact_payment_scope_required', 'trusted_payment_ports_unavailable',
  'payment_attempt_busy', 'payment_context_changed', 'submission_unknown']);
const UNCERTAIN_CODES = new Set(['submission_unknown', 'hosted_payment_outcome_uncertain', 'durable_order_fence_unverified']);
const STATES = new Set(['no_order_intent', 'prepared', 'submission_unknown', 'order_seen', 'paid', 'unpaid', 'pending',
  'guest_form_observed_repeat_blocked', 'reviewed_form_disposition', 'known_session_expired', 'no_prior_attempt']);
const EFFECTS = ['checkout_navigation_issued', 'navigation_issued', 'card_initialization_issued', 'card_data_entered',
  'pay_issued', 'payment_issued', 'dispatch_issued', 'pay_invoked', 'payment_invoked', 'approval_invoked', 'capture_invoked'];
const exact = (value, keys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(descriptors).length === keys.length && keys.every(key =>
    descriptors[key]?.enumerable === true && Object.hasOwn(descriptors[key], 'value'));
};
const number = value => typeof value === 'number' && Number.isFinite(value);
const stop = (code, uncertain = false) => ({ schema: 'discogs_hosted_payment_control_response_v1', status: 'blocked', code,
  payment_allowed: false, purchase_allowed: false, may_submit_again: false,
  ...(uncertain ? { requires_reconciliation: true, outcome: 'uncertain' } : {}) });

async function privateDirectory(path, socketParent = false) {
  const info = await lstat(path), mode = info.mode & 0o7777;
  // The existing /bridge mount is owner10001/2710: its group may traverse,
  // never read/write. Preserve it; the socket itself remains owner-only0600.
  // Policy/mode-file directories retain the stricter exact0700 requirement.
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      !(mode === 0o700 || socketParent && mode === 0o2710))
    throw new Error('owner_private_payment_directory_required');
}
async function loadMode(path) {
  try {
    await privateDirectory(dirname(path));
    const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    let value;
    try {
      const info = await handle.stat();
      if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 || info.nlink !== 1 || info.size < 2 || info.size > 256) return { ...OFF };
      const bytes = await handle.readFile(); if (bytes.length > 256) return { ...OFF }; value = JSON.parse(bytes.toString('utf8'));
    } finally { await handle.close(); }
    if (!exact(value, ['schema', 'mode']) || value.schema !== 'discogs_hosted_payment_mode_v1' || !['off', 'secure_entry', 'payment'].includes(value.mode)) return { ...OFF };
    return { entryGate: value.mode !== 'off', liveGate: value.mode === 'payment', controlEnabled: value.mode !== 'off' };
  } catch { return { ...OFF }; }
}
export function loadHostedPaymentMode() { return loadMode(HOSTED_PAYMENT_MODE); }
export function loadHostedPaymentModeFixture(path) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !isAbsolute(path || '') || resolve(path) !== path) throw new TypeError('fixture_context_required');
  return loadMode(path);
}
function entryScope(value, now) {
  return exact(value, ENTRY_KEYS) && value.schema === 'discogs_secure_entry_scope_v1' &&
    value.release_id === 3422100 && value.listing_id === '1366950910' && value.seller === 'Mailman21842' &&
    value.quantity === 1 && value.currency === 'USD' && value.max_total === '5.05' &&
    typeof value.expected_guest_head === 'string' && HASH.test(value.expected_guest_head) &&
    typeof value.expected_claim_sha256 === 'string' && HASH.test(value.expected_claim_sha256) &&
    number(value.expires_at) && now < value.expires_at && value.expires_at - now <= 300;
}
function payScope(value, now) {
  return exact(value, PAY_KEYS) && [value.intent_id, value.approval_ref, value.destination_ref, value.payment_ref]
    .every(item => typeof item === 'string' && UUID.test(item)) && value.release_id === 3422100 &&
    value.listing_id === '1366950910' && value.seller === 'Mailman21842' && value.one_copy_limit === 1 &&
    value.currency === 'USD' && value.max_total === '5.05' && Number.isSafeInteger(value.rules_revision) && value.rules_revision >= 0 &&
    [value.quote_fingerprint, value.terms_fingerprint].every(item => typeof item === 'string' && HASH.test(item)) &&
    number(value.expires_at) && now < value.expires_at && value.expires_at - now <= 300;
}
export function sanitizeHostedPaymentControlRequest(value, { now = Date.now() / 1000 } = {}) {
  if (!number(now) || !exact(value, ['schema', 'command', 'request']) || value.schema !== 'discogs_hosted_payment_control_request_v1' ||
      !Object.hasOwn(COMMANDS, value.command)) return null;
  if (value.command === 'complete_secure_entry') {
    if (!exact(value.request, ['entry_ref', 'completed_at']) || typeof value.request.entry_ref !== 'string' || !UUID.test(value.request.entry_ref) ||
        !number(value.request.completed_at) || value.request.completed_at > now || now - value.request.completed_at >= 60) return null;
    return { schema: value.schema, command: value.command, request: { ...value.request } };
  }
  if (!exact(value.request, ['scope'])) return null;
  const scope = value.request.scope;
  const accepted = value.command === 'begin_secure_entry' ? entryScope(scope, now) : value.command === 'inspect_readiness' ?
    entryScope(scope, now) || payScope(scope, now) : payScope(scope, now);
  return accepted ? { schema: value.schema, command: value.command, request: { scope: Object.freeze({ ...scope }) } } : null;
}
export function sanitizeHostedPaymentControlResult(value, command, { now = Date.now() / 1000, request = null } = {}) {
  const effects = Object.fromEntries(EFFECTS.filter(key => Object.getOwnPropertyDescriptor(value || {}, key)?.value === true).map(key => [key, true]));
  const fields = value && typeof value === 'object' && !Array.isArray(value) ? Object.getOwnPropertyDescriptors(value) : {};
  const field = key => Object.hasOwn(fields[key] || {}, 'value') ? fields[key].value : undefined;
  if (Object.keys(effects).length) return { ...stop('payment_control_outcome_uncertain', true), ...effects };
  const status = field('status'), code = field('code');
  if (status === 'entry_ready' && command === 'begin_secure_entry') {
    const ref = field('entry_ref'), started = field('entry_started_at');
    if (typeof ref !== 'string' || !UUID.test(ref) || !number(started) || started > now || now - started >= 300 ||
        field('reads_suspended') !== true || field('payment_allowed') !== false) return stop('payment_control_response_unverified', true);
    return { ...stop('secure_entry_ready'), status: 'entry_ready', entry_ref: ref, entry_started_at: started,
      reads_suspended: true, ...(field('entry_evidence') === 'bound_public_guest_form_verified' ? { entry_evidence: field('entry_evidence') } : {}) };
  }
  if (!['observed', 'blocked'].includes(status) || !CODES.has(code)) return stop('payment_control_response_unverified', true);
  const result = { ...stop(code, UNCERTAIN_CODES.has(code)), status };
  if (status === 'observed' && code === 'secure_entry_completed') {
    const ref = field('entry_ref'), start = field('entry_started_at'), completed = field('entry_completed_at');
    if (command !== 'complete_secure_entry' || request !== null && request.entry_ref !== ref || typeof ref !== 'string' || !UUID.test(ref) || !number(start) || !number(completed) ||
        start > completed || completed > now || completed - start > 300) return stop('payment_control_response_unverified', true);
    Object.assign(result, { entry_ref: ref, entry_started_at: start, entry_completed_at: completed });
  }
  for (const key of ['guest_state', 'real_order_state']) if (STATES.has(field(key))) result[key] = field(key);
  if (field('test_mode') === 'exact_kc_one_copy_only') result.test_mode = field('test_mode');
  for (const key of ['browser_evidence_pending', 'reads_suspended']) if (typeof field(key) === 'boolean') result[key] = field(key);
  return result;
}
async function listen(runtime, socketPath, options, fixture) {
  const keys = fixture ? ['enabled', 'timeoutMs'] : ['enabled'];
  if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).some(key => !keys.includes(key)) ||
      options.enabled !== undefined && typeof options.enabled !== 'boolean') throw new TypeError('fixed_payment_control_configuration_required');
  if (options.enabled !== true) return async () => {};
  if (fixture ? process.env.NODE_TEST_CONTEXT === undefined || !isAbsolute(socketPath || '') || resolve(socketPath) !== socketPath ||
      basename(socketPath) !== 'payment-control.sock' : socketPath !== HOSTED_PAYMENT_CONTROL_SOCKET) throw new TypeError('fixed_payment_control_socket_required');
  if (!runtime || [...Object.values(COMMANDS), 'inspectEntryReadiness'].some(method => typeof runtime[method] !== 'function')) throw new TypeError('fixed_payment_runtime_required');
  if (options.timeoutMs !== undefined && (!fixture || !Number.isInteger(options.timeoutMs) || options.timeoutMs < 1 || options.timeoutMs > 35_000))
    throw new TypeError('bounded_fixture_deadline_required');
  await privateDirectory(dirname(socketPath), true);
  try { await lstat(socketPath); throw new Error('payment_control_path_exists'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  let closing = false, uncertain = false, active = false, closePromise = null, owned = null;
  const connections = new Map(), pending = new Set();
  const server = createServer(connection => {
    if (closing || connections.size >= 8) { connection.end(JSON.stringify(stop('payment_control_unavailable')) + '\n'); return; }
    const controller = new AbortController(); let input = Buffer.alloc(0), started = false, done = false, timer;
    const send = result => { if (!done && !connection.destroyed) { done = true; clearTimeout(timer); connection.end(JSON.stringify(result) + '\n'); } };
    const cancel = reason => { if (started) uncertain = true; controller.abort(reason); };
    const deadline = milliseconds => { clearTimeout(timer); timer = setTimeout(() => {
      cancel('payment_control_request_timed_out'); send(stop('payment_control_request_timed_out', started));
    }, milliseconds); };
    deadline(options.timeoutMs ?? 5000); connections.set(connection, controller);
    connection.on('end', () => { if (!done) cancel('payment_control_client_disconnected'); done = true; clearTimeout(timer); });
    connection.on('close', () => { if (!done) cancel('payment_control_client_disconnected'); done = true; clearTimeout(timer); connections.delete(connection); });
    connection.on('error', () => { if (!done) cancel('payment_control_client_disconnected'); done = true; clearTimeout(timer); });
    connection.on('data', chunk => {
      if (done) return;
      if (started) { cancel('unsupported_payment_request'); send(stop('unsupported_payment_request', true)); return; }
      if (input.length + chunk.length > 8192) { send(stop('payment_control_request_too_large')); return; }
      input = Buffer.concat([input, chunk]); const newline = input.indexOf(10); if (newline < 0) return;
      if (newline !== input.length - 1) { send(stop('unsupported_payment_request')); return; }
      let message;
      try { message = sanitizeHostedPaymentControlRequest(JSON.parse(input.subarray(0, newline).toString('utf8'))); }
      catch { message = null; }
      if (!message) { send(stop('unsupported_payment_request')); return; }
      if (uncertain) { send(stop('payment_control_reconciliation_required', true)); return; }
      if (active) { send(stop('payment_control_busy')); return; }
      started = true; active = true; deadline(options.timeoutMs ?? DEADLINES[message.command]);
      const task = (async () => {
        try {
          if (controller.signal.aborted || closing) return;
          const argument = message.command === 'complete_secure_entry' ? message.request : message.request.scope;
          const method = message.command === 'inspect_readiness' && argument.schema === 'discogs_secure_entry_scope_v1' ? 'inspectEntryReadiness' : COMMANDS[message.command];
          const value = await runtime[method](argument, { signal: controller.signal });
          if (controller.signal.aborted || closing) return;
          const result = sanitizeHostedPaymentControlResult(value, message.command, { request: message.request });
          if (result.requires_reconciliation) uncertain = true;
          send(result);
        } catch { if (!controller.signal.aborted && !closing) { uncertain = true; send(stop('payment_control_operation_uncertain', true)); } }
        finally { active = false; }
      })();
      pending.add(task); void task.then(() => pending.delete(task), () => pending.delete(task));
    });
  });
  let bound = false; const mask = process.umask(0o077);
  try {
    await new Promise((resolveListen, reject) => { server.once('error', reject); server.listen(socketPath, () => {
      bound = true; server.off('error', reject); resolveListen();
    }); });
    owned = await lstat(socketPath);
    if (!owned.isSocket() || owned.uid !== process.getuid()) throw new Error('owner_payment_socket_required');
    await chmod(socketPath, 0o600);
  } catch (error) {
    if (bound) await new Promise(resolveClose => server.close(resolveClose));
    throw error;
  } finally { process.umask(mask); }
  const close = () => {
    if (closePromise) return closePromise;
    closing = true;
    for (const [connection, controller] of connections) { controller.abort('payment_control_closing'); connection.destroy(); }
    closePromise = (async () => {
      // Node unlinks a bound Unix path on close. Refuse closure while its name
      // refers to somebody else's inode rather than deleting a replacement.
      let current; try { current = await lstat(socketPath); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (current && (!current.isSocket() || current.dev !== owned.dev || current.ino !== owned.ino || current.uid !== owned.uid)) {
        server.unref(); throw new Error('payment_control_socket_replaced');
      }
      // Keep the owned name bound until actual runtime continuations settle;
      // a replacement listener must not overlap an aborted old invocation.
      await Promise.allSettled([...pending]);
      try { current = await lstat(socketPath); } catch (error) { if (error.code !== 'ENOENT') throw error; current = null; }
      if (current && (!current.isSocket() || current.dev !== owned.dev || current.ino !== owned.ino || current.uid !== owned.uid)) {
        server.unref(); throw new Error('payment_control_socket_replaced');
      }
      await new Promise((resolveClose, reject) => server.close(error => error ? reject(error) : resolveClose()));
      try { const remaining = await lstat(socketPath); if (remaining.dev === owned.dev && remaining.ino === owned.ino && remaining.isSocket()) await unlink(socketPath); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    })().catch(error => { closePromise = null; throw error; });
    return closePromise;
  };
  return close;
}
export function listenHostedPaymentControl(runtime, fixedSocket = HOSTED_PAYMENT_CONTROL_SOCKET, options = { enabled: false }) {
  return listen(runtime, fixedSocket, options, false);
}
export function listenHostedPaymentControlFixture(runtime, socketPath, options = { enabled: false }) {
  return listen(runtime, socketPath, options, true);
}
