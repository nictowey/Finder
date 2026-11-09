'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { resolve } = require('node:path');

test('actual noncanonical PTY delivers full start, larger frame and ACK without echo or actions', () => {
  const source = String.raw`
import errno,json,os,pty,select,subprocess,sys,termios,time
master,slave=pty.openpty()
p=subprocess.Popen(['sh',sys.argv[1],sys.argv[2],sys.argv[3],'--transport-check'],stdin=slave,stdout=slave,stderr=slave,close_fds=True)
os.close(slave)
try:
    end=time.monotonic()+3
    while termios.tcgetattr(master)[3] & (termios.ICANON|termios.ECHO):
        if time.monotonic()>end: raise RuntimeError('PTY did not become raw')
        time.sleep(.01)
    start={'type':'start','ready_ref':'synthetic','scope':{'marker':'NEVER_ECHO_PAYLOAD'},'userReady':{},'priorFailure':{},'expected_latest_hash':'synthetic'}
    ack={'type':'operator_ack','intent_id':'synthetic','ready_ref':'synthetic','scope_digest':'synthetic','request_id':'synthetic','stage':'open_cart','deadline':0,'from_document_ref':None}
    def frame(value,size):
        text=json.dumps(value,separators=(',',':'))
        return (text+' '*(size-len(text))).encode()+b'\n'
    data=frame(start,1544)+frame(start,8192)+frame(ack,4096)+b'{"type":"transport_finish"}\n'
    while data:
        sent=os.write(master,data); data=data[sent:]
    output=b''; end=time.monotonic()+5
    while time.monotonic()<end:
        ready,_,_=select.select([master],[],[],.1)
        if ready:
            try: chunk=os.read(master,65536)
            except OSError as e:
                if e.errno==errno.EIO: break
                raise
            if not chunk: break
            output+=chunk
        elif p.poll() is not None: break
    p.wait(timeout=2)
    assert p.returncode==0,output
    assert b'NEVER_ECHO_PAYLOAD' not in output,output
    print(json.dumps([json.loads(line) for line in output.decode().splitlines() if line.strip()]))
finally:
    if p.poll() is None: p.kill(); p.wait()
    os.close(master)
`;
  const result = spawnSync('python3', ['-c', source, resolve('scripts/operator_stdio_transport.sh'),
    process.execPath, resolve('scripts/discogs_guest_checkout_operator.mjs')], { encoding: 'utf8', timeout: 12000 });
  assert.equal(result.status, 0, result.stderr);
  const lines = JSON.parse(result.stdout);
  assert.deepEqual(lines.slice(0, 3).map(x => x.received_bytes), [1544, 8192, 4096]);
  assert.deepEqual(lines.slice(0, 3).map(x => x.parsed_type), ['start', 'start', 'operator_ack']);
  assert.ok(lines.every(x => x.no_action === true));
  assert.deepEqual(lines.at(-1), { type: 'transport_complete', frames: 3, no_action: true });
});

test('transport diagnostic rejects oversized data and cannot combine with live mode', () => {
  const file = resolve('scripts/discogs_guest_checkout_operator.mjs');
  const invalid = spawnSync(process.execPath, [file, '--transport-check'],
    { input: JSON.stringify({ type: 'start', secret: 'NEVER_LOG'.repeat(3000) }) + '\n', encoding: 'utf8' });
  assert.equal(invalid.status, 2);
  assert.equal(invalid.stdout.includes('NEVER_LOG'), false);
  assert.equal(JSON.parse(invalid.stdout).no_action, true);
  const combined = spawnSync(process.execPath, [file, '--transport-check', '--waiting-ready'], { encoding: 'utf8' });
  assert.equal(combined.status, 2); assert.equal(combined.stdout, '');
});
