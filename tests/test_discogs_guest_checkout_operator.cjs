'use strict';
// Real CLI processes/files/stdio; every observed website value below is synthetic.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, writeFile, readFile, readdir, stat, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { pathToFileURL } = require('node:url');
const { createHash, randomUUID } = require('node:crypto');
const { spawn, spawnSync } = require('node:child_process');
const { createInterface } = require('node:readline');
let helper, operator;
before(async () => { helper = await import('../scripts/discogs_guest_card_prepare.mjs');
  operator = await import('../scripts/discogs_guest_checkout_operator.mjs'); });
const encoded = value => JSON.stringify(value) + '\n';
const hash = value => createHash('sha256').update(value).digest('hex');
const moduleURL = pathToFileURL(resolve('scripts/discogs_guest_checkout_operator.mjs')).href;

async function stateFixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-cli-'));
  const original = { schema: 'discogs_guest_card_init_fence_v1', intent_id: randomUUID(),
    scope_digest: 'a'.repeat(64), release_id: 3422100, listing_id: '1366950910', created_at: Date.now() / 1000 - 30 };
  const saved = {};
  const put = async (name, value) => {
    const bytes = encoded(value); saved[name] = bytes;
    await writeFile(join(directory, name), bytes, { flag: 'wx', mode: 0o600 }); return hash(bytes);
  };
  const a = await put(helper.GUEST_CARD_FENCE, original);
  const recovery = { schema: 'discogs_guest_card_recovery_review_v1', original_fence_sha256: a,
    original_intent_id: original.intent_id, original_scope_digest: original.scope_digest,
    retry_intent_id: randomUUID(), retry_scope_digest: 'b'.repeat(64) };
  const b = await put(helper.GUEST_CARD_RECOVERY_REVIEW, recovery);
  const retry = { schema: 'discogs_guest_card_recovery_attempt_v1', attempt: 1,
    original_fence_sha256: a, reconciliation_sha256: b, intent_id: recovery.retry_intent_id,
    scope_digest: recovery.retry_scope_digest, created_at: original.created_at + 1 };
  const c = await put(helper.GUEST_CARD_RECOVERY_FENCE, retry);
  const navigation = { schema: 'discogs_guest_card_navigation_reopen_review_v1',
    original_fence_sha256: a, original_intent_id: original.intent_id,
    original_scope_digest: original.scope_digest, recovery_review_sha256: b, recovery_fence_sha256: c,
    recovery_intent_id: retry.intent_id, recovery_scope_digest: retry.scope_digest,
    reopen_intent_id: randomUUID(), reopen_scope_digest: 'c'.repeat(64) };
  const d = await put(helper.GUEST_CARD_NAVIGATION_REOPEN_REVIEW, navigation);
  await put(helper.GUEST_CARD_NAVIGATION_REOPEN_FENCE, {
    schema: 'discogs_guest_card_navigation_reopen_attempt_v1', attempt: 1,
    original_fence_sha256: a, recovery_review_sha256: b, recovery_fence_sha256: c,
    reconciliation_sha256: d, intent_id: navigation.reopen_intent_id,
    scope_digest: navigation.reopen_scope_digest, created_at: retry.created_at + 1 });
  try { await run(directory, saved); }
  finally { await rm(directory, { recursive: true, force: true }); }
}
function client(directory, { advanceMs = 0 } = {}) {
  const source = `import {runGuestCheckoutOperator} from ${JSON.stringify(moduleURL)};
    const actualNow = Date.now; let offset = 0;
    if (${advanceMs}) { Date.now = () => actualNow() + offset;
      process.on('SIGUSR1', () => { offset += ${advanceMs}; }); }
    process.exit(await runGuestCheckoutOperator({directory:process.argv[1]}));`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', source, directory],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  child.stdin.on('error', () => {});
  const reader = createInterface({ input: child.stdout }), iterator = reader[Symbol.asyncIterator]();
  const output = [], errors = [];
  child.stdout.on('data', value => output.push(value.toString()));
  child.stderr.on('data', value => errors.push(value.toString()));
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  return { child, output, errors, exited, send: value => child.stdin.write(encoded(value)),
    next: async (milliseconds = 5000) => {
      let timer;
      try {
        const line = await Promise.race([iterator.next(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('CLI response deadline')), milliseconds);
        })]);
        assert.equal(line.done, false, errors.join('')); return JSON.parse(line.value);
      } finally { clearTimeout(timer); }
    },
    close: async () => { if (child.exitCode === null && !child.killed) child.kill('SIGKILL'); await exited; reader.close(); },
  };
}
function startFor(waiting, { ttl = 45, readyRef = randomUUID(), intent = randomUUID() } = {}) {
  const now = Date.now() / 1000;
  const scope = { intent_id: intent, approval_ref: randomUUID(), release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    rules_revision: 0, quote_fingerprint: 'd'.repeat(64), destination_ref: randomUUID(),
    terms_fingerprint: 'e'.repeat(64), issued_at: now, expires_at: now + 300 };
  return { type: 'start', ready_ref: readyRef, scope, expected_latest_hash: waiting.latest_hash,
    userReady: { source: 'explicit_current_user_ready', ready: true,
      scope_digest: helper.guestCardScopeDigest(scope), observed_at: now, expires_at: now + ttl },
    priorFailure: { source: 'operator_reviewed_normal_discogs_ui', last_error_code: 'SESSION_EXPIRED',
      release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1,
      currency: 'USD', max_total: '5.05', card_data_entered: false, pay_invoked: false,
      approval_invoked: false, capture_invoked: false, failure_observed_at: now - 0.001,
      order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50',
      order_history_archived: 'All', order_history_status: 'All', order_history_count: 0,
      order_history_complete: true, order_history_observed_at: now, reviewed_at: now, expires_at: now + 300 } };
}
function page(start, cart, refs) {
  const s = start.scope;
  return { schema: 'discogs_guest_session_page_v1', source: 'operator_verified_current_screen',
    url: cart ? 'https://www.discogs.com/sell/cart/' : helper.GUEST_CARD_URL,
    document_ref: cart ? refs.cart : refs.checkout, access: 'normal', history_restore: false,
    resubmission_prompt: false, extra_agreement_required: false,
    selected_shipping_ref: refs.shipping, selected_shipping_label: 'Economy - Media Mail',
    go_to_checkout: cart ? { action_kind: 'normal_go_to_checkout', label: 'Go to Checkout',
      count: 1, visible: true, enabled: true, control_ref: refs.go } : null,
    evidence: { source: 'trusted_current_checkout', release_id: s.release_id, listing_id: s.listing_id,
      seller: s.seller, quantity: 1, item_count: 1, seller_count: 1, rules_revision: 0, rules_current: true,
      criteria_satisfied: true, already_owned: false, existing_order: false, uncertain_prior_action: false,
      duplicate_guard_verified: true, approval_ref: s.approval_ref, authorization_current: true,
      authorization_revoked: false, currency: 'USD', item: '0.10', shipping: '4.95', tax: null,
      tax_verified: false, fees: null, fees_verified: false, total: '5.05',
      amount_basis: 'explicit_merchant_checkout_total', preparation_quote_verified: true,
      final_payment_quote_verified: false, deferred_total_notice: false, shipping_selection_verified: true,
      quote_fingerprint: s.quote_fingerprint, destination_ref: s.destination_ref, destination_verified: true,
      terms_fingerprint: s.terms_fingerprint, terms_verified: true,
      observed_at: Date.now() / 1000, expires_at: s.expires_at } };
}
function ack(request, payload) {
  const keys = ['intent_id', 'ready_ref', 'scope_digest', 'request_id', 'stage', 'deadline', 'from_document_ref'];
  return { type: 'operator_ack', ...Object.fromEntries(keys.map(key => [key, request[key]])), ...payload };
}
function refs() { return { cart: randomUUID(), checkout: randomUUID(), shipping: randomUUID(), go: randomUUID(), card: randomUUID() }; }
async function throughCheckout(cli, start, values = refs(), patch = {}) {
  const templates = operator.prepareOperatorAckTemplates(start, values);
  const proof = () => ({ observed_at: Date.now() / 1000, exact_scope: true, quote: true,
    destination: true, terms: true, no_card: true, normal: true });
  const open = await cli.next(); assert.equal(open.stage, 'open_cart');
  const cartAck = operator.buildOperatorAck(open, templates, proof());
  if (patch.cart) cartAck.cart = patch.cart(cartAck.cart);
  cli.send(cartAck);
  const go = await cli.next(); if (go.type !== 'operator_action') return go;
  assert.equal(go.stage, 'go_to_checkout');
  assert.ok(go.deadline <= start.scope.expires_at * 1000 && go.deadline <= Date.now() + 120_000);
  const checkoutAck = operator.buildOperatorAck(go, templates, proof());
  if (patch.transition) checkoutAck.transition = patch.transition(checkoutAck.transition);
  if (patch.checkout) checkoutAck.checkout = patch.checkout(checkoutAck.checkout);
  cli.send(checkoutAck);
  const guest = await cli.next();
  if (guest.type === 'operator_action') { assert.equal(guest.stage, 'open_guest_card');
    assert.ok(guest.deadline <= (checkoutAck.checkout.evidence.observed_at + 20) * 1000); }
  return guest;
}
async function audits(directory) {
  const names = (await readdir(directory)).filter(name => /^guest-checkout-\d+\.json$/u.test(name)).sort();
  return Promise.all(names.map(async name => JSON.parse(await readFile(join(directory, name), 'utf8'))));
}

