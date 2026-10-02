// Synthetic differential fixture shared by PGlite and the isolated PostgreSQL gate.
import assert from 'node:assert/strict';
import { readOperations, summarizeOperations } from './operations.js';
import { operationsHistoryQuery } from './operations-query.js';
type DB = { query(sql: string, values?: any[]): Promise<{ rows: any[] }> };
const now = Date.parse('2026-09-23T12:00:00Z');
const cutoff = new Date(now - 14 * 86400000).toISOString();
const watch = { id:'synthetic-watch', config:{enabled:true}, status:'healthy',
  last_success_at:'2026-09-23T11:50:00Z', next_scan_at:'2026-09-23T12:20:00Z' };

export async function checkOperationsFixture(db: DB) {
  // TEMP tables ensure the fixture is isolated even inside a shared rehearsal schema.
  for (const sql of [
    `CREATE TEMP TABLE finder_scan_attempts(id text PRIMARY KEY,watch_id text,revision int,due_at text,
      started_at text,lease_until text,finished_at text,previous_success_at text,status text,
      run_id text,source text,dispatch_id text,metrics json)`,
    `CREATE TEMP TABLE finder_dispatch_attempts(id text PRIMARY KEY,scheduled_at text,started_at text,
      finished_at text,status text,http_status int)`,
    `CREATE TEMP TABLE finder_private_settings(key text PRIMARY KEY,data json)`,
    `CREATE TEMP TABLE finder_push_subscriptions(id text PRIMARY KEY,data json)`,
  ]) await db.query(sql);
  const reset = () => db.query(`TRUNCATE finder_scan_attempts,finder_dispatch_attempts,
    finder_private_settings,finder_push_subscriptions`);
  const attempt = async (index: number, overrides: any = {}, rawMetrics?: string) => {
    const at = new Date(now - index * 60000).toISOString();
    const row = { id:'a'+index, watch_id:'synthetic-watch', revision:1, due_at:at, started_at:at,
      lease_until:at, finished_at:null, previous_success_at:null, status:'completed', run_id:null,
      source:'scheduled', dispatch_id:null, ...overrides };
    await db.query(`INSERT INTO finder_scan_attempts VALUES
      ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::json)`,
      [...Object.values(row), rawMetrics ?? JSON.stringify({browse_requests:9,browse_retries:1})]);
  };
  const dispatch = (id: string, status: string, at = new Date(now).toISOString()) => db.query(
    'INSERT INTO finder_dispatch_attempts VALUES($1,$2,$2,NULL,$3,202)',[id,at,status]);
  const settings = async () => {
    await db.query(`INSERT INTO finder_private_settings VALUES
      ('operations_since','{"at":"2026-09-01T00:00:00Z"}'),
      ('scan_trigger_health','{"at":"2026-09-23T11:55:00Z"}'),
      ('notification_health','{"at":"2026-09-23T11:59:00Z","status":"delivered"}')`);
    await db.query(`INSERT INTO finder_push_subscriptions VALUES('device','{}'),('second','{}')`);
  };
  const compare = async (label: string, watches: any[] = [watch]) => {
    const oldStart = performance.now();
    const attempts = (await db.query(`SELECT * FROM finder_scan_attempts WHERE started_at >= $1
      ORDER BY started_at DESC LIMIT 10001`,[cutoff])).rows;
    const dispatches = (await db.query(`SELECT * FROM finder_dispatch_attempts WHERE started_at >= $1
      ORDER BY started_at DESC LIMIT 10001`,[cutoff])).rows;
    const oldHistoryMs = performance.now() - oldStart;
    const config = Object.fromEntries((await db.query('SELECT key,data FROM finder_private_settings')).rows.map(r=>[r.key,r.data]));
    const devices = Number((await db.query('SELECT count(*) AS count FROM finder_push_subscriptions')).rows[0].count);
    const old = {...summarizeOperations(watches,attempts,dispatches,config.operations_since?.at ?? null,
      config.scan_trigger_health?.at ?? null,devices,config.notification_health,now),
      history_truncated:attempts.length>10000||dispatches.length>10000};
    let history: any[] = [], newHistoryMs = 0;
    const actual = await readOperations({query:async(sql,values)=>{
      const started = performance.now();
      const result = await db.query(sql,values);
      if(sql===operationsHistoryQuery) { history=result.rows; newHistoryMs=performance.now()-started; }
      return result;
    }},watches,now);
    assert.deepEqual(actual,old,label);
    return {actual,history,oldHistoryMs,newHistoryMs,oldRows:attempts.length+dispatches.length,newRows:history.length,
      oldBytes:Buffer.byteLength(JSON.stringify(attempts))+Buffer.byteLength(JSON.stringify(dispatches)),
      newBytes:Buffer.byteLength(JSON.stringify(history))};
  };
  try {
    await compare('empty history, no measurement');
    await settings();
    await compare('empty measured history, notifications retained',[]);
    await attempt(0,{status:'started',lease_until:'2026-09-23T12:00:00Z',dispatch_id:'d1'});
    await attempt(1,{status:'started',lease_until:'2026-09-23T12:00:00.001Z',dispatch_id:'d1'});
    await attempt(2,{status:'started',lease_until:'September 23, 2026 11:59:59 GMT',dispatch_id:'d2'});
    await attempt(3,{status:'failed',due_at:'2026-09-23T11:00:00Z'});
    for(const [i,status] of ['quota_paused','abandoned','superseded','unknown'].entries()) await attempt(i+4,{status});
    await attempt(9);
    await attempt(10,{},'{"quota_remaining":120,"catalog_check_failed":true,"partial_details":2}');
    await attempt(11,{},'{"quota_remaining":200,"catalog_search_incomplete":true,"capped_pages":3}');
    await attempt(12,{dispatch_id:'outside'},'{}');
    await attempt(30000,{status:'failed',dispatch_id:'d3'},'{"browse_requests":9999}');
    await dispatch('d1','accepted');await dispatch('d2','unknown','2026-09-23T11:59:00Z');
    await dispatch('d3','rejected','2026-09-23T11:58:00Z');await dispatch('reserved','reserved','2026-09-23T11:57:00Z');
    await dispatch('outside','accepted','2026-08-01T00:00:00Z');
    const normal=await compare('mixed bounded history, expiry boundary, correlation');
    assert.equal(normal.actual.counts.abandoned,3);assert.equal(normal.actual.dispatches.correlated,2);
    assert.equal(normal.actual.latest_quota_remaining,120);assert.equal(normal.actual.recent.length,12);
    await compare('current watch health remains JS',[
      {...watch,id:'stale',last_success_at:'bad',next_scan_at:'bad'},
      {...watch,id:'expired',status:'scanning',lease_until:'2026-09-23T12:00:00Z'},
      {...watch,id:'paused',config:{enabled:false},last_success_at:null},
      {...watch,id:'overdue',next_scan_at:'2026-09-23T11:44:00Z'},
      {...watch,id:'failed',status:'failed'},
    ]);

    for(const metric of ['browse_requests','browse_retries','capped_pages','partial_details']) {
      await reset();
      const values = ['0.1','0.2','0.3','9007199254740992','-9007199254740992','1e400','null',
        'false','true','0','""','"2"','[]','[1,2]','{}','{"x":1}','1e-400'];
      for(const [i,value] of values.entries()) await attempt(i,{},`{"${metric}":${value}}`);
      await compare('ordered coercions and floating point '+metric);
    }
    await reset();
    for(const [i,metrics] of ['{}','null','[]','42','"text"',
      '{"browse_requests":999999999999999}', '{"browse_requests":-999999999999999}',
      '{"browse_requests":true}', '{"browse_requests":false}', '{"browse_requests":""}',
      '{"catalog_check_failed":[],"catalog_search_incomplete":false}',
      '{"catalog_check_failed":{},"catalog_search_incomplete":null}',
      '{"catalog_check_failed":0,"catalog_search_incomplete":""}',
      '{"catalog_check_failed":1e-400}', '{"catalog_search_incomplete":1e400}',
      '{"catalog_check_failed":false,"catalog_search_incomplete":"false"}',
      '{"catalog_check_failed":"\\u0000"}',
      '{"ignored":"\\ud800","browse_requests":4,"quota_remaining":77,"catalog_check_failed":true}',
      '{"ignored":"\\udc00","browse_requests":"2","quota_remaining":88}',
      '{"ignored":"\\ud83d\\ude00","browse_requests":8}',
      '{"ignored":"\\u0000","browse_requests":7}',
      '{"browse_requests":1e1000000,"catalog_check_failed":1e1000000}',

    ].entries()) await attempt(i,{},metrics);
    await compare('missing/scalar metrics and exact catalog truthiness');
    for (const quotas of [
      ['"4"','null','true','1.5','1e400','9007199254740992.1','123'],
      ['"4"','0','9'], ['1e-400','5'], ['-1','1e400'], ['1e400','1.5'],
    ]) {
      await reset();
      for(const [i,value] of quotas.entries()) await attempt(i,{},`{"quota_remaining":${value}}`);
      await compare('latest JS integer quota '+quotas.join(','));
    }
    await reset();
    const timestamps = [
      '2026-09-23T11:00:00.000999+00:00','2026-09-23T11:00:00.999999Z',
      '2026-02-30T11:00:00Z','2026-04-31T11:00:00Z',
      '2026-09-23T24:00:00Z','2026-09-23T11:00:00-01:00',
      '2026-09-23','September 23, 2026 11:00:00 GMT','0000-01-01T00:00:00Z',
      '2026-09-23T11:00:00.1Z','2026-09-23T11:00:00.01Z','2026-09-23T11:00:00.123456789Z',
      '2026-13-01T11:00:00Z','2026-09-23T11:60:00Z','2026-09-32T11:00:00Z','not a date',null,
    ];
    for(const [i,value] of timestamps.entries()) {
      // Compare individually: a single NaN must not mask valid-date differences.
      await reset();await attempt(i,{status:'started',due_at:value,lease_until:value});
      await compare('timestamp due/lease '+value);
      await reset();await attempt(i,{started_at:value,due_at:'2026-09-23T11:00:00Z'});
      await compare('timestamp started '+value);
    }
    await reset();await attempt(0,{dispatch_id:''});await dispatch('','accepted');
    await compare('empty dispatch reference is not correlated');

    // Realistic allow-listed metrics; no arbitrary padding or seller payloads.
    const seed = async (count: number) => {
      await reset();await settings();
      await db.query(`INSERT INTO finder_scan_attempts
        SELECT 'a'||g,'synthetic-watch',1,to_char(timestamp '2026-09-23 12:00:00'-g*interval '1 second','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
          to_char(timestamp '2026-09-23 12:00:00'-g*interval '1 second','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
          '2026-09-23T12:20:00Z','2026-09-23T12:01:00Z','2026-09-23T11:50:00Z',
          'completed','synthetic-run','scheduled','d'||g,
          '{"browse_requests":9,"browse_retries":1,"quota_remaining":4200,"quota_required":12,"search_requests":3,"detail_requests":5,"quota_requests":1,"catalog_requests":4,"catalog_retries":0,"observed":8,"ambiguous_leads":2,"new_inbox_rows":2,"catalog_check_failed":false,"catalog_search_incomplete":false,"capped_pages":1,"partial_details":0}'::json
        FROM generate_series(1,$1::int) g`,[count]);
      await db.query(`INSERT INTO finder_dispatch_attempts
        SELECT 'd'||g,'2026-09-23T12:00:00Z',
          to_char(timestamp '2026-09-23 12:00:00'-g*interval '1 second','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
          '2026-09-23T12:00:01Z','accepted',202 FROM generate_series(1,$1::int) g`,[count]);
    };
    let measured: any;
    for(const count of [9999,10000,10001,10002]) {
      await seed(count);
      measured=await compare('cap boundary '+count);
      assert.equal(measured.actual.history_truncated,count>10000);
      assert.equal(measured.actual.counts.completed,Math.min(count,10001));
      assert.equal(measured.actual.dispatches.correlated,Math.min(count,10001));
      assert.equal(measured.actual.browse_requests,Math.min(count,10001)*9);
      assert.equal(measured.history[0].browse_requests.fallback,null);
      assert.equal(measured.history[0].delay_fallback,null);
    }
    assert.ok(measured.newBytes < measured.oldBytes/100,'normal history should reduce returned JSON by >99%');
    const measurement = {fixture:'synthetic 10002 attempts + 10002 dispatches, each capped at 10001; not billing telemetry',
      old_rows:measured.oldRows,new_rows:measured.newRows,old_json_bytes:measured.oldBytes,new_json_bytes:measured.newBytes,
      old_history_ms:Math.round(measured.oldHistoryMs),new_history_ms:Math.round(measured.newHistoryMs)};
    // Cap exclusion affects correlation independently on either side.
    await db.query("UPDATE finder_scan_attempts SET dispatch_id='d10002' WHERE id='a1'");
    await db.query("UPDATE finder_scan_attempts SET dispatch_id='d1' WHERE id='a10002'");
    await compare('independent capped correlation');
    await db.query('TRUNCATE finder_scan_attempts');await compare('dispatch-only truncation');
    await seed(10002);await db.query('TRUNCATE finder_dispatch_attempts');await compare('attempt-only truncation');
    // A safe-looking integer sequence whose intermediate sum is not safe must fall back.
    await db.query(`UPDATE finder_scan_attempts SET metrics='{"browse_requests":999999999999999}'`);
    const overflow=await compare('ordered integer overflow fallback');
    assert.equal(overflow.history[0].browse_requests.fallback.length,10001);
    return measurement;
  } finally {
    for(const table of ['finder_scan_attempts','finder_dispatch_attempts','finder_private_settings','finder_push_subscriptions'])
      await db.query(`DROP TABLE ${table}`);
  }
}
