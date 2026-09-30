/// <reference types="emscripten" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { createHandler } from './watchlist.js';

const origin='https://finder.example';
async function fixture(){
 const db=new PGlite();
 await db.exec(`CREATE TABLE finder_watches(id text PRIMARY KEY,config json,revision int,status text,last_started_at text,last_success_at text,next_scan_at text,lease_until text,summary json,catalog json,catalog_observed_at text);
 CREATE TABLE listings(marketplace text,marketplace_item_id text,data json,PRIMARY KEY(marketplace,marketplace_item_id));
 CREATE TABLE finder_inbox(watch_id text REFERENCES finder_watches,marketplace text,marketplace_item_id text,data json,first_seen_at text,last_seen_at text,dismissed boolean,PRIMARY KEY(watch_id,marketplace,marketplace_item_id),FOREIGN KEY(marketplace,marketplace_item_id) REFERENCES listings);
 CREATE TABLE finder_decisions(watch_id text,marketplace text,marketplace_item_id text,verdict text,purchased boolean,tier text,decided_at text,prediction json,PRIMARY KEY(watch_id,marketplace,marketplace_item_id),FOREIGN KEY(watch_id,marketplace,marketplace_item_id) REFERENCES finder_inbox);
 INSERT INTO finder_watches(id,config,revision) VALUES('w','{"maximum_subtotal":"1","currency":"USD"}',1);`);
 const insert=async(id:string,verdict:string|null,purchased=false,dismissed=false,tier:string|null='family_review')=>{
  await db.query("INSERT INTO listings VALUES('ebay',$1,$2)",[id,{title:'Synthetic reference',details_observed_at:'2000-01-01T00:00:00Z',current_price:'999',item_specifics:{}}]);
  await db.query("INSERT INTO finder_inbox VALUES('w','ebay',$1,$2,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',$3)",[id,{status:'conflicting',watch_revision:0,availability:'unavailable_on_recheck'},dismissed]);
  await db.query("INSERT INTO finder_decisions VALUES('w','ebay',$1,$2,$3,$4,'2026-01-01T00:00:00Z',NULL)",[id,verdict,purchased,tier]);
 };
 for(let i=0;i<60;i++)await insert('other'+String(i).padStart(3,'0'),'other',false,false,i===0?'unrecognized-legacy-tier':'family_review');
 for(let i=0;i<55;i++)await insert('mine'+String(i).padStart(3,'0'),'mine',false,i===0,i===0?null:'family_review');
 await insert('unsure','unsure');await insert('purchase-a',null,true,true);await insert('purchase-b',null,true);
 await insert('both','mine',true);await insert('cleared',null);
 const adapter={query:async(sql:string,args?:unknown[])=>{
  if(sql.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(sql.includes('FROM finder_decisions')||sql.includes('FROM finder_inbox')||sql.includes('FROM finder_watches ORDER BY id'))return db.query(sql,args);
  return {rows:[]};
 }};
 const handler=createHandler({db:adapter,origin,authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:'2099-01-01T00:00:00Z'}}))});
 return {db,handler,get:async(params='')=>{
  const response=await handler(new Request(origin+'/api/dashboard?filter=judged'+params));
  assert.equal(response.status,200);assert.equal(response.headers.get('Cache-Control'),'no-store');return response.json();
 }};
}

test('Judged filters precede pagination and totals include dismissed, stale and unavailable saved rows',async()=>{
 const f=await fixture();try{
  const a=await f.get('&judgment=mine');assert.equal(a.leads.length,50);assert.equal(a.filtered_total,56);
  assert.deepEqual(a.judged_counts,{all:119,mine:56,other:60,unsure:1,purchased:3});
  assert.deepEqual(a.decision_totals,{judged:117,mine:56,other:60,unsure:1,purchased:3,purchase_only:2,dismissed_judged:1});
  assert.ok(a.leads.every((r:any)=>r.verdict==='mine'));
  const b=await f.get('&judgment=mine&cursor='+encodeURIComponent(a.next_cursor));
  assert.equal(b.leads.length,6);assert.equal(b.next_cursor,null);assert.equal(b.filtered_total,56);
  assert.deepEqual(b.judged_counts,a.judged_counts);assert.equal(new Set([...a.leads,...b.leads].map(r=>r.marketplace_item_id)).size,56);
  assert.ok([...a.leads,...b.leads].some(r=>r.dismissed));
  assert.equal((await f.get('&judgment=other')).filtered_total,60);
  assert.equal((await f.get('&judgment=unsure')).filtered_total,1);
 }finally{await f.db.close();}
});

test('purchase facets intersect identity without inventing identity or changing global counts',async()=>{
 const f=await fixture();try{
  const all=await f.get('&purchased=yes');assert.equal(all.leads.length,3);assert.equal(all.filtered_total,3);
  assert.equal(all.leads.filter((r:any)=>r.verdict===null).length,2);assert.ok(all.leads.some((r:any)=>r.dismissed));
  const both=await f.get('&judgment=mine&purchased=yes');assert.equal(both.filtered_total,1);assert.equal(both.leads[0].marketplace_item_id,'both');
  assert.deepEqual(both.judged_counts,all.judged_counts);assert.deepEqual(both.decision_totals,all.decision_totals);
  const none=await f.get('&judgment=other&purchased=yes');assert.equal(none.filtered_total,0);assert.deepEqual(none.leads,[]);
  assert.equal(all.accuracy.reduce((n:number,r:any)=>n+r.n,0),117);
  assert.ok(all.accuracy.some((r:any)=>r.tier===null));assert.ok(all.accuracy.some((r:any)=>r.tier==='unrecognized-legacy-tier'));
 }finally{await f.db.close();}
});

test('invalid and incompatible judgment filters fail explicitly',async()=>{
 const f=await fixture();try{
  for(const query of ['filter=judged&judgment=bought','filter=judged&purchased=no','filter=review&judgment=mine','filter=dismissed&purchased=yes']){
   assert.equal((await f.handler(new Request(origin+'/api/dashboard?'+query))).status,400);
  }
 }finally{await f.db.close();}
});
