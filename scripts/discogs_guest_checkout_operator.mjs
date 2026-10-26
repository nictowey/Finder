/** Manual operator transport, not an autonomous browser/payment adapter.
 * Only metadata crosses stdio. Actual UI actions belong to the reviewed CUA
 * operator, which must check each deadline and one-use request before acting.
 */
import { constants } from 'node:fs';
import { open, readdir, lstat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { prepareFreshGuestCheckoutSession, guestCardScopeDigest, validateGuestCardInspection,
  revalidateGuestCardClick, GUEST_CARD_URL, GUEST_CARD_SELECTOR, GUEST_CARD_FENCE,
  GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE,
  GUEST_CARD_NAVIGATION_REOPEN_REVIEW, GUEST_CARD_NAVIGATION_REOPEN_FENCE } from './discogs_guest_card_prepare.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const LEGACY = [GUEST_CARD_FENCE, GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE,
  GUEST_CARD_NAVIGATION_REOPEN_REVIEW, GUEST_CARD_NAVIGATION_REOPEN_FENCE];
const sha = value => createHash('sha256').update(value).digest('hex');
const uuid = value => typeof value === 'string' && UUID.test(value);
const hash = value => typeof value === 'string' && HASH.test(value);
const num = value => typeof value === 'number' && Number.isFinite(value);
const shape = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const encoded = value => JSON.stringify(value) + '\n';

