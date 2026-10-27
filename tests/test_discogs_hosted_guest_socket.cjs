'use strict';
// Actual private IPC and fake context only: no browser, profile, site or grants.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { connect } = require('node:net');
const { stat } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
let listenHostedGuestPreparation, createHostedContextLease, listenHostedReadOnly;
before(async () => {
  ({ listenHostedGuestPreparation, createHostedContextLease } = await import('../scripts/discogs_hosted_guest_socket.mjs'));
  ({ listenHostedReadOnly } = await import('../scripts/discogs_hosted_readonly.mjs'));
});
const path = () => `/tmp/sniper-guest-test-${randomUUID()}.sock`;
const request = action => JSON.stringify({ action, request: { fixture: true } }) + '\n';
function exchange(socketPath, data) {
  return new Promise((resolve, reject) => {
    const client = connect(socketPath); let output = '';
    client.on('connect', () => client.write(data)); client.on('error', reject);
    client.on('data', chunk => { output += chunk; });
    client.on('end', () => resolve(JSON.parse(output)));
  });
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function options(overrides = {}) {
  return { authorize: async () => ({ fixture: true }), accessGuard: async () => true,
    acquireContextLease: createHostedContextLease(), fixtureOperations: {
      prepare_guest_form: async () => ({ status: 'blocked', code: 'prior_attempt_unresolved' }),
      probe_guest_evidence: async () => ({ status: 'observed', code: 'current_dom_evidence_only', observed: { destination_match: false } }),
    }, ...overrides };
}

test('fixed IPC is owner-only, narrow and redacts arbitrary operation output', async () => {
  const socket = path(), config = options();
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'current_dom_evidence_only',
    observed: { destination_match: true, address: 'must not escape' }, token: 'must not escape', final_submission_allowed: true });
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    assert.equal((await stat(socket)).mode & 0o777, 0o600);
    const result = await exchange(socket, request('probe_guest_evidence'));
    assert.deepEqual(result.observed, { destination_match: true });
    assert.equal(result.final_submission_allowed, false);
    assert.equal(JSON.stringify(result).includes('must not escape'), false);
    assert.equal((await exchange(socket, request('navigate'))).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, JSON.stringify({ action: 'probe_guest_evidence', request: {}, url: 'https://invalid/' }) + '\n')).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, request('probe_guest_evidence') + '{}\n')).code, 'unsupported_guest_request');
    assert.equal((await exchange(socket, 'x'.repeat(16385))).code, 'request_too_large');
  } finally { await close(); }
  await assert.rejects(stat(socket), { code: 'ENOENT' });
});

test('trusted scope/access callbacks required; denied scope never reaches browser operation', async () => {
  const socket = path(); let calls = 0;
  await assert.rejects(listenHostedGuestPreparation({}, socket, {}), /scoped_guest_configuration_required/);
  await assert.rejects(listenHostedGuestPreparation({}, '/tmp/unapproved.sock', options()), /unsupported_guest_socket_path/);
  const config = options({ accessGuard: async () => false });
  config.fixtureOperations.prepare_guest_form = async () => { calls++; throw new Error('should not run'); };
  const close = await listenHostedGuestPreparation({}, socket, config);
  try { assert.equal((await exchange(socket, request('prepare_guest_form'))).code, 'scoped_access_required'); assert.equal(calls, 0); }
  finally { await close(); }
});

