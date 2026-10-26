'use strict';
// Offline injected ports only. No browser, credentials, merchant or payment I/O.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, readFile, writeFile, stat, chmod, rm, symlink } = require('node:fs/promises');
const { createHash } = require('node:crypto');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');
let prepareExactGuestCard, GUEST_CARD_URL, GUEST_CARD_SELECTOR, GUEST_CARD_FENCE,
  validateGuestCardInspection, revalidateGuestCardClick, guestCardScopeDigest,
  GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE;
before(async () => ({ prepareExactGuestCard, GUEST_CARD_URL, GUEST_CARD_SELECTOR, GUEST_CARD_FENCE,
  validateGuestCardInspection, revalidateGuestCardClick, guestCardScopeDigest,
  GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE } =
  await import('../scripts/discogs_guest_card_prepare.mjs')));
const NOW = 1_800_000_000_000;
const UUID = '123e4567-e89b-42d3-a456-426614174000';
const OTHER = '123e4567-e89b-42d3-a456-426614174001';
const scope = () => ({ intent_id: UUID, approval_ref: UUID, release_id: 3422100,
  listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD',
  max_total: '5.05', rules_revision: 2, quote_fingerprint: 'a'.repeat(64),
  destination_ref: UUID, terms_fingerprint: 'b'.repeat(64),
  issued_at: NOW / 1000, expires_at: NOW / 1000 + 120 });

function evidence() {
  return { source: 'trusted_current_checkout', release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, item_count: 1,
    seller_count: 1, rules_revision: 2, rules_current: true, criteria_satisfied: true,
    already_owned: false, existing_order: false, uncertain_prior_action: false,
    duplicate_guard_verified: true, approval_ref: UUID, authorization_current: true,
    authorization_revoked: false, currency: 'USD', item: '0.10', shipping: '4.95',
    tax: null, fees: null, total: '5.05', tax_verified: false, fees_verified: false,
    amount_basis: 'explicit_merchant_checkout_total', preparation_quote_verified: true,
    final_payment_quote_verified: false, deferred_total_notice: false,
    shipping_selection_verified: true, quote_fingerprint: 'a'.repeat(64),
    destination_ref: UUID, destination_verified: true, terms_fingerprint: 'b'.repeat(64),
    terms_verified: true, observed_at: NOW / 1000, expires_at: NOW / 1000 + 120 };
}
function inspection() {
  return { schema: 'discogs_guest_card_inspection_v1', url: GUEST_CARD_URL, access: 'normal',
    control: { count: 1, visible: true, enabled: true, role: 'link', funding_source: 'card',
      aria_label: 'Debit or Credit Card', aria_disabled: 'false', control_ref: UUID },
    evidence: evidence() };
}
function fixture(directory, { inspectHook = null, clickHook = null,
  expectedScope = scope(), currentTime = NOW } = {}) {
  const calls = [], signals = [];
  let reads = 0;
  const ports = {
    inspectActualControl: async options => {
      calls.push('inspect'); signals.push(options.signal); reads++;
      return inspectHook ? inspectHook(inspection(), reads, options) : inspection();
    },
    clickExactlyOnce: async (binding, options) => {
      calls.push('click'); signals.push(options.signal);
      assert.equal(options.signal.aborted, false);
      assert.equal(binding.url, GUEST_CARD_URL);
      assert.equal(binding.selector, GUEST_CARD_SELECTOR);
      assert.equal(binding.control_ref, UUID);
      assert.equal(binding.intent_id, expectedScope.intent_id);
      assert.match(binding.scope_digest, /^[0-9a-f]{64}$/u);
      assert.match(binding.inspection_fingerprint, /^[0-9a-f]{64}$/u);
      assert.ok(Object.isFrozen(binding) && Object.isFrozen(binding.expected_scope));
      assert.deepEqual(binding.expected_scope, expectedScope);
      // Actual persistent fence must exist and be fully readable before effect.
      const marker = JSON.parse(await readFile(join(directory, GUEST_CARD_FENCE), 'utf8'));
      assert.equal(marker.state, 'initialization_outcome_unknown');
      assert.equal(marker.listing_id, '1366950910');
      assert.equal((await stat(join(directory, GUEST_CARD_FENCE))).mode & 0o777, 0o600);
      if (!clickHook) assert.equal(revalidateGuestCardClick(binding, inspection(),
        { now: currentTime / 1000 }), true);
      return clickHook ? clickHook(binding, options) :
        { schema: 'discogs_guest_card_click_v1', outcome: 'guest_form_visible' };
    },
  };
  return { ports, calls, signals, options: { enablePreparation: true,
    fenceDirectory: directory, clock: () => currentTime } };
}
async function temporary(fn) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-guest-init-'));
  try { await fn(directory); }
  finally { await rm(directory, { recursive: true, force: true }); }
}
async function absent(directory) {
  await assert.rejects(stat(join(directory, GUEST_CARD_FENCE)), { code: 'ENOENT' });
}

