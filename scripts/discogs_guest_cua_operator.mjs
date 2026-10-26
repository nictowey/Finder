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
