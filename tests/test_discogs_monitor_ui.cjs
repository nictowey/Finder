/* Synthetic cloud UI checks. No real sockets, Discogs, owner sessions or browser storage. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {TextEncoder}=require('node:util');
const {JSDOM,VirtualConsole}=require('jsdom');
const html=fs.readFileSync(process.argv[2],'utf8'), csrf=process.argv[3];
require('node:net').Socket.prototype.connect=()=>{throw new Error('Network disabled.');};
require('node:dns').lookup=()=>{throw new Error('DNS disabled.');};
const noResources={dispatch(){throw new Error('External resource forbidden.');},close(){return Promise.resolve();},destroy(){return Promise.resolve();}};
const target={release_id:123,label:'Synthetic release',enabled:true,min_media:'VG+',min_sleeve:'VG',
  min_seller_percent:99,min_feedback:100,max_total:'50.00',currency:'USD',destination_country:'US',destination_postal:'00000'};
let state={revision:1,targets:[target],owned:[],mode:'dry_run',purchasing_enabled:false,kill_switch:true,control_revision:0,
  capabilities:{listing_read_enabled:false,automatic_listing_discovery:false,checkout_available:false},
  monitor:{configured:true,worker_running:true,revision:0,automatic_listing_discovery:false,checkout_available:false,email_monitoring:false,
    discovery_blocker:'Release counts do not discover individual listing IDs.',rows:[],events:[]}};
const calls=[],errors=[];
let failTrack=false;
const releaseRow={release_id:123,kind:'release',reference:'123',last_attempt:100,last_success:100,next_check:700,status:'ok',fresh:true,
  data:{num_for_sale:4,lowest_item_price:'20.00',currency:'USD'}};
async function fetchMock(path,options){
  assert.equal(options.credentials,'same-origin');assert.equal(options.mode,'same-origin');assert.equal(options.redirect,'error');
  calls.push(path);
  if(path==='/api/snapshot')return {ok:true,status:200,json:async()=>structuredClone(state)};
  assert.equal(options.headers['X-Finder-CSRF'],csrf);assert.equal(options.method,'POST');
  const data=JSON.parse(options.body);
  if(path==='/api/kill'){
    assert.equal(data.expected_revision,state.control_revision);state.control_revision++;state.kill_switch=data.killed;
    state.capabilities.listing_read_enabled=!state.kill_switch;
  }else if(path==='/api/monitor/run'){
    assert.deepEqual(data,{});assert.equal(state.kill_switch,false);
    state.monitor.rows=[structuredClone(releaseRow)];
    state.monitor.events=[{sequence:1,at:100,release_id:123,kind:'release',reference:'123'}];
  }else if(path==='/api/monitor/track'){
    if(failTrack){failTrack=false;return {ok:false,status:503,json:async()=>({error:'Storage unavailable.'})};}
    assert.equal(data.expected_revision,state.revision);assert.equal(data.monitor_revision,state.monitor.revision);
    assert.equal(data.release_id,123);assert.equal(typeof data.listing,'string');state.monitor.revision++;
    if(data.remove)state.monitor.rows=state.monitor.rows.filter(row=>row.kind!=='listing');
    else state.monitor.rows.push({release_id:123,kind:'listing',reference:'456',last_attempt:100,last_success:100,next_check:400,status:'ok',fresh:true,
      data:{release_id:123,media:'VG+',sleeve:'VG',seller_percent:99.9,feedback:200,item:'20.00',currency:'USD',reported_shipping:'2.50'},
      review:{status:'needs_total',reasons:['Destination shipping, tax and fees are unknown.'],total:null,qualifies_all_in:false,purchase_allowed:false}});
  }else throw new Error(`Unexpected endpoint ${path}`);
  return {ok:true,status:200,json:async()=>({status:'ok'})};
}
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e));
const dom=new JSDOM(html,{url:'https://finder.example/',runScripts:'dangerously',resources:{dispatcher:noResources},virtualConsole:vc,
  beforeParse(w){w.fetch=fetchMock;w.TextEncoder=TextEncoder;w.Storage.prototype.setItem=()=>{throw new Error('No browser storage.');};}});
const w=dom.window,d=w.document,el=id=>d.getElementById(id);
async function settle(){for(let i=0;i<100;i++){await new Promise(r=>setImmediate(r));if(el('targets').getAttribute('aria-busy')==='false')return;}throw new Error('UI did not settle.');}
(async()=>{
  await settle();
  assert.equal(el('monitor-panel').hidden,false);assert.match(el('monitor-status').textContent,/stopped/);
  assert.equal(el('monitor-once').disabled,true);assert.equal(el('track-target').value,'123');
  assert.match(d.body.textContent,/Purchasing disabled\. Read-only monitoring\./);
  assert.doesNotMatch(d.body.textContent,/No background polling\./);
  el('toggle-monitor').click();await settle();assert.equal(state.kill_switch,false);assert.equal(el('monitor-once').disabled,false);
  assert.match(el('monitor-status').textContent,/Individual listing discovery is not connected/);
  el('monitor-once').click();await settle();
  assert.match(el('monitor-rows').textContent,/availability only/);assert.match(el('monitor-rows').textContent,/Lowest item price only/);
  assert.match(el('monitor-events').textContent,/listing identities unknown/);
  el('track-listing').value='https://www.discogs.com/sell/item/456';
  el('track-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
  assert.match(el('monitor-rows').textContent,/Needs delivered total · does not qualify yet/);
  assert.match(el('monitor-rows').textContent,/Unknown · shipping, tax and fees unverified/);
  assert.equal(el('monitor-rows').querySelector('a').href,'https://www.discogs.com/sell/release/123');
  const remove=[...d.querySelectorAll('button')].find(b=>b.textContent==='Stop tracking this listing');remove.click();await settle();
  assert.equal(state.monitor.rows.length,1);
  el('toggle-monitor').click();await settle();assert.equal(state.kill_switch,true);
  failTrack=true;el('track-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
  assert.equal(el('track-listing-button').disabled,true);assert.match(el('status').textContent,/Refresh saved state before retrying/);
  assert.equal(el('track-listing').value,'https://www.discogs.com/sell/item/456');
  el('refresh').click();await settle();assert.equal(el('track-listing-button').disabled,false);
  state.monitor.configured=false;el('refresh').click();await settle();
  assert.equal(el('toggle-monitor').disabled,true);assert.match(el('monitor-status').textContent,/not configured/);
  assert.ok(calls.every(path=>!path.includes('checkout')&&!path.includes('email')));
  assert.deepEqual(errors,[]);dom.window.close();console.log('Cloud monitor DOM checks passed.');
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