test('real CLI emits exact normal stages and durable linked attempt before one guest instruction', async () => stateFixture(async (directory, saved) => {
  const cli = client(directory);
  try {
    const waiting = await cli.next(); assert.equal(waiting.type, 'waiting_for_user_ready');
    assert.equal((await audits(directory)).length, 0);
    const start = startFor(waiting, { ttl: 300 }); cli.send(start);
    const values = refs(), templates = operator.prepareOperatorAckTemplates(start, values);
    const guest = await throughCheckout(cli, start, values); assert.equal(guest.stage, 'open_guest_card');
    assert.ok(guest.deadline <= Date.now() + 20_000);
    const before = await audits(directory); assert.deepEqual(before.map(x => x.kind), ['ready', 'review', 'attempt']);
    assert.equal(before[1].payload.provenance.observation_basis, 'operator_verified_current_screen');
    assert.equal(before[2].payload.deadline, guest.deadline);
    assert.equal(before[2].payload.control_ref, guest.control_ref);
    cli.send(operator.buildOperatorAck(guest, templates, { observed_at: Date.now() / 1000,
      exact_scope: true, quote: true, destination: true, terms: true, no_card: true, normal: true,
      outcome: 'guest_form_visible' }));
    assert.equal((await cli.next()).type, 'finished'); assert.equal((await cli.exited).code, 0);
    const records = await audits(directory); assert.equal(records[3].payload.outcome, 'guest_form_visible');
    for (const [name, bytes] of Object.entries(saved)) assert.equal(await readFile(join(directory, name), 'utf8'), bytes);
    for (const name of (await readdir(directory)).filter(name => /^guest-checkout-\d+\.json$/u.test(name))) {
      assert.equal((await stat(join(directory, name))).mode & 0o777, 0o600);
    }
    assert.equal(cli.errors.join(''), '');
  } finally { await cli.close(); }
}));

