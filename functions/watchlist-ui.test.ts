import assert from 'node:assert/strict';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {html,javascript,stylesheet} from './watchlist-ui.js';
import {dashboardFixture} from './watchlist-ui-fixture.js';

const tick=()=>new Promise<void>(resolve=>setImmediate(resolve));
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>{resolve=r;});return {promise,resolve};}
async function app(initial=dashboardFixture(),fetcher?:(url:string,opts:any)=>Promise<Response>){
 const dom=new JSDOM(html,{url:'https://finder.example/',runScripts:'outside-only',pretendToBeVisual:true});
 const requests:{url:string;body:any}[]=[];const saved=structuredClone(initial);
 const w=dom.window;w.matchMedia=(()=>({matches:false})) as any;w.HTMLElement.prototype.scrollIntoView=()=>{};w.setInterval=(()=>0) as any;
 w.fetch=(async(url:any,opts:any={})=>{const body=opts.body?JSON.parse(opts.body):null;requests.push({url:String(url),body});if(fetcher)return fetcher(String(url),opts);if(String(url).startsWith('/api/dashboard'))return response(saved);const row=saved.leads.find(r=>r.marketplace_item_id===body?.marketplace_item_id);if(row){if(String(url)==='/api/verdict'){row.verdict=body.verdict;row.verdict_tier ||= row.data.status;row.verdict_decided_at ||= new Date().toISOString();row.verdict_provenance ||= 'recorded_prediction';}if(String(url)==='/api/purchase')row.purchased=body.purchased;return response({ok:true,verdict:row.verdict,purchased:row.purchased,verdict_tier:row.verdict_tier,verdict_decided_at:row.verdict_decided_at,verdict_provenance:row.verdict_provenance});}return response({ok:true});}) as any;
 w.eval(javascript);await tick();await tick();
 return {dom,w,doc:w.document,requests,saved,close:()=>dom.window.close(),click:(selector:string)=>(w.document.querySelector(selector) as HTMLButtonElement).click()};
}

test('review leads are first, with secondary views and accessible touch controls',async()=>{
 const a=await app();try{
 assert.equal(a.doc.querySelector('#reviewpanel')?.hasAttribute('hidden'),false);
 assert.equal(a.doc.querySelector('#healthpanel')?.hasAttribute('hidden'),true);
 assert.equal(a.doc.querySelector('#watchespanel')?.hasAttribute('hidden'),true);
 assert.equal(a.doc.querySelectorAll('.lead').length,3);
 assert.equal(a.doc.querySelector('.hero-photo img')?.getAttribute('alt'),'Listing photo 1 of 2');
 assert.equal(a.doc.querySelector('[data-filter="review"]')?.getAttribute('aria-pressed'),'true');
 assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'false');
 assert.ok(stylesheet.includes('min-height:44px'));
 assert.ok(stylesheet.includes('prefers-reduced-motion'));
 assert.ok(a.doc.querySelector('a[href="https://www.discogs.com"]'));
 }finally{a.close();}
});

test('all identity choices save and rehydrate independently from purchase',async()=>{
 for(const verdict of ['mine','other','unsure']){
  const a=await app();try{
   a.click('[data-verdict="0|'+verdict+'"]');await tick();await tick();
   assert.equal(a.saved.leads[0].verdict,verdict);assert.equal(a.saved.leads[0].purchased,false);
   const request=a.requests.find(r=>r.url==='/api/verdict')!;
   assert.equal(request.body.observed_tier,'possible_pressing');assert.equal(request.body.observed_evaluated_at,a.saved.leads[0].last_seen_at);
   a.click('[data-filter="judged"]');await tick();await tick();
   assert.equal(a.doc.querySelector('[data-verdict="0|'+verdict+'"]')?.getAttribute('aria-pressed'),'true');
   assert.match(a.doc.querySelector('.verdict-status')!.textContent!,/Saved/);
   const b=await app(a.saved);try{b.click('[data-filter="judged"]');await tick();assert.equal(b.doc.querySelector('[data-verdict="0|'+verdict+'"]')?.getAttribute('aria-pressed'),'true');}finally{b.close();}
  }finally{a.close();}
 }
});

test('selected judgment is idempotent; only explicit clear sends null and retains purchase',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='mine';data.leads[0].purchased=true;
 const a=await app(data);try{a.click('[data-filter="judged"]');await tick();const n=a.requests.length;a.click('[data-verdict="0|mine"]');await tick();assert.equal(a.requests.length,n);
 a.click('[data-clear="0"]');await tick();await tick();assert.equal(a.saved.leads[0].verdict,null);assert.equal(a.saved.leads[0].purchased,true);
 assert.equal(a.doc.querySelector('[data-purchase="0"]')?.getAttribute('aria-pressed'),'true');
 assert.ok(a.doc.querySelector('.judgment-meta')?.textContent?.includes('does not confirm the pressing'));
 }finally{a.close();}
});

