/** Metadata-only fixed bridge to DiscogsOrderLedger. No browser/payment port.
 * Production uses /bridge/discogs-real-orders.sqlite3 and never initializes it.
 * The Pay adapter remains default-off and no dispatcher is supplied here.
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { isAbsolute } from 'node:path';

const SCRIPT = fileURLToPath(new URL('./discogs_order_ledger_port.py', import.meta.url));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH = /^[0-9a-f]{64}$/u;
const MONEY = /^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$/u;
const stop = code => Object.freeze({ status: 'blocked', code, durable: false,
  purchase_allowed: false });
const cents = value => typeof value === 'string' && MONEY.test(value) ?
  BigInt(value.split('.')[0]) * 100n + BigInt(value.split('.')[1]) : null;

function expectedOrder(scope, quote, now) {
  if (!scope || !quote ||
      ![scope.intent_id, scope.approval_ref, scope.destination_ref, scope.payment_ref].every(value => UUID.test(value || '')) ||
      scope.release_id !== 3422100 || scope.listing_id !== '1366950910' ||
      scope.seller !== 'Mailman21842' || scope.one_copy_limit !== 1 ||
      scope.currency !== 'USD' || scope.max_total !== '5.05' ||
      !Number.isSafeInteger(scope.rules_revision) || scope.rules_revision < 0 ||
      !HASH.test(scope.quote_fingerprint || '') ||
      quote.source !== 'verified_noncommitting_final_quote' || quote.final_quote_verified !== true ||
      quote.all_payable_charges_included !== true || quote.payment_action !== 'none' ||
      quote.intent_id !== scope.intent_id || quote.release_id !== scope.release_id ||
      quote.listing_id !== scope.listing_id || quote.seller !== scope.seller ||
      quote.rules_revision !== scope.rules_revision || quote.quantity !== 1 ||
      quote.currency !== 'USD' || quote.quote_fingerprint !== scope.quote_fingerprint ||
      quote.destination_ref !== scope.destination_ref || quote.payment_ref !== scope.payment_ref ||
      !Number.isFinite(quote.expires_at) || !Number.isFinite(scope.expires_at) ||
      !(now < quote.expires_at && quote.expires_at <= scope.expires_at && quote.expires_at <= now + 300)) return null;
  const amount = key => quote[key] && quote[key].currency === 'USD' ? cents(quote[key].amount) : null;
  const item = amount('item'), shipping = amount('shipping'), tax = amount('tax'),
    fee = quote.fees === null && quote.fees_absent_verified === true ? 0n : amount('fees'),
    total = amount('total');
  if ([item, shipping, tax, fee, total].some(value => value === null) ||
      item + shipping + tax + fee !== total || total > cents('5.05')) return null;
  return Object.freeze({ intent_id: scope.intent_id, approval_ref: scope.approval_ref,
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842',
    quoted_total: quote.total.amount, currency: 'USD', quote_fingerprint: scope.quote_fingerprint,
    rules_revision: scope.rules_revision, destination_ref: scope.destination_ref,
    payment_ref: scope.payment_ref, quote_expires_at: quote.expires_at });
}

async function run(command, fixturePath = null) {
  const encoded = JSON.stringify(command);
  if (Buffer.byteLength(encoded) > 4096) return stop('ledger_request_too_large');
  return new Promise(resolve => {
    const child = spawn('python3', [SCRIPT, ...(fixturePath ? ['--test-ledger', fixturePath] : [])], {
      stdio: ['pipe', 'pipe', 'pipe'], env: fixturePath ?
        { PATH: process.env.PATH || '', TMPDIR: process.env.TMPDIR || '/tmp',
          SNIPER_LEDGER_FIXTURE_TEST: '1' } :
        { PATH: process.env.PATH || '' },
    });
    let output = '', done = false;
    const finish = result => {
      if (done) return; done = true; clearTimeout(timer); resolve(result);
    };
    const timer = setTimeout(() => { child.kill(); finish(stop('ledger_timeout')); }, 7000);
    child.stdout.on('data', chunk => {
      output += chunk.toString('utf8');
      if (Buffer.byteLength(output) > 4096) { child.kill(); finish(stop('ledger_response_too_large')); }
    });
    child.stderr.on('data', () => {}); // Never forward internal paths or payloads.
    child.stdin.on('error', () => {}); // A rejected child may close stdin first.
    child.on('error', () => finish(stop('ledger_unavailable')));
    child.on('close', code => {
      if (done) return;
      if (code !== 0) return finish(stop('ledger_unavailable'));
      try {
        const value = JSON.parse(output);
        finish(value && typeof value === 'object' && !Array.isArray(value) &&
          ['observed', 'initialized', 'blocked'].includes(value.status) ? value : stop('ledger_response_invalid'));
      } catch { finish(stop('ledger_response_invalid')); }
    });
    child.stdin.end(encoded + '\n');
  });
}

function createPort(fixturePath = null) {
  return Object.freeze({
    /** A single metadata operation: idempotent full-scope prepare then atomic
     * prepared -> submission_unknown transition. A lost response is unknown.
     */
    fencePossibleSubmit: async (intentId, quote, scope) => {
      if (intentId !== scope?.intent_id) return stop('ledger_identity_unverified');
      const expected = expectedOrder(scope, quote, Date.now() / 1000);
      if (!expected) return stop('ledger_quote_unverified');
      const response = await run({ op: 'fence', expected }, fixturePath);
      const row = response?.ledger;
      return response?.status === 'observed' && response.durable === true &&
        row?.intent_id === intentId && row.release_id === 3422100 &&
        row.state === 'submission_unknown' && row.may_submit_again === false ?
          Object.freeze({ intent_id: intentId, state: 'submission_unknown',
            may_submit_again: false, durable: true }) : stop('ledger_fence_unverified');
    },
    prepare: async (scope, quote) => {
      const expected = expectedOrder(scope, quote, Date.now() / 1000);
      return expected ? run({ op: 'prepare', expected }, fixturePath) : stop('ledger_quote_unverified');
    },
    status: () => run({ op: 'status', release_id: 3422100 }, fixturePath),
    reconcile: (intentId, evidence) => UUID.test(intentId || '') ?
      run({ op: 'reconcile', intent_id: intentId, evidence }, fixturePath) :
      Promise.resolve(stop('ledger_identity_unverified')),
  });
}

export function createEnhancedOrderLedgerPort() { return createPort(); }

/** Isolated tests only. No production path or Python source override. */
export function createEnhancedOrderLedgerFixturePort(ledgerPath) {
  if (process.env.NODE_TEST_CONTEXT === undefined || !isAbsolute(ledgerPath || '')) {
    throw new Error('fixture_context_required');
  }
  return Object.freeze({ ...createPort(ledgerPath),
    initialize: () => run({ op: 'initialize' }, ledgerPath),
  });
}
