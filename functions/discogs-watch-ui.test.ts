import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {JSDOM} from 'jsdom';

const source=readFileSync(new URL('../src/finder/discogs_watch_ui.py',import.meta.url),'utf8');
const html=source.split('PAGE = r"""')[1].split('"""',1)[0].replaceAll('@@NONCE@@','test');
const tick=()=>new Promise<void>(r=>setImmediate(r));
const fixture=()=>({csrf:'synthetic',monitor_enabled:true,watches:[{release_id:1,public_title:'Synthetic <script>alert(1)</script>',currency:'USD',item_price_ceiling:'40.00',any_price:false,enabled:true,removed:false,revision:1,alert_initial_low:true,alert_price_drops:true,alert_count_increases:false,last_success:null,lowest_item_price:null,count:null,status:'waiting',notifications:{accepted:5,accepted_current_revision:2,pending:1,in_flight:0,uncertain:1}}]});
async function app(handler?:(path:string,opts:any)=>Promise<any>,hash='',storage:Record<string,string>={}){
 const dom=new JSDOM(html,{url:'https://sniper.example/watches'+hash,runScripts:'outside-only'});
 const w=dom.window, requests:{path:string,body:any}[]=[];
 for(const [key,value] of Object.entries(storage))w.localStorage.setItem(key,value);
 w.confirm=()=>true;
 w.scrollTo=()=>{};
 w.fetch=(async(path:any,opts:any={})=>{requests.push({path:String(path),body:opts.body?JSON.parse(opts.body):null});return handler?handler(String(path),opts):{ok:true,json:async()=>String(path).endsWith('/save')?{saved:true}:fixture()};}) as any;
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
  assert.match(a.doc.querySelector('.card details')!.textContent!,/Alert preferences: initial eligible check, price drops, aggregate count increases/);
  assert.equal(a.requests.length,1);
 }finally{a.close();}
});

function removalFixture(){
 const s=fixture();
 s.watches.push({...s.watches[0],release_id:2,public_title:'Synthetic paused record',enabled:false,revision:4});
 s.watches.push({...s.watches[0],release_id:3,public_title:'Synthetic removed <script>record</script>',enabled:false,removed:true,revision:7});
 return s;
}

test('Removed watches have a separate searchable recovery view and never count as paused',async()=>{
 const a=await app(async()=>({ok:true,json:async()=>removalFixture()}));try{
  assert.equal(a.doc.querySelectorAll('#watches .card').length,2);
  assert.equal(a.doc.querySelectorAll('#removed-watches .card').length,1);
  assert.match(a.doc.getElementById('count')!.textContent!,/1 enabled · 1 paused/);
  assert.equal(a.doc.getElementById('removed-nav-count')!.textContent,'(1)');
  assert.doesNotMatch(a.doc.getElementById('watches')!.textContent!,/Synthetic removed/);
  assert.match(a.doc.getElementById('removed-watches')!.textContent!,/Synthetic removed <script>record<\/script>/);
  assert.equal(a.doc.querySelectorAll('#removed-watches script').length,0);
  assert.equal(a.doc.querySelector('#removed-watches .pill')!.textContent,'Removed');
  assert.equal(a.doc.querySelector('#removed-watches [data-action="edit"]'),null);
  assert.equal(a.doc.querySelector('#removed-watches [data-action="toggle"]'),null);
  assert.equal(a.doc.querySelector('#removed-watches [data-action="remove"]'),null);
  assert.equal(a.doc.querySelector('#removed-watches [data-action="restore"]')!.textContent,'Restore paused');
  assert.match(a.doc.getElementById('removed-watches')!.textContent!,/not confirmed phone delivery/);
  assert.match(a.doc.getElementById('removed-watches')!.textContent!,/Recorded notifications5/);
  assert.doesNotMatch(a.doc.getElementById('removed-watches')!.textContent!,/Watch paused/);
  a.input('removed-search','absent');assert.match(a.doc.getElementById('removed-watches')!.textContent!,/No removed watches match/);
  a.input('removed-search','3');assert.equal(a.doc.querySelectorAll('#removed-watches .card').length,1);
  assert.equal(a.requests.length,1);
 }finally{a.close();}
});