test('Node retains flock after Python exits; concurrent processes blocked and crash releases OS lock', async () => stateFixture(async directory => {
  const first = client(directory); let second, third;
  try {
    assert.equal((await first.next()).type, 'waiting_for_user_ready');
    const probe = spawnSync('python3', ['-c',
      'import fcntl,sys; f=open(sys.argv[1],"a");\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(23)',
      join(directory, 'guest-checkout-operator.lock')]);
    assert.equal(probe.status, 23);
    second = client(directory); assert.equal((await second.next()).type, 'stopped');
    assert.equal((await audits(directory)).length, 0);
    await first.close();
    third = client(directory); assert.equal((await third.next()).type, 'waiting_for_user_ready');
  } finally { await first.close(); if (second) await second.close(); if (third) await third.close(); }
}));

test('stale readiness, Back provenance, changed total and revoked acknowledgements never initialize', async () => {
  for (const mode of ['stale', 'back', 'price', 'cancel']) await stateFixture(async directory => {
    const cli = client(directory);
    try {
      const start = startFor(await cli.next());
      if (mode === 'stale') start.userReady.expires_at = Date.now() / 1000 - 1;
      cli.send(start);
      if (mode === 'stale') assert.equal((await cli.next()).type, 'stopped');
      else if (mode === 'cancel') { const request = await cli.next(); cli.send({ ...ack(request, {}), type: 'cancel' }); assert.equal((await cli.next()).type, 'stopped'); }
      else assert.equal((await throughCheckout(cli, start, refs(), mode === 'back' ?
        { transition: value => ({ ...value, action: 'history_back' }) } :
        { cart: value => ({ ...value, evidence: { ...value.evidence, total: '5.06' } }) })).type, 'stopped');
      await cli.exited;
      assert.ok(!cli.output.join('').includes('open_guest_card'));
      assert.ok(!(await audits(directory)).some(record => record.kind === 'attempt'));
    } finally { await cli.close(); }
  });
});

test('ready ID is consumed before first UI instruction and cannot replay across process restart', async () => stateFixture(async directory => {
  const a = client(directory); let b;
  try {
    const start = startFor(await a.next()); a.send(start);
    assert.equal((await a.next()).stage, 'open_cart');
    assert.equal((await audits(directory))[0].payload.ready_ref, start.ready_ref);
    await a.close(); b = client(directory);
    const replay = startFor(await b.next(), { readyRef: start.ready_ref, intent: start.scope.intent_id });
    b.send(replay); assert.equal((await b.next()).type, 'stopped');
    assert.ok(!b.output.join('').includes('operator_action'));
  } finally { await a.close(); if (b) await b.close(); }
}));

test('crash after guest fence remains pending even with new readiness and reconciliation', async () => stateFixture(async directory => {
  const a = client(directory); let b, c;
  try {
    const start = startFor(await a.next()); a.send(start);
    assert.equal((await throughCheckout(a, start)).stage, 'open_guest_card');
    await a.close(); assert.deepEqual((await audits(directory)).map(x => x.kind), ['ready', 'review', 'attempt']);
    b = client(directory); const newStart = startFor(await b.next());
    newStart.priorFailure = start.priorFailure; b.send(newStart);
    assert.equal((await b.next()).type, 'stopped'); await b.exited;
    await new Promise(resolve => setTimeout(resolve, 5));
    c = client(directory); const reconciled = startFor(await c.next()); c.send(reconciled);
    assert.equal((await c.next()).type, 'stopped');
    assert.ok(!c.output.join('').includes('operator_action'));
  } finally { await a.close(); if (b) await b.close(); if (c) await c.close(); }
}));

test('late ACK or disconnect leaves unknown outcome and emits no late UI instruction', async () => {
  for (const disconnect of [false, true]) await stateFixture(async directory => {
    const cli = client(directory);
    try {
      const start = startFor(await cli.next(), { ttl: 0.6 }); cli.send(start);
      const guest = await throughCheckout(cli, start); assert.equal(guest.stage, 'open_guest_card');
      if (disconnect) cli.child.stdin.end();
      else {
        await new Promise(resolve => setTimeout(resolve, Math.max(1, guest.deadline - Date.now() + 20)));
        cli.send(ack(guest, { outcome: 'guest_form_visible', observed_at: Date.now() / 1000 }));
      }
      assert.equal((await cli.next()).type, 'stopped'); await cli.exited;
      const records = await audits(directory); assert.equal(records.at(-1).payload.outcome, 'unknown');
      assert.equal(cli.output.join('').split('"stage":"open_guest_card"').length - 1, 1);
    } finally { await cli.close(); }
  });
});

test('missing, partial or modified audit/legacy chains fail closed without any UI request', async () => {
  for (const mode of ['partial', 'gap', 'legacy']) await stateFixture(async directory => {
    if (mode === 'legacy') await writeFile(join(directory, helper.GUEST_CARD_FENCE), '{}\n');
    else await writeFile(join(directory, mode === 'gap' ? 'guest-checkout-000002.json' : 'guest-checkout-000001.json'),
      mode === 'partial' ? '{' : '{}\n', { mode: 0o600 });
    const cli = client(directory);
    try { assert.equal((await cli.next()).type, 'stopped'); assert.ok(!cli.output.join('').includes('operator_action')); }
    finally { await cli.close(); }
  });
});

