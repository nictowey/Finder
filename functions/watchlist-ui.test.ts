import assert from 'node:assert/strict';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {html,javascript,stylesheet} from './watchlist-ui.js';
import {dashboardFixture,fixtureDashboard} from './watchlist-ui-fixture.js';

const tick=()=>new Promise<void>(resolve=>setImmediate(resolve));
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>{resolve=r;});return {promise,resolve};}
async function app(initial=dashboardFixture(),fetcher?:(url:string,opts:any)=>Promise<Response>){
 const dom=new JSDOM(html,{url:'https://finder.example/',runScripts:'outside-only',pretendToBeVisual:true});
 const requests:{url:string;body:any}[]=[];const saved=structuredClone(initial);
 const w=dom.window;w.matchMedia=(()=>({matches:false})) as any;w.HTMLElement.prototype.scrollIntoView=()=>{};w.setInterval=(()=>0) as any;
 w.fetch=(async(url:any,opts:any={})=>{const body=opts.body?JSON.parse(opts.body):null;requests.push({url:String(url),body});if(fetcher)return fetcher(String(url),opts);if(String(url).startsWith('/api/dashboard'))return response(saved);const row=saved.leads.find(r=>r.marketplace_item_id===body?.marketplace_item_id);if(row){if(String(url)==='/api/verdict'){row.verdict=body.verdict;row.verdict_tier ||= row.data.status;row.verdict_decided_at ||= new Date().toISOString();row.verdict_provenance ||= 'recorded_prediction';}if(String(url)==='/api/purchase')row.purchased=body.purchased;return response({ok:true,verdict:row.verdict,purchased:row.purchased,verdict_tier:row.verdict_tier,verdict_decided_at:row.verdict_decided_at,verdict_provenance:row.verdict_provenance});}return response({ok:true});}) as any;
 w.eval(javascript+';window.testSavedState=()=>JSON.parse(JSON.stringify(state));');await tick();await tick();
 return {dom,w,doc:w.document,requests,saved,close:()=>dom.window.close(),click:(selector:string)=>(w.document.querySelector(selector) as HTMLButtonElement).click(),change:(selector:string,value:string)=>{const el=w.document.querySelector(selector) as HTMLSelectElement;el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));}};
}

test('an HTML gateway response during refresh keeps saved state and offers a readable retry',async()=>{
 for(const status of [502,503]){
  const data=dashboardFixture();data.leads[0].verdict='mine';let reads=0;
  const a=await app(data,async()=>++reads===1?response(data):new Response('<html>synthetic gateway details</html>',{status}));
  try{
   const before=a.doc.querySelectorAll('.lead').length;a.click('#refresh');await tick();await tick();
   assert.equal(a.doc.querySelectorAll('.lead').length,before);
   assert.equal((a.w as any).testSavedState().leads[0].verdict,'mine');
   assert.equal(a.doc.querySelector('#dashboard')?.hasAttribute('hidden'),false);
   assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);
   assert.equal(a.doc.querySelector('#notice')?.textContent,'Finder returned an unreadable response. Please try Refresh.');
   assert.ok(!a.doc.querySelector('#notice')?.textContent?.includes('synthetic gateway'));
  }finally{a.close();}
 }
});