test('IPC retains bounded provider diagnostics and effect facts but blocks malformed or secret-shaped records', async () => {
  const socket = path(), config = options();
  const diagnostic = { schema: 'provider_frame_predicates_v1', selection: 'one', total_count: 2,
    origin_count: 1, buttons_path_count: 1, matching_count: 1, truncated: false, candidates: [{
      origin_matches: true, path_matches: true, credentials_absent: true, fragment_absent: true, allowlisted: true,
      direct_parent: true, element_visible: true, content_frame_matches: true, owner_frame_matches: true,
      provider_ready: true, access: 'normal', existing_guest_form: false, card_count: 1,
      card_visible: true, card_enabled: true, card_aria_enabled: true, url_stable: true, error_stage: 'none' }] };
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'current_dom_evidence_only', provider_frame_diagnostic: diagnostic });
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    const observed = await exchange(socket, request('probe_guest_evidence'));
    assert.equal(observed.status, 'observed'); assert.deepEqual(observed.provider_frame_diagnostic, diagnostic);
    for (const code of ['provider_frame_missing', 'provider_frame_multiple', 'provider_frame_inspection_error']) {
      config.fixtureOperations.prepare_guest_form = async () => ({ status: 'blocked', code,
        provider_frame_diagnostic: diagnostic, checkout_navigation_issued: true, card_initialization_issued: false, initialization: 'not_dispatched' });
      const result = await exchange(socket, request('prepare_guest_form'));
      assert.equal(result.code, code); assert.equal(result.checkout_navigation_issued, true);
      assert.equal(result.card_initialization_issued, false); assert.deepEqual(result.provider_frame_diagnostic, diagnostic);
    }
    for (const alter of [d => { d.url = 'SECRET_URL'; }, d => { d.candidates[0].token = 'SECRET_TOKEN'; },
      d => { d.candidates[0].access = 'SECRET'; }, d => { d.matching_count = 3; },
      d => { d.candidates.push(d.candidates[0], d.candidates[0]); }, d => { d.candidates[0].error_stage = 'SECRET_EXCEPTION'; }]) {
      const bad = structuredClone(diagnostic); alter(bad);
      config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'current_dom_evidence_only',
        provider_frame_diagnostic: bad, document_ref: randomUUID(), observed: { destination_match: true }, structure: { address_container_count: 1 } });
      const result = await exchange(socket, request('probe_guest_evidence'));
      assert.equal(result.status, 'blocked'); assert.equal(result.code, 'provider_frame_inspection_error');
      assert.equal(result.document_ref, undefined); assert.equal(result.observed, undefined); assert.equal(result.structure, undefined);
      assert.ok(!JSON.stringify(result).includes('SECRET'));
      config.fixtureOperations.prepare_guest_form = async () => ({ status: 'blocked', code: 'guest_form_observation_required',
        provider_frame_diagnostic: bad, checkout_navigation_issued: true, card_initialization_issued: true, initialization: 'outcome_requires_reconciliation' });
      const issued = await exchange(socket, request('prepare_guest_form'));
      assert.equal(issued.status, 'blocked'); assert.equal(issued.card_initialization_issued, true);
      assert.equal(issued.checkout_navigation_issued, true); assert.equal(issued.initialization, 'outcome_requires_reconciliation');
    }
  } finally { await close(); }
});

test('readonly qualification exposes only opaque reference and bounded public structure', async () => {
  const socket = path(), config = options(), ref = randomUUID();
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'current_dom_evidence_only',
    document_ref: ref, merchant_route: 'enhanced_checkout', address_present: true,
    destination_binding_available: false, destination_match: null, observed: { destination_match: null },
    structure: { item_row_count: 1, item_title_present: true, media_grade: 'P', sleeve_grade: 'Generic',
      address_container_count: 1, address_line_count: 4, address: 'never export', address_digest: 'never export' },
    detail_diagnostic: { status: 'incomplete', error_code: 'listing_main_unavailable', ready_state: 'complete',
      main_count: 0, release_link_count: 0, grade_count: 0, body: 'never export' },
    address: 'never export', address_digest: 'never export', initialization_allowed: true });
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    const result = await exchange(socket, request('probe_guest_evidence'));
    assert.equal(result.document_ref, ref); assert.equal(result.destination_match, null);
    assert.deepEqual(result.observed, { destination_match: null });
    assert.deepEqual(result.structure, { item_row_count: 1, address_container_count: 1,
      item_title_present: true, media_grade: 'P', sleeve_grade: 'Generic', address_line_count: 4 });
    assert.equal(JSON.stringify(result).includes('never export'), false);
    assert.equal(result.final_submission_allowed, false); assert.equal(result.initialization_allowed, undefined);
    assert.deepEqual(result.detail_diagnostic, { status: 'incomplete', error_code: 'listing_main_unavailable',
      ready_state: 'complete', main_count: 0, release_link_count: 0, grade_count: 0 });
    config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'approved_saved_destination_bound',
      document_ref: 'bad', merchant_route: 'other', structure: { item_row_count: 3, media_grade: 'secret', address_line_count: 99 },
      detail_diagnostic: { status: 'never export', error_code: 'never export', ready_state: 'never export', main_count: 99 } });
    const rejected = await exchange(socket, request('probe_guest_evidence'));
    assert.equal(rejected.status, 'observed'); assert.equal(rejected.document_ref, undefined);
    assert.equal(rejected.merchant_route, undefined); assert.deepEqual(rejected.structure, {});
    assert.deepEqual(rejected.detail_diagnostic, { error_code: 'listing_detail_unverified' });
  } finally { await close(); }
});