test('strict bindings and metadata schemas reject tampering or secret-like input without logging it', async () => {
  for (const patch of [{ card_number: 'SYNTHETIC_NEVER_LOG_THIS' }, { request_id: randomUUID() },
    { ready_ref: randomUUID() }, { stage: 'open_guest_card' }, { deadline: 1 }]) await stateFixture(async directory => {
    const cli = client(directory);
    try {
      const start = startFor(await cli.next()); cli.send(start); const request = await cli.next();
      cli.send({ ...ack(request, { navigation: { action: 'visible_cart_control', document_ref: randomUUID() },
        cart: page(start, true, refs()) }), ...patch });
      assert.equal((await cli.next()).type, 'stopped'); await cli.exited;
      assert.ok(!cli.output.join('').includes('SYNTHETIC_NEVER_LOG_THIS'));
      assert.ok(!cli.errors.join('').includes('SYNTHETIC_NEVER_LOG_THIS'));
      assert.ok(!JSON.stringify(await audits(directory)).includes('SYNTHETIC_NEVER_LOG_THIS'));
    } finally { await cli.close(); }
  });
});

test('post-ready mutation of current ready or earlier operator review stops before another SDK instruction', async () => {
  for (const kind of ['ready', 'review']) await stateFixture(async directory => {
    let first, cli;
    try {
      if (kind === 'review') {
        first = client(directory); const initial = startFor(await first.next()); first.send(initial);
        const guest = await throughCheckout(first, initial);
        first.send(ack(guest, { outcome: 'SESSION_EXPIRED', observed_at: Date.now() / 1000 }));
        assert.equal((await first.next()).type, 'finished'); await first.exited;
        await new Promise(resolve => setTimeout(resolve, 5));
      }
      cli = client(directory); const start = startFor(await cli.next()); cli.send(start);
      const request = await cli.next(); assert.equal(request.stage, 'open_cart');
      const name = kind === 'ready' ? 'guest-checkout-000001.json' : 'guest-checkout-000002.json';
      const value = JSON.parse(await readFile(join(directory, name), 'utf8'));
      value.payload.synthetic_tamper = true;
      await writeFile(join(directory, name), encoded(value));
      const values = refs();
      cli.send(ack(request, { navigation: { action: 'visible_cart_control', document_ref: values.cart },
        cart: page(start, true, values) }));
      assert.equal((await cli.next()).type, 'stopped'); await cli.exited;
      assert.ok(!cli.output.join('').includes('open_guest_card'));
      assert.ok(!cli.output.join('').includes('go_to_checkout'));
    } finally { if (first) await first.close(); if (cli) await cli.close(); }
  });
});

test('late audit tamper prevents both outcome and catch-unknown append without altering any bytes', async () => {
  for (const invalidAck of [false, true]) await stateFixture(async directory => {
    const cli = client(directory);
    try {
      const start = startFor(await cli.next()); cli.send(start);
      const guest = await throughCheckout(cli, start);
      assert.equal(guest.stage, 'open_guest_card');
      const name = 'guest-checkout-000001.json';
      const value = JSON.parse(await readFile(join(directory, name), 'utf8'));
      value.payload.synthetic_tamper = true;
      await writeFile(join(directory, name), encoded(value));
      const names = (await readdir(directory)).sort();
      const before = await Promise.all(names.map(name => readFile(join(directory, name))));
      cli.send({ ...ack(guest, { outcome: 'guest_form_visible', observed_at: Date.now() / 1000 }),
        ...(invalidAck ? { request_id: randomUUID() } : {}) });
      assert.equal((await cli.next()).type, 'stopped'); await cli.exited;
      assert.deepEqual((await readdir(directory)).sort(), names);
      for (let i = 0; i < names.length; i++) assert.deepEqual(await readFile(join(directory, names[i])), before[i]);
      assert.equal((await audits(directory)).length, 3);
    } finally { await cli.close(); }
  });
});

test('manual CLI rejects DOM claims, mixed bases and unsupported observations before guest dispatch', async () => {
  for (const patch of [
    { cart: value => ({ ...value, source: 'trusted_normal_current_dom' }) },
    { checkout: value => ({ ...value, source: 'trusted_normal_current_dom' }) },
    { cart: value => ({ ...value, source: 'unverified_screen' }) },
  ]) await stateFixture(async directory => {
    const cli = client(directory);
    try {
      const start = startFor(await cli.next()); cli.send(start);
      const stopped = await throughCheckout(cli, start, refs(), patch);
      assert.equal(stopped.type, 'stopped'); await cli.exited;
      assert.ok(!cli.output.join('').includes('open_guest_card'));
      assert.ok(!(await audits(directory)).some(value => value.kind === 'attempt'));
    } finally { await cli.close(); }
  });
});

test('pure ACK templates remain unattested and require current complete matching operator proof', () => {
  const start = startFor({ latest_hash: 'f'.repeat(64) }, { ttl: 300 });
  const templates = operator.prepareOperatorAckTemplates(start, refs());
  assert.equal(templates.cart.evidence.observed_at, null);
  assert.equal(templates.checkout.evidence.observed_at, null);
  const request = { type: 'operator_action', intent_id: start.scope.intent_id, ready_ref: start.ready_ref,
    scope_digest: templates.scope_digest, request_id: randomUUID(), stage: 'open_cart',
    deadline: Date.now() + 120_000, from_document_ref: null };
  const proof = { observed_at: Date.now() / 1000, exact_scope: true, quote: true,
    destination: true, terms: true, no_card: true, normal: true };
  const result = operator.buildOperatorAck(request, templates, proof);
  assert.equal(result.cart.evidence.observed_at, proof.observed_at);
  assert.equal(result.cart.source, 'operator_verified_current_screen');
  assert.equal(result.cart.evidence.final_payment_quote_verified, false);
  assert.equal(result.cart.evidence.tax, null);
  for (const patch of [{ exact_scope: false }, { quote: null }, { destination: false }, { terms: false },
    { no_card: false }, { normal: false }, { observed_at: Date.now() / 1000 - 21 },
    { observed_at: Date.now() / 1000 + 60 }, { card_number: 'SYNTHETIC_NEVER_ACCEPT' }]) {
    assert.throws(() => operator.buildOperatorAck(request, templates, { ...proof, ...patch }));
  }
  for (const patch of [{ ready_ref: randomUUID() }, { scope_digest: 'a'.repeat(64) },
    { deadline: templates.availability_deadline + 1 }, { deadline: Date.now() - 1 },
    { from_document_ref: randomUUID() }, { card_number: 'SYNTHETIC_NEVER_ACCEPT' }]) {
    assert.throws(() => operator.buildOperatorAck({ ...request, ...patch }, templates, proof));
  }
  const changed = JSON.parse(JSON.stringify(templates)); changed.checkout.evidence.total = '5.06';
  assert.throws(() => operator.buildOperatorAck(request, changed, proof));
});