test('a persisted save with an unreadable response reconciles without repeating the POST',async()=>{
 const data=dashboardFixture();let writes=0;
 const a=await app(data,async(url,options)=>{
  if(url==='/api/verdict'){
   writes++;data.leads[0].verdict=JSON.parse(options.body).verdict;
   return new Response('<html>synthetic gateway details</html>',{status:503});
  }
  return response(data);
 });
 try{
  a.click('[data-verdict="0|mine"]');await tick();await tick();await tick();
  assert.equal(writes,1);assert.equal((a.w as any).testSavedState().leads[0].verdict,'mine');
  assert.match(a.doc.querySelector('#notice')!.textContent!,/Couldn’t confirm the save/);
  assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);
  a.click('[data-filter="judged"]');await tick();await tick();
  assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'true');
  assert.equal(writes,1);
 }finally{a.close();}
});

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
test('invalidated evidence gives a fresh-check warning without a current Likely or price assurance',async()=>{
 for(const reason of ['seller_changed','refresh_failed']){
  const data:any=dashboardFixture();data.leads=[data.leads[0]];const row=data.leads[0];row.evidence_stale=true;row.evidence_invalidated_reason=reason;
  row.listing={title:'Listing evidence needs a fresh check',listing_url:'https://www.ebay.com/itm/1',item_specifics:{}};
  row.data={status:'possible_pressing',budget:'needs_refresh',notify:false,verify:[reason==='seller_changed'?'seller_details_changed':'detail_refresh_failed']};
  row.verdict='mine';row.verdict_tier='family_review';
  const a=await app(data);try{
   a.click('[data-filter="judged"]');await tick();assert.match(a.doc.querySelector('.lead .badge')!.textContent!,/Needs fresh check/);
   assert.match(a.doc.querySelector('.price')!.textContent!,/Total unknown/);assert.ok(a.doc.querySelector('[data-recheck]'));
   assert.match(a.doc.querySelector('.photo-empty')!.textContent!,reason==='seller_changed'?/seller.*details changed/i:/detail check failed/i);
   assert.equal(a.doc.querySelector('[data-verdict="0|mine"]')?.getAttribute('aria-pressed'),'true');
  }finally{a.close();}
 }
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

test('Judged categories query the whole dataset and combine independently with Bought',async()=>{
 const data=dashboardFixture(),source=data.leads[0];data.leads=Array.from({length:121},(_,i)=>({...structuredClone(source),marketplace_item_id:String(i).padStart(3,'0'),verdict:i<70?'mine':i<110?'other':i<120?'unsure':null,purchased:i%2===0,dismissed:i===0}));
 const a=await app(data,async(url)=>response(fixtureDashboard(data,url)));
 try{a.click('[data-filter="judged"]');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,50);assert.match(a.doc.querySelector('#leadcount')!.textContent!,/121 matching/);assert.equal(a.doc.querySelector('[data-judgment-count="mine"]')!.textContent,'(70)');
 a.click('[data-judgment="mine"]');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,50);assert.match(a.doc.querySelector('#leadcount')!.textContent!,/70 matching/);
 a.click('#nextpage');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,20);assert.match(a.doc.querySelector('#leads')!.textContent!,/Dismissed/);
 a.click('[data-judgment="other"]');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,40);assert.equal((a.doc.querySelector('#firstpage') as HTMLButtonElement).disabled,true);assert.ok(!a.requests.at(-1)!.url.includes('cursor='));
 a.click('#purchased-filter');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,20);assert.match(a.doc.querySelector('#leadcount')!.textContent!,/20 matching/);assert.equal(a.doc.querySelector('[data-judgment-count="other"]')!.textContent,'(40)');
 a.click('[data-judgment="unsure"]');await tick();assert.equal(a.doc.querySelectorAll('.lead').length,5);assert.equal(a.doc.querySelectorAll('[data-verdict][aria-pressed="true"]').length,5);
 a.click('[data-judgment="all"]');await tick();assert.match(a.doc.querySelector('#leadcount')!.textContent!,/61 matching/);
 a.click('[data-filter="review"]');await tick();assert.match(a.requests.at(-1)!.url,/judgment=all&purchased=all/);
 }finally{a.close();}
});

test('Your verdicts totals include every original tier and refresh when entered',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='mine';data.leads[0].verdict_tier=null;data.leads[1].verdict='unsure';data.leads[1].verdict_tier='unexpected-legacy';data.leads[2].purchased=true;
 const a=await app(data,async(url)=>response(fixtureDashboard(data,url)));
 try{assert.match(a.doc.querySelector('#decision-totals')!.textContent!,/2 saved pressing judgments/);assert.match(a.doc.querySelector('#accuracy')!.textContent!,/Unknown prediction/);assert.match(a.doc.querySelector('#watches')!.textContent!,/1 can’t tell/);
 data.leads[2].verdict='other';data.leads[2].verdict_tier='conflicting';a.w.location.hash='#accuracy';await tick();await tick();assert.match(a.doc.querySelector('#decision-totals')!.textContent!,/3 saved pressing judgments/);assert.equal(a.doc.querySelector('[data-open-judgment="other"] strong')!.textContent,'1');assert.equal(a.doc.querySelector('[data-open-judgment="bought"] strong')!.textContent,'1');
 a.click('[data-open-judgment="unsure"]');await tick();await tick();assert.equal(a.w.location.hash,'#review');assert.equal(a.doc.querySelectorAll('.lead').length,1);assert.equal(a.doc.querySelector('[data-judgment="unsure"]')!.getAttribute('aria-pressed'),'true');
 }finally{a.close();}
});

