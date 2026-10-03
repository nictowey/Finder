/* Offline DOM verification. Installed jsdom only; all network and resource loads denied. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {TextEncoder}=require('node:util');
const {JSDOM,VirtualConsole}=require('jsdom');
const html=fs.readFileSync(process.argv[2],'utf8');
const fixture=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
let saved={schema_version:1,revision:0,target:null,settings:null,source:null,rows:[]};
let delayed=null, failNext=false, conflictNext=false;
const calls=[], errors=[];
require('node:net').Socket.prototype.connect=() => { throw new Error('Network disabled.'); };
require('node:dns').lookup=() => { throw new Error('DNS disabled.'); };
const denyDispatcher={dispatch(){throw new Error('External resource forbidden.');},close(){return Promise.resolve();},destroy(){return Promise.resolve();}};
const response=(status,data) => ({ok:status === 200,status,json:async()=>structuredClone(data)});
async function mockFetch(path,options) {
  assert.ok(['/api/snapshot','/api/import','/api/profile','/api/candidate','/api/verdict'].includes(path));
  assert.equal(options.redirect,'error'); calls.push({path,options});
  if(path === '/api/snapshot') return response(200,saved);
  assert.equal(options.headers['X-Finder-CSRF'],'dom-test-token');
  const input=JSON.parse(options.body);
  if(delayed) await delayed.promise;
  if(failNext){failNext=false;return response(400,{error:'Invalid input.'});}
  if(conflictNext){conflictNext=false;saved.revision++;saved.settings.maximum_subtotal='19.00';return response(409,{code:'stale_state',error:'Saved state changed. Your drafts are retained.'});}
  if(input.expected_revision !== saved.revision) return response(409,{code:'stale_state',error:'Saved state changed.'});
  if(path === '/api/profile') {
    saved={...saved,...structuredClone(input.profile),revision:saved.revision+1};
  } else if(path === '/api/import') {
    const bundle=input.bundle;
    saved={...structuredClone(fixture),...structuredClone(bundle),revision:saved.revision+1};
    delete saved.listings;
    saved.rows.forEach(row=>row.current_token='token-1');
  } else if(path === '/api/candidate') {
    assert.deepEqual(Object.keys(input).sort(),['expected_current','expected_revision','observation']);
    const old=saved.rows.find(row=>row.id === input.observation.id);
    if(old && old.current_token !== input.expected_current) return response(409,{code:'stale_state',error:'Saved case changed.'});
    if(old && input.observation.observed_at === old.listing.source_metadata.manual_input.observed_at &&
        JSON.stringify(input.observation) !== JSON.stringify(old.listing.source_metadata.manual_input)) {
      return response(409,{code:'observation_collision',error:'Different case data already uses this observation time. Supply the actual later observation time.'});
    }
    const row={...structuredClone(fixture.rows[0]),id:input.observation.id,current_token:'token-'+calls.length};
    row.listing.title=input.observation.title;row.listing.source_metadata.manual_input=structuredClone(input.observation);
    row.listing.source_metadata.local_source=saved.source;
    saved.rows=saved.rows.filter(r=>r.id !== row.id).concat(row);
  } else {
    const row=saved.rows.find(row=>row.id === input.id); row.verdict=input.verdict;
  }
  return response(200,saved);
}
function open() {
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e));
  return new JSDOM(html,{url:'http://127.0.0.1:18765/',runScripts:'dangerously',resources:{dispatcher:denyDispatcher},virtualConsole,
    beforeParse(w){w.fetch=mockFetch;w.TextEncoder=TextEncoder;}});
}
async function settled(w){for(let i=0;i<100;i++){await new Promise(r=>setImmediate(r));if(w.document.getElementById('cases').getAttribute('aria-busy') === 'false') return;}throw new Error('UI did not settle');}
let dom,w,d;
const el=id=>d.getElementById(id);
function fill(fields){Object.entries(fields).forEach(([id,value])=>{el(id).value=value;});}
function submit(id){el(id).dispatchEvent(new w.Event('submit',{cancelable:true}));}
function clickText(text){const button=[...d.querySelectorAll('button')].find(b=>b.textContent === text);assert.ok(button,text);button.click();}
(async()=>{
  dom=open();w=dom.window;d=w.document;await settled(w);
  assert.match(el('summary').textContent,/workspace is empty/);
  assert.equal(el('detailed').checked,false);
  fill({'target-artist':'Manual Artist','target-album':'Manual Album','target-colors':'Blue','settings-maximum_subtotal':'30.00'});
  submit('profile-form');await settled(w);
  assert.equal(saved.schema_version,1);assert.equal(saved.rows.length,0);assert.deepEqual(saved.target.formats,[]);assert.deepEqual(saved.settings.tells,[]);
  assert.equal(saved.settings.maximum_subtotal,'30.00');
  // A later cap edit is saved, rather than silently ignored.
  el('settings-maximum_subtotal').value='25.00';submit('profile-form');await settled(w);
  assert.equal(saved.settings.maximum_subtotal,'25.00');
  // Refresh preserves dirty profile input. Pending-save edits are retained too.
  el('target-album').value='Draft Album';el('refresh').click();await settled(w);assert.equal(el('target-album').value,'Draft Album');
  el('reload-profile').click();assert.equal(el('target-album').value,'Manual Album');
  let release;delayed={promise:new Promise(r=>{release=r;})};
  el('settings-maximum_subtotal').value='24.00';submit('profile-form');submit('profile-form');el('refresh').click();
  const count=calls.filter(c=>c.path === '/api/profile').length;
  el('settings-maximum_subtotal').value='23.00';release();delayed=null;await settled(w);
  assert.equal(calls.filter(c=>c.path === '/api/profile').length,count);assert.equal(el('settings-maximum_subtotal').value,'23.00');
  submit('profile-form');await settled(w);assert.equal(saved.settings.maximum_subtotal,'23.00');
  // A pending-save edit back to the previous value is still a newer unsaved edit.
  delayed={promise:new Promise(r=>{release=r;})};
  el('settings-maximum_subtotal').value='21.00';submit('profile-form');
  el('settings-maximum_subtotal').value='23.00';release();delayed=null;await settled(w);
  assert.equal(saved.settings.maximum_subtotal,'21.00');assert.equal(el('settings-maximum_subtotal').value,'23.00');
  el('refresh').click();await settled(w);assert.equal(el('settings-maximum_subtotal').value,'23.00');
  submit('profile-form');await settled(w);assert.equal(saved.settings.maximum_subtotal,'23.00');
  failNext=true;el('settings-maximum_subtotal').value='22.00';submit('profile-form');await settled(w);
  assert.match(el('status').textContent,/Invalid input/);assert.equal(el('settings-maximum_subtotal').value,'22.00');
  conflictNext=true;submit('profile-form');await settled(w);
  assert.equal(saved.settings.maximum_subtotal,'19.00');assert.equal(el('settings-maximum_subtotal').value,'22.00');
  const before=calls.length;submit('profile-form');await settled(w);assert.equal(calls.length,before);
  el('reload-profile').click();assert.equal(el('settings-maximum_subtotal').value,'19.00');
  // Explicit v2 toggle, full profile and explicit sign roles; no implied required color.
  el('detailed').checked=true;el('detailed').dispatchEvent(new w.Event('change'));
  fill({'target-catalog_numbers':'CAT-1\nCAT-2','target-barcodes':'012345','target-formats':'LP\nStereo','target-country':'US',
    'target-release_year':'2024','target-editions':'Limited Edition','target-cover_edition':'Alpha','target-required_components':'signed_insert',
    'settings-condition_ids':'1000\n3000','settings-alert_mode':'strict','settings-auction_alert_minutes':'60'});
  el('add-clue').click();let signs=d.querySelector('.sign-row');signs.querySelector('input').value='Blue';signs.querySelectorAll('select')[0].value='required';signs.querySelectorAll('select')[1].value='color';
  submit('profile-form');await settled(w);
  assert.equal(saved.schema_version,2);assert.deepEqual(saved.target.catalog_numbers,['CAT-1','CAT-2']);assert.equal(saved.settings.tells[0].required,true);
  el('new-comparison').click();fill({'comparison-id':'black','comparison-colors':'Black','comparison-formats':'LP'});submit('comparison-form');await settled(w);
  assert.equal(saved.alternatives.length,1);assert.equal(saved.settings.maximum_subtotal,'19.00');
  clickText('Edit black');el('comparison-cover_edition').value='Beta';submit('comparison-form');await settled(w);assert.equal(saved.alternatives[0].cover_edition,'Beta');
  clickText('Edit black');el('remove-comparison').click();await settled(w);assert.equal(saved.alternatives.length,0);
  // Unknowns are explicit, timestamps never filled or advanced, currencies are independent.
  assert.equal(el('candidate-observed_at').value,'');assert.equal(el('candidate-price_kind').value,'unknown');
  fill({'candidate-id':'manual-1','candidate-title':'Manual Artist Manual Album blue vinyl','candidate-observed_at':'2026-01-02T03:04:05Z',
    'candidate-current_price':'10.00','candidate-currency':'EUR','candidate-shipping_cost':'2.00','candidate-shipping_currency':'USD'});
  submit('case-form');submit('case-form');await settled(w);
  let manual=saved.rows[0].listing.source_metadata.manual_input;
  assert.equal(manual.details_observed_at,null);assert.equal(manual.price_kind,'unknown');assert.deepEqual(manual.formats,[]);
  assert.equal(manual.currency,'EUR');assert.equal(manual.shipping_currency,'USD');
  assert.equal(saved.settings.maximum_subtotal,'19.00');
  clickText('Record another observation');assert.equal(el('candidate-observed_at').value,manual.observed_at);
  el('candidate-title').value='Corrected draft title';submit('case-form');await settled(w);
  assert.match(el('status').textContent,/actual later observation time/);assert.equal(saved.rows[0].listing.title,manual.title);
  assert.equal(el('candidate-title').value,'Corrected draft title');
  el('candidate-observed_at').value='2026-01-02T04:04:05Z';submit('case-form');await settled(w);
  assert.equal(saved.rows.length,1);assert.equal(saved.rows[0].listing.title,'Corrected draft title');
  assert.equal(JSON.parse(calls.at(-1).options.body).observation.observed_at,'2026-01-02T04:04:05Z');
  // Reload hydrates all rich saved fields and sign flags without defaulting facts.
  dom.window.close();dom=open();w=dom.window;d=w.document;await settled(w);
  assert.equal(el('settings-maximum_subtotal').value,'19.00');assert.equal(el('target-cover_edition').value,'Alpha');
  assert.equal(el('target-formats').value,'LP\nStereo');assert.equal(el('settings-condition_ids').value,'1000\n3000');
  assert.equal(el('candidate-observed_at').value,'');assert.equal(el('detailed').checked,true);
  // Imported rich settings, including anti-sign required flag, survive a narrow cap edit.
  saved.settings.anti_tells=[{kind:'keyword',value:'reissue',required:true}];el('refresh').click();await settled(w);
  el('settings-maximum_subtotal').value='25.00';submit('profile-form');await settled(w);
  assert.deepEqual(saved.settings.anti_tells,[{kind:'keyword',value:'reissue',required:true}]);assert.equal(saved.target.required_components[0],'signed_insert');
  // Duplicate nested JSON keys reach strict server validation intact.
  const duplicate='{"schema_version":1,"source":"manual","source":"synthetic","listings":[]}';
  el('draft').value=duplicate;failNext=true;el('save').click();await settled(w);
  assert.ok(calls.at(-1).options.body.includes(duplicate));assert.equal(el('draft').value,duplicate);
  // File read races retain newer edits.
  let finishRead;const file={size:100,text:()=>new Promise(r=>{finishRead=r;})};
  Object.defineProperty(el('file'),'files',{value:[file]});el('file').dispatchEvent(new w.Event('change'));
  el('draft').value='keep my newer edit';finishRead('{"schema_version":1,"listings":[]}');await settled(w);
  assert.equal(el('draft').value,'keep my newer edit');assert.match(el('status').textContent,/draft changed/);
  dom.window.close();
  saved={...structuredClone(fixture),schema_version:1,revision:20,source:'manual',target:{artist:'Imported Artist',album:'Imported Album',
    colors:['Red','Gold'],catalog_numbers:['IMP-1'],barcodes:['0123'],formats:['EP','Stereo']},
    settings:{maximum_subtotal:'30.00',gamble_max:'12.00',currency:'GBP',country:'GB',postal_code:'AA1 1AA',
      condition_ids:['1000','3000'],alert_mode:'strict',auction_alert_minutes:75,
      tells:[{kind:'catalog_number',value:'IMP-1',required:false}],anti_tells:[{kind:'keyword',value:'reissue',required:false}]}};
  dom=open();w=dom.window;d=w.document;await settled(w);
  assert.equal(el('detailed').checked,false);assert.equal(el('target-formats').value,'EP\nStereo');
  assert.equal(d.querySelectorAll('.case img').length,0);assert.ok(d.querySelector('.case').textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(el('target-catalog_numbers').value,'IMP-1');assert.equal(el('settings-currency').value,'GBP');
  el('settings-maximum_subtotal').value='25.00';submit('profile-form');await settled(w);
  assert.equal(saved.schema_version,1);assert.equal('country' in saved.target,false);assert.equal('alternatives' in saved,false);
  assert.deepEqual(saved.target.formats,['EP','Stereo']);assert.equal(saved.settings.auction_alert_minutes,75);
  assert.deepEqual(saved.settings.tells,[{kind:'catalog_number',value:'IMP-1',required:false}]);
  assert.equal(errors.length,0,errors.map(String).join('\n'));dom.window.close();
  console.log('Self-serve local DOM: profile/caps, explicit v1/v2, comparison CRUD, rich hydration, unknowns, currencies, collision correction, dirty/stale/pending drafts and duplicate/file races passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
