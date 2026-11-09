import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {JSDOM} from 'jsdom';

const source=readFileSync(new URL('../src/finder/discogs_watch_ui.py',import.meta.url),'utf8');
const html=source.split('PAGE = r"""')[1].split('"""',1)[0].replaceAll('@@NONCE@@','test');
const tick=()=>new Promise<void>(r=>setImmediate(r));
const fixture=()=>({csrf:'synthetic',monitor_enabled:true,watches:[{release_id:1,public_title:'Synthetic <script>alert(1)</script>',currency:'USD',item_price_ceiling:'40.00',any_price:false,enabled:true,revision:1,alert_initial_low:true,alert_price_drops:true,alert_count_increases:false,last_success:null,lowest_item_price:null,count:null,status:'waiting',notifications:{accepted:5,accepted_current_revision:2,pending:1,in_flight:0,uncertain:1}}]});
async function app(handler?:(path:string,opts:any)=>Promise<any>,hash=''){
 const dom=new JSDOM(html,{url:'https://sniper.example/watches'+hash,runScripts:'outside-only'});
 const w=dom.window, requests:{path:string,body:any}[]=[];
 w.confirm=()=>true;
 w.scrollTo=()=>{};
 w.fetch=(async(path:any,opts:any={})=>{requests.push({path:String(path),body:opts.body?JSON.parse(opts.body):null});return handler?handler(String(path),opts):{ok:true,json:async()=>fixture()};}) as any;
 w.eval(w.document.querySelector('script')!.textContent!);await tick();await tick();
 const input=(id:string,value:string)=>{const node=w.document.getElementById(id) as HTMLInputElement;node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}));};
 const click=(selector:string)=>(w.document.querySelector(selector) as HTMLButtonElement).click();
 const submit=()=>w.document.getElementById('watch-form')!.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 return {dom,w,doc:w.document,requests,input,click,submit,close:()=>dom.window.close()};
}

test('Saved labels are text, filtering works, and refresh retains the draft',async()=>{
 const a=await app();try{
  assert.equal(a.doc.querySelectorAll('#watches script').length,0);
  assert.equal(a.doc.getElementById('status')!.textContent,'');
  assert.match(a.doc.querySelector('#watches')!.textContent!,/Synthetic <script>/);
  a.input('title','My unsaved draft');a.click('#refresh');await tick();await tick();
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'My unsaved draft');
  a.input('search','no match');assert.match(a.doc.getElementById('watches')!.textContent!,/No watches match/);
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Expired authentication keeps the draft, blocks writes and never auto retries',async()=>{
 let reads=0;const a=await app(async(path)=>path.endsWith('/state')?{ok:true,json:async()=>{reads++;return fixture();}}:{ok:false,json:async()=>({error:'Sign in again.'})});try{
  a.input('release','99');a.input('title','Synthetic draft');a.input('price','12');a.submit();a.submit();await tick();await tick();
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Synthetic draft');
  assert.equal((a.doc.getElementById('save') as HTMLButtonElement).disabled,true);
  assert.match(a.doc.getElementById('status')!.textContent!,/No automatic retry/);assert.equal(reads,1);
 }finally{a.close();}
});

test('Edit preserves current revision; cancel discards draft without a write',async()=>{
 const a=await app();try{
  a.click('[data-action="edit"]');assert.equal((a.doc.getElementById('release') as HTMLInputElement).readOnly,true);
  a.input('price','30');a.w.confirm=()=>false;a.click('#cancel');assert.equal((a.doc.getElementById('price') as HTMLInputElement).value,'30');
  a.w.confirm=()=>true;a.click('#cancel');assert.equal((a.doc.getElementById('release') as HTMLInputElement).readOnly,false);
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Successful save refreshes once; failed refresh cannot resend a confirmed save',async()=>{
 let reads=0;const a=await app(async(path)=>path.endsWith('/state')?(++reads===1?{ok:true,json:async()=>fixture()}:{ok:false,json:async()=>({error:'State unavailable.'})}):{ok:true,json:async()=>({saved:true})});try{
  a.input('release','99');a.input('title','Synthetic draft');a.input('price','12');a.submit();await tick();await tick();
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);assert.equal(reads,2);
  assert.equal((a.doc.getElementById('save') as HTMLButtonElement).disabled,true);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'');
 }finally{a.close();}
});