test('disabled by default; no directory, browser or card-data access', async () => {
  const result = await prepareExactGuestCard(null, null);
  assert.equal(result.code, 'guest_card_preparation_disabled');
  assert.equal(result.purchase_allowed, false);
  assert.equal(result.may_initialize_again, false);
});

test('operator-assisted preparation has a bounded ninety-second ceiling', async () => temporary(async directory => {
  let item = fixture(directory);
  assert.equal((await prepareExactGuestCard(item.ports, scope(), { ...item.options, timeoutMs: 90_000 })).form_visible, true);
  item = fixture(directory);
  assert.equal((await prepareExactGuestCard(item.ports, scope(), { ...item.options, timeoutMs: 90_001 })).code,
    'guest_card_scope_or_ports_unverified');
  assert.deepEqual(item.calls, []);
}));

test('one exact inspected control is fenced before click; form is only a handoff', async () => temporary(async directory => {
  const item = fixture(directory);
  const result = await prepareExactGuestCard(item.ports, scope(), item.options);
  assert.deepEqual(item.calls, ['inspect', 'inspect', 'inspect', 'click']);
  assert.equal(result.status, 'handoff_required');
  assert.equal(result.form_visible, true);
  assert.equal(result.order_status, 'unverified');
  assert.equal(result.payment_status, 'unverified');
  assert.equal(result.final_payment_quote_verified, false);
  assert.equal(result.payment_execution, 'not_implemented');
  assert.equal(result.purchase_allowed, false);
  assert.equal(result.may_initialize_again, false);
  assert.ok(item.signals.every(signal => signal.aborted));
  const raw = await readFile(join(directory, GUEST_CARD_FENCE), 'utf8');
  assert.deepEqual(Object.keys(JSON.parse(raw)), ['schema', 'intent_id', 'scope_digest',
    'created_at', 'release_id', 'listing_id', 'state']);
  assert.ok(!raw.includes('destination_ref') && !raw.includes('payment'));
}));

test('exact approval scope rejects retargeting, unknowns and expired bounds', async () => {
  for (const [field, value] of Object.entries({ listing_id: '1366950911', release_id: 1,
  seller: 'OtherSeller', quantity: 2, currency: 'EUR', max_total: '5.06', rules_revision: -1,
  quote_fingerprint: 'unknown', destination_ref: null, terms_fingerprint: null,
  approval_ref: new String(UUID), expires_at: NOW / 1000,
  })) await temporary(async directory => {
    const item = fixture(directory);
    const result = await prepareExactGuestCard(item.ports, { ...scope(), [field]: value }, item.options);
    assert.equal(result.code, 'guest_card_scope_or_ports_unverified', field);
    assert.deepEqual(item.calls, []); await absent(directory);
  });
});