test('Removal cancellation names the saved record and release, changes nothing, and keeps the draft',async()=>{
 const a=await app();try{
  a.input('release','99');a.input('title','Unsaved draft');a.input('price','12');
  let message='';a.w.confirm=text=>{message=String(text);return false;};
  a.click('[data-action="remove"]');await tick();
  assert.match(message,/Synthetic <script>alert\(1\)<\/script>/);
  assert.match(message,/Discogs release 1/);
  assert.match(message,/new checks and queued alerts/);
  assert.match(message,/already in progress may still arrive/);
  assert.match(message,/settings and notification history stay in Removed/);
  assert.match(message,/restoring keeps the watch paused/);
  assert.equal(a.requests.length,1);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Unsaved draft');
  assert.equal((a.doc.querySelector('[data-action="remove"]') as HTMLButtonElement).disabled,false);
 }finally{a.close();}
});

test('Confirmed removal sends only the action, identity, revision and token once, then refreshes recovery',async()=>{
 const snapshot=fixture();let resolveSave:(value:any)=>void=()=>{};
 const a=await app(async(path)=>path.endsWith('/state')?{ok:true,json:async()=>structuredClone(snapshot)}:new Promise(resolve=>{resolveSave=resolve;}));try{
  a.input('title','Keep my unsaved draft');let confirms=0;a.w.confirm=()=>{confirms++;return true;};
  a.click('[data-action="remove"]');a.click('[data-action="remove"]');
  assert.equal(confirms,1);
  assert.equal((a.doc.getElementById('refresh') as HTMLButtonElement).disabled,true);
  assert.equal((a.doc.getElementById('refresh-removed') as HTMLButtonElement).disabled,true);
  const saves=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(saves.length,1);
  assert.deepEqual(saves[0].body,{action:'remove',release:'1',revision:1,csrf:'synthetic'});
  snapshot.watches[0].removed=true;snapshot.watches[0].enabled=false;snapshot.watches[0].revision=2;
  resolveSave({ok:true,json:async()=>({saved:true})});await tick();await tick();
  assert.equal(a.requests.length,3);
  assert.equal(a.doc.querySelectorAll('#watches .card').length,0);
  assert.equal(a.doc.querySelectorAll('#removed-watches .card').length,1);
  assert.match(a.doc.getElementById('status')!.textContent!,/^Watch removed/);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Keep my unsaved draft');
  assert.match(a.doc.getElementById('count')!.textContent!,/0 enabled · 0 paused/);
 }finally{a.close();}
});

test('Restore is explicit and returns paused with a new revision; only a later Resume enables it',async()=>{
 const snapshot=removalFixture();
 const a=await app(async(path,opts)=>{if(path.endsWith('/save')){const body=JSON.parse(opts.body);const target=snapshot.watches.find(w=>String(w.release_id)===body.release)!;target.removed=false;target.enabled=body.action==='restore'?false:body.enabled;target.revision++;return {ok:true,json:async()=>({saved:true})};}return {ok:true,json:async()=>structuredClone(snapshot)};},'#removed');try{
  a.input('title','Unrelated draft');
  a.click('[data-action="restore"]');a.click('[data-action="restore"]');await tick();await tick();
  let saves=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(saves.length,1);
  assert.deepEqual(saves[0].body,{action:'restore',release:'3',revision:7,csrf:'synthetic'});
  assert.equal(a.doc.querySelector('#watches [data-release="3"] .pill')!.textContent,'Paused');
  assert.equal(a.doc.querySelector('#watches [data-release="3"] [data-action="toggle"]')!.textContent,'Resume');
  assert.equal(a.doc.querySelectorAll('#removed-watches .card').length,0);
  assert.match(a.doc.getElementById('status')!.textContent!,/restored and paused/);
  assert.match(a.doc.getElementById('count')!.textContent!,/1 enabled · 2 paused/);
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Unrelated draft');
  a.click('#watches [data-release="3"] [data-action="toggle"]');await tick();await tick();
  saves=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(saves.length,2);assert.equal(saves[1].body.enabled,true);assert.equal(saves[1].body.revision,8);
 }finally{a.close();}
});