test('Pause sends the saved revision and existing price, with no deletion',async()=>{
 const a=await app(async(path)=>({ok:true,json:async()=>path.endsWith('/state')?fixture():{saved:true}}));try{
  a.click('[data-action="toggle"]');await tick();await tick();const save=a.requests.find(r=>r.path.endsWith('/save'))!;
  assert.equal(save.body.enabled,false);assert.equal(save.body.revision,1);assert.equal(save.body.price,'40.00');assert.equal(save.body.release,'1');
 }finally{a.close();}
});

test('Lost POST response preserves draft and requires inspection, with no repeated mutation',async()=>{
 const a=await app(async(path)=>{if(path.endsWith('/save'))throw Error('Failed to fetch');return {ok:true,json:async()=>fixture()};});try{
  a.input('release','99');a.input('title','Synthetic uncertain draft');a.input('price','12');a.submit();await tick();await tick();
  assert.match(a.doc.getElementById('status')!.textContent!,/Refresh saved watches to check whether it saved before retrying/);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Synthetic uncertain draft');
  a.click('#refresh');await tick();await tick();assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
 }finally{a.close();}
});

test('Stale edit refresh does not silently overwrite its revision or draft',async()=>{
 let reads=0;const a=await app(async(path)=>path.endsWith('/state')?{ok:true,json:async()=>{const s=fixture();s.watches[0].revision=++reads;s.watches[0].public_title=reads===1?'Original title':'Newer title';return s;}}:{ok:false,json:async()=>({error:'Saved watch changed.'})});try{
  a.click('[data-action="edit"]');a.input('title','My retained edit');a.submit();await tick();await tick();
  a.click('#refresh');await tick();await tick();assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'My retained edit');
  a.click('[data-action="edit"]');assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Newer title');
  a.input('title','Reviewed edit');a.submit();await tick();await tick();
  const saves=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(saves[0].body.revision,1);assert.equal(saves[1].body.revision,2);
 }finally{a.close();}
});

test('Notification cards separate accepted history, current settings and unconfirmed work',async()=>{
 const a=await app();try{
  const text=a.doc.getElementById('watches')!.textContent!;
  assert.match(text,/Recorded notifications5/);assert.match(text,/With current watch settings2/);
  assert.match(text,/not confirmed phone delivery/);assert.match(text,/1 queued · 0 in progress · 1 uncertain/);
  assert.match(a.doc.getElementById('count')!.textContent!,/1 enabled · 0 paused/);
 }finally{a.close();}
});

test('A release and price can be submitted without a separate record label',async()=>{
 const a=await app(async(path)=>({ok:true,json:async()=>path.endsWith('/state')?fixture():{saved:true}}));try{
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).required,false);
  a.input('release','99');a.input('price','12');a.submit();await tick();await tick();
  const save=a.requests.find(r=>r.path.endsWith('/save'))!;assert.equal(save.body.title,'');assert.equal(save.body.release,'99');assert.equal(save.body.price,'12');
 }finally{a.close();}
});


