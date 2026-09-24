import assert from "node:assert/strict";
import test from "node:test";
import { createHandler, MAX_WATCHES, validateWatch, validImage, validPush } from "./watchlist.js";
import { html, javascript, serviceWorker } from "./watchlist-ui.js";
import { inboxQuery } from "./inbox-query.js";

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
test("default inbox includes unclear candidates and reports review tiers per watch",async()=>{
 let filter:unknown;
 const db={query:async(q:string,v:unknown[]=[])=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(q.includes('FROM finder_watches ORDER BY id'))return {rows:[{id:'watch',config:{enabled:true},catalog_observed_at:null}]};
  if(q.includes("AS tier,count(*)"))return {rows:[{watch_id:'watch',tier:'family_review',n:3},{watch_id:'watch',tier:'unrelated',n:8}]};
  if(q.includes('FROM finder_inbox i JOIN listings'))filter=v[0];
  return {rows:[]};
 }};
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:owner});
 const response=await handler(new Request(origin+'/api/dashboard'));
 assert.equal(response.status,200);
 const data=await response.json();
 assert.equal(filter,'review');
 assert.deepEqual(data.watches[0].inbox_counts,{family_review:3,unrelated:8});
 assert.ok(inboxQuery.includes("$1='review' AND i.data->>'status' IN ('possible_pressing','family_review')"));
 assert.ok(html.includes('data-filter="review" class="selected"'));
 assert.ok(javascript.includes("filter='review'"));
 assert.ok(javascript.includes("filter==='review'?['possible_pressing','family_review']"));
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

test('new discovery controls preserve owner authentication and mutation origin gates',async()=>{
 for(const endpoint of ['/api/refresh-lead']){
  const anonymous=setup().handler;
  assert.equal((await anonymous(new Request(origin+endpoint,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:'{}'}))).status,401);
  const owner=setup(true).handler;
  assert.equal((await owner(new Request(origin+endpoint,{method:'POST',headers:{Origin:'https://evil.test','Content-Type':'application/json'},body:'{}'}))).status,403);
 }
});

test('inbox pagination keeps older references without exposing stale provider content',async()=>{
 let values:unknown[]=[],sql='';
 const rows=Array.from({length:51},(_,i)=>({watch_id:'watch',marketplace:'ebay',marketplace_item_id:String(i),first_seen_at:'2026-01-01T00:00:00Z',last_seen_at:'2026-01-01T00:00:00Z',revision:1,dismissed:false,
 data:{status:'possible_pressing',watch_revision:1,subtotal:'123.45',clues:['private clue'],verify:[]},listing:{title:'private old title',item_specifics:{old:['content']},listing_url:'https://www.ebay.com/itm/123',details_observed_at:'2026-01-01T00:00:00Z'}}));
 const db={query:async(q:string,v:unknown[]=[])=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(q.includes('FROM finder_inbox i JOIN listings')){sql=q;values=v;return {rows:structuredClone(rows)};}
  return {rows:[]};
 }};
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}}))});
 const response=await handler(new Request(origin+'/api/dashboard?filter=possible_pressing'));
 assert.equal(response.status,200);
 const data=await response.json();assert.equal(data.leads.length,50);assert.ok(data.next_cursor);
 assert.equal(data.leads[0].evidence_stale,true);
 assert.ok(!JSON.stringify(data).includes('private old title'));assert.ok(!JSON.stringify(data).includes('123.45'));
 assert.ok(sql.includes('LIMIT 51'));assert.ok(!sql.includes('i.last_seen_at >= $1'));
 await handler(new Request(origin+'/api/dashboard?filter=possible_pressing&cursor='+encodeURIComponent(data.next_cursor)));
 assert.deepEqual(values.slice(1,4),JSON.parse(data.next_cursor));
});

test('coverage wording separates worker success, page exhaustion, and pending evaluation',()=>{
 assert.ok(javascript.includes('awaiting details/evaluation'));
 assert.ok(javascript.includes('Search exhaustion is for this pass'));
 assert.ok(javascript.includes('covered through'));
 assert.ok(javascript.includes('query_passes'));
 assert.ok(javascript.includes('min overdue'));
 assert.ok(html.includes('Next page'));
 assert.ok(javascript.includes('details_observed_at'));
});


test('price scope defaults to saved ceilings and all-prices is explicit', async()=>{
 let values:unknown[]=[];
 const db={query:async(q:string,v:unknown[]=[])=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(q.includes('FROM finder_inbox i JOIN listings'))values=v;
  return {rows:[]};
 }};
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}}))});
 assert.equal((await handler(new Request(origin+'/api/dashboard'))).status,200);
 assert.equal(values[4],'watch');
 assert.ok(Number.isFinite(Date.parse(String(values[5]))));
 assert.equal((await handler(new Request(origin+'/api/dashboard?prices=all'))).status,200);
 assert.equal(values[4],'all');
 assert.equal((await handler(new Request(origin+'/api/dashboard?prices=invalid'))).status,400);
 assert.ok(html.includes('Within each watch’s ceiling'));
 assert.ok(html.includes('All prices'));
});

const owner=async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}}));