test('confirmed save updates totals immediately even if the following reload fails',async()=>{
 const data=dashboardFixture();let reads=0;
 const a=await app(data,async(url,opts)=>{if(url.startsWith('/api/dashboard'))return ++reads===1?response(fixtureDashboard(data,url)):response({error:'Temporary outage'},500);const body=JSON.parse(opts.body),r=data.leads.find(x=>x.marketplace_item_id===body.marketplace_item_id)!;r.verdict=body.verdict;r.verdict_tier=r.data.status;return response({ok:true,...r});});
 try{a.click('[data-verdict="0|other"]');await tick();await tick();assert.equal(a.doc.querySelector('[data-open-judgment="other"] strong')!.textContent,'1');assert.match(a.doc.querySelector('#decision-totals')!.textContent!,/1 saved pressing judgments/);assert.match(a.doc.querySelector('#verdict-updated')!.textContent!,/refresh pending/);assert.equal((a.doc.querySelector('#refresh-verdicts') as HTMLButtonElement).disabled,false);
 }finally{a.close();}
});

test('save, edit, clear and purchase update category totals after server reload',async()=>{
 const data=dashboardFixture();const fetcher=async(url:string,opts:any)=>{if(url.startsWith('/api/dashboard'))return response(fixtureDashboard(data,url));const body=JSON.parse(opts.body),r=data.leads.find(x=>x.marketplace_item_id===body.marketplace_item_id)!;if(url==='/api/verdict'){r.verdict=body.verdict;r.verdict_tier ||= r.data.status;}else r.purchased=body.purchased;return response({ok:true,...r});};
 const a=await app(data,fetcher);try{
 a.click('[data-verdict="0|mine"]');await tick();await tick();a.click('[data-filter="judged"]');await tick();assert.equal(a.doc.querySelector('[data-judgment-count="mine"]')!.textContent,'(1)');
 a.click('[data-verdict="0|other"]');await tick();await tick();assert.equal(a.doc.querySelector('[data-judgment-count="mine"]')!.textContent,'(0)');assert.equal(a.doc.querySelector('[data-judgment-count="other"]')!.textContent,'(1)');
 a.click('[data-purchase="0"]');await tick();await tick();a.click('[data-clear="0"]');await tick();await tick();assert.equal(a.doc.querySelector('[data-judgment-count="all"]')!.textContent,'(1)');assert.equal(a.doc.querySelector('[data-judgment-count="other"]')!.textContent,'(0)');assert.equal(a.doc.querySelector('#purchased-count')!.textContent,'(1)');assert.match(a.doc.querySelector('#decision-totals')!.textContent!,/0 saved pressing judgments/);
 const b=await app(data,fetcher);try{b.click('[data-filter="judged"]');await tick();assert.equal(b.doc.querySelectorAll('.lead').length,1);assert.equal(b.doc.querySelectorAll('[data-verdict][aria-pressed="true"]').length,0);}finally{b.close();}
 }finally{a.close();}
});

test('category switches ignore obsolete responses and empty filtered states are actionable',async()=>{
 const data=dashboardFixture();data.leads[0].verdict='mine';const slow=deferred<Response>();
 const a=await app(data,async(url)=>url.includes('judgment=other')?slow.promise:response(fixtureDashboard(data,url)));
 try{a.click('[data-filter="judged"]');await tick();a.click('[data-judgment="other"]');a.click('[data-judgment="unsure"]');await tick();slow.resolve(response(fixtureDashboard(data,'/api/dashboard?filter=judged&judgment=other')));await tick();assert.equal(a.doc.querySelector('[data-judgment="unsure"]')!.getAttribute('aria-pressed'),'true');assert.match(a.doc.querySelector('#leads')!.textContent!,/No saved listings match/);assert.match(a.doc.querySelector('#leads')!.textContent!,/turn off Bought only/);
 }finally{a.close();}
});