test('identity/rule/quote/destination/terms unknowns fail closed without an attempt', async () => {
  for (const [field, value] of Object.entries({ source: 'rendered_display_only',
  listing_id: '1366950911', release_id: 1, seller: 'OtherSeller', quantity: 2,
  item_count: 2, seller_count: 2, rules_revision: 3, rules_current: false,
  criteria_satisfied: false, already_owned: true, existing_order: true,
  uncertain_prior_action: true, duplicate_guard_verified: null,
  authorization_current: false, authorization_revoked: true, approval_ref: OTHER,
  currency: null, item: '0.11', shipping: '0.00', tax: 'unknown', fees: 'unknown', total: '5.06',
  tax_verified: null, fees_verified: true, amount_basis: 'estimated_total',
  preparation_quote_verified: false, final_payment_quote_verified: true,
  deferred_total_notice: true, shipping_selection_verified: false,
  quote_fingerprint: 'c'.repeat(64), destination_ref: OTHER, destination_verified: null,
  terms_fingerprint: 'c'.repeat(64), terms_verified: false,
  observed_at: NOW / 1000 - 301, expires_at: NOW / 1000,
  })) await temporary(async directory => {
    const item = fixture(directory, { inspectHook: valueNow => ({ ...valueNow,
      evidence: { ...valueNow.evidence, [field]: value } }) });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.code, 'guest_card_preflight_unverified', field);
    assert.ok(!item.calls.includes('click')); await absent(directory);
  });
});

test('explicit merchant aggregate allows preparation without inventing unknown tax or fees', async () => {
  for (const patch of [{}, { tax: '0.00', tax_verified: true },
    { fees: '0.00', fees_verified: true }]) await temporary(async directory => {
    let received;
    const item = fixture(directory, { inspectHook: value => {
      Object.assign(value.evidence, patch); received = value.evidence; return value;
    }, clickHook: async binding => {
      const current = inspection(); Object.assign(current.evidence, patch);
      assert.equal(revalidateGuestCardClick(binding, current, { now: NOW / 1000 }), true);
      return { schema: 'discogs_guest_card_click_v1', outcome: 'guest_form_visible' };
    } });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.status, 'handoff_required');
    assert.equal(result.final_payment_quote_verified, false);
    if (!Object.hasOwn(patch, 'tax')) assert.equal(received.tax, null);
    if (!Object.hasOwn(patch, 'fees')) assert.equal(received.fees, null);
  });
});

test('known extra charges, deferred totals and false zero-tax assertions block preparation', async () => {
  for (const patch of [{ tax: '0.01', tax_verified: true },
    { fees: '0.01', fees_verified: true }, { tax: '-0.01', tax_verified: true },
    { tax: null, tax_verified: true }, { fees: null, fees_verified: true },
    { tax: '0.00', tax_verified: false }, { deferred_total_notice: null },
    { deferred_total_notice: true }, { amount_basis: null }]) await temporary(async directory => {
    const item = fixture(directory, { inspectHook: value => ({ ...value,
      evidence: { ...value.evidence, ...patch } }) });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.code, 'guest_card_preflight_unverified', JSON.stringify(patch));
    assert.ok(!item.calls.includes('click')); await absent(directory);
  });
});

test('only exact normal checkout and one visible enabled card control are accepted', async () => {
  for (const patch of [{ url: 'https://www.discogs.com/marketplace/checkout?x=1' }, { access: 'challenge' },
  { access: 'login_required' }, { access: 'access_restricted' },
  { control: { count: 2 } }, { control: { visible: false } }, { control: { enabled: false } },
  { control: { role: 'button' } }, { control: { funding_source: 'paypal' } },
  { control: { aria_disabled: 'true' } }]) await temporary(async directory => {
    const item = fixture(directory, { inspectHook: value => ({ ...value, ...patch,
      control: { ...value.control, ...patch.control } }) });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.code, 'guest_card_preflight_unverified', JSON.stringify(patch));
    assert.ok(!item.calls.includes('click')); await absent(directory);
  });
});