async function privateDirectory(directory) {
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      (info.mode & 0o7777) !== 0o700) throw new Error('private_state_required');
}
async function readRecord(directory, name) {
  const file = await open(join(directory, name), constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.size > 16_384) throw new Error('invalid_record');
    const bytes = await file.readFile();
    if (bytes.length > 16_384) throw new Error('invalid_record');
    return { name, value: JSON.parse(bytes), hash: sha(bytes) };
  } finally { await file.close(); }
}
async function anchors(directory) {
  const files = [];
  for (const name of LEGACY) files.push(await readRecord(directory, name));
  const [a, b, c, d, e] = files, original = a.value, recovery = b.value,
    retry = c.value, navigation = d.value, last = e.value;
  if (original.schema !== 'discogs_guest_card_init_fence_v1' ||
      original.listing_id !== '1366950910' || original.release_id !== 3422100 ||
      !uuid(original.intent_id) || !hash(original.scope_digest) || !num(original.created_at) ||
      recovery.schema !== 'discogs_guest_card_recovery_review_v1' ||
      recovery.original_fence_sha256 !== a.hash || recovery.original_intent_id !== original.intent_id ||
      recovery.original_scope_digest !== original.scope_digest ||
      retry.schema !== 'discogs_guest_card_recovery_attempt_v1' || retry.attempt !== 1 ||
      retry.original_fence_sha256 !== a.hash || retry.reconciliation_sha256 !== b.hash ||
      retry.intent_id !== recovery.retry_intent_id || retry.scope_digest !== recovery.retry_scope_digest ||
      navigation.schema !== 'discogs_guest_card_navigation_reopen_review_v1' ||
      navigation.original_fence_sha256 !== a.hash || navigation.original_intent_id !== original.intent_id ||
      navigation.original_scope_digest !== original.scope_digest ||
      navigation.recovery_review_sha256 !== b.hash || navigation.recovery_fence_sha256 !== c.hash ||
      navigation.recovery_intent_id !== retry.intent_id || navigation.recovery_scope_digest !== retry.scope_digest ||
      last.schema !== 'discogs_guest_card_navigation_reopen_attempt_v1' || last.attempt !== 1 ||
      last.original_fence_sha256 !== a.hash || last.recovery_review_sha256 !== b.hash ||
      last.recovery_fence_sha256 !== c.hash || last.reconciliation_sha256 !== d.hash ||
      last.intent_id !== navigation.reopen_intent_id || last.scope_digest !== navigation.reopen_scope_digest ||
      !uuid(retry.intent_id) || !hash(retry.scope_digest) || !uuid(last.intent_id) || !hash(last.scope_digest) ||
      !num(retry.created_at) || !num(last.created_at) ||
      !(original.created_at <= retry.created_at && retry.created_at <= last.created_at)) throw new Error('invalid_legacy_chain');
  const manifest = files.map(({ name, hash: digest }) => ({ name, sha256: digest }));
  return { manifest, hash: sha(encoded(manifest)), lastEffectAt: last.created_at };
}
async function lockDirectory(directory) {
  const file = await open(join(directory, 'guest-checkout-operator.lock'),
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600) throw new Error('invalid_lock');
    // dup FD3 shares the parent's open-file description: Node retains the lock
    // after this fixed Python process exits, until this FileHandle closes.
    const probe = spawnSync('python3', ['-c', 'import fcntl; fcntl.flock(3, fcntl.LOCK_EX | fcntl.LOCK_NB)'],
      { stdio: ['ignore', 'ignore', 'ignore', file.fd], timeout: 5000 });
    if (probe.status !== 0) throw new Error('operator_busy');
    return file;
  } catch (error) { await file.close(); throw error; }
}
async function ledger(directory, anchor) {
  const names = (await readdir(directory)).filter(name => name.startsWith('guest-checkout-') && name !== 'guest-checkout-operator.lock');
  if (names.some(name => !/^guest-checkout-[0-9]{6}\.json$/u.test(name)) || names.length > 999_999) throw new Error('invalid_ledger');
  names.sort(); const records = []; let head = anchor.hash, group = null;
  for (let i = 0; i < names.length; i++) {
    if (names[i] !== `guest-checkout-${String(i + 1).padStart(6, '0')}.json`) throw new Error('missing_ledger_record');
    const item = await readRecord(directory, names[i]), value = item.value, p = value.payload;
    if (!shape(value, ['schema', 'sequence', 'kind', 'previous_sha256', 'legacy_anchor_sha256', 'recorded_at', 'payload']) ||
        value.schema !== 'discogs_guest_operator_record_v1' || value.sequence !== i + 1 ||
        value.previous_sha256 !== head || value.legacy_anchor_sha256 !== anchor.hash || !num(value.recorded_at) ||
        !p || !uuid(p.intent_id) || !uuid(p.ready_ref) || !hash(p.scope_digest) ||
        !['ready', 'review', 'attempt', 'outcome'].includes(value.kind)) throw new Error('invalid_ledger');
    if (value.kind === 'ready') group = { intent: p.intent_id, ready: p.ready_ref, scope: p.scope_digest, kind: 'ready' };
    else {
      if (!group || group.intent !== p.intent_id || group.ready !== p.ready_ref || group.scope !== p.scope_digest ||
          ({ review: 'ready', attempt: 'review', outcome: 'attempt' })[value.kind] !== group.kind ||
          value.kind === 'attempt' && p.review_sha256 !== head ||
          value.kind === 'outcome' && p.attempt_sha256 !== head) throw new Error('invalid_ledger_transition');
      group.kind = value.kind;
    }
    records.push(item); head = item.hash;
  }
  return { records, head };
}
async function append(directory, state, anchor, kind, payload) {
  const currentAnchor = await anchors(directory);
  if (currentAnchor.hash !== anchor.hash) throw new Error('legacy_changed');
  const current = await ledger(directory, currentAnchor);
  if (current.head !== state.head || current.records.length !== state.records.length) {
    throw new Error('operator_ledger_changed');
  }
  const sequence = state.records.length + 1;
  const value = { schema: 'discogs_guest_operator_record_v1', sequence, kind,
    previous_sha256: state.head, legacy_anchor_sha256: anchor.hash, recorded_at: Date.now() / 1000, payload };
  const bytes = encoded(value), name = `guest-checkout-${String(sequence).padStart(6, '0')}.json`;
  const parent = await open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const file = await open(join(directory, name), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    try { await file.writeFile(bytes); await file.sync(); } finally { await file.close(); }
    await parent.sync();
  } finally { await parent.close(); }
  const item = { name, value, hash: sha(bytes) }; state.records.push(item); state.head = item.hash;
  return item.hash;
}

/** Actual stdio CLI; directory override is for isolated child-process tests only.
 * The executable entry point below always uses the fixed /state mount.
 */
