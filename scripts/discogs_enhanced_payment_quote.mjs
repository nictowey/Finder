/** Fixed read-only payment-evidence producer for the already-open enhanced
 * checkout. Merchant summary and narrowly public guest Pay-control metadata may
 * be observed. Final quote evidence also requires fresh trusted post-entry
 * amount/delivery/terms confirmation. Never reads payment
 * inputs, addresses, account data or values.
 */
import { randomUUID } from 'node:crypto';
import { collectEnhancedCheckoutDom } from './discogs_enhanced_checkout.mjs';
import { startGuestPayAncestryDiagnostic, sanitizeGuestPayAncestryDiagnostic } from './discogs_guest_pay_ancestry.mjs';

export { collectEnhancedCheckoutDom as collectEnhancedPaymentQuoteDom };
export const ENHANCED_PAYMENT_QUOTE_URL = 'https://www.discogs.com/marketplace/checkout';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const STOP = Symbol('payment_quote_stop');
// This layout was observed through the exact retained two-edge relationship.
// Its public control alone never establishes finality or the delivery address.
const OBSERVED_GUEST_LAYOUT = 'paypal_smart_nested_guest_v1';
const observedProviders = new WeakMap();
const actualGuestBindings = new WeakMap();
const verifiedFinalReports = new WeakMap();
const pending = new WeakMap();
const active = new WeakSet();
const finished = new WeakSet();
const fail = code => { throw Object.assign(new Error(code), { [STOP]: code }); };
const base = code => ({ schema: 'discogs_enhanced_payment_quote_v1', status: 'unverified', code,
  payment_allowed: false, purchase_allowed: false, final_submission_allowed: false,
  final_quote_verified: false, destination_verified: false, provider_terms_verified: false });
const record = (value, keys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const fields = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(fields).length === keys.length && keys.every(key =>
    fields[key]?.enumerable === true && Object.hasOwn(fields[key], 'value'));
};
const number = value => typeof value === 'number' && Number.isFinite(value);
const money = value => record(value, ['amount', 'currency']) && value.currency === 'USD' &&
  typeof value.amount === 'string' && MONEY.test(value.amount);
const cents = value => { const [whole, fraction] = value.split('.'); return BigInt(whole) * 100n + BigInt(fraction); };
const PAY_METADATA_COUNTS = ['paypal_origin_count', 'candidate_count', 'associated_count', 'eligible_count', 'unsupported_count'];
const PAY_METADATA_NULLS = ['submit_count', 'submit_visible', 'submit_enabled', 'button_type_matches', 'label', 'amount', 'currency', 'location'];
const SELECTION_COUNTS = ['total', 'visited', 'origin', 'buttons_excluded', 'non_direct', 'hidden',
  'association_rejected', 'eligible_frames', 'inspected', 'error'];
const SELECTION_REASONS = ['total_frame_limit', 'eligible_frame_limit', 'frame_changed', 'frame_inspection_error',
  'control_metadata_unsupported', 'complete'];
const selectionDiagnostic = (reason, total = 0) => ({ schema: 'discogs_guest_pay_selection_v1', reason,
  ...Object.fromEntries(SELECTION_COUNTS.map(key => [key, key === 'total' ? Math.min(total, 32) : 0])) });
const emptyPayMetadata = status => ({ schema: 'discogs_guest_pay_metadata_v1', status,
  ...Object.fromEntries(PAY_METADATA_COUNTS.map(key => [key, 0])), truncated: false,
  ...Object.fromEntries(PAY_METADATA_NULLS.map(key => [key, null])), same_document_verified: false, frame_associated: false });

/** Public metadata only. This does not qualify a quote, destination or Pay action.
 * no_eligible_frame concerns the merchant/associated PayPal candidates only;
 * unknown-origin documents are never inspected or evidence of global absence. */
export function sanitizeGuestPayMetadata(value) {
  if (value === undefined || value === null) return emptyPayMetadata('unsupported_collection');
  const hasSelection = typeof value === 'object' && Object.hasOwn(value, 'selection_diagnostic');
  const hasAncestry = typeof value === 'object' && Object.hasOwn(value, 'ancestry_diagnostic');
  if (!record(value, ['schema', 'status', ...PAY_METADATA_COUNTS, 'truncated', ...PAY_METADATA_NULLS,
    'same_document_verified', 'frame_associated', ...(hasSelection ? ['selection_diagnostic'] : []),
    ...(hasAncestry ? ['ancestry_diagnostic'] : [])]) || value.schema !== 'discogs_guest_pay_metadata_v1' ||
      !['observed', 'no_eligible_frame', 'unsupported_collection', 'ambiguous'].includes(value.status) ||
      PAY_METADATA_COUNTS.some(key => ![0, 1, 2].includes(value[key])) || typeof value.truncated !== 'boolean' ||
      typeof value.same_document_verified !== 'boolean' || typeof value.frame_associated !== 'boolean') return null;
  if (value.eligible_count > value.associated_count || value.associated_count > value.candidate_count ||
      value.unsupported_count > value.associated_count) return null;
  if (value.status !== 'observed') {
    if (PAY_METADATA_NULLS.some(key => value[key] !== null) || value.same_document_verified || value.frame_associated ||
        value.status === 'no_eligible_frame' && (value.eligible_count !== 0 || value.unsupported_count !== 0 || value.truncated || value.associated_count < 1) ||
        value.status === 'ambiguous' && (value.eligible_count !== 2 || value.unsupported_count !== 0 || value.truncated)) return null;
  } else {
    const label = typeof value.label === 'string' ? /^Pay \$((?:0|[1-9][0-9]{0,6})\.[0-9]{2})( USD)?$/u.exec(value.label) : null;
    if (value.truncated || value.eligible_count !== 1 || value.unsupported_count !== 0 || value.associated_count < 1 ||
        value.submit_count !== 1 || value.submit_visible !== true || typeof value.submit_enabled !== 'boolean' ||
        value.button_type_matches !== true || !label || value.amount !== label[1] || value.currency !== (label[2] ? 'USD' : null) ||
        !['merchant_main', 'paypal_child'].includes(value.location) || !value.same_document_verified || !value.frame_associated) return null;
  }
  if (hasSelection) {
    const diagnostic = value.selection_diagnostic;
    if (!record(diagnostic, ['schema', 'reason', ...SELECTION_COUNTS]) || diagnostic.schema !== 'discogs_guest_pay_selection_v1' ||
        !SELECTION_REASONS.includes(diagnostic.reason) || SELECTION_COUNTS.some(key => !Number.isInteger(diagnostic[key]) || diagnostic[key] < 0 || diagnostic[key] > 32) ||
        diagnostic.visited > diagnostic.total || diagnostic.origin > diagnostic.visited ||
        ['buttons_excluded', 'non_direct', 'hidden', 'association_rejected', 'eligible_frames'].reduce((sum, key) => sum + diagnostic[key], 0) > diagnostic.origin ||
        diagnostic.inspected > diagnostic.eligible_frames + 1 ||
        diagnostic.reason !== 'complete' && value.status !== 'unsupported_collection' ||
        ['total_frame_limit', 'eligible_frame_limit'].includes(diagnostic.reason) && !value.truncated ||
        diagnostic.reason === 'complete' && (diagnostic.error !== 0 || value.truncated || diagnostic.visited !== diagnostic.total ||
          ['buttons_excluded', 'non_direct', 'hidden', 'association_rejected', 'eligible_frames'].reduce((sum, key) => sum + diagnostic[key], 0) !== diagnostic.origin) ||
        value.status === 'observed' && value.location === 'paypal_child' && diagnostic.eligible_frames < 1 ||
        diagnostic.reason === 'total_frame_limit' && (diagnostic.total !== 32 || SELECTION_COUNTS.some(key => key !== 'total' && diagnostic[key] !== 0)) ||
        diagnostic.reason === 'eligible_frame_limit' && (diagnostic.eligible_frames <= 2 || diagnostic.inspected !== 0) ||
        diagnostic.reason === 'frame_inspection_error' && diagnostic.error === 0) return null;
    const ancestry = hasAncestry ? sanitizeGuestPayAncestryDiagnostic(value.ancestry_diagnostic) : null;
    if (hasAncestry && !ancestry) return null;
    return { ...value, selection_diagnostic: { ...diagnostic }, ...(hasAncestry ? { ancestry_diagnostic: ancestry } : {}) };
  }
  if (hasAncestry) {
    const ancestry = sanitizeGuestPayAncestryDiagnostic(value.ancestry_diagnostic);
    if (!ancestry) return null;
    return { ...value, ancestry_diagnostic: ancestry };
  }
  return { ...value };
}

