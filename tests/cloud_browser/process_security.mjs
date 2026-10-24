/** Read-only Linux process evidence, not a complete sandbox/security audit.
 * No launches, signals, ptrace, environment reads or security-setting changes.
 * Playwright 1.64.1 pins Firefox source fdd757a2e09c9471cddf383e64e631e4ce178499:
 * ipc/glue/GeckoChildProcessHost.cpp prefixes exe,-contentproc and appends childId,type;
 * ipc/app/MozillaRuntimeMain.cpp consumes the final argument as type; Content is 'tab'.
 * https://man7.org/linux/man-pages/man5/proc_pid_status.5.html
 */
import * as nodeFs from 'node:fs';
import {dirname, join} from 'node:path';

const baselines = new WeakMap();
const LIMIT = 4096;
const ROLES = ['tab', 'gpu', 'rdd', 'socket', 'utility', 'forkserver', 'gmplugin', 'vr', 'ipdlunittest'];
// Diagnostic classifications only. Names never establish process identity or permission.
function knownName(value) {
  return ({'Web Content': 'web_content', 'Isolated Web Co': 'isolated_web_content',
    'Isolated Web Content': 'isolated_web_content', 'Privileged Cont': 'privileged_content',
    'Privileged Content': 'privileged_content', 'RDD Process': 'rdd',
    'Socket Process': 'socket', 'Utility Process': 'utility', 'forkserver': 'forkserver',
    'MainThread': 'main_thread', 'firefox': 'firefox', 'firefox-bin': 'firefox'})[value] ?? 'other';
}
function descendantDiagnostics(fs, descendants, argsByPid, allowed) {
  const groups = new Map();
  for (const pid of descendants) {
    const args = argsByPid.get(pid);
    let comm = 'unavailable';
    try {
      const value = fs.readFileSync(`/proc/${pid}/comm`, 'utf8');
      comm = value.length <= 256 ? knownName(value.trim()) : 'other';
    } catch { /* Optional diagnostic only; required evidence is never skipped. */ }
    const row = {pinned_argv0: allowed.has(args[0]), argv0_name: knownName(args[0]),
      has_contentproc_token: args.includes('-contentproc'), contentproc_second: args[1] === '-contentproc',
      has_contentproc_text: args.some(arg => /(?:^|\s)-contentproc(?:\s|$)/u.test(arg)),
      has_is_for_browser: args.includes('-isForBrowser'), argument_count: args.length,
      final_role: ROLES.includes(args.at(-1)) ? args.at(-1) : 'other', comm};
    const key = JSON.stringify(row);
    groups.set(key, (groups.get(key) ?? 0) + 1);
  }
  return {descendant_count: descendants.length, groups: [...groups].slice(0, 16)
    .map(([shape, count]) => ({...JSON.parse(shape), count})), truncated: groups.size > 16};
}
const reject = (code, detail) => {
  const error = new Error(`Process security ${code}: ${detail}`);
  error.code = code;
  throw error;
};
const requireThat = (condition, detail) => {
  if (!condition) reject('failed', detail);
};
function read(fs, path) {
  let text;
  try { text = fs.readFileSync(path, 'utf8'); }
  catch { reject('unavailable', 'required process evidence is unreadable'); }
  if (typeof text !== 'string' || text.length > 65536)
    reject('unavailable', 'process evidence exceeds its supported bound');
  return text;
}
function ids(fs, path) {
  let names;
  try { names = fs.readdirSync(path); }
  catch { reject('unavailable', 'process enumeration is unreadable'); }
  const result = names.filter(name => /^[1-9][0-9]*$/u.test(name)).map(Number);
  if (result.length > LIMIT || result.some(id => !Number.isSafeInteger(id)))
    reject('unavailable', 'process enumeration exceeds its supported bound');
  return result.sort((a, b) => a - b);
}
function stat(fs, path, pid) {
  const value = read(fs, `${path}/stat`);
  const end = value.lastIndexOf(')');
  const fields = value.slice(end + 1).trim().split(/\s+/u);
  if (end < 0 || !value.startsWith(`${pid} (`) || fields.length < 20 ||
      !/^\d+$/u.test(fields[1]) || !/^\d+$/u.test(fields[19]))
    reject('unavailable', 'malformed process identity');
  if (['Z', 'X', 'x'].includes(fields[0])) reject('unavailable', 'process exited during inspection');
  return {pid, ppid: Number(fields[1]), started: fields[19]};
}
function status(fs, path, pid, uid) {
  const fields = new Map(read(fs, `${path}/status`).trim().split('\n').map(line => {
    const index = line.indexOf(':');
    return [line.slice(0, index), line.slice(index + 1).trim()];
  }));
  const number = name => {
    const value = fields.get(name);
    if (!/^\d+$/u.test(value ?? '') || !Number.isSafeInteger(Number(value)))
      reject('unavailable', `missing or malformed ${name}`);
    return Number(value);
  };
  requireThat(number('Pid') === pid, 'process identity mismatch');
  const uids = fields.get('Uid')?.split(/\s+/u);
  if (!uids || uids.length !== 4 || !uids.every(value => /^\d+$/u.test(value)))
    reject('unavailable', 'missing or malformed Uid');
  requireThat(uids.every(value => Number(value) === uid), 'unexpected process UID');
  requireThat(number('NoNewPrivs') === 1, 'no-new-privileges is not active');
  requireThat(number('Seccomp') === 2, 'seccomp filter mode is not active');
  const filters = number('Seccomp_filters');
  requireThat(filters > 0, 'no seccomp filters attached');
  return {filters, ppid: number('PPid'), tgid: number('Tgid')};
}
function processEvidence(fs, pid, uid) {
  const path = `/proc/${pid}`;
  const before = stat(fs, path, pid);
  const leader = status(fs, path, pid, uid);
  requireThat(leader.ppid === before.ppid && leader.tgid === pid, 'process identity mismatch');
  const tids = ids(fs, `${path}/task`);
  requireThat(tids.includes(pid) && tids.length <= 256, 'unsupported process thread set');
  const threads = tids.map(tid => {
    const threadPath = `${path}/task/${tid}`;
    const first = stat(fs, threadPath, tid);
    const evidence = status(fs, threadPath, tid, uid);
    requireThat(evidence.tgid === pid, 'thread belongs to another process');
    const last = stat(fs, threadPath, tid);
    if (JSON.stringify(first) !== JSON.stringify(last)) reject('unavailable', 'thread identity changed');
    return {tid, started: first.started, filters: evidence.filters};
  });
  if (JSON.stringify(tids) !== JSON.stringify(ids(fs, `${path}/task`)) ||
      JSON.stringify(before) !== JSON.stringify(stat(fs, path, pid)))
    reject('unavailable', 'process changed during inspection');
  return {...before, filters: leader.filters, threads};
}
function argv(fs, pid) {
  const raw = read(fs, `/proc/${pid}/cmdline`);
  if (!raw.endsWith('\0')) reject('unavailable', 'missing bounded process arguments');
  return raw.slice(0, -1).split('\0');
}
function tree(fs, rootPid) {
  const records = new Map();
  for (const pid of ids(fs, '/proc')) {
    // Exits are evidence gaps, not silently ignored candidates.
    records.set(pid, stat(fs, `/proc/${pid}`, pid));
  }
  const descendants = pid => {
    const result = new Set();
    const pending = [pid];
    while (pending.length) {
      const parent = pending.pop();
      for (const item of records.values()) {
        if (item.ppid !== parent) continue;
        if (result.has(item.pid) || item.pid === pid) reject('unavailable', 'cyclic process ancestry');
        result.add(item.pid);
        pending.push(item.pid);
      }
    }
    return [...result].sort((a, b) => a - b);
  };
  requireThat(records.has(rootPid), 'runner process is missing');
  return {records, descendants};
}
function executable(fs, pid) {
  try { return fs.readlinkSync(`/proc/${pid}/exe`); }
  catch { reject('unavailable', 'browser executable identity is unreadable'); }
}
function maxFilters(evidence) {
  return Math.max(evidence.filters, ...evidence.threads.map(thread => thread.filters));
}

