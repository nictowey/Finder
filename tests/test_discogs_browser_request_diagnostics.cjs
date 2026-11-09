const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test, before } = require('node:test');
let startBrowserRequestDiagnostics, REQUEST_DIAGNOSTIC_EVENT_CAP, REQUEST_DIAGNOSTIC_DURATION_MS;
before(async () => ({ startBrowserRequestDiagnostics, REQUEST_DIAGNOSTIC_EVENT_CAP,
  REQUEST_DIAGNOSTIC_DURATION_MS } = await import('../scripts/discogs_browser_request_diagnostics.mjs')));
const secret = 'https://private.invalid/?token=SECRET card=4111111111111111 address=PRIVATE';
function fixture(options = {}) {
  const context = new EventEmitter(), reports = []; let time = 1000;
  for (const key of ['pages', 'newPage', 'cookies', 'storageState', 'route', 'request', 'close']) {
    Object.defineProperty(context, key, { get() { throw new Error(secret); } });
  }
  const value = startBrowserRequestDiagnostics(context, { enabled: true,
    clock: () => time, report: x => reports.push(x), ...options });
  return { context, reports, value, time: x => { time = x; } };
}
function request(errorText) {
  return new Proxy({ failure: () => ({ errorText }) }, { get(target, key) {
    if (key !== 'failure') throw new Error(secret); return target[key];
  } });
}
function response(status) {
  return new Proxy({ status: () => status }, { get(target, key) {
    if (key !== 'status') throw new Error(secret); return target[key];
  } });
}
test('disabled default touches no context, clock or reporter and cannot be enabled by truthy strings', () => {
  const forbidden = new Proxy({}, { get() { throw new Error(secret); } });
  const d = startBrowserRequestDiagnostics(forbidden, { clock: () => { throw new Error(secret); },
    report: () => { throw new Error(secret); } });
  d.stop(); assert.equal(d.snapshot().enabled, false); assert.equal(d.snapshot().stop_reason, 'disabled');
  assert.throws(() => startBrowserRequestDiagnostics(forbidden, { enabled: 'true' }), /boolean_required/);
});
test('only fixed status/failure buckets and coarse local timing survive arbitrary sensitive payloads', () => {
  const f = fixture();
  f.context.emit('requestfailed', request('net::ERR_NAME_NOT_RESOLVED ' + secret));
  f.context.emit('requestfailed', request('SEC_ERROR_UNKNOWN_ISSUER: ' + secret));
  f.context.emit('requestfailed', request(secret));
  for (const code of [200, 302, 401, 403, 429, 404, 503, 101, secret]) f.context.emit('response', response(code));
  f.time(1359); f.value.stop();
  assert.equal(f.reports.length, 1); const out = JSON.parse(f.reports[0]);
  assert.equal(out.elapsed_ms, 300); assert.equal(out.failure_counts.dns, 1);
  assert.equal(out.failure_counts.tls, 1); assert.equal(out.failure_counts.other, 1);
  assert.deepEqual(out.response_counts, { success_2xx: 1, redirect_3xx: 1, unauthorized_401: 1,
    forbidden_403: 1, rate_limited_429: 1, other_4xx: 1, server_5xx: 1, other_status: 2 });
  assert.ok(f.reports[0].length < 8192); assert.doesNotMatch(f.reports[0], /SECRET|411111|private|PRIVATE|https/);
  out.failure_counts.dns = 999; assert.equal(f.value.snapshot().failure_counts.dns, 1);
});
test('bounded aggregate counters detach at the event cap without inspecting later events or retrying', () => {
  const f = fixture(); let reads = 0;
  for (let i = 0; i < REQUEST_DIAGNOSTIC_EVENT_CAP + 100; i++) {
    f.context.emit('response', { status() { reads++; return 403; } });
  }
  assert.equal(reads, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(f.value.snapshot().observed_events, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(f.value.snapshot().response_counts.forbidden_403, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(f.value.snapshot().stop_reason, 'event_cap');
  assert.equal(f.context.listenerCount('response'), 0); assert.equal(f.context.listenerCount('requestfailed'), 0);
  f.value.stop(); assert.equal(f.reports.length, 1);
});
test('duration cap detaches with fixed timing and no navigation or extra reports', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture();
  f.time(1000 + REQUEST_DIAGNOSTIC_DURATION_MS + 30000);
  t.mock.timers.tick(REQUEST_DIAGNOSTIC_DURATION_MS);
  assert.equal(f.value.snapshot().stop_reason, 'duration_cap');
  assert.equal(f.value.snapshot().elapsed_ms, REQUEST_DIAGNOSTIC_DURATION_MS);
  assert.equal(f.context.listenerCount('response'), 0); assert.equal(f.reports.length, 1);
});
test('delayed timer delivery cannot admit another event after the duration or a lost clock', () => {
  for (const time of [61000, NaN]) {
    const f = fixture(); f.time(time);
    f.context.emit('requestfailed', new Proxy({}, { get() { throw new Error('expired event inspected'); } }));
    assert.equal(f.value.snapshot().observed_events, 0);
    assert.equal(f.value.snapshot().stop_reason, Number.isNaN(time) ? 'clock_unavailable' : 'duration_cap');
    assert.equal(f.reports.length, 1);
  }
});
test('reentrant event delivery is ignored before reading any nested metadata', () => {
  const f = fixture(); let reads = 0;
  f.context.emit('requestfailed', { failure() {
    reads++;
    for (let i = 0; i < REQUEST_DIAGNOSTIC_EVENT_CAP; i++) {
      f.context.emit('response', { status() { reads++; return 200; } });
    }
    return { errorText: 'NS_ERROR_UNKNOWN_HOST' };
  } });
  assert.equal(reads, 1); assert.equal(f.value.snapshot().observed_events, 1);
  assert.equal(f.value.snapshot().failure_counts.dns, 1);
  assert.equal(f.value.snapshot().response_counts.success_2xx, 0);
  f.value.stop(); assert.equal(f.reports.length, 1);
});
test('a cap reached during listener attachment cannot leave newly added listeners behind', () => {
  const context = new EventEmitter(), reports = []; let reads = 0;
  context.on('newListener', (name, fn) => {
    if (name === 'requestfailed') for (let i = 0; i < REQUEST_DIAGNOSTIC_EVENT_CAP + 1; i++) {
      fn({ failure() { reads++; return { errorText: 'NS_ERROR_UNKNOWN_HOST' }; } });
    }
  });
  const d = startBrowserRequestDiagnostics(context, { enabled: true, report: x => reports.push(x) });
  assert.equal(d.snapshot().stop_reason, 'event_cap'); assert.equal(reads, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(context.listenerCount('requestfailed'), 0); assert.equal(context.listenerCount('response'), 0);
  assert.equal(reports.length, 1);
});
test('a backward clock after a valid event stops before reading more metadata', () => {
  const f = fixture(); f.time(1400); f.context.emit('response', response(200)); f.time(1200);
  f.context.emit('response', new Proxy({}, { get() { throw new Error('backward-clock read'); } }));
  const s = f.value.snapshot(); assert.equal(s.stop_reason, 'clock_unavailable');
  assert.equal(s.timing_available, false); assert.equal(s.observed_events, 1);
  assert.equal(s.first_event_ms, 400); assert.equal(s.last_event_ms, 400);
});
test('malformed/oversized errors, throwing readers and bad clocks never disclose values or claim known timing', () => {
  const f = fixture();
  f.context.emit('requestfailed', request('NS_ERROR_UNKNOWN_HOST ' + secret.repeat(20)));
  f.context.emit('requestfailed', { failure() { throw new Error(secret); } });
  f.context.emit('response', { status() { throw new Error(secret); } });
  f.time(-1); f.value.stop(); const out = f.value.snapshot();
  assert.equal(out.failure_counts.other, 1); assert.equal(out.failure_counts.observation_error, 2);
  assert.equal(out.timing_available, false); assert.equal(out.elapsed_ms, null);
  assert.doesNotMatch(f.reports.join(''), /SECRET|411111|PRIVATE|https/);
});
test('attachment/report/detach failures close the observer without raw exception logs or subsequent reads', () => {
  const context = new EventEmitter(), original = context.on.bind(context), retained = [];
  context.on = (name, fn) => { retained.push(fn); if (name === 'response') throw new Error(secret); return original(name, fn); };
  context.off = () => { throw new Error(secret); };
  const d = startBrowserRequestDiagnostics(context, { enabled: true, report() { throw new Error(secret); } });
  assert.equal(d.snapshot().stop_reason, 'attachment_failed');
  for (const fn of retained) fn(new Proxy({}, { get() { throw new Error('closed observer read'); } }));
  assert.equal(d.snapshot().observed_events, 0); d.stop();
});

function categorized(url, status = 403, errorText = 'ERR_BLOCKED_BY_CLIENT') {
  return new Proxy({ url: () => url, status: () => status, failure: () => ({ errorText }) }, {
    get(target, key) { if (!Object.hasOwn(target, key)) throw new Error(secret); return target[key]; },
  });
}
test('fixed resource groups associate status/error counts and coarse timing without retaining URLs', () => {
  const f = fixture();
  const events = [
    ['challenge_bootstrap', 'https://www.discogs.com/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?token=SECRET#PRIVATE'],
    ['challenge_bootstrap', 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=SECRET'],
    ['pat', 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/pat/SECRET?card=4111111111111111'],
    ['pat', 'https://www.discogs.com/cdn-cgi/challenge-platform/h/g/pat/SECRET'],
    ['diagnostic_dns_probe', 'https://SECRET.challenges.cloudflare.com/PRIVATE?token=SECRET'],
    ['diagnostic_dns_probe', 'https://random.dnstest.dev/PRIVATE'],
    ['other', secret],
  ];
  f.time(1249);
  for (const [group, url] of events) f.context.emit('response', categorized(url, group === 'pat' ? 401 : 403));
  f.time(1481);
  for (const [, url] of events) f.context.emit('requestfailed', categorized(url));
  f.value.stop(); const out = f.value.snapshot();
  assert.equal(out.schema, 'sniper_browser_request_diagnostics_v2');
  assert.deepEqual(Object.keys(out.resource_counts), ['challenge_bootstrap', 'pat', 'diagnostic_dns_probe', 'other']);
  for (const [group, expected] of [['challenge_bootstrap', 2], ['pat', 2], ['diagnostic_dns_probe', 2], ['other', 1]]) {
    const counts = out.resource_counts[group];
    assert.equal(counts.observed_events, expected * 2);
    assert.equal(counts.failure_counts.blocked, expected);
    assert.equal(counts.response_counts[group === 'pat' ? 'unauthorized_401' : 'forbidden_403'], expected);
    assert.equal(counts.first_event_ms, 200); assert.equal(counts.last_event_ms, 400);
  }
  assert.equal(out.observed_events, 14);
  assert.doesNotMatch(JSON.stringify(out), /SECRET|411111|PRIVATE|https|cloudflare|discogs|dnstest/);
  out.resource_counts.pat.failure_counts.blocked = 99;
  assert.equal(f.value.snapshot().resource_counts.pat.failure_counts.blocked, 2);
});
test('unknown or deceptive resource metadata remains other, including query-only category names', () => {
  const f = fixture();
  const urls = [
    'https://attacker.invalid/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1',
    'https://challenges.cloudflare.com.attacker.invalid/turnstile/v0/api.js',
    'https://fake-dnstest.dev/', 'https://dnstest.dev/',
    'https://www.discogs.com/?path=/cdn-cgi/challenge-platform/h/b/pat/secret',
    'https://www.discogs.com/#/cdn-cgi/challenge-platform/h/b/orchestrate/secret',
    'https://SECRET@challenges.cloudflare.com/turnstile/v0/api.js',
    'http://challenges.cloudflare.com/turnstile/v0/api.js',
    'https://challenges.cloudflare.com:8443/turnstile/v0/api.js',
    'https://challenges.cloudflare.com/turnstile/v0/api.js.evil',
    'https://www.discogs.com/SECRET\\cdn-cgi/challenge-platform/h/b/pat/x',
    'https://www.discogs.com/\ncdn-cgi/challenge-platform/h/b/pat/x',
    secret.repeat(1000), null, {},
  ];
  for (const url of urls) f.context.emit('response', categorized(url, 200));
  f.context.emit('response', { url() { throw new Error(secret); }, status() { return 200; } });
  f.value.stop(); const out = f.value.snapshot();
  assert.equal(out.resource_counts.other.observed_events, urls.length + 1);
  assert.equal(out.resource_counts.challenge_bootstrap.observed_events, 0);
  assert.equal(out.resource_counts.pat.observed_events, 0);
  assert.equal(out.resource_counts.diagnostic_dns_probe.observed_events, 0);
  assert.doesNotMatch(f.reports.join(''), /SECRET|PRIVATE|411111|attacker|https/);
});
test('URL callbacks cannot reenter, read payload after shutdown/expiry or exceed shared event cap', () => {
  for (const stop of ['stop', 'expire']) {
    const f = fixture(); let payloadReads = 0;
    f.context.emit('response', { url() {
      f.context.emit('response', new Proxy({}, { get() { throw new Error('nested metadata read'); } }));
      if (stop === 'stop') f.value.stop(); else f.time(61000);
      return 'https://challenges.cloudflare.com/turnstile/v0/api.js';
    }, status() { payloadReads++; return 403; } });
    assert.equal(payloadReads, 0); assert.equal(f.value.snapshot().observed_events, 0);
    assert.equal(f.reports.length, 1);
  }
  const f = fixture(); let urlReads = 0, payloadReads = 0;
  for (let i = 0; i < REQUEST_DIAGNOSTIC_EVENT_CAP + 20; i++) f.context.emit('response', {
    url() { urlReads++; return 'https://challenges.cloudflare.com/turnstile/v0/api.js?token=SECRET'; },
    status() { payloadReads++; return 403; },
  });
  assert.equal(urlReads, REQUEST_DIAGNOSTIC_EVENT_CAP); assert.equal(payloadReads, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(f.value.snapshot().resource_counts.challenge_bootstrap.observed_events, REQUEST_DIAGNOSTIC_EVENT_CAP);
  assert.equal(f.context.listenerCount('response'), 0); assert.equal(f.reports.length, 1);
});
test('slow or stopping payload callback does not record post-deadline events or resume observation', () => {
  for (const stop of ['stop', 'expire']) {
    const f = fixture();
    f.context.emit('requestfailed', { url: () => 'https://x.dnstest.dev/', failure() {
      if (stop === 'stop') f.value.stop(); else f.time(61000);
      return { errorText: 'ERR_NAME_NOT_RESOLVED ' + secret };
    } });
    assert.equal(f.value.snapshot().observed_events, 0);
    assert.equal(f.value.snapshot().resource_counts.diagnostic_dns_probe.observed_events, 0);
    assert.equal(f.context.listenerCount('requestfailed'), 0); assert.equal(f.reports.length, 1);
  }
});