test('shared context lease blocks reader while preparation owns context, then releases after completion', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const config = options({ acquireContextLease });
  config.fixtureOperations.prepare_guest_form = async () => { entered.resolve(); await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const closeGuest = await listenHostedGuestPreparation({}, guest, config);
  const closeReader = await listenHostedReadOnly({ pages: () => [] }, reader, { acquireContextLease });
  try {
    const first = exchange(guest, request('prepare_guest_form')); await entered.promise;
    assert.equal((await exchange(reader, '{"action":"current_release","release_id":"111"}\n')).code, 'read_in_progress');
    assert.equal((await exchange(guest, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await first;
    assert.equal((await exchange(reader, '{"action":"current_release","release_id":"111"}\n')).code, 'exact_current_page_not_unique');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('reader lease blocks preparation until actual reader work settles', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise;
    return { source: 'discogs_rendered_release', release_id: 111, url: 'https://www.discogs.com/sell/release/111', status: 'observed', complete: true, has_next: false, total: 0, rows: [] };
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  try {
    const first = exchange(reader, '{"action":"current_release","release_id":"111"}\n'); await entered.promise;
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await first;
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('disconnected operation receives cancellation and retains lease until it settles', async () => {
  const socket = path(), entered = deferred(), finish = deferred(), closed = deferred(); let observedSignal;
  const acquireContextLease = createHostedContextLease();
  const config = options({ acquireContextLease });
  config.fixtureOperations.prepare_guest_form = async (context, value, { signal }) => { observedSignal = signal; entered.resolve(); await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const close = await listenHostedGuestPreparation({}, socket, config);
  const client = connect(socket); client.on('error', () => {}); client.on('close', closed.resolve);
  client.on('connect', () => client.write(request('prepare_guest_form')));
  try {
    await entered.promise;
    const aborted = new Promise(resolve => observedSignal.addEventListener('abort', resolve, { once: true }));
    client.destroy(); await closed.promise; await aborted;
    assert.equal(observedSignal.aborted, true);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'current_dom_evidence_only');
  } finally { finish.resolve(); await close(); }
});

test('timeout aborts but cannot free a still-running operation', async () => {
  const socket = path(), finish = deferred(); let signal;
  const config = options({ timeoutMs: 20 });
  config.fixtureOperations.prepare_guest_form = async (context, value, opts) => { signal = opts.signal; await finish.promise; return { code: 'prior_attempt_unresolved' }; };
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    assert.equal((await exchange(socket, request('prepare_guest_form'))).code, 'request_timed_out');
    assert.equal(signal.aborted, true);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve();
  } finally { finish.resolve(); await close(); }
});

// The collector outlives its disconnected client; the shared context remains
// unavailable until actual DOM read completion, even if the response is gone.
test('disconnected readonly collector cannot release preparation lease early', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise; return {};
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  const client = connect(reader); client.on('error', () => {});
  client.on('connect', () => client.write('{"action":"current_release","release_id":"111"}\n'));
  try {
    await entered.promise; client.destroy();
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('readonly deadline does not release a still-running underlying collector', async () => {
  const guest = path(), reader = `/tmp/guest-reader-${randomUUID().slice(0, 8)}.sock`;
  const acquireContextLease = createHostedContextLease(), entered = deferred(), finish = deferred();
  const context = { pages: () => [{ url: () => 'https://www.discogs.com/sell/release/111', evaluate: async () => {
    entered.resolve(); await finish.promise; return {};
  } }] };
  const closeGuest = await listenHostedGuestPreparation({}, guest, options({ acquireContextLease }));
  const closeReader = await listenHostedReadOnly(context, reader, { acquireContextLease });
  try {
    const result = exchange(reader, '{"action":"current_release","release_id":"111"}\n');
    await entered.promise;
    assert.ok(['request_timed_out', 'observation_unavailable'].includes((await result).code));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(guest, request('prepare_guest_form'))).code, 'prior_attempt_unresolved');
  } finally { finish.resolve(); await closeGuest(); await closeReader(); }
});

test('launcher rejects missing or expanded guest configuration before browser launch', async () => {
  const { launchHostedReadOnlyBrowser } = await import('../scripts/discogs_hosted_browser_runtime.mjs');
  let calls = 0;
  const browser = { launchPersistentContext: async () => { calls++; } };
  for (const config of [{}, { authorize: () => ({}) }, { authorize: () => ({}), accessGuard: () => true, url: 'https://invalid/' }]) {
    await assert.rejects(launchHostedReadOnlyBrowser(browser, '/profile', '/bridge/sniper-read-only.sock', { guestPreparation: config }), /scoped_guest_configuration_required/);
  }
  assert.equal(calls, 0);
});


test('operation returning on cancellation still retains lease until module settlement hook finishes', async () => {
  const socket = path(), entered = deferred(), finish = deferred(), operationReturned = deferred();
  const config = options({ timeoutMs: 20 });
  config.fixtureOperations.prepare_guest_form = async (context, value, { signal }) => {
    entered.resolve(); await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
    operationReturned.resolve(); return { code: 'cancelled' };
  };
  config.fixtureOperations.waitForSettlement = async () => finish.promise;
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    const result = exchange(socket, request('prepare_guest_form')); await entered.promise;
    assert.equal((await result).code, 'request_timed_out'); await operationReturned.promise;
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'read_in_progress');
    finish.resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'current_dom_evidence_only');
  } finally { finish.resolve(); await close(); }
});

test('cart capture output admits only bounded exact-listing public title/grade nodes in authorized capture mode', async () => {
  const socket = path(), config = options();
  config.authorize = async () => ({ mode: 'capture_cart_structure' });
  const structure = { row_count: 1, listing_href: '/sell/item/1366950910',
    title_link: { tag: 'A', text: 'KC & The Sunshine Band - Get Down Tonight', nodes: [
      { path: [0], tag: 'SPAN', text: 'KC & The Sunshine Band', token: 'not exported' }] },
    grade_nodes: [{ path: [0], tag: 'SPAN', label: 'Media', grade: 'P', text: 'Media Condition: Poor (P)', address: 'not exported' },
      { path: [1], tag: 'SPAN', label: 'Sleeve', grade: 'Generic', text: 'Generic' }], address: 'not exported' };
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'cart_structure_captured',
    cart_structure: structure, raw_html: 'not exported', card_entry_allowed: true });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const capture = JSON.stringify({ action: 'probe_guest_evidence', request: { phase: 'capture_cart_structure' } }) + '\n';
  try {
    const result = await exchange(socket, capture); assert.equal(result.status, 'observed');
    assert.equal(result.cart_structure.listing_href, '/sell/item/1366950910');
    assert.equal(result.cart_structure.grade_nodes[0].text, 'Media Condition: Poor (P)');
    assert.equal(JSON.stringify(result).includes('not exported'), false); assert.equal(result.card_entry_allowed, false);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'cart_capture_unverified');
    for (const mutate of [() => { structure.listing_href += '?secret=1'; }, () => { structure.listing_href = '/sell/item/1366950910'; structure.grade_nodes[0].text = 'private address'; },
      () => { structure.grade_nodes[0].text = 'Poor (P)'; structure.title_link.nodes[0].tag = 'SCRIPT'; }]) {
      mutate(); assert.equal((await exchange(socket, capture)).code, 'cart_capture_unverified');
    }
  } finally { await close(); }
});

test('separate fixed preflight action reports only noncommitting diagnostics and cannot masquerade as a readonly probe', async () => {
  const socket = path(), config = options(); let preflights = 0, prepares = 0;
  config.authorize = async () => ({ schema: 'discogs_guest_preflight_authorization_v1', mode: 'cart_checkout_preflight' });
  const result = { status: 'observed', code: 'cart_checkout_preflight_observed', checkout_navigation_issued: true,
    normal_cart_transition_verified: true, checkout_conditions_verified: true, cart_conditions_verified: false,
    initialization_allowed: true, card_initialization_allowed: true, card_entry_allowed: true, payment_allowed: true, final_submission_allowed: true,
    destination_match: true, destination_binding_available: true, address: 'not exported' };
  config.fixtureOperations.cart_checkout_preflight = async () => { preflights++; return result; };
  config.fixtureOperations.probe_guest_evidence = async () => result;
  config.fixtureOperations.prepare_guest_form = async () => { prepares++; throw new Error('forbidden'); };
  const close = await listenHostedGuestPreparation({}, socket, config);
  const message = JSON.stringify({ action: 'cart_checkout_preflight', request: {
    schema: 'discogs_guest_checkout_preflight_v1', phase: 'navigate_checkout_preflight', job_id: randomUUID(), document_ref: randomUUID() } }) + '\n';
  try {
    const observed = await exchange(socket, message); assert.equal(observed.status, 'observed');
    assert.equal(observed.checkout_navigation_issued, true); assert.equal(observed.normal_cart_transition_verified, true);
    assert.equal(observed.initialization_allowed, false); assert.equal(observed.card_initialization_allowed, false);
    assert.equal(observed.final_submission_allowed, false); assert.equal(Object.hasOwn(observed, 'address'), false);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'preflight_authorization_required');
    assert.equal(preflights, 1); assert.equal(prepares, 0);
    result.card_initialization_issued = true;
    const violation = await exchange(socket, message);
    assert.equal(violation.status, 'blocked'); assert.equal(violation.code, 'preflight_scope_violation');
    assert.equal(violation.card_initialization_issued, true); assert.equal(violation.initialization, 'outcome_requires_reconciliation');
    assert.equal(violation.checkout_navigation_issued, true); assert.equal(violation.stop_required, true);
    assert.equal(violation.card_entry_allowed, false); assert.equal(violation.payment_allowed, false);
    assert.equal(violation.final_submission_allowed, false); assert.equal(preflights, 2);
    delete result.card_initialization_issued;
    config.authorize = async () => ({ schema: 'discogs_guest_readonly_authorization_v1' });
    // Listener captures its trusted callback at startup; a separate listener
    // exercises a readonly grant against the navigation action below.
  } finally { await close(); }
  config.authorize = async () => ({ schema: 'discogs_guest_readonly_authorization_v1' });
  const readonlyClose = await listenHostedGuestPreparation({}, socket, config);
  try {
    assert.equal((await exchange(socket, message)).code, 'preflight_authorization_required');
    assert.equal(preflights, 2); assert.equal(prepares, 0);
  } finally { await readonlyClose(); }
});