/** Self-contained fixed public control inspection. Never reads any input,
 * arbitrary attributes, page text, hidden content or application state. */
export function collectGuestPayControlMetadataDom(binding) {
  const unsupported = count => ({ schema: 'discogs_guest_pay_control_dom_v1', status: 'unsupported_collection',
    submit_count: count, submit_visible: null, submit_enabled: null, button_type_matches: null, label: null, amount: null, currency: null });
  try {
    if (typeof document === 'undefined' || typeof location === 'undefined' ||
        !binding || binding.expectedDocument !== document ||
        !['merchant_main', 'paypal_child'].includes(binding.role)) return unsupported(null);
    const current = new URL(location.href);
    if (binding.role === 'merchant_main' ? current.href !== 'https://www.discogs.com/marketplace/checkout' :
      current.origin !== 'https://www.paypal.com' || current.pathname === '/smart/buttons' || current.username || current.password) return unsupported(null);
    if (binding.nested === true && (binding.role !== 'paypal_child' || current.href !== binding.expectedUrl ||
        !current.pathname.startsWith('/smart/') || current.pathname === '/smart/buttons')) return unsupported(null);
    if (document.readyState !== 'complete') return unsupported(null);
    const matches = document.querySelectorAll('#submit-button');
    if (matches.length === 0) return { ...unsupported(0), status: 'absent' };
    if (matches.length !== 1) return unsupported(2);
    const button = matches[0];
    if (button.tagName !== 'BUTTON' || button.getAttribute('type') !== 'button') return unsupported(1);
    const visible = node => {
      if (!node?.isConnected) return false;
      for (let current = node; current; current = current.parentElement) {
        if (current.hidden || current.getAttribute('aria-hidden') === 'true') return false;
        const style = document.defaultView.getComputedStyle(current);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            style.contentVisibility === 'hidden' || style.opacity === '0') return false;
      }
      return [...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
    };
    if (!visible(button)) return unsupported(1);
    let visited = 0, length = 0;
    const text = (node, depth) => {
      if (++visited > 12 || depth > 3) throw new Error('unsupported');
      if (node.nodeType === 3) {
        length += (node.nodeValue || '').length;
        if (length > 40) throw new Error('unsupported');
        return node.nodeValue || '';
      }
      if (node.nodeType !== 1 || node !== button && node.tagName !== 'SPAN' || !visible(node)) throw new Error('unsupported');
      return [...node.childNodes].map(child => text(child, depth + 1)).join('');
    };
    const label = text(button, 0).replace(/\s+/gu, ' ').trim();
    const parsed = /^Pay \$((?:0|[1-9][0-9]{0,6})\.[0-9]{2})( USD)?$/u.exec(label);
    if (!parsed) return unsupported(1);
    return { schema: 'discogs_guest_pay_control_dom_v1', status: 'observed', submit_count: 1,
      submit_visible: true, submit_enabled: !button.disabled && !button.matches(':disabled') && button.getAttribute('aria-disabled') !== 'true',
      button_type_matches: true, label, amount: parsed[1], currency: parsed[2] ? 'USD' : null };
  } catch { return unsupported(null); }
}

// The observed nested anatomy is exactly one smart guest under the actual
// verified buttons frame, then the merchant. This captures no input or state.
function captureNestedGuestDocument(binding) {
  try {
    const current = new URL(location.href);
    if (current.href !== binding.expectedUrl || current.origin !== 'https://www.paypal.com' || current.username || current.password ||
        (binding.buttons ? current.pathname !== '/smart/buttons' : binding.guest &&
          (!current.pathname.startsWith('/smart/') || current.pathname === '/smart/buttons'))) return null;
    return document;
  } catch { return null; }
}