test('extra fields or accessors cannot become retained card/address data', async () => temporary(async directory => {
  for (const inspectHook of [value => ({ ...value, card_number: 'synthetic-sensitive' }),
    value => ({ ...value, evidence: { ...value.evidence, address: 'synthetic-sensitive' } }),
    value => { Object.defineProperty(value.evidence, 'tax', { get() { throw new Error('do not read'); }, enumerable: true }); return value; }]) {
    const item = fixture(directory, { inspectHook });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.code, 'guest_card_preflight_unverified');
    assert.ok(!JSON.stringify(result).includes('sensitive')); await absent(directory);
  }
}));

test('changed control before fence creates no attempt; after fence remains uncertain', async () => temporary(async directory => {
  let item = fixture(directory, { inspectHook: (value, reads) => ({ ...value,
    control: { ...value.control, control_ref: reads === 2 ? OTHER : UUID } }) });
  let result = await prepareExactGuestCard(item.ports, scope(), item.options);
  assert.equal(result.code, 'guest_card_context_changed'); await absent(directory);
  item = fixture(directory, { inspectHook: (value, reads) => ({ ...value,
    control: { ...value.control, control_ref: reads === 3 ? OTHER : UUID } }) });
  result = await prepareExactGuestCard(item.ports, scope(), item.options);
  assert.equal(result.code, 'guest_card_initialization_uncertain');
  assert.ok(!item.calls.includes('click'));
  assert.ok((await stat(join(directory, GUEST_CARD_FENCE))).isFile());
}));

test('changed current rule/quote/destination after fencing cannot dispatch', async () => {
  for (const patch of [{ rules_revision: 3 }, { quote_fingerprint: 'c'.repeat(64) },
    { destination_ref: OTHER }, { authorization_revoked: true }]) await temporary(async directory => {
    const item = fixture(directory, { inspectHook: (value, reads) => reads < 3 ? value :
      { ...value, evidence: { ...value.evidence, ...patch } } });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(result.code, 'guest_card_initialization_uncertain');
    assert.ok(!item.calls.includes('click'));
  });
});

test('same control with changed quote or revoked approval inside actual port cannot dispatch', async () => {
  for (const patch of [{ total: '5.06' }, { quote_fingerprint: 'c'.repeat(64) },
    { authorization_revoked: true }, { destination_verified: false },
    { terms_verified: false }]) await temporary(async directory => {
    let dispatches = 0;
    const item = fixture(directory, { clickHook: async binding => {
      const fresh = inspection(); Object.assign(fresh.evidence, patch);
      // The future real port uses the same guard at its last dispatch boundary.
      if (!revalidateGuestCardClick(binding, fresh, { now: NOW / 1000 })) {
        return { schema: 'discogs_guest_card_click_v1', outcome: 'unknown' };
      }
      dispatches++;
      return { schema: 'discogs_guest_card_click_v1', outcome: 'guest_form_visible' };
    } });
    const result = await prepareExactGuestCard(item.ports, scope(), item.options);
    assert.equal(dispatches, 0, JSON.stringify(patch));
    assert.equal(result.code, 'guest_card_initialization_uncertain');
  });
});

test('pure click revalidation rejects tampered bindings, expiry and ambiguous inspection', async () => temporary(async directory => {
  const item = fixture(directory, { clickHook: async binding => {
    const current = inspection();
    assert.equal(validateGuestCardInspection(current, binding.expected_scope,
      { now: NOW / 1000 }).inspection_fingerprint, binding.inspection_fingerprint);
    assert.equal(revalidateGuestCardClick(Object.freeze({ ...binding,
      inspection_fingerprint: 'c'.repeat(64) }), current, { now: NOW / 1000 }), false);
    assert.equal(revalidateGuestCardClick(Object.freeze({ ...binding,
      expected_scope: Object.freeze({ ...binding.expected_scope, intent_id: OTHER }) }),
    current, { now: NOW / 1000 }), false);
    assert.equal(revalidateGuestCardClick(binding, current, { now: NOW / 1000 + 121 }), false);
    current.control.count = 2;
    assert.equal(revalidateGuestCardClick(binding, current, { now: NOW / 1000 }), false);
    return { schema: 'discogs_guest_card_click_v1', outcome: 'guest_form_visible' };
  } });
  assert.equal((await prepareExactGuestCard(item.ports, scope(), item.options)).status, 'handoff_required');
}));