test('purchase-only save and identity clear keep all saved counters separate after failed refresh',async()=>{
 const data=dashboardFixture();let failReads=false;
 const a=await app(data,async(url,opts)=>{if(url.startsWith('/api/dashboard'))return failReads?response({error:'Refresh failed'},500):response(fixtureDashboard(data,url));const body=JSON.parse(opts.body),r=data.leads.find(x=>x.marketplace_item_id===body.marketplace_item_id)!;if(url==='/api/purchase')r.purchased=body.purchased;else{r.verdict=body.verdict;r.verdict_tier ||= r.data.status;}return response({ok:true,...r});});
 try{failReads=true;a.click('[data-purchase="0"]');await tick();await tick();let counters=(a.w as any).testSavedState().decision_totals;assert.equal(counters.judged,0);assert.equal(counters.purchase_only,1);assert.equal((a.w as any).testSavedState().judged_counts.all,1);
 failReads=false;a.click('[data-filter="judged"]');await tick();a.click('[data-verdict="0|mine"]');await tick();await tick();failReads=true;a.click('[data-clear="0"]');await tick();await tick();counters=(a.w as any).testSavedState().decision_totals;assert.equal(counters.judged,0);assert.equal(counters.mine,0);assert.equal(counters.purchase_only,1);assert.equal((a.w as any).testSavedState().judged_counts.all,1);assert.equal(counters.dismissed_judged,0);
 }finally{a.close();}
});

test('fresh-check notices respect paused watches and avoid promising the next worker',async()=>{
 for(const paused of [false,true]){
  const data=dashboardFixture();data.watches[1].config.enabled=!paused;
  const error='This watch is paused. Resume it before requesting a fresh check.';
  const a=await app(data,async url=>url.startsWith('/api/dashboard')?response(data):response(paused?{error}:{ok:true},paused?409:200));
  try{
   a.click('[data-recheck]');await tick();await tick();
   assert.equal(a.requests.filter(r=>r.url==='/api/refresh-lead').length,1);
   assert.equal(a.requests.some(r=>r.url.startsWith('/api/watches')),false);
   assert.equal(a.doc.querySelector('#notice')?.textContent,paused?error:'Fresh check queued. Finder will retrieve current evidence when the watch can run.');
  }finally{a.close();}
 }
});

test('newest remains the default and opting in renders only supplied, escaped ordering clues',async()=>{
 const data=dashboardFixture();data.leads[0].pressing_clues=['Recorded numbering <img src=x onerror=alert(1)>'];
 data.leads[1].listing.title='Signed numbered target-color rare pressing';data.leads[1].pressing_clues=[];
 const a=await app(data);
 try{
  assert.equal(new URL(a.requests[0].url,'https://finder.example').searchParams.get('order'),'newest');
  assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'newest');
  assert.equal((a.doc.querySelector('#order-filter') as HTMLElement).hidden,false);
  assert.equal((a.doc.querySelector('#order-note') as HTMLElement).hidden,true);
  assert.ok(!a.doc.querySelector('#leads')!.textContent!.includes('Ordering clues'));
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'Newest results');
  a.change('#order','pressing_clues');await tick();
  assert.match(a.requests.at(-1)!.url,/order=pressing_clues/);
  assert.equal((a.doc.querySelector('#order-note') as HTMLElement).hidden,false);
  assert.match(a.doc.querySelector('#order-note')!.textContent!,/changes order, not Finder’s identity assessment/);
  assert.match(a.doc.querySelector('#order-note')!.textContent!,/All other leads stay in the list/);
  assert.match(a.doc.querySelector('#order-note')!.textContent!,/checked within the last hour/);
  assert.match(a.doc.querySelector('#order-note')!.textContent!,/Each group is newest first/);
  const clues=Array.from(a.doc.querySelectorAll('.evidence-row')).filter(el=>el.querySelector('strong')?.textContent==='Ordering clues');
  assert.equal(clues.length,3);assert.equal(clues[0].querySelector('p')!.textContent,data.leads[0].pressing_clues[0]);
  assert.match(clues[1].textContent!,/No supporting ordering clues/);assert.equal(clues[1].querySelector('.evidence-icon')!.textContent,'↕');assert.ok(stylesheet.includes('.ordering-clues .evidence-icon{color:var(--muted)}'));assert.equal(a.doc.querySelector('img[src="x"]'),null);
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'First results');
  a.change('#order','newest');await tick();assert.ok(!a.doc.querySelector('#leads')!.textContent!.includes('Ordering clues'));
 }finally{a.close();}
});