for(const action of ['remove','restore']){
 test(`A lost ${action} response locks further writes until explicit refresh and never retries`,async()=>{
  const snapshot=removalFixture();const target=snapshot.watches.find(w=>w.release_id===(action==='remove'?1:3))!;
  const a=await app(async(path)=>{if(path.endsWith('/save')){target.removed=action==='remove';target.enabled=false;target.revision++;throw Error('Failed to fetch');}return {ok:true,json:async()=>structuredClone(snapshot)};});try{
   a.input('title','Retain after uncertain operation');a.click(`[data-action="${action}"]`);await tick();await tick();
   assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
   assert.match(a.doc.getElementById('status')!.textContent!,new RegExp('check whether it was '+(action==='remove'?'removed':'restored')));
   assert.match(a.doc.getElementById('status')!.textContent!,/No automatic retry/);
   assert.ok([...a.doc.querySelectorAll<HTMLButtonElement>('[data-action]')].every(button=>button.disabled));
   a.click(`[data-action="${action}"]`);await tick();assert.equal(a.requests.length,2);
   a.click('#refresh-removed');await tick();await tick();
   assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
   assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Retain after uncertain operation');
   assert.equal(a.doc.querySelectorAll(`#${action==='remove'?'removed-watches':'watches'} [data-release="${target.release_id}"]`).length,1);
  }finally{a.close();}
 });
 test(`A stale ${action} conflicts without updating its revision or retrying`,async()=>{
  let reads=0;const a=await app(async(path)=>path.endsWith('/state')?{ok:true,json:async()=>{const s=removalFixture();if(++reads>1)s.watches.forEach(w=>w.revision+=10);return s;}}:{ok:false,json:async()=>({error:'Saved watch changed. Refresh saved watches.'})});try{
   a.click(`[data-action="${action}"]`);await tick();await tick();
   assert.match(a.doc.getElementById('status')!.textContent!,/Saved watch changed/);
   assert.equal(reads,1);assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
   a.click('#refresh');await tick();await tick();
   assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
   a.click(`[data-action="${action}"]`);await tick();await tick();
   const saves=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(saves.length,2);assert.equal(saves[1].body.revision,saves[0].body.revision+10);
  }finally{a.close();}
 });
 test(`Confirmed ${action} is not reported as failed when the following state read fails`,async()=>{
  let reads=0;const a=await app(async(path)=>path.endsWith('/state')?(++reads===1?{ok:true,json:async()=>removalFixture()}:{ok:false,json:async()=>({error:'State unavailable.'})}):{ok:true,json:async()=>({saved:true})});try{
   a.click(`[data-action="${action}"]`);await tick();await tick();
   assert.match(a.doc.getElementById('status')!.textContent!,new RegExp('^Watch '+(action==='remove'?'removed':'restored and paused')+'\\. State unavailable'));
   assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);assert.equal(reads,2);
   assert.ok([...a.doc.querySelectorAll<HTMLButtonElement>('[data-action]')].every(button=>button.disabled));
  }finally{a.close();}
 });
}

test('An unverified successful HTTP response is uncertain rather than claiming a removal',async()=>{
 const a=await app(async(path)=>({ok:true,json:async()=>path.endsWith('/state')?fixture():{saved:false}}));try{
  a.click('[data-action="remove"]');await tick();await tick();
  assert.match(a.doc.getElementById('status')!.textContent!,/save result could not be verified/);
  assert.match(a.doc.getElementById('status')!.textContent!,/No automatic retry/);
  assert.equal(a.requests.length,2);
  assert.equal(a.doc.querySelectorAll('#watches .card').length,1);
 }finally{a.close();}
});