test('a caller cannot retarget immutable scope during awaits', async () => temporary(async directory => {
  const mutable = scope();
  const item = fixture(directory, { inspectHook: value => { mutable.listing_id = '999'; return value; } });
  const result = await prepareExactGuestCard(item.ports, mutable, item.options);
  assert.equal(result.status, 'handoff_required');
  const marker = JSON.parse(await readFile(join(directory, GUEST_CARD_FENCE), 'utf8'));
  assert.equal(marker.listing_id, '1366950910');
}));

test('two competing workers and changed intent IDs share one permanent slot', async () => temporary(async directory => {
  const a = fixture(directory), b = fixture(directory);
  const results = await Promise.all([prepareExactGuestCard(a.ports, scope(), a.options),
    prepareExactGuestCard(b.ports, scope(), b.options)]);
  assert.equal(results.filter(value => value.status === 'handoff_required').length, 1);
  assert.equal([...a.calls, ...b.calls].filter(value => value === 'click').length, 1);
  const fresh = fixture(directory);
  const result = await prepareExactGuestCard(fresh.ports, { ...scope(), intent_id: OTHER }, fresh.options);
  assert.equal(result.code, 'guest_card_initialization_already_fenced');
  assert.deepEqual(fresh.calls, []);
}));

for (const kind of ['throw', 'unknown', 'timeout', 'disconnect']) {
  test(`${kind} after possible effect is fenced and never retried`, async () => temporary(async directory => {
    const controller = new AbortController();
    const item = fixture(directory, { clickHook: async (_binding, options) => {
      if (kind === 'throw') throw new Error('private-provider-details');
      if (kind === 'unknown') return { schema: 'discogs_guest_card_click_v1', outcome: 'unknown' };
      if (kind === 'disconnect') controller.abort();
      await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
      return { schema: 'discogs_guest_card_click_v1', outcome: 'guest_form_visible' };
    } });
    const result = await prepareExactGuestCard(item.ports, scope(), { ...item.options,
      signal: controller.signal, timeoutMs: 100 });
    assert.equal(result.code, 'guest_card_initialization_uncertain');
    assert.ok(!JSON.stringify(result).includes('private-provider'));
    const fresh = fixture(directory);
    assert.equal((await prepareExactGuestCard(fresh.ports, scope(), fresh.options)).code,
      'guest_card_initialization_already_fenced');
    assert.deepEqual(fresh.calls, []);
  }));
}

test('cancelled preflight cannot dispatch late when its read eventually returns', async () => temporary(async directory => {
  let finish;
  const wait = new Promise(resolve => { finish = resolve; });
  const item = fixture(directory, { inspectHook: async value => { await wait; return value; } });
  const result = await prepareExactGuestCard(item.ports, scope(), { ...item.options, timeoutMs: 30 });
  assert.equal(result.code, 'guest_card_preflight_unverified');
  finish(); await new Promise(resolve => setImmediate(resolve));
  assert.ok(!item.calls.includes('click')); await absent(directory);
}));

test('quote expiry and pre-dispatch cancellation remain checked after fence', async () => temporary(async directory => {
  let now = NOW;
  const item = fixture(directory, { inspectHook: (value, reads) => {
    value.evidence.expires_at = NOW / 1000 + 1;
    if (reads === 3) now = NOW + 1000;
    return value;
  } });
  const result = await prepareExactGuestCard(item.ports, scope(), { ...item.options, clock: () => now });
  assert.equal(result.code, 'guest_card_initialization_uncertain');
  assert.ok(!item.calls.includes('click'));
}));

