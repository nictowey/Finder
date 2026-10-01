// Synthetic SQL rehearsal only. The caller supplies an isolated test database/session.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { deliver } from './send_watch_notifications.mjs';

export async function checkNotificationPolicy(db) {
 const source=readFileSync(new URL('../src/finder/watch_worker.py',import.meta.url),'utf8');
 const policies=[...source.matchAll(/^POLICY = ["']([^"']+)["']$/gm)];
 assert.equal(policies.length,1);
 const policy=policies[0][1], version=Number(policy.match(/v(\d+)$/)[1]);
 const previous=policy.replace(/v\d+$/,`v${version-1}`), future=policy.replace(/v\d+$/,`v${version+1}`);
 const fresh=new Date(Date.now()-10000).toISOString(), old=new Date(Date.now()-7200000).toISOString();
 const boundary=new Date(Date.now()-5000).toISOString();
 // A rollback removes the temporary shadows and every synthetic change, even on failure.
 await db.query('BEGIN');
 try {
  const setup=`CREATE TEMP TABLE finder_private_settings(key text PRIMARY KEY,data json);
   CREATE TEMP TABLE finder_push_subscriptions(id text,data json);
   CREATE TEMP TABLE finder_watches(id text,enabled boolean,revision int);
   CREATE TEMP TABLE listings(marketplace text,marketplace_item_id text,data json);
   CREATE TEMP TABLE finder_inbox(watch_id text,marketplace text,marketplace_item_id text,data json,last_seen_at text,dismissed boolean);
   CREATE TEMP TABLE finder_verdicts(watch_id text,marketplace text,marketplace_item_id text);
   CREATE TEMP TABLE finder_discovery_work(marketplace text,item_id text,status text,kind text,reason text,last_search_at text,fingerprint text);
   CREATE TEMP TABLE finder_outbox(id text,watch_id text,marketplace text,marketplace_item_id text,status text,attempts int);
   INSERT INTO finder_private_settings VALUES('vapid','{"publicKey":"synthetic","privateKey":"synthetic"}');
   INSERT INTO finder_push_subscriptions VALUES('synthetic','{}');`;
  for(const statement of setup.split(';').filter(value=>value.trim()))await db.query(statement);
  const cases=[
   {id:'current',expected:'sent',attempts:1},
   {id:'retry',status:'sending',initialAttempts:1,expected:'sent',attempts:2},
   {id:'previous',policy:previous,expected:'expired'},
   {id:'future',policy:future,expected:'expired'},
   {id:'missing-policy',policy:null,expected:'expired'},
   {id:'stale-details',details:old,expected:'expired'},
   {id:'stale-review',seen:old,expected:'expired'},
   {id:'ended',ended:old,expected:'expired'},
   {id:'disabled',enabled:false,expected:'expired'},
   {id:'dismissed',dismissed:true,expected:'expired'},
   {id:'judged',judged:true,expected:'expired'},
   {id:'old-revision',revision:0,expected:'expired'},
   {id:'not-eligible',notify:false,expected:'expired'},
   {id:'invalidated',invalidated:boundary,expected:'expired'},
   {id:'held-current',held:true,expected:'pending'},
   {id:'held-previous',held:true,policy:previous,expected:'pending'},
   {id:'exhausted',initialAttempts:3,expected:'failed',attempts:3},
   {id:'already-expired',status:'expired',expected:'expired'},
   {id:'already-sent',status:'sent',initialAttempts:1,expected:'sent',attempts:1},
  ];
  for(const row of cases){
   const review={status:'possible_pressing',policy:row.policy===undefined?policy:row.policy,watch_revision:row.revision??1,
    notify:row.notify??true,details_observed_at:row.details??fresh,...(row.invalidated?{evidence_invalidated_at:row.invalidated}:{})};
   await db.query('INSERT INTO finder_watches VALUES($1,$2,1)',[row.id,row.enabled??true]);
   await db.query("INSERT INTO listings VALUES('synthetic',$1,$2)",[row.id,{details_observed_at:row.details??fresh,listing_ends_at:row.ended??null}]);
   await db.query("INSERT INTO finder_inbox VALUES($1,'synthetic',$1,$2,$3,$4)",[row.id,review,row.seen??fresh,row.dismissed??false]);
   await db.query("INSERT INTO finder_outbox VALUES($1,$1,'synthetic',$1,$2,$3)",[row.id,row.status??'pending',row.initialAttempts??0]);
   if(row.judged)await db.query("INSERT INTO finder_verdicts VALUES($1,'synthetic',$1)",[row.id]);
   if(row.held)await db.query("INSERT INTO finder_discovery_work VALUES('synthetic',$1,'pending','existing_listing_updated',NULL,$2,'H1')",[row.id,boundary]);
  }
  let sends=0;
  const send=async(_subscription,payload,options)=>{
   sends++;assert.deepEqual(JSON.parse(payload),{kind:'review-inbox'});
   assert.equal(options.topic,'finder-inbox');assert.equal(options.TTL,1800);
  };
  const first=await deliver(db,send);
  assert.equal(first.status,'accepted_by_push_service');assert.equal(first.accepted_events,2);assert.equal(sends,1);
  const expected=cases.map(row=>({id:row.id,status:row.expected,attempts:row.attempts??0})).sort((a,b)=>a.id.localeCompare(b.id));
  const actual=await db.query('SELECT id,status,attempts FROM finder_outbox ORDER BY id');
  assert.deepEqual(actual.rows,expected);
  const second=await deliver(db,send);
  assert.equal(second.status,'no_eligible_alerts');assert.equal(second.accepted_events,0);assert.equal(sends,1);
  assert.deepEqual((await db.query('SELECT id,status,attempts FROM finder_outbox ORDER BY id')).rows,expected);
 }finally{await db.query('ROLLBACK');}
}