function cartLayoutFixture() {
  return { schema: 'discogs_cart_layout_v1', collector_gate: { code: 'not_run_private_text_guard',
    form_count: 0, order_container_count: 2, summary_count: 0, address_wrapper_count: 1, address_count: 1,
    form_contains_row: false, container_contains_row: true, container_in_form: false,
    go_count: 1, go_form_present: true, go_form_legacy: false, go_form_contains_row: true, go_in_container: false,
    shipping_select_count: 1, shipping_form_present: true, shipping_form_same_as_go: false, shipping_in_container: true,
    selected_count: 1, selected_index: 2, option_count: 4 },
  ancestry: [{ tag: 'TR', hints: ['order_row'] }, { tag: 'DIV', hints: ['order_container', 'cart_order_prefix'] }],
  tables: [{ hints: ['order_summary'], row_count: 2, rows: [
    { hints: ['order_subtotal'], cell_count: 2, cells: [
      { tag: 'TD', hints: [], child_types: ['#text'], public_text: 'Subtotal' },
      { tag: 'TD', hints: ['order_summary_value'], child_types: ['SPAN'], public_text: '$0.10 USD' }] },
    { hints: ['order_total'], cell_count: 2, cells: [
      { tag: 'TD', hints: [], child_types: ['#text'], public_text: 'Total' },
      { tag: 'TD', hints: ['order_summary_value'], child_types: ['#text'], public_text: '$5.05 USD' }] }] }],
  address_shapes: [{ kind: 'shipping_heading_following', tag: 'DIV', hints: ['shipping_address_prefix'],
    child_types: ['#text', 'BR', '#text', 'BR', '#text'], children: [
      { tag: 'BR', hints: [], child_types: [] }, { tag: 'BR', hints: [], child_types: [] }] }], truncated: false };
}