test('injected process clock enforces 120-second stage, 20-second guest quote and 300-second availability', async () => {
  for (const mode of ['stage', 'quote', 'availability']) await stateFixture(async directory => {
    const cli = client(directory, { advanceMs: mode === 'stage' ? 120_001 : mode === 'quote' ? 20_001 : 300_001 });
    try {
      const start = startFor(await cli.next(), { ttl: 300 }); cli.send(start);
      const values = refs(); let request;
      if (mode === 'quote') request = await throughCheckout(cli, start, values);
      else {
        request = await cli.next(); assert.equal(request.stage, 'open_cart');
        assert.ok(request.deadline <= Date.now() + 120_000);
        assert.ok(request.deadline < start.scope.expires_at * 1000);
      }
      cli.child.kill('SIGUSR1'); await new Promise(resolve => setTimeout(resolve, 20));
      cli.send(mode === 'quote' ? ack(request, { outcome: 'guest_form_visible', observed_at: Date.now() / 1000 }) :
        ack(request, { navigation: { action: 'visible_cart_control', document_ref: values.cart },
          cart: page(start, true, values) }));
      assert.equal((await cli.next()).type, 'stopped'); await cli.exited;
      const records = await audits(directory);
      if (mode === 'quote') assert.equal(records.at(-1).payload.outcome, 'unknown');
      else assert.ok(!records.some(value => value.kind === 'attempt'));
    } finally { await cli.close(); }
  });
});

async function currentClaim(directory) {
  const probe = client(directory); let waiting;
  try { waiting = await probe.next(); } finally { await probe.close(); }
  const start = startFor(waiting, { ttl: 300 }), values = refs();
  const templates = operator.prepareOperatorAckTemplates(start, values), observed = Date.now() / 1000;
  return { type: 'claim_current_checkout', ready_ref: start.ready_ref, scope: start.scope,
    userReady: start.userReady, priorFailure: start.priorFailure, expected_latest_hash: start.expected_latest_hash,
    provenance: { source: 'operator_verified_current_screen', navigation: 'normal_cart_to_fresh_checkout',
      scope_digest: templates.scope_digest, cart_document_ref: values.cart, checkout_document_ref: values.checkout,
      cart_observed_at: observed, checkout_observed_at: observed, cart_scope_verified: true,
      go_to_checkout_verified: true, history_restore: false, resubmission_prompt: false, extra_agreement_required: false },
    inspection: { schema: 'discogs_guest_card_inspection_v1', url: helper.GUEST_CARD_URL, access: 'normal',
      control: templates.card_control, evidence: { ...templates.checkout.evidence, observed_at: observed } } };
}
async function oneShot(directory, value, { mode = 'claim', crashBeforePermit = false, inputFailure = false } = {}) {
  const source = `import {runCurrentCheckoutClaim} from ${JSON.stringify(moduleURL)};
    process.exit(await runCurrentCheckoutClaim({directory:process.argv[1],mode:${JSON.stringify(mode)}
      ${crashBeforePermit ? ",output:{write(){process.kill(process.pid,'SIGKILL')}}" : ''}
      ${inputFailure ? ",input:{async *[Symbol.asyncIterator](){throw new Error('SYNTHETIC_NEVER_LOG_THIS')}}" : ''}}));`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', source, directory], { stdio: ['pipe', 'pipe', 'pipe'] });
  let output = '', errors = ''; child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { errors += chunk; }); child.stdin.on('error', () => {});
  const exited = new Promise(resolve => child.on('exit', (code, signal) => resolve({ code, signal })));
  child.stdin.end(encoded(value));
  const result = await exited;
  return { ...result, message: output ? JSON.parse(output.trim()) : null, output, errors };
}
const outcomeFor = (claim, granted, outcome = 'SESSION_EXPIRED') => ({ type: 'record_claim_outcome',
  intent_id: claim.scope.intent_id, ready_ref: claim.ready_ref, scope_digest: claim.provenance.scope_digest,
  claim_sha256: granted.claim_sha256, outcome, observed_at: Date.now() / 1000 });

test('one-shot claim fsyncs exact current checkout permit and outcome accounting is bound and idempotent', async () => stateFixture(async (directory, saved) => {
  const claim = await currentClaim(directory), result = await oneShot(directory, claim);
  assert.equal(result.code, 0); assert.equal(result.message.type, 'checkout_claim_granted');
  assert.equal(result.message.permit.stage, 'open_guest_card');
  assert.ok(result.message.permit.deadline <= (claim.inspection.evidence.observed_at + 20) * 1000);
  const before = await audits(directory); assert.deepEqual(before.map(record => record.kind), ['ready', 'review', 'attempt']);
  assert.deepEqual(before[2].payload.permit, result.message.permit);
  assert.equal(hash(await readFile(join(directory, 'guest-checkout-000003.json'))), result.message.claim_sha256);
  const outcome = outcomeFor(claim, result.message);
  for (const patch of [{ claim_sha256: 'a'.repeat(64) }, { scope_digest: 'b'.repeat(64) }, { intent_id: randomUUID() }]) {
    assert.equal((await oneShot(directory, { ...outcome, ...patch }, { mode: 'outcome' })).message.type, 'claim_stopped');
    assert.equal((await audits(directory)).length, 3);
  }
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome' })).message.duplicate, false);
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome' })).message.duplicate, true);
  assert.equal((await audits(directory)).length, 4);
  for (const [name, bytes] of Object.entries(saved)) assert.equal(await readFile(join(directory, name), 'utf8'), bytes);
}));

