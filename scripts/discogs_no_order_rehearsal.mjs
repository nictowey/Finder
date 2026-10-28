/** Read-only cart rehearsal. Caller supplies an authorized existing page and owner data port.
 * No browser creation, navigation, cart mutation, agreement, order or payment operation.
 */
import { inspectCurrentCartDom } from './discogs_cart_inspector.mjs';
import { listingEvidenceCode, snapshotCode } from './discogs_rule_cart_gate.mjs';

const ID = /^[1-9][0-9]{0,15}$/u;
const KEY = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

function stopped(code, cart = null, observedTotal = null, claimState = 'unconfigured') {
  return { status: 'blocked', code, source: 'read_only_cart_rehearsal',
    cart_inspection: cart, observed_total: observedTotal, claim_state: claimState,
    order_submission: 'disabled', stop_required: true, purchase_allowed: false };
}

function inputValue(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Reflect.ownKeys(options).some(key => !['expectedReleaseId', 'expectedListingId', 'expectedRevision', 'idempotencyKey'].includes(key))) return null;
  const own = key => Object.getOwnPropertyDescriptor(options, key)?.value;
  const release = own('expectedReleaseId'), listing = own('expectedListingId');
  const savedRevision = own('expectedRevision'), key = own('idempotencyKey');
  if (typeof release !== 'string' || !ID.test(release) || !Number.isSafeInteger(Number(release)) ||
      typeof listing !== 'string' || !ID.test(listing) || !Number.isSafeInteger(Number(listing)) ||
      !Number.isSafeInteger(savedRevision) || savedRevision < 0 || typeof key !== 'string' ||
      !KEY.test(key) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/u.test(key)) return null;
  return { release, listing, savedRevision, key };
}

function cents(value) {
  if (typeof value !== 'string' || !/^[0-9]{1,7}(?:\.[0-9]{1,2})?$/u.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
}

/** Caller data methods are read-only. No claim is created here or by this module. */
export async function rehearseCurrentCart(page, authenticatedDataPort, options = {}) {
  let input;
  try { input = inputValue(options); } catch { return stopped('invalid_rehearsal_options'); }
  if (!input) return stopped('invalid_rehearsal_options');
  if (!page || typeof page.url !== 'function' || typeof page.evaluate !== 'function' ||
      !authenticatedDataPort || typeof authenticatedDataPort.readSnapshot !== 'function' ||
      typeof authenticatedDataPort.readListing !== 'function') return stopped('read_only_ports_required');
  const context = { input, controlRevision: undefined, rules: undefined, target: undefined };
  let cart = null;
  try {
    let code = snapshotCode(await authenticatedDataPort.readSnapshot(), context);
    if (code) return stopped(code);
    const response = await authenticatedDataPort.readListing({ release_id: Number(input.release), listing: input.listing });
    code = listingEvidenceCode(response, input, context.target);
    if (code) return stopped(code);
    cart = await inspectCurrentCartDom(page, { expectedListingId: input.listing });
    code = snapshotCode(await authenticatedDataPort.readSnapshot(), context);
    if (code) return stopped(code, cart);
    if (!cart.observations?.includes('expected_listing_link_observed') ||
        !cart.observations?.includes('single_cart_row_observed')) return stopped('single_expected_cart_row_unverified', cart);

    const quote = cart.observed_quote;
    const total = quote?.total;
    if (!total || quote.final_quote_verified !== false || total.currency !== context.target.currency) {
      return stopped('delivered_total_unverified', cart);
    }
    const observedTotal = { currency: total.currency, amount: total.amount, verified: false };
    const amount = cents(total.amount);
    if (amount === null) return stopped('delivered_total_unverified', cart);
    if (amount > cents(context.target.max_total)) return stopped('observed_total_above_ceiling', cart, observedTotal);

    if (typeof authenticatedDataPort.readCartClaim !== 'function') {
      return stopped('durable_one_copy_claim_unconfigured', cart, observedTotal);
    }
    const claim = await authenticatedDataPort.readCartClaim({ release_id: Number(input.release), listing: input.listing,
      rules_revision: input.savedRevision, idempotency_key: input.key });
    if (!claim || claim.mode !== 'durable_cart_rehearsal' || claim.purchase_allowed !== false ||
        claim.release_id !== Number(input.release) || claim.listing_id !== input.listing ||
        claim.rules_revision !== input.savedRevision || claim.idempotency_key !== input.key) {
      return stopped('durable_one_copy_claim_unverified', cart, observedTotal);
    }
    if (claim.state !== 'cart_observed') return stopped('cart_add_outcome_requires_reconciliation', cart, observedTotal, claim.state);
    // A visible cart amount cannot establish destination, fees, tax treatment or
    // a final payable quote. No order adapter exists even with a durable claim.
    return stopped('final_delivered_quote_unverified', cart, observedTotal, claim.state);
  } catch {
    return stopped('rehearsal_read_failed', cart);
  }
}