test('one authorized cart capture returns all layout mismatch and association diagnostics without private text or attributes', async () => {
  const socket = path(), config = options(); config.authorize = async () => ({ mode: 'capture_cart_structure' });
  const layout = cartLayoutFixture();
  layout.address_shapes[0].text = 'NEVER_EXPORT_PRIVATE_ADDRESS'; layout.address_shapes[0].id = 'NEVER_EXPORT_ID';
  layout.address_shapes[0].children[0].value = 'NEVER_EXPORT_INPUT_VALUE';
  layout.collector_gate.form_action = 'NEVER_EXPORT_URL'; layout.tables[0].rows[0].cells[0].html = 'NEVER_EXPORT_HTML';
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'cart_structure_captured',
    cart_structure: { row_count: 1, listing_href: '/sell/item/1366950910', title_link: { tag: 'A', text: 'Get Down Tonight', nodes: [] }, grade_nodes: [] },
    cart_layout: layout });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const capture = JSON.stringify({ action: 'probe_guest_evidence', request: { phase: 'capture_cart_structure' } }) + '\n';
  try {
    const result = await exchange(socket, capture); assert.equal(result.status, 'observed');
    assert.equal(result.cart_layout.collector_gate.form_count, 0); assert.equal(result.cart_layout.collector_gate.order_container_count, 2);
    assert.equal(result.cart_layout.collector_gate.summary_count, 0); assert.equal(result.cart_layout.collector_gate.shipping_form_same_as_go, false);
    assert.equal(result.cart_layout.collector_gate.selected_index, 2);
    assert.deepEqual(result.cart_layout.address_shapes[0].child_types, ['#text', 'BR', '#text', 'BR', '#text']);
    assert.equal(result.cart_layout.tables[0].rows[1].cells[1].public_text, '$5.05 USD');
    assert.equal(JSON.stringify(result).includes('NEVER_EXPORT'), false);
    assert.equal((await exchange(socket, request('probe_guest_evidence'))).code, 'cart_capture_unverified');
  } finally { await close(); }
});