async function inspectGuestPayMetadata(page, frames, mainDocument, { read, readHandle, buttonsAssociation }) {
  const result = emptyPayMetadata('unsupported_collection');
  const diagnostic = selectionDiagnostic(frames.length > 32 ? 'total_frame_limit' : 'complete', frames.length);
  result.selection_diagnostic = diagnostic;
  if (frames.length > 32) { result.truncated = true; return async () => result; }
  const count = value => Math.min(value, 2);
  const main = await read(() => page.mainFrame()), inventory = [], children = [];
  let changed = false;
  const errors = new Set();
  // Classify the complete bounded inventory BEFORE spending the two-child cap.
  // Excluded frames retain private snapshots so a late new candidate cannot be
  // ignored. No unknown-origin or excluded document is queried for controls.
  for (const frame of frames) {
    const entry = { target: frame, url: null, kind: 'other_origin' };
    inventory.push(entry); diagnostic.visited++;
    try {
      entry.url = await read(() => frame === main ? page.url() : frame.url());
      let parsed; try { parsed = new URL(entry.url); } catch { continue; }
      if (frame === main) { entry.kind = 'merchant_main'; continue; }
      if (parsed.origin !== 'https://www.paypal.com' || parsed.username || parsed.password) continue;
      diagnostic.origin++;
      if (parsed.pathname === '/smart/buttons') {
        entry.kind = 'buttons'; diagnostic.buttons_excluded++;
        if (buttonsAssociation.value && frame === buttonsAssociation.frame) {
          entry.parent = await read(() => frame.parentFrame()); entry.element = buttonsAssociation.element;
          entry.visible = await read(() => entry.element.isVisible()); entry.content = await read(() => entry.element.contentFrame());
          entry.owner = await read(() => entry.element.ownerFrame());
          if (entry.parent !== main || entry.visible !== true || entry.content !== frame || entry.owner !== main) {
            changed = true; buttonsAssociation.value = false;
          }
        }
        continue;
      }
      entry.parent = await read(() => frame.parentFrame());
      if (entry.parent !== main) { entry.kind = 'non_direct'; diagnostic.non_direct++; continue; }
      entry.element = await readHandle(() => frame.frameElement());
      entry.visible = await read(() => entry.element.isVisible());
      entry.content = await read(() => entry.element.contentFrame());
      entry.owner = await read(() => entry.element.ownerFrame());
      if (typeof entry.visible !== 'boolean') throw new Error('unsupported_frame_metadata');
      if (!entry.visible) { entry.kind = 'hidden'; diagnostic.hidden++; continue; }
      if (entry.content !== frame || entry.owner !== main) { entry.kind = 'association_rejected'; diagnostic.association_rejected++; continue; }
      entry.kind = 'eligible'; diagnostic.eligible_frames++; children.push(entry);
    } catch (error) {
      if (error?.[STOP]) throw error; entry.kind = 'error'; errors.add(entry);
      if (entry.target === buttonsAssociation.frame) buttonsAssociation.value = false;
    }
  }
  result.paypal_origin_count = count(diagnostic.origin);
  // Only the now-observed exact two-edge smart guest -> actual buttons ->
  // merchant relationship can join public-control metadata collection. This
  // never qualifies a provider final quote, destination, payment or dispatch.
  const nestedFrames = inventory.filter(entry => entry.kind === 'non_direct').map(entry => entry.target);
  const finalizeAncestry = nestedFrames.length ? await startGuestPayAncestryDiagnostic(page, {
    frames, main, mainDocument, nestedFrames, buttonsAssociation, read, readHandle,
  }) : null;
  const nestedEntries = inventory.filter(entry => entry.kind === 'non_direct');
  const nestedIndex = report => {
    if (!report || !['observed', 'incomplete'].includes(report.status) || !['complete', 'candidate_incomplete'].includes(report.reason) ||
        report.truncated || report.candidate_count !== nestedEntries.length || report.candidates.length !== nestedEntries.length ||
        buttonsAssociation.value !== true || !buttonsAssociation.frame) return -1;
    const positive = [];
    for (let i = 0; i < report.candidates.length; i++) {
      const item = report.candidates[i], first = item.hops[0];
      if (nestedEntries[i].parent === buttonsAssociation.frame && item.public_submit_count === 1 && item.category === 'paypal_smart_other' && item.stop === 'merchant_root' &&
          item.depth === 2 && item.chain_complete && item.buttons_ancestor_verified && item.hops.length === 2 &&
          first.child_category === 'paypal_smart_other' && first.parent_category === 'paypal_buttons' &&
          item.hops[1].child_category === 'paypal_buttons' && item.hops[1].parent_category === 'merchant_checkout' &&
          item.hops.every(hop => ['visible', 'content_frame_matches', 'owner_frame_matches', 'documents_same'].every(key => hop[key] === true))) positive.push(i);
      // The only incomplete peers in the actual anatomy are hidden, correctly
      // owned children. Unknown, deeper or visibly unassociated peers cannot be
      // ignored to assert a unique public control.
      else if (!(item.stop === 'association_mismatch' && item.depth === 1 && !item.chain_complete &&
          item.public_submit_count === null && !item.buttons_ancestor_verified && ['paypal_smart_other', 'paypal_other', 'paypal_buttons'].includes(first?.parent_category) &&
          first.visible === false && first.content_frame_matches === true && first.owner_frame_matches === true && first.documents_same === true)) return -1;
    }
    return positive.length === 1 ? positive[0] : -1;
  };
  let nestedSelected = null, nestedButton = null;
  if (finalizeAncestry) {
    const initialAncestry = await finalizeAncestry(), index = nestedIndex(initialAncestry);
    result.ancestry_diagnostic = initialAncestry;
    if (index !== -1) {
      try {
        nestedButton = inventory.find(entry => entry.target === buttonsAssociation.frame && entry.kind === 'buttons');
        if (!nestedButton || !nestedButton.element) throw new Error('nested_buttons_unavailable');
        nestedButton.document = await readHandle(() => nestedButton.target.evaluateHandle(captureNestedGuestDocument,
          { expectedUrl: nestedButton.url, buttons: true }));
        for (let i = 0; i < nestedEntries.length; i++) {
          const entry = nestedEntries[i];
          entry.element = await readHandle(() => entry.target.frameElement());
          entry.visible = await read(() => entry.element.isVisible()); entry.content = await read(() => entry.element.contentFrame());
          entry.owner = await read(() => entry.element.ownerFrame());
          if (!entry.document) entry.document = await readHandle(() => entry.target.evaluateHandle(captureNestedGuestDocument,
            { expectedUrl: entry.url, buttons: false, guest: i === index }));
          const parentEntry = inventory.find(value => value.target === entry.parent);
          if (!parentEntry || parentEntry.kind === 'other_origin' || parentEntry.kind === 'merchant_main') throw new Error('nested_parent_unverified');
          if (!parentEntry.document) parentEntry.document = await readHandle(() => parentEntry.target.evaluateHandle(captureNestedGuestDocument,
            { expectedUrl: parentEntry.url, buttons: parentEntry.kind === 'buttons', guest: false }));
          entry.kind = i === index ? 'nested_eligible' : 'nested_hidden'; entry.nested = true; entry.nestedParent = parentEntry;
          if (entry.visible !== (i === index) || entry.content !== entry.target || entry.owner !== entry.parent) throw new Error('nested_edge_unverified');
        }
        nestedSelected = nestedEntries[index]; children.push(nestedSelected);
        diagnostic.non_direct--; diagnostic.eligible_frames++;
      } catch (error) { if (error?.[STOP]) throw error; errors.add(nestedButton || page); }
    }
  }
  result.candidate_count = result.associated_count = count(1 + children.length);
  result.truncated = children.length > 2;
  const eligibleControls = []; let selectedPayEntry = null;
  let unsupported = 0;
  const stableLeafEntry = async entry => {
    if (await read(() => entry.target === main ? page.url() : entry.target.url()) !== entry.url) return false;
    if (['other_origin', 'merchant_main'].includes(entry.kind) || entry.kind === 'buttons' && !entry.element) return true;
    if (entry.kind === 'error') return false;
    if (await read(() => entry.target.parentFrame()) !== entry.parent) return false;
    // A non-direct frame cannot become eligible without changing its parent.
    if (entry.kind === 'non_direct') return true;
    if (await read(() => entry.element.isVisible()) !== entry.visible ||
        await read(() => entry.element.contentFrame()) !== entry.content ||
        await read(() => entry.element.ownerFrame()) !== entry.owner) return false;
    return !entry.document || await read(() => entry.target.evaluate(expected => document === expected, entry.document)) === true;
  };
  const stableEntry = async entry => {
    // Prove ancestors first. No awaited ancestor proof may follow the leaf's
    // final URL/parent/element/Document checks and hide a late leaf mutation.
    if (entry.nested) {
      if (!entry.nestedParent || entry.parent !== entry.nestedParent.target) return false;
      if (entry.kind === 'nested_eligible') {
        if (buttonsAssociation.value !== true || entry.nestedParent.target !== buttonsAssociation.frame ||
            await read(() => page.url()) !== ENHANCED_PAYMENT_QUOTE_URL ||
            await read(() => page.evaluate(expected => document === expected, mainDocument)) !== true ||
            !await stableEntry(entry.nestedParent)) return false;
      } else if (await read(() => entry.nestedParent.target.url()) !== entry.nestedParent.url ||
          await read(() => entry.nestedParent.target.evaluate(expected => document === expected, entry.nestedParent.document)) !== true) return false;
    }
    return stableLeafEntry(entry);
  };
  const candidates = [{ target: page, location: 'merchant_main', document: mainDocument },
    ...(result.truncated || errors.size ? [] : children.map(entry => ({ ...entry, location: 'paypal_child', entry })))];
  // Too many actually eligible children is a limit failure, never permission to
  // inspect an arbitrary first two; merchant-main metadata has its own slot.
  if (!result.truncated && !errors.size) for (const candidate of candidates) {
    const { target, location, entry } = candidate;
    try {
      if (entry) {
        if (!entry.document) entry.document = await readHandle(() => target.evaluateHandle(() => document));
        candidate.document = entry.document;
        if (!await stableEntry(entry)) { changed = true; continue; }
      }
      const observed = await read(() => {
        diagnostic.inspected++;
        return target.evaluate(collectGuestPayControlMetadataDom, { role: location, expectedDocument: candidate.document,
          ...(entry?.nested ? { nested: true, expectedUrl: entry.url } : {}) });
      });
      if (entry ? !await stableEntry(entry) : await read(() => target.evaluate(expected => document === expected, candidate.document)) !== true) {
        changed = true; continue;
      }
      if (!record(observed, ['schema', 'status', 'submit_count', 'submit_visible', 'submit_enabled', 'button_type_matches', 'label', 'amount', 'currency']) ||
          observed.schema !== 'discogs_guest_pay_control_dom_v1') { unsupported++; continue; }
      candidate.publicObservation = observed;
      if (observed.status === 'absent' && observed.submit_count === 0) continue;
      const proposed = { ...emptyPayMetadata('observed'), candidate_count: result.candidate_count,
        associated_count: 1, eligible_count: 1, submit_count: observed.submit_count, submit_visible: observed.submit_visible,
        submit_enabled: observed.submit_enabled, button_type_matches: observed.button_type_matches, label: observed.label,
        amount: observed.amount, currency: observed.currency, location, same_document_verified: true, frame_associated: true };
      if (observed.status !== 'observed' || !sanitizeGuestPayMetadata(proposed)) { unsupported++; continue; }
      eligibleControls.push(proposed);
      selectedPayEntry = eligibleControls.length === 1 ? entry : null;
    } catch (error) { if (error?.[STOP]) throw error; errors.add(entry || page); unsupported++; }
  }
  // Called AFTER the destination matcher and merchant checks. Every original
  // frame is revalidated, including excluded hidden/non-direct/other origins.
  return async () => {
    // These additional diagnostic awaits must precede the original full audit,
    // especially the selected direct child's final identity/ownership check.
    if (finalizeAncestry) {
      result.ancestry_diagnostic = await finalizeAncestry();
      if (nestedSelected && nestedIndex(result.ancestry_diagnostic) !== nestedEntries.indexOf(nestedSelected)) changed = true;
    }
    if (nestedSelected && !result.truncated && !errors.size) {
      try {
        for (const candidate of candidates) {
          const entry = candidate.entry;
          if (entry && !await stableEntry(entry)) { changed = true; break; }
          const current = await read(() => candidate.target.evaluate(collectGuestPayControlMetadataDom,
            { role: candidate.location, expectedDocument: candidate.document,
              ...(entry?.nested ? { nested: true, expectedUrl: entry.url } : {}) }));
          const keys = ['schema', 'status', 'submit_count', 'submit_visible', 'submit_enabled', 'button_type_matches', 'label', 'amount', 'currency'];
          if (!record(current, keys) || !candidate.publicObservation || keys.some(key => current[key] !== candidate.publicObservation[key])) changed = true;
        }
      } catch (error) { if (error?.[STOP]) throw error; errors.add(nestedSelected); }
    }
    try {
      const afterFrames = await read(() => page.frames());
      if (!Array.isArray(afterFrames) || afterFrames.length !== frames.length || afterFrames.some((frame, index) => frame !== frames[index])) changed = true;
    } catch (error) { if (error?.[STOP]) throw error; errors.add(page); }
    for (const entry of inventory) {
      if (entry.kind === 'error') continue;
      try { if (!await stableEntry(entry)) { changed = true; if (entry.target === buttonsAssociation.frame) buttonsAssociation.value = false; } }
      catch (error) { if (error?.[STOP]) throw error; errors.add(entry); if (entry.target === buttonsAssociation.frame) buttonsAssociation.value = false; }
    }
    // A selected child may have changed while later excluded frames were
    // audited. Recheck that private binding last; never rescan for a new child.
    if (selectedPayEntry) {
      try { if (!await stableEntry(selectedPayEntry)) changed = true; }
      catch (error) { if (error?.[STOP]) throw error; errors.add(selectedPayEntry); }
    }
    // The fixed public collector is LAST after all ancestor and leaf awaits.
    // Its single DOM call checks the captured Document/origin and public facts;
    // no later browser proof may hide a same-document Pay-amount change.
    if (nestedSelected && selectedPayEntry === nestedSelected && !changed && !errors.size) {
      try {
        if (!await stableLeafEntry(nestedSelected)) changed = true;
        else {
          const candidate = candidates.find(value => value.entry === nestedSelected);
          const current = await read(() => nestedSelected.target.evaluate(collectGuestPayControlMetadataDom,
            { role: 'paypal_child', expectedDocument: nestedSelected.document, nested: true, expectedUrl: nestedSelected.url }));
          const keys = ['schema', 'status', 'submit_count', 'submit_visible', 'submit_enabled', 'button_type_matches', 'label', 'amount', 'currency'];
          if (!record(current, keys) || !candidate?.publicObservation || keys.some(key => current[key] !== candidate.publicObservation[key])) changed = true;
        }
      } catch (error) { if (error?.[STOP]) throw error; errors.add(nestedSelected); }
    }
    if (nestedSelected && changed && result.ancestry_diagnostic?.candidates.length) result.ancestry_diagnostic = {
      ...result.ancestry_diagnostic, status: 'changed', reason: 'inventory_changed', candidates: result.ancestry_diagnostic.candidates.map(candidate => ({
        ...candidate, stop: 'document_changed', chain_complete: false, buttons_ancestor_verified: false, public_submit_count: null })) };
    diagnostic.error = Math.min(errors.size, 32);
    result.eligible_count = count(eligibleControls.length);
    result.unsupported_count = Math.min(count(unsupported), result.associated_count);
    diagnostic.reason = errors.size ? 'frame_inspection_error' : changed ? 'frame_changed' : result.truncated ? 'eligible_frame_limit' :
      unsupported ? 'control_metadata_unsupported' : 'complete';
    if (diagnostic.reason !== 'complete') return result;
    if (eligibleControls.length > 1) return { ...result, status: 'ambiguous' };
    if (eligibleControls.length === 0) return { ...result, status: 'no_eligible_frame' };
    const metadata = { ...eligibleControls[0], paypal_origin_count: result.paypal_origin_count, associated_count: result.associated_count,
      selection_diagnostic: diagnostic, ...(result.ancestry_diagnostic ? { ancestry_diagnostic: result.ancestry_diagnostic } : {}) };
    if (nestedSelected && selectedPayEntry === nestedSelected) actualGuestBindings.set(metadata, {
      guestFrame: nestedSelected.target, guestDocument: nestedSelected.document,
      buttonsFrame: nestedButton.target, buttonsDocument: nestedButton.document, merchantDocument: mainDocument });
    return metadata;
  };
}
function merchantEvidence(value) {
  if (!record(value, ['schema', 'status', 'source', 'purchase_allowed', 'seller_handle', 'visible_row_count', 'displayed', 'selected_shipping_label']) ||
      value.schema !== 'discogs_enhanced_checkout_dom_v1' || value.status !== 'observed' ||
      value.source !== 'visible_enhanced_checkout_dom' || value.purchase_allowed !== false || value.visible_row_count !== 1 ||
      typeof value.seller_handle !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/u.test(value.seller_handle) ||
      typeof value.selected_shipping_label !== 'string' || value.selected_shipping_label.length < 1 || value.selected_shipping_label.length > 100 ||
      /[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/u.test(value.selected_shipping_label) ||
      !record(value.displayed, ['subtotal', 'shipping', 'total']) ||
      !['subtotal', 'shipping', 'total'].every(key => money(value.displayed[key])) ||
      cents(value.displayed.subtotal.amount) + cents(value.displayed.shipping.amount) !== cents(value.displayed.total.amount)) return null;
  return { seller_handle: value.seller_handle, subtotal: { ...value.displayed.subtotal }, shipping: { ...value.displayed.shipping },
    total: { ...value.displayed.total }, tax: null, fees: null,
    selected_shipping_label: value.selected_shipping_label };
}
function provenanceValid(p, now) {
  return record(p, ['schema', 'source', 'document_ref', 'observed_at', 'expires_at', 'same_document_verified', 'frame_association']) &&
    p.schema === 'discogs_payment_quote_provenance_v1' && p.source === 'current_enhanced_checkout' && UUID.test(p.document_ref) &&
    p.same_document_verified === true && number(p.observed_at) && number(p.expires_at) && p.observed_at <= now &&
    now - p.observed_at < 20 && now < p.expires_at && p.expires_at <= p.observed_at + 20 &&
    record(p.frame_association, ['known_buttons_count', 'known_buttons_associated', 'payment_quote_frame_qualified']) &&
    [0, 1, 2].includes(p.frame_association.known_buttons_count) &&
    typeof p.frame_association.known_buttons_associated === 'boolean' && typeof p.frame_association.payment_quote_frame_qualified === 'boolean';
}
function destinationEvidence(value, documentRef) {
  if (!record(value, ['source', 'document_ref', 'destination_ref', 'match']) ||
      value.source !== 'trusted_same_document_destination_binding' || value.document_ref !== documentRef ||
      !(value.destination_ref === 'saved_shipping_address' || UUID.test(value.destination_ref)) ||
      ![true, false, null].includes(value.match)) return null;
  return { destination_ref: value.destination_ref, match: value.match };
}

const CONFIRMATION_KEYS = ['schema', 'source', 'merchant_document_ref', 'provider_document_ref', 'observed_at', 'expires_at',
  'entry_confirmed_at', 'entry_complete', 'final_total', 'all_payable_charges_included', 'tax_treatment', 'fees_treatment',
  'deferred_charges', 'competing_currency', 'conversion_notice', 'new_notice', 'destination_ref', 'destination_match',
  'shipping_selection_verified', 'terms_verified', 'terms_fingerprint'];
function postEntryConfirmation(value, request, now) {
  if (!record(value, CONFIRMATION_KEYS) || value.schema !== 'discogs_guest_post_entry_confirmation_v1' ||
      value.source !== 'trusted_same_document_post_entry_confirmation' || value.merchant_document_ref !== request.merchant_document_ref ||
      value.provider_document_ref !== request.provider_document_ref || !number(value.observed_at) || !number(value.expires_at) ||
      value.observed_at < request.observed_at || value.observed_at > now || now - value.observed_at >= 20 ||
      now >= value.expires_at || value.expires_at > Math.min(request.expires_at, value.observed_at + 20) ||
      !number(value.entry_confirmed_at) || value.entry_confirmed_at > value.observed_at || value.entry_confirmed_at > now ||
      now - value.entry_confirmed_at >= 60 || !money(value.final_total) ||
      ['entry_complete', 'all_payable_charges_included', 'deferred_charges', 'competing_currency', 'conversion_notice', 'new_notice',
        'destination_match', 'shipping_selection_verified', 'terms_verified'].some(key => typeof value[key] !== 'boolean') ||
      !['included_in_final_total', 'unverified'].includes(value.tax_treatment) || !['included_in_final_total', 'unverified'].includes(value.fees_treatment) ||
      value.destination_ref !== request.approved_destination_ref ||
      !(value.terms_fingerprint === null || typeof value.terms_fingerprint === 'string' && HASH.test(value.terms_fingerprint))) return null;
  return { ...value, final_total: { ...value.final_total } };
}
function observedProvider(metadata, merchant, request, confirmation, callbackSupplied) {
  if (!actualGuestBindings.has(metadata) || metadata.status !== 'observed' || metadata.amount !== merchant.total.amount ||
      merchant.total.currency !== 'USD' || ![null, 'USD'].includes(metadata.currency)) return null;
  const noNotices = confirmation !== null && ['competing_currency', 'conversion_notice', 'new_notice', 'deferred_charges']
    .every(key => confirmation[key] === false);
  const inclusive = noNotices && confirmation.entry_complete === true && confirmation.all_payable_charges_included === true &&
    confirmation.tax_treatment === 'included_in_final_total' && confirmation.fees_treatment === 'included_in_final_total' &&
    confirmation.final_total.amount === metadata.amount;
  const currencyVerified = !callbackSupplied || confirmation !== null && noNotices;
  const provider = { schema: 'discogs_provider_display_evidence_v1', layout_id: OBSERVED_GUEST_LAYOUT,
    amount: metadata.amount, currency: currencyVerified ? 'USD' : null, final_total_verified: inclusive,
    tax_finality: inclusive ? 'included_in_total' : 'unverified', mandatory_charges_finality: inclusive ? 'included_in_total' : 'unverified',
    shipping_selection_verified: inclusive && confirmation.shipping_selection_verified,
    destination_mode: inclusive && confirmation.destination_match ? 'approved_saved_destination' : 'unverified',
    destination_match: inclusive ? confirmation.destination_match : null,
    terms_verified: inclusive && confirmation.terms_verified && confirmation.terms_fingerprint !== null,
    terms_fingerprint: inclusive ? confirmation.terms_fingerprint : null,
    provenance: { source: 'qualified_visible_provider_dom', merchant_document_ref: request.merchant_document_ref,
      provider_document_ref: request.provider_document_ref, observed_at: request.observed_at,
      expires_at: Math.min(request.expires_at, confirmation?.expires_at ?? request.expires_at), same_document_verified: true, frame_associated: true } };
  observedProviders.set(provider, { raw_currency: metadata.currency, currency_verified: currencyVerified,
    notices_verified: noNotices, confirmation_verified: inclusive && confirmation.destination_match && confirmation.shipping_selection_verified &&
      confirmation.terms_verified && confirmation.terms_fingerprint !== null,
    destination_ref: confirmation?.destination_ref ?? null, terms_fingerprint: provider.terms_fingerprint });
  return provider;
}
function freezeEvidence(value) {
  if (value && typeof value === 'object') { for (const child of Object.values(value)) freezeEvidence(child); Object.freeze(value); }
  return value;
}
/** Does not mint authority. Only a fresh actual producer result can pass; caller
 * booleans or a copied JSON report never acquire the private production brand. */
export function isVerifiedCurrentEnhancedPaymentQuote(value, { now = Date.now() / 1000, page = null, destinationRef = null, termsFingerprint = null } = {}) {
  const binding = value && verifiedFinalReports.get(value);
  return !!binding && (page === null || binding.page === page) &&
    (destinationRef === null || binding.destinationRef === destinationRef) &&
    (termsFingerprint === null || binding.termsFingerprint === termsFingerprint) && number(now) && value.final_quote_verified === true &&
    provenanceValid(value.provenance, now) && now < value.provenance.expires_at;
}

/** Pure parser. The observed-layout gate additionally requires the private
 * actual-DOM producer brand; shaped provider data cannot register a layout. */
export function parseEnhancedPaymentQuoteEvidence(input, { now = Date.now() / 1000 } = {}) {
  if (!number(now) || !record(input, ['merchant', 'provider', 'destination', 'provenance']) || !provenanceValid(input.provenance, now)) {
    return base('same_document_freshness_unverified');
  }
  const merchant = merchantEvidence(input.merchant);
  if (!merchant) return base('merchant_summary_unverified');
  const destination = destinationEvidence(input.destination, input.provenance.document_ref);
  const provider = input.provider;
  const providerShape = record(provider, ['schema', 'layout_id', 'amount', 'currency', 'final_total_verified', 'tax_finality',
    'mandatory_charges_finality', 'shipping_selection_verified', 'destination_mode', 'destination_match', 'terms_verified', 'terms_fingerprint', 'provenance']) &&
    provider.schema === 'discogs_provider_display_evidence_v1' && typeof provider.layout_id === 'string' && provider.layout_id.length <= 80;
  const layout = providerShape ? observedProviders.get(provider) : null;
  const pp = providerShape ? provider.provenance : null;
  const frameBound = record(pp, ['source', 'merchant_document_ref', 'provider_document_ref', 'observed_at', 'expires_at', 'same_document_verified', 'frame_associated']) &&
    pp.source === 'qualified_visible_provider_dom' && pp.merchant_document_ref === input.provenance.document_ref &&
    UUID.test(pp.provider_document_ref) && pp.frame_associated === true && pp.same_document_verified === true &&
    number(pp.observed_at) && number(pp.expires_at) && pp.observed_at >= input.provenance.observed_at && pp.observed_at <= now &&
    now - pp.observed_at < 20 && now < pp.expires_at && pp.expires_at <= pp.observed_at + 20;
  const amount = providerShape && typeof provider.amount === 'string' && MONEY.test(provider.amount) ? provider.amount : null;
  const currency = providerShape && typeof provider.currency === 'string' && /^[A-Z]{3}$/u.test(provider.currency) ? provider.currency : null;
  const gates = {
    provider_dom_qualification: !!layout && provider.layout_id === OBSERVED_GUEST_LAYOUT,
    provider_frame_and_document: !!frameBound && input.provenance.frame_association.payment_quote_frame_qualified === true,
    provider_explicit_currency: currency === 'USD',
    provider_amount_matches_merchant: amount !== null && currency === 'USD' && amount === merchant.total.amount,
    provider_final_all_in_total: providerShape && provider.final_total_verified === true,
    final_tax_treatment: providerShape && ['included_in_total', 'not_applicable_verified'].includes(provider.tax_finality),
    mandatory_charges_finality: providerShape && ['included_in_total', 'none_applicable_verified'].includes(provider.mandatory_charges_finality),
    provider_shipping_selection: providerShape && provider.shipping_selection_verified === true,
    provider_delivery_binding: providerShape && provider.destination_mode === 'approved_saved_destination' &&
      provider.destination_match === true && destination?.match === true,
    provider_terms: providerShape && provider.terms_verified === true && typeof provider.terms_fingerprint === 'string' && HASH.test(provider.terms_fingerprint),
  };
  const final = Object.values(gates).every(value => value === true);
  return { ...base(final ? 'final_quote_evidence_verified' : 'provider_final_quote_unverified'), status: final ? 'observed' : 'unverified',
    amount_basis: final ? 'qualified_provider_final_total' : 'merchant_aggregate_preparation_only', merchant_displayed: merchant,
    provider_displayed: { amount, currency: layout ? layout.raw_currency : currency }, final_quote_verified: final,
    final_quote: { amount: final ? amount : null, currency: final ? currency : null, tax: null, fees: null,
      ...(final && layout ? { charge_basis: 'aggregate_inclusive', total: { amount, currency }, tax_treatment: 'included_in_final_total',
        fees_treatment: 'included_in_final_total', deferred_charges: false, aggregate_total_verified: true, all_payable_charges_included: true } : {}) },
    ...(layout ? { current_layout_qualifications: { schema: 'discogs_guest_layout_qualification_v1', layout_id: OBSERVED_GUEST_LAYOUT,
      dom_verified: true, frame_document_verified: !!frameBound, public_pay_control_verified: true,
      merchant_currency_explicit: true, amount_matches_merchant: amount === merchant.total.amount,
      currency_notice_review_verified: layout.notices_verified, post_entry_confirmation_verified: layout.confirmation_verified },
    effective_currency: { currency: layout.currency_verified ? 'USD' : null, source: 'explicit_merchant_usd_matching_bound_pay',
      raw_pay_currency: layout.raw_currency, merchant_currency: 'USD', merchant_document_ref: input.provenance.document_ref,
      provider_document_ref: pp.provider_document_ref, notice_review_verified: layout.notices_verified } } : {}),
    merchant_destination_match: destination?.match ?? null,
    destination_verified: final && gates.provider_delivery_binding, provider_terms_verified: final && gates.provider_terms,
    gates, missing_evidence: Object.keys(gates).filter(key => gates[key] !== true),
    provenance: { ...input.provenance, frame_association: { ...input.provenance.frame_association } } };
}
function releaseReadIfSettled(page) {
  if (finished.has(page) && !pending.get(page)?.size) {
    active.delete(page); finished.delete(page); pending.delete(page);
  }
}
function track(page, task) {
  let tasks = pending.get(page);
  if (!tasks) { tasks = new Set(); pending.set(page, tasks); }
  tasks.add(task);
  const settle = () => { tasks.delete(task); releaseReadIfSettled(page); };
  task.then(settle, settle);
  return task;
}
/** Shared browser owner must await this before releasing its context lease after
 * a cancelled/timed-out read; no late read is allowed to authorize a payment.
 */
export async function waitForEnhancedPaymentQuoteSettlement(page) {
  const tasks = pending.get(page);
  while (tasks?.size) await Promise.allSettled([...tasks]);
}

/** Existing-page production path. The fixed merchant summary and narrowly
 * public #submit-button metadata are inspected. An actual observed nested layout
 * still needs a trusted fresh post-entry final amount/delivery/terms confirmation. Optional
 * destinationMatcher is a trusted same-process opaque binding port, not input
 * data: it receives the actual Page/Document handle and must never return PII.
 */
export async function inspectCurrentEnhancedPaymentQuote(page, { signal = null, timeoutMs = 1500, destinationMatcher = null, finalQuoteMatcher = null } = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 1500 || !page || typeof page !== 'object' ||
      !['url', 'evaluate', 'evaluateHandle', 'frames', 'mainFrame'].every(key => typeof page[key] === 'function') ||
      destinationMatcher !== null && typeof destinationMatcher !== 'function' || finalQuoteMatcher !== null && typeof finalQuoteMatcher !== 'function') return base('read_unavailable');
  if (signal?.aborted) return base('read_cancelled');
  if (active.has(page) || pending.get(page)?.size) return base('quote_read_in_progress');
  active.add(page); finished.delete(page);
  const deadline = Date.now() + timeoutMs, documentRef = randomUUID();
  let documentHandle = null;
  const handles = new Set();
  const disposed = new WeakSet();
  const dispose = handle => {
    if (!handle || typeof handle !== 'object' || typeof handle.dispose !== 'function' || disposed.has(handle)) return;
    disposed.add(handle); void track(page, Promise.resolve().then(() => handle.dispose())).catch(() => {});
  };
  const read = async operation => {
    if (signal?.aborted) fail('read_cancelled');
    if (Date.now() >= deadline) fail('read_timeout');
    let timer, onAbort;
    const task = track(page, Promise.resolve().then(() => {
      if (signal?.aborted) fail('read_cancelled'); if (Date.now() >= deadline) fail('read_timeout'); return operation();
    }));
    try {
      const value = await Promise.race([task, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error('read_timeout'), { [STOP]: 'read_timeout' })), deadline - Date.now());
        if (signal) { onAbort = () => reject(Object.assign(new Error('read_cancelled'), { [STOP]: 'read_cancelled' })); signal.addEventListener('abort', onAbort, { once: true }); }
      })]);
      if (signal?.aborted) fail('read_cancelled'); if (Date.now() >= deadline) fail('read_timeout'); return value;
    } finally { clearTimeout(timer); if (onAbort) signal.removeEventListener('abort', onAbort); }
  };
  const readHandle = operation => read(async () => {
    const handle = await operation(); handles.add(handle);
    if (signal?.aborted || Date.now() >= deadline) { dispose(handle); fail(signal?.aborted ? 'read_cancelled' : 'read_timeout'); }
    return handle;
  });
  try {
    if (await read(() => page.url()) !== ENHANCED_PAYMENT_QUOTE_URL) return base('url_unverified');
    documentHandle = await readHandle(() => page.evaluateHandle(() => document));
    const observedAt = Date.now() / 1000;
    const merchant = await read(() => page.evaluate(collectEnhancedCheckoutDom));
    if (!merchantEvidence(merchant)) return base(merchant?.code === 'access_restricted' ? 'access_restricted' : 'merchant_summary_unverified');
    const currentFrames = await read(() => page.frames());
    if (!Array.isArray(currentFrames) || currentFrames.length > 32) {
      const diagnostic = selectionDiagnostic(Array.isArray(currentFrames) ? 'total_frame_limit' : 'frame_inspection_error', Array.isArray(currentFrames) ? currentFrames.length : 0);
      if (!Array.isArray(currentFrames)) diagnostic.error = 1;
      return { ...base('frame_metadata_unverified'), merchant_displayed: merchantEvidence(merchant),
        guest_pay_metadata: { ...emptyPayMetadata('unsupported_collection'), truncated: Array.isArray(currentFrames), selection_diagnostic: diagnostic } };
    }
    const frames = [...currentFrames]; // A provider may reuse and mutate its array.
    const buttons = frames.filter(frame => {
      try { const url = new URL(frame.url()); return url.origin === 'https://www.paypal.com' && url.pathname === '/smart/buttons' && !url.username && !url.password && !url.hash; }
      catch { return false; }
    });
    let associated = false, buttonsElement = null;
    if (buttons.length === 1 && buttons[0].parentFrame() === page.mainFrame()) {
      const element = await readHandle(() => buttons[0].frameElement());
      buttonsElement = element;
      associated = await read(() => element.isVisible()) === true && await read(() => element.contentFrame()) === buttons[0] &&
        await read(() => element.ownerFrame()) === page.mainFrame();
    }
    const buttonsAssociation = { value: associated, frame: buttons[0] || null, element: buttonsElement };
    let finalizeGuestPayMetadata;
    try { finalizeGuestPayMetadata = await inspectGuestPayMetadata(page, frames, documentHandle, { read, readHandle, buttonsAssociation }); }
    catch (error) {
      if (error?.[STOP]) throw error;
      const diagnostic = selectionDiagnostic('frame_inspection_error', frames.length); diagnostic.error = 1;
      finalizeGuestPayMetadata = async () => ({ ...emptyPayMetadata('unsupported_collection'), selection_diagnostic: diagnostic });
    }
    const destination = destinationMatcher === null ? null : await read(() => destinationMatcher({
      page, documentHandle, document_ref: documentRef, observed_at: observedAt, expires_at: observedAt + 20 }));
    if (await read(() => page.url()) !== ENHANCED_PAYMENT_QUOTE_URL ||
        await read(() => page.evaluate(expected => document === expected, documentHandle)) !== true) return base('document_changed');
    let guestPayMetadata = await finalizeGuestPayMetadata();
    const originalBinding = actualGuestBindings.get(guestPayMetadata), merchantDisplayed = merchantEvidence(merchant);
    let provider = null, finalDestination = destination;
    if (originalBinding && guestPayMetadata.amount === merchantDisplayed.total.amount) {
      const request = Object.freeze({ schema: 'discogs_guest_post_entry_request_v1', merchant_document_ref: documentRef,
        provider_document_ref: randomUUID(), observed_at: observedAt, expires_at: observedAt + 20,
        merchant_total: Object.freeze({ ...merchantDisplayed.total }), pay_amount: guestPayMetadata.amount,
        raw_pay_currency: guestPayMetadata.currency, selected_shipping_label: merchantDisplayed.selected_shipping_label,
        approved_destination_ref: destinationEvidence(destination, documentRef)?.destination_ref ?? 'saved_shipping_address' });
      const response = finalQuoteMatcher === null ? null : await read(() => finalQuoteMatcher(request));
      const confirmation = postEntryConfirmation(response, request, Date.now() / 1000);
      // The callback receives no browser capability. Its observations confer no
      // finality if the actual private document/control relationship changed.
      if (finalQuoteMatcher !== null) {
        const latestMerchant = merchantEvidence(await read(() => page.evaluate(collectEnhancedCheckoutDom)));
        if (JSON.stringify(latestMerchant) !== JSON.stringify(merchantDisplayed)) return base('merchant_summary_changed');
        if (await read(() => page.url()) !== ENHANCED_PAYMENT_QUOTE_URL ||
            await read(() => page.evaluate(expected => document === expected, documentHandle)) !== true) return base('document_changed');
        // All final merchant/callback awaits precede this final private guest
        // check. Provider/report branding below is synchronous from its result.
        guestPayMetadata = await finalizeGuestPayMetadata();
      }
      const after = actualGuestBindings.get(guestPayMetadata);
      if (after && ['guestFrame', 'guestDocument', 'buttonsFrame', 'buttonsDocument', 'merchantDocument'].every(key => after[key] === originalBinding[key])) {
        provider = observedProvider(guestPayMetadata, merchantDisplayed, request, confirmation, finalQuoteMatcher !== null);
        if (destination === null && confirmation?.destination_match === true) finalDestination = {
          source: 'trusted_same_document_destination_binding', document_ref: documentRef,
          destination_ref: confirmation.destination_ref, match: true };
      }
    }
    if ((finalQuoteMatcher === null || !originalBinding) && (await read(() => page.url()) !== ENHANCED_PAYMENT_QUOTE_URL ||
        await read(() => page.evaluate(expected => document === expected, documentHandle)) !== true)) return base('document_changed');
    const report = { ...parseEnhancedPaymentQuoteEvidence({ merchant, provider, destination: finalDestination, provenance: {
      schema: 'discogs_payment_quote_provenance_v1', source: 'current_enhanced_checkout', document_ref: documentRef,
      observed_at: observedAt, expires_at: Math.min(observedAt + 20, provider?.provenance.expires_at ?? observedAt + 20), same_document_verified: true,
      frame_association: { known_buttons_count: Math.min(buttons.length, 2), known_buttons_associated: buttonsAssociation.value,
        payment_quote_frame_qualified: provider !== null } } }),
    guest_pay_metadata: sanitizeGuestPayMetadata(guestPayMetadata) || emptyPayMetadata('unsupported_collection') };
    if (report.final_quote_verified) {
      const layout = observedProviders.get(provider); freezeEvidence(report);
      verifiedFinalReports.set(report, { page, destinationRef: layout.destination_ref, termsFingerprint: layout.terms_fingerprint });
    }
    return report;
  } catch (error) { return base(error?.[STOP] || 'read_failed'); }
  finally {
    for (const handle of handles) dispose(handle);
    finished.add(page); releaseReadIfSettled(page);
  }
}
