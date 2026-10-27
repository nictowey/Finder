/** Dormant fixed-control port for the resident enhanced checkout.
 * No runtime installs this port. Explicit gates and the existing payment adapter
 * are required; this module never reads, stores or enters payment input values.
 */
import { createHash, randomUUID } from 'node:crypto';
import * as quoteProducer from './discogs_enhanced_payment_quote.mjs';
import { createEnhancedOrderLedgerPort } from './discogs_enhanced_order_ledger_port.mjs';

const CHECKOUT = 'https://www.discogs.com/marketplace/checkout';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const READERS = ['readVerifiedQuoteReport', 'readDestination', 'readPaymentMethod', 'readTerms', 'readAuthorization', 'readCurrentState'];
const brands = new WeakSet(), states = new WeakMap();
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ?
  `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const economicBinding = (scope, item, shipping, total) => ({ release_id: scope.release_id, listing_id: scope.listing_id,
  seller: scope.seller, quantity: scope.one_copy_limit, currency: scope.currency, item, shipping, total,
  tax: null, fees: null, charge_basis: 'aggregate_inclusive', tax_treatment: 'included_in_final_total',
  fees_treatment: 'included_in_final_total', deferred_charges: false, destination_ref: scope.destination_ref,
  payment_ref: scope.payment_ref, terms_fingerprint: scope.terms_fingerprint, rules_revision: scope.rules_revision });
/** Fingerprints a proposed exact KC economic binding, not proof or authority.
 * The actual producer's same fields must independently match before a fence. */
export function fixedEnhancedPayQuoteFingerprint(scope) {
  return digest(economicBinding(scope, { amount: '0.10', currency: 'USD' }, { amount: '4.95', currency: 'USD' }, { amount: '5.05', currency: 'USD' }));
}
const stop = code => Object.freeze({ status: 'blocked', code, payment_allowed: false, may_submit_again: false });
const validScope = (scope, now) => !!scope && scope.release_id === 3422100 && scope.listing_id === '1366950910' &&
  scope.seller === 'Mailman21842' && scope.one_copy_limit === 1 && scope.currency === 'USD' && scope.max_total === '5.05' &&
  ['intent_id', 'approval_ref', 'destination_ref', 'payment_ref'].every(key => UUID.test(scope[key] || '')) &&
  HASH.test(scope.quote_fingerprint || '') && HASH.test(scope.terms_fingerprint || '') &&
  Number.isSafeInteger(scope.rules_revision) && scope.rules_revision >= 0 && Number.isFinite(scope.expires_at) &&
  now < scope.expires_at && scope.expires_at - now <= 300;
const rawCategory = url => {
  try { const parsed = new URL(url); return parsed.origin === 'https://www.paypal.com' && !parsed.username && !parsed.password ?
    parsed.pathname === '/smart/buttons' && !parsed.hash ? 'buttons' : parsed.pathname.startsWith('/smart/') && parsed.pathname !== '/smart/buttons' ? 'guest' : 'other' : 'other'; }
  catch { return 'other'; }
};

// Evaluates only fixed public button anatomy in its already-bound Document.
// No input, arbitrary attribute, raw HTML, address or application-state read.
function publicControl(node, binding) {
  try {
    const here = new URL(location.href);
    if (document !== binding.document || here.href !== binding.url || here.origin !== 'https://www.paypal.com' ||
        !here.pathname.startsWith('/smart/') || here.pathname === '/smart/buttons' || here.username || here.password ||
        document.readyState !== 'complete' || !node.isConnected || node.ownerDocument !== document ||
        document.querySelectorAll('#submit-button').length !== 1 || node.id !== 'submit-button' ||
        node.tagName !== 'BUTTON' || node.getAttribute('type') !== 'button') return null;
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || current.getAttribute('aria-hidden') === 'true') return null;
      const style = document.defaultView.getComputedStyle(current);
      if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
          style.opacity === '0' || style.contentVisibility === 'hidden') return null;
    }
    if (![...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)) return null;
    let count = 0, size = 0;
    const text = value => {
      if (++count > 12) throw new Error('unsupported');
      if (value.nodeType === 3) { size += (value.nodeValue || '').length; if (size > 40) throw new Error('unsupported'); return value.nodeValue || ''; }
      if (value.nodeType !== 1 || value !== node && value.tagName !== 'SPAN') throw new Error('unsupported');
      return [...value.childNodes].map(text).join('');
    };
    const label = text(node).replace(/\s+/gu, ' ').trim();
    if (!/^Pay \$5\.05(?: USD)?$/u.test(label)) return null;
    return { label, enabled: !node.disabled && !node.matches(':disabled') && node.getAttribute('aria-disabled') !== 'true' };
  } catch { return null; }
}

function boundDocument(binding) {
  try {
    const here = new URL(location.href);
    if (here.href !== binding.url || (binding.main ? here.href !== 'https://www.discogs.com/marketplace/checkout' :
      here.origin !== 'https://www.paypal.com' || here.username || here.password)) return null;
    return document;
  } catch { return null; }
}
function fixedControlElement(binding) {
  try {
    const here = new URL(location.href);
    if (document !== binding.document || here.href !== binding.url || here.origin !== 'https://www.paypal.com' ||
      !here.pathname.startsWith('/smart/') || here.pathname === '/smart/buttons' || here.username || here.password) return null;
    const nodes = document.querySelectorAll('#submit-button');
    return nodes.length === 1 && nodes[0].tagName === 'BUTTON' && nodes[0].getAttribute('type') === 'button' ? nodes[0] : null;
  } catch { return null; }
}

function factory(options, fixture) {
  if (!options || typeof options !== 'object' || Object.keys(options).some(key => !['page', 'context', 'acquireContextLease',
    'evidencePorts', 'liveGate', 'clock', 'orderFencePort'].includes(key))) throw new TypeError('fixed_pay_configuration_required');
  const { page, context, acquireContextLease, evidencePorts, liveGate = false, clock = () => Date.now(), orderFencePort = null } = options || {};
  if (typeof liveGate !== 'boolean' || typeof clock !== 'function') throw new TypeError('fixed_pay_configuration_required');
  if (!fixture && orderFencePort !== null) throw new TypeError('fixture_ledger_override_forbidden');
  if (!fixture && Object.hasOwn(options, 'clock')) throw new TypeError('fixture_clock_override_forbidden');
  const state = { page, context, acquireContextLease, evidencePorts, enabled: liveGate, fixture, clock,
    mode: 'unarmed', pending: new Set(), handles: new Set(), release: null, binding: null,
    scope: null, entryAt: null, control: null, fenceAttempted: false, fenced: false, consumed: false, closed: false,
    deadline: null, stopped: false, cleanup: null, quoteTimes: null,
    ledger: orderFencePort || createEnhancedOrderLedgerPort() };
  const live = (scope, signal) => {
    if (!state.enabled || state.closed || state.stopped || signal?.aborted || !validScope(scope, clock() / 1000) ||
        state.deadline !== null && clock() >= state.deadline ||
        state.scope && canonical(scope) !== canonical(state.scope)) throw new Error('fixed_pay_stopped');
    if (state.mode === 'entry' || state.mode === 'entry_pending') throw new Error('secure_entry_reads_suspended');
  };
  const track = operation => {
    const task = Promise.resolve().then(operation); state.pending.add(task);
    task.then(() => state.pending.delete(task), () => state.pending.delete(task)); return task;
  };
  const read = async (scope, signal, operation) => {
    live(scope, signal);
    const remaining = Math.min(state.deadline ?? clock() + 30_000, scope.expires_at * 1000) - clock();
    let timer, abort;
    const task = track(() => { live(scope, signal); return operation(); });
    try {
      const value = await Promise.race([task, new Promise((_, reject) => {
        const halt = () => { state.stopped = true; reject(new Error('fixed_pay_stopped')); };
        timer = setTimeout(halt, Math.max(0, remaining));
        if (signal) { abort = halt; signal.addEventListener('abort', abort, { once: true }); }
      })]);
      live(scope, signal); return value;
    } finally { clearTimeout(timer); if (abort) signal.removeEventListener('abort', abort); }
  };
  const lease = () => {
    if (state.release) return;
    if (typeof acquireContextLease !== 'function') throw new Error('shared_context_lease_required');
    const release = acquireContextLease();
    if (typeof release !== 'function') throw new Error('context_busy'); state.release = release;
  };
  const readHandle = (scope, signal, operation) => read(scope, signal, async () => {
    const value = await operation(); state.handles.add(value); return value;
  });
  const documentFor = async (scope, signal, target) => {
    const url = await read(scope, signal, () => target.url());
    return readHandle(scope, signal, () => target.evaluateHandle(boundDocument, { url, main: target === page }));
  };
  const same = (scope, signal, target, document) => read(scope, signal, () => target.evaluate(expected => document === expected, document));
  const edge = async (scope, signal, frame, parent, element) =>
    await read(scope, signal, () => frame.parentFrame()) === parent && await read(scope, signal, () => element.isVisible()) === true &&
    await read(scope, signal, () => element.contentFrame()) === frame && await read(scope, signal, () => element.ownerFrame()) === parent;
  const capture = async (scope, signal, requireEnabled = true) => {
    live(scope, signal); lease();
    if (!page || !context || typeof context.pages !== 'function' || typeof page.context !== 'function' ||
        await read(scope, signal, () => page.context()) !== context ||
        !(await read(scope, signal, () => context.pages())).includes(page) || await read(scope, signal, () => page.url()) !== CHECKOUT) throw new Error('resident_page_required');
    let binding = state.binding;
    if (!binding) {
      const frames = await read(scope, signal, () => page.frames());
      if (!Array.isArray(frames) || frames.length > 32) throw new Error('frame_inventory_unverified');
      const main = await read(scope, signal, () => page.mainFrame());
      const urls = new Map(), inventory = [];
      for (const frame of frames) {
        const url = await read(scope, signal, () => frame === main ? page.url() : frame.url()); urls.set(frame, url);
        const item = { frame, parent: frame === main ? null : await read(scope, signal, () => frame.parentFrame()), url };
        if (frame !== main && rawCategory(url) !== 'other') {
          item.element = await readHandle(scope, signal, () => frame.frameElement());
          item.visible = await read(scope, signal, () => item.element.isVisible());
          item.content = await read(scope, signal, () => item.element.contentFrame());
          item.owner = await read(scope, signal, () => item.element.ownerFrame());
          item.document = await documentFor(scope, signal, frame);
        }
        inventory.push(item);
      }
      const buttons = frames.filter(frame => rawCategory(urls.get(frame)) === 'buttons');
      if (!frames.includes(main) || buttons.length !== 1 || await read(scope, signal, () => buttons[0].parentFrame()) !== main) throw new Error('buttons_frame_unverified');
      const candidates = [];
      for (const frame of frames) if (rawCategory(urls.get(frame)) === 'guest' && await read(scope, signal, () => frame.parentFrame()) === buttons[0]) {
        const element = await readHandle(scope, signal, () => frame.frameElement());
        if (await read(scope, signal, () => element.isVisible()) === true) candidates.push({ frame, element });
      }
      if (candidates.length !== 1) throw new Error('guest_frame_unverified');
      const guest = candidates[0], buttonsElement = await readHandle(scope, signal, () => buttons[0].frameElement());
      const mainDocument = await documentFor(scope, signal, page), buttonsDocument = await documentFor(scope, signal, buttons[0]);
      const guestDocument = await documentFor(scope, signal, guest.frame);
      const handle = await readHandle(scope, signal, () => guest.frame.evaluateHandle(fixedControlElement,
        { document: guestDocument, url: urls.get(guest.frame) }));
      const control = handle?.asElement?.();
      if (!control) throw new Error('fixed_control_unverified'); state.handles.add(control);
      binding = { frames: [...frames], inventory, urls, main, mainDocument, buttons: buttons[0], buttonsDocument, buttonsElement,
        guest: guest.frame, guestDocument, guestElement: guest.element, control };
      state.binding = binding;
    }
    // Ancestors first, leaf and its exact node last. No selector is supplied by a caller.
    const frames = await read(scope, signal, () => page.frames());
    if (!Array.isArray(frames) || frames.length !== binding.frames.length || frames.some((frame, i) => frame !== binding.frames[i])) throw new Error('frame_inventory_changed');
    for (const frame of binding.frames) if (await read(scope, signal, () => frame === binding.main ? page.url() : frame.url()) !== binding.urls.get(frame)) throw new Error('frame_url_changed');
    for (const item of binding.inventory) {
      if (item.frame !== binding.main && await read(scope, signal, () => item.frame.parentFrame()) !== item.parent) throw new Error('frame_parent_changed');
      if (item.element && (await read(scope, signal, () => item.element.isVisible()) !== item.visible ||
          await read(scope, signal, () => item.element.contentFrame()) !== item.content ||
          await read(scope, signal, () => item.element.ownerFrame()) !== item.owner ||
          !await same(scope, signal, item.frame, item.document))) throw new Error('frame_classification_changed');
    }
    const merchant = await read(scope, signal, () => page.evaluate(quoteProducer.collectEnhancedPaymentQuoteDom));
    if (merchant?.status !== 'observed' || merchant.seller_handle !== scope.seller || merchant.visible_row_count !== 1 ||
        merchant.displayed?.subtotal?.amount !== '0.10' || merchant.displayed.subtotal.currency !== 'USD' ||
        merchant.displayed?.shipping?.amount !== '4.95' || merchant.displayed.shipping.currency !== 'USD' ||
        merchant.displayed?.total?.amount !== '5.05' || merchant.displayed.total.currency !== 'USD' ||
        state.shippingLabel && merchant.selected_shipping_label !== state.shippingLabel) throw new Error('merchant_quote_changed');
    if (!await same(scope, signal, page, binding.mainDocument) ||
        !await same(scope, signal, binding.buttons, binding.buttonsDocument) ||
        !await edge(scope, signal, binding.buttons, binding.main, binding.buttonsElement) ||
        !await edge(scope, signal, binding.guest, binding.buttons, binding.guestElement) ||
        !await same(scope, signal, binding.guest, binding.guestDocument)) throw new Error('control_binding_changed');
    const metadata = await read(scope, signal, () => binding.control.evaluate(publicControl,
      { document: binding.guestDocument, url: binding.urls.get(binding.guest) }));
    if (!metadata || requireEnabled && metadata.enabled !== true) throw new Error('fixed_control_unverified');
    return metadata;
  };
  const readEvidence = async (name, scope, signal) => {
    live(scope, signal); lease();
    if (!evidencePorts || READERS.some(key => typeof evidencePorts[key] !== 'function')) throw new Error('trusted_evidence_readers_required');
    return read(scope, signal, () => evidencePorts[name](scope, { signal }));
  };
  const snapshot = async (scope, signal) => {
    const startedAt = clock() / 1000;
    const evidence = {};
    for (const key of READERS) evidence[key] = await readEvidence(key, scope, signal);
    const raw = evidence.readVerifiedQuoteReport, d = evidence.readDestination,
      m = evidence.readPaymentMethod, t = evidence.readTerms, a = evidence.readAuthorization, s = evidence.readCurrentState;
    const rawVerified = typeof quoteProducer.isVerifiedCurrentEnhancedPaymentQuote === 'function' &&
      quoteProducer.isVerifiedCurrentEnhancedPaymentQuote(raw, { now: clock() / 1000, page,
        destinationRef: scope.destination_ref, termsFingerprint: scope.terms_fingerprint });
    const final = raw?.final_quote, merchant = raw?.merchant_displayed;
    if (!rawVerified || raw === state.lastReport || raw.provenance.observed_at < startedAt || raw.destination_verified !== true || raw.provider_terms_verified !== true ||
        merchant?.seller_handle !== scope.seller || final?.charge_basis !== 'aggregate_inclusive' ||
        final.total?.amount !== '5.05' || final.total?.currency !== 'USD' || final.tax !== null || final.fees !== null ||
        final.tax_treatment !== 'included_in_final_total' || final.fees_treatment !== 'included_in_final_total' ||
        final.all_payable_charges_included !== true || final.aggregate_total_verified !== true || final.deferred_charges !== false ||
        raw.effective_currency?.currency !== 'USD' || raw.effective_currency?.source !== 'explicit_merchant_usd_matching_bound_pay' ||
        raw.effective_currency?.notice_review_verified !== true || !raw.current_layout_qualifications?.post_entry_confirmation_verified ||
        merchant.subtotal?.amount !== '0.10' || merchant.subtotal?.currency !== 'USD' || merchant.shipping?.amount !== '4.95' || merchant.shipping?.currency !== 'USD') throw new Error('branded_final_quote_required');
    if (raw.provenance.observed_at < state.entryAt || raw.provenance.expires_at <= clock() / 1000) throw new Error('post_entry_quote_expired');
    if (!state.quoteTimes) state.quoteTimes = { observed_at: raw.provenance.observed_at, expires_at: raw.provenance.expires_at };
    if (raw.provenance.expires_at < state.quoteTimes.expires_at) throw new Error('post_entry_confirmation_shortened');
    if (digest(economicBinding(scope, merchant.subtotal, merchant.shipping, final.total)) !== scope.quote_fingerprint) throw new Error('quote_fingerprint_unverified');
    if (state.shippingLabel && state.shippingLabel !== merchant.selected_shipping_label) throw new Error('selected_shipping_changed');
    state.shippingLabel = merchant.selected_shipping_label;
    const q = { source: 'verified_noncommitting_final_quote', intent_id: scope.intent_id, release_id: scope.release_id,
      listing_id: scope.listing_id, seller: scope.seller, rules_revision: scope.rules_revision, quantity: 1, currency: 'USD',
      quote_fingerprint: scope.quote_fingerprint, destination_ref: scope.destination_ref, payment_ref: scope.payment_ref,
      final_quote_verified: true, charge_basis: final.charge_basis, all_payable_charges_included: true, payment_action: 'none',
      shipping_selection_verified: true, selected_shipping_ref: d?.selected_shipping_ref,
      selected_shipping_label: merchant.selected_shipping_label, ...state.quoteTimes,
      item: { ...merchant.subtotal }, shipping: { ...merchant.shipping }, tax: null, fees: null,
      total: { ...final.total }, tax_treatment: final.tax_treatment, fees_treatment: final.fees_treatment,
      deferred_charges: false, aggregate_total_verified: true };
    evidence.readFinalQuote = q;
    if (!Number.isFinite(q.observed_at) || !Number.isFinite(q.expires_at) || q.observed_at > clock() / 1000 ||
        q.observed_at < state.entryAt || clock() / 1000 - q.observed_at > 20 || clock() / 1000 >= q.expires_at ||
        d?.source !== 'verified_current_checkout_destination' || d.current !== true || d.selected_for_checkout !== true ||
        d.destination_ref !== scope.destination_ref || d.approved_for_intent !== scope.intent_id || d.selected_shipping_ref !== q.selected_shipping_ref ||
        m?.source !== 'verified_current_enhanced_payment_method' || m.selected !== true || m.method_kind !== 'card' ||
        m.payment_ref !== scope.payment_ref || m.approved_for_intent !== scope.intent_id ||
        t?.source !== 'owner_confirmed_current_cart_terms' || t.current !== true || t.fingerprint !== scope.terms_fingerprint || t.approved_for_intent !== scope.intent_id ||
        a?.source !== 'owner_approved_exact_order' || a.current !== true || a.revoked !== false || canonical(a.scope) !== canonical(scope) ||
        s?.source !== 'current_guarded_candidate' || s.intent_id !== scope.intent_id || s.release_id !== scope.release_id ||
        s.listing_id !== scope.listing_id || s.seller !== scope.seller || s.active !== true || s.kill_switch !== false || s.owned !== false ||
        s.rules_revision !== scope.rules_revision || s.claim_state !== 'cart_observed' || s.one_copy_limit !== 1 ||
        s.intent_state !== (state.fenced ? 'submission_unknown' : 'prepared')) throw new Error('fresh_post_entry_evidence_required');
    state.lastReport = raw; return evidence;
  };
  const ports = { kind: 'trusted_production_enhanced_payment_v1' };
  for (const name of [...READERS.filter(key => key !== 'readVerifiedQuoteReport'), 'readFinalQuote']) ports[name] = async scope => {
    if (!state.enabled || state.mode !== 'ready') return stop('fixed_pay_disabled_or_entry_incomplete');
    if (name === 'readFinalQuote') { const evidence = await snapshot(scope); return evidence[name]; }
    return readEvidence(name, scope);
  };
  ports.readPayControl = async scope => {
    if (!state.enabled || state.mode !== 'ready') return stop('fixed_pay_disabled_or_entry_incomplete');
    const metadata = await capture(scope);
    if (!state.control) state.control = Object.freeze({ source: 'verified_current_enhanced_pay_control', url: CHECKOUT,
      control_ref: randomUUID(), control_fingerprint: digest({ scope, label: metadata.label }), observed_label: metadata.label,
      visible: true, enabled: true, selector_verified: true, commit_semantics: 'creates_order_or_payment',
      observed_at: clock() / 1000, expires_at: Math.min(scope.expires_at, state.quoteTimes?.expires_at ?? 0, clock() / 1000 + 20) });
    if (metadata.label !== state.control.observed_label) throw new Error('fixed_control_changed');
    return state.control;
  };
  ports.fencePossibleSubmit = async (intentId, quote, scope) => {
    if (!state.enabled || state.mode !== 'ready' || state.fenceAttempted || intentId !== scope?.intent_id) return stop('fixed_pay_fence_unavailable');
    state.fenceAttempted = true;
    const current = await snapshot(scope); const metadata = await capture(scope); live(scope);
    if (state.control && metadata.label !== state.control.observed_label) return stop('fixed_control_changed');
    if (canonical(current.readFinalQuote) !== canonical(quote)) return stop('fixed_quote_binding_changed');
    const fence = await track(() => state.ledger.fencePossibleSubmit(intentId, quote, scope));
    if (fence?.durable !== true || fence.state !== 'submission_unknown' || fence.intent_id !== intentId || fence.may_submit_again !== false) return stop('durable_order_fence_unverified');
    state.fenced = true; return fence;
  };
  ports.dispatchObservedPay = async ({ scope, control_ref, control_fingerprint, timeout_ms, signal } = {}) => {
    if (!state.enabled || state.mode !== 'ready' || !state.fenced || state.consumed || !state.control ||
        control_ref !== state.control.control_ref || control_fingerprint !== state.control.control_fingerprint ||
        !Number.isFinite(timeout_ms) || timeout_ms <= 0) return stop('fixed_pay_dispatch_unavailable');
    // Reserve the one-shot synchronously, before any awaited proof. Concurrent
    // calls and a failed post-fence proof must never gain another dispatch.
    state.consumed = true;
    await snapshot(scope, signal); const metadata = await capture(scope, signal); live(scope, signal);
    if (metadata.label !== state.control.observed_label) throw new Error('fixed_control_changed');
    // Capture may await several browser proofs. Recheck current authority after
    // those awaits, then recheck only the retained fixed node before dispatch.
    const authorization = await readEvidence('readAuthorization', scope, signal);
    const current = await readEvidence('readCurrentState', scope, signal);
    if (authorization?.source !== 'owner_approved_exact_order' || authorization.current !== true || authorization.revoked !== false ||
        canonical(authorization.scope) !== canonical(scope) || current?.source !== 'current_guarded_candidate' ||
        current.intent_id !== scope.intent_id || current.release_id !== scope.release_id || current.listing_id !== scope.listing_id ||
        current.seller !== scope.seller || current.rules_revision !== scope.rules_revision || current.claim_state !== 'cart_observed' ||
        current.one_copy_limit !== 1 || current.active !== true || current.kill_switch !== false || current.owned !== false ||
        current.intent_state !== 'submission_unknown') throw new Error('final_authority_changed');
    const finalControl = await read(scope, signal, () => state.binding.control.evaluate(publicControl,
      { document: state.binding.guestDocument, url: state.binding.urls.get(state.binding.guest) }));
    if (finalControl?.enabled !== true || finalControl.label !== state.control.observed_label) throw new Error('fixed_control_changed');
    const remaining = Math.min(timeout_ms, (state.control.expires_at - clock() / 1000) * 1000,
      (scope.expires_at - clock() / 1000) * 1000);
    if (remaining <= 0) throw new Error('fixed_pay_expired');
    state.mode = 'submission_unknown';
    const effectDeadline = clock() + remaining;
    await track(() => {
      if (signal?.aborted || state.stopped || clock() >= effectDeadline) throw new Error('fixed_pay_stopped');
      return state.binding.control.click({ timeout: effectDeadline - clock(), noWaitAfter: true });
    });
    return stop('submission_unknown');
  };
  state.read = read; state.capture = capture; state.snapshot = snapshot; state.lease = lease;
  const value = Object.freeze(ports); brands.add(value); states.set(value, state); return value;
}

export function createEnhancedFixedPayPort(options = {}) { return factory(options, false); }
export function createEnhancedFixedPayFixturePort(options = {}) {
  if (process.env.NODE_TEST_CONTEXT === undefined) throw new Error('fixture_context_required');
  return factory(options, true);
}
export const isFixedEnhancedPayPort = ports => !!ports && brands.has(ports);

export async function beginFixedEnhancedPaySecureEntry(ports, scope, { signal = null } = {}) {
  const s = states.get(ports);
  if (!s?.enabled || s.closed || s.fenceAttempted || s.mode !== 'unarmed') return stop('fixed_pay_disabled_or_busy');
  s.mode = 'entry_pending'; s.deadline = null;
  while (s.pending.size) await Promise.allSettled([...s.pending]);
  await quoteProducer.waitForEnhancedPaymentQuoteSettlement(s.page);
  if (signal?.aborted || !validScope(scope, s.clock() / 1000)) return stop('secure_entry_unavailable');
  try {
    s.lease(); s.scope = Object.freeze({ ...scope }); s.mode = 'arming_entry';
    await s.capture(scope, signal, false);
    s.entryAt = s.clock() / 1000; s.mode = 'entry';
    return Object.freeze({ status: 'entry_ready', reads_suspended: true, payment_allowed: false });
  } catch { s.mode = 'entry_pending'; return stop('secure_entry_unavailable'); }
}

export async function resumeFixedEnhancedPayAfterEntry(ports, scope, { signal = null } = {}) {
  const s = states.get(ports);
  if (!s?.enabled || s.closed || s.mode !== 'entry' || canonical(scope) !== canonical(s.scope)) return stop('secure_entry_not_active');
  s.mode = 'post_entry_revalidation'; s.deadline = s.clock() + 30_000;
  try { await s.snapshot(scope, signal); await s.capture(scope, signal); s.mode = 'ready'; return stop('fresh_post_entry_context_verified'); }
  catch { s.mode = 'entry'; return stop('fresh_post_entry_evidence_required'); }
}

/** Called by the adapter's finally path. An issued operation retains the shared
 * lease through actual settlement; entry deliberately keeps that lease held. */
export function finishFixedEnhancedPayPortAttempt(ports) {
  const s = states.get(ports); if (!s) return;
  if (['entry', 'entry_pending', 'arming_entry', 'post_entry_revalidation'].includes(s.mode)) return;
  if (!s.cleanup) s.cleanup = (async () => {
    s.stopped = true;
    while (s.pending.size) await Promise.allSettled([...s.pending]);
    await quoteProducer.waitForEnhancedPaymentQuoteSettlement(s.page);
    for (const handle of s.handles) { try { await handle?.dispose?.(); } catch {} }
    s.handles.clear(); s.release?.(); s.release = null; s.closed = true;
  })();
}
export async function waitForFixedEnhancedPayPortSettlement(ports) {
  const s = states.get(ports); if (!s) return;
  while (s.pending.size) await Promise.allSettled([...s.pending]);
  await s.cleanup;
}
export function closeFixedEnhancedPayPort(ports) {
  const s = states.get(ports); if (!s) return;
  s.mode = 'closed'; finishFixedEnhancedPayPortAttempt(ports);
}