test('bought is purchase-only, moves to Judged, and does not count as positive accuracy',async()=>{
 const a=await app();try{a.click('[data-purchase="0"]');await tick();await tick();assert.equal(a.saved.leads[0].purchased,true);assert.equal(a.saved.leads[0].verdict,null);
 assert.equal(a.doc.querySelectorAll('.lead').length,2);
 a.click('[data-filter="judged"]');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,1);
 assert.equal(a.doc.querySelectorAll('.verdicts [aria-pressed="true"]').length,0);
 assert.equal(a.doc.querySelector('[data-purchase="0"]')?.getAttribute('aria-pressed'),'true');
 assert.ok(!javascript.includes("count(t,'bought')"));assert.ok(!javascript.includes("count(tier,'bought')"));
 }finally{a.close();}
});

test('one in-flight mutation locks identity, clear, purchase and dismiss on that row',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='mine';const pending=deferred<Response>();
 const a=await app(data,async(url)=>url.startsWith('/api/dashboard')?response(data):pending.promise);
 try{a.click('[data-filter="judged"]');await tick();a.click('[data-verdict="0|other"]');
 assert.equal(a.doc.querySelector('.judgment')?.getAttribute('aria-busy'),'true');
 for(const selector of ['[data-verdict="0|mine"]','[data-purchase="0"]','[data-clear="0"]','[data-dismiss="0"]'])assert.equal((a.doc.querySelector(selector) as HTMLButtonElement).disabled,true);
 a.click('[data-purchase="0"]');a.click('[data-clear="0"]');assert.equal(a.requests.filter(r=>r.body).length,1);
 pending.resolve(response({error:'Unavailable'},503));await tick();assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'true');assert.match(a.doc.querySelector('.verdict-status')!.textContent!,/Couldn’t confirm/);
 }finally{a.close();}
});

test('stale evidence error retains the existing judgment and asks for fresh review',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='other';const a=await app(data,async(url)=>url.startsWith('/api/dashboard')?response(data):response({error:'Changed'},409));
 try{a.click('[data-filter="judged"]');await tick();a.click('[data-verdict="0|mine"]');await tick();assert.equal(a.doc.querySelector('[data-verdict="0|other"]')?.getAttribute('aria-pressed'),'true');assert.match(a.doc.querySelector('.verdict-status')!.textContent!,/listing changed/);}finally{a.close();}
});

test('an old dashboard response cannot overwrite a newer successful save',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='mine';const old=deferred<Response>();let getCount=0;
 const a=await app(data,async(url,opts)=>{if(url.startsWith('/api/dashboard')){getCount++;if(getCount===3)return old.promise;return response(data);}const body=JSON.parse(opts.body);data.leads[0].verdict=body.verdict;return response({ok:true,verdict:body.verdict,purchased:false,verdict_tier:'possible_pressing',verdict_decided_at:new Date().toISOString(),verdict_provenance:'recorded_prediction'});});
 try{a.click('[data-filter="judged"]');await tick();const obsolete=structuredClone(data);a.click('#refresh');a.click('[data-verdict="0|other"]');await tick();await tick();old.resolve(response(obsolete));await tick();assert.equal(a.doc.querySelector('[data-verdict="0|other"]')?.getAttribute('aria-pressed'),'true');assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'false');}finally{a.close();}
});

test('a late filter response cannot replace newer navigation',async()=>{
 const data=dashboardFixture();const slow=deferred<Response>();const a=await app(data,async(url)=>url.includes('family_review')?slow.promise:response(data));
 try{a.click('[data-filter="family_review"]');a.click('[data-filter="possible_pressing"]');await tick();slow.resolve(response(data));await tick();assert.equal(a.doc.querySelectorAll('.lead').length,1);assert.match(a.doc.querySelector('.badge')!.textContent!,/Likely yours/);}finally{a.close();}
});

test('view navigation and editor Cancel preserve saved decisions and focus',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='unsure';const a=await app(data);
 try{a.w.location.hash='#watches';await tick();assert.equal((a.doc.querySelector('#watchespanel') as HTMLElement).hidden,false);a.click('[data-edit="sample-a"]');assert.equal((a.doc.querySelector('#editor') as HTMLElement).hidden,false);a.click('#cancel');assert.equal((a.doc.querySelector('#editor') as HTMLElement).hidden,true);assert.equal(a.doc.activeElement?.id,'add');a.w.location.hash='#review';await tick();a.click('[data-filter="judged"]');await tick();assert.equal(a.doc.querySelector('[data-verdict="0|unsure"]')?.getAttribute('aria-pressed'),'true');}finally{a.close();}
});

