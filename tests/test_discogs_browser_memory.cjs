const assert = require('node:assert/strict');
const { test, before } = require('node:test');
let browserMemorySnapshot, startBrowserMemoryLogging;
before(async () => {
  ({ browserMemorySnapshot, startBrowserMemoryLogging } = await import('../scripts/discogs_browser_memory.mjs'));
});
function reader({ current = '400', peak = '700', limit = '1000', events = 'oom 1\noom_kill 1\n' } = {}) {
  const values = { 'memory.current': current, 'memory.peak': peak, 'memory.max': limit, 'memory.events': events };
  return async path => {
    assert.match(path, /^\/sys\/fs\/cgroup\/memory\.(?:current|peak|max|events)$/u);
    return values[path.split('/').pop()];
  };
}
test('fixed local memory counters report pressure without page or process data', async () => {
  const value = await browserMemorySnapshot({ read: reader({ current: '850' }) });
  assert.deepEqual(value, { schema: 'sniper_browser_memory_v1', current_bytes: 850,
    peak_bytes: 700, limit_bytes: 1000, oom_kills: 1, status: 'pressure' });
  assert.equal((await browserMemorySnapshot({ read: reader() })).status, 'within_limit');
});
test('unlimited or unavailable counters never claim a healthy memory limit', async () => {
  assert.equal((await browserMemorySnapshot({ read: reader({ limit: 'max' }) })).status, 'unbounded');
  const value = await browserMemorySnapshot({ read: async () => { throw new Error('secret payload'); } });
  assert.deepEqual(value, { schema: 'sniper_browser_memory_v1', current_bytes: null,
    peak_bytes: null, limit_bytes: null, oom_kills: null, status: 'unknown' });
});
test('malformed, unsafe and duplicate counters are not normalized into reassurance', async () => {
  for (const bad of ['-1', '1.5', '1e6', '9007199254740992', '42\nsecret']) {
    assert.equal((await browserMemorySnapshot({ read: reader({ current: bad }) })).status, 'unknown');
  }
  assert.equal((await browserMemorySnapshot({ read: reader({ limit: '0' }) })).status, 'unknown');
  assert.equal((await browserMemorySnapshot({ read: reader({ events: 'oom_kill 1\noom_kill 2\n' }) })).oom_kills, null);
});
test('stop during pending sample emits nothing and is idempotent', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const reports = [];
  const stop = startBrowserMemoryLogging({ read: () => pending, report: value => reports.push(value) });
  stop(); stop(); finish('100');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(reports, []);
});
test('logging receives only fixed JSON and exceptions do not escape', async () => {
  const reports = [];
  const stop = startBrowserMemoryLogging({ read: reader(), report: value => { reports.push(JSON.parse(value)); throw new Error('secret log failure'); } });
  await new Promise(resolve => setImmediate(resolve));
  stop();
  assert.equal(reports.length, 1);
  assert.deepEqual(Object.keys(reports[0]), ['schema','current_bytes','peak_bytes','limit_bytes','oom_kills','status']);
});
