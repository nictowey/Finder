// Invoked only in the caller's disposable synthetic PostgreSQL schema.
import assert from 'node:assert/strict';
import { Pool } from 'pg';
import { deliver } from './send_watch_notifications.mjs';
import { inboxQuery } from '../functions/inbox-query.js';

const pool=new Pool({connectionString:process.env.FINDER_DATABASE_URL});
try {
 const db={query:async(sql:string,values?:unknown[])=>{
  if(sql.includes("key='vapid'"))return {rows:[{data:{publicKey:'synthetic',privateKey:'synthetic'}}]};
  if(sql.includes('SELECT id,data FROM finder_push_subscriptions'))return {rows:[{id:'synthetic',data:{}}]};
  return pool.query(sql,values);
 }};
 const result=await deliver(db,async()=>{throw new Error('Synthetic evidence quarantine must not send');});
 assert.equal(result.accepted_events,0);assert.equal(result.status,'no_eligible_alerts');
 console.log('{"legacy_pending_alert_quarantine":"passed"}');
 // A dedicated session's temporary tables model the per-watch reference cap.
 // No persistent rows or actual listing data enter this query-plan check.
 const client=await pool.connect();try{
  await client.query(`CREATE TEMP TABLE finder_watches(id text,config json,revision int);
   CREATE TEMP TABLE listings(marketplace text,marketplace_item_id text,data json);
   CREATE TEMP TABLE finder_inbox(watch_id text,marketplace text,marketplace_item_id text,data json,first_seen_at text,last_seen_at text,dismissed boolean);
   CREATE TEMP TABLE finder_decisions(watch_id text,marketplace text,marketplace_item_id text,verdict text,purchased boolean);
   CREATE TEMP TABLE finder_discovery_work(watch_id text,item_id text,marketplace text,status text,kind text,reason text,last_search_at text,fingerprint text,PRIMARY KEY(watch_id,item_id));
   INSERT INTO finder_watches VALUES('w','{}',1);
   INSERT INTO listings SELECT 'ebay',n::text,'{"details_observed_at":"2026-10-01T00:00:00Z"}'::json FROM generate_series(1,100)n;
   INSERT INTO finder_inbox SELECT 'w','ebay',n::text,'{"status":"family_review","details_observed_at":"2026-10-01T00:00:00Z"}'::json,'2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',false FROM generate_series(1,100)n;
   INSERT INTO finder_discovery_work SELECT 'w'||(n%20)::text,n::text,NULL,'pending',CASE WHEN n%1000=1 THEN 'existing_listing_updated' ELSE 'new_to_finder' END,NULL,'2026-10-01T00:01:00Z','H1' FROM generate_series(1,30000)n;
   ANALYZE finder_watches;ANALYZE listings;ANALYZE finder_inbox;ANALYZE finder_decisions;ANALYZE finder_discovery_work;`);
  const plan=(await client.query('EXPLAIN (ANALYZE,FORMAT JSON) '+inboxQuery,['review',null,null,null,'all','2026-09-30T18:00:00Z','all','all'])).rows[0]['QUERY PLAN'][0].Plan;
  const nodes=(node:any):any[]=>[node,...(node.Plans||[]).flatMap(nodes)];
  const work=nodes(plan).filter(node=>node['Relation Name']==='finder_discovery_work');
  assert.ok(work.length);assert.ok(work.every(node=>node['Node Type']!=='Seq Scan'||node['Actual Loops']<=1),'Shared evidence lookup must not repeatedly scan the full work table');
  console.log('{"pending_evidence_lookup_plan":"passed"}');
 }finally{client.release();}
}finally{await pool.end();}
