/** Real isolated browser, synthetic source, unchanged collector and Python pipeline. */
import assert from 'node:assert/strict';
import {mkdirSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {performance} from 'node:perf_hooks';
import {firefox} from 'playwright';
import {getResourceProfile,collectResourceProfile} from './resource_profile.mjs';
import {captureProcessSecurityBaseline,collectStableProcessSecurity} from './process_security.mjs';
import {collectReleaseMarketplaceDom} from '../../scripts/discogs_release_snapshot.mjs';

const started=performance.now();
const profileName=process.env.FINDER_BROWSER_PROFILE??'standard';
const profile=getResourceProfile(profileName);
collectResourceProfile({profile:profileName});
assert.equal(process.getuid(),10001,'Smoke must run as the isolated non-root user');
for(const name of ['MOZ_DISABLE_CONTENT_SANDBOX','MOZ_DISABLE_GMP_SANDBOX','MOZ_DISABLE_RDD_SANDBOX']) {
  assert.equal(process.env[name],undefined,'Sandbox-disabling environment is forbidden');
}
mkdirSync('/tmp/home',{recursive:true});
const baseline=captureProcessSecurityBaseline({rootPid:process.pid,expectedUid:10001});
const browser=await firefox.launch({headless:true,timeout:profile.timeouts.launch_ms});
try {
  const context=await browser.newContext({serviceWorkers:'block',acceptDownloads:false});
  let markup='', fulfilled=0, blocked=0;
  const url='https://www.discogs.com/sell/release/111';
  await context.route('**/*',async route=>{
    if(route.request().url()===url && route.request().isNavigationRequest() && route.request().method()==='GET') {
      fulfilled++;await route.fulfill({status:200,contentType:'text/html',body:markup});
    } else {blocked++;await route.abort('blockedbyclient');}
  });
  const page=await context.newPage();
  let processSecurity;
  let securityError;
  const securityFailures=[];
  const pagination='<nav class="pagination"><span class="pagination_total">1 – 1 of 1</span><form action="/sell/release/111"><select name="limit"><option selected value="25">25</option></select></form></nav>';
  const html=(id,description,partial=false)=>`<!doctype html><html><head><meta charset="utf-8"><title>Synthetic smoke fixture</title></head><body>${pagination}<div id="pjax_container"><table class="mpitems"><thead><tr><th>Item</th></tr></thead><tbody>${partial?'':`<tr><td class="item_description"><a class="item_description_title" href="/sell/item/${id}">Synthetic album</a><a class="item_release_link" href="/release/111-Synthetic">Release</a><p class="item_condition"><span>Very Good Plus (VG+) <span class="has-tooltip">Help</span></span><span class="item_sleeve_condition">Very Good Plus (VG+)</span></p><p class="hide_mobile">${description}</p></td><td class="seller_info"><a href="/seller/synthetic/profile">Synthetic seller</a><ul><li><strong>100.0%</strong><a class="section_link">100 ratings</a></li></ul></td><td class="item_price"><span class="price" data-currency="USD">$20.00</span><span class="item_shipping">+$5.00 shipping</span></td></tr>`}</tbody></table></div>${pagination}</body></html>`;
  const snapshots=[];
  for(const [id,description,partial] of [['222','Clean copy, no scratches or skips.',false],['333','NO DISC 1, disc 2 only.',false],['333','',true],['333','NO DISC 1, disc 2 only.',false]]) {
    markup=html(id,description,partial);
    await page.goto(url,{waitUntil:'load',timeout:profile.timeouts.navigation_ms});
    const snapshot=await page.evaluate(collectReleaseMarketplaceDom,'111');
    if(partial) assert.equal(snapshot.complete,false); else assert.equal(snapshot.complete,true, snapshot.error_code);
    // Synthetic functional evidence remains independently observable; the overall
    // smoke still fails if mandatory process evidence is absent or fails.
    try {
      processSecurity=await collectStableProcessSecurity({rootPid:process.pid,executablePath:firefox.executablePath(),expectedUid:10001,baseline});
    } catch(error) {
      securityError ??= error;
      securityFailures.push({code:error.code??'unknown',message:error.message});
    }
    snapshots.push(snapshot);
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  assert.equal(fulfilled,4);
  // Explicitly prove unmatched requests are refused, never continued to the network.
  const denied=await page.evaluate(async()=>{try {await fetch('https://forbidden.invalid/probe');return false;}catch{return true;}});
  assert.equal(denied,true);assert.ok(blocked>=1);
  const check=spawnSync('/opt/venv/bin/python',['tests/cloud_browser/verify_pipeline.py'],{
    input:JSON.stringify(snapshots),encoding:'utf8',timeout:profile.timeouts.bridge_ms,maxBuffer:65536,
    env:{...process.env,PYTHONPATH:'src'},
  });
  assert.equal(check.status,0,check.stderr||check.error?.message||check.stdout);
  const pipeline=JSON.parse(check.stdout);
  const resources=collectResourceProfile({profile:profileName});
  console.log(JSON.stringify({status:securityError?'functional_pass_security_gate_failed':'passed',browser:'firefox',browser_version:browser.version(),playwright:'1.64.1',
    process_security:processSecurity??null,security_check_passed:!securityError,security_failures:securityFailures,
    security_error:securityError?{code:securityError.code??'unknown',message:securityError.message,diagnostics:securityError.diagnostics??null}:null,uid:process.getuid(),synthetic_navigations:fulfilled,blocked_requests:blocked,
    elapsed_ms:Math.round(performance.now()-started),node_max_rss_kib:process.resourceUsage().maxRSS,
    container_memory_peak_bytes:resources.memory_peak_bytes,resource_profile:profileName,resources,pipeline,purchase_allowed:false,production_source_enabled:false}));
  await context.close();
  if(securityError) throw securityError;
} finally {await browser.close();}