test('seller content remains escaped, image URLs allowlisted, and stale references honest',async()=>{
 const data=dashboardFixture();data.leads[0].listing.title='<img src=x onerror=alert(1)>';data.leads[0].listing.images.push('https://evil.example/track');
 const a=await app(data);try{assert.equal(a.doc.querySelectorAll('img[src="x"]').length,0);assert.equal(a.doc.querySelector('img[src^="https://evil.example"]'),null);assert.match(a.doc.querySelector('#leads')!.textContent!,/Older seller content is withheld/);assert.match(a.doc.querySelector('.lead h3')!.textContent!,/<img/);}finally{a.close();}
});

test('photo controls, broken photo fallback and disclosures survive refresh',async()=>{
 const a=await app();try{a.click('[data-photo="0|1"]');assert.equal(a.doc.querySelector('.hero-photo img')?.getAttribute('src'),a.saved.leads[0].listing.images[1]);(a.doc.querySelector('.lead-details') as HTMLDetailsElement).open=true;a.click('#refresh');await tick();assert.equal(a.doc.querySelector('.hero-photo img')?.getAttribute('src'),a.saved.leads[0].listing.images[1]);assert.equal((a.doc.querySelector('.lead-details') as HTMLDetailsElement).open,true);a.doc.querySelector('.hero-photo img')!.dispatchEvent(new a.w.Event('error'));assert.equal((a.doc.querySelector('.photo-error') as HTMLElement).hidden,false);}finally{a.close();}
});

test('a failed save superseding a refresh releases all dashboard loading controls',async()=>{
 const data=dashboardFixture(),old=deferred<Response>(),saving=deferred<Response>();let gets=0;
 const a=await app(data,async(url)=>url.startsWith('/api/dashboard')?(++gets===2?old.promise:response(data)):saving.promise);
 try{a.click('#refresh');a.click('[data-verdict="0|mine"]');saving.resolve(response({error:'Unavailable'},503));await tick();old.resolve(response(data));await tick();await tick();
 assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);assert.equal(a.doc.querySelector('#leads')?.getAttribute('aria-busy'),'false');assert.match(a.doc.querySelector('.verdict-status')!.textContent!,/Couldn’t confirm/);
 }finally{a.close();}
});

test('a successful row remains saved when another simultaneous row fails',async()=>{
 const data=dashboardFixture(),first=deferred<Response>(),second=deferred<Response>();
 const a=await app(data,async(url,opts)=>url.startsWith('/api/dashboard')?response(data):JSON.parse(opts.body).marketplace_item_id===data.leads[0].marketplace_item_id?first.promise:second.promise);
 try{a.click('[data-verdict="0|mine"]');a.click('[data-verdict="1|other"]');data.leads[0].verdict='mine';first.resolve(response({ok:true,verdict:'mine',purchased:false,verdict_tier:'possible_pressing',verdict_decided_at:new Date().toISOString(),verdict_provenance:'recorded_prediction'}));await tick();assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'true');second.resolve(response({error:'Unavailable'},503));await tick();await tick();
 assert.equal(a.doc.querySelectorAll('.lead').length,2);a.click('[data-filter="judged"]');await tick();assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'true');assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);
 }finally{a.close();}
});

test('narrow-card CSS constrains wrapped menus, long evidence and large prices',()=>{
 assert.ok(stylesheet.includes('.filters{position:relative;'));
 assert.ok(stylesheet.includes('.more-filters{position:static}.more-filters>div{top:calc(100% + 6px);left:0;right:0;min-width:0}'));
 assert.ok(stylesheet.includes('.price-row{display:flex;flex-wrap:wrap;'));
 assert.ok(stylesheet.includes('grid-template-columns:20px minmax(0,1fr)'));
 assert.ok(stylesheet.includes('.evidence-row>div{min-width:0;overflow-wrap:anywhere}'));
});

test('paired editor fields align below optional hints and verdict copy preserves legacy uncertainty',()=>{
 assert.ok(stylesheet.includes('.fields>label>input,.fields>label>select,.fields>label>textarea{margin-top:auto}'));
 assert.ok(stylesheet.includes('repeat(auto-fit,minmax(240px,1fr))'));
 assert.ok(stylesheet.includes('gap:16px;margin-bottom:18px'));
 assert.ok(html.includes('the prediction recorded with each judgment'));
 assert.ok(!html.includes('when you first judged each listing'));
});