test('Known removed releases cannot be added or edited into an active watch and recovery keeps the draft',async()=>{
 for(const release of ['3','https://www.discogs.com/release/3-Synthetic','https://discogs.com/en/release/3?test=yes']){
  const a=await app(async()=>({ok:true,json:async()=>removalFixture()}),'#add');try{
   a.input('release',release);a.input('title','My unsaved replacement');a.input('price','10');
   assert.equal((a.doc.getElementById('removed-hint') as HTMLElement).hidden,false);
   assert.match(a.doc.getElementById('removed-hint')!.textContent!,/Restore the saved watch there first/);
   assert.equal((a.doc.getElementById('save') as HTMLButtonElement).disabled,true);
   a.submit();await tick();assert.equal(a.requests.length,1);
   a.click('#view-removed');a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
   assert.equal(a.w.location.hash,'#removed');assert.equal((a.doc.getElementById('removed-view') as HTMLElement).hidden,false);
   assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'My unsaved replacement');
   assert.equal((a.doc.getElementById('removed-search') as HTMLInputElement).value,'3');
   assert.equal(a.requests.length,1);
  }finally{a.close();}
 }
});

test('A draft of a newly removed watch survives refresh but cannot silently re-enable it',async()=>{
 let reads=0;const a=await app(async()=>({ok:true,json:async()=>{const s=fixture();if(++reads>1){s.watches[0].removed=true;s.watches[0].enabled=false;s.watches[0].revision=2;}return s;}}));try{
  a.click('[data-action="edit"]');a.input('title','Old unsaved edit');a.click('#refresh');await tick();await tick();
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Old unsaved edit');
  assert.equal((a.doc.getElementById('save') as HTMLButtonElement).disabled,true);
  a.submit();await tick();assert.ok(a.requests.every(r=>r.path.endsWith('/state')));
 }finally{a.close();}
});

test('Recovery navigation is read-only, supports deep links, and keeps an unsaved draft and unload guard',async()=>{
 const a=await app(async()=>({ok:true,json:async()=>removalFixture()}),'#removed');try{
  assert.equal((a.doc.getElementById('removed-view') as HTMLElement).hidden,false);
  assert.equal(a.doc.getElementById('nav-removed')!.getAttribute('aria-current'),'page');
  assert.match(a.doc.title,/Removed watches/);
  a.input('title','Preserved navigation draft');
  for(const view of ['add','removed','watches','removed','add']){
   a.w.location.hash=view;a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
   assert.equal([...a.doc.querySelectorAll<HTMLElement>('.layout>section')].filter(section=>!section.hidden).length,1);
   assert.equal(a.doc.getElementById('nav-'+view)!.getAttribute('aria-current'),'page');
  }
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Preserved navigation draft');
  const leaving=new a.w.Event('beforeunload',{cancelable:true});a.w.dispatchEvent(leaving);assert.equal(leaving.defaultPrevented,true);
  assert.equal(a.requests.length,1);
 }finally{a.close();}
});


test('Mobile controls use readable fonts and preserve viewport zoom and labels',async()=>{
 const a=await app();try{
  const viewport=a.doc.querySelector('meta[name="viewport"]')!.getAttribute('content')!;
  assert.match(viewport,/viewport-fit=cover/);
  assert.doesNotMatch(viewport,/user-scalable|maximum-scale|minimum-scale/);
  for(const field of a.doc.querySelectorAll<HTMLInputElement|HTMLSelectElement>('input,select')){
   assert.ok(parseFloat(a.w.getComputedStyle(field).fontSize)>=16,field.id);
   assert.ok(field.labels?.length,field.id+' has an accessible label');
  }
  assert.equal(a.doc.querySelector('meta[name="theme-color"]')!.getAttribute('content'),'#101827');
  assert.doesNotMatch(html,/gesturestart|gesturechange|touchstart|touchmove/);
  assert.match(html,/button,a,summary,input,select,label\.check\{touch-action:manipulation\}/);
  assert.doesNotMatch(html,/touch-action:none/);
 }finally{a.close();}
});

