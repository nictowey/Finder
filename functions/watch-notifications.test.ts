import assert from "node:assert/strict";
import test from "node:test";
import { deliver } from "../scripts/send_watch_notifications.mjs";

test("no browser subscription leaves inbox usable without sending",async()=>{
  const db={query:async()=>({rows:[]})};
  const result=await deliver(db,async()=>{throw new Error("must not send");});
  assert.equal(result.delivered,0);
});
test("dispatcher invalidates old policy and watch revisions before claiming alerts",async()=>{
  let checked=false;
  const db={query:async(sql:string,values:any[]=[])=>{
    if(sql.includes("key='vapid'"))return {rows:[{data:{publicKey:'p',privateKey:'s'}}]};
    if(sql.includes('SELECT id,data'))return {rows:[{id:'device',data:{}}]};
    if(sql.includes("status='expired'")){
      assert.equal(values[1],'private-target-review-v2');
      assert.ok(sql.includes("(i.data->>'policy')=$2"));
      assert.ok(sql.includes("(i.data->>'watch_revision')=w.revision::text"));
      checked=true;
    }
    if(sql.includes('RETURNING id'))assert.ok(checked);
    return {rows:[]};
  }};
  const result=await deliver(db,async()=>{throw new Error('Expired assessments must not send');});
  assert.equal(result.delivered,0);
  assert.ok(checked);
});
test("push payload is generic and retries keep the stable browser topic",async()=>{
  const queries:{sql:string;values:any[]}[]=[];
  const db={query:async(sql:string,values:any[]=[])=>{
    queries.push({sql,values});
    if(sql.includes("key='vapid'"))return {rows:[{data:{publicKey:"synthetic",privateKey:"synthetic"}}]};
    if(sql.includes('SELECT id,data'))return {rows:[{id:'device',data:{endpoint:'https://example.test'}}]};
    if(sql.includes('RETURNING id'))return {rows:[{id:'event'}]};
    return {rows:[]};
  }};
  let sent=0;
  const result=await deliver(db,async(_subscription:any,payload:string,options:any)=>{
    sent++;assert.deepEqual(JSON.parse(payload),{kind:'review-inbox'});
    assert.equal(options.topic,'finder-inbox');assert.equal(options.TTL,1800);
  });
  assert.equal(sent,1);assert.equal(result.delivered,1);
  assert.ok(queries.some(q=>q.values[1]==='sent'));
});
test("transient delivery failure remains retryable, expired device is removed",async()=>{
  const updates:{sql:string;values:any[]}[]=[];
  const db={query:async(sql:string,values:any[]=[])=>{
    updates.push({sql,values});
    if(sql.includes("key='vapid'"))return {rows:[{data:{publicKey:'p',privateKey:'s'}}]};
    if(sql.includes('SELECT id,data'))return {rows:[{id:'expired',data:{expired:true}},{id:'retry',data:{expired:false}}]};
    if(sql.includes('RETURNING id'))return {rows:[{id:'event'}]};
    return {rows:[]};
  }};
  const result=await deliver(db,async(subscription:any)=>{throw {statusCode:subscription.expired?410:503};});
  assert.equal(result.failed_devices,1);
  assert.ok(updates.some(q=>q.sql.includes('DELETE FROM finder_push_subscriptions')&&q.values[0]==='expired'));
  assert.ok(updates.some(q=>q.values[1]==='pending'));
});
