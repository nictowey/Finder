/* Real loopback-service DOM check; no browser, provider, or registry access. */
'use strict';
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const {TextEncoder} = require('node:util');
const {JSDOM, VirtualConsole} = require('jsdom');
const origin = process.argv[2];
assert.match(origin, /^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/);
const port = Number(new URL(origin).port);
assert.ok(port <= 65535);
const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = () => { throw new Error('Unapproved network access.'); };
require('node:dns').lookup = () => { throw new Error('DNS is disabled.'); };
const denyDispatcher = {
  dispatch() { throw new Error('External resources are disabled.'); },
  close() { return Promise.resolve(); }, destroy() { return Promise.resolve(); }
};
const loopbackAgent = new http.Agent({keepAlive:false});
loopbackAgent.createConnection = (options) => {
  assert.equal(options.host, '127.0.0.1');
  assert.equal(Number(options.port), port);
  const socket = new net.Socket();
  originalConnect.call(socket, {host:'127.0.0.1', port});
  return socket;
};
const errors = [];
function localRequest(path, options={}) {
  assert.ok(['/', '/api/snapshot', '/api/import', '/api/verdict', '/api/export', '/api/profile', '/api/candidate'].includes(path));
  assert.ok([undefined, 'GET', 'POST'].includes(options.method));
  return new Promise((resolve, reject) => {
    const body = options.body;
    const headers = {...options.headers};
    if (body !== undefined) {
      headers.Origin = origin;
      headers['Content-Length'] = Buffer.byteLength(body);
    }
    const request = http.request(origin + path, {method:options.method || 'GET', headers,
      agent:loopbackAgent, timeout:5000
    }, (response) => {
      let result = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { result += chunk; });
      response.on('end', () => resolve({ok:response.statusCode === 200, status:response.statusCode,
        json:async () => JSON.parse(result), text:async () => result}));
    });
    request.on('error', reject);
    request.on('timeout', () => request.destroy(new Error('Local request timed out.')));
    if (body !== undefined) request.write(body);
    request.end();
  });
}
async function openPage() {
  const response = await localRequest('/');
  assert.equal(response.status, 200);
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => errors.push(error));
  const dom = new JSDOM(await response.text(), {url:origin + '/', runScripts:'dangerously',
    resources:{dispatcher:denyDispatcher}, virtualConsole,
    beforeParse(window) { window.fetch=localRequest; window.TextEncoder=TextEncoder; }});
  await settled(dom.window);
  return dom;
}
async function settled(window) {
  for (let i=0; i<500; i++) {
    await new Promise((resolve) => setTimeout(resolve, 10));
    if (window.document.getElementById('cases').getAttribute('aria-busy') === 'false') return;
  }
  throw new Error('Local UI did not settle.');
}
function blueCard(document) {
  return [...document.querySelectorAll('.case')].find((card) =>
    card.textContent.includes('Case example-blue'));
}
function field(document,id,value) { document.getElementById(id).value=value; }
function submit(dom,id) { dom.window.document.getElementById(id).dispatchEvent(new dom.window.Event('submit',{cancelable:true})); }
function click(document,text) { const button=[...document.querySelectorAll('button')].find(node=>node.textContent === text); assert.ok(button,text); button.click(); }
const readSaved=async()=>await (await localRequest('/api/snapshot')).json();
const exportSaved=async()=>await (await localRequest('/api/export')).json();
(async () => {
  let dom=await openPage();let document=dom.window.document;
  if(process.argv[3] !== '--verify') {
    assert.match(document.getElementById('summary').textContent,/workspace is empty/);
    Object.entries({'profile-source':'synthetic','target-artist':'Example Ensemble','target-album':'Offline Horizons',
      'target-colors':'Blue','settings-maximum_subtotal':'30.00','settings-gamble_max':'15.00'}).forEach(([id,value])=>field(document,id,value));
    submit(dom,'profile-form');submit(dom,'profile-form');await settled(dom.window);
    let saved=await readSaved();assert.equal(saved.rows.length,0);assert.equal(saved.schema_version,1);
    assert.deepEqual(saved.target.formats,[]);assert.deepEqual(saved.settings.tells,[]);
    assert.equal((await exportSaved()).observations.length,0);
    // Detailed profile fields require an explicit version upgrade through the UI.
    document.getElementById('detailed').checked=true;
    document.getElementById('detailed').dispatchEvent(new dom.window.Event('change'));
    Object.entries({'target-country':'US','target-release_year':'2024','target-editions':'Limited Edition',
      'target-cover_edition':'Alpha','target-required_components':'signed_insert','settings-condition_ids':'1000',
      'settings-alert_mode':'strict','settings-auction_alert_minutes':'90'}).forEach(([id,value])=>field(document,id,value));
    submit(dom,'profile-form');await settled(dom.window);saved=await readSaved();assert.equal(saved.schema_version,2);
    document.getElementById('new-comparison').click();
    field(document,'comparison-id','invented-black');field(document,'comparison-colors','Black');
    submit(dom,'comparison-form');await settled(dom.window);
    click(document,'Edit invented-black');field(document,'comparison-cover_edition','Beta');submit(dom,'comparison-form');await settled(dom.window);
    assert.equal((await readSaved()).alternatives[0].cover_edition,'Beta');
    click(document,'Edit invented-black');document.getElementById('remove-comparison').click();await settled(dom.window);
    assert.equal((await readSaved()).alternatives,null);
    document.getElementById('new-comparison').click();field(document,'comparison-id','invented-black');field(document,'comparison-colors','Black');
    submit(dom,'comparison-form');await settled(dom.window);
    for(const [id,color] of [['blue','Blue'],['unclear',''],['black','Black']]) {
      document.getElementById('new-candidate').click();
      Object.entries({'candidate-id':`example-${id}`,'candidate-title':`Example Ensemble Offline Horizons ${color} vinyl`,
        'candidate-observed_at':'2026-01-02T03:04:05Z','candidate-colors':color}).forEach(([key,value])=>field(document,key,value));
      if(id === 'blue') {
        field(document,'candidate-details_observed_at','2026-01-02T03:04:05Z');
        field(document,'candidate-current_price','20.00');field(document,'candidate-currency','EUR');
        field(document,'candidate-shipping_cost','4.00');field(document,'candidate-shipping_currency','USD');
      }
      submit(dom,'case-form');submit(dom,'case-form');await settled(dom.window);
      assert.match(document.getElementById('status').textContent,/Candidate observation saved/,document.getElementById('status').textContent);
    }
    saved=await readSaved();assert.equal(saved.rows.length,3);
    let row=saved.rows.find(row=>row.id === 'example-blue');
    assert.match(row.listing.details_observed_at,/2026-01-02T03:04:05/);assert.equal(row.listing.price_kind,'unknown');
    assert.deepEqual(row.listing.source_metadata.manual_input.formats,[]);assert.equal(row.review.subtotal,null);
    assert.equal(row.listing.currency,'EUR');assert.equal(row.listing.shipping_currency,'USD');
    blueCard(document).querySelector('.verdict').click();await settled(dom.window);
    const firstJudgment=(await exportSaved()).judgments[0].data;
    // Cap editing from 30 to 25 persists and leaves the original judgment intact.
    field(document,'settings-maximum_subtotal','25.00');submit(dom,'profile-form');await settled(dom.window);
    saved=await readSaved();assert.equal(saved.settings.maximum_subtotal,'25.00');
    assert.deepEqual((await exportSaved()).judgments[0].data,firstJudgment);
    // A changed same-time observation fails without poisoning subsequent correction.
    const observe=[...blueCard(document).querySelectorAll('button')].find(node=>node.textContent === 'Record another observation');observe.click();
    assert.equal(document.getElementById('candidate-observed_at').value,'2026-01-02T03:04:05Z');
    field(document,'candidate-title','Example Ensemble Offline Horizons blue vinyl new observation');submit(dom,'case-form');await settled(dom.window);
    assert.match(document.getElementById('status').textContent,/actual later observation time/);
    assert.equal((await exportSaved()).observations.length,3);
    document.getElementById('refresh').click();await settled(dom.window);
    assert.match(document.getElementById('candidate-title').value,/new observation/);
    field(document,'candidate-observed_at','2026-01-02T04:04:05Z');submit(dom,'case-form');await settled(dom.window);
    assert.match(document.getElementById('status').textContent,/Candidate observation saved/);
    assert.equal((await exportSaved()).observations.length,4);assert.equal((await readSaved()).settings.maximum_subtotal,'25.00');
    assert.deepEqual((await exportSaved()).judgments[0].data,firstJudgment);
    // Another tab can append a newer observation without changing profile revision.
    const other=await openPage();
    [...blueCard(other.window.document).querySelectorAll('button')].find(node=>node.textContent === 'Record another observation').click();
    field(other.window.document,'candidate-observed_at','2026-01-02T05:04:05Z');
    field(other.window.document,'candidate-title','Example Ensemble Offline Horizons blue vinyl second tab');
    ['candidate-details_observed_at','candidate-colors','candidate-current_price','candidate-currency','candidate-shipping_cost','candidate-shipping_currency'].forEach(id=>field(other.window.document,id,''));
    submit(other,'case-form');await settled(other.window);
    const unknown=(await readSaved()).rows.find(row=>row.id === 'example-blue').listing;
    assert.equal(unknown.details_observed_at,null);assert.equal(unknown.current_price,null);assert.equal(unknown.shipping_cost,null);
    assert.deepEqual(unknown.item_specifics,{});
    field(document,'candidate-title','Example Ensemble Offline Horizons blue vinyl final observation');
    field(document,'candidate-observed_at','2026-01-02T06:04:05Z');submit(dom,'case-form');await settled(dom.window);
    assert.match(document.getElementById('status').textContent,/Saved state changed/);
    assert.match(document.getElementById('candidate-title').value,/final observation/);
    assert.equal((await exportSaved()).observations.length,5);
    document.getElementById('rebase-candidate').click();submit(dom,'case-form');await settled(dom.window);
    assert.equal((await exportSaved()).observations.length,6);
    assert.equal((await readSaved()).settings.maximum_subtotal,'25.00');
    assert.deepEqual((await exportSaved()).judgments[0].data,firstJudgment);
    // Cross-tab profile edits conflict; retained drafts cannot silently overwrite caps.
    field(other.window.document,'settings-maximum_subtotal','24.00');submit(other,'profile-form');await settled(other.window);
    field(document,'settings-maximum_subtotal','23.00');submit(dom,'profile-form');await settled(dom.window);
    assert.match(document.getElementById('status').textContent,/Saved state changed/);
    assert.equal(document.getElementById('settings-maximum_subtotal').value,'23.00');assert.equal((await readSaved()).settings.maximum_subtotal,'24.00');
    document.getElementById('reload-profile').click();field(document,'settings-maximum_subtotal','25.00');submit(dom,'profile-form');await settled(dom.window);
    other.window.close();
    // Rich fields and current caps hydrate after a genuine page reload.
    dom.window.close();dom=await openPage();document=dom.window.document;
  }
  const saved=await readSaved();
  assert.equal(saved.rows.length,3);assert.equal(saved.settings.maximum_subtotal,'25.00');
  assert.equal(document.getElementById('settings-maximum_subtotal').value,'25.00');
  assert.equal(document.getElementById('target-cover_edition').value,'Alpha');
  assert.equal(document.getElementById('target-required_components').value,'signed_insert');
  assert.equal(document.getElementById('settings-condition_ids').value,'1000');
  assert.equal(document.getElementById('settings-alert_mode').value,'strict');
  assert.equal(document.getElementById('candidate-observed_at').value,'');
  assert.equal(blueCard(document).querySelector('.verdict').getAttribute('aria-pressed'),'true');
  const record=await exportSaved();assert.equal(record.export_version,1);assert.equal(record.observations.length,6);
  assert.equal(record.judgments[0].data.verdict,'mine');assert.equal(record.target_history[0].data.input.schema_version,undefined);
  assert.equal(errors.length,0,errors.map(String).join('\n'));dom.window.close();
  console.log(process.argv[3] === '--verify' ? 'Real local service: profile, rich fields, caps, observations and first judgment survive restart.' :
    'Real local service: JSON-free profile/candidate forms, comparisons, unknowns, cap edits, collision correction, cross-tab conflicts, retained drafts and page reload passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