export function captureProcessSecurityBaseline({rootPid = process.pid, expectedUid = 10001, fs = nodeFs} = {}) {
  const root = processEvidence(fs, rootPid, expectedUid);
  const baseline = Object.freeze({root_pid: rootPid, root_start_ticks: root.started,
    container_filters: maxFilters(root)});
  baselines.set(baseline, {rootPid, expectedUid, fs, browser: null});
  return baseline;
}

export function collectProcessSecurity({rootPid = process.pid, executablePath, expectedUid = 10001,
  baseline, fs = nodeFs}) {
  const saved = baselines.get(baseline);
  requireThat(saved && saved.rootPid === rootPid && saved.expectedUid === expectedUid && saved.fs === fs,
    'capture the matching baseline before launching the browser');
  let pinned;
  try { pinned = fs.realpathSync(executablePath); }
  catch { reject('unavailable', 'pinned browser executable is unavailable'); }
  // The official Linux archive carries firefox and firefox-bin copies. Only that directory is accepted.
  const allowed = new Set([pinned, join(dirname(pinned), 'firefox-bin')]);
  const inspect = () => {
    const root = processEvidence(fs, rootPid, expectedUid);
    requireThat(root.started === baseline.root_start_ticks, 'runner identity changed');
    const hierarchy = tree(fs, rootPid);
    const argumentsByPid = new Map(hierarchy.descendants(rootPid).map(pid => [pid, argv(fs, pid)]));
    const parents = [...argumentsByPid].filter(([, args]) =>
      allowed.has(args[0]) && args.includes('-juggler-pipe') && !args.includes('-contentproc'));
    requireThat(parents.length === 1, 'expected exactly one pinned Firefox parent');
    const browserPid = parents[0][0];
    requireThat(allowed.has(executable(fs, browserPid)), 'browser executable differs from pinned Firefox');
    const browser = processEvidence(fs, browserPid, expectedUid);
    const identity = `${browser.pid}:${browser.started}`;
    requireThat(!saved.browser || saved.browser === identity, 'browser identity changed between observations');
    const threshold = Math.max(baseline.container_filters, maxFilters(root), maxFilters(browser));
    const content = [];
    for (const pid of hierarchy.descendants(browserPid)) {
      const args = argumentsByPid.get(pid);
      if (!args.includes('-contentproc')) continue;
      requireThat(args[1] === '-contentproc', 'unsupported Firefox child argument structure');
      requireThat(allowed.has(args[0]), 'content executable argument differs from pinned Firefox');
      if (!ROLES.includes(args.at(-1))) reject('unavailable', 'unknown Firefox child process role');
      if (args.at(-1) !== 'tab') continue;
      // Do not request ptrace permissions for child /exe. Identity is the observed ancestry,
      // pinned argv[0], exact version-specific role and stable PID/starttime, not binary attestation.
      const item = processEvidence(fs, pid, expectedUid);
      requireThat(item.filters > threshold && item.threads.every(thread => thread.filters > threshold),
        'content filtering does not exceed the measured runner and browser baselines');
      content.push(item);
    }
    if (!content.length) reject('failed', 'no identifiable Firefox content processes; ' +
      JSON.stringify(descendantDiagnostics(fs, hierarchy.descendants(browserPid), argumentsByPid, allowed)));
    // Re-read every ancestry edge, argv and executable used above after the security reads.
    for (const pid of [rootPid, ...argumentsByPid.keys()]) {
      if (JSON.stringify(hierarchy.records.get(pid)) !== JSON.stringify(stat(fs, `/proc/${pid}`, pid)))
        reject('unavailable', 'process ancestry changed during inspection');
      if (pid !== rootPid && JSON.stringify(argumentsByPid.get(pid)) !== JSON.stringify(argv(fs, pid)))
        reject('unavailable', 'process arguments changed during inspection');
    }
    requireThat(allowed.has(executable(fs, browserPid)), 'browser executable changed during inspection');
    return {identity, browser, content, threshold};
  };
  const first = inspect(), last = inspect();
  const identities = result => JSON.stringify([result.identity, result.content.map(item =>
    [item.pid, item.started, item.threads.map(thread => [thread.tid, thread.started])])]);
  if (identities(first) !== identities(last)) reject('unavailable', 'content process/thread set is unstable');
  saved.browser = last.identity;
  return {status: 'observed', mechanism: 'additional_content_seccomp_filters', uid: expectedUid,
    no_new_privileges: true, seccomp_mode: 2, browser_pid: last.browser.pid,
    browser_start_ticks: last.browser.started, container_baseline_filters: baseline.container_filters,
    browser_parent_filters: maxFilters(last.browser), comparison_baseline_filters: last.threshold,
    content_processes: last.content.length,
    content_threads: last.content.reduce((count, item) => count + item.threads.length, 0),
    minimum_content_filters: Math.min(...last.content.flatMap(item =>
      [item.filters, ...item.threads.map(thread => thread.filters)])),
    content_identity: 'stable_descendant_and_pinned_argv_role',
    child_executable_links_inspected: false, full_sandbox_attested: false};
}