test('unsafe directory or symlink never creates a fence', async () => temporary(async directory => {
  await chmod(directory, 0o755);
  let item = fixture(directory);
  assert.equal((await prepareExactGuestCard(item.ports, scope(), item.options)).code,
    'guest_card_preflight_unverified');
  await chmod(directory, 0o700);
  const alias = directory + '-alias';
  await symlink(directory, alias);
  try {
    item = fixture(alias);
    assert.equal((await prepareExactGuestCard(item.ports, scope(), item.options)).code,
      'guest_card_preflight_unverified');
  } finally { await rm(alias); }
  await absent(directory);
}));

test('real process crash after durable fence is blocked across process restart', async () => temporary(async directory => {
  const moduleUrl = pathToFileURL(resolve('scripts/discogs_guest_card_prepare.mjs')).href;
  const source = `import {prepareExactGuestCard} from ${JSON.stringify(moduleUrl)};
    const scope=${JSON.stringify(scope())}; const value=${JSON.stringify(inspection())}; let reads=0;
    await prepareExactGuestCard({inspectActualControl:async()=>{if(++reads===3)process.exit(42);return value;},
      clickExactlyOnce:async()=>{process.exit(99);}},scope,
      {enablePreparation:true,fenceDirectory:${JSON.stringify(directory)},clock:()=>${NOW}});`;
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', source], { timeout: 5000 });
  assert.equal(child.status, 42, child.stderr.toString());
  const item = fixture(directory);
  const result = await prepareExactGuestCard(item.ports, scope(), item.options);
  assert.equal(result.code, 'guest_card_initialization_already_fenced');
  assert.deepEqual(item.calls, []);
}));

async function recoveryFixture(directory) {
  const initial = fixture(directory, { clickHook: async () =>
    ({ schema: 'discogs_guest_card_click_v1', outcome: 'unknown' }) });
  assert.equal((await prepareExactGuestCard(initial.ports, scope(), initial.options)).code,
    'guest_card_initialization_uncertain');
  const originalBytes = await readFile(join(directory, GUEST_CARD_FENCE));
  const old = JSON.parse(originalBytes.toString('utf8'));
  const retryScope = { ...scope(), intent_id: OTHER,
    issued_at: NOW / 1000 + 10, expires_at: NOW / 1000 + 130 };
  const review = { schema: 'discogs_guest_card_recovery_review_v1',
    source: 'operator_reviewed_normal_discogs_ui', authorization_ref: UUID,
    original_fence_sha256: createHash('sha256').update(originalBytes).digest('hex'),
    original_intent_id: old.intent_id, original_scope_digest: old.scope_digest,
    retry_intent_id: retryScope.intent_id,
    retry_scope_digest: guestCardScopeDigest(retryScope, { now: NOW / 1000 + 20 }),
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1,
    currency: 'USD', max_total: '5.05', terminal_error_code: 'SESSION_EXPIRED',
    guest_form_opened: false, card_data_entered: false, approval_invoked: false,
    capture_invoked: false, failure_observed_at: NOW / 1000 + 1,
    order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50',
    order_history_archived: 'All', order_history_status: 'All', order_history_count: 0,
    order_history_complete: true, order_history_observed_at: NOW / 1000 + 2,
    reviewed_at: NOW / 1000 + 3, expires_at: NOW / 1000 + 100, max_additional_attempts: 1 };
  const make = options => fixture(directory, { expectedScope: retryScope,
    currentTime: NOW + 20_000, ...options });
  return { originalBytes, retryScope, review, make };
}

