/** Bounded read-only ancestry diagnostics. This is not a payment-frame profile,
 * eligibility decision, final quote, or permission to operate any control. */
const CHECKOUT = 'https://www.discogs.com/marketplace/checkout';
const CATEGORIES = Object.freeze(['merchant_checkout', 'paypal_buttons', 'paypal_smart_other', 'paypal_other', 'unknown']);
const STOPS = Object.freeze(['merchant_root', 'unknown_origin', 'missing_parent', 'outside_inventory', 'cycle', 'depth_limit',
  'association_mismatch', 'document_changed', 'inspection_error']);
const REASONS = Object.freeze(['complete', 'no_nested_candidates', 'inventory_limit', 'candidate_limit', 'inventory_changed',
  'candidate_incomplete', 'ambiguous_controls', 'inspection_error', 'invalid_inventory', 'invalid_candidates']);
const API_ERROR = Symbol('ancestry_api_error');
const count = (value, max) => Number.isInteger(value) && value >= 0 && value <= max;
const plain = (value, keys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(descriptors).length === keys.length && keys.every(key => descriptors[key]?.enumerable === true && Object.hasOwn(descriptors[key], 'value'));
};
const array = (value, max) => {
  if (!Array.isArray(value) || value.length > max) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(descriptors).length === value.length + 1 && Array.from({ length: value.length }, (_, i) => String(i))
    .every(key => descriptors[key]?.enumerable === true && Object.hasOwn(descriptors[key], 'value'));
};
const nullableBoolean = value => value === null || typeof value === 'boolean';
const empty = (reason, inventory = 0, candidates = 0) => ({ schema: 'discogs_guest_pay_ancestry_v1',
  status: ['inventory_limit', 'candidate_limit'].includes(reason) ? 'limit' : reason === 'no_nested_candidates' ? 'observed' : 'unsupported',
  reason, inventory_count: Math.min(inventory, 32), candidate_count: Math.min(candidates, 32),
  truncated: ['inventory_limit', 'candidate_limit'].includes(reason), candidates: [] });

