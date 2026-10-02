// Synthetic query fixtures shared by local PostgreSQL-engine tests and the real
// PostgreSQL CI gate. All provider references and source text are invented.
import assert from 'node:assert/strict';
import { createHandler } from './watchlist.js';

type Query = (sql:string, args?:any[])=>Promise<{rows:any[]}>;
const origin='https://finder.example';

export function pressingOrderFixture(query:Query) {
 const now=Date.now(), fresh=new Date(now-300_000).toISOString();
 const watches=['watch-a','watch-b'].map(id=>({id,revision:1,config:{maximum_subtotal:'20',currency:'USD',country:'US',postal_code:'00000'}}));
 const listings:any[]=[], inbox:any[]=[], decisions:any[]=[], work:any[]=[];
 const add=(id:string,{support=false,watch='watch-a',marketplace='ebay',verdict=null,purchased=false,...overrides}:any={})=>{
  const listing={marketplace,marketplace_item_id:id,data:{title:'Synthetic album listing',details_observed_at:fresh,current_price:'10',shipping_cost:'5',currency:'USD',shipping_currency:'USD',price_kind:'fixed_price',quality_flags:[],source_metadata:{delivery_country:'US',delivery_postal_code:'00000'},item_specifics:{}}};
  const row={watch_id:watch,marketplace,marketplace_item_id:id,first_seen_at:support?'2026-01-01T00:00:00Z':'2026-01-02T00:00:00Z',last_seen_at:fresh,dismissed:false,data:{status:'family_review',watch_revision:1,details_observed_at:fresh,clues:support?['artist_and_album','seller_identifier_claim']:['artist_and_album'],verify:[],signs:[]}};
  Object.assign(listing.data,overrides.listing);Object.assign(row.data,overrides.review);
  if(overrides.dismissed)row.dismissed=true;
  if(!listings.some(r=>r.marketplace===marketplace&&r.marketplace_item_id===id))listings.push(listing);
  inbox.push(row);
  if(verdict||purchased)decisions.push({watch_id:watch,marketplace,marketplace_item_id:id,verdict,purchased});
  return {listing:listing.data as any,review:row.data as any,row};
 };
 const prefix=(offset:number)=>`WITH finder_watches AS (SELECT * FROM jsonb_to_recordset($${offset+1}::jsonb) AS x(id text,config jsonb,revision int,status text,last_started_at text,last_success_at text,next_scan_at text,lease_until text,summary jsonb,catalog jsonb,catalog_observed_at text)),
 listings AS (SELECT * FROM jsonb_to_recordset($${offset+2}::jsonb) AS x(marketplace text,marketplace_item_id text,data jsonb)),
 finder_inbox AS (SELECT * FROM jsonb_to_recordset($${offset+3}::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,data jsonb,first_seen_at text,last_seen_at text,dismissed boolean)),
 finder_decisions AS (SELECT * FROM jsonb_to_recordset($${offset+4}::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,verdict text,purchased boolean,tier text,decided_at text,prediction jsonb)),
 finder_discovery_work AS (SELECT * FROM jsonb_to_recordset($${offset+5}::jsonb) AS x(marketplace text,listing_id text,item_id text,status text,kind text,reason text,last_search_at text,fingerprint text)) `;
 const db={query:async(sql:string,args:any[]=[])=>{
  if(sql.includes('owner_email'))return {rows:[{data:{email:'owner@example.com'}}]};
  if(sql.includes('FROM finder_inbox')||sql.includes('FROM finder_decisions')||sql.includes('FROM finder_watches ORDER BY')){
   const text=sql.startsWith('WITH ')?prefix(args.length)+','+sql.slice(5):prefix(args.length)+sql;
   return query(text,[...args,...[watches,listings,inbox,decisions,work].map(value=>JSON.stringify(value))]);
  }
  return {rows:[]};
 }};
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:'2099-01-01T00:00:00Z'}}))});
 const request=async(params='')=>handler(new Request(origin+'/api/dashboard?'+params));
 const get=async(params='')=>{const response=await request(params);assert.equal(response.status,200,await response.clone().text());return response.json();};
 return {add,get,request,now,fresh,watches,listings,inbox,decisions,work};
}

