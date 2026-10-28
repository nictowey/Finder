/** Offline /proc snapshots only. No processes or browser launches. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {captureProcessSecurityBaseline, collectProcessSecurity, collectStableProcessSecurity}
  from './process_security.mjs';

const EXE = '/pinned/firefox/firefox';
function fixture() {
  const processes = new Map();
  const add = (pid, ppid, args, filters = 1, tids = [pid]) => {
    const item = {pid, ppid, args, filters, tids, started: String(pid * 100), uid: '10001 10001 10001 10001',
      noNew: 1, seccomp: 2, exe: EXE, threadOverrides: {}};
    processes.set(pid, item);
    return item;
  };
  add(1, 0, ['/sbin/init']);
  add(10, 1, ['/node', 'synthetic-smoke'], 1, [10, 11]);
  add(20, 10, [EXE, '-headless', '-profile', '/tmp/private-profile', '-juggler-pipe'], 1, [20, 21]);
  add(25, 20, [EXE, '-contentproc', '-other', '3', 'forkserver']);
  add(30, 25, [EXE, '-contentproc', '-isForBrowser', '4', 'tab'], 3, [30, 31]);
  add(40, 20, [EXE, '-contentproc', '5', 'socket']);
  const calls = new Map(), failures = new Map();
  let onRead = () => {};
  const missing = () => { throw Object.assign(new Error('PRIVATE error must not escape'), {code: 'EACCES'}); };
  const fs = {
    readFileSync(path) {
      calls.set(path, (calls.get(path) ?? 0) + 1);
      onRead(path, calls.get(path));
      if (failures.has(path)) return missing();
      const comm = /^\/proc\/(\d+)\/comm$/u.exec(path);
      if (comm) return (processes.get(Number(comm[1])).comm ?? 'unknown') + '\n';
      const match = /^\/proc\/(\d+)(?:\/task\/(\d+))?\/(stat|status|cmdline)$/u.exec(path);
      assert.ok(match, `Unexpected fixture read: ${path}`);
      const process = processes.get(Number(match[1]));
      if (!process) return missing();
      const pid = Number(match[2] ?? match[1]);
      const row = {...process, ...(process.threadOverrides[pid] ?? {})};
      if (match[3] === 'cmdline') return process.args.join('\0') + '\0';
      if (match[3] === 'stat') {
        const fields = Array(20).fill('0');
        fields[0] = 'S'; fields[1] = String(row.ppid); fields[19] = row.started;
        return `${pid} (name with (parentheses)) ${fields.join(' ')}\n`;
      }
      return `Pid:\t${pid}\nTgid:\t${process.pid}\nPPid:\t${row.ppid}\nUid:\t${row.uid}\n` +
        `NoNewPrivs:\t${row.noNew}\nSeccomp:\t${row.seccomp}\n` +
        (row.filters === undefined ? '' : `Seccomp_filters:\t${row.filters}\n`);
    },
    readdirSync(path) {
      if (failures.has(path)) return missing();
      if (path === '/proc') return [...processes.keys()].map(String).concat(['self', 'net']);
      const match = /^\/proc\/(\d+)\/task$/u.exec(path);
      assert.ok(match);
      return processes.get(Number(match[1])).tids.map(String);
    },
    readlinkSync(path) {
      calls.set(path, (calls.get(path) ?? 0) + 1);
      if (failures.has(path)) return missing();
      return processes.get(Number(path.split('/')[2])).exe;
    },
    realpathSync(path) { assert.equal(path, EXE); return EXE; },
  };
  const options = {rootPid: 10, expectedUid: 10001, fs};
  const baseline = captureProcessSecurityBaseline(options);
  return {processes, calls, failures, add, onRead: fn => {onRead = fn;}, baseline,
    check: () => collectProcessSecurity({...options, baseline, executablePath: EXE})};
}

test('stable descendant role and every thread show additional filtering above actual baselines', () => {
  const f = fixture(), result = f.check();
  assert.equal(result.status, 'observed');
  assert.equal(result.browser_pid, 20);
  assert.equal(result.content_processes, 1);
  assert.equal(result.content_threads, 2);
  assert.equal(result.minimum_content_filters, 3);
  assert.equal(result.comparison_baseline_filters, 1);
  assert.equal(result.full_sandbox_attested, false);
  assert.equal(result.child_executable_links_inspected, false);
  assert.equal(f.calls.get('/proc/30/exe'), undefined);
  assert.ok(!JSON.stringify(result).includes('private-profile'));
  assert.deepEqual(f.check(), result);
});

test('filter thresholds are measured, not architecture-specific hard-coded counts', () => {
  const f = fixture();
  f.processes.get(10).filters = 4;
  f.processes.get(20).filters = 5;
  f.processes.get(30).filters = 6;
  assert.equal(f.check().comparison_baseline_filters, 5);
});

for (const [name, change] of [
  ['mixed root UID', f => {f.processes.get(30).uid = '10001 0 10001 10001';}],
  ['no-new-privileges off', f => {f.processes.get(30).noNew = 0;}],
  ['seccomp off', f => {f.processes.get(30).seccomp = 0;}],
  ['only inherited container filters', f => {f.processes.get(30).filters = 1;}],
  ['one unfiltered content thread', f => {f.processes.get(30).threadOverrides[31] = {filters: 1};}],
  ['one privileged content thread', f => {f.processes.get(30).threadOverrides[31] = {noNew: 0};}],
  ['browser parent stronger than content', f => {f.processes.get(20).threadOverrides[21] = {filters: 3};}],
  ['runner baseline strengthened later', f => {f.processes.get(10).filters = 3;}],
  ['unrelated browser identity', f => {f.processes.get(20).exe = '/other/firefox';}],
  ['foreign content binary argument', f => {f.processes.get(30).args[0] = '/other/firefox';}],
  ['ambiguous parent', f => {f.add(22, 10, [EXE, '-juggler-pipe']);}],
  ['browser outside runner descendants', f => {f.processes.get(20).ppid = 1;}],
  ['no web content; helpers alone cannot pass', f => {f.processes.get(30).args[4] = 'socket';}],
  ['content marker in wrong position', f => {f.processes.get(30).args = [EXE, 'x', '-contentproc', 'tab'];}],
]) test(`rejects ${name}`, () => {
  const f = fixture(); change(f);
  assert.throws(f.check, error => error.code === 'failed');
});

for (const [name, change] of [
  ['missing kernel filter counter', f => {f.processes.get(30).filters = undefined;}],
  ['unreadable content status', f => {f.failures.set('/proc/30/status', true);}],
  ['unreadable content thread status', f => {f.failures.set('/proc/30/task/31/status', true);}],
  ['unreadable parent executable', f => {f.failures.set('/proc/20/exe', true);}],
  ['unreadable intermediate ancestry', f => {f.failures.set('/proc/25/stat', true);}],
  ['unknown child role', f => {f.processes.get(30).args[4] = 'future-type';}],
  ['PID reuse while inspecting', f => f.onRead((path, count) => {
    if (path === '/proc/30/stat' && count === 3) f.processes.get(30).started = '99999';
  })],
  ['ancestry change while inspecting', f => f.onRead((path, count) => {
    if (path === '/proc/25/stat' && count === 2) f.processes.get(25).ppid = 10;
  })],
]) test(`reports unavailable evidence for ${name}, never a silent pass`, () => {
  const f = fixture(); change(f);
  assert.throws(f.check, error => error.code === 'unavailable' && !error.message.includes('PRIVATE'));
});

test('a stable new browser cannot replace the previously inspected browser identity', () => {
  const f = fixture(); f.check(); f.processes.get(20).started = '999';
  assert.throws(f.check, /browser identity changed between observations/u);
});

test('child executable links need no ptrace access and are not attested', () => {
  const f = fixture(); f.failures.set('/proc/30/exe', true);
  assert.equal(f.check().content_identity, 'stable_descendant_and_pinned_argv_role');
  assert.equal(f.calls.get('/proc/30/exe'), undefined);
});

test('main executable may be the official sibling firefox-bin', () => {
  const f = fixture(); f.processes.get(20).exe = '/pinned/firefox/firefox-bin';
  f.processes.get(30).args[0] = '/pinned/firefox/firefox-bin';
  assert.equal(f.check().content_processes, 1);
});

test('baseline is mandatory and cannot be replaced with an invented filter count', () => {
  assert.throws(() => collectProcessSecurity({rootPid: 10, executablePath: EXE, baseline: {}}),
    /capture the matching baseline/u);
});

test('failure diagnostics describe runtime title shape without accepting it as identity', () => {
  const f = fixture();
  f.processes.get(30).args = ['Web Content', 'PRIVATE_PROFILE_PATH', 'PRIVATE_TOKEN'];
  f.processes.get(30).comm = 'Web Content';
  assert.throws(f.check, error => {
    assert.equal(error.code, 'failed');
    assert.match(error.message, /"argv0_name":"web_content"/u);
    assert.match(error.message, /"comm":"web_content"/u);
    assert.match(error.message, /"has_contentproc_token":false/u);
    assert.ok(!error.message.includes('PRIVATE'));
    assert.ok(!error.message.includes('/pinned'));
    return true;
  });
});

test('unknown names and unreadable optional diagnostic comm remain sanitized', () => {
  const f = fixture();
  f.processes.get(30).args = ['PRIVATE unrecognized -contentproc shape'];
  f.processes.get(30).comm = 'PRIVATE_COMPUTER';
  f.failures.set('/proc/25/comm', true);
  assert.throws(f.check, error => {
    assert.match(error.message, /"has_contentproc_text":true/u);
    assert.match(error.message, /"comm":"other"/u);
    assert.match(error.message, /"comm":"unavailable"/u);
    assert.ok(!error.message.includes('PRIVATE'));
    return error.code === 'failed';
  });
});

test('diagnostics aggregate equal shapes rather than logging raw per-process arguments', () => {
  const f = fixture();
  f.processes.get(30).args = ['Web Content'];
  f.add(35, 25, ['Web Content'], 3);
  assert.throws(f.check, error => {
    assert.match(error.message, /"descendant_count":4/u);
    assert.match(error.message, /"count":2/u);
    return error.code === 'failed';
  });
});

test('one canonical space-joined child command preserves exact identity and filter gates', () => {
  const f = fixture();
  f.processes.get(30).args = [f.processes.get(30).args.join(' ')];
  const result = f.check();
  assert.equal(result.content_processes, 1);
  assert.equal(result.flattened_content_processes, 1);
  assert.equal(result.minimum_content_filters, 3);
  f.processes.get(30).threadOverrides[31] = {filters: 1};
  assert.throws(f.check, /content filtering does not exceed/u);
});

for (const [name, args] of [
  ['foreign executable', ['/foreign/firefox -contentproc 4 tab']],
  ['prefix garbage', [`PREFIX ${EXE} -contentproc 4 tab`]],
  ['suffix garbage', [`${EXE} -contentproc 4 tab SUFFIX`]],
  ['quoted executable', [`"${EXE}" -contentproc 4 tab`]],
  ['single-quoted argument', [`${EXE} -contentproc 'ambiguous value' 4 tab`]],
  ['backslash escape', [`${EXE} -contentproc escaped\\ value 4 tab`]],
  ['embedded fake flag', [`${EXE} fake-contentproc 4 tab`]],
  ['mixed NUL and joined arguments', [EXE, '-contentproc 4 tab']],
  ['mixed joined command and extra NUL token', [`${EXE} -contentproc 4 tab`, 'extra']],
  ['unknown role', [`${EXE} -contentproc 4 future-role`]],
  ['tab whitespace', [`${EXE}\t-contentproc 4 tab`]],
  ['newline whitespace', [`${EXE} -contentproc\n4 tab`]],
  ['double spaces', [`${EXE}  -contentproc 4 tab`]],
  ['leading space', [` ${EXE} -contentproc 4 tab`]],
  ['trailing space', [`${EXE} -contentproc 4 tab `]],
  ['control character', [`${EXE} -contentproc x\x01 4 tab`]],
  ['non-ASCII text', [`${EXE} -contentproc café 4 tab`]],
]) test(`flattened child representation rejects ${name}`, () => {
  const f = fixture(); f.processes.get(30).args = args;
  assert.throws(f.check, error => error.code === 'failed' || error.code === 'unavailable');
});

test('rejected flattened diagnostics expose booleans and allowlisted roles, never input text', () => {
  const f = fixture();
  f.processes.get(30).args = [`${EXE} -contentproc "PRIVATE_PROFILE" 4 tab`];
  assert.throws(f.check, error => {
    assert.match(error.message, /"flattened_canonical_ascii_spaces":false/u);
    assert.match(error.message, /"flattened_pinned_first":true/u);
    assert.match(error.message, /"flattened_child_marker_second":true/u);
    assert.match(error.message, /"flattened_final_role":"tab"/u);
    assert.ok(!error.message.includes('PRIVATE_PROFILE'));
    assert.ok(!error.message.includes(EXE));
    return error.code === 'failed';
  });
});

test('stability comparisons retain the original unnormalized process arguments', () => {
  const f = fixture();
  const original = [...f.processes.get(30).args];
  f.onRead((path, count) => {
    if (path === '/proc/30/cmdline' && count === 2) f.processes.get(30).args = [original.join(' ')];
  });
  assert.throws(f.check, error => error.code === 'unavailable' && /arguments changed/u.test(error.message));
});

test('stable wrapper immediately returns strict evidence without a delay', async () => {
  const result = {status: 'observed'}, options = {sentinel: true};
  let calls = 0;
  assert.equal(await collectStableProcessSecurity(options, {
    collect: received => {assert.equal(received, options); calls++; return result;},
    delay: () => assert.fail('No delay after success'),
  }), result);
  assert.equal(calls, 1);
});

test('stable wrapper retries unavailable evidence twice with exactly 50ms waits', async () => {
  let calls = 0;
  const waits = [], result = {status: 'observed'};
  assert.equal(await collectStableProcessSecurity({}, {
    collect: () => {
      if (++calls < 3) throw Object.assign(new Error('transient'), {code: 'unavailable'});
      return result;
    },
    delay: milliseconds => {waits.push(milliseconds);},
  }), result);
  assert.equal(calls, 3);
  assert.deepEqual(waits, [50, 50]);
});

for (const code of ['unavailable', 'failed', undefined])
  test(`stable wrapper keeps ${code ?? 'unexpected'} errors bounded and failing`, async () => {
    const original = Object.assign(new Error('not accepted'), {code});
    let calls = 0;
    const waits = [];
    await assert.rejects(collectStableProcessSecurity({}, {
      collect: () => {calls++; throw original;},
      delay: milliseconds => {waits.push(milliseconds);},
    }), error => error === original);
    assert.equal(calls, code === 'unavailable' ? 3 : 1);
    assert.deepEqual(waits, code === 'unavailable' ? [50, 50] : []);
  });

test('failed security after transient evidence loss stops the wrapper immediately', async () => {
  let calls = 0;
  const waits = [];
  await assert.rejects(collectStableProcessSecurity({}, {
    collect: () => {
      calls++;
      throw Object.assign(new Error('cannot pass'), {code: calls === 1 ? 'unavailable' : 'failed'});
    },
    delay: milliseconds => {waits.push(milliseconds);},
  }), error => error.code === 'failed');
  assert.equal(calls, 2);
  assert.deepEqual(waits, [50]);
});