test('one-shot concurrent and restarted claims cannot pass a pending or unknown durable attempt', async () => stateFixture(async directory => {
  const first = await currentClaim(directory), second = await currentClaim(directory);
  const results = await Promise.all([oneShot(directory, first), oneShot(directory, second)]);
  assert.equal(results.filter(result => result.message.type === 'checkout_claim_granted').length, 1);
  assert.equal((await audits(directory)).length, 3);
  const grantedIndex = results.findIndex(result => result.message.type === 'checkout_claim_granted');
  const granted = results[grantedIndex].message, claim = [first, second][grantedIndex];
  for (let i = 0; i < 2; i++) {
    const next = await currentClaim(directory);
    assert.equal((await oneShot(directory, next)).message.type, 'claim_stopped');
    assert.equal((await audits(directory)).length, 3);
  }
  assert.equal((await oneShot(directory, outcomeFor(claim, granted, 'unknown'), { mode: 'outcome' })).code, 0);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal((await oneShot(directory, await currentClaim(directory))).message.type, 'claim_stopped');
  assert.equal((await audits(directory)).length, 4);
}));

test('process crash before one-shot permit output leaves a durable pending attempt across restart', async () => stateFixture(async directory => {
  const claim = await currentClaim(directory);
  const result = await oneShot(directory, claim, { crashBeforePermit: true });
  assert.equal(result.signal, 'SIGKILL'); assert.equal(result.output, '');
  assert.equal((await audits(directory)).length, 3);
  assert.equal((await oneShot(directory, await currentClaim(directory))).message.type, 'claim_stopped');
  assert.equal((await audits(directory)).length, 3);
}));

test('one-shot claims reject stale, changed, revoked, mismatched or secret-like evidence without writes', async () => {
  for (const alter of [
    value => { value.inspection.evidence.total = '5.06'; },
    value => { value.inspection.evidence.currency = null; },
    value => { value.inspection.evidence.release_id = 1; },
    value => { value.inspection.evidence.authorization_revoked = true; },
    value => { value.inspection.evidence.destination_verified = false; },
    value => { value.inspection.evidence.terms_verified = false; },
    value => { value.userReady.ready = false; },
    value => { value.priorFailure.order_history_count = 1; },
    value => { value.provenance.history_restore = true; },
    value => { value.provenance.source = 'trusted_normal_current_dom'; },
    value => { value.provenance.checkout_document_ref = value.provenance.cart_document_ref; },
    value => { value.provenance.checkout_observed_at -= 21; value.provenance.cart_observed_at -= 21;
      value.inspection.evidence.observed_at -= 21; },
    value => { value.card_number = 'SYNTHETIC_NEVER_LOG_THIS'; },
  ]) await stateFixture(async (directory, saved) => {
    const claim = await currentClaim(directory); alter(claim);
    const result = await oneShot(directory, claim); assert.equal(result.message.type, 'claim_stopped');
    assert.equal((await audits(directory)).length, 0);
    assert.ok(!result.output.includes('SYNTHETIC_NEVER_LOG_THIS') && !result.errors.includes('SYNTHETIC_NEVER_LOG_THIS'));
    for (const [name, bytes] of Object.entries(saved)) assert.equal(await readFile(join(directory, name), 'utf8'), bytes);
  });
});

test('one-shot outcome refuses tampered attempt or legacy bytes without appending', async () => {
  for (const legacy of [false, true]) await stateFixture(async directory => {
    const claim = await currentClaim(directory), result = await oneShot(directory, claim);
    const name = legacy ? helper.GUEST_CARD_FENCE : 'guest-checkout-000003.json';
    const value = JSON.parse(await readFile(join(directory, name), 'utf8')); value.synthetic_tamper = true;
    const bytes = encoded(value); await writeFile(join(directory, name), bytes);
    assert.equal((await oneShot(directory, outcomeFor(claim, result.message), { mode: 'outcome' })).message.type, 'claim_stopped');
    assert.equal((await audits(directory)).length, 3);
    assert.equal(await readFile(join(directory, name), 'utf8'), bytes);
  });
});

test('all dispatch modes share pending/unknown/form-visible/restricted guard and preserve known-expiry reconciliation', async () => {
  for (const outcome of [null, 'unknown', 'guest_form_visible', 'access_restricted', 'SESSION_EXPIRED']) {
    await stateFixture(async directory => {
      const claim = await currentClaim(directory), granted = await oneShot(directory, claim);
      if (outcome) assert.equal((await oneShot(directory, outcomeFor(claim, granted.message, outcome), { mode: 'outcome' })).code, 0);
      await new Promise(resolve => setTimeout(resolve, 5));
      const before = (await audits(directory)).length, legacy = client(directory);
      try {
        const start = startFor(await legacy.next()); legacy.send(start);
        const result = await legacy.next();
        if (outcome === 'SESSION_EXPIRED') assert.equal(result.stage, 'open_cart');
        else {
          assert.equal(result.type, 'stopped'); await legacy.exited;
          assert.ok(!legacy.output.join('').includes('operator_action'));
          assert.equal((await audits(directory)).length, before);
        }
      } finally { await legacy.close(); }
    });
  }
});