const filterFixture=()=>({...fixture(),watches:[
 {...fixture().watches[0],release_id:30,public_title:'Zulu',last_success:100},
 {...fixture().watches[0],release_id:20,public_title:'alpha',enabled:false,last_success:200},
 {...fixture().watches[0],release_id:10,public_title:'Alpha',last_success:200},
 {...fixture().watches[0],release_id:40,public_title:'Unknown'},
 {...fixture().watches[0],release_id:50,public_title:'Removed',removed:true,enabled:false,last_success:300},
]});
function select(a:Awaited<ReturnType<typeof app>>,id:string,value:string){const input=a.doc.getElementById(id) as HTMLSelectElement;input.value=value;input.dispatchEvent(new a.w.Event('change',{bubbles:true}));}
function releases(a:Awaited<ReturnType<typeof app>>){return [...a.doc.querySelectorAll('#watches [data-release]')].map(n=>Number((n as HTMLElement).dataset.release));}

test('Watch filters combine with search and stable sorting without writes',async()=>{
 const snapshot=filterFixture();const a=await app(async()=>({ok:true,json:async()=>structuredClone(snapshot)}));try{
  assert.deepEqual(releases(a),[10,20,30,40]);
  select(a,'watch-sort','name');assert.deepEqual(releases(a),[10,20,40,30]);
  select(a,'watch-sort','checked');assert.deepEqual(releases(a),[10,20,30,40]);
  select(a,'watch-filter','paused');assert.deepEqual(releases(a),[20]);
  assert.equal(a.doc.getElementById('shown-count')!.textContent,'Showing 1 of 4 watches');
  select(a,'watch-filter','enabled');a.input('search','alpha');assert.deepEqual(releases(a),[10]);
  a.input('search','missing');assert.deepEqual(releases(a),[]);a.click('#watches .empty button');
  assert.deepEqual(releases(a),[10,20,30,40]);assert.equal(a.doc.activeElement!.id,'search');
  assert.equal((a.doc.getElementById('watch-sort') as HTMLSelectElement).value,'checked');
  assert.deepEqual(snapshot,filterFixture());assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
  assert.equal(a.doc.querySelectorAll('#removed-watches [data-release]').length,1);
 }finally{a.close();}
});