test('provided clue ordering pages through every row and keeps newest cursors compatible',async()=>{
 const data=dashboardFixture(),source=data.leads[1];
 data.leads=Array.from({length:52},(_,i)=>({...structuredClone(source),marketplace_item_id:'sample-'+String(i).padStart(3,'0'),pressing_clues:i===0?['Recorded numbering clue']:[]}));
 const a=await app(data,async url=>response(fixtureDashboard(data,url)));
 const ids=()=>Array.from(a.doc.querySelectorAll('.lead')).map(el=>JSON.parse((el as HTMLElement).dataset.key!)[2]);
 try{
  assert.equal(ids()[0],'sample-051');a.click('#nextpage');await tick();assert.equal(ids().length,2);
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'Older results');
  const newestCursor=new URL(a.requests.at(-1)!.url,'https://finder.example').searchParams.get('cursor')!;
  assert.ok(Array.isArray(JSON.parse(newestCursor)));
  a.change('#order','pressing_clues');await tick();assert.ok(!a.requests.at(-1)!.url.includes('cursor='));
  const first=ids();assert.equal(first[0],'sample-000');assert.equal(first.length,50);
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'First results');
  a.click('#nextpage');await tick();const second=ids();assert.equal(second.length,2);
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'More results');
  assert.equal(new Set([...first,...second]).size,52);assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,true);
  a.click('#firstpage');await tick();assert.equal(ids()[0],'sample-000');assert.ok(!a.requests.at(-1)!.url.includes('cursor='));
 }finally{a.close();}
});

test('price, order, saved judgment and filter changes reset cursors and unsupported filters reset the order',async()=>{
 const data=dashboardFixture();data.next_cursor='synthetic-page-cursor' as any;data.leads[0].verdict='mine';
 const a=await app(data);
 const firstPage=()=>assert.ok(!a.requests.at(-1)!.url.includes('cursor='));
 try{
  a.change('#order','pressing_clues');await tick();a.click('#nextpage');await tick();assert.match(a.requests.at(-1)!.url,/cursor=/);
  a.change('#prices','all');await tick();firstPage();assert.match(a.requests.at(-1)!.url,/prices=all.*order=pressing_clues/);
  a.click('#nextpage');await tick();a.click('[data-filter="judged"]');await tick();firstPage();
  assert.equal((a.doc.querySelector('#order-filter') as HTMLElement).hidden,false);assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'pressing_clues');
  a.click('#nextpage');await tick();a.click('[data-judgment="mine"]');await tick();firstPage();
  a.click('#nextpage');await tick();a.click('#purchased-filter');await tick();firstPage();assert.match(a.requests.at(-1)!.url,/judgment=mine&purchased=yes&order=pressing_clues/);
  a.click('[data-filter="review"]');await tick();firstPage();assert.match(a.requests.at(-1)!.url,/judgment=all&purchased=all&order=pressing_clues/);
  for(const filter of ['possible_pressing','family_review','conflicting','unrelated','unavailable','dismissed']){
   a.click('[data-filter="'+filter+'"]');await tick();firstPage();assert.match(a.requests.at(-1)!.url,/order=newest/);
   assert.equal((a.doc.querySelector('#order-filter') as HTMLElement).hidden,true);assert.equal((a.doc.querySelector('#order-note') as HTMLElement).hidden,true);
   a.click('[data-filter="review"]');await tick();assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'newest');
   a.change('#order','pressing_clues');await tick();
  }
  a.click('#nextpage');await tick();a.change('#order','newest');await tick();firstPage();
 }finally{a.close();}
});

