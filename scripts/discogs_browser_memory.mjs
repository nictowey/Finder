/** Local cgroup counters only. Never reads pages, profiles, URLs or process args. */
import { readFile } from 'node:fs/promises';

const FILES = Object.freeze({
  current: '/sys/fs/cgroup/memory.current',
  peak: '/sys/fs/cgroup/memory.peak',
  limit: '/sys/fs/cgroup/memory.max',
  events: '/sys/fs/cgroup/memory.events',
});
export const BROWSER_MEMORY_SAMPLE_MS = 60_000;

function counter(value) {
  if (typeof value !== 'string' || !/^\d+\s*$/u.test(value)) return null;
  const parsed = Number(value.trim());
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

export async function browserMemorySnapshot({ read = readFile } = {}) {
  const values = {};
  await Promise.all(Object.entries(FILES).map(async ([key, path]) => {
    try { values[key] = await read(path, 'utf8'); }
    catch { values[key] = null; }
  }));
  const current = counter(values.current), peak = counter(values.peak);
  const limit = counter(values.limit);
  const unbounded = typeof values.limit === 'string' && values.limit.trim() === 'max';
  const kills = typeof values.events === 'string' ?
    values.events.split('\n').filter(line => /^oom_kill\s+/u.test(line)) : [];
  const oomKills = kills.length === 1 ? counter(kills[0].replace(/^oom_kill\s+/u, '')) : null;
  return {
    schema: 'sniper_browser_memory_v1',
    current_bytes: current,
    peak_bytes: peak,
    limit_bytes: limit !== null && limit > 0 ? limit : null,
    oom_kills: oomKills,
    status: current === null ? 'unknown' : unbounded ? 'unbounded' :
      limit === null || limit <= 0 ? 'unknown' : current >= limit * 0.85 ? 'pressure' : 'within_limit',
  };
}

/** Observe only. Pressure does not restart/close a browser or retry an operation. */
export function startBrowserMemoryLogging({ report = message => console.log(message), read = readFile } = {}) {
  let stopped = false, sampling = false;
  const sample = async () => {
    if (stopped || sampling) return;
    sampling = true;
    try {
      const value = await browserMemorySnapshot({ read });
      if (!stopped) {
        try { report(JSON.stringify(value)); }
        catch { /* Logging failure must not alter browser or transaction state. */ }
      }
    } finally { sampling = false; }
  };
  void sample();
  const timer = setInterval(() => { void sample(); }, BROWSER_MEMORY_SAMPLE_MS);
  timer.unref?.();
  return () => { stopped = true; clearInterval(timer); };
}
