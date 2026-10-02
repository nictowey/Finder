import assert from 'node:assert/strict';
import test from 'node:test';
import { summarizeOperations } from './operations.js';
const now = Date.parse('2026-09-23T12:00:00Z');
const watch = { id:'w',config:{enabled:true},status:'healthy',last_success_at:'2026-09-23T11:50:00Z',next_scan_at:'2026-09-23T12:20:00Z' };

test('a silent scheduler outage is visible even with no failed attempts',()=>{
 const old={...watch,last_success_at:'2026-09-23T08:00:00Z',next_scan_at:'2026-09-23T08:30:00Z'};
 const r=summarizeOperations([old],[],[],'2026-09-23T07:00:00Z',null,0,null,now);
 assert.equal(r.status,'attention_needed');assert.equal(r.counts.failed,0);
 assert.equal(r.current[0].age_minutes,240);assert.equal(r.current[0].overdue_minutes,210);
 assert.equal(r.trigger_stale,true);assert.ok(r.elapsed_days<1);
});
test('expired unfinished work is counted without needing the next worker to run',()=>{
 const a={status:'started',started_at:'2026-09-23T11:00:00Z',due_at:'2026-09-23T10:45:00Z',lease_until:'2026-09-23T11:12:00Z',metrics:{}};
 const r=summarizeOperations([{...watch,status:'scanning',lease_until:a.lease_until}],[a],[],'2026-09-23T10:00:00Z',null,0,null,now);
 assert.equal(r.counts.abandoned,1);assert.equal(r.max_start_delay_minutes,15);
 assert.equal(r.status,'attention_needed');assert.equal(r.recent[0].status,'abandoned');
});
test('accepted dispatches and correlated work are separate, no automated certification',()=>{
 const a={status:'completed',started_at:'2026-09-23T11:50:00Z',due_at:'2026-09-23T11:45:00Z',dispatch_id:'d',metrics:{browse_requests:9,browse_retries:1,capped_pages:1}};
 const r=summarizeOperations([watch],[a],[{id:'d',status:'accepted'},{id:'other',status:'rejected'}],'2026-09-01T00:00:00Z','2026-09-23T11:55:00Z',0,null,now);
 assert.equal(r.status,'collecting_evidence');assert.equal(r.elapsed_days,14);
 assert.equal(r.dispatches.accepted,1);assert.equal(r.dispatches.correlated,1);assert.equal(r.dispatches.rejected,1);
 assert.equal(r.browse_requests,9);assert.equal(r.notifications.registered_devices,0);
});
test('paused watches do not manufacture an outage',()=>{
 const r=summarizeOperations([{...watch,config:{enabled:false},last_success_at:null}],[],[],'2026-09-23T10:00:00Z',null,0,null,now);
 assert.equal(r.status,'collecting_evidence');assert.equal(r.trigger_stale,false);
});

test('lightweight health retains live status and notifications without reading either ledger',async()=>{
 const {readOperationsHealth}=await import('./operations.js');
 const queries:string[]=[];
 const db={query:async(sql:string)=>{
  queries.push(sql);
  if(sql.includes('finder_private_settings'))return {rows:[
   {key:'operations_since',data:{at:'2026-09-23T10:00:00Z'}},
   {key:'scan_trigger_health',data:{at:'2026-09-23T11:00:00Z'}},
   {key:'notification_health',data:{status:'delivered',at:'2026-09-23T11:59:00Z'}},
  ]};
  assert.equal(sql,'SELECT COUNT(*) AS count FROM finder_push_subscriptions');
  return {rows:[{count:'2'}]};
 }};
 const r=await readOperationsHealth(db,[{...watch,status:'scanning',lease_until:'2026-09-23T11:59:00Z'}],now);
 assert.equal(r.status,'attention_needed');assert.equal(r.current[0].lease_expired,true);
 assert.equal(r.trigger_stale,true);assert.equal(r.notifications.registered_devices,2);
 assert.deepEqual(r.notifications.last_delivery,{status:'delivered',at:'2026-09-23T11:59:00Z'});
 assert.equal(queries.length,2);
 assert.equal(Object.hasOwn(r,'counts'),false);assert.equal(Object.hasOwn(r,'recent'),false);
});

test('real PostgreSQL gate refuses production or ambiguous targets before connecting',async()=>{
 const {validateOperationsFixtureURL}=await import('../scripts/operations-fixture-target.js');
 validateOperationsFixtureURL('postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1:5432/finder_ci');
 for(const url of ['', 'postgresql://user:secret@production.example/db',
  'postgresql://finder_ci:finder_ci_ephemeral@localhost:5432/finder_ci',
  'postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1/finder_ci',
  'postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1:5432/production',
  'postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1:5432/finder_ci?host=production.example',
  'postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1:5432/finder_ci#fragment'])
  assert.throws(()=>validateOperationsFixtureURL(url),{message:'Operations gate requires the isolated loopback CI fixture'});
});

test('Node pg JSON and numeric parsers retain the transport types used by history assembly',async()=>{
 const {types}=await import('pg');
 const json='{"sum":42,"fallback":[0.1,1e400,9007199254740992.1],"counts":{"completed":10001},"metrics":"{\\"ignored\\":\\"\\\\u0000\\"}"}';
 assert.deepEqual(types.getTypeParser(114,'text')(json),JSON.parse(json));
 assert.equal(types.getTypeParser(20,'text')('10001'),'10001');
 assert.equal(types.getTypeParser(1700,'text')('1234.000000'),'1234.000000');
});
