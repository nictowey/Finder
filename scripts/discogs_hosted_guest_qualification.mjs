/** Thin, local read-only qualification issuer. No credentials, browser calls or
 * transaction grants. A qualification job admits inspect and same-document
 * destination confirmation; a distinct capture job admits one public cart read.
 * Policy absence keeps the socket off.
 */
import { constants } from 'node:fs';
import { open, lstat } from 'node:fs/promises';
import { dirname, join, isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';

export const HOSTED_QUALIFICATION_POLICY = '/bridge/guest-qualification.json';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const SCOPE = Object.freeze({ release_id: '3422100', listing_id: '1366950910', seller: 'Mailman21842',
  quantity: 1, currency: 'USD', max_total: '5.05', approved_destination_ref: 'saved_shipping_address' });
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const invalid = () => { throw new Error('invalid_guest_qualification_policy'); };
async function privateJson(path, { optional = false } = {}) {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 || info.nlink !== 1 || info.size > 8192) invalid();
    const source = await file.readFile('utf8');
    if (Buffer.byteLength(source) > 8192) invalid();
    return JSON.parse(source);
  } catch (error) { if (optional && error.code === 'ENOENT') return null; invalid(); }
  finally { await file?.close(); }
}
const capturePolicy = policy => policy?.schema === 'discogs_guest_cart_capture_policy_v1';
function validatePolicy(policy, now, { allowExpired = false } = {}) {
  if (!exact(policy, ['schema', 'job_id', 'approval_ref', 'issued_at', 'expires_at', 'max_calls', 'action', 'scope', 'destination_ack']) ||
      !['discogs_guest_qualification_policy_v1', 'discogs_guest_cart_capture_policy_v1'].includes(policy.schema) || !UUID.test(policy.job_id) || !UUID.test(policy.approval_ref) ||
      policy.action !== 'probe_guest_evidence' || policy.max_calls !== (capturePolicy(policy) ? 1 : 2) ||
      !Number.isFinite(policy.issued_at) || !Number.isFinite(policy.expires_at) || policy.issued_at < 0 ||
      policy.issued_at > now || !allowExpired && now >= policy.expires_at || policy.expires_at <= policy.issued_at ||
      policy.expires_at - policy.issued_at > 1800 || !exact(policy.scope, Object.keys(SCOPE)) ||
      Object.entries(SCOPE).some(([key, value]) => policy.scope[key] !== value)) invalid();
  const ack = policy.destination_ack;
  if (capturePolicy(policy) && ack !== null) invalid();
  if (ack !== null && (!exact(ack, ['document_ref', 'source', 'approved_destination_ref', 'observed_at']) ||
      !UUID.test(ack.document_ref) || ack.source !== 'operator_visual_saved_destination_match' ||
      ack.approved_destination_ref !== SCOPE.approved_destination_ref || !Number.isFinite(ack.observed_at) ||
      ack.observed_at < policy.issued_at || ack.observed_at > now || !allowExpired && now - ack.observed_at > 60)) invalid();
  return policy;
}
function identity(policy) {
  return { schema: policy.schema, job_id: policy.job_id, approval_ref: policy.approval_ref,
    issued_at: policy.issued_at, expires_at: policy.expires_at, max_calls: policy.max_calls,
    action: policy.action, scope: SCOPE };
}
function requestAllowed(request, policy) {
  if (!exact(request, request?.phase === 'confirm_saved_destination' ?
    ['schema', 'job_id', 'phase', 'document_ref'] : ['schema', 'job_id', 'phase']) ||
      request.schema !== 'discogs_guest_readonly_probe_v1' || request.job_id !== policy.job_id) return false;
  if (capturePolicy(policy)) return request.phase === 'capture_cart_structure';
  return request.phase === 'inspect' ? policy.destination_ack === null :
    request.phase === 'confirm_saved_destination' && policy.destination_ack !== null &&
      request.document_ref === policy.destination_ack.document_ref;
}
async function exclusiveJson(path, value) {
  let file;
  try {
    file = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await file.writeFile(JSON.stringify(value) + '\n'); await file.sync();
  } finally { await file?.close(); }
  // A persisted slot must survive a process crash/restart before browser access.
  const directory = await open(dirname(path), constants.O_RDONLY);
  try { await directory.sync(); } finally { await directory.close(); }
}
async function createIssuer(policyPath, now, fixture) {
  const directory = dirname(policyPath), info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
      (fixture ? (info.mode & 0o777) !== 0o700 : (info.mode & 0o7777) !== 0o2710 || info.gid !== 113)) invalid();
  const first = await privateJson(policyPath, { optional: true });
  if (first === null) return null;
  validatePolicy(first, now(), { allowExpired: true });
  if (now() >= first.expires_at) return null; // Expiry disables capability; it must not strand the viewer.
  const original = identity(first), identityDigest = hash(original);
  const prefix = join(directory, `guest-qualification-${first.job_id}`);
  const identityPath = `${prefix}-identity.json`;
  const existing = await privateJson(identityPath, { optional: true });
  if (existing === null) {
    try { await exclusiveJson(identityPath, { identity_sha256: identityDigest }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  if ((await privateJson(identityPath))?.identity_sha256 !== identityDigest) invalid();
  const load = async () => {
    const policy = validatePolicy(await privateJson(policyPath), now());
    if (hash(identity(policy)) !== identityDigest) invalid();
    return policy;
  };
  const slot = phase => `${prefix}-${phase === 'capture_cart_structure' ? '3-cart-capture' : phase === 'inspect' ? '1-inspect' : '2-confirm'}.json`;
  const validReceipt = receipt => exact(receipt, ['schema', 'job_id', 'identity_sha256', 'phase', 'consumed_at']) &&
    receipt.schema === 'discogs_guest_qualification_use_v1' && receipt.job_id === first.job_id &&
    receipt.identity_sha256 === identityDigest && ['inspect', 'confirm_saved_destination', 'capture_cart_structure'].includes(receipt.phase) &&
    Number.isFinite(receipt.consumed_at) && receipt.consumed_at >= first.issued_at && receipt.consumed_at < first.expires_at;
  const allowed = async request => {
    const policy = await load();
    if (!requestAllowed(request, policy) || await privateJson(slot(request.phase), { optional: true }) !== null) return null;
    if (request.phase === 'confirm_saved_destination') {
      const previous = await privateJson(slot('inspect'), { optional: true });
      if (!validReceipt(previous) || previous.phase !== 'inspect' || policy.destination_ack.observed_at < previous.consumed_at) return null;
    }
    return policy;
  };
  return Object.freeze({
    accessGuard: async (action, request, { signal } = {}) => {
      if (action !== 'probe_guest_evidence' || signal?.aborted) return false;
      try { return !!await allowed(request) && !signal?.aborted; } catch { return false; }
    },
    authorize: async (request, { signal } = {}) => {
      if (signal?.aborted) return null;
      try {
        const policy = await allowed(request);
        if (!policy || signal?.aborted) return null;
        const consumedAt = now();
        validatePolicy(policy, consumedAt);
        await exclusiveJson(slot(request.phase), { schema: 'discogs_guest_qualification_use_v1', job_id: policy.job_id,
          identity_sha256: identityDigest, phase: request.phase, consumed_at: consumedAt });
        // Recheck revocation and an operator's same-job policy update after the
        // durable write, before returning any browser authorization.
        if (!requestAllowed(request, await load()) || signal?.aborted) return null; // Spent safely; never rearm after uncertainty.
        return Object.freeze({ schema: 'discogs_guest_readonly_authorization_v1', job_id: policy.job_id,
          issued_at: policy.issued_at, expires_at: policy.expires_at, ...SCOPE,
          ...(capturePolicy(policy) ? { mode: 'capture_cart_structure' } : {}),
          ...(policy.destination_ack ? { destination_ack: Object.freeze({ ...policy.destination_ack }) } : {}) });
      } catch { return null; }
    },
  });
}
export async function loadHostedGuestQualification() {
  try { return await createIssuer(HOSTED_QUALIFICATION_POLICY, () => Date.now() / 1000, false); }
  catch (error) { if (error.code === 'ENOENT') return null; throw new Error('invalid_guest_qualification_policy'); }
}
/** Isolated local policy fixtures only; production has no path/env override. */
export async function createHostedGuestQualificationFixture({ policyPath, now = () => Date.now() / 1000 } = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !isAbsolute(policyPath || '')) throw new Error('fixture_context_required');
  return createIssuer(policyPath, now, true);
}