test('a late clue-order response or order-change conflict cannot replace a newer order',async()=>{
 for(const conflict of [false,true]){
  const data=dashboardFixture(),slow=deferred<Response>();data.next_cursor='synthetic-page-cursor' as any;
  const a=await app(data,async url=>url.includes('order=pressing_clues')&&(!conflict||url.includes('cursor='))?slow.promise:response(data));
  try{
   a.change('#order','pressing_clues');if(conflict){await tick();a.click('#nextpage');}
   assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,true);
   a.click('#nextpage');a.change('#order','newest');await tick();const count=a.requests.length;
   const old=structuredClone(data);old.leads[0].listing.title='Obsolete clue-order response';
   slow.resolve(conflict?response({error:'The review order changed. Start again from the first page.',code:'inbox_order_changed'},409):response(old));await tick();await tick();
   assert.equal(a.requests.length,count);assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'newest');
   assert.ok(!a.doc.querySelector('#leads')!.textContent!.includes('Obsolete clue-order response'));
   assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'Newest results');assert.equal(a.doc.querySelector('#notice')!.textContent,'');
   assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,false);
  }finally{a.close();}
 }
});

test('a pending verdict keeps its row locked while order changes wait and reload the latest selection',async()=>{
 const data=dashboardFixture(),save=deferred<Response>(),oldPage=deferred<Response>();data.next_cursor='synthetic-page-cursor' as any;
 const a=await app(data,async(url,opts)=>{
  if(url.startsWith('/api/dashboard'))return url.includes('cursor=')?oldPage.promise:response(data);
  return save.promise;
 });
 try{
  a.click('#nextpage');a.click('[data-verdict="0|mine"]');a.change('#order','pressing_clues');a.change('#prices','all');a.change('#order','newest');a.change('#order','pressing_clues');
  const count=a.requests.length;a.click('#nextpage');a.click('[data-verdict="0|other"]');a.click('[data-purchase="0"]');
  assert.equal(a.requests.length,count);assert.equal(a.requests.filter(r=>r.url==='/api/verdict').length,1);
  assert.equal((a.doc.querySelector('[data-verdict="0|mine"]') as HTMLButtonElement).disabled,true);
  assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,true);
  data.leads[0].verdict='mine';save.resolve(response({ok:true,...data.leads[0]}));await tick();await tick();
  assert.match(a.requests.at(-1)!.url,/prices=all.*order=pressing_clues/);assert.ok(!a.requests.at(-1)!.url.includes('cursor='));
  oldPage.resolve(response(dashboardFixture()));await tick();
  assert.equal((a.w as any).testSavedState().leads[0].verdict,'mine');assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'pressing_clues');
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'First results');assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);
 }finally{a.close();}
});

test('a changed clue-order page restarts once with the selected filters and locks repeated page clicks',async()=>{
 const data=dashboardFixture(),restart=deferred<Response>();data.next_cursor='synthetic-page-cursor' as any;data.leads[0].verdict='mine';data.leads[0].purchased=true;
 let resetPending=false;
 const a=await app(data,async url=>{
  if(url.includes('cursor=')){resetPending=true;return response({error:'The review order changed. Start again from the first page.',code:'inbox_order_changed'},409);}
  return resetPending?restart.promise:response(data);
 });
 try{
  a.click('[data-filter="judged"]');await tick();a.click('[data-judgment="mine"]');await tick();a.click('#purchased-filter');await tick();a.change('#order','pressing_clues');await tick();
  a.click('#nextpage');a.click('#nextpage');await tick();const count=a.requests.length;
  a.click('#nextpage');a.click('#firstpage');assert.equal(a.requests.length,count);
  const retry=new URL(a.requests.at(-1)!.url,'https://finder.example').searchParams;
  assert.equal(retry.get('filter'),'judged');assert.equal(retry.get('judgment'),'mine');assert.equal(retry.get('purchased'),'yes');assert.equal(retry.get('order'),'pressing_clues');assert.equal(retry.has('cursor'),false);
  assert.match(a.doc.querySelector('#notice')!.textContent!,/review order changed.*Restarted from the first page/);
  restart.resolve(response(data));await tick();await tick();
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'First results');assert.equal((a.doc.querySelector('#firstpage') as HTMLButtonElement).disabled,true);
  assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,false);
  assert.equal(a.requests.filter(r=>r.url.includes('cursor=')).length,1);
 }finally{a.close();}
});

