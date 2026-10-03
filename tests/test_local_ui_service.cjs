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
  assert.ok(['/', '/api/snapshot', '/api/import', '/api/verdict', '/api/export'].includes(path));
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
(async () => {
  let dom = await openPage();
  let document = dom.window.document;
  if (process.argv[3] !== '--verify') {
    assert.match(document.getElementById('summary').textContent, /workspace is empty/);
    document.getElementById('example').click();
    document.getElementById('save').click();
    await settled(dom.window);
    assert.equal(document.querySelectorAll('.case').length, 3, document.getElementById('status').textContent);
    blueCard(document).querySelector('.verdict').click();
    await settled(dom.window);
    assert.match(document.getElementById('status').textContent, /Verdict saved/);
    dom.window.close();
    dom = await openPage();
    document = dom.window.document;
  }
  assert.equal(blueCard(document).querySelector('.verdict').getAttribute('aria-pressed'), 'true');
  const record = await (await localRequest('/api/export')).json();
  assert.equal(record.export_version, 1);
  assert.ok(JSON.stringify(record).includes('"mine"'));
  assert.equal(errors.length, 0, errors.map(String).join('\n'));
  dom.window.close();
  console.log(process.argv[3] === '--verify' ?
    'Real local service: verdict survives server restart.' :
    'Real local service: UI import, evidence rendering, verdict, page reload, and export passed.');
})().catch((error) => { console.error(error); process.exitCode=1; });