test('explicit one-use recovery preserves original and fixed immutable audit records', async () => temporary(async directory => {
  const setup = await recoveryFixture(directory);
  const noReview = setup.make();
  assert.equal((await prepareExactGuestCard(noReview.ports, setup.retryScope,
    noReview.options)).code, 'guest_card_initialization_already_fenced');
  assert.deepEqual(noReview.calls, []);
  const item = setup.make();
  const result = await prepareExactGuestCard(item.ports, setup.retryScope,
    { ...item.options, recoveryReview: setup.review });
  assert.equal(result.status, 'handoff_required');
  assert.deepEqual(await readFile(join(directory, GUEST_CARD_FENCE)), setup.originalBytes);
  const reviewedBytes = await readFile(join(directory, GUEST_CARD_RECOVERY_REVIEW));
  const spentBytes = await readFile(join(directory, GUEST_CARD_RECOVERY_FENCE));
  const spent = JSON.parse(spentBytes.toString('utf8'));
  assert.equal(spent.attempt, 1);
  assert.equal(spent.original_fence_sha256, setup.review.original_fence_sha256);
  assert.equal(spent.reconciliation_sha256, createHash('sha256').update(reviewedBytes).digest('hex'));
  for (const name of [GUEST_CARD_RECOVERY_REVIEW, GUEST_CARD_RECOVERY_FENCE]) {
    assert.equal((await stat(join(directory, name))).mode & 0o777, 0o600);
  }
  const nextScope = { ...setup.retryScope, intent_id: '123e4567-e89b-42d3-a456-426614174002' };
  const newReview = { ...setup.review, retry_intent_id: nextScope.intent_id,
    retry_scope_digest: guestCardScopeDigest(nextScope, { now: NOW / 1000 + 20 }) };
  const next = fixture(directory, { expectedScope: nextScope, currentTime: NOW + 20_000 });
  assert.equal((await prepareExactGuestCard(next.ports, nextScope,
    { ...next.options, recoveryReview: newReview })).code, 'guest_card_recovery_already_fenced');
  assert.deepEqual(next.calls, []);
  assert.deepEqual(await readFile(join(directory, GUEST_CARD_RECOVERY_REVIEW)), reviewedBytes);
  assert.deepEqual(await readFile(join(directory, GUEST_CARD_RECOVERY_FENCE)), spentBytes);
}));

test('recovery rejects incomplete, stale, mismatched or potentially committed evidence', async () => {
  for (const patch of [{ original_fence_sha256: 'c'.repeat(64) },
    { authorization_ref: OTHER },
    { original_intent_id: OTHER }, { retry_scope_digest: 'c'.repeat(64) },
    { terminal_error_code: 'UNKNOWN' }, { guest_form_opened: true },
    { card_data_entered: true }, { approval_invoked: true }, { capture_invoked: true },
    { order_history_archived: 'Not Archived' }, { order_history_status: 'Invoice Sent' },
    { order_history_count: 1 }, { order_history_complete: false },
    { order_history_observed_at: NOW / 1000 - 301 }, { expires_at: NOW / 1000 + 19 },
    { max_additional_attempts: 2 }, { listing_id: '999' }]) await temporary(async directory => {
    const setup = await recoveryFixture(directory), item = setup.make();
    const result = await prepareExactGuestCard(item.ports, setup.retryScope,
      { ...item.options, recoveryReview: { ...setup.review, ...patch } });
    assert.equal(result.code, 'guest_card_recovery_unverified', JSON.stringify(patch));
    assert.deepEqual(item.calls, []);
    assert.deepEqual(await readFile(join(directory, GUEST_CARD_FENCE)), setup.originalBytes);
    await assert.rejects(stat(join(directory, GUEST_CARD_RECOVERY_REVIEW)), { code: 'ENOENT' });
  });
});

test('recovery history age at 299 seconds bounds operator wait and prevents dispatch after expiry', async () => temporary(async directory => {
  const setup = await recoveryFixture(directory);
  const initial = NOW + 301_000;
  let now = initial;
  const retryScope = { ...setup.retryScope, issued_at: initial / 1000 - 1,
    expires_at: initial / 1000 + 120 };
  const review = { ...setup.review,
    retry_scope_digest: guestCardScopeDigest(retryScope, { now: initial / 1000 }),
    reviewed_at: initial / 1000, expires_at: initial / 1000 + 100 };
  assert.equal(initial / 1000 - review.order_history_observed_at, 299);
  const historyDeadline = (review.order_history_observed_at + 300) * 1000;
  const item = fixture(directory, { expectedScope: retryScope, currentTime: initial,
    inspectHook: async (value, reads, options) => {
      assert.equal(options.deadline, historyDeadline);
      value.evidence.observed_at = initial / 1000;
      value.evidence.expires_at = initial / 1000 + 120;
      if (reads === 3) {
        await new Promise(resolve => setImmediate(resolve));
        now += 1001; // Operator wait crosses the independent reconciliation deadline.
      }
      return value;
    } });
  const result = await prepareExactGuestCard(item.ports, retryScope,
    { ...item.options, clock: () => now, recoveryReview: review });
  assert.equal(result.code, 'guest_card_initialization_uncertain');
  assert.ok(!item.calls.includes('click'));
  assert.ok((await stat(join(directory, GUEST_CARD_RECOVERY_FENCE))).isFile());
}));

