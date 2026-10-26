/** Supported CUA operator adapter. No DOM evaluation, keyboard, card or payment API.
 * The agent must attest a just-observed screen; this is not an automatic parser.
 * Bind only the existing private viewer. Installing the factory causes no input.
 */
export function makeCuaGuestOperator(view, { clock = () => Date.now() } = {}) {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
  const hash = /^[0-9a-f]{64}$/u;
  const checkout = 'https://www.discogs.com/marketplace/checkout';
  const cart = 'https://www.discogs.com/sell/cart/';
  const orders = 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50';
  const stages = ['open_cart', 'go_to_checkout', 'open_guest_card'];
  const permitKeys = ['type', 'stage', 'intent_id', 'ready_ref', 'scope_digest', 'request_id',
    'deadline', 'from_document_ref'];
  const screenKeys = ['source', 'observed_at', 'url', 'document_ref', 'control_label', 'point', 'control_ref', 'binding_fingerprint',
    'approval_scope_verified', 'item_scope_verified', 'quote_verified', 'destination_verified',
    'terms_verified', 'no_card_data_entered', 'total', 'currency'];
  const exact = (value, keys) => Boolean(value && typeof value === 'object' &&
    !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype &&
    Reflect.ownKeys(value).length === keys.length && keys.every(key => {
      const d = Object.getOwnPropertyDescriptor(value, key);
      return d?.enumerable === true && Object.hasOwn(d, 'value');
    }));
  const point = value => Array.isArray(value) && value.length === 2 &&
    value.every(n => Number.isFinite(n) && n >= 0 && n <= 20000);
  let context = null, next = 0, frozen = false, busy = false, reason = 'unarmed', idle = Promise.resolve();
  const used = new Set();
  for (const method of ['click', 'scroll', 'getAXState', 'getScreenshot', 'markHandoff']) {
    if (typeof view?.[method] !== 'function') throw new Error('supported_viewer_required');
  }
  function live() {
    if (!context || frozen || !Number.isFinite(clock()) || clock() >= context.deadline) {
      throw new Error('operator_not_live');
    }
  }
  async function operation(work) {
    live();
    if (busy) throw new Error('operator_busy');
    busy = true;
    let resolveIdle;
    idle = new Promise(resolve => { resolveIdle = resolve; });
    try { return await work(); }
    finally { busy = false; resolveIdle(); }
  }
  return Object.freeze({
    status() { return { armed: context !== null, frozen, busy, next_stage: stages[next] ?? null, reason }; },
    arm(value) {
      if (context || frozen || !exact(value, ['intent_id', 'ready_ref', 'scope_digest', 'deadline']) ||
          !uuid.test(value.intent_id) || !uuid.test(value.ready_ref) || !hash.test(value.scope_digest) ||
          !Number.isFinite(value.deadline) || !(clock() < value.deadline && value.deadline - clock() <= 300000)) {
        throw new Error('fresh_operator_binding_required');
      }
      context = Object.freeze({ ...value }); reason = 'armed';
      return this.status();
    },
    async observe() {
      return operation(async () => {
        await view.getAXState({ emit: false }); live();
        const screenshot = await view.getScreenshot({ emit: false }); live();
        return screenshot;
      });
    },
    async dispatch(permit, screen) {
      return operation(async () => {
        const now = clock();
        const extra = permit?.stage === 'go_to_checkout' ? ['control_ref', 'cart_fingerprint'] :
          permit?.stage === 'open_guest_card' ? ['control_ref', 'inspection_fingerprint'] : [];
        if (!exact(permit, [...permitKeys, ...extra]) || permit.type !== 'operator_action' || permit.stage !== stages[next] ||
            permit.intent_id !== context.intent_id || permit.ready_ref !== context.ready_ref ||
            permit.scope_digest !== context.scope_digest || !uuid.test(permit.request_id) ||
            used.has(permit.request_id) || !Number.isFinite(permit.deadline) ||
            !(now < permit.deadline && permit.deadline <= context.deadline) ||
            !(permit.stage === 'open_cart' ? permit.from_document_ref === null : uuid.test(permit.from_document_ref)) ||
            !exact(screen, screenKeys) || screen.source !== 'operator_current_screen' ||
            !Number.isFinite(screen.observed_at) || screen.observed_at * 1000 > now ||
            now - screen.observed_at * 1000 > 20000 || !uuid.test(screen.document_ref) || !point(screen.point) ||
            screen.approval_scope_verified !== true || screen.no_card_data_entered !== true ||
            (permit.stage === 'open_cart' ? screen.control_ref !== null || screen.binding_fingerprint !== null :
              !uuid.test(permit.control_ref) || permit.control_ref !== screen.control_ref ||
              !hash.test(screen.binding_fingerprint) || screen.binding_fingerprint !==
                (permit.stage === 'go_to_checkout' ? permit.cart_fingerprint : permit.inspection_fingerprint))) {
          throw new Error('operator_binding_or_screen_unverified');
        }
        const label = permit.stage === 'open_cart' ? /^Cart(?: 1)?$/u.test(screen.control_label) :
          screen.control_label === (permit.stage === 'go_to_checkout' ? 'Go to Checkout' : 'Debit or Credit Card');
        if (!label || (permit.stage === 'open_cart' ? ![cart, orders, checkout].includes(screen.url) :
          screen.url !== (permit.stage === 'go_to_checkout' ? cart : checkout)) ||
            (permit.from_document_ref !== null && permit.from_document_ref !== screen.document_ref) ||
            (permit.stage !== 'open_cart' && (screen.item_scope_verified !== true || screen.quote_verified !== true ||
              screen.destination_verified !== true || screen.terms_verified !== true ||
              screen.total !== '5.05' || screen.currency !== 'USD'))) {
          throw new Error('operator_target_unverified');
        }
        // Consume before awaiting any effect: an exception cannot authorize a retry.
        const target = [...screen.point];
        used.add(permit.request_id); next++; reason = 'effect_started';
        try {
          live();
          if (clock() >= permit.deadline) throw new Error('operator_deadline_expired');
          await view.click(target);
          live();
          await view.getAXState({ emit: false });
          live(); reason = 'effect_requires_screen_observation';
          return { status: reason, request_id: permit.request_id };
        } catch {
          frozen = true; reason = 'effect_outcome_uncertain';
          throw new Error('operator_effect_uncertain_no_retry');
        }
      });
    },
    async positionBlankForm({ blank_form_verified, no_card_data_entered, point: target, direction = 'down', pages = 1 } = {}) {
      return operation(async () => {
        if (next !== 3 || blank_form_verified !== true || no_card_data_entered !== true ||
            !point(target) || !['down', 'up'].includes(direction) || !Number.isFinite(pages) || pages <= 0 || pages > 4) {
          throw new Error('blank_form_position_unverified');
        }
        try {
          await view.scroll([...target], direction, pages); live();
          await view.getAXState({ emit: false }); live();
          const screenshot = await view.getScreenshot({ emit: false }); live();
          return screenshot;
        } catch {
          frozen = true; reason = 'position_outcome_uncertain';
          throw new Error('operator_position_uncertain');
        }
      });
    },
    async freezeForEntry() {
      frozen = true; reason = 'secure_entry_handoff';
      // An already-running screenshot cannot be cancelled through CUA. Drain it
      // before declaring the secure-entry handoff complete; no card entry yet.
      await idle;
      await view.markHandoff();
      return this.status();
    },
    cancel() { frozen = true; reason = 'cancelled'; return this.status(); },
  });
}