function noSubmitAuthorization(claim) {
  const now = Date.now() / 1000;
  const result = { ...claim }; delete result.userReady;
  result.testAuthorization = { source: 'explicit_user_authorized_no_submit_test', approval_ref: claim.scope.approval_ref,
    scope_digest: claim.provenance.scope_digest, attempt_id: claim.scope.intent_id,
    authorized_at: now - 3600, issued_at: now, expires_at: now + 300,
    card_entry_allowed: false, payment_allowed: false, final_submission_allowed: false };
  return result;
}

test('no-submit test authorization is distinct from human readiness and records original approval without payment authority', async () => stateFixture(async directory => {
  const claim = noSubmitAuthorization(await currentClaim(directory));
  const result = await oneShot(directory, claim);
  assert.equal(result.message.type, 'checkout_claim_granted');
  assert.equal(result.message.requires_execution_revalidation, true);
  assert.deepEqual(result.message.expected_scope, claim.scope);
  assert.equal(result.message.permit.deadline, Math.min(claim.scope.expires_at * 1000,
    claim.testAuthorization.expires_at * 1000, claim.priorFailure.expires_at * 1000,
    (claim.priorFailure.order_history_observed_at + 300) * 1000, claim.inspection.evidence.expires_at * 1000));
  assert.ok(result.message.permit.deadline > (claim.inspection.evidence.observed_at + 20) * 1000);
  for (const key of ['human_payment_ready', 'card_entry_allowed', 'payment_allowed', 'final_submission_allowed']) {
    assert.equal(result.message[key], false);
  }
  const ready = (await audits(directory))[0].payload;
  assert.equal(ready.authorization_source, 'explicit_user_authorized_no_submit_test');
  assert.equal(ready.authorized_at, claim.testAuthorization.authorized_at);
  assert.deepEqual(ready.testAuthorization, claim.testAuthorization);
  assert.ok(!Object.hasOwn(ready, 'ready_observed_at'));
  assert.equal((await oneShot(directory, noSubmitAuthorization(await currentClaim(directory)))).message.stop_reason, 'prior_attempt_unresolved');
}));

test('no-submit authorization rejects changed references, flags, stale issue and mixed human authority before writes', async () => {
  for (const alter of [
    value => { value.testAuthorization.card_entry_allowed = true; },
    value => { value.testAuthorization.payment_allowed = true; },
    value => { value.testAuthorization.final_submission_allowed = true; },
    value => { value.testAuthorization.approval_ref = randomUUID(); },
    value => { value.testAuthorization.scope_digest = 'a'.repeat(64); },
    value => { value.testAuthorization.attempt_id = randomUUID(); },
    value => { value.testAuthorization.issued_at -= 301; value.testAuthorization.expires_at -= 301; },
    value => { value.testAuthorization.expires_at += 1; },
    value => { value.testAuthorization.authorized_at = value.testAuthorization.issued_at + 1; },
    value => { value.userReady = { ready: true }; },
  ]) await stateFixture(async directory => {
    const claim = noSubmitAuthorization(await currentClaim(directory)); alter(claim);
    const result = await oneShot(directory, claim);
    assert.equal(result.message.type, 'claim_stopped');
    assert.ok(['test_authorization_unverified', 'authorization_unverified'].includes(result.message.stop_reason));
    assert.equal((await audits(directory)).length, 0);
  });
});

test('one-shot failure reasons are fixed allowlisted labels and never echo arbitrary exception content', async () => stateFixture(async directory => {
  const claim = await currentClaim(directory);
  const unexpected = await oneShot(directory, claim, { inputFailure: true });
  assert.equal(unexpected.message.stop_reason, 'unknown_internal_stop');
  assert.ok(!unexpected.output.includes('SYNTHETIC_NEVER_LOG_THIS') && !unexpected.errors.includes('SYNTHETIC_NEVER_LOG_THIS'));
  claim.inspection.evidence.total = '5.06';
  assert.equal((await oneShot(directory, claim)).message.stop_reason, 'scope_or_quote_unverified');
  assert.equal((await audits(directory)).length, 0);
}));

async function seedExpiredNoEffectUnknown(directory) {
  const claim = await currentClaim(directory);
  claim.userReady.expires_at = Date.now() / 1000 + 0.35;
  const granted = await oneShot(directory, claim); assert.equal(granted.message.type, 'checkout_claim_granted');
  await new Promise(resolve => setTimeout(resolve, Math.max(1, granted.message.permit.deadline - Date.now() + 5)));
  const actorObserved = Date.now() / 1000;
  assert.equal((await oneShot(directory, outcomeFor(claim, granted.message, 'unknown'), { mode: 'outcome' })).code, 0);
  const proof = { source: 'operator_reviewed_supported_cua_no_dispatch',
    previous_claim_sha256: granted.message.claim_sha256,
    previous_outcome_sha256: hash(await readFile(join(directory, 'guest-checkout-000004.json'))),
    request_id: granted.message.permit.request_id, actor_result: 'expired_before_effect', clicked: false,
    observed_at: actorObserved, card_data_entered: false, pay_invoked: false, approval_invoked: false, capture_invoked: false };
  return { claim, granted: granted.message, proof };
}