test('cart layout sanitizer rejects the entire capture on unknown text, raw class hints, invalid structure or exceeded bounds', async () => {
  const socket = path(), config = options(); config.authorize = async () => ({ mode: 'capture_cart_structure' });
  let layout;
  config.fixtureOperations.probe_guest_evidence = async () => ({ status: 'observed', code: 'cart_structure_captured',
    cart_structure: { row_count: 1, listing_href: '/sell/item/1366950910', title_link: { tag: 'A', text: 'Get Down Tonight', nodes: [] }, grade_nodes: [] },
    cart_layout: layout });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const capture = JSON.stringify({ action: 'probe_guest_evidence', request: { phase: 'capture_cart_structure' } }) + '\n';
  const mutations = [
    value => { value.tables[0].rows[0].cells[0].public_text = 'Private street with $5.05 USD inside'; },
    value => { value.tables[0].rows[0].cells[1].public_text = '$0.10'; },
    value => { value.tables[0].rows[0].cells[1].public_text = '$12345.00 USD'; },
    value => { value.address_shapes[0].hints = ['shipping_address_123']; },
    value => { value.address_shapes[0].children[0].tag = 'SCRIPT'; },
    value => { value.address_shapes[0].child_types = ['actual private address']; },
    value => { value.collector_gate.form_count = 33; },
    value => { value.collector_gate.selected_index = 32; },
    value => { delete value.collector_gate.go_form_contains_row; },
    value => { value.tables[0].rows[0].cells = Array(5).fill(value.tables[0].rows[0].cells[0]); },
    value => { value.address_shapes = Array(7).fill(value.address_shapes[0]); },
    value => { value.ancestry = Array(9).fill(value.ancestry[0]); },
    value => { value.tables[0].row_count = 0; },
  ];
  try {
    for (const mutate of mutations) { layout = cartLayoutFixture(); mutate(layout);
      const result = await exchange(socket, capture); assert.equal(result.code, 'cart_capture_unverified');
      assert.equal(Object.hasOwn(result, 'cart_layout'), false); assert.equal(Object.hasOwn(result, 'cart_structure'), false);
    }
  } finally { await close(); }
});

test('cart gate diagnostics export only fixed predicate booleans and capped counts', async () => {
  const socket = path(), config = options();
  const countKeys = ['go_count','container_count','row_count','select_count','scoped_summary_count','legacy_count','visible_address_wrapper_count'];
  const booleanKeys = ['discovery_attempted','go_type_submit','go_value_matches','go_text_matches','go_visible','go_enabled',
    'go_not_disabled_property','go_not_disabled_selector','go_not_aria_disabled','form_present','form_current_document','form_connected',
    'container_closest_form_matches','select_form_matches','form_contains_container','form_contains_row','form_contains_go',
    'form_contains_select','form_contains_summary','container_contains_row','container_contains_go','container_contains_select',
    'container_visible','row_visible','select_visible','summary_count_one'];
  const binding = { schema:'cart_binding_predicates_v1',first_failed:'go_value_matches',
    ...Object.fromEntries(countKeys.map(key=>[key,key==='legacy_count'?0:1])),
    ...Object.fromEntries(booleanKeys.map(key=>[key,key!=='go_value_matches' && key!=='discovery_attempted'])) };
  const reader = { schema:'cart_reader_predicates_v1',code:'observed',first_failed:'control_binding_mismatch',
    counts:{legacy_count:0,go_count:1,select_count:1,container_count:1,global_row_count:1,global_summary_count:1} };
  const observed = () => ({status:'observed',code:'current_dom_evidence_only',merchant_route:'cart',
    cart_binding_diagnostic:structuredClone(binding),cart_reader_diagnostic:structuredClone(reader)});
  config.fixtureOperations.probe_guest_evidence = async () => observed();
  const close = await listenHostedGuestPreparation({},socket,config);
  try {
    const result = await exchange(socket,request('probe_guest_evidence'));
    assert.equal(result.status,'observed');
    assert.deepEqual(result.cart_binding_diagnostic,binding); assert.deepEqual(result.cart_reader_diagnostic,reader);
    assert.equal(result.final_submission_allowed,false);
    for (const mutate of [
      value=>{value.cart_binding_diagnostic.private_address='MUST_NOT_EXPORT';},
      value=>{value.cart_binding_diagnostic.go_count=3;},
      value=>{value.cart_binding_diagnostic.go_value_matches='MUST_NOT_EXPORT';},
      value=>{value.cart_binding_diagnostic.first_failed='MUST_NOT_EXPORT';},
      value=>{delete value.cart_binding_diagnostic.form_present;},
      value=>{value.cart_reader_diagnostic.counts.global_row_count=-1;},
      value=>{value.cart_reader_diagnostic.counts.form_action='MUST_NOT_EXPORT';},
      value=>{value.cart_reader_diagnostic.first_failed='MUST_NOT_EXPORT';},
      value=>{value.cart_reader_diagnostic.code='MUST_NOT_EXPORT';},
      value=>{value.merchant_route='enhanced_checkout';},
    ]) {
      config.fixtureOperations.probe_guest_evidence = async () => {const value=observed();mutate(value);return value;};
      const blocked = await exchange(socket,request('probe_guest_evidence'));
      assert.equal(blocked.status,'blocked'); assert.equal(blocked.code,'probe_unverified');
      assert.ok(!JSON.stringify(blocked).includes('MUST_NOT_EXPORT'));
      assert.equal(blocked.cart_binding_diagnostic,undefined); assert.equal(blocked.cart_reader_diagnostic,undefined);
    }
  } finally { await close(); }
});

