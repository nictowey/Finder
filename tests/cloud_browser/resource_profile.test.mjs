/** Pure filesystem-value fixtures only; no browsers, containers or resource writes. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {collectResourceProfile, getResourceProfile} from './resource_profile.mjs';

function fixture(profile = 'standard') {
  const files = new Map(Object.entries({
    'cpu.max': profile === 'micro' ? '12500 100000\n' : '200000 100000\n',
    'memory.max': `${profile === 'micro' ? 1024 ** 3 : 2 * 1024 ** 3}\n`,
    'memory.swap.max': '0\n',
    'memory.events': 'low 0\nhigh 2\nmax 3\noom 0\noom_kill 0\noom_group_kill 0\n',
    'cpu.stat': 'usage_usec 8000\nuser_usec 6000\nsystem_usec 2000\nnr_periods 100\nnr_throttled 7\nthrottled_usec 25000\n',
    'memory.current': '100000000\n',
    'memory.peak': '123456789\n',
  }));
  const reads = [];
  const read = path => {
    assert.match(path, /^\/sys\/fs\/cgroup\/[a-z.]+$/u);
    const file = path.slice('/sys/fs/cgroup/'.length);
    reads.push(file);
    if (!files.has(file)) throw Error('PRIVATE provider error and path');
    return files.get(file);
  };
  return {files, reads, read, check: () => collectResourceProfile({profile, read})};
}

test('closed profiles provide exact resources and bounded timeouts', () => {
  assert.deepEqual(getResourceProfile('standard'), {name: 'standard', cpus: 2,
    memory_bytes: 2147483648, swap_bytes: 0,
    timeouts: {launch_ms: 30000, navigation_ms: 10000, bridge_ms: 30000}});
  assert.deepEqual(getResourceProfile('micro'), {name: 'micro', cpus: 0.125,
    memory_bytes: 1073741824, swap_bytes: 0,
    timeouts: {launch_ms: 180000, navigation_ms: 60000, bridge_ms: 120000}});
  assert.equal(getResourceProfile(), getResourceProfile('standard'));
  assert.throws(() => {getResourceProfile('micro').timeouts.launch_ms = Infinity;}, TypeError);
  assert.throws(() => {getResourceProfile('micro').cpus = 100;}, TypeError);
});

for (const name of ['Micro', '', 'large', 'constructor', '__proto__', 1, null, {name: 'micro'}])
  test(`unsupported profile ${JSON.stringify(name)} fails before reading`, () => {
    assert.throws(() => collectResourceProfile({profile: name,
      read: () => assert.fail('must not read for invalid profile')}), error => error.code === 'failed');
  });

for (const profile of ['standard', 'micro']) test(`${profile} reports measured limits and cumulative counters`, () => {
  const f = fixture(profile), result = f.check();
  assert.equal(result.status, 'observed');
  assert.equal(result.profile, profile);
  assert.equal(result.limits.cpus, getResourceProfile(profile).cpus);
  assert.equal(result.limits.memory_bytes, getResourceProfile(profile).memory_bytes);
  assert.equal(result.limits.swap_bytes, 0);
  assert.equal(result.memory_peak_bytes, 123456789);
  assert.equal(result.memory_current_bytes, 100000000);
  assert.equal(result.peak_headroom_bytes, result.limits.memory_bytes - 123456789);
  assert.equal(result.peak_headroom_ratio, result.peak_headroom_bytes / result.limits.memory_bytes);
  assert.equal(result.memory_pressure_observed, true);
  assert.equal(result.cpu_stat.nr_throttled, 7);
  assert.equal(result.cpu_stat.throttled_usec, 25000);
  assert.equal(result.memory_events.max, 3);
  assert.equal(result.vm_equivalence_verified, false);
  assert.deepEqual(f.reads, ['cpu.max', 'memory.max', 'memory.swap.max', 'memory.events', 'cpu.stat',
    'memory.current', 'memory.peak']);
});

test('CPU ratio accepts a different period without assuming a host architecture', () => {
  const f = fixture('micro'); f.files.set('cpu.max', '25000 200000\n');
  assert.equal(f.check().limits.cpu_period_usec, 200000);
});

for (const [name, file, value] of [
  ['unlimited CPU', 'cpu.max', 'max 100000'],
  ['wrong CPU quota', 'cpu.max', '100000 100000'],
  ['almost the requested CPU quota', 'cpu.max', '199999 100000'],
  ['unlimited memory', 'memory.max', 'max'],
  ['wrong memory', 'memory.max', '1073741824'],
  ['unlimited swap', 'memory.swap.max', 'max'],
  ['extra swap', 'memory.swap.max', '4096'],
  ['OOM events', 'memory.events', 'low 0\nhigh 0\nmax 0\noom 1\noom_kill 0'],
  ['OOM kills', 'memory.events', 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 1'],
  ['OOM group kills', 'memory.events', 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 1'],
]) test(`rejects ${name}`, () => {
  const f = fixture(); f.files.set(file, value);
  assert.throws(f.check, error => error.code === 'failed');
});

for (const file of ['cpu.max', 'memory.max', 'memory.swap.max', 'memory.events', 'cpu.stat', 'memory.current', 'memory.peak'])
  test(`unreadable ${file} fails without leaking the underlying error`, () => {
    const f = fixture(); f.files.delete(file);
    assert.throws(f.check, error => error.code === 'unavailable' &&
      error.message.includes(file) && !error.message.includes('PRIVATE'));
  });

for (const [name, file, value] of [
  ['empty file', 'memory.peak', ' \n'],
  ['oversized file', 'memory.peak', '1'.repeat(8193)],
  ['non-string input', 'memory.peak', 42],
  ['negative integer', 'memory.peak', '-1'],
  ['unsafe integer', 'memory.peak', '9007199254740992'],
  ['fractional memory', 'memory.max', '2147483648.0'],
  ['extra CPU field', 'cpu.max', '200000 100000 extra'],
  ['zero quota', 'cpu.max', '0 100000'],
  ['zero period', 'cpu.max', '200000 0'],
  ['missing OOM counter', 'memory.events', 'low 0\nhigh 0\nmax 0\noom 0'],
  ['duplicate OOM counter', 'memory.events', 'low 0\nhigh 0\nmax 0\noom 0\noom 0\noom_kill 0'],
  ['malformed counter', 'memory.events', 'low 0\nhigh 0\nmax 0\noom SECRET\noom_kill 0'],
  ['missing throttling data', 'cpu.stat', 'usage_usec 1\nuser_usec 1\nsystem_usec 0'],
  ['excessive keys', 'cpu.stat', Array.from({length: 65}, (_, i) => `field${i} 0`).join('\n')],
]) test(`unavailable evidence: ${name}`, () => {
  const f = fixture(); f.files.set(file, value);
  assert.throws(f.check, error => error.code === 'unavailable' && !error.message.includes('SECRET'));
});

test('key order is irrelevant; optional supported counters are not fabricated', () => {
  const f = fixture();
  f.files.set('memory.events', 'oom_kill 0\nmax 0\nlow 0\noom 0\nhigh 0');
  f.files.set('cpu.stat', f.files.get('cpu.stat') + 'nr_bursts 2\nburst_usec 100\nfuture_kernel_field 99\n');
  const result = f.check();
  assert.equal(Object.hasOwn(result.memory_events, 'oom_group_kill'), false);
  assert.equal(result.cpu_stat.nr_bursts, 2);
  assert.equal(result.cpu_stat.burst_usec, 100);
  assert.equal(Object.hasOwn(result.cpu_stat, 'future_kernel_field'), false);
});

test('peak is reported honestly even above memory.max; kernel allows temporary overshoot', () => {
  const f = fixture(); f.files.set('memory.peak', '2200000000');
  const result = f.check();
  assert.equal(result.memory_peak_bytes, 2200000000);
  assert.equal(result.peak_headroom_bytes, 2147483648 - 2200000000);
  assert.ok(result.peak_headroom_ratio < 0);
});

test('micro is not silently replaced by the standard allocation', () => {
  const f = fixture('micro'); f.files.set('cpu.max', '200000 100000');
  assert.throws(f.check, error => error.code === 'failed');
});

test('no pressure is reported only from measured zero low/high/max event counts', () => {
  const f = fixture(); f.files.set('memory.events', 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0');
  assert.equal(f.check().memory_pressure_observed, false);
  for (const field of ['low', 'high', 'max']) {
    f.files.set('memory.events', `low ${field === 'low' ? 1 : 0}\nhigh ${field === 'high' ? 1 : 0}\n` +
      `max ${field === 'max' ? 1 : 0}\noom 0\noom_kill 0`);
    assert.equal(f.check().memory_pressure_observed, true);
  }
});