test('Navigation shows only one workspace and preserves unsaved input',async()=>{
 const a=await app();try{
  const hidden=(id:string)=>(a.doc.getElementById(id) as HTMLElement).hidden;
  assert.equal(hidden('add-view'),true);assert.equal(hidden('watches-view'),false);
  a.w.location.hash='add';a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal(hidden('add-view'),false);assert.equal(hidden('watches-view'),true);
  assert.equal(a.doc.getElementById('nav-add')!.getAttribute('aria-current'),'page');
  a.input('title','Keep this draft');a.input('release','99');
  a.w.location.hash='watches';a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal(hidden('add-view'),true);assert.equal(hidden('watches-view'),false);
  a.w.location.hash='add';a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Keep this draft');
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Watch cards keep verbose history collapsed, editing opens its own view',async()=>{
 const a=await app();try{
  assert.equal((a.doc.querySelector('#watches details') as HTMLDetailsElement).open,false);
  a.click('[data-action="edit"]');
  a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal((a.doc.getElementById('watches-view') as HTMLElement).hidden,true);
  assert.equal((a.doc.getElementById('add-view') as HTMLElement).hidden,false);
  assert.match(a.doc.title,/Edit watch/);
  a.click('#cancel');a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal((a.doc.getElementById('add-view') as HTMLElement).hidden,true);
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Confirmed save returns to watches without repeating the write',async()=>{
 const a=await app();try{
  a.w.location.hash='add';a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  a.input('release','99');a.input('price','12');a.submit();await tick();await tick();
  a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
  assert.equal(a.w.location.hash,'#watches');
  assert.equal((a.doc.getElementById('add-view') as HTMLElement).hidden,true);
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
 }finally{a.close();}
});


test('Direct Add link opens its form and empty watchlist offers an Add action',async()=>{
 const a=await app(undefined,'#add');try{
  assert.equal((a.doc.getElementById('add-view') as HTMLElement).hidden,false);
  assert.equal((a.doc.getElementById('watches-view') as HTMLElement).hidden,true);
 }finally{a.close();}
 const empty=await app(async()=>({ok:true,json:async()=>({...fixture(),watches:[]})}));try{
  empty.click('#watches button');empty.w.dispatchEvent(new empty.w.HashChangeEvent('hashchange'));
  assert.equal(empty.w.location.hash,'#add');
  assert.equal((empty.doc.getElementById('add-view') as HTMLElement).hidden,false);
 }finally{empty.close();}
});


async function checkState(watch:Record<string,unknown>,monitor:unknown=true){
 return app(async()=>({ok:true,json:async()=>{const s=fixture();return {...s,monitor_enabled:monitor,watches:[{...s.watches[0],...watch}]};}}));
}

test('Never-checked watches show visible missing evidence, not a running claim',async()=>{
 const a=await checkState({});try{
  const summary=a.doc.querySelector('.check-summary')!;
  assert.equal(summary.closest('details'),null);
  assert.match(summary.textContent!,/Last successful check: None recorded for current settings/);
  assert.match(summary.textContent!,/Latest recorded check: No result recorded for current settings/);
  assert.match(a.doc.getElementById('monitor-status')!.textContent!,/Monitor setting: enabled · running status not verified/);
  assert.equal(a.doc.querySelector('.card .pill')!.textContent,'Enabled');
  assert.equal(a.doc.querySelector('.check-summary time'),null);
  assert.match(a.doc.querySelector('.card > .meta')!.textContent!,/Last observed item minimumUnknownLast observed copies for saleUnknown/);
  assert.equal(a.requests.length,1);assert.equal(a.requests[0].path,'/watches/state');
 }finally{a.close();}
});

test('Successful observations show a visible dated snapshot without inferred freshness',async()=>{
 const a=await checkState({status:'ok',last_success:1000,lowest_item_price:'18.00',count:3});try{
  const time=a.doc.querySelector('.check-summary time')!;
  assert.equal(time.closest('details'),null);assert.equal(time.getAttribute('datetime'),'1970-01-01T00:16:40.000Z');
  assert.match(a.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Successful/);
  assert.match(a.doc.querySelector('.card > .meta')!.textContent!,/Last observed item minimumUSD 18.00Last observed copies for sale3/);
  assert.doesNotMatch(a.doc.querySelector('.check-summary')!.textContent!,/stale|overdue|healthy|next check/i);
  assert.match(a.doc.querySelector('.notice')!.textContent!,/aggregate prices and counts may have changed/);
  assert.match(a.doc.querySelector('.notice')!.textContent!,/No media\/sleeve grade or US-seller filtering/);
  assert.match(a.doc.querySelector('.notice')!.textContent!,/shipping, tax and fees/);
  assert.equal(a.doc.querySelector('.notice details'),null);
 }finally{a.close();}
});

for(const [status,label] of [['error','Failed'],['unknown','Result could not be confirmed']]){
 test(`${status} results retain the dated prior observation and show the failure outside details`,async()=>{
  const a=await checkState({status,last_success:1000,lowest_item_price:'18.00',count:3});try{
   const result=a.doc.querySelector('.check-summary .error')!;
   assert.equal(result.textContent,'Latest recorded check: '+label);assert.equal(result.closest('details'),null);
   assert.equal(a.doc.querySelector('.check-summary time')!.getAttribute('datetime'),'1970-01-01T00:16:40.000Z');
   assert.match(a.doc.querySelector('.card > .meta')!.textContent!,/USD 18.00Last observed copies for sale3/);
   assert.doesNotMatch(result.textContent!,/retry|scheduled|running|delivered/i);
   assert.equal(a.requests.length,1);
  }finally{a.close();}
 });
}

test('A failed first check does not manufacture a successful sample or zero availability',async()=>{
 const a=await checkState({status:'error'});try{
  assert.match(a.doc.querySelector('.check-summary')!.textContent!,/None recorded for current settings.*Latest recorded check: Failed/);
  assert.equal(a.doc.querySelector('.check-summary time'),null);
  assert.match(a.doc.querySelector('.card > .meta')!.textContent!,/Last observed copies for saleUnknown/);
 }finally{a.close();}
});

test('Paused watches and disabled monitor settings remain distinct from check history',async()=>{
 const paused=await checkState({enabled:false,status:'ok',last_success:1000});try{
  assert.equal(paused.doc.querySelector('.card .pill')!.textContent,'Paused');
  assert.match(paused.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Successful/);
  assert.match(paused.doc.querySelector('.check-summary')!.textContent!,/Watch paused · new checks are disabled for this watch/);
  assert.equal(paused.doc.querySelector('[data-action="toggle"]')!.textContent,'Resume');
  assert.equal(paused.requests.length,1);
 }finally{paused.close();}
 const disabled=await checkState({status:'error',last_success:1000},false);try{
  assert.match(disabled.doc.getElementById('monitor-status')!.textContent!,/Monitor setting: disabled · saving watches will not start it/);
  assert.equal(disabled.doc.querySelector('.card .pill')!.textContent,'Enabled');
  assert.match(disabled.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Failed/);
  assert.match(disabled.doc.querySelector('.check-summary')!.textContent!,/Monitor disabled · new checks are stopped/);
  assert.equal(disabled.requests.length,1);
 }finally{disabled.close();}
});

test('Missing or unfamiliar status and invalid success dates stay unknown',async()=>{
 for(const status of [null,'unrecognized','__proto__']){
  const a=await checkState({status,last_success:'not-a-date',count:0},null);try{
   assert.match(a.doc.querySelector('.check-summary')!.textContent!,/Last successful check: Time unavailable/);
   assert.match(a.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Status unavailable/);
   assert.equal(a.doc.querySelector('.check-summary time'),null);
   assert.match(a.doc.getElementById('monitor-status')!.textContent!,/Monitor setting unavailable · running status not verified/);
   assert.match(a.doc.querySelector('.card > .meta')!.textContent!,/Last observed copies for sale0/);
   assert.doesNotMatch(a.doc.querySelector('.card')!.textContent!,/Invalid Date|NaN|undefined/);
  }finally{a.close();}
 }
});

test('Manual refresh updates recorded results without starting another check or losing a draft',async()=>{
 let reads=0;const a=await app(async()=>({ok:true,json:async()=>{const s=fixture();return {...s,watches:[{...s.watches[0],status:++reads===1?'error':'ok',last_success:reads===1?1000:2000,lowest_item_price:'18.00',count:3}]};}}));try{
  assert.match(a.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Failed/);
  a.input('title','Unsaved health check draft');a.click('#refresh');await tick();await tick();
  assert.match(a.doc.querySelector('.check-summary')!.textContent!,/Latest recorded check: Successful/);
  assert.equal(a.doc.querySelector('.check-summary time')!.getAttribute('datetime'),'1970-01-01T00:33:20.000Z');
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Unsaved health check draft');
  assert.equal(a.requests.length,2);assert.ok(a.requests.every(r=>r.path==='/watches/state'&&r.body===null));
 }finally{a.close();}
});

test('Price-capped watches retain their saved count-increase alert preference',async()=>{
 const a=await checkState({alert_count_increases:true});try{
  assert.match(a.doc.querySelector('.card details')!.textContent!,/Alert preferences: initial availability, price drops, count increases/);
  assert.equal(a.requests.length,1);
 }finally{a.close();}
});
