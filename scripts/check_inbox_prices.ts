// Execute the dashboard's actual query against synthetic CTEs only. No production
// rows are read or written and no provider identities enter this check.
import assert from "node:assert/strict";
import { Pool } from "pg";
import { inboxQuery } from "../functions/inbox-query.js";

const pool = new Pool({connectionString:process.env.FINDER_DATABASE_URL});
const prefix = `WITH finder_watches AS (
  SELECT * FROM jsonb_to_recordset($9::jsonb) AS x(id text,config jsonb,revision integer)
), listings AS (
  SELECT * FROM jsonb_to_recordset($10::jsonb) AS x(marketplace text,marketplace_item_id text,data jsonb)
), finder_inbox AS (
  SELECT * FROM jsonb_to_recordset($11::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,data jsonb,first_seen_at text,last_seen_at text,dismissed boolean)
), finder_decisions AS (
  SELECT * FROM jsonb_to_recordset($12::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,verdict text,purchased boolean)
) , finder_discovery_work AS (
  SELECT * FROM jsonb_to_recordset($13::jsonb) AS x(marketplace text,listing_id text,item_id text,status text,kind text,reason text,last_search_at text,fingerprint text)
) `;
const config = {maximum_subtotal:"20.00",currency:"USD",country:"US",postal_code:"00000"};
const listing = {current_price:"15.00",shipping_cost:"5.00",currency:"USD",shipping_currency:"USD",price_kind:"fixed_price",details_observed_at:"2026-01-02T12:00:00.000Z",source_metadata:{delivery_country:"US",delivery_postal_code:"00000"}};
const stamp = "2026-01-02T12:00:00.000Z";
async function query(items:any[], watch:any=config, scope="watch", cursor:any[]= [null,null,null], filter="possible_pressing", judged:string[]=[]) {
  const listings=items.map(x=>({marketplace:"ebay",marketplace_item_id:x.id,data:{...listing,...x.listing}}));
  const inbox=items.map(x=>({watch_id:"synthetic",marketplace:"ebay",marketplace_item_id:x.id,data:{status:"possible_pressing",watch_revision:1,details_observed_at:stamp,...x.review},first_seen_at:stamp,last_seen_at:stamp,dismissed:false}));
  const verdicts=judged.map(id=>({watch_id:"synthetic",marketplace:"ebay",marketplace_item_id:id,verdict:"other"}));
  const work=items.filter(x=>x.pending).map(x=>({marketplace:null,listing_id:null,item_id:x.id,status:"pending",kind:"existing_listing_updated",reason:null,last_search_at:"2026-01-02T12:01:00+00:00",fingerprint:"changed",...x.pending}));
  return (await pool.query(prefix+inboxQuery,[filter,...cursor,scope,"2026-01-02T06:00:00.000Z","all","all",JSON.stringify([{id:"synthetic",config:watch,revision:1}]),JSON.stringify(listings),JSON.stringify(inbox),JSON.stringify(verdicts),JSON.stringify(work)])).rows;
}
try {
  const cases = [
    {id:"boundary"},
    {id:"under",listing:{current_price:"14.99"}},
    {id:"over",listing:{current_price:"15.01"}},
    {id:"high",listing:{current_price:"100.00"}},
    {id:"unknown-shipping",listing:{shipping_cost:null}},
    {id:"other-currency",listing:{currency:"EUR"}},
    {id:"shipping-currency",listing:{shipping_currency:"EUR"}},
    {id:"auction",listing:{price_kind:"current_bid"}},
    {id:"unknown-kind",listing:{price_kind:"unknown"}},
    {id:"stale",listing:{details_observed_at:"2026-01-01T00:00:00.000Z"}},
    {id:"revision",review:{watch_revision:0}},
    {id:"wrong-destination",listing:{source_metadata:{delivery_country:"US",delivery_postal_code:"99999"}}},
    {id:"failed-details",listing:{quality_flags:["details_unavailable"]}},
    {id:"invalidated-review",review:{evidence_invalidated_at:stamp}},
    {id:"legacy-pending-change",pending:{}},
    {id:"bad-number",listing:{current_price:"NaN"}},
  ];
  assert.deepEqual((await query(cases)).map(x=>x.marketplace_item_id).sort(),["auction","boundary","under"]);
  assert.equal((await query(cases,config,"all")).length,cases.length);
  assert.equal((await query(cases,{...config,maximum_subtotal:null})).length,cases.length);
  const newer=await query([{id:"newer-review",pending:{},review:{details_observed_at:"2026-01-02T12:02:00Z"}}]);
  assert.equal(newer.length,1);assert.equal(newer[0].pending_evidence_change,false);
  const equal=await query([{id:"equal-time",pending:{last_search_at:"2026-01-02T07:00:00-05:00"}}]);
  assert.equal(equal.length,1);assert.equal(equal[0].pending_evidence_change,false);
  const duplicate=await query([{id:"duplicate",pending:{last_search_at:"2026-01-02T12:03:00Z"},listing:{source_metadata:{...listing.source_metadata,finder_summary_fingerprint:"changed",finder_details_invalidated_at:"2026-01-02T11:59:00Z"}}}]);
  assert.equal(duplicate.length,1);assert.equal(duplicate[0].pending_evidence_change,false);
  const held=await query([{id:"legacy",pending:{}}],config,"all");
  assert.equal(held[0].pending_evidence_change,true);
  assert.equal((await query([{id:"edited"}],{...config,maximum_subtotal:"19.99"})).length,0);
  assert.equal((await query([{id:"edited"}],{...config,maximum_subtotal:"20.01"})).length,1);
  const reviewed=[{id:"new"},{id:"judged"}];
  assert.deepEqual((await query(reviewed,config,"watch",[null,null,null],"review",["judged"])).map(x=>x.marketplace_item_id),["new"]);
  assert.deepEqual((await query(reviewed,config,"watch",[null,null,null],"judged",["judged"])).map(x=>x.marketplace_item_id),["judged"]);
  assert.deepEqual((await query([{id:"costly",listing:{current_price:"100.00"}}],config,"watch",[null,null,null],"judged",["costly"])).map(x=>x.marketplace_item_id),["costly"]);
  const many=[...Array.from({length:60},(_,i)=>({id:"z"+String(i).padStart(3,"0"),listing:{current_price:"100"}})),{id:"a"},{id:"b"}];
  assert.deepEqual((await query(many)).map(x=>x.marketplace_item_id),["b","a"]);
  const first=await query(many,config,"all");
  assert.equal(first.length,51);
  const tail=first[49];
  const second=await query(many,config,"all",[tail.first_seen_at,tail.watch_id,tail.marketplace_item_id]);
  assert.equal(second.length,12);
  assert.equal(new Set([...first.slice(0,50),...second].map(x=>x.marketplace_item_id)).size,62);
  console.log('{"inbox_price_ceiling_query":"passed"}');
} catch {
  console.error('{"inbox_price_ceiling_query":"failed"}');
  process.exitCode=1;
} finally {
  await pool.end();
}
