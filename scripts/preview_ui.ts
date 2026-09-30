/**
 * Build a self-contained, offline design preview from the bundled dashboard.
 * All data and artwork are synthetic. Its fetch adapter stores changes in memory
 * for this tab only, with no external calls, auth credentials or production writes.
 * Usage: node --import tsx scripts/preview_ui.ts [output.html]
 */
import {mkdirSync,writeFileSync} from 'node:fs';
import {dirname} from 'node:path';
import {html,javascript,stylesheet} from '../functions/watchlist-ui.js';
import {dashboardFixture,fixtureDashboard} from '../functions/watchlist-ui-fixture.js';
const data=dashboardFixture();
const artwork=(palette:string[],title:string,artist:string,reverse=false)=>'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="720" height="640" viewBox="0 0 720 640"><rect width="720" height="640" fill="${palette[0]}"/><g transform="translate(55 46)"><rect x="10" y="12" width="500" height="532" rx="3" fill="#152c2620"/><rect width="500" height="532" rx="2" fill="${palette[1]}"/><path d="M0 310 500 120V420L0 525Z" fill="${palette[2]}" opacity=".55"/><text x="34" y="62" fill="${palette[3]}" font-family="Arial,sans-serif" font-size="25" letter-spacing="4">${artist}</text><text x="34" y="101" fill="${palette[3]}" font-family="Georgia,serif" font-size="41">${title}</text><text x="34" y="495" fill="${palette[3]}" font-family="Arial,sans-serif" font-size="10" letter-spacing="4">SYNTHETIC PREVIEW · 33 ⅓ RPM</text><circle cx="${reverse?250:465}" cy="${reverse?295:348}" r="178" fill="${reverse?palette[2]:'#142621'}"/><g fill="none" stroke="${reverse?'#ffffff30':'#60706640'}" stroke-width="2">${[64,90,116,140,160].map(r=>`<circle cx="${reverse?250:465}" cy="${reverse?295:348}" r="${r}"/>`).join('')}</g><circle cx="${reverse?250:465}" cy="${reverse?295:348}" r="51" fill="${palette[2]}"/><circle cx="${reverse?250:465}" cy="${reverse?295:348}" r="7" fill="${palette[0]}"/></g></svg>`);
const images:Record<string,string>={};
for(const [i,row] of data.leads.entries())for(const [j,url] of row.listing.images.entries())images[url]=artwork(i===0?['#e5e8dc','#9aaf86','#5a7051','#173b31']:['#e7ddd1','#a7654c','#dda365','#fff3dc'],i===0?'After Hours':'Still Life',i===0?'MILO WEST':'THE PINES',j===1);
const adapter=String.raw`
const __name=fn=>fn; // tsx name annotations in the serialized synthetic helper
const fixtureDashboard=FIXTURE_DASHBOARD;
const previewData=PREVIEW_DATA,previewImages=PREVIEW_IMAGES;
const previewScenario=new URLSearchParams(location.search).get('scenario');
if(previewScenario==='empty')previewData.leads=[];
if(previewScenario==='long'){const r=previewData.leads[0];r.listing.title='Milo West — After Hours: Limited Collector Edition, Moss Green Double Vinyl with Alternate Cover, Numbered Sleeve and Bonus Insert';r.data.subtotal='9999999.99';previewData.watches[0].config.maximum_subtotal='9999999.99';r.data.signs.push('SYNTHETICCATALOG'.repeat(5));r.listing.images=Array.from({length:12},(_,i)=>r.listing.images[i%2]);}
if(previewScenario==='categories'){const source=previewData.leads[0];previewData.leads=Array.from({length:125},(_,i)=>({...structuredClone(source),marketplace_item_id:'synthetic-'+String(i).padStart(3,'0'),verdict:i%4===0?'mine':i%4===1?'other':i%4===2?'unsure':null,purchased:i%3===0||i%4===3,dismissed:i%10===0,verdict_tier:i%2?'family_review':'possible_pressing'}));}
if(previewScenario==='saved'){const r=previewData.leads[0];r.verdict='mine';r.purchased=true;r.verdict_tier='family_review';r.verdict_decided_at=r.last_seen_at;r.verdict_provenance='recorded_prediction';}
window.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;await new Promise(r=>setTimeout(r,350));
 if(path.startsWith('/api/dashboard'))return {ok:true,json:async()=>fixtureDashboard(previewData,path)};
 if(path==='/api/verdict'||path==='/api/purchase'){if(previewScenario==='error')return {ok:false,status:503,json:async()=>({error:'Synthetic save failure'})};if(previewScenario==='pending')await new Promise(r=>setTimeout(r,60000));const r=previewData.leads.find(x=>x.marketplace_item_id===body.marketplace_item_id);if(path==='/api/verdict'){r.verdict=body.verdict;r.verdict_tier ||= r.data.status;r.verdict_decided_at ||= new Date().toISOString();r.verdict_provenance ||= 'recorded_prediction';}else r.purchased=body.purchased;return {ok:true,json:async()=>({ok:true,verdict:r.verdict,purchased:r.purchased,verdict_tier:r.verdict_tier,verdict_decided_at:r.verdict_decided_at,verdict_provenance:r.verdict_provenance})};}
 if(path==='/api/dismiss'){const r=previewData.leads.find(x=>x.marketplace_item_id===body.marketplace_item_id);r.dismissed=body.dismissed;return {ok:true,json:async()=>({ok:true})};}
 return {ok:false,status:400,json:async()=>({error:'This is an offline design preview. This action is available in the real app.'})};
};
` .replace('FIXTURE_DASHBOARD',fixtureDashboard.toString()).replace('PREVIEW_DATA',JSON.stringify(data)).replace('PREVIEW_IMAGES',JSON.stringify(images));
// Image transport alone is adapted for offline synthetic artwork. The real UI
// keeps its eBay-host allowlist and never stores listing photos.
const previewScript=javascript.replace("(r.listing.images||[]).filter(u=>/^https:\\/\\/i\\.ebayimg\\.com\\//.test(u))", "(r.listing.images||[]).filter(u=>/^https:\\/\\/i\\.ebayimg\\.com\\//.test(u)).map(u=>previewImages[u])");
if(previewScript===javascript)throw new Error('Preview image adapter did not match');
const safe=(s:string)=>s.replace(/<\/script/gi,'<\\/script');
const banner='<div class="preview-banner">DESIGN PREVIEW · SYNTHETIC LISTINGS &amp; ARTWORK · CHANGES ARE NOT SAVED</div>';
let out=html.replace('<link rel="stylesheet" href="/app.css">','<style>'+stylesheet+'.preview-banner{background:#244f40;color:#fff;padding:10px 18px;text-align:center;font:600 9px/1.6 ui-sans-serif,sans-serif;letter-spacing:1.2px}</style>')
 .replace('<script src="/app.js" defer></script>','').replace('<link rel="manifest" href="/manifest.webmanifest">','')
 .replace('<body>','<body>'+banner).replace('class="brand" href="/"','class="brand" href="#review"').replace('</body>','<script>'+safe(adapter)+'\n'+safe(previewScript)+'</script></body>');
// Synthetic external listing links must never lead to a real merchant item.
out=out.replace('</body>','<script>document.addEventListener("click",e=>{const a=e.target.closest("a[target=\\"_blank\\"]");if(a){e.preventDefault();notice("Preview only. The live app opens the source listing or catalog here.");}});</script></body>');
const target=process.argv[2]||'dist/finder-design-preview.html';mkdirSync(dirname(target),{recursive:true});writeFileSync(target,out);console.log(target);