/** Exact bounded scalar clone; rejects accessors, extra keys and free text. */
export function sanitizeGuestPayAncestryDiagnostic(value) {
  if (!plain(value, ['schema', 'status', 'reason', 'inventory_count', 'candidate_count', 'truncated', 'candidates']) ||
      value.schema !== 'discogs_guest_pay_ancestry_v1' || !['observed', 'incomplete', 'changed', 'unsupported', 'limit'].includes(value.status) ||
      !REASONS.includes(value.reason) || !count(value.inventory_count, 32) || !count(value.candidate_count, 32) ||
      value.candidate_count > value.inventory_count || typeof value.truncated !== 'boolean' || !array(value.candidates, 4)) return null;
  if (value.status === 'limit' ? !value.truncated || !['inventory_limit', 'candidate_limit'].includes(value.reason) || value.candidates.length !== 0 ||
      value.reason === 'inventory_limit' && value.inventory_count !== 32 || value.reason === 'candidate_limit' && value.candidate_count <= 4 : value.truncated) return null;
  if (value.status !== 'limit' && ['inventory_limit', 'candidate_limit'].includes(value.reason) ||
      value.reason === 'no_nested_candidates' && (value.status !== 'observed' || value.candidate_count !== 0) ||
      value.reason === 'complete' && (value.status !== 'observed' || value.candidate_count === 0) ||
      value.status === 'observed' && !['complete', 'no_nested_candidates'].includes(value.reason) ||
      value.status === 'changed' && value.reason !== 'inventory_changed' ||
      value.reason === 'inventory_changed' && value.status !== 'changed' ||
      value.status === 'incomplete' && !['candidate_incomplete', 'ambiguous_controls'].includes(value.reason) ||
      value.status === 'unsupported' && !['inspection_error', 'invalid_inventory', 'invalid_candidates'].includes(value.reason)) return null;
  if (!['limit', 'unsupported'].includes(value.status) && value.candidates.length !== value.candidate_count) return null;
  const candidates = [];
  for (const candidate of value.candidates) {
    if (!plain(candidate, ['category', 'stop', 'depth', 'chain_complete', 'buttons_ancestor_verified', 'public_submit_count', 'hops']) ||
        !CATEGORIES.includes(candidate.category) || !STOPS.includes(candidate.stop) || !count(candidate.depth, 4) ||
        !array(candidate.hops, 4) || candidate.depth !== candidate.hops.length || typeof candidate.chain_complete !== 'boolean' ||
        typeof candidate.buttons_ancestor_verified !== 'boolean' || !(candidate.public_submit_count === null || count(candidate.public_submit_count, 2))) return null;
    const hops = [];
    for (let i = 0; i < candidate.hops.length; i++) {
      const hop = candidate.hops[i];
      if (!plain(hop, ['child_category', 'parent_category', 'visible', 'content_frame_matches', 'owner_frame_matches', 'documents_same']) ||
          !CATEGORIES.includes(hop.child_category) || !CATEGORIES.includes(hop.parent_category) ||
          ['visible', 'content_frame_matches', 'owner_frame_matches', 'documents_same'].some(key => !nullableBoolean(hop[key])) ||
          hop.child_category !== (i ? candidate.hops[i - 1].parent_category : candidate.category) ||
          [hop.child_category, hop.parent_category].includes('unknown') &&
            ['visible', 'content_frame_matches', 'owner_frame_matches', 'documents_same'].some(key => hop[key] !== null)) return null;
      hops.push({ ...hop });
    }
    const complete = candidate.stop === 'merchant_root';
    if (candidate.chain_complete !== complete || complete && (!['paypal_smart_other', 'paypal_other'].includes(candidate.category) ||
        !hops.length || hops.at(-1).parent_category !== 'merchant_checkout' || hops.some(hop =>
          ['visible', 'content_frame_matches', 'owner_frame_matches', 'documents_same'].some(key => hop[key] !== true))) ||
        !complete && candidate.public_submit_count !== null || candidate.buttons_ancestor_verified &&
          (!complete || !hops.some(hop => hop.parent_category === 'paypal_buttons')) ||
        candidate.stop === 'depth_limit' && candidate.depth !== 4) return null;
    candidates.push({ ...candidate, hops });
  }
  if (['changed', 'unsupported'].includes(value.status) && candidates.some(candidate => candidate.chain_complete ||
      candidate.buttons_ancestor_verified || candidate.public_submit_count !== null)) return null;
  const ambiguous = candidates.filter(candidate => candidate.public_submit_count > 0).length > 1 || candidates.some(candidate => candidate.public_submit_count === 2);
  if (value.reason === 'complete' && (candidates.some(candidate => !candidate.chain_complete || candidate.public_submit_count === null) || ambiguous) ||
      value.reason === 'ambiguous_controls' && !ambiguous || value.reason === 'candidate_incomplete' && candidates.every(candidate => candidate.chain_complete && candidate.public_submit_count !== null)) return null;
  return { ...value, candidates };
}

function category(url, main = false) {
  if (main) return url === CHECKOUT ? 'merchant_checkout' : 'unknown';
  try {
    const parsed = new URL(url);
    if (parsed.origin !== 'https://www.paypal.com' || parsed.username || parsed.password) return 'unknown';
    return parsed.pathname === '/smart/buttons' ? 'paypal_buttons' : parsed.pathname.startsWith('/smart/') ? 'paypal_smart_other' : 'paypal_other';
  } catch { return 'unknown'; }
}

// Runs in the bound document. URL/origin are checked BEFORE any document read.
function captureKnownDocument(binding) {
  try {
    const current = new URL(location.href);
    if (current.href !== binding.url || (binding.main ? current.href !== 'https://www.discogs.com/marketplace/checkout' :
      current.origin !== 'https://www.paypal.com' || current.username || current.password)) return null;
    return document;
  } catch { return null; }
}
function sameKnownDocument(binding) {
  try {
    const current = new URL(location.href);
    if (current.href !== binding.url || (binding.main ? current.href !== 'https://www.discogs.com/marketplace/checkout' :
      current.origin !== 'https://www.paypal.com' || current.username || current.password)) return false;
    return document === binding.expectedDocument;
  } catch { return false; }
}

/** Fixed public control COUNT only. No label, inputs, arbitrary text or values. */
export function collectGuestPayAncestryControlDom(binding) {
  try {
    const current = new URL(location.href);
    if (!binding || current.href !== binding.url || current.origin !== 'https://www.paypal.com' || current.username || current.password ||
        current.pathname === '/smart/buttons' || document !== binding.expectedDocument || document.readyState !== 'complete') return null;
    return Math.min(document.querySelectorAll('button#submit-button[type="button"]').length, 2);
  } catch { return null; }
}