test('definitive no-dispatch proof permits only a new no-submit claim, retaining all prior bytes and original quote expiry', async () => stateFixture(async directory => {
  const old = await seedExpiredNoEffectUnknown(directory);
  const names = (await readdir(directory)).sort(), before = await Promise.all(names.map(name => readFile(join(directory, name))));
  const claim = noSubmitAuthorization(await currentClaim(directory)); claim.priorNoDispatch = old.proof;
  // The actual old SESSION_EXPIRED observation stays historical, never invented anew.
  claim.priorFailure.failure_observed_at = old.claim.priorFailure.failure_observed_at;
  claim.inspection.evidence.expires_at = Date.now() / 1000 + 30;
  const result = await oneShot(directory, claim);
  assert.equal(result.message.type, 'checkout_claim_granted');
  assert.equal(result.message.requires_execution_revalidation, true);
  assert.deepEqual(result.message.expected_scope, claim.scope);
  assert.equal(result.message.permit.deadline, claim.inspection.evidence.expires_at * 1000);
  const records = await audits(directory); assert.equal(records.length, 7);
  assert.deepEqual(records[5].payload.priorNoDispatch, old.proof);
  for (let i = 0; i < names.length; i++) assert.deepEqual(await readFile(join(directory, names[i])), before[i]);
  const another = noSubmitAuthorization(await currentClaim(directory)); another.priorNoDispatch = old.proof;
  assert.equal((await oneShot(directory, another)).message.type, 'claim_stopped');
  assert.equal((await audits(directory)).length, 7);
}));

test('unknown resolution rejects wrong hashes, request, times, flags, generic errors and stale order reconciliation', async () => stateFixture(async directory => {
  const old = await seedExpiredNoEffectUnknown(directory);
  for (const alter of [
    value => { value.priorNoDispatch.previous_claim_sha256 = 'a'.repeat(64); },
    value => { value.priorNoDispatch.previous_outcome_sha256 = 'b'.repeat(64); },
    value => { value.priorNoDispatch.request_id = randomUUID(); },
    value => { value.priorNoDispatch.actor_result = 'timeout'; },
    value => { value.priorNoDispatch.clicked = true; },
    value => { value.priorNoDispatch.card_data_entered = true; },
    value => { value.priorNoDispatch.pay_invoked = true; },
    value => { value.priorNoDispatch.approval_invoked = true; },
    value => { value.priorNoDispatch.capture_invoked = true; },
    value => { value.priorNoDispatch.observed_at = old.granted.permit.deadline / 1000 - 0.001; },
    value => { value.priorNoDispatch.observed_at = Date.now() / 1000 + 60; },
    value => { value.priorFailure.failure_observed_at = old.claim.priorFailure.failure_observed_at;
      value.priorFailure.order_history_observed_at = old.proof.observed_at - 0.001; },
    value => { value.priorNoDispatch.extra = 'SYNTHETIC_UNSUPPORTED'; },
  ]) {
    const claim = noSubmitAuthorization(await currentClaim(directory)); claim.priorNoDispatch = { ...old.proof }; alter(claim);
    const result = await oneShot(directory, claim);
    assert.equal(result.message.type, 'claim_stopped');
    assert.equal(result.message.stop_reason, 'prior_no_dispatch_unverified');
    assert.equal((await audits(directory)).length, 4);
  }
  const human = await currentClaim(directory); human.priorNoDispatch = old.proof;
  assert.equal((await oneShot(directory, human)).message.stop_reason, 'authorization_unverified');
  assert.equal((await audits(directory)).length, 4);
}));

test('two concurrent no-dispatch resolution claims can consume the reconciled history only once', async () => stateFixture(async directory => {
  const old = await seedExpiredNoEffectUnknown(directory);
  const a = noSubmitAuthorization(await currentClaim(directory)), b = noSubmitAuthorization(await currentClaim(directory));
  a.priorNoDispatch = old.proof; b.priorNoDispatch = old.proof;
  const results = await Promise.all([oneShot(directory, a), oneShot(directory, b)]);
  assert.equal(results.filter(value => value.message.type === 'checkout_claim_granted').length, 1);
  assert.equal((await audits(directory)).length, 7);
  const legacy = client(directory);
  try {
    const start = startFor(await legacy.next()); legacy.send(start);
    assert.equal((await legacy.next()).type, 'stopped'); await legacy.exited;
    assert.ok(!legacy.output.join('').includes('operator_action'));
  } finally { await legacy.close(); }
}));

test('317.227-second producer quote interval is rejected until expiry is clamped without refreshing observation', () => {
  const start = startFor({ latest_hash: 'f'.repeat(64) }, { ttl: 300 });
  const now = start.scope.issued_at, observedAt = now - 17.227;
  const templates = operator.prepareOperatorAckTemplates(start, refs());
  const inspection = { schema: 'discogs_guest_card_inspection_v1', url: helper.GUEST_CARD_URL,
    access: 'normal', control: templates.card_control,
    evidence: { ...templates.checkout.evidence, observed_at: observedAt, expires_at: start.scope.expires_at } };
  assert.equal((inspection.evidence.expires_at - observedAt).toFixed(3), '317.227');
  assert.equal(helper.validateGuestCardInspection(inspection, start.scope, { now }), null);
  inspection.evidence.expires_at = Math.min(start.scope.expires_at, observedAt + 300);
  assert.equal(inspection.evidence.observed_at, observedAt);
  assert.equal(inspection.evidence.expires_at, observedAt + 300);
  assert.ok(helper.validateGuestCardInspection(inspection, start.scope, { now }));
});

test('a later expired-grant probe cannot resolve an earlier unknown action', async () => stateFixture(async directory => {
  const old = await seedExpiredNoEffectUnknown(directory);
  const unknown = (await audits(directory))[3];
  await new Promise(resolve => setTimeout(resolve, 5));
  const claim = noSubmitAuthorization(await currentClaim(directory));
  claim.priorNoDispatch = { ...old.proof,
    observed_at: Math.max(unknown.payload.observed_at, unknown.recorded_at) + 0.001 };
  assert.ok(claim.priorNoDispatch.observed_at < Date.now() / 1000);
  assert.ok(claim.priorNoDispatch.observed_at * 1000 >= old.granted.permit.deadline);
  const result = await oneShot(directory, claim);
  assert.equal(result.message.type, 'claim_stopped');
  assert.equal(result.message.stop_reason, 'prior_no_dispatch_unverified');
  assert.equal((await audits(directory)).length, 4);
}));
