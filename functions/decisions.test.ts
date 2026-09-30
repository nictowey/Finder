/// <reference types="emscripten" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { PGlite } from '@electric-sql/pglite';
import { createHandler } from './watchlist.js';
import { decisionInput,saveDecisionQuery } from './decisions.js';
const schema=`CREATE TABLE finder_watches(id text PRIMARY KEY);
CREATE TABLE finder_inbox(watch_id text REFERENCES finder_watches ON DELETE CASCADE,marketplace text,marketplace_item_id text,data json,last_seen_at text,PRIMARY KEY(watch_id,marketplace,marketplace_item_id));
CREATE TABLE finder_verdicts(watch_id text,marketplace text,marketplace_item_id text,verdict text,tier text,decided_at text,PRIMARY KEY(watch_id,marketplace,marketplace_item_id),FOREIGN KEY(watch_id,marketplace,marketplace_item_id) REFERENCES finder_inbox ON DELETE CASCADE);
CREATE TABLE finder_decisions(watch_id text,marketplace text,marketplace_item_id text,verdict text,purchased boolean NOT NULL,tier text,decided_at text,updated_at text NOT NULL,prediction json,legacy json,PRIMARY KEY(watch_id,marketplace,marketplace_item_id),FOREIGN KEY(watch_id,marketplace,marketplace_item_id) REFERENCES finder_inbox ON DELETE CASCADE);
CREATE TABLE finder_outbox(watch_id text,marketplace text,marketplace_item_id text,status text);
INSERT INTO finder_watches VALUES('w');
INSERT INTO finder_inbox VALUES('w','ebay','1','{"status":"family_review","policy":"test-v1","watch_revision":1,"clues":["private seller text"]}','2026-09-30T12:00:00Z');
ALTER TABLE finder_inbox ADD COLUMN dismissed boolean DEFAULT false;`;
const key={watch_id:'w',marketplace:'ebay',marketplace_item_id:'1'};
async function setup(){const db=new PGlite();await db.exec(schema);for(const name of ['decision_bridge.sql','save_decision.sql'])await db.exec(readFileSync(new URL('../src/finder/'+name,import.meta.url),'utf8'));return db;}
async function save(db:PGlite,value:object,purchase=false){return db.query(saveDecisionQuery,decisionInput({...key,...value},purchase)!);}
async function row(db:PGlite){return (await db.query<any>('SELECT * FROM finder_decisions')).rows[0];}

