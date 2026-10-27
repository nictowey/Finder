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
let helper, operator, makeCurrentCheckoutCuaActor;
before(async () => { helper = await import('../scripts/discogs_guest_card_prepare.mjs');
  operator = await import('../scripts/discogs_guest_checkout_operator.mjs');
  ({ makeCurrentCheckoutCuaActor } = await import('../scripts/discogs_guest_cua_operator.mjs')); });
const encoded = value => JSON.stringify(value) + '\n';
const hash = value => createHash('sha256').update(value).digest('hex');
const moduleURL = pathToFileURL(resolve('scripts/discogs_guest_checkout_operator.mjs')).href;

async function stateFixture(run, { now = Date.now() } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'sniper-operator-cli-'));
  const original = { schema: 'discogs_guest_card_init_fence_v1', intent_id: randomUUID(),
    scope_digest: 'a'.repeat(64), release_id: 3422100, listing_id: '1366950910', created_at: now / 1000 - 30 };
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
function startFor(waiting, { ttl = 45, readyRef = randomUUID(), intent = randomUUID(), now = Date.now() / 1000 } = {}) {
  const scope = { intent_id: intent, approval_ref: randomUUID(), release_id: 3422100,
    listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    rules_revision: 0, quote_fingerprint: 'd'.repeat(64), destination_ref: randomUUID(),
    terms_fingerprint: 'e'.repeat(64), issued_at: now, expires_at: now + 300 };
  return { type: 'start', ready_ref: readyRef, scope, expected_latest_hash: waiting.latest_hash,
    userReady: { source: 'explicit_current_user_ready', ready: true,
      scope_digest: helper.guestCardScopeDigest(scope, { now }), observed_at: now, expires_at: now + ttl },
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

async function currentClaim(directory, { now = Date.now() / 1000 } = {}) {
  const probe = client(directory); let waiting;
  try { waiting = await probe.next(); } finally { await probe.close(); }
  const start = startFor(waiting, { ttl: 300, now }), values = refs();
  const templates = operator.prepareOperatorAckTemplates(start, values), observed = now;
  return { type: 'claim_current_checkout', ready_ref: start.ready_ref, scope: start.scope,
    userReady: start.userReady, priorFailure: start.priorFailure, expected_latest_hash: start.expected_latest_hash,
    provenance: { source: 'operator_verified_current_screen', navigation: 'normal_cart_to_fresh_checkout',
      scope_digest: templates.scope_digest, cart_document_ref: values.cart, checkout_document_ref: values.checkout,
      cart_observed_at: observed, checkout_observed_at: observed, cart_scope_verified: true,
      go_to_checkout_verified: true, history_restore: false, resubmission_prompt: false, extra_agreement_required: false },
    inspection: { schema: 'discogs_guest_card_inspection_v1', url: helper.GUEST_CARD_URL, access: 'normal',
      control: templates.card_control, evidence: { ...templates.checkout.evidence, observed_at: observed } } };
}
async function oneShot(directory, value, { mode = 'claim', crashBeforePermit = false, inputFailure = false, now = null } = {}) {
  const source = `import {runCurrentCheckoutClaim} from ${JSON.stringify(moduleURL)};
    ${now === null ? '' : `Date.now = () => ${Number(now)};`}
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

function noSubmitAuthorization(claim, now = Date.now() / 1000) {
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

function interceptedCurrentActor(grant, clock) {
  const { runInThisContext } = require('node:vm');
  const factory = runInThisContext(`(${makeCurrentCheckoutCuaActor.toString()})`);
  const effects = []; let screen = 'synthetic_normal_checkout';
  const view = Object.freeze({
    getAXState: async options => { effects.push(['getAXState', options, clock.now]); return { screen }; },
    getScreenshot: async options => { effects.push(['getScreenshot', options, clock.now]); return { screen, synthetic: true }; },
    click: async point => {
      assert.deepEqual(point, [350, 550]); effects.push(['click', point, clock.now]); screen = 'synthetic_blank_guest_form';
    },
    markHandoff: async () => { effects.push(['markHandoff', null, clock.now]); },
  });
  const actor = factory(view, grant, { clock: () => clock.now });
  const proof = observed => {
    const s = grant.expected_scope, p = grant.permit;
    return { source: 'operator_current_screen', observation_id: observed.observation_id,
      claim_sha256: grant.claim_sha256, intent_id: p.intent_id, ready_ref: p.ready_ref, scope_digest: p.scope_digest,
      url: helper.GUEST_CARD_URL, document_ref: p.from_document_ref, control_ref: p.control_ref,
      inspection_fingerprint: p.inspection_fingerprint, control_label: 'Debit or Credit Card', control_count: 1,
      control_visible: true, control_enabled: true, point: [350, 550], approval_scope_verified: true,
      item_scope_verified: true, quote_verified: true, destination_verified: true, terms_verified: true,
      no_card_data_entered: true, normal_checkout: true, history_restore: false, resubmission_prompt: false,
      extra_agreement_required: false, challenge_present: false, release_id: s.release_id, listing_id: s.listing_id,
      seller: s.seller, quantity: 1, item_count: 1, seller_count: 1, rules_revision: s.rules_revision,
      approval_ref: s.approval_ref, currency: s.currency, item: '0.10', shipping: '4.95', total: '5.05',
      quote_fingerprint: s.quote_fingerprint, destination_ref: s.destination_ref, terms_fingerprint: s.terms_fingerprint,
      authorization_current: true, authorization_revoked: false, already_owned: false, existing_order: false,
      uncertain_prior_action: false, duplicate_guard_verified: true, rules_current: true, criteria_satisfied: true,
      shipping_selection_verified: true, preparation_quote_verified: true, final_payment_quote_verified: false,
      deferred_total_notice: false, amount_basis: 'explicit_merchant_checkout_total' };
  };
  return { actor, view, effects, proof };
}

test('intercepted integration joins real claim, serialized actor and exactly-once outcome after delayed transport', async () => stateFixture(async directory => {
  const clock = { now: 1_800_000_000_000 };
  const claim = noSubmitAuthorization(await currentClaim(directory, { now: clock.now / 1000 }), clock.now / 1000);
  const result = await oneShot(directory, claim, { now: clock.now });
  assert.equal(result.message.type, 'checkout_claim_granted');
  const originalDeadline = result.message.permit.deadline;
  assert.equal((await audits(directory)).length, 3);
  clock.now += 45_000; // Simulated transport exceeds the former whole-operation quote budget.
  assert.ok(clock.now > (claim.inspection.evidence.observed_at + 20) * 1000 && clock.now < originalDeadline);
  const s = interceptedCurrentActor(result.message, clock), observed = await s.actor.observe();
  assert.equal(observed.observed_at, clock.now / 1000);
  assert.equal(observed.screenshot.screen, 'synthetic_normal_checkout');
  clock.now += 11_168;
  const effect = await s.actor.dispatch(s.proof(observed));
  assert.equal(effect.status, 'effect_requires_screen_observation');
  const clicks = s.effects.filter(effect => effect[0] === 'click');
  assert.equal(clicks.length, 1); assert.equal(clicks[0][2] - observed.observed_at * 1000, 11_168);
  assert.equal(s.actor.status().deadline, originalDeadline);
  assert.equal((await s.view.getAXState({ emit: false })).screen, 'synthetic_blank_guest_form');
  await assert.rejects(s.actor.dispatch(s.proof(observed)), /not_live/);
  assert.deepEqual(Object.keys(s.view).sort(), ['click', 'getAXState', 'getScreenshot', 'markHandoff']);
  assert.ok(s.effects.every(effect => ['click', 'getAXState', 'getScreenshot'].includes(effect[0])));
  const outcome = { ...outcomeFor(claim, result.message, 'guest_form_visible'), observed_at: clock.now / 1000 };
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome', now: clock.now })).message.duplicate, false);
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome', now: clock.now })).message.duplicate, true);
  const records = await audits(directory); assert.equal(records.length, 4);
  assert.equal(records[3].payload.outcome, 'guest_form_visible');
  for (const key of ['human_payment_ready', 'card_entry_allowed', 'payment_allowed', 'final_submission_allowed', 'autonomous_checkout']) {
    assert.equal(result.message[key], false);
  }
  assert.equal(result.message.order_status, 'unverified'); assert.equal(result.message.payment_status, 'unverified');
}, { now: 1_800_000_000_000 }));

test('intercepted integration rejects stale or changed quote; rebound runtime cannot mint grants past pending or unknown', async () => {
  for (const mode of ['stale', 'changed_total']) await stateFixture(async directory => {
    const clock = { now: 1_800_000_000_000 };
    const claim = noSubmitAuthorization(await currentClaim(directory, { now: clock.now / 1000 }), clock.now / 1000);
    const granted = await oneShot(directory, claim, { now: clock.now });
    assert.equal(granted.message.type, 'checkout_claim_granted');
    clock.now += 45_000;
    const s = interceptedCurrentActor(granted.message, clock), observed = await s.actor.observe();
    const proof = s.proof(observed);
    if (mode === 'stale') clock.now += 20_001;
    else { clock.now += 11_168; proof.total = '5.06'; }
    await assert.rejects(s.actor.dispatch(proof), /screen_unverified/); s.actor.cancel();
    assert.equal(s.effects.some(effect => effect[0] === 'click'), false);
    // Rebinding only creates code. It does not replay the existing permit or bypass server state.
    const rebound = require('node:vm').runInThisContext(`(${makeCurrentCheckoutCuaActor.toString()})`);
    assert.equal(typeof rebound, 'function');
    const nextClaim = () => currentClaim(directory, { now: clock.now / 1000 })
      .then(value => noSubmitAuthorization(value, clock.now / 1000));
    const pending = await oneShot(directory, await nextClaim(), { now: clock.now });
    assert.equal(pending.message.stop_reason, 'prior_attempt_unresolved');
    assert.ok(!Object.hasOwn(pending.message, 'permit'));
    assert.equal((await audits(directory)).length, 3);
    const outcome = { ...outcomeFor(claim, granted.message, 'unknown'), observed_at: clock.now / 1000 };
    assert.equal((await oneShot(directory, outcome, { mode: 'outcome', now: clock.now })).code, 0);
    clock.now += 1;
    const unknown = await oneShot(directory, await nextClaim(), { now: clock.now });
    assert.equal(unknown.message.stop_reason, 'prior_attempt_unresolved');
    assert.ok(!Object.hasOwn(unknown.message, 'permit'));
    assert.equal((await audits(directory)).length, 4);
    assert.equal(s.effects.some(effect => effect[0] === 'click'), false);
  }, { now: 1_800_000_000_000 });
});

async function visibleFormDispositionFixture(directory) {
  const at = 1_800_000_000_000;
  const claim = noSubmitAuthorization(await currentClaim(directory, { now: at / 1000 }), at / 1000);
  const granted = await oneShot(directory, claim, { now: at });
  assert.equal(granted.message.type, 'checkout_claim_granted');
  const outcome = { ...outcomeFor(claim, granted.message, 'guest_form_visible'), observed_at: (at + 1000) / 1000 };
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome', now: at + 1000 })).code, 0);
  const disposition = { type: 'record_form_disposition', source: 'operator_reviewed_normal_sdk_dismissal',
    previous_claim_sha256: granted.message.claim_sha256,
    previous_outcome_sha256: hash(await readFile(join(directory, 'guest-checkout-000004.json'))),
    intent_id: claim.scope.intent_id, ready_ref: claim.ready_ref, scope_digest: claim.provenance.scope_digest,
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    blank_form_verified: true, blank_form_observed_at: (at + 2000) / 1000, card_data_entered: false,
    pay_invoked: false, approval_invoked: false, capture_invoked: false, dismissal_kind: 'visible_sdk_close',
    sdk_close_verified: true, dismissal_observed_at: (at + 3000) / 1000, current_form_present: false,
    current_url: helper.GUEST_CARD_URL, normal_route_verified: true, current_observed_at: (at + 4000) / 1000,
    order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50',
    order_history_archived: 'All', order_history_status: 'All', order_history_count: 0, order_history_complete: true,
    order_history_observed_at: (at + 5000) / 1000, reviewed_at: (at + 6000) / 1000, expires_at: (at + 306000) / 1000 };
  return { at, claim, disposition };
}

async function unknownFormObservationFixture(directory, outcomeDelayMs = 1000) {
  const at = 1_800_000_000_000;
  const claim = noSubmitAuthorization(await currentClaim(directory, { now: at / 1000 }), at / 1000);
  const granted = await oneShot(directory, claim, { now: at });
  assert.equal(granted.message.type, 'checkout_claim_granted');
  const outcome = { ...outcomeFor(claim, granted.message, 'unknown'), observed_at: (at + outcomeDelayMs) / 1000 };
  assert.equal((await oneShot(directory, outcome, { mode: 'outcome', now: at + outcomeDelayMs })).code, 0);
  const observation = { type: 'record_form_observation', source: 'operator_verified_current_blank_form',
    previous_claim_sha256: granted.message.claim_sha256,
    previous_outcome_sha256: hash(await readFile(join(directory, 'guest-checkout-000004.json'))),
    intent_id: claim.scope.intent_id, ready_ref: claim.ready_ref, scope_digest: claim.provenance.scope_digest,
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, max_total: '5.05',
    checkout_document_ref: claim.provenance.checkout_document_ref,
    current_document_ref: claim.provenance.checkout_document_ref, document_continuity_verified: true,
    current_url: helper.GUEST_CARD_URL, access: 'normal', blank_form_visible: true, card_fields_blank: true,
    payment_controls_untouched: true, displayed_pay_amount: '5.05', displayed_merchant_total: '5.05', currency: 'USD',
    observed_at: (at + outcomeDelayMs + 1000) / 1000, expires_at: (at + outcomeDelayMs + 61000) / 1000,
    merchant_order_status: 'unverified', payment_status: 'unverified', provider_draft_status: 'unverified' };
  return { at, claim, granted: granted.message, observation };
}

async function observedFormDispositionFixture(directory, outcomeDelayMs = 1000) {
  const original = await unknownFormObservationFixture(directory, outcomeDelayMs);
  const { at, observation } = original, base = at + outcomeDelayMs;
  const recorded = await oneShot(directory, observation, { mode: 'observation', now: base + 2000 });
  assert.equal(recorded.message.type, 'form_observation_recorded');
  const disposition = { type: 'record_form_disposition', source: 'operator_reviewed_normal_sdk_dismissal',
    previous_claim_sha256: observation.previous_claim_sha256, previous_outcome_sha256: observation.previous_outcome_sha256,
    intent_id: observation.intent_id, ready_ref: observation.ready_ref, scope_digest: observation.scope_digest,
    release_id: 3422100, listing_id: '1366950910', seller: 'Mailman21842', quantity: 1, currency: 'USD', max_total: '5.05',
    blank_form_verified: true, blank_form_observed_at: (base + 3000) / 1000, card_data_entered: false,
    pay_invoked: false, approval_invoked: false, capture_invoked: false, dismissal_kind: 'visible_sdk_close',
    sdk_close_verified: true, dismissal_observed_at: (base + 4000) / 1000, current_form_present: false,
    current_url: helper.GUEST_CARD_URL, normal_route_verified: true, current_observed_at: (base + 5000) / 1000,
    order_history_url: 'https://www.discogs.com/sell/purchases?archived=A&status=All&limit=50',
    order_history_archived: 'All', order_history_status: 'All', order_history_count: 0, order_history_complete: true,
    order_history_observed_at: (base + 6000) / 1000, reviewed_at: (base + 7000) / 1000,
    expires_at: (base + 307000) / 1000, previous_observation_sha256: recorded.message.observation_sha256,
    merchant_order_status: 'unverified', payment_status: 'unverified', provider_draft_status: 'unverified' };
  return { ...original, base, disposition };
}

test('observed-form disposition preserves unknown and observation, allows only a new scoped no-submit claim with newer reconciliation', async () => stateFixture(async directory => {
  const { at, base, claim: originalClaim, disposition } = await observedFormDispositionFixture(directory);
  const before = await audits(directory);
  const recorded = await oneShot(directory, disposition, { mode: 'disposition', now: base + 7000 });
  assert.equal(recorded.message.type, 'form_disposition_recorded');
  assert.equal(recorded.message.provider_draft_status, 'unverified');
  assert.deepEqual((await audits(directory)).slice(0, 5), before);
  const gate = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(gate.state, 'reviewed_form_disposition'); assert.equal(gate.record_count, 6);
  assert.equal(gate.init_allowed, false); assert.equal(gate.no_submit_init_allowed, true);
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (base + 8000) / 1000 }), (base + 8000) / 1000);
  next.priorFailure.failure_observed_at = originalClaim.priorFailure.failure_observed_at;
  assert.equal((await oneShot(directory, next, { now: base + 8000 })).message.stop_reason, 'prior_attempt_unresolved');
  next.priorFormDisposition_sha256 = recorded.message.disposition_sha256;
  const oldHistory = structuredClone(next); oldHistory.priorFailure.order_history_observed_at = (base + 7000) / 1000;
  assert.equal((await oneShot(directory, oldHistory, { now: base + 8000 })).message.stop_reason, 'form_disposition_unverified');
  const human = await currentClaim(directory, { now: (base + 8000) / 1000 });
  human.priorFormDisposition_sha256 = recorded.message.disposition_sha256;
  assert.equal((await oneShot(directory, human, { now: base + 8000 })).message.stop_reason, 'authorization_unverified');
  const granted = await oneShot(directory, next, { now: base + 8000 });
  assert.equal(granted.message.type, 'checkout_claim_granted');
  assert.equal(granted.message.card_entry_allowed, false); assert.equal(granted.message.payment_allowed, false);
  assert.equal((await audits(directory))[7].payload.priorFormDisposition_sha256, recorded.message.disposition_sha256);
  assert.equal(before[3].payload.outcome, 'unknown'); assert.equal(before[4].kind, 'form_observation');
}, { now: 1_800_000_000_000 }));

test('observed disposition rejects wrong linkage, missing reconciliation, unsafe dismissal and invented provider status', async () => stateFixture(async directory => {
  const { base, disposition } = await observedFormDispositionFixture(directory);
  for (const patch of [{ previous_claim_sha256: 'a'.repeat(64) }, { previous_outcome_sha256: 'b'.repeat(64) },
    { previous_observation_sha256: 'c'.repeat(64) }, { scope_digest: 'd'.repeat(64) }, { listing_id: 'other' },
    { previous_observation_sha256: undefined }, { dismissal_kind: 'timeout' }, { sdk_close_verified: false },
    { blank_form_observed_at: (base + 1999) / 1000 }, { card_data_entered: true }, { pay_invoked: true },
    { approval_invoked: true }, { capture_invoked: true }, { current_form_present: true },
    { order_history_complete: false }, { order_history_count: 1 }, { order_history_status: 'New Order' },
    { order_history_archived: 'Not Archived' }, { order_history_observed_at: (base + 3000) / 1000 },
    { provider_draft_status: 'none' }, { merchant_order_status: 'none' }, { payment_status: 'unpaid' }]) {
    const result = await oneShot(directory, { ...disposition, ...patch }, { mode: 'disposition', now: base + 7000 });
    assert.equal(result.message.stop_reason, 'form_disposition_unverified');
    assert.equal((await audits(directory)).length, 5);
  }
  assert.equal((await oneShot(directory, null, { mode: 'disposition', now: base + 7000 })).message.type, 'claim_stopped');
}, { now: 1_800_000_000_000 }));

test('observed disposition exact replay survives restart without creating fresh authority; changed or old-chain replay cannot append', async () => stateFixture(async directory => {
  const { base, disposition } = await observedFormDispositionFixture(directory);
  const recorded = await oneShot(directory, disposition, { mode: 'disposition', now: base + 7000 });
  const replay = await oneShot(directory, disposition, { mode: 'disposition', now: base + 400000 });
  assert.equal(replay.message.duplicate, true); assert.equal(replay.message.disposition_sha256, recorded.message.disposition_sha256);
  const processGate = spawnSync(process.execPath, ['--input-type=module', '-e',
    `import{inspectGuestCheckoutLedger}from ${JSON.stringify(moduleURL)};
    console.log(JSON.stringify(await inspectGuestCheckoutLedger({directory:process.argv[1]})));`, directory], { encoding: 'utf8' });
  assert.equal(processGate.status, 0); assert.equal(JSON.parse(processGate.stdout).state, 'reviewed_form_disposition');
  assert.equal((await oneShot(directory, { ...disposition, order_history_observed_at: (base + 6500) / 1000 },
    { mode: 'disposition', now: base + 8000 })).message.type, 'claim_stopped');
  const unlinked = { ...disposition }; delete unlinked.previous_observation_sha256;
  delete unlinked.merchant_order_status; delete unlinked.payment_status; delete unlinked.provider_draft_status;
  assert.equal((await oneShot(directory, unlinked, { mode: 'disposition', now: base + 8000 })).message.type, 'claim_stopped');
  assert.equal((await audits(directory)).length, 6);
}, { now: 1_800_000_000_000 }));

test('observed disposition cannot be bypassed by valid no-dispatch override and concurrent appends remain at most once', async () => stateFixture(async directory => {
  const { base, claim, granted, disposition } = await observedFormDispositionFixture(directory, 301000);
  const results = await Promise.all([oneShot(directory, disposition, { mode: 'disposition', now: base + 7000 }),
    oneShot(directory, disposition, { mode: 'disposition', now: base + 7000 })]);
  assert.equal(results.filter(r => r.message.type === 'form_disposition_recorded' && !r.message.duplicate).length, 1);
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (base + 8000) / 1000 }), (base + 8000) / 1000);
  next.priorFailure.failure_observed_at = claim.priorFailure.failure_observed_at;
  next.priorNoDispatch = { source: 'operator_reviewed_supported_cua_no_dispatch',
    previous_claim_sha256: granted.claim_sha256, previous_outcome_sha256: disposition.previous_outcome_sha256,
    request_id: granted.permit.request_id, actor_result: 'expired_before_effect', clicked: false,
    observed_at: granted.permit.deadline / 1000 + 0.001, card_data_entered: false,
    pay_invoked: false, approval_invoked: false, capture_invoked: false };
  assert.equal((await oneShot(directory, next, { now: base + 8000 })).message.stop_reason, 'prior_attempt_unresolved');
  assert.equal((await audits(directory)).length, 6);
}, { now: 1_800_000_000_000 }));

test('a visible-form observation blocks even an otherwise matching prior-no-dispatch override', async () => stateFixture(async directory => {
  const { at, claim, granted, observation } = await unknownFormObservationFixture(directory, 301000);
  assert.equal((await oneShot(directory, observation, { mode: 'observation', now: at + 303000 })).code, 0);
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (at + 304000) / 1000 }), (at + 304000) / 1000);
  next.priorNoDispatch = { source: 'operator_reviewed_supported_cua_no_dispatch',
    previous_claim_sha256: granted.claim_sha256, previous_outcome_sha256: observation.previous_outcome_sha256,
    request_id: granted.permit.request_id, actor_result: 'expired_before_effect', clicked: false,
    observed_at: granted.permit.deadline / 1000 + 0.001, card_data_entered: false,
    pay_invoked: false, approval_invoked: false, capture_invoked: false };
  next.priorFailure.failure_observed_at = claim.priorFailure.failure_observed_at;
  assert.equal((await oneShot(directory, next, { now: at + 304000 })).message.stop_reason, 'prior_attempt_unresolved');
  assert.equal((await audits(directory)).length, 5);
}, { now: 1_800_000_000_000 }));

test('blank form observation preserves unknown outcome, blocks every retry path and survives fresh process inspection', async () => stateFixture(async directory => {
  const { at, observation } = await unknownFormObservationFixture(directory);
  const before = await audits(directory);
  assert.ok(before.every(record => !Object.hasOwn(record.payload, 'scope')));
  assert.equal(Object.hasOwn(observation, 'scope'), false);
  const recorded = await oneShot(directory, observation, { mode: 'observation', now: at + 3000 });
  assert.equal(recorded.message.type, 'form_observation_recorded');
  assert.equal(recorded.message.duplicate, false); assert.equal(recorded.message.init_allowed, false);
  assert.equal(recorded.message.no_submit_init_allowed, false);
  const after = await audits(directory);
  assert.deepEqual(after.slice(0, 4), before); assert.equal(after[3].payload.outcome, 'unknown');
  assert.equal(after[4].kind, 'form_observation');
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e',
    `import {inspectGuestCheckoutLedger} from ${JSON.stringify(moduleURL)};
     console.log(JSON.stringify(await inspectGuestCheckoutLedger({directory:process.argv[1]})));`, directory], { encoding: 'utf8' });
  assert.equal(restarted.status, 0);
  const gate = JSON.parse(restarted.stdout);
  assert.equal(gate.state, 'guest_form_observed_repeat_blocked'); assert.equal(gate.record_count, 5);
  assert.equal(gate.init_allowed, false); assert.equal(gate.no_submit_init_allowed, false);
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (at + 7000) / 1000 }), (at + 7000) / 1000);
  assert.equal((await oneShot(directory, next, { now: at + 7000 })).message.stop_reason, 'prior_attempt_unresolved');
  const legacy = client(directory);
  try {
    const start = startFor(await legacy.next()); legacy.send(start);
    assert.equal((await legacy.next()).type, 'stopped'); await legacy.exited;
    assert.ok(!legacy.output.join('').includes('operator_action'));
  } finally { await legacy.close(); }
  assert.equal((await audits(directory)).length, 5);
}, { now: 1_800_000_000_000 }));

test('form observation rejects wrong claim, stale/replaced document, scope/amount mismatch and invented outcomes', async () => stateFixture(async directory => {
  const { at, observation } = await unknownFormObservationFixture(directory);
  for (const patch of [{ previous_claim_sha256: 'a'.repeat(64) }, { previous_outcome_sha256: 'b'.repeat(64) },
    { current_document_ref: randomUUID() }, { checkout_document_ref: randomUUID() }, { document_continuity_verified: false },
    { scope_digest: 'a'.repeat(64) }, { release_id: 3422101 }, { listing_id: '1366950911' },
    { seller: 'different' }, { quantity: 2 }, { max_total: '5.06' }, { displayed_pay_amount: '5.06' },
    { displayed_merchant_total: '5.06' }, { currency: 'CAD' }, { card_fields_blank: false },
    { payment_controls_untouched: false }, { access: 'challenge' }, { blank_form_visible: false },
    { merchant_order_status: 'none' }, { payment_status: 'unpaid' }, { provider_draft_status: 'none' },
    { observed_at: (at - 1000) / 1000 }, { observed_at: (at + 4000) / 1000 },
    { expires_at: (at + 63000) / 1000 }, { secret_card_number: 'SYNTHETIC_NEVER_LOG' }]) {
    const result = await oneShot(directory, { ...observation, ...patch }, { mode: 'observation', now: at + 3000 });
    assert.equal(result.message.stop_reason, 'form_observation_unverified');
    assert.ok(!result.output.includes('SYNTHETIC_NEVER_LOG'));
    assert.equal((await audits(directory)).length, 4);
  }
  assert.equal((await oneShot(directory, observation, { mode: 'observation', now: at + 62000 })).message.stop_reason,
    'form_observation_unverified');
}, { now: 1_800_000_000_000 }));

test('form observation exact replay is idempotent after restart/expiry, changed replay cannot append', async () => stateFixture(async directory => {
  const { at, observation } = await unknownFormObservationFixture(directory);
  const recorded = await oneShot(directory, observation, { mode: 'observation', now: at + 3000 });
  const replay = await oneShot(directory, observation, { mode: 'observation', now: at + 120000 });
  assert.equal(replay.message.duplicate, true);
  assert.equal(replay.message.observation_sha256, recorded.message.observation_sha256);
  assert.equal((await oneShot(directory, { ...observation, observed_at: (at + 3000) / 1000 },
    { mode: 'observation', now: at + 4000 })).message.stop_reason, 'form_observation_unverified');
  assert.equal((await audits(directory)).length, 5);
}, { now: 1_800_000_000_000 }));

test('concurrent observations append at most once and tampered history remains blocked', async () => stateFixture(async directory => {
  const { at, observation } = await unknownFormObservationFixture(directory);
  const results = await Promise.all([oneShot(directory, observation, { mode: 'observation', now: at + 3000 }),
    oneShot(directory, observation, { mode: 'observation', now: at + 3000 })]);
  assert.equal(results.filter(result => result.message.type === 'form_observation_recorded' && !result.message.duplicate).length, 1);
  assert.equal((await audits(directory)).length, 5);
  const name = join(directory, 'guest-checkout-000005.json'), item = JSON.parse(await readFile(name, 'utf8'));
  item.payload.current_document_ref = randomUUID(); await writeFile(name, encoded(item));
  const gate = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(gate.state, 'unverified'); assert.equal(gate.init_allowed, false); assert.equal(gate.no_submit_init_allowed, false);
}, { now: 1_800_000_000_000 }));

test('reviewed visible SDK dismissal appends disposition, preserves history and requires explicit new no-submit reconciliation', async () => stateFixture(async directory => {
  const { at, claim: originalClaim, disposition } = await visibleFormDispositionFixture(directory);
  const names = (await readdir(directory)).sort(), before = await Promise.all(names.map(name => readFile(join(directory, name))));
  const blocked = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(blocked.state, 'guest_form_visible'); assert.equal(blocked.init_allowed, false);
  assert.equal(blocked.no_submit_init_allowed, false);
  const browserEffects = [];
  if (blocked.no_submit_init_allowed) browserEffects.push('navigate');
  assert.deepEqual(browserEffects, []);
  const record = await oneShot(directory, disposition, { mode: 'disposition', now: at + 6000 });
  assert.equal(record.message.type, 'form_disposition_recorded'); assert.equal(record.message.duplicate, false);
  assert.equal(record.message.merchant_order_status, 'unverified');
  assert.equal((await audits(directory)).length, 5);
  assert.equal((await oneShot(directory, disposition, { mode: 'disposition', now: at + 6000 })).message.duplicate, true);
  for (let i = 0; i < names.length; i++) assert.deepEqual(await readFile(join(directory, names[i])), before[i]);
  const gate = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(gate.current_head, record.message.disposition_sha256); assert.equal(gate.record_count, 5);
  assert.equal(gate.state, 'reviewed_form_disposition'); assert.equal(gate.init_allowed, false);
  assert.equal(gate.no_submit_init_allowed, true);
  const human = await currentClaim(directory, { now: (at + 7000) / 1000 });
  assert.equal((await oneShot(directory, human, { now: at + 7000 })).message.stop_reason, 'prior_attempt_unresolved');
  human.priorFormDisposition_sha256 = record.message.disposition_sha256;
  assert.equal((await oneShot(directory, human, { now: at + 7000 })).message.stop_reason, 'authorization_unverified');
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (at + 7000) / 1000 }), (at + 7000) / 1000);
  next.priorFailure.failure_observed_at = originalClaim.priorFailure.failure_observed_at;
  assert.equal((await oneShot(directory, next, { now: at + 7000 })).message.stop_reason, 'prior_attempt_unresolved');
  next.priorFormDisposition_sha256 = record.message.disposition_sha256;
  const granted = await oneShot(directory, next, { now: at + 7000 });
  assert.equal(granted.message.type, 'checkout_claim_granted');
  assert.equal((await audits(directory))[6].payload.priorFormDisposition_sha256, record.message.disposition_sha256);
  assert.equal((await operator.inspectGuestCheckoutLedger({ directory })).state, 'pending_attempt');
}, { now: 1_800_000_000_000 }));

test('form disposition rejects timeout, missing form, unverified effects, wrong linkage, partial history or false source without writes', async () => stateFixture(async directory => {
  const { at, disposition } = await visibleFormDispositionFixture(directory);
  for (const patch of [{ source: 'verified_server_dom_normal_sdk_dismissal' }, { dismissal_kind: 'timeout' },
    { dismissal_kind: 'session_missing' }, { sdk_close_verified: false }, { blank_form_verified: false },
    { blank_form_observed_at: (at + 999) / 1000 }, { card_data_entered: true }, { pay_invoked: true },
    { approval_invoked: true }, { capture_invoked: true }, { current_form_present: true },
    { current_url: 'https://example.com/' }, { normal_route_verified: false },
    { previous_claim_sha256: 'a'.repeat(64) }, { previous_outcome_sha256: 'b'.repeat(64) },
    { intent_id: randomUUID() }, { listing_id: 'other' }, { order_history_archived: 'Not Archived' },
    { order_history_complete: false }, { order_history_count: 1 },
    { order_history_observed_at: (at + 2500) / 1000 }, { expires_at: (at + 6000) / 1000 },
    { merchant_order_cancelled: true }]) {
    const result = await oneShot(directory, { ...disposition, ...patch }, { mode: 'disposition', now: at + 6000 });
    assert.equal(result.message.stop_reason, 'form_disposition_unverified');
    assert.equal((await audits(directory)).length, 4);
  }
}, { now: 1_800_000_000_000 }));

test('concurrent disposition and claim cannot race an unlock; replay is exact and newer reconciliation remains required', async () => stateFixture(async directory => {
  const { at, disposition } = await visibleFormDispositionFixture(directory);
  const claim = noSubmitAuthorization(await currentClaim(directory, { now: (at + 7000) / 1000 }), (at + 7000) / 1000);
  claim.priorFormDisposition_sha256 = 'a'.repeat(64);
  const [record, raced] = await Promise.all([
    oneShot(directory, disposition, { mode: 'disposition', now: at + 6000 }),
    oneShot(directory, claim, { now: at + 7000 }),
  ]);
  // flock is nonblocking: if the read-only claim won the lock, recording may be safely retried.
  const accepted = record.message.type === 'form_disposition_recorded' ? record :
    await oneShot(directory, disposition, { mode: 'disposition', now: at + 6000 });
  assert.equal(accepted.message.type, 'form_disposition_recorded');
  assert.equal(raced.message.type, 'claim_stopped'); assert.equal((await audits(directory)).length, 5);
  const changed = { ...disposition, current_url: 'https://www.discogs.com/sell/cart/' };
  assert.equal((await oneShot(directory, changed, { mode: 'disposition', now: at + 7000 })).message.type, 'claim_stopped');
  const next = noSubmitAuthorization(await currentClaim(directory, { now: (at + 7000) / 1000 }), (at + 7000) / 1000);
  next.priorFormDisposition_sha256 = accepted.message.disposition_sha256;
  next.priorFailure.failure_observed_at = (at - 1) / 1000;
  next.priorFailure.order_history_observed_at = (at + 6000) / 1000;
  assert.equal((await oneShot(directory, next, { now: at + 7000 })).message.stop_reason, 'form_disposition_unverified');
  assert.equal((await audits(directory)).length, 5);
}, { now: 1_800_000_000_000 }));

test('read-only ledger disposition gate validates full history and fails closed on tampered disposition', async () => stateFixture(async directory => {
  const clear = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(clear.state, 'no_prior_attempt'); assert.equal(clear.init_allowed, true);
  const { at, disposition } = await visibleFormDispositionFixture(directory);
  const legacy = client(directory);
  try {
    const start = startFor(await legacy.next()); legacy.send(start);
    assert.equal((await legacy.next()).type, 'stopped'); await legacy.exited;
    assert.ok(!legacy.output.join('').includes('operator_action'));
  } finally { await legacy.close(); }
  assert.equal((await oneShot(directory, disposition, { mode: 'disposition', now: at + 6000 })).code, 0);
  const name = join(directory, 'guest-checkout-000005.json');
  const value = JSON.parse(await readFile(name, 'utf8')); value.payload.sdk_close_verified = false;
  const bytes = encoded(value); await writeFile(name, bytes);
  const state = await operator.inspectGuestCheckoutLedger({ directory });
  assert.equal(state.state, 'unverified'); assert.equal(state.init_allowed, false); assert.equal(state.no_submit_init_allowed, false);
  assert.equal(await readFile(name, 'utf8'), bytes); assert.equal((await audits(directory)).length, 5);
}, { now: 1_800_000_000_000 }));

test('ledger inspection blocks crash prefixes ending in ready or review before any attempt fence', async () => {
  for (const previousDisposition of [false, true]) for (const tail of ['ready', 'review']) {
    await stateFixture(async directory => {
      const at = 1_800_000_000_000;
      let baseCount = 0, dispositionHash;
      if (previousDisposition) {
        const visible = await visibleFormDispositionFixture(directory);
        const recorded = await oneShot(directory, visible.disposition, { mode: 'disposition', now: at + 6000 });
        dispositionHash = recorded.message.disposition_sha256; baseCount = 5;
        assert.equal((await operator.inspectGuestCheckoutLedger({ directory })).no_submit_init_allowed, true);
      }
      const claim = noSubmitAuthorization(await currentClaim(directory, { now: (at + 7000) / 1000 }), (at + 7000) / 1000);
      if (dispositionHash) claim.priorFormDisposition_sha256 = dispositionHash;
      assert.equal((await oneShot(directory, claim, { now: at + 7000 })).message.type, 'checkout_claim_granted');
      // Isolated fixture retains exactly the fsynced prefix a process crash could leave.
      await rm(join(directory, `guest-checkout-${String(baseCount + 3).padStart(6, '0')}.json`));
      if (tail === 'ready') await rm(join(directory, `guest-checkout-${String(baseCount + 2).padStart(6, '0')}.json`));
      const before = await audits(directory), gate = await operator.inspectGuestCheckoutLedger({ directory });
      assert.equal(before.at(-1).kind, tail);
      assert.equal(gate.state, 'incomplete_claim'); assert.equal(gate.init_allowed, false); assert.equal(gate.no_submit_init_allowed, false);
      assert.equal(gate.record_count, baseCount + (tail === 'ready' ? 1 : 2));
      const direct = noSubmitAuthorization(await currentClaim(directory, { now: (at + 8000) / 1000 }), (at + 8000) / 1000);
      if (dispositionHash) direct.priorFormDisposition_sha256 = dispositionHash;
      const denied = await oneShot(directory, direct, { now: at + 8000 });
      assert.equal(denied.message.stop_reason, 'incomplete_claim');
      const legacy = client(directory);
      try {
        legacy.send(startFor(await legacy.next(), { now: (at + 8000) / 1000 }));
        assert.equal((await legacy.next()).type, 'stopped'); await legacy.exited;
        assert.ok(!legacy.output.join('').includes('operator_action'));
      } finally { await legacy.close(); }
      assert.deepEqual(await audits(directory), before);
    }, { now: 1_800_000_000_000 });
  }
});

test('fixed server DOM provenance is audit-visible and accepted only for typed no-submit claims', async () => {
  for (const mode of ['no_submit', 'human', 'unsupported_source']) await stateFixture(async directory => {
    const current = await currentClaim(directory);
    const claim = mode === 'human' ? current : noSubmitAuthorization(current);
    claim.provenance.source = mode === 'unsupported_source' ? 'automated_dom' : 'fixed_server_dom_current_checkout';
    const result = await oneShot(directory, claim);
    if (mode === 'no_submit') {
      assert.equal(result.message.type, 'checkout_claim_granted');
      assert.equal((await audits(directory))[1].payload.provenance.source, 'fixed_server_dom_current_checkout');
      assert.equal(result.message.card_entry_allowed, false); assert.equal(result.message.payment_allowed, false);
    } else {
      assert.equal(result.message.stop_reason, 'provenance_unverified');
      assert.equal((await audits(directory)).length, 0);
    }
  });
});