export async function runGuestCheckoutOperator({ directory = '/state', input = process.stdin, output = process.stdout } = {}) {
  const lines = createInterface({ input, crlfDelay: Infinity }), iterator = lines[Symbol.asyncIterator]();
  const abort = new AbortController(); lines.once('close', () => abort.abort('disconnected'));
  const emit = value => new Promise((resolve, reject) => output.write(encoded(value), error => error ? reject(error) : resolve()));
  const receive = async () => {
    const line = await iterator.next();
    if (line.done || line.value.length > 16_384) throw new Error('invalid_operator_input');
    return JSON.parse(line.value);
  };
  let lock, state, anchor, scope, start, scopeHash, deadline, attemptHash = null, outcomeWritten = false;
  const check = () => {
    if (abort.signal.aborted || !num(deadline) || Date.now() >= deadline ||
        guestCardScopeDigest(scope) !== scopeHash || start.userReady.ready !== true) throw new Error('operator_stopped');
  };
  const verifyAnchors = async () => {
    const currentAnchor = await anchors(directory);
    if (currentAnchor.hash !== anchor.hash) throw new Error('legacy_changed');
    const current = await ledger(directory, currentAnchor);
    if (current.head !== state.head || current.records.length !== state.records.length) {
      throw new Error('operator_ledger_changed');
    }
  };
  const envelope = (stage, requestId, from = null) => ({ intent_id: scope.intent_id,
    ready_ref: start.ready_ref, scope_digest: scopeHash, request_id: requestId, stage, deadline,
    from_document_ref: from });
  const exchange = async (stage, from, keys, guard = null, control = {}) => {
    check(); if (guard && !guard()) throw new Error('operator_stopped');
    await verifyAnchors(); check(); if (guard && !guard()) throw new Error('operator_stopped');
    const binding = envelope(stage, randomUUID(), from);
    await emit({ type: 'operator_action', ...binding, ...control }); check();
    let timer;
    try {
      const ack = await Promise.race([receive(), new Promise((_, reject) => {
        timer = setTimeout(() => { abort.abort('timed_out'); reject(new Error('operator_stopped')); }, Math.max(1, deadline - Date.now()));
      })]);
      check();
      if (!shape(ack, ['type', ...Object.keys(binding), ...keys]) || ack.type !== 'operator_ack' ||
          Object.keys(binding).some(key => ack[key] !== binding[key])) throw new Error('operator_ack_unverified');
      await verifyAnchors(); check();
      return ack;
    } finally { clearTimeout(timer); }
  };
  try {
    await privateDirectory(directory); lock = await lockDirectory(directory);
    anchor = await anchors(directory); state = await ledger(directory, anchor);
    await emit({ type: 'waiting_for_user_ready', latest_hash: state.head,
      legacy_anchor_sha256: anchor.hash, mode: 'operator_assisted_no_card_entry' });
    start = await receive();
    if (!shape(start, ['type', 'ready_ref', 'scope', 'userReady', 'priorFailure', 'expected_latest_hash']) ||
        start.type !== 'start' || !uuid(start.ready_ref) || start.expected_latest_hash !== state.head) throw new Error('invalid_start');
    scope = start.scope; scopeHash = guestCardScopeDigest(scope);
    const prior = start.priorFailure, ready = start.userReady;
    if (!scopeHash || !ready || !prior || state.records.some(item =>
      item.value.kind === 'ready' && (item.value.payload.ready_ref === start.ready_ref || item.value.payload.intent_id === scope.intent_id))) throw new Error('ready_replayed');
    let lastEffectAt = anchor.lastEffectAt;
    for (const item of state.records) {
      if (item.value.kind === 'attempt') lastEffectAt = Math.max(lastEffectAt, item.value.recorded_at);
      if (item.value.kind === 'outcome') lastEffectAt = Math.max(lastEffectAt, item.value.payload.observed_at || 0);
    }
    if (!(num(prior.failure_observed_at) && prior.failure_observed_at >= lastEffectAt &&
        num(prior.order_history_observed_at) && prior.order_history_observed_at >=
          (state.records.at(-1)?.value.recorded_at || lastEffectAt))) throw new Error('reconciliation_required');
    deadline = Math.min(Date.now() + 90_000, scope.expires_at * 1000, ready.expires_at * 1000,
      prior.expires_at * 1000, (prior.order_history_observed_at + 300) * 1000);
    let cartAck, checkoutAck, readyConsumed = false;
    const base = () => ({ intent_id: scope.intent_id, ready_ref: start.ready_ref, scope_digest: scopeHash });
    const ports = {
      openCartViaVisibleControl: async options => {
        deadline = Math.min(deadline, options.deadline); check();
        if (!options.revalidateBeforeDispatch()) throw new Error('operator_stopped');
        await append(directory, state, anchor, 'ready', { ...base(),
          ready_observed_at: ready.observed_at, ready_expires_at: ready.expires_at,
          failure_observed_at: prior.failure_observed_at, history_observed_at: prior.order_history_observed_at });
        readyConsumed = true; check();
        cartAck = await exchange('open_cart', null, ['navigation', 'cart'], options.revalidateBeforeDispatch);
        if (cartAck.cart?.source !== 'operator_verified_current_screen') throw new Error('operator_screen_unverified');
        return cartAck.navigation;
      },
      inspectCurrentCart: async () => cartAck.cart,
      clickGoToCheckout: async (binding, options) => {
        deadline = Math.min(deadline, options.deadline); check();
        checkoutAck = await exchange('go_to_checkout', binding.from_document_ref,
          ['transition', 'checkout', 'card_control'], options.revalidateBeforeDispatch,
          { control_ref: binding.control_ref, cart_fingerprint: binding.cart_fingerprint });
        if (checkoutAck.checkout?.source !== 'operator_verified_current_screen') throw new Error('operator_screen_unverified');
        if (checkoutAck.transition?.cart_fingerprint !== binding.cart_fingerprint ||
            checkoutAck.transition?.control_ref !== binding.control_ref) throw new Error('transition_unverified');
        return checkoutAck.transition;
      },
      inspectFreshCheckout: async () => checkoutAck.checkout,
    };
    const fresh = await prepareFreshGuestCheckoutSession(ports, scope,
      { userReady: ready, priorFailure: prior, signal: abort.signal,
        timeoutMs: Math.max(1, Math.floor(deadline - Date.now())) });
    check();
    if (fresh.status !== 'prepared_review_only' || !readyConsumed) throw new Error('fresh_checkout_unverified');
    deadline = Math.min(deadline, fresh.provenance.expires_at * 1000); check();
    const inspection = { schema: 'discogs_guest_card_inspection_v1', url: GUEST_CARD_URL,
      access: checkoutAck.checkout.access, control: checkoutAck.card_control, evidence: checkoutAck.checkout.evidence };
    const parsed = validateGuestCardInspection(inspection, scope);
    if (!parsed) throw new Error('guest_control_unverified');
    deadline = Math.min(deadline, parsed.expires_at * 1000); check();
    const binding = Object.freeze({ url: GUEST_CARD_URL, selector: GUEST_CARD_SELECTOR,
      control_ref: parsed.control_ref, scope_digest: scopeHash, intent_id: scope.intent_id,
      inspection_fingerprint: parsed.inspection_fingerprint, expected_scope: Object.freeze({ ...scope }) });
    const reviewHash = await append(directory, state, anchor, 'review', { ...base(),
      latest_prior_hash: start.expected_latest_hash, legacy_files: anchor.manifest,
      failure_observed_at: prior.failure_observed_at, history_observed_at: prior.order_history_observed_at,
      no_card: true, no_pay: true, no_approval: true, no_capture: true, provenance: fresh.provenance });
    check(); await verifyAnchors(); check();
    attemptHash = await append(directory, state, anchor, 'attempt', { ...base(), review_sha256: reviewHash,
      control_ref: parsed.control_ref, inspection_fingerprint: parsed.inspection_fingerprint, deadline });
    check(); await verifyAnchors(); check();
    if (!revalidateGuestCardClick(binding, inspection)) throw new Error('guest_control_changed');
    const ack = await exchange('open_guest_card', fresh.provenance.checkout_document_ref,
      ['outcome', 'observed_at'], null,
      { control_ref: parsed.control_ref, inspection_fingerprint: parsed.inspection_fingerprint });
    if (!['guest_form_visible', 'SESSION_EXPIRED', 'unknown', 'access_restricted'].includes(ack.outcome) ||
        !num(ack.observed_at) || ack.observed_at > Date.now() / 1000 ||
        ack.observed_at < state.records.at(-1).value.recorded_at) throw new Error('outcome_unverified');
    await append(directory, state, anchor, 'outcome', { ...base(), attempt_sha256: attemptHash,
      outcome: ack.outcome, observed_at: ack.observed_at }); outcomeWritten = true; check();
    await emit({ type: 'finished', code: 'operator_outcome_recorded', outcome: ack.outcome,
      order_status: 'unverified', payment_status: 'unverified', autonomous_checkout: false });
    return 0;
  } catch {
    if (attemptHash && !outcomeWritten) {
      await append(directory, state, anchor, 'outcome', { intent_id: scope.intent_id,
        ready_ref: start.ready_ref, scope_digest: scopeHash, attempt_sha256: attemptHash,
        outcome: 'unknown', observed_at: Date.now() / 1000 }).catch(() => {});
    }
    await emit({ type: 'stopped', code: 'operator_blocked_or_uncertain',
      order_status: 'unverified', payment_status: 'unverified' }).catch(() => {});
    return 2;
  } finally { abort.abort('finished'); lines.close(); if (lock) await lock.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.slice(2).some(value => value !== '--waiting-ready') || process.argv.length > 3) process.exit(2);
  process.exit(await runGuestCheckoutOperator());
}