for(const verdict of ['mine','other','unsure'])test(`identity ${verdict} saves and rehydrates with immutable first prediction`,async()=>{
 const db=await setup();try{
  await db.exec("INSERT INTO finder_outbox VALUES('w','ebay','1','pending')");
  await save(db,{verdict,observed_tier:'family_review',observed_evaluated_at:'2026-09-30T12:00:00Z'});
  const first=await row(db);assert.equal(first.verdict,verdict);assert.equal(first.purchased,false);
  assert.equal(first.tier,'family_review');assert.equal(first.prediction.policy,'test-v1');assert.ok(!JSON.stringify(first.prediction).includes('private seller text'));
  assert.equal((await db.query('SELECT * FROM finder_outbox')).rows.length,0);
  await db.exec(`UPDATE finder_inbox SET data='{"status":"conflicting","policy":"test-v2"}',last_seen_at='2026-09-30T13:00:00Z'`);
  await save(db,{verdict:'other'});await save(db,{verdict:null});await save(db,{verdict:'mine'});
  const after=await row(db);assert.equal(after.verdict,'mine');assert.equal(after.tier,first.tier);assert.equal(after.decided_at,first.decided_at);assert.deepEqual(after.prediction,first.prediction);
 }finally{await db.close();}
});
test('purchase is independent, legacy bought never invents identity, clear/unpurchase preserves provenance',async()=>{
 const db=await setup();try{
  await save(db,{verdict:'bought'});let d=await row(db);assert.equal(d.verdict,null);assert.equal(d.purchased,true);assert.equal(d.tier,null);assert.equal(d.decided_at,null);
  await save(db,{verdict:'mine'});const first=await row(db);
  await save(db,{verdict:null});d=await row(db);assert.equal(d.verdict,null);assert.equal(d.purchased,true);
  assert.equal((await db.query<any>('SELECT verdict FROM finder_verdicts')).rows[0].verdict,'bought');
  await save(db,{purchased:false},true);d=await row(db);assert.equal(d.purchased,false);assert.equal((await db.query('SELECT * FROM finder_verdicts')).rows.length,0);
  assert.equal(d.decided_at,first.decided_at);assert.deepEqual(d.prediction,first.prediction);
  await save(db,{purchased:true},true);await save(db,{verdict:'other'});assert.equal((await row(db)).purchased,true);
 }finally{await db.close();}
});
test('old API writes remain compatible across migration and rollback windows',async()=>{
 const db=await setup();try{
  await db.exec("INSERT INTO finder_verdicts VALUES('w','ebay','1','bought','conflicting','old-time')");
  const legacy=(await row(db)).legacy;assert.equal((await row(db)).verdict,null);
  await db.exec("UPDATE finder_verdicts SET verdict='mine',tier='family_review',decided_at='first-identity'");
  const first=await row(db);assert.equal(first.verdict,'mine');assert.equal(first.purchased,true);
  await db.exec("UPDATE finder_verdicts SET verdict='bought',decided_at='later'");assert.equal((await row(db)).verdict,'mine');
  await db.exec('DELETE FROM finder_verdicts');assert.equal((await row(db)).verdict,null);assert.equal((await row(db)).purchased,true);
  assert.equal((await db.query<any>('SELECT verdict FROM finder_verdicts')).rows[0].verdict,'bought');
  await save(db,{verdict:'other'});const after=await row(db);assert.deepEqual(after.legacy,legacy);assert.deepEqual(after.prediction,first.prediction);assert.equal(after.decided_at,first.decided_at);
  await db.exec("DELETE FROM finder_watches WHERE id='w'");assert.equal((await db.query('SELECT * FROM finder_decisions')).rows.length,0);assert.equal((await db.query('SELECT * FROM finder_verdicts')).rows.length,0);
 }finally{await db.close();}
});
test('stale displayed prediction rejects save without changing labels or pending events',async()=>{
 const db=await setup();try{
  await db.exec("INSERT INTO finder_outbox VALUES('w','ebay','1','pending')");
  assert.equal((await save(db,{verdict:'mine',observed_tier:'possible_pressing'})).rows.length,0);
  assert.equal((await save(db,{verdict:'mine',observed_evaluated_at:'2026-09-30T11:00:00Z'})).rows.length,0);
  assert.equal((await db.query('SELECT * FROM finder_decisions')).rows.length,0);assert.equal((await db.query('SELECT * FROM finder_outbox')).rows.length,1);
 }finally{await db.close();}
});
test('failure in mirror write rolls back feedback and outbox cleanup together',async()=>{
 const db=await setup();try{
  await db.exec("INSERT INTO finder_outbox VALUES('w','ebay','1','pending'); ALTER TABLE finder_verdicts ADD CONSTRAINT synthetic_failure CHECK(verdict <> 'mine')");
  await assert.rejects(save(db,{verdict:'mine'}));assert.equal((await db.query('SELECT * FROM finder_decisions')).rows.length,0);assert.equal((await db.query('SELECT * FROM finder_outbox')).rows.length,1);
 }finally{await db.close();}
});
test('feedback endpoints validate all paths and report stale/missing distinctly',async()=>{
 const db=await setup();try{
  const adapter={query:async(sql:string,args?:unknown[])=>sql.includes('owner_email')?{rows:[{data:{email:'owner@example.com'}}]}:db.query(sql,args)};
  const handler=createHandler({db:adapter,origin:'https://finder.example',authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:'2099-01-01T00:00:00Z'}}))});
  const post=(value:object,path='/api/verdict',origin='https://finder.example')=>handler(new Request('https://finder.example'+path,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({...key,...value})}));
  for(const verdict of ['mine','other','unsure','bought',null])assert.equal((await post({verdict})).status,200);
  assert.equal((await post({purchased:false},'/api/purchase')).status,200);
  assert.equal((await post({verdict:'mine',observed_tier:'conflicting'})).status,409);
  assert.equal((await post({verdict:'mine',marketplace_item_id:'missing'})).status,404);
  assert.equal((await post({verdict:'maybe'})).status,400);assert.equal((await post({purchased:'yes'},'/api/purchase')).status,400);
  assert.equal((await post({verdict:'mine'},'/api/verdict','https://evil.example')).status,403);
 }finally{await db.close();}
});
test('dashboard reload restores each saved identity and purchase without counting purchase-only',async()=>{
 const db=await setup();try{
  const fresh=new Date().toISOString();
  const adapter={query:async(sql:string,args?:unknown[])=>{
   if(sql.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
   if(sql.includes('FROM finder_decisions'))return db.query(sql,args);
   if(sql.includes('FROM finder_inbox i JOIN listings'))return {rows:[{...key,revision:1,first_seen_at:fresh,last_seen_at:fresh,data:{status:'family_review',watch_revision:1},listing:{title:'Synthetic',details_observed_at:fresh,item_specifics:{}}}]};
   return {rows:[]};
  }};
  const handler=createHandler({db:adapter,origin:'https://finder.example',authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:'2099-01-01T00:00:00Z'}}))});
  const get=async()=>(await handler(new Request('https://finder.example/api/dashboard?filter=judged'))).json();
  await save(db,{purchased:true},true);let data=await get();assert.equal(data.leads[0].purchased,true);assert.equal(data.leads[0].verdict,null);assert.deepEqual(data.accuracy,[]);
  for(const verdict of ['mine','other','unsure']){
   await save(db,{verdict});data=await get();assert.equal(data.leads[0].verdict,verdict);assert.equal(data.leads[0].purchased,true);assert.equal(data.leads[0].verdict_tier,'family_review');assert.equal(data.leads[0].verdict_provenance,'recorded_prediction');assert.equal(data.accuracy[0].verdict,verdict);
  }
  await save(db,{verdict:null});data=await get();assert.equal(data.leads[0].verdict,null);assert.equal(data.leads[0].purchased,true);assert.deepEqual(data.accuracy,[]);
 }finally{await db.close();}
});