const key=(row:any)=>JSON.stringify([row.watch_id,row.marketplace,row.marketplace_item_id]);
const clues='filter=review&order=pressing_clues';
export async function verifyPressingOrder(query:Query) {
 const f=pressingOrderFixture(query);
 for(let i=0;i<70;i++)f.add('generic-'+String(i).padStart(3,'0'));
 for(let i=0;i<60;i++)f.add('support-'+String(i).padStart(3,'0'),{support:true});
 // Equal timestamps and IDs must retain both watches and marketplaces.
 f.add('support-000',{support:true,watch:'watch-b'});
 f.add('support-000',{support:true,marketplace:'synthetic-market'});
 f.add('filtered-high',{support:true,listing:{current_price:'100'}});
 f.add('saved-mine',{support:true,verdict:'mine'});
 f.add('saved-other',{verdict:'other'});
 f.add('saved-purchase',{purchased:true});
 const newest=await f.get();
 assert.ok(newest.leads.every((r:any)=>r.marketplace_item_id.startsWith('generic')));
 assert.equal(JSON.parse(newest.next_cursor).length,3);
 const first=await f.get(clues);
 assert.equal(first.leads.length,50);assert.ok(first.leads.every((r:any)=>r.marketplace_item_id.startsWith('support')));
 assert.ok(first.leads.every((r:any)=>r.pressing_clues.length===1));
 const second=await f.get(clues+'&cursor='+encodeURIComponent(first.next_cursor));
 const third=await f.get(clues+'&cursor='+encodeURIComponent(second.next_cursor));
 const rows=[...first.leads,...second.leads,...third.leads];
 assert.equal(third.next_cursor,null);assert.equal(rows.length,132);assert.equal(new Set(rows.map(key)).size,132);
 assert.equal(rows.findIndex((r:any)=>!r.pressing_clues.length),62);
 assert.ok(rows.every((r:any)=>r.data.status==='family_review'));
 assert.ok(!JSON.stringify(rows).includes('order_generation'));
 const legacy=await f.get('cursor='+encodeURIComponent(newest.next_cursor));assert.equal(legacy.leads.length,50);
 const prices=await f.get(clues+'&prices=all');
 const morePrices=await f.get(clues+'&prices=all&cursor='+encodeURIComponent(prices.next_cursor));
 assert.ok(morePrices.leads.some((r:any)=>r.marketplace_item_id==='filtered-high'));
 const judged=await f.get('filter=judged&order=pressing_clues&judgment=mine');
 assert.equal(judged.filtered_total,1);assert.equal(judged.leads[0].verdict,'mine');
 const purchase=await f.get('filter=judged&order=pressing_clues&purchased=yes');
 assert.equal(purchase.filtered_total,1);assert.equal(purchase.leads[0].verdict,null);
 for(const params of ['order=guess','filter=family_review&order=pressing_clues',
  clues+'&cursor='+encodeURIComponent(newest.next_cursor),
  'cursor='+encodeURIComponent(first.next_cursor),
  clues+'&prices=all&cursor='+encodeURIComponent(first.next_cursor),
  'filter=judged&order=pressing_clues&cursor='+encodeURIComponent(first.next_cursor)])assert.equal((await f.request(params)).status,400);
 // A live set is not a snapshot: additions, removals and rank changes reject the
 // old generation before returning a misleading duplicate or skipped page.
 f.add('new-arrival');
 let changed=await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor));
 assert.equal(changed.status,409);assert.equal((await changed.json()).code,'inbox_order_changed');
 f.inbox.pop();f.listings.pop();
 assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,200);
 f.inbox[0].data.clues=['seller_numbered_claim'];
 assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,409);
 f.inbox[0].data.clues=['artist_and_album'];
 const supportedRow=f.inbox[70],supportedListing=f.listings.find(r=>r.marketplace_item_id===supportedRow.marketplace_item_id);
 for(const change of [{evidence_invalidated_at:f.fresh},{watch_revision:2}]){
  const before={...supportedRow.data};Object.assign(supportedRow.data,change);
  assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,409);
  supportedRow.data=before;
 }
 supportedListing.data.details_observed_at=new Date(f.now-200_000).toISOString();
 assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,409);
 supportedListing.data.details_observed_at=f.fresh;
 supportedRow.dismissed=true;
 assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,409);
 supportedRow.dismissed=false;
 // This is also a natural freshness crossing, without source-field changes.
 const originalNow=Date.now;Date.now=()=>f.now+3_600_000;
 try{assert.equal((await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor))).status,409);}finally{Date.now=originalNow;}
 f.inbox.length=0;f.listings.length=0;f.decisions.length=0;
 changed=await f.request(clues+'&cursor='+encodeURIComponent(first.next_cursor));
 assert.equal(changed.status,409);
 assert.deepEqual((await f.get(clues)).leads,[]);

 const e=pressingOrderFixture(query);
 const cases:[string,any,string[]][]=[
  ['identifier',{clues:['seller_identifier_claim']},['Matching identifier claim']],
  ['numbered',{clues:['seller_numbered_claim']},['Seller claims a numbered copy']],
  ['color',{clues:['seller_color_claim']},['Comparable vinyl color claim']],
  ['owner',{signs:['keyword: promo','numbered','catalog_number: SYN-1']},['Saved sign: catalog_number: SYN-1','Saved sign: keyword: promo','Saved sign: numbered']],
  ['owner-color',{signs:['color: blue']},['Saved sign: color: blue']],
  ['generic',{clues:['artist_and_album','cheat_sheet_sign']},[]],
  ['empty',{signs:['','color: ','unrecognized','keyword: ']},[]],
  ['malformed-signs',{signs:{'keyword: promo':true}},[]],
  ['missing-common',{missing_signs:['numbered'],common_signs:['keyword: reissue']},[]],
  ['revision',{clues:['seller_numbered_claim'],watch_revision:2},[]],
  ['string-revision',{clues:['seller_numbered_claim'],watch_revision:'1'},[]],
  ['invalidated',{clues:['seller_numbered_claim'],evidence_invalidated_at:e.fresh},[]],
  ['missing-review-time',{clues:['seller_numbered_claim'],details_observed_at:null},[]],
  ['stale-review',{clues:['seller_numbered_claim'],details_observed_at:'2000-01-01T00:00:00Z'},[]],
  ['invalid-review-time',{clues:['seller_numbered_claim'],details_observed_at:'2026-02-31T12:00:00Z'},[]],
  ['different-source-time',{clues:['seller_numbered_claim'],details_observed_at:new Date(e.now-200_000).toISOString()},[]],
 ];
 for(const flag of ['color_pair_incomplete','color_claim_ambiguous','color_conflict','required_sign_conflict','color_not_comparable']){
  cases.push(['blocked-color-'+flag,{clues:['seller_color_claim'],signs:['color: blue'],verify:[flag]},[]]);
  cases.push(['independent-'+flag,{clues:['seller_color_claim','seller_identifier_claim'],signs:['color: blue','keyword: promo'],verify:[flag]},['Matching identifier claim','Saved sign: keyword: promo']]);
 }
 for(const [id,review] of cases)e.add(id,{review});
 for(const [id,listing] of [
  ['missing-listing-time',{details_observed_at:null}],['stale-listing',{details_observed_at:'2000-01-01T00:00:00Z'}],
  ['invalid-listing-time',{details_observed_at:'2026-02-31T12:00:00Z'}],['future-listing',{details_observed_at:'2099-01-01T00:00:00Z'}],
  ['ended',{listing_ends_at:'2000-01-01T00:00:00Z'}],['invalid-end',{listing_ends_at:'2099-02-31T00:00:00Z'}],
  ['unsupported-end',{listing_ends_at:'2099-01-01'}],['invalid-end-hour',{listing_ends_at:'2099-01-01T29:00:00Z'}],
  ['missing-quality',{quality_flags:null}],['object-quality',{quality_flags:{}}],['scalar-quality',{quality_flags:'details_unavailable'}],['nested-quality',{quality_flags:[{}]}],
  ...['details_unavailable','item_specifics_stale','details_not_requested'].map(flag=>['quality-'+flag,{quality_flags:[flag]}]),
 ] as [string,any][]){e.add(id,{support:true,listing});cases.push([id,{},[]]);}
 e.add('pending',{support:true});cases.push(['pending',{},[]]);
 e.work.push({marketplace:'ebay',item_id:'pending',status:'pending',kind:'existing_listing_updated',reason:null,last_search_at:new Date(e.now).toISOString(),fingerprint:'synthetic-change'});
 const evidence=await e.get(clues+'&prices=all');
 assert.equal(evidence.leads.length,cases.length);
 for(const [id,,expected] of cases)assert.deepEqual(evidence.leads.find((r:any)=>r.marketplace_item_id===id).pressing_clues,expected,id);
 // Shared listing refreshes cannot freshen the older source behind review clues.
 assert.equal(evidence.leads.find((r:any)=>r.marketplace_item_id==='stale-review').pressing_clues.length,0);

 const j=pressingOrderFixture(query);
 for(let i=0;i<55;i++)j.add('mine-'+String(i).padStart(3,'0'),{support:true,verdict:'mine',purchased:i%2===0});
 j.add('other-clue',{support:true,verdict:'other'});
 j.add('saved-unavailable',{support:true,verdict:'mine',review:{availability:'unavailable_on_recheck',signs:['keyword: promo']},dismissed:true,listing:{current_price:'1000'}});
 for(const status of ['conflicting','unrelated'])j.add('saved-'+status,{support:true,verdict:'other',review:{status,signs:['keyword: promo']}});
 const scope='filter=judged&order=pressing_clues&judgment=mine',ja=await j.get(scope),jb=await j.get(scope+'&cursor='+encodeURIComponent(ja.next_cursor));
 assert.equal(ja.filtered_total,56);assert.equal(jb.leads.length,6);
 assert.equal(new Set([...ja.leads,...jb.leads].map(key)).size,56);
 const unavailable=jb.leads.find((r:any)=>r.marketplace_item_id==='saved-unavailable');
 assert.equal(unavailable.dismissed,true);assert.equal(unavailable.verdict,'mine');assert.deepEqual(unavailable.pressing_clues,[]);
 assert.equal((await j.get(scope+'&purchased=yes')).filtered_total,28);
 assert.equal((await j.request(scope+'&purchased=yes&cursor='+encodeURIComponent(ja.next_cursor))).status,400);
 assert.equal((await j.request('filter=judged&order=pressing_clues&judgment=other&cursor='+encodeURIComponent(ja.next_cursor))).status,400);
 const other=await j.get('filter=judged&order=pressing_clues&judgment=other');
 assert.deepEqual(other.leads[0].pressing_clues,ja.leads[0].pressing_clues);
 for(const status of ['conflicting','unrelated'])assert.deepEqual(other.leads.find((r:any)=>r.data.status===status).pressing_clues,[]);

 const realNow=Date.now;Date.now=()=>Date.UTC(2028,2,1,0,20);
 try{
  const b=pressingOrderFixture(query);
  const times:[string,string,boolean][]=[
   ['hour-boundary','2028-02-29T23:20:00+00:00',true],['too-old','2028-02-29T23:19:59.999Z',false],
   ['microseconds','2028-03-01T00:19:59.123456Z',true],['current','2028-03-01T00:20:00Z',true],
   ['future','2028-03-01T00:20:00.001Z',false],['invalid-calendar','2028-02-30T23:40:00Z',false],
   ['bad-hour','2028-03-01T24:00:00Z',false],['bad-minute','2028-03-01T00:60:00Z',false],
   ['unsupported-offset','2028-02-29T19:00:00-05:00',false],['unsupported-date','2028-03-01',false],
  ];
  for(const [id,at] of times)b.add(id,{support:true,listing:{details_observed_at:at},review:{details_observed_at:at}});
  b.add('future-end',{support:true,listing:{listing_ends_at:'2028-03-05T12:00:00Z'}});
  const page=await b.get(clues+'&prices=all');
  for(const [id,,supported] of times)assert.equal(page.leads.find((r:any)=>r.marketplace_item_id===id).pressing_clues.length,supported?1:0,id);
  assert.equal(page.leads.find((r:any)=>r.marketplace_item_id==='future-end').pressing_clues.length,1);
 }finally{Date.now=realNow;}
}
