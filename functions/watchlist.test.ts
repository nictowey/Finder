import assert from "node:assert/strict";
import test from "node:test";
import { createHandler, validateWatch, validPush } from "./watchlist.js";
import { html, javascript, serviceWorker } from "./watchlist-ui.js";

const origin = "https://finder.example";
function setup(authenticated=false, owner=true) {
  const queries: string[] = [];
  const db={query:async(sql:string) => {queries.push(sql);return {rows:sql.includes("owner_email") && owner ? [{data:{email:"owner@example.com"}}] : []};}};
  const request=async()=>new Response(JSON.stringify(authenticated?{user:{email:"owner@example.com",emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}}:null));
  return {handler:createHandler({db,origin,authURL:"https://auth.example/auth",fetch:request as typeof fetch}),queries};
}
test("anonymous requests cannot retrieve saved watches", async()=>{
  const {handler,queries}=setup();
  assert.equal((await handler(new Request(origin+"/api/dashboard"))).status,401);
  assert.equal(queries.some(q=>q.includes("FROM finder_watches")),false);
});
test("missing owner fails closed and foreign origins cannot mutate",async()=>{
  const {handler}=setup(false,false);
  assert.equal((await handler(new Request(origin+"/api/dashboard"))).status,503);
  assert.equal((await handler(new Request(origin+"/api/watches",{method:"POST",headers:{Origin:"https://attacker.example","Content-Type":"application/json"},body:"{}"}))).status,403);
});
test("verified owner can retrieve dashboard",async()=>{
  const {handler}=setup(true);
  const response=await handler(new Request(origin+"/api/dashboard"));
  assert.equal(response.status,200);
  assert.deepEqual((await response.json()).leads,[]);
});
test("watch inputs validate prices, release links, destinations and conditions",()=>{
  assert.equal(validateWatch({release_id:"https://www.discogs.com/release/123-Example",maximum_subtotal:"50.25"}).release_id,123);
  for(const value of [{release_id:"https://attacker.example/123"},{release_id:123,maximum_subtotal:"Infinity"},{release_id:123,maximum_subtotal:"0"},{release_id:123,country:"US"},{release_id:123,condition_ids:["<script>"]}])assert.throws(()=>validateWatch(value));
});
test("push delivery rejects arbitrary endpoints and malformed keys",()=>{
  const keys={p256dh:"a".repeat(87),auth:"a".repeat(22)};
  assert.equal(validPush({endpoint:"https://fcm.googleapis.com/fcm/send/example",keys}),true);
  for(const endpoint of ["http://localhost/", "https://169.254.169.254/", "https://fcm.googleapis.com.attacker.example/", "https://user@fcm.googleapis.com/", "https://fcm.googleapis.com:444/"])assert.equal(validPush({endpoint,keys}),false);
});
test("frontend scripts parse and do not use HTML insertion for notices",()=>{
  new Function(javascript);new Function(serviceWorker);
  assert.ok(javascript.includes("$('#notice').textContent=s"));
  assert.ok(html.includes("Scheduled scans can be delayed"));
  assert.ok(!html.includes("Checks about every 30 minutes"));
  assert.ok(javascript.includes("newestCount===1?'query':'queries'"));
});
test("OTP cookie proxy keeps credentials out of response JSON",async()=>{
  const db={query:async()=>({rows:[{data:{email:"owner@example.com"}}]})};
  const handler=createHandler({db,origin,authURL:"https://auth.example/auth",fetch:(async()=>new Response('{"token":"synthetic-credential"}',{headers:{"Set-Cookie":"session=synthetic; HttpOnly; Secure; Domain=auth.example; SameSite=None"}})) as typeof fetch});
  const response=await handler(new Request(origin+"/auth/verify-code",{method:"POST",headers:{Origin:origin,"Content-Type":"application/json"},body:JSON.stringify({email:"owner@example.com",otp:"123456"})}));
  assert.equal(response.status,200);assert.deepEqual(await response.json(),{ok:true});
  assert.ok(!response.headers.get("set-cookie")?.includes("Domain="));
  assert.ok(response.headers.get("set-cookie")?.includes("SameSite=Strict"));
});

test('notification tests require owner authentication and same-origin requests',async()=>{
 const {handler}=setup();
 assert.equal((await handler(new Request(origin+'/api/push/test',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:'{}'}))).status,401);
 const authorized=setup(true).handler;
 assert.equal((await authorized(new Request(origin+'/api/push/test',{method:'POST',headers:{Origin:'https://other.test','Content-Type':'application/json'},body:'{}'}))).status,403);
});
test('notification test sends only to enrolled device and observes a cooldown',async()=>{
 const subscription={endpoint:'https://web.push.apple.com/synthetic',keys:{p256dh:'a'.repeat(87),auth:'a'.repeat(22)}};
 let reserved=true,sent=0;
 const db={query:async(sql:string)=>({rows:sql.includes('owner_email')?[{data:{email:'owner@example.com'}}]:sql.includes('FROM finder_push_subscriptions WHERE')?[{data:subscription}]:sql.includes("key='vapid'")?[{data:{publicKey:'p',privateKey:'s'}}]:sql.includes('RETURNING key')&&reserved?[{key:'r'}]:[]})};
 const handler=createHandler({db,origin,authURL:'https://auth.example/auth',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}})),sendPush:async(sub,payload)=>{assert.deepEqual(sub,subscription);assert.equal(payload,'{"kind":"test"}');sent++;return {statusCode:201,body:'',headers:{}};}});
 const req=()=>new Request(origin+'/api/push/test',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({endpoint:subscription.endpoint})});
 const r=await handler(req());assert.equal(r.status,200);assert.equal((await r.json()).accepted,true);
 reserved=false;assert.equal((await handler(req())).status,429);assert.equal(sent,1);
});
test('Home Screen manifest is public but dashboard data stays private',async()=>{
 const {handler}=setup();const r=await handler(new Request(origin+'/manifest.webmanifest'));
 assert.equal(r.status,200);const manifest=await r.json();assert.equal(manifest.display,'standalone');
 assert.equal(manifest.start_url,'/');assert.ok(html.includes('rel="manifest"'));
 assert.equal(validateWatch({release_id:123}).alert_mode,'review_leads');
 assert.equal(validateWatch({release_id:123,alert_mode:'strict'}).alert_mode,'strict');
 assert.throws(()=>validateWatch({release_id:123,alert_mode:'exact_guaranteed'}));
});