test('blocked preflight retains bounded predicate diagnostics without leaking destination values or hashes', async () => {
  const socket = path(), config = options();
  config.authorize = async () => ({ schema: 'discogs_guest_preflight_authorization_v1', mode: 'cart_checkout_preflight' });
  config.fixtureOperations.cart_checkout_preflight = async () => ({ status: 'blocked', code: 'visible_scope_evidence_missing',
    checkout_navigation_issued: true, card_initialization_issued: false,
    scope_diagnostic: { schema: 'checkout_scope_predicates_v1', stage: 'checkout', quantity_matches: false,
      destination_matches: false, address_present: true, address_container_count: 1, address_line_count: 4,
      address: 'never export', digest: 'never export' }, address: 'never export' });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const message = JSON.stringify({ action: 'cart_checkout_preflight', request: {
    schema: 'discogs_guest_checkout_preflight_v1', phase: 'navigate_checkout_preflight', job_id: randomUUID(), document_ref: randomUUID() } }) + '\n';
  try {
    const result = await exchange(socket, message);
    assert.deepEqual(result.scope_diagnostic, { schema: 'checkout_scope_predicates_v1', stage: 'checkout',
      quantity_matches: false, destination_matches: false, address_present: true, address_container_count: 1, address_line_count: 4 });
    assert.equal(result.checkout_navigation_issued, true); assert.equal(result.card_initialization_issued, false);
    assert.ok(!JSON.stringify(result).includes('never export')); assert.equal(result.payment_allowed, false);
  } finally { await close(); }
});

function paymentInspectionPayload() {
  const keys = ['provider_dom_qualification', 'provider_frame_and_document', 'provider_explicit_currency',
    'provider_amount_matches_merchant', 'provider_final_all_in_total', 'final_tax_treatment', 'mandatory_charges_finality',
    'provider_shipping_selection', 'provider_delivery_binding', 'provider_terms'];
  return { schema: 'enhanced_payment_readiness_v1', document_bound: true, claim_verified: true, ledger_head_verified: true,
    ledger_state: 'guest_form_observed_repeat_blocked', quote_status: 'unverified',
    merchant_subtotal: { amount: '0.10', currency: 'USD' }, merchant_shipping: { amount: '4.95', currency: 'USD' },
    merchant_total: { amount: '5.05', currency: 'USD' }, selected_shipping_label: 'Economy - Media Mail', merchant_destination_match: null,
    final_quote_verified: false, provider_destination_verified: false, provider_terms_verified: false, payment_enabled: false,
    gates: Object.fromEntries(keys.map(key => [key, false])), missing_evidence: keys,
    missing_profiles: ['provider_final_quote_dom', 'provider_quote_frame_association', 'provider_delivery_destination', 'provider_terms',
      'verified_pay_control', 'verified_selected_payment_method', 'merchant_positive_order_dom'],
    frame_association: { known_buttons_count: 1, known_buttons_associated: true, payment_quote_frame_qualified: false } };
}
const paymentInspectionRequest = () => JSON.stringify({ action: 'inspect_enhanced_payment', request: {
  schema: 'discogs_guest_payment_inspection_v1', job_id: randomUUID(), phase: 'inspect_payment_readiness',
  document_ref: randomUUID(), claim_sha256: 'a'.repeat(64) } }) + '\n';

test('fixed payment inspection IPC returns only finite readiness diagnostics and never accepts another authorization mode', async () => {
  const socket = path(), config = options(); let calls = 0;
  config.authorize = async () => ({ schema: 'discogs_guest_payment_inspection_authorization_v1', mode: 'inspect_enhanced_payment' });
  config.fixtureOperations.inspect_enhanced_payment = async () => { calls++; return { status: 'observed', code: 'enhanced_payment_readiness_observed',
    inspection: paymentInspectionPayload(), source_url: 'https://secret.invalid/token', address: 'NEVER_EXPORT',
    payment_allowed: true, final_submission_allowed: true }; };
  const close = await listenHostedGuestPreparation({}, socket, config);
  try {
    const result = await exchange(socket, paymentInspectionRequest()); assert.equal(result.status, 'observed');
    assert.equal(result.inspection.merchant_total.amount, '5.05'); assert.equal(result.inspection.final_quote_verified, false);
    assert.equal(result.payment_allowed, false); assert.equal(result.final_submission_allowed, false); assert.equal(result.initialization_allowed, false);
    assert.equal(JSON.stringify(result).includes('NEVER_EXPORT'), false); assert.equal(JSON.stringify(result).includes('secret.invalid'), false);
    assert.equal(calls, 1);
  } finally { await close(); }
  config.authorize = async () => ({ schema: 'discogs_guest_readonly_authorization_v1' });
  const another = await listenHostedGuestPreparation({}, socket, config);
  try { assert.equal((await exchange(socket, paymentInspectionRequest())).code, 'payment_inspection_authorization_required'); assert.equal(calls, 1); }
  finally { await another(); }
});

