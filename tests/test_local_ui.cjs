/* Offline DOM checks only. Run with an already installed jsdom; never fetch dependencies. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {TextEncoder} = require('node:util');
const {JSDOM, VirtualConsole} = require('jsdom');
const html = fs.readFileSync(process.argv[2], 'utf8');
const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
let saved = {schema_version:1, revision:0, source:null, target:null, settings:null, rows:[]};
let delayed = null;
let rejectNextVerdict = false;
let failNextImport = false;
const calls = [];
const errors = [];
const denyDispatcher = {
  dispatch() { throw new Error('All resource requests are forbidden in this offline test.'); },
  close() { return Promise.resolve(); },
  destroy() { return Promise.resolve(); }
};
// Backstop every native Node connection as well as jsdom resource requests.
require('node:net').Socket.prototype.connect = () => { throw new Error('Network disabled.'); };
require('node:dns').lookup = () => { throw new Error('DNS disabled.'); };
function response(status, data) {
  return {ok:status === 200, status, json:async () => structuredClone(data)};
}
async function mockFetch(path, options) {
  assert.ok(['/api/snapshot','/api/import','/api/verdict'].includes(path));
  assert.equal(options.redirect, 'error');
  calls.push({path, options});
  if (path === '/api/snapshot') return response(200, saved);
  assert.equal(options.headers['X-Finder-CSRF'], 'dom-test-token');
  assert.equal(options.headers['Content-Type'], 'application/json');
  const input = JSON.parse(options.body);
  if (path === '/api/import') {
    assert.equal(input.schema_version, 1);
    if (delayed) await delayed.promise;
    if (failNextImport) { failNextImport = false; return response(400, {error:'Invalid input.'}); }
    saved = structuredClone(fixture);
    return response(200, saved);
  }
  if (rejectNextVerdict) {
    rejectNextVerdict = false;
    saved.rows[0].verdict = 'other';
    saved.rows[0].review_fingerprint = 'newer-fingerprint';
    return response(409, {error:'This review changed. Refresh, inspect it, then save again.'});
  }
  assert.equal(input.expected_revision, saved.revision);
  assert.equal(input.review_fingerprint, saved.rows[0].review_fingerprint);
  saved.rows[0].verdict = input.verdict;
  saved.rows[0].review_fingerprint = `${input.verdict}-fingerprint`;
  return response(200, saved);
}
function makeWindow() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => errors.push(error));
  return new JSDOM(html, {url:'http://127.0.0.1:18765/', runScripts:'dangerously',
    resources:{dispatcher:denyDispatcher}, virtualConsole,
    beforeParse(window) { window.fetch = mockFetch; window.TextEncoder = TextEncoder; }});
}
async function settled(window) {
  for (let i=0; i<100; i++) {
    await new Promise((resolve) => setImmediate(resolve));
    if (window.document.getElementById('cases').getAttribute('aria-busy') === 'false') return;
  }
  throw new Error('UI did not settle');
}
(async () => {
  let dom = makeWindow();
  let window = dom.window;
  let document = window.document;
  await settled(window);
  assert.match(document.getElementById('summary').textContent, /workspace is empty/);
  document.getElementById('example').click();
  const draft = document.getElementById('draft').value;
  const example = JSON.parse(draft);
  assert.equal(example.source, 'synthetic');
  assert.equal(example.listings.length, 3);
  assert.match(example.listings[0].observed_at, /Z$/);
  let release;
  delayed = {promise:new Promise((resolve) => { release = resolve; })};
  document.getElementById('save').click();
  document.getElementById('save').click();
  document.getElementById('refresh').click();
  assert.equal(calls.filter((call) => call.path === '/api/import').length, 1);
  assert.equal(document.getElementById('save').disabled, true);
  // Keep edits made during a pending save; the response never rewrites the draft.
  document.getElementById('draft').value = draft + '\n';
  release(); delayed = null;
  await settled(window);
  assert.equal(document.getElementById('draft').value, draft + '\n');
  assert.equal(document.querySelectorAll('.case').length, fixture.rows.length);
  assert.match(document.querySelector('.case').textContent, /Your likely-match cap/);
  assert.match(document.querySelector('.case').textContent, /30.00 USD/);
  assert.equal(document.querySelectorAll('.case img').length, 0);
  assert.ok(document.querySelector('.case').textContent.includes('<img src=x onerror=alert(1)>'));
  document.querySelector('.verdict').click();
  document.querySelector('.verdict').click();
  await settled(window);
  assert.equal(calls.filter((call) => call.path === '/api/verdict').length, 1);
  assert.equal(saved.rows[0].verdict, 'mine');
  assert.equal(document.querySelector('.verdict').getAttribute('aria-pressed'), 'true');
  dom.window.close();
  dom = makeWindow(); window = dom.window; document = window.document;
  await settled(window);
  assert.equal(document.querySelector('.verdict').getAttribute('aria-pressed'), 'true');
  // A stale save refreshes the displayed decision and does not replay the user's mutation.
  rejectNextVerdict = true;
  document.querySelectorAll('.verdict')[2].click();
  await settled(window);
  assert.equal(saved.rows[0].verdict, 'other');
  assert.equal(document.querySelectorAll('.verdict')[1].getAttribute('aria-pressed'), 'true');
  assert.match(document.getElementById('status').textContent, /review changed/);
  assert.equal(document.getElementById('status').classList.contains('error'), true);
  // Failed imports keep the typed input and show the server's error.
  document.getElementById('draft').value = draft;
  failNextImport = true;
  document.getElementById('save').click();
  await settled(window);
  assert.equal(document.getElementById('draft').value, draft);
  assert.match(document.getElementById('status').textContent, /Invalid input/);
  // Duplicate keys must reach server validation intact instead of being normalized away.
  const duplicate = '{"schema_version":1,"source":"manual","source":"synthetic","listings":[]}';
  document.getElementById('draft').value = duplicate;
  document.getElementById('save').click();
  await settled(window);
  assert.equal(calls.at(-1).options.body, duplicate);
  // Form input uses explicit observation times and keeps existing draft observations.
  document.getElementById('draft').value = '';
  const fields = {artist:'Written Artist',album:'Written Album','case-id':'written-1',
    'case-title':'Written Artist Written Album blue vinyl',observed:'2026-01-02T03:04:05Z',
    color:'Blue',maximum:'30.00',gamble:'15.00'};
  Object.entries(fields).forEach(([id,value]) => { document.getElementById(id).value = value; });
  document.getElementById('case-form').dispatchEvent(new window.Event('submit', {cancelable:true}));
  const manual = JSON.parse(document.getElementById('draft').value);
  assert.equal(manual.source, 'manual');
  assert.equal(manual.listings[0].observed_at, fields.observed);
  assert.equal(manual.listings[0].details_observed_at, null);
  assert.equal(manual.listings[0].shipping_cost, null);
  assert.equal(manual.settings.tells[0].required, true);
  // File loading must not overwrite a draft edited while its asynchronous read was pending.
  let finishRead;
  const file = {size:100, text:() => new Promise((resolve) => { finishRead=resolve; })};
  Object.defineProperty(document.getElementById('file'), 'files', {value:[file]});
  document.getElementById('file').dispatchEvent(new window.Event('change'));
  document.getElementById('draft').value = 'keep my newer edit';
  finishRead(draft);
  await settled(window);
  assert.equal(document.getElementById('draft').value, 'keep my newer edit');
  assert.match(document.getElementById('status').textContent, /draft changed/);
  assert.equal(document.querySelector('a[download]').getAttribute('href'), '/api/export');
  assert.equal(errors.length, 0, errors.map(String).join('\n'));
  dom.window.close();
  console.log('Local review DOM: import, evidence, caps, verdict, reload, duplicate clicks, conflict, draft retention, hostile text, explicit timestamps, and file race passed.');
})().catch((error) => { console.error(error); process.exitCode=1; });