/** Self-contained for installation with factory.toString() in the CUA session.
 * Observe captures the actual supported view. The model then inspects those
 * captures and supplies sanitized metadata to dispatch, with no backend round
 * trip between calls. Only the actor supplies observation IDs and timestamps.
 * Install this factory once. Its metadata registry prevents accidental local
 * reconstruction; it is not durable execution authority across runtime resets.
 * A lost actor's pending server audit must be reconciled before another grant.
 */
export function makeCurrentCheckoutCuaActor(view, grant, { clock = () => Date.now() } = {}) {
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value);
  const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/u.test(value);
  const num = value => typeof value === 'number' && Number.isFinite(value);
  const exact = (value, keys) => Boolean(value && typeof value === 'object' && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Reflect.ownKeys(value).length === keys.length &&
    keys.every(key => { const d = Object.getOwnPropertyDescriptor(value, key);
      return d?.enumerable === true && Object.hasOwn(d, 'value'); }));
  const grantKeys = ['type', 'claim_sha256', 'permit', 'expected_scope', 'order_status', 'payment_status',
    'autonomous_checkout', 'requires_execution_revalidation', 'human_payment_ready', 'card_entry_allowed',
    'payment_allowed', 'final_submission_allowed'];
  const permitKeys = ['type', 'intent_id', 'ready_ref', 'scope_digest', 'request_id', 'stage', 'deadline',
    'from_document_ref', 'control_ref', 'inspection_fingerprint'];
  const scopeKeys = ['intent_id', 'approval_ref', 'release_id', 'listing_id', 'seller', 'quantity', 'currency',
    'max_total', 'rules_revision', 'quote_fingerprint', 'destination_ref', 'terms_fingerprint', 'issued_at', 'expires_at'];
  const proofKeys = ['source', 'observation_id', 'claim_sha256', 'intent_id', 'ready_ref', 'scope_digest',
    'url', 'document_ref', 'control_ref', 'inspection_fingerprint', 'control_label', 'control_count',
    'control_visible', 'control_enabled', 'point', 'approval_scope_verified', 'item_scope_verified',
    'quote_verified', 'destination_verified', 'terms_verified', 'no_card_data_entered', 'normal_checkout',
    'history_restore', 'resubmission_prompt', 'extra_agreement_required', 'challenge_present',
    'release_id', 'listing_id', 'seller', 'quantity', 'item_count', 'seller_count', 'rules_revision',
    'approval_ref', 'currency', 'item', 'shipping', 'total', 'quote_fingerprint', 'destination_ref',
    'terms_fingerprint', 'authorization_current', 'authorization_revoked', 'already_owned', 'existing_order',
    'uncertain_prior_action', 'duplicate_guard_verified', 'rules_current', 'criteria_satisfied',
    'shipping_selection_verified', 'preparation_quote_verified', 'final_payment_quote_verified',
    'deferred_total_notice', 'amount_basis'];
  const now = clock();
  for (const method of ['getAXState', 'getScreenshot', 'click', 'markHandoff']) {
    if (typeof view?.[method] !== 'function') throw new Error('supported_viewer_required');
  }
  if (!num(now) || !exact(grant, grantKeys) || !exact(grant.permit, permitKeys) ||
      !exact(grant.expected_scope, scopeKeys)) throw new Error('current_checkout_grant_unverified');
  const p = grant.permit, s = grant.expected_scope;
  if (grant.type !== 'checkout_claim_granted' || !hash(grant.claim_sha256) ||
      grant.order_status !== 'unverified' || grant.payment_status !== 'unverified' ||
      grant.requires_execution_revalidation !== true ||
      ['autonomous_checkout', 'human_payment_ready', 'card_entry_allowed', 'payment_allowed', 'final_submission_allowed']
        .some(key => grant[key] !== false) || p.type !== 'operator_action' || p.stage !== 'open_guest_card' ||
      !uuid(p.intent_id) || !uuid(p.ready_ref) || !uuid(p.request_id) || !uuid(p.from_document_ref) ||
      !uuid(p.control_ref) || !hash(p.inspection_fingerprint) || !hash(p.scope_digest) || p.intent_id !== s.intent_id ||
      !uuid(s.approval_ref) || !uuid(s.destination_ref) || s.release_id !== 3422100 ||
      s.listing_id !== '1366950910' || s.seller !== 'Mailman21842' || s.quantity !== 1 ||
      s.currency !== 'USD' || s.max_total !== '5.05' || !Number.isSafeInteger(s.rules_revision) || s.rules_revision < 0 ||
      !hash(s.quote_fingerprint) || !hash(s.terms_fingerprint) || !num(s.issued_at) || !num(s.expires_at) ||
      !(0 <= s.issued_at && s.issued_at * 1000 <= now && s.expires_at > s.issued_at && s.expires_at - s.issued_at <= 300) ||
      !num(p.deadline) || !(now < p.deadline && p.deadline - now <= 300000 && p.deadline <= s.expires_at * 1000)) {
    throw new Error('current_checkout_grant_unverified');
  }
  // Snapshot the grant: observe() cannot renew this authorization or its scope.
  const permit = Object.freeze({ ...p }), scope = Object.freeze({ ...s }), claim = grant.claim_sha256;
  let reservations = Object.getOwnPropertyDescriptor(makeCurrentCheckoutCuaActor, 'currentCheckoutReservations')?.value;
  if (!reservations) {
    reservations = Object.freeze({ claims: new Set(), requests: new Set() });
    Object.defineProperty(makeCurrentCheckoutCuaActor, 'currentCheckoutReservations', { value: reservations });
  }
  if (reservations.claims.has(claim) || reservations.requests.has(permit.request_id)) {
    throw new Error('current_checkout_grant_already_reserved');
  }
  // Reserve only validated grants, and retain reservations after every outcome.
  reservations.claims.add(claim); reservations.requests.add(permit.request_id);
  let lastNow = now, frozen = false, busy = false, consumed = false, observation = null, serial = 0;
  let reason = 'armed', idle = Promise.resolve();
  function live() {
    const current = clock();
    if (!num(current) || current < lastNow || current >= permit.deadline) {
      frozen = true; reason = 'claim_expired_or_clock_invalid'; observation = null;
    }
    if (frozen || consumed) throw new Error('current_checkout_actor_not_live');
    lastNow = current;
    return current;
  }
  async function operation(work) {
    live();
    if (busy) throw new Error('current_checkout_actor_busy');
    busy = true;
    let resolveIdle;
    idle = new Promise(resolve => { resolveIdle = resolve; });
    try { return await work(); } finally { busy = false; resolveIdle(); }
  }
  const status = () => ({ frozen, busy, consumed, reason, claim_sha256: claim, deadline: permit.deadline });
  return Object.freeze({
    status,
    async observe() {
      return operation(async () => {
        observation = null;
        // Use capture start so a slow AX/screenshot pair cannot refresh old data.
        const observedAt = live();
        const axState = await view.getAXState({ emit: false }); live();
        const screenshot = await view.getScreenshot({ emit: false }); live();
        const captured = Object.freeze({ id: `${permit.request_id}:${++serial}`, observedAt });
        observation = captured;
        return Object.freeze({ observation_id: captured.id, observed_at: observedAt / 1000,
          claim_sha256: claim, deadline: permit.deadline, ax_state: axState, screenshot });
      });
    },
    async dispatch(proof) {
      return operation(async () => {
        const current = live(), captured = observation;
        if (!exact(proof, proofKeys) || !captured || proof.source !== 'operator_current_screen' ||
            proof.observation_id !== captured.id || proof.claim_sha256 !== claim ||
            current - captured.observedAt > 20000 || proof.intent_id !== permit.intent_id ||
            proof.ready_ref !== permit.ready_ref || proof.scope_digest !== permit.scope_digest ||
            proof.url !== 'https://www.discogs.com/marketplace/checkout' || proof.document_ref !== permit.from_document_ref ||
            proof.control_ref !== permit.control_ref || proof.inspection_fingerprint !== permit.inspection_fingerprint ||
            proof.control_label !== 'Debit or Credit Card' || proof.control_count !== 1 ||
            proof.control_visible !== true || proof.control_enabled !== true ||
            !Array.isArray(proof.point) || proof.point.length !== 2 ||
            ![0, 1].every(i => { const d = Object.getOwnPropertyDescriptor(proof.point, String(i));
              return d && Object.hasOwn(d, 'value') && num(d.value) && d.value >= 0 && d.value <= 20000; }) ||
            Reflect.ownKeys(proof.point).length !== 3 ||
            ['approval_scope_verified', 'item_scope_verified', 'quote_verified', 'destination_verified', 'terms_verified',
              'no_card_data_entered', 'normal_checkout', 'authorization_current', 'duplicate_guard_verified', 'rules_current',
              'criteria_satisfied', 'shipping_selection_verified', 'preparation_quote_verified'].some(key => proof[key] !== true) ||
            ['history_restore', 'resubmission_prompt', 'extra_agreement_required', 'challenge_present', 'authorization_revoked',
              'already_owned', 'existing_order', 'uncertain_prior_action', 'final_payment_quote_verified', 'deferred_total_notice']
              .some(key => proof[key] !== false) ||
            ['release_id', 'listing_id', 'seller', 'quantity', 'rules_revision', 'approval_ref', 'currency', 'quote_fingerprint',
              'destination_ref', 'terms_fingerprint'].some(key => proof[key] !== scope[key]) ||
            proof.item_count !== 1 || proof.seller_count !== 1 || proof.item !== '0.10' || proof.shipping !== '4.95' ||
            proof.total !== '5.05' || proof.amount_basis !== 'explicit_merchant_checkout_total') {
          throw new Error('current_checkout_screen_unverified');
        }
        const target = [proof.point[0], proof.point[1]];
        // Consume both before awaiting any effect. A failure cannot grant a retry.
        consumed = true; observation = null; reason = 'effect_started';
        try {
          await view.click(target);
          const finished = clock();
          if (frozen || !num(finished) || finished < current || finished >= permit.deadline) throw new Error('effect_not_reconciled');
          frozen = true; reason = 'effect_requires_screen_observation';
          return Object.freeze({ status: reason, request_id: permit.request_id, claim_sha256: claim });
        } catch {
          frozen = true; reason = 'effect_outcome_uncertain';
          throw new Error('current_checkout_effect_uncertain_no_retry');
        }
      });
    },
    async freezeForEntry() {
      frozen = true; observation = null; reason = 'secure_entry_handoff';
      await idle;
      await view.markHandoff();
      return status();
    },
    cancel() { frozen = true; observation = null; reason = 'cancelled'; return status(); },
  });
}
