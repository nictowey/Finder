/// <reference types="emscripten" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { deliver } from '../scripts/send_watch_notifications.mjs';

test('legacy shared changes hold events without attempts or expiry, and newer same-fact reviews remain usable',async()=>{
 const db=new PGlite();try{
  await db.exec(`CREATE TABLE finder_private_settings(key text PRIMARY KEY,data json);
   CREATE TABLE finder_push_subscriptions(id text,data json);
   CREATE TABLE finder_watches(id text,enabled boolean,revision int);
   CREATE TABLE listings(marketplace text,marketplace_item_id text,data json);
   CREATE TABLE finder_inbox(watch_id text,marketplace text,marketplace_item_id text,data json,last_seen_at text,dismissed boolean);
   CREATE TABLE finder_verdicts(watch_id text,marketplace text,marketplace_item_id text);
   CREATE TABLE finder_discovery_work(marketplace text,listing_id text,item_id text,status text,kind text,reason text,last_search_at text,fingerprint text);
   CREATE TABLE finder_outbox(id text,watch_id text,marketplace text,marketplace_item_id text,status text,attempts int);
   INSERT INTO finder_private_settings VALUES('vapid','{"publicKey":"synthetic","privateKey":"synthetic"}');
   INSERT INTO finder_push_subscriptions VALUES('synthetic','{}');
   INSERT INTO finder_watches VALUES('a',true,1),('b',true,1);
   INSERT INTO finder_outbox VALUES('event-a','a','ebay','item','pending',0),('event-b','b','ebay','item','pending',0);`);
  const old=new Date(Date.now()-7200000).toISOString(),boundary=new Date(Date.now()-20000).toISOString(),fresh=new Date(Date.now()-10000).toISOString(),later=new Date().toISOString();
  const review={status:'possible_pressing',policy:'private-target-review-v12',watch_revision:1,notify:true,details_observed_at:old};
  await db.query("INSERT INTO listings VALUES('ebay','item',$1)",[{details_observed_at:old}]);
  for(const watch of ['a','b'])await db.query("INSERT INTO finder_inbox VALUES($1,'ebay','item',$2,$3,false)",[watch,review,old]);
  // A different watch can know the changed identity before it ever hydrates it.
  await db.query("INSERT INTO finder_discovery_work VALUES(NULL,NULL,'item','pending','existing_listing_updated',NULL,$1,'H1')",[boundary]);
  let sent=0;
  const send=async()=>{sent++;};
  const held=await deliver(db,send);
  assert.equal(held.accepted_events,0);assert.equal(sent,0);
  assert.deepEqual((await db.query('SELECT id,status,attempts FROM finder_outbox ORDER BY id')).rows,[{id:'event-a',status:'pending',attempts:0},{id:'event-b',status:'pending',attempts:0}]);
  // Another watch repeats H1 later. Its established boundary remains earlier
  // than B's successful assessment; A's older assessment must stay quarantined.
  await db.query("UPDATE listings SET data=$1",[{details_observed_at:fresh,source_metadata:{finder_details_invalidated_at:boundary,finder_summary_fingerprint:'H1'}}]);
  await db.query("UPDATE finder_inbox SET data=$1,last_seen_at=$2 WHERE watch_id='b'",[{...review,details_observed_at:fresh},fresh]);
  await db.query('UPDATE finder_discovery_work SET last_search_at=$1',[later]);
  const current=await deliver(db,send);
  assert.equal(current.accepted_events,1);assert.equal(sent,1);
  assert.deepEqual((await db.query('SELECT id,status,attempts FROM finder_outbox ORDER BY id')).rows,[{id:'event-a',status:'pending',attempts:0},{id:'event-b',status:'sent',attempts:1}]);
 }finally{await db.close();}
});