/** Uses the caller's existing bounded/retained read functions and handle cleanup.
 * No browser/controller is created and no effect-bearing API is available. */
export async function startGuestPayAncestryDiagnostic(page, { frames, main, mainDocument, nestedFrames,
  buttonsAssociation, read, readHandle } = {}) {
  if (!Array.isArray(frames) || !Array.isArray(nestedFrames) || typeof read !== 'function' || typeof readHandle !== 'function')
    return async () => empty('invalid_inventory');
  const inventory = [...frames], nested = [...nestedFrames];
  if (inventory.length > 32) return async () => empty('inventory_limit', 32, Math.min(nested.length, 32));
  if (!inventory.includes(main) || new Set(inventory).size !== inventory.length || !page || !mainDocument)
    return async () => empty('invalid_inventory', inventory.length);
  if (nested.length > inventory.length || new Set(nested).size !== nested.length || nested.some(frame => frame === main || !inventory.includes(frame)))
    return async () => empty('invalid_candidates', inventory.length, Math.min(nested.length, inventory.length));
  if (nested.length > 4) return async () => empty('candidate_limit', inventory.length, nested.length);
  const call = async (operation, handle = false) => (handle ? readHandle : read)(async () => {
    try { return await operation(); }
    catch { throw Object.assign(new Error('ancestry_api_unavailable'), { [API_ERROR]: true }); }
  });
  const apiError = error => error && Object.getOwnPropertyDescriptor(error, API_ERROR)?.value === true;
  const snapshots = new Map(), documents = new Map(), edges = [], results = [];
  const verifiedButtons = buttonsAssociation?.value === true ? buttonsAssociation.frame : null;
  let initialError = false;
  try {
    for (const frame of inventory) {
      const url = await call(() => frame === main ? page.url() : frame.url());
      const parent = frame === main ? null : await call(() => frame.parentFrame());
      snapshots.set(frame, { url, parent, category: category(url, frame === main) });
    }
  } catch (error) { if (!apiError(error)) throw error; initialError = true; }
  if (initialError) return async () => empty('inspection_error', inventory.length, nested.length);
  if (nested.some(frame => snapshots.get(frame).parent === main)) return async () => empty('invalid_candidates', inventory.length, nested.length);
  const target = frame => frame === main ? page : frame;
  const docSame = async frame => {
    const snapshot = snapshots.get(frame), handle = documents.get(frame);
    return !!snapshot && !!handle && await call(() => target(frame).url()) === snapshot.url &&
      await call(() => target(frame).evaluate(sameKnownDocument, { url: snapshot.url, main: frame === main, expectedDocument: handle })) === true;
  };
  const documentFor = async frame => {
    const snapshot = snapshots.get(frame);
    if (!snapshot || snapshot.category === 'unknown' || await call(() => target(frame).url()) !== snapshot.url) return false;
    if (!documents.has(frame)) documents.set(frame, frame === main ? mainDocument :
      await call(() => target(frame).evaluateHandle(captureKnownDocument, { url: snapshot.url, main: false }), true));
    return docSame(frame);
  };
  for (const frame of nested) {
    const result = { category: snapshots.get(frame).category, stop: 'inspection_error', depth: 0, chain_complete: false,
      buttons_ancestor_verified: false, public_submit_count: null, hops: [] };
    const seen = new Set([frame]); let child = frame;
    try {
      if (result.category === 'unknown' || result.category === 'paypal_buttons') { result.stop = 'unknown_origin'; results.push(result); continue; }
      while (true) {
        if (result.hops.length === 4) { result.stop = 'depth_limit'; break; }
        const childSnapshot = snapshots.get(child), parent = childSnapshot.parent;
        if (!parent) { result.stop = 'missing_parent'; break; }
        if (!snapshots.has(parent)) { result.stop = 'outside_inventory'; break; }
        const parentSnapshot = snapshots.get(parent);
        const hop = { child_category: childSnapshot.category, parent_category: parentSnapshot.category,
          visible: null, content_frame_matches: null, owner_frame_matches: null, documents_same: null };
        result.hops.push(hop); result.depth = result.hops.length;
        if (seen.has(parent)) { result.stop = 'cycle'; break; }
        if (parentSnapshot.category === 'unknown') { result.stop = 'unknown_origin'; break; }
        if (!await documentFor(child) || !await documentFor(parent)) { hop.documents_same = false; result.stop = 'document_changed'; break; }
        const element = await call(() => child.frameElement(), true);
        const edge = { child, parent, element }; edges.push(edge);
        edge.visible = hop.visible = await call(() => element.isVisible());
        edge.content = await call(() => element.contentFrame()); hop.content_frame_matches = edge.content === child;
        edge.owner = await call(() => element.ownerFrame()); hop.owner_frame_matches = edge.owner === parent;
        hop.documents_same = await docSame(child) && await docSame(parent);
        if (hop.visible !== true || !hop.content_frame_matches || !hop.owner_frame_matches) { result.stop = 'association_mismatch'; break; }
        if (!hop.documents_same || await call(() => child.parentFrame()) !== parent) { result.stop = 'document_changed'; break; }
        if (parent === main) { result.stop = 'merchant_root'; result.chain_complete = true; break; }
        seen.add(parent); child = parent;
      }
      if (result.chain_complete && await docSame(frame)) {
        result.public_submit_count = await call(() => frame.evaluate(collectGuestPayAncestryControlDom,
          { url: snapshots.get(frame).url, expectedDocument: documents.get(frame) }));
        if (!count(result.public_submit_count, 2)) { result.public_submit_count = null; result.stop = 'inspection_error'; result.chain_complete = false; }
      }
    } catch (error) { if (!apiError(error)) throw error; result.stop = 'inspection_error'; result.chain_complete = false; result.public_submit_count = null; }
    results.push(result);
  }
  return async () => {
    let changed = false, failed = false;
    try {
      const current = await call(() => page.frames());
      if (!Array.isArray(current) || current.length !== inventory.length || current.some((frame, index) => frame !== inventory[index])) changed = true;
      for (const [frame, snapshot] of snapshots) {
        if (await call(() => target(frame).url()) !== snapshot.url || (frame === main ? null : await call(() => frame.parentFrame())) !== snapshot.parent) changed = true;
      }
      for (const frame of documents.keys()) if (!await docSame(frame)) changed = true;
      for (const edge of edges) if (await call(() => edge.child.parentFrame()) !== edge.parent ||
          await call(() => edge.element.isVisible()) !== edge.visible || await call(() => edge.element.contentFrame()) !== edge.content ||
          await call(() => edge.element.ownerFrame()) !== edge.owner) changed = true;
      // Document proofs are repeated after the awaited edge audits. A later
      // document proof can itself await while an earlier edge changes, so the
      // final observation must also repeat parent/visibility/ownership proofs.
      for (const frame of documents.keys()) if (!await docSame(frame)) changed = true;
      for (const edge of edges) if (await call(() => edge.child.parentFrame()) !== edge.parent ||
          await call(() => edge.element.isVisible()) !== edge.visible || await call(() => edge.element.contentFrame()) !== edge.content ||
          await call(() => edge.element.ownerFrame()) !== edge.owner) changed = true;
    } catch (error) { if (!apiError(error)) throw error; failed = true; }
    if (changed || failed) for (const result of results) {
      result.stop = changed ? 'document_changed' : 'inspection_error'; result.chain_complete = false;
      result.public_submit_count = null; result.buttons_ancestor_verified = false;
    }
    else for (const result of results) result.buttons_ancestor_verified = result.chain_complete && verifiedButtons !== null &&
      buttonsAssociation?.value === true && buttonsAssociation.frame === verifiedButtons && result.hops.some((_, index) => {
        let frame = nested[results.indexOf(result)];
        for (let i = 0; i <= index; i++) frame = snapshots.get(frame)?.parent;
        return frame === verifiedButtons;
      });
    const ambiguous = results.filter(result => result.public_submit_count > 0).length > 1 || results.some(result => result.public_submit_count === 2);
    const incomplete = results.some(result => !result.chain_complete || result.public_submit_count === null);
    const report = { schema: 'discogs_guest_pay_ancestry_v1', status: changed ? 'changed' : failed ? 'unsupported' : incomplete || ambiguous ? 'incomplete' : 'observed',
      reason: changed ? 'inventory_changed' : failed ? 'inspection_error' : incomplete ? 'candidate_incomplete' : ambiguous ? 'ambiguous_controls' : nested.length ? 'complete' : 'no_nested_candidates',
      inventory_count: inventory.length, candidate_count: nested.length, truncated: false, candidates: results };
    return sanitizeGuestPayAncestryDiagnostic(report) || empty('inspection_error', inventory.length, nested.length);
  };
}