test('Refresh and navigation preserve filters, sorting and an unsaved draft',async()=>{
 const snapshot=filterFixture();const a=await app(async()=>({ok:true,json:async()=>structuredClone(snapshot)}));try{
  select(a,'watch-filter','enabled');select(a,'watch-sort','name');a.input('search','alpha');
  a.input('title','Keep my draft');a.w.location.hash='#add';await tick();a.w.location.hash='#watches';await tick();
  snapshot.watches[2].enabled=false;a.click('#refresh');await tick();await tick();
  assert.deepEqual(releases(a),[]);assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Keep my draft');
  assert.equal((a.doc.getElementById('watch-filter') as HTMLSelectElement).value,'enabled');
  assert.equal((a.doc.getElementById('watch-sort') as HTMLSelectElement).value,'name');
  assert.equal((a.doc.getElementById('search') as HTMLInputElement).value,'alpha');
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Filters follow refreshed removals and paused restorations without changing preferences',async()=>{
 const snapshot=filterFixture();const a=await app(async()=>({ok:true,json:async()=>structuredClone(snapshot)}));try{
  select(a,'watch-filter','paused');assert.deepEqual(releases(a),[20]);
  snapshot.watches[1].removed=true;snapshot.watches[4].removed=false;
  a.click('#refresh');await tick();await tick();assert.deepEqual(releases(a),[50]);
  assert.equal(a.doc.getElementById('shown-count')!.textContent,'Showing 1 of 4 watches');
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
 }finally{a.close();}
});

test('Last-success sorting keeps epoch timestamps ahead of unknown dates and failures honest',async()=>{
 const snapshot=filterFixture();snapshot.watches[0].last_success=0;snapshot.watches[1].status='error';
 const a=await app(async()=>({ok:true,json:async()=>structuredClone(snapshot)}));try{
  select(a,'watch-sort','checked');assert.deepEqual(releases(a),[10,20,30,40]);
  assert.match(a.doc.querySelector('#watches [data-release="20"]')!.textContent!,/Latest recorded check: Failed/);
  assert.match(a.doc.querySelector('#watches [data-release="40"]')!.textContent!,/None recorded for current settings/);
  for(const id of ['watch-filter','watch-sort']){
   const select=a.doc.getElementById(id) as HTMLSelectElement;
   assert.ok(select.closest('label'));assert.equal(a.w.getComputedStyle(select).fontSize,'16px');
   assert.ok(Number.parseFloat(a.w.getComputedStyle(select).minHeight)>=44);
  }
 }finally{a.close();}
});


test('Pan-only styling has no toggle or stored preference and survives navigation and errors',async()=>{
 const assertPanOnly=(doc:Document)=>{
  assert.equal(doc.querySelector('#allow-pinch-zoom,#zoom-option'),null);
  assert.match(doc.querySelector('style')!.textContent!,/html,body\{touch-action:pan-x pan-y\}/);
  assert.doesNotMatch(doc.querySelector('script')!.textContent!,/localStorage|sessionStorage|updatePinchZoom|pinch-locked/);
 };
 // Synthetic stale keys: the old checkbox never actually persisted a preference.
 for(const value of ['true','false']){
  const a=await app(undefined,'',{'allow-pinch-zoom':value});try{
   assertPanOnly(a.doc);
   a.input('title','Preserved draft');
   a.w.location.hash='#add';a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
   a.click('#refresh');await tick();await tick();
   assertPanOnly(a.doc);
   assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Preserved draft');
   assert.equal(a.w.localStorage.getItem('allow-pinch-zoom'),value);
   assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
  }finally{a.close();}
 }
 const failed=await app(async()=>{throw Error('Offline');});try{
  assert.equal((failed.doc.getElementById('save') as HTMLButtonElement).disabled,true);
  assertPanOnly(failed.doc);
 }finally{failed.close();}
 const noScript=new JSDOM(html);try{
  assertPanOnly(noScript.window.document);
 }finally{noScript.window.close();}
});

test('Preference drafts need explicit save, survive filtering and refresh, retain their revision',async()=>{
 let reads=0;const a=await app(async(path)=>({ok:true,json:async()=>{if(path.endsWith('/save'))return {saved:true};const data=fixture();data.watches[0].revision=++reads;return data;}}));try{
  a.input('title','Keep watch draft');
  a.click('[data-preference="alert_count_increases"]');
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,0);
  a.input('search','hidden');a.input('search','');
  a.click('#refresh');await tick();await tick();
  assert.equal((a.doc.querySelector('[data-preference="alert_count_increases"]') as HTMLInputElement).checked,true);
  a.click('[data-action="preferences"]');a.click('[data-action="preferences"]');await tick();await tick();
  const writes=a.requests.filter(r=>r.path.endsWith('/save'));assert.equal(writes.length,1);
  assert.deepEqual(writes[0].body,{action:'preferences',release:'1',revision:1,alert_price_drops:true,alert_count_increases:true,csrf:'synthetic'});
  assert.equal((a.doc.getElementById('title') as HTMLInputElement).value,'Keep watch draft');
  assert.match(a.doc.getElementById('status')!.textContent!,/Alert preferences saved/);
  assert.match(a.doc.querySelector('#watches')!.textContent!,/does not mute/);
 }finally{a.close();}
});

test('Failed preference save locks actions, keeps choices, and reset uses refreshed saved settings',async()=>{
 const a=await app(async(path)=>path.endsWith('/state')?{ok:true,json:async()=>fixture()}:{ok:false,json:async()=>({error:'Watch changed.'})});try{
  a.click('[data-preference="alert_count_increases"]');a.click('[data-action="preferences"]');await tick();await tick();
  assert.equal(a.requests.filter(r=>r.path.endsWith('/save')).length,1);
  assert.equal((a.doc.querySelector('[data-action="preferences"]') as HTMLButtonElement).disabled,true);
  a.click('#refresh');await tick();await tick();
  assert.equal((a.doc.querySelector('[data-preference="alert_count_increases"]') as HTMLInputElement).checked,true);
  a.click('[data-action="reset-preferences"]');
  assert.equal((a.doc.querySelector('[data-preference="alert_count_increases"]') as HTMLInputElement).checked,false);
 }finally{a.close();}
});