test('competing recovery workers cannot spend more than the fixed single retry', async () => temporary(async directory => {
  const setup = await recoveryFixture(directory), a = setup.make(), b = setup.make();
  const results = await Promise.all([a, b].map(item => prepareExactGuestCard(item.ports,
    setup.retryScope, { ...item.options, recoveryReview: setup.review })));
  assert.equal(results.filter(result => result.status === 'handoff_required').length, 1);
  assert.equal([...a.calls, ...b.calls].filter(call => call === 'click').length, 1);
  assert.deepEqual(await readFile(join(directory, GUEST_CARD_FENCE)), setup.originalBytes);
}));

test('recovery rechecks original and reconciliation hashes after final inspection', async () => {
  for (const name of [GUEST_CARD_FENCE, GUEST_CARD_RECOVERY_REVIEW]) await temporary(async directory => {
    const setup = await recoveryFixture(directory);
    const item = setup.make({ inspectHook: async (value, reads) => {
      if (reads === 3) await writeFile(join(directory, name), '{}\n');
      return value;
    } });
    const result = await prepareExactGuestCard(item.ports, setup.retryScope,
      { ...item.options, recoveryReview: setup.review });
    assert.equal(result.code, 'guest_card_initialization_uncertain');
    assert.ok(!item.calls.includes('click'));
    assert.ok((await stat(join(directory, GUEST_CARD_RECOVERY_FENCE))).isFile());
  });
});

test('recovery crash or disconnected outcome never enables another attempt after restart', async () => {
  for (const crash of [true, false]) await temporary(async directory => {
    const setup = await recoveryFixture(directory);
    if (crash) {
      const moduleUrl = pathToFileURL(resolve('scripts/discogs_guest_card_prepare.mjs')).href;
      const source = `import {prepareExactGuestCard} from ${JSON.stringify(moduleUrl)};
        const scope=${JSON.stringify(setup.retryScope)}; const value=${JSON.stringify(inspection())};let reads=0;
        await prepareExactGuestCard({inspectActualControl:async()=>{if(++reads===3)process.exit(42);return value;},
          clickExactlyOnce:async()=>{process.exit(99);}},scope,{enablePreparation:true,
          fenceDirectory:${JSON.stringify(directory)},recoveryReview:${JSON.stringify(setup.review)},clock:()=>${NOW + 20_000}});`;
      const child = spawnSync(process.execPath, ['--input-type=module', '-e', source], { timeout: 5000 });
      assert.equal(child.status, 42, child.stderr.toString());
    } else {
      const controller = new AbortController();
      const item = setup.make({ clickHook: async () => { controller.abort(); throw new Error('disconnected'); } });
      assert.equal((await prepareExactGuestCard(item.ports, setup.retryScope,
        { ...item.options, recoveryReview: setup.review, signal: controller.signal })).code,
      'guest_card_initialization_uncertain');
    }
    const fresh = setup.make();
    assert.equal((await prepareExactGuestCard(fresh.ports, setup.retryScope,
      { ...fresh.options, recoveryReview: setup.review })).code, 'guest_card_recovery_already_fenced');
    assert.deepEqual(fresh.calls, []);
    assert.deepEqual(await readFile(join(directory, GUEST_CARD_FENCE)), setup.originalBytes);
  });
});
