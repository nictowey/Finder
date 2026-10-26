/* Offline browser-state checks. All provider, resource, socket and DNS access is denied. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {TextEncoder}=require('node:util');
const {JSDOM,VirtualConsole}=require('jsdom');
const html=fs.readFileSync(process.argv[2],'utf8');
require('node:net').Socket.prototype.connect=() => { throw new Error('Network disabled.'); };
require('node:dns').lookup=() => { throw new Error('DNS disabled.'); };
const denyDispatcher={dispatch(){throw new Error('External resource forbidden.');},close(){return Promise.resolve();},destroy(){return Promise.resolve();}};
const clone=value=>structuredClone(value);
let saved={revision:0,targets:[],owned:[],mode:'dry_run',purchasing_enabled:false,capabilities:{listing_read_enabled:false},
  in_app_alerts:{channel:'in_app_only',messages_sent:0,purchase_allowed:false,control_revision:null,pending:[]},
  hosted_schedule:{paused:true,monitor_releases:[],targets:[],reader_ready:false,purchase_allowed:false}};
let mutationFailure=null, readFailure=null, failReadAfterSave=false, delayed=null;
let evaluation={eligible_for_review:false,reasons:['Pressing evidence is unknown.'],total:null,purchase_allowed:false};
let listingFailure=null;
let listingResponse={listing:{listing_id:'456',release_id:123,media:'VG+',sleeve:null,seller_percent:99.8,feedback:0,
  item:'20.00',currency:'USD',reported_shipping:null,reported_shipping_currency:null,shipping_blocked:true,available:true,
  url:'https://www.discogs.com/sell/item/456'},
  evaluation:{eligible_for_review:false,reasons:['Destination shipping is unknown.','Tax and fees are unknown.','Pressing evidence is unverified.'],total:null,purchase_allowed:false},
  warning:'Reported shipping is not a final destination quote; tax/fees/pressing unverified.'};
const calls=[],errors=[],requestTimeouts=[];
const response=(status,data)=>({ok:status>=200 && status<300,status,json:async()=>clone(data)});
async function mockFetch(path, options) {
  assert.ok(['/api/snapshot','/api/target','/api/owned','/api/evaluate','/api/listing','/api/change-alert/ack','/api/monitor-release'].includes(path));
  assert.equal(options.mode,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');
  assert.equal(options.cache,'no-store');calls.push({path,options});
  if(path === '/api/snapshot') {
    assert.equal(options.method,'GET');
    if(readFailure){const error=readFailure;readFailure=null;throw error;}
    return response(200,saved);
  }
  assert.equal(options.method,'POST');assert.equal(options.headers['X-Finder-CSRF'],'dom-test-token');
  assert.equal(options.headers['Content-Type'],'application/json');
  const input=JSON.parse(options.body);
  if(delayed) await delayed.promise;
  if(path === '/api/change-alert/ack') {
    assert.deepEqual(Object.keys(input).sort(),['change_sequences','expected_control_revision','expected_revision']);
    assert.equal(input.expected_revision,saved.revision);
    assert.equal(input.expected_control_revision,null);
    saved.in_app_alerts.pending=saved.in_app_alerts.pending.filter(row=>!input.change_sequences.includes(row.change_sequence));
    return response(200,{acknowledged_change_sequences:input.change_sequences,messages_sent:0});
  }
  if(path === '/api/monitor-release') {
    assert.deepEqual(Object.keys(input).sort(),['enabled','expected_revision','release_id']);
    assert.equal(input.expected_revision,saved.revision);
    assert.equal(typeof input.enabled,'boolean');
    assert.equal(saved.hosted_schedule.paused,true);
    const match=String(input.release_id).match(/(?:release\/)?([1-9][0-9]*)/);
    assert.ok(match);
    const release_id=Number(match[1]);assert.ok(Number.isSafeInteger(release_id));
    saved.hosted_schedule.monitor_releases=saved.hosted_schedule.monitor_releases
      .filter(row=>row.release_id!==release_id).concat({release_id,enabled:input.enabled})
      .sort((a,b)=>a.release_id-b.release_id);
    saved.revision++;
    return response(200,{release_id,enabled:input.enabled,rules_revision:saved.revision,
      hosted_schedule:saved.hosted_schedule,purchase_allowed:false});
  }
  if(path === '/api/listing') {
    assert.deepEqual(Object.keys(input).sort(),['listing','release_id']);
    assert.equal(typeof input.release_id,'number');assert.equal(typeof input.listing,'string');
    assert.equal(saved.capabilities.listing_read_enabled,true);
    if(listingFailure) {const failure=listingFailure;listingFailure=null;
      if(failure instanceof Error) throw failure;return response(failure,{error:'Known listing is unavailable.'});}
    return response(200,{...listingResponse,evaluation:{rules_revision:saved.revision,...listingResponse.evaluation}});
  }
  if(path === '/api/evaluate') {
    assert.deepEqual(Object.keys(input).sort(),['observation','release_id']);
    assert.equal(typeof input.release_id,'number');assert.equal(input.observation.source,'synthetic');
    if(saved.targets.find(row=>row.release_id===input.release_id)?.enabled === false) {
      return response(200,{eligible_for_review:false,reasons:['Target is paused.'],total:null,purchase_allowed:false,rules_revision:saved.revision});
    }
    return response(200,{rules_revision:saved.revision,...evaluation});
  }
  if(mutationFailure) {
    const failure=mutationFailure;mutationFailure=null;
    if(failure instanceof Error) throw failure;
    if(failure===409) {saved.revision++;saved.targets[0].max_total='80.00';}
    return response(failure,{error:failure===409?'Saved state changed.':'Invalid target rules.'});
  }
  assert.equal(input.expected_revision,saved.revision);
  if(path === '/api/target') {
    assert.deepEqual(Object.keys(input).sort(),['expected_revision','target']);
    assert.equal(typeof input.target.release_id,'string');
    assert.equal(typeof input.target.max_total,'string');
    assert.equal(typeof input.target.min_seller_percent,'number');
    assert.equal(typeof input.target.min_feedback,'number');
    assert.equal(typeof input.target.enabled,'boolean');
    const releaseId=Number(input.target.release_id.match(/(?:release\/)?([0-9]+)/)[1]);
    const target={...input.target,release_id:releaseId,destination_postal:input.target.destination_postal.toUpperCase()};
    saved.targets=saved.targets.filter(row=>row.release_id!==releaseId).concat(target);
  } else {
    assert.deepEqual(Object.keys(input).sort(),['expected_revision','owned','release_id']);
    assert.equal(typeof input.owned,'boolean');assert.equal(typeof input.release_id,'number');
    saved.owned=saved.owned.filter(id=>id!==input.release_id);
    if(input.owned) saved.owned.push(input.release_id);
  }
  saved.revision++;
  if(failReadAfterSave){failReadAfterSave=false;readFailure=new TypeError('offline after accepted save');}
  return response(200,saved);
}
function open() {
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
  return new JSDOM(html,{url:'http://127.0.0.1:18765/',runScripts:'dangerously',resources:{dispatcher:denyDispatcher},virtualConsole,
    beforeParse(w){w.fetch=mockFetch;w.TextEncoder=TextEncoder;
      const timer=w.setTimeout.bind(w);w.setTimeout=(fn,delay,...args)=>{requestTimeouts.push(delay);return timer(fn,delay,...args);};
      w.Storage.prototype.setItem=()=>{throw new Error('Browser persistence forbidden.');};
      w.Storage.prototype.getItem=()=>{throw new Error('Browser persistence forbidden.');};}});
}
async function settled(w) {
  for(let i=0;i<100;i++) {
    await new Promise(resolve=>setImmediate(resolve));
    if(w.document.getElementById('targets').getAttribute('aria-busy')==='false') return;
  }
  throw new Error('UI did not settle.');
}
let dom,w,d;
const el=id=>d.getElementById(id);
const fill=fields=>Object.entries(fields).forEach(([id,value])=>{el(id).value=value;});
const submit=id=>el(id).dispatchEvent(new w.Event('submit',{cancelable:true}));
const posts=path=>calls.filter(call=>call.path===path && call.options.method==='POST');
function clickText(text) {
  const button=[...d.querySelectorAll('button')].find(row=>row.textContent===text);
  assert.ok(button,text);button.click();
}
(async()=>{
  dom=open();w=dom.window;d=w.document;await settled(w);
  assert.match(d.body.textContent,/Purchasing disabled\. Rules only\. No live discovery\./);
  assert.match(el('candidate-pipeline-status').textContent,/fixture-tested only/);
  assert.match(el('candidate-pipeline-status').textContent,/No purchase authorization, hosted candidate worker, live checkout, or record purchasing is running/);
  assert.match(el('candidate-pipeline-status').textContent,/Hosted browser reads are stopped/);
  assert.match(el('candidate-pipeline-status').textContent,/partial marketplace page.*cannot establish full availability or qualify a row by itself/);
  assert.equal(d.querySelectorAll('script').length,1);assert.equal(d.querySelectorAll('script[src],link[href],img,iframe').length,0);
  assert.equal(d.querySelector('script').nonce,'dom-test-nonce');assert.equal(d.querySelector('style').nonce,'dom-test-nonce');
  assert.equal(el('save-target').disabled,false);assert.equal(el('evaluate').disabled,true);
  assert.equal(el('read-listing').disabled,true);assert.match(el('listing-capability').textContent,/disabled on this server/);
  submit('listing-form');await settled(w);assert.equal(posts('/api/listing').length,0);
  assert.equal(el('destination-country').value,'US');assert.equal(el('destination-postal').value,'');
  assert.equal(el('enabled').checked,true);
  for(const entry of ['https://www.discogs.com/release/28720177-56-Nights','5520534',
    'https://www.discogs.com/release/13053315-Not-All-Heroes']) {
    el('monitor-release-id').value=entry;submit('monitor-release-form');await settled(w);
  }
  assert.equal(saved.targets.length,0);
  assert.deepEqual(saved.hosted_schedule.monitor_releases.map(row=>row.release_id),[5520534,13053315,28720177]);
  assert.equal(saved.hosted_schedule.paused,true);
  assert.match(el('hosted-schedule-status').textContent,/paused; 3 monitor-only IDs saved/);
  assert.equal(posts('/api/listing').length,0);
  const pause=[...d.querySelectorAll('.monitor-release-toggle')][0];pause.click();await settled(w);
  assert.equal(saved.hosted_schedule.monitor_releases[0].enabled,false);
  assert.equal(saved.hosted_schedule.paused,true);
  const alertText='<img src=x onerror="window.pwned=true">';
  saved.in_app_alerts.pending=[{change_sequence:1,release_id:123,listing_id:'456',kind:'new',
    observed_at:alertText,delivery:'in_app_only',purchase_allowed:false}];
  el('refresh').click();await settled(w);
  assert.match(el('change-alert-list').textContent,/In-app|Review only; no buying action/);
  assert.equal(el('change-alert-list').querySelectorAll('img,script').length,0);
  assert.equal(el('change-alert-list').querySelector('a').href,'https://www.discogs.com/sell/item/456');
  clickText('Mark reviewed');await settled(w);
  assert.equal(posts('/api/change-alert/ack').length,1);
  assert.equal(saved.in_app_alerts.pending.length,0);
  assert.match(el('change-alert-list').textContent,/No pending listing changes/);
  assert.equal(el('destination-postal').required,true);assert.equal(el('destination-postal').maxLength,16);
  for(const postal of ['90210','90210-1234','SW1A 1AA']) {el('destination-postal').value=postal;assert.equal(el('destination-postal').checkValidity(),true);}
  for(const postal of ['','90210_1234','90210\\1234']) {el('destination-postal').value=postal;assert.equal(el('destination-postal').checkValidity(),false);}
  el('destination-postal').value='';
  assert.deepEqual([...el('min-media').options].map(row=>row.value),['P','F','G','G+','VG','VG+','NM','M']);
  assert.deepEqual([...el('currency').options].map(row=>row.value),['USD','EUR','GBP','CAD','AUD']);
  const malicious='<img src=x onerror="window.pwned=true"><script>window.pwned=true</script>';
  fill({'release-id':'https://www.discogs.com/release/123-Synthetic',label:malicious,'max-total':'50.00','destination-postal':'90210'});
  submit('target-form');await settled(w);
  assert.equal(saved.targets.length,1);assert.equal(saved.targets[0].release_id,123);
  assert.equal(saved.targets[0].enabled,true);
  assert.equal(saved.targets[0].destination_country,'US');assert.match(el('status').textContent,/saved/);
  assert.ok(el('targets').textContent.includes(malicious));assert.equal(el('targets').querySelectorAll('img,script').length,0);assert.equal(w.pwned,undefined);
  assert.equal(el('release-id').value,'https://www.discogs.com/release/123-Synthetic');
  assert.equal(el('evaluate-target').value,'123');assert.equal(el('evaluate').disabled,false);
  el('example').click();const example=JSON.parse(el('observation').value);
  assert.equal(example.source,'synthetic');assert.equal(example.release_id,123);assert.equal(example.pressing_evidence,'unknown');
  assert.equal(example.destination_postal,'90210');assert.ok(Math.abs(Date.now()-Date.parse(example.observed_at))<10000);
  assert.equal(typeof example.item,'string');assert.equal(typeof example.fees,'string');
  evaluation={eligible_for_review:false,reasons:['Shipping is unknown.',malicious],total:null,purchase_allowed:false};
  submit('evaluate-form');await settled(w);
  assert.match(el('result').textContent,/Not eligible for review/);assert.match(el('result').textContent,/Unknown, not zero/);
  assert.equal(el('result').querySelectorAll('li').length,2);assert.ok(el('result').textContent.includes(malicious));
  assert.equal(el('result').querySelectorAll('img,script').length,0);assert.equal(w.pwned,undefined);
  evaluation={eligible_for_review:true,reasons:[],total:'27.00',purchase_allowed:false};
  submit('evaluate-form');await settled(w);
  assert.match(el('result').textContent,/Eligible for synthetic review/);assert.match(el('result').textContent,/27.00 USD/);
  assert.match(el('result').textContent,/No\. Purchasing is disabled/);
  // Never display eligibility for an unseen, stale, or missing rules revision.
  for(const revision of [saved.revision+1,saved.revision-1,undefined,'1']) {
    evaluation.rules_revision=revision;submit('evaluate-form');await settled(w);
    assert.match(el('result').textContent,/Rules version is unverified/);
    assert.doesNotMatch(el('result').textContent,/Eligible for synthetic review|Not eligible for review/);
    assert.match(el('status').textContent,/Refresh saved state, then run/);
    assert.equal(el('evaluate').disabled,true);assert.equal(el('save-target').disabled,true);
    assert.equal(el('observation').value,JSON.stringify(example,null,2));
    el('refresh').click();await settled(w);assert.equal(el('evaluate').disabled,false);
    assert.match(el('result').textContent,/Rules version is unverified/);
  }
  delete evaluation.rules_revision;submit('evaluate-form');await settled(w);
  assert.match(el('result').textContent,/Eligible for synthetic review/);
  evaluation.purchase_allowed=true;submit('evaluate-form');await settled(w);
  assert.match(el('result').textContent,/No usable test result/);assert.match(el('status').textContent,/invalid dry-run/);
  evaluation.purchase_allowed=false;
  const evaluated=posts('/api/evaluate').length;
  for(const bad of ['{bad','[]',JSON.stringify({...example,source:'live'}),' '.repeat(65537)]) {
    el('observation').value=bad;submit('evaluate-form');await settled(w);
    assert.equal(posts('/api/evaluate').length,evaluated);assert.equal(el('observation').value,bad);
  }
  const jsonDraft=JSON.stringify(example,null,2);el('observation').value=jsonDraft;
  el('max-total').value='44.00';el('refresh').click();await settled(w);
  assert.equal(el('max-total').value,'44.00');assert.equal(el('observation').value,jsonDraft);
  let release;delayed={promise:new Promise(resolve=>{release=resolve;})};
  const beforeSave=posts('/api/target').length;submit('target-form');submit('target-form');el('refresh').click();
  assert.equal(posts('/api/target').length,beforeSave+1);assert.equal(el('save-target').disabled,true);
  el('max-total').value='43.00';el('observation').value='pending JSON edit';release();delayed=null;await settled(w);
  assert.equal(saved.targets[0].max_total,'44.00');assert.equal(el('max-total').value,'43.00');assert.equal(el('observation').value,'pending JSON edit');
  mutationFailure=400;submit('target-form');await settled(w);
  assert.match(el('status').textContent,/Invalid target/);assert.equal(el('max-total').value,'43.00');assert.equal(saved.targets[0].max_total,'44.00');
  mutationFailure=409;submit('target-form');await settled(w);
  assert.match(el('status').textContent,/changed in another tab/);assert.equal(el('max-total').value,'43.00');
  assert.match(el('targets').textContent,/80.00/);assert.equal(el('observation').value,'pending JSON edit');
  submit('target-form');await settled(w);assert.equal(saved.targets[0].max_total,'43.00');
  mutationFailure=new TypeError('network down');el('max-total').value='42.00';submit('target-form');await settled(w);
  assert.match(el('status').textContent,/outcome is unverified/);assert.equal(el('save-target').disabled,true);
  const beforeBlocked=posts('/api/target').length;submit('target-form');await settled(w);assert.equal(posts('/api/target').length,beforeBlocked);
  readFailure=new TypeError('still offline');el('refresh').click();await settled(w);
  assert.equal(el('save-target').disabled,true);assert.equal(el('max-total').value,'42.00');
  el('refresh').click();await settled(w);assert.equal(el('save-target').disabled,false);
  failReadAfterSave=true;submit('target-form');await settled(w);
  assert.equal(saved.targets[0].max_total,'42.00');assert.match(el('status').textContent,/save was accepted.*could not be verified/);
  assert.equal(el('save-target').disabled,true);assert.equal(el('observation').value,'pending JSON edit');
  el('refresh').click();await settled(w);assert.equal(el('save-target').disabled,false);
  // One-copy markers are local rules, not a purchase action, and invalidate an older result.
  el('observation').value=jsonDraft;submit('evaluate-form');await settled(w);
  clickText('Mark already owned');await settled(w);assert.deepEqual(saved.owned,[123]);
  assert.match(el('targets').textContent,/Yes · blocks rule eligibility/);assert.match(el('result-stale').textContent,/changed after this test/);
  assert.doesNotMatch(el('result').textContent,/Eligible for synthetic review/);
  assert.equal(el('max-total').value,'42.00');assert.equal(el('observation').value,jsonDraft);
  clickText('Clear already-owned marker');await settled(w);assert.deepEqual(saved.owned,[]);
  // A failed conflict refresh must keep write controls paused until a successful read.
  mutationFailure=409;readFailure=new TypeError('conflict read failed');submit('target-form');await settled(w);
  assert.match(el('status').textContent,/changed.*refreshing failed/);assert.equal(el('save-target').disabled,true);
  assert.ok([...d.querySelectorAll('.ownership')].every(button=>button.disabled));
  el('refresh').click();await settled(w);assert.equal(el('max-total').value,'42.00');
  clickText('Load these rules into the form');assert.equal(el('max-total').value,'80.00');assert.equal(el('release-id').value,'123');
  assert.equal(el('label').value,malicious);
  // Paused state is explicit, round-trips through edits, and blocks synthetic review.
  el('enabled').checked=false;el('max-total').value='79.00';
  delayed={promise:new Promise(resolve=>{release=resolve;})};
  const beforePause=posts('/api/target').length;submit('target-form');submit('target-form');
  assert.equal(posts('/api/target').length,beforePause+1);release();delayed=null;await settled(w);
  assert.equal(saved.targets[0].enabled,false);assert.equal(saved.targets[0].max_total,'79.00');
  assert.match(el('targets').textContent,/Paused · blocks rule eligibility/);
  el('enabled').checked=true;clickText('Load these rules into the form');assert.equal(el('enabled').checked,false);
  submit('evaluate-form');await settled(w);assert.match(el('result').textContent,/Not eligible for review/);
  assert.match(el('result').textContent,/Target is paused/);
  el('enabled').checked=true;mutationFailure=409;submit('target-form');await settled(w);
  assert.equal(el('enabled').checked,true);assert.equal(saved.targets[0].enabled,false);
  submit('target-form');await settled(w);assert.equal(saved.targets[0].enabled,true);
  assert.equal(saved.targets[0].max_total,'79.00');assert.match(el('targets').textContent,/Active · rules only/);
  clickText('Mark already owned');await settled(w);assert.deepEqual(saved.owned,[123]);
  assert.equal(saved.targets[0].enabled,true);assert.equal(el('enabled').checked,true);
  clickText('Clear already-owned marker');await settled(w);assert.deepEqual(saved.owned,[]);
  // A new page reload sees saved state without inventing persisted drafts.
  dom.window.close();dom=open();w=dom.window;d=w.document;await settled(w);
  assert.match(el('targets').textContent,/79.00/);assert.equal(el('release-id').value,'');assert.equal(el('observation').value,'');
  clickText('Load these rules into the form');assert.equal(el('release-id').value,'123');assert.equal(el('max-total').value,'79.00');
  assert.equal(el('enabled').checked,true);
  saved.purchasing_enabled=true;el('refresh').click();await settled(w);
  assert.match(el('status').textContent,/purchasing-disabled/);assert.equal(el('save-target').disabled,true);assert.equal(el('evaluate').disabled,true);
  saved.purchasing_enabled=false;el('refresh').click();await settled(w);assert.equal(el('save-target').disabled,false);
  // A manual known-listing read is isolated from synthetic tests and never starts automatically.
  assert.equal(posts('/api/listing').length,0);
  saved.capabilities.listing_read_enabled=true;el('refresh').click();await settled(w);
  assert.equal(el('read-listing').disabled,false);assert.equal(posts('/api/listing').length,0);
  assert.equal(el('listing-target').value,'123');assert.match(el('listing-capability').textContent,/no automatic fetching/);
  el('observation').value='synthetic draft remains separate';el('max-total').value='78.00';
  el('listing-input').value='https://www.discogs.com/sell/item/456';
  delayed={promise:new Promise(resolve=>{release=resolve;})};
  submit('listing-form');submit('listing-form');el('read-listing').click();el('refresh').click();
  assert.equal(posts('/api/listing').length,1);assert.equal(el('read-listing').disabled,true);
  el('listing-input').value='457';release();delayed=null;await settled(w);
  assert.equal(JSON.parse(posts('/api/listing')[0].options.body).listing,'https://www.discogs.com/sell/item/456');
  assert.equal(el('listing-input').value,'457');assert.equal(el('observation').value,'synthetic draft remains separate');
  assert.equal(el('max-total').value,'78.00');assert.match(el('listing-result').textContent,/Live listing does not pass the rules/);
  assert.match(el('listing-result').textContent,/Unknown, not zero/);assert.match(el('listing-result').textContent,/Shipping blocked/);
  assert.match(el('listing-result').textContent,/Data provided by Discogs/);assert.match(el('listing-result').textContent,/not a final destination quote/);
  assert.equal(el('listing-result').querySelectorAll('li').length,3);
  assert.equal(el('listing-result').querySelector('a').href,'https://www.discogs.com/sell/item/456');
  assert.equal(el('listing-result').querySelector('a').rel,'noopener noreferrer');
  assert.equal(el('listing-result').querySelectorAll('img,script,iframe').length,0);
  const facts=[...el('listing-result').querySelectorAll('dt')];
  assert.equal(facts.find(row=>row.textContent==='Seller feedback count').nextElementSibling.textContent,'0');
  assert.equal(facts.find(row=>row.textContent==='Sleeve grade').nextElementSibling.textContent,'Unknown');
  el('refresh').click();await settled(w);assert.equal(posts('/api/listing').length,1);assert.equal(el('listing-input').value,'457');
  // Provider data and warnings stay inert text; unsafe or mismatched URLs never become links.
  const cleanListing=clone(listingResponse);
  listingResponse.listing.media=malicious;listingResponse.warning=malicious;listingResponse.evaluation.reasons=[malicious];
  listingResponse.listing.url='javascript:window.pwned=true';listingResponse.listing.available=false;
  submit('listing-form');await settled(w);
  assert.ok(el('listing-result').textContent.includes(malicious));assert.equal(el('listing-result').querySelectorAll('img,script,a').length,0);
  assert.equal(w.pwned,undefined);assert.match(el('listing-result').textContent,/Reported unavailable/);
  listingResponse=clone(cleanListing);listingResponse.listing.url='https://www.discogs.com/sell/item/999';
  submit('listing-form');await settled(w);assert.equal(el('listing-result').querySelectorAll('a').length,0);
  listingResponse=clone(cleanListing);
  // Read errors preserve every draft, show no stale success, and do not retry.
  for(const failure of [403,404,502,new TypeError('offline')]) {
    listingFailure=failure;const previous=posts('/api/listing').length;submit('listing-form');await settled(w);
    assert.equal(posts('/api/listing').length,previous+1);assert.match(el('listing-result').textContent,/No usable listing result/);
    assert.match(el('listing-result').textContent,/No automatic retry/);assert.equal(el('listing-input').value,'457');
    assert.equal(el('observation').value,'synthetic draft remains separate');assert.equal(el('read-listing').disabled,false);
  }
  // A read-only response can never grant purchase permission or qualification against unseen rules.
  listingResponse.evaluation.purchase_allowed=true;submit('listing-form');await settled(w);
  assert.match(el('listing-result').textContent,/invalid read-only listing result/);
  listingResponse.evaluation.purchase_allowed=false;listingResponse.evaluation.eligible_for_review=true;
  for(const revision of [saved.revision+1,saved.revision-1,undefined,'1']) {
    listingResponse.evaluation.rules_revision=revision;submit('listing-form');await settled(w);
    assert.match(el('listing-result').textContent,/Rules version is unverified/);
    assert.doesNotMatch(el('listing-result').textContent,/Manual review only|Live listing does not pass/);
    assert.equal(el('read-listing').disabled,true);assert.equal(el('evaluate').disabled,true);
    assert.equal(el('listing-result').querySelectorAll('li').length,3);
    const previous=posts('/api/listing').length;el('refresh').click();await settled(w);
    assert.equal(posts('/api/listing').length,previous);assert.equal(el('read-listing').disabled,false);
  }
  listingResponse=clone(cleanListing);submit('listing-form');await settled(w);
  clickText('Mark already owned');await settled(w);assert.match(el('listing-result-stale').textContent,/changed after this read/);
  assert.match(el('listing-result').textContent,/Previous listing check is out of date/);
  const readsBeforeDisable=posts('/api/listing').length;
  saved.capabilities.listing_read_enabled=false;el('refresh').click();await settled(w);
  submit('listing-form');await settled(w);assert.equal(el('read-listing').disabled,true);assert.equal(posts('/api/listing').length,readsBeforeDisable);
  saved.capabilities={listing_read_enabled:'true'};el('refresh').click();await settled(w);assert.equal(el('read-listing').disabled,true);
  delete saved.capabilities;el('refresh').click();await settled(w);assert.equal(el('read-listing').disabled,true);
  assert.ok(requestTimeouts.includes(25000));assert.ok(requestTimeouts.includes(10000));
  assert.ok(requestTimeouts.every(delay=>delay===10000 || delay===25000));
  assert.deepEqual(errors,[]);dom.window.close();
  console.log('Discogs UI offline DOM checks passed: rules, stale drafts, owned markers, synthetic tests, explicit read-only listing requests, revision guards and XSS.');
})().catch(error=>{console.error(error);if(dom)dom.window.close();process.exitCode=1;});