test('a first-page conflict after automatic restart stops safely without looping or changing the order',async()=>{
 const data=dashboardFixture();data.next_cursor='synthetic-page-cursor' as any;let changed=false;
 const error={error:'The review order changed. Start again from the first page.',code:'inbox_order_changed'};
 const a=await app(data,async url=>{if(url.includes('cursor='))changed=true;return changed?response(error,409):response(data);});
 try{
  a.change('#order','pressing_clues');await tick();const before=a.requests.length;a.click('#nextpage');await tick();await tick();await tick();
  assert.equal(a.requests.length,before+2);assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'pressing_clues');
  assert.equal(a.doc.querySelector('#notice')!.textContent,error.error);assert.equal((a.doc.querySelector('#refresh') as HTMLButtonElement).disabled,false);
  assert.equal((a.doc.querySelector('#nextpage') as HTMLButtonElement).disabled,true);
  a.click('#nextpage');await tick();assert.equal(a.requests.length,before+2);
 }finally{a.close();}
});

test('browser Back and Forward between views retain the selected review order and page',async()=>{
 const data=dashboardFixture();data.next_cursor='synthetic-page-cursor' as any;
 const a=await app(data);
 const traverse=async(direction:'back'|'forward')=>{const changed=new Promise<void>(resolve=>a.w.addEventListener('hashchange',()=>resolve(),{once:true}));a.w.history[direction]();await changed;await tick();};
 try{
  a.change('#order','pressing_clues');await tick();a.click('#nextpage');await tick();
  a.w.location.hash='#watches';await tick();await tick();a.w.location.hash='#health';await tick();await tick();
  await traverse('back');assert.equal((a.doc.querySelector('#watchespanel') as HTMLElement).hidden,false);
  await traverse('back');assert.equal((a.doc.querySelector('#reviewpanel') as HTMLElement).hidden,false);
  assert.equal((a.doc.querySelector('#order') as HTMLSelectElement).value,'pressing_clues');assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'More results');
  await traverse('forward');assert.equal((a.doc.querySelector('#watchespanel') as HTMLElement).hidden,false);
  await traverse('back');assert.equal((a.doc.querySelector('#reviewpanel') as HTMLElement).hidden,false);
  assert.equal(a.doc.querySelector('#pagestatus')!.textContent,'More results');
 }finally{a.close();}
});


test('fixture clue order is binary and uses marketplace to resolve cursor ties',()=>{
 const data=dashboardFixture(),source=data.leads[1];
 data.leads=[
  {...structuredClone(source),marketplace_item_id:'sample-001',pressing_clues:['Recorded numbering clue','Recorded catalog number clue']},
  {...structuredClone(source),marketplace_item_id:'sample-002',pressing_clues:['Recorded numbering clue']},
  {...structuredClone(source),marketplace_item_id:'sample-003',pressing_clues:[]}
 ];
 const first=fixtureDashboard(data,'/api/dashboard?order=pressing_clues');
 assert.deepEqual(first.leads.map(r=>r.marketplace_item_id),['sample-002','sample-001','sample-003']);
 data.leads=Array.from({length:51},(_,i)=>({...structuredClone(source),marketplace:'synthetic-market-'+String(i).padStart(3,'0'),marketplace_item_id:'sample-shared-id',pressing_clues:['Recorded numbering clue']}));
 const page=fixtureDashboard(data,'/api/dashboard?order=pressing_clues'),tail=page.leads.at(-1)!;
 assert.equal(page.leads.length,50);assert.equal(JSON.parse(page.next_cursor!).after[3],tail.marketplace);
 const next=fixtureDashboard(data,'/api/dashboard?order=pressing_clues&cursor='+encodeURIComponent(page.next_cursor!));
 assert.equal(next.leads.length,1);assert.equal(new Set([...page.leads,...next.leads].map(r=>r.marketplace)).size,51);
});