test('payment inspection sanitizer rejects malformed, private, contradictory or incomplete diagnostics atomically', async () => {
  const socket = path(), config = options(); let payload;
  config.authorize = async () => ({ schema: 'discogs_guest_payment_inspection_authorization_v1', mode: 'inspect_enhanced_payment' });
  config.fixtureOperations.inspect_enhanced_payment = async () => ({ status: 'observed', code: 'enhanced_payment_readiness_observed', inspection: payload });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const mutations = [
    value => { value.address = 'NEVER_EXPORT_PRIVATE'; }, value => { value.merchant_total.currency = 'CAD'; },
    value => { value.merchant_total.amount = 'NEVER_EXPORT_PRIVATE'; }, value => { delete value.frame_association.known_buttons_count; },
    value => { value.frame_association.known_buttons_count = null; }, value => { value.frame_association.known_buttons_count = 0; },
    value => { value.missing_evidence = []; }, value => { value.missing_profiles = []; },
    value => { value.quote_status = 'unverified'; value.final_quote_verified = true;
      for (const key of Object.keys(value.gates)) value.gates[key] = true;
      value.missing_evidence = []; value.missing_profiles = ['verified_pay_control', 'verified_selected_payment_method', 'merchant_positive_order_dom'];
      value.provider_destination_verified = true; value.provider_terms_verified = true; },
    value => { value.provider_destination_verified = true; }, value => { value.selected_shipping_label = 'Private\nAddress'; },
  ];
  try { for (const mutate of mutations) { payload = paymentInspectionPayload(); mutate(payload);
    const result = await exchange(socket, paymentInspectionRequest()); assert.equal(result.code, 'payment_inspection_unverified');
    assert.equal(Object.hasOwn(result, 'inspection'), false); assert.equal(JSON.stringify(result).includes('NEVER_EXPORT'), false);
  } } finally { await close(); }
});

test('unexpected readonly inspection effect reports remain truthful scope violations, never fabricated no-effect successes', async () => {
  const socket = path(), config = options(); let effect;
  config.authorize = async () => ({ schema: 'discogs_guest_payment_inspection_authorization_v1', mode: 'inspect_enhanced_payment' });
  config.fixtureOperations.inspect_enhanced_payment = async () => ({ status: 'observed', code: 'enhanced_payment_readiness_observed',
    inspection: paymentInspectionPayload(), [effect]: true });
  const close = await listenHostedGuestPreparation({}, socket, config);
  try { for (effect of ['checkout_navigation_issued', 'card_initialization_issued', 'pay_invoked', 'payment_issued', 'approval_invoked', 'capture_invoked']) {
    const result = await exchange(socket, paymentInspectionRequest()); assert.equal(result.code, 'readonly_scope_violation');
    assert.equal(result.status, 'blocked'); assert.equal(result.stop_required, true); assert.equal(result[effect], true);
    assert.equal(result.payment_allowed, false); assert.equal(result.final_submission_allowed, false);
    assert.equal(Object.hasOwn(result, 'inspection'), false);
  } } finally { await close(); }
});

test('payment inspection fixed blocked codes survive IPC but arbitrary provider or exception text does not', async () => {
  const socket = path(), config = options(); let code;
  config.authorize = async () => ({ schema: 'discogs_guest_payment_inspection_authorization_v1', mode: 'inspect_enhanced_payment' });
  config.fixtureOperations.inspect_enhanced_payment = async () => ({ status: 'blocked', code, address: 'NEVER_EXPORT' });
  const close = await listenHostedGuestPreparation({}, socket, config);
  const codes = ['authorization_required', 'reference_unverified', 'scope_unverified', 'expired', 'replayed', 'busy', 'cancelled',
    'timeout', 'ledger_unverified', 'document_changed', 'unverified', 'unavailable'].map(suffix => `payment_inspection_${suffix}`);
  try {
    for (code of codes) { const result = await exchange(socket, paymentInspectionRequest());
      assert.equal(result.code, code); assert.equal(result.status, 'blocked'); assert.equal(result.payment_allowed, false);
      assert.equal(JSON.stringify(result).includes('NEVER_EXPORT'), false);
    }
    code = 'NEVER_EXPORT_PROVIDER_TOKEN';
    const redacted = await exchange(socket, paymentInspectionRequest()); assert.equal(redacted.code, 'operation_unavailable');
    assert.equal(JSON.stringify(redacted).includes('NEVER_EXPORT'), false);
  } finally { await close(); }
});
