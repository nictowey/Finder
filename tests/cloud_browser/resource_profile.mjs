/** Read-only cgroup-v2 evidence for isolated, synthetic CI resource profiles.
 * No resource writes, browser options, VM provisioning or hosting claims.
 * https://docs.kernel.org/admin-guide/cgroup-v2.html
 */
import {readFileSync} from 'node:fs';

const PROFILES = Object.freeze({
  standard: Object.freeze({name: 'standard', cpus: 2, memory_bytes: 2 * 1024 ** 3, swap_bytes: 0,
    timeouts: Object.freeze({launch_ms: 30000, navigation_ms: 10000, bridge_ms: 30000})}),
  micro: Object.freeze({name: 'micro', cpus: 0.125, memory_bytes: 1024 ** 3, swap_bytes: 0,
    timeouts: Object.freeze({launch_ms: 180000, navigation_ms: 60000, bridge_ms: 120000})}),
});
const MEMORY_KEYS = ['low', 'high', 'max', 'oom', 'oom_kill'];
const CPU_KEYS = ['usage_usec', 'user_usec', 'system_usec', 'nr_periods', 'nr_throttled', 'throttled_usec'];
const reject = (code, detail) => {
  const error = new Error(`Resource profile ${code}: ${detail}`);
  error.code = code;
  throw error;
};

export function getResourceProfile(name = 'standard') {
  if (typeof name !== 'string' || !Object.hasOwn(PROFILES, name))
    reject('failed', 'select exactly standard or micro');
  return PROFILES[name];
}

function integer(value, file) {
  if (!/^(?:0|[1-9][0-9]*)$/u.test(value) || !Number.isSafeInteger(Number(value)))
    reject('unavailable', `${file} contains an unsupported integer`);
  return Number(value);
}

function keyed(text, file, required, optional = []) {
  const values = new Map();
  const lines = text.split('\n');
  if (lines.length > 64) reject('unavailable', `${file} exceeds its supported field bound`);
  for (const line of lines) {
    const match = /^([a-z][a-z0-9_.]{0,63})[ \t]+(\S+)$/u.exec(line);
    if (!match || values.has(match[1])) reject('unavailable', `${file} has malformed or duplicate fields`);
    values.set(match[1], integer(match[2], file));
  }
  const result = {};
  for (const key of required) {
    if (!values.has(key)) reject('unavailable', `${file} is missing required ${key}`);
    result[key] = values.get(key);
  }
  // Future kernel fields are not echoed into logs; optional known fields are never fabricated.
  for (const key of optional) if (values.has(key)) result[key] = values.get(key);
  return result;
}

/** The private container must mount its own cgroup-v2 root at /sys/fs/cgroup.
 * Values are one observation, not CPU-speed/architecture equivalence to any cloud VM.
 * Call before and after the bounded workload. Any failure throws, never a fallback value.
 */
export function collectResourceProfile({profile = 'standard', read = path => readFileSync(path, 'utf8')} = {}) {
  const selected = getResourceProfile(profile);
  const readValue = file => {
    let value;
    try { value = read(`/sys/fs/cgroup/${file}`); }
    catch { reject('unavailable', `${file} cannot be read from the container cgroup-v2 root`); }
    if (typeof value !== 'string' || value.length > 8192 || !value.trim())
      reject('unavailable', `${file} is empty or exceeds its supported bound`);
    return value.trim();
  };
  const cpuText = readValue('cpu.max');
  const cpu = /^(max|[0-9]+)[ \t]+([0-9]+)$/u.exec(cpuText);
  if (!cpu) reject('unavailable', 'cpu.max has an unsupported format');
  if (cpu[1] === 'max') reject('failed', 'CPU quota is unlimited');
  const quota = integer(cpu[1], 'cpu.max'), period = integer(cpu[2], 'cpu.max');
  if (quota === 0 || period === 0) reject('unavailable', 'cpu.max must have a positive quota and period');
  const numerator = profile === 'micro' ? 1n : 2n;
  const denominator = profile === 'micro' ? 8n : 1n;
  if (BigInt(quota) * denominator !== BigInt(period) * numerator)
    reject('failed', 'observed CPU quota differs from the selected profile');
  const limit = file => {
    const value = readValue(file);
    if (value === 'max') reject('failed', `${file} is unlimited`);
    return integer(value, file);
  };
  const memory = limit('memory.max'), swap = limit('memory.swap.max');
  if (memory !== selected.memory_bytes) reject('failed', 'observed memory limit differs from the selected profile');
  if (swap !== 0) reject('failed', 'swap is not disabled for the container');
  const events = keyed(readValue('memory.events'), 'memory.events', MEMORY_KEYS, ['oom_group_kill']);
  if (events.oom !== 0 || events.oom_kill !== 0 ||
      (Object.hasOwn(events, 'oom_group_kill') && events.oom_group_kill !== 0))
    reject('failed', 'the cgroup reports an OOM or OOM-kill event');
  const cpuStat = keyed(readValue('cpu.stat'), 'cpu.stat', CPU_KEYS, ['nr_bursts', 'burst_usec']);
  const current = integer(readValue('memory.current'), 'memory.current');
  const peak = integer(readValue('memory.peak'), 'memory.peak');
  // memory.max may be exceeded temporarily, per the kernel contract. Report the actual peak.
  return {status: 'observed', scope: 'container_cgroup_v2', profile,
    limits: {cpu_quota_usec: quota, cpu_period_usec: period, cpus: quota / period,
      memory_bytes: memory, swap_bytes: swap},
    memory_current_bytes: current, memory_peak_bytes: peak,
    peak_headroom_bytes: memory - peak, peak_headroom_ratio: (memory - peak) / memory,
    memory_pressure_observed: events.low > 0 || events.high > 0 || events.max > 0,
    memory_events: events, cpu_stat: cpuStat,
    counters_scope: 'cgroup_and_descendants_cumulative', vm_equivalence_verified: false};
}