test('watch settings validate cheat sheets, the gamble price, auctions and extra searches',()=>{
 const watch=validateWatch({release_id:1,gamble_max:'25.50',auction_alert_minutes:60,extra_queries:[' Exampel Album '],
  tells:[{kind:'color',value:' Pink/Green ',required:true}],anti_tells:[{kind:'keyword',value:'reissue'}]});
 assert.equal(watch.gamble_max,'25.50');assert.equal(watch.auction_alert_minutes,60);
 assert.deepEqual(watch.extra_queries,['Exampel Album']);
 assert.deepEqual(watch.tells,[{kind:'color',value:'Pink/Green',required:true}]);
 assert.deepEqual(watch.anti_tells,[{kind:'keyword',value:'reissue',required:false}]);
 const defaults=validateWatch({release_id:1});
 assert.equal(defaults.auction_alert_minutes,120);assert.deepEqual(defaults.tells,[]);assert.equal(defaults.gamble_max,null);
 for(const value of [{gamble_max:'-1'},{auction_alert_minutes:5000},{auction_alert_minutes:'60'},{extra_queries:['a','b','c']},{extra_queries:['  ']},
  {tells:[{kind:'price',value:'x'}]},{tells:[{kind:'color',value:''}]},{tells:[{kind:'color',value:'x',required:'yes'}]},{anti_tells:Array(13).fill({kind:'keyword',value:'x'})}])
  assert.throws(()=>validateWatch({release_id:1,...value}));
});

test('photos are limited to eBay image hosts and allowed by the content policy',async()=>{
 assert.equal(validImage('https://i.ebayimg.com/images/g/abc/s-l1600.jpg'),true);
 for(const url of ['http://i.ebayimg.com/a.jpg','https://i.ebayimg.com.evil.test/a.jpg','https://evil.test/a.jpg','javascript:alert(1)',null])assert.equal(validImage(url),false);
 const {handler}=setup();
 const response=await handler(new Request(origin+'/'));
 assert.ok(response.headers.get('Content-Security-Policy')?.includes("img-src 'self' https://i.ebayimg.com"));
 const fresh=new Date().toISOString();
 const db={query:async(q:string)=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(q.includes('FROM finder_inbox i JOIN listings'))return {rows:[{watch_id:'w',marketplace:'ebay',marketplace_item_id:'1',first_seen_at:fresh,last_seen_at:fresh,revision:1,dismissed:false,
   data:{status:'family_review',watch_revision:1,clues:[],verify:[]},listing:{title:'t',listing_url:'https://www.ebay.com/itm/1',details_observed_at:fresh,item_specifics:{},
   primary_image:'https://i.ebayimg.com/a.jpg',additional_images:['https://evil.test/b.jpg','https://i.ebayimg.com/c.jpg']}}]};
  if(q.includes('FROM finder_verdicts WHERE'))return {rows:[{watch_id:'w',marketplace:'ebay',marketplace_item_id:'1',verdict:'mine'}]};
  if(q.includes('GROUP BY tier'))return {rows:[{tier:'family_review',verdict:'mine',n:1}]};
  return {rows:[]};
 }};
 const data=await (await createHandler({db,origin,authURL:'https://auth.example',fetch:owner})(new Request(origin+'/api/dashboard'))).json();
 assert.deepEqual(data.leads[0].listing.images,['https://i.ebayimg.com/a.jpg','https://i.ebayimg.com/c.jpg']);
 assert.equal(data.leads[0].verdict,'mine');assert.equal(data.accuracy[0].n,1);assert.equal(data.max_watches,MAX_WATCHES);
});

test('verdicts are validated, recorded against the inbox row and can be cleared',async()=>{
 const calls:[string,unknown[]][]=[];let found=true;
 const db={query:async(q:string,v:unknown[]=[])=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  calls.push([q,v]);
  return {rows:q.includes('RETURNING verdict')&&found?[{verdict:v[3]}]:[]};
 }};
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:owner});
 const post=(body:unknown)=>handler(new Request(origin+'/api/verdict',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}));
 const key={watch_id:'w',marketplace:'ebay',marketplace_item_id:'1'};
 assert.equal((await post({...key,verdict:'mine'})).status,200);
 assert.ok(calls.at(-1)![0].includes('FROM finder_inbox'));assert.equal(calls.at(-1)![1][3],'mine');
 assert.equal((await post({...key,verdict:null})).status,200);
 assert.ok(calls.at(-1)![0].startsWith('DELETE FROM finder_verdicts'));
 assert.equal((await post({...key,verdict:'maybe'})).status,400);
 assert.equal((await post({...key,marketplace:'other',verdict:'mine'})).status,400);
 found=false;
 assert.equal((await post({...key,verdict:'other'})).status,404);
});

test('watch capacity follows the shared maximum',async()=>{
 let values:unknown[]=[];
 const db={query:async(q:string,v:unknown[]=[])=>{
  if(q.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(q.includes('generate_series')){values=v;return {rows:[]};}
  return {rows:[]};
 }};
 const response=await createHandler({db,origin,authURL:'https://auth.example',fetch:owner})(new Request(origin+'/api/watches',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({release_id:1})}));
 assert.equal(response.status,409);assert.equal(values[4],MAX_WATCHES);
 assert.ok((await response.json()).error.includes(String(MAX_WATCHES)));
 assert.ok(javascript.includes("state.max_watches"));assert.ok(html.includes('Cheat sheet'));
 assert.ok(javascript.includes('data-verdict'));assert.ok(javascript.includes('Ask the seller'));
});
