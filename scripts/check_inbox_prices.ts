// Execute the dashboard's actual query against synthetic CTEs only. No production
// rows are read or written and no provider identities enter this check.
import assert from "node:assert/strict";
import { Pool } from "pg";
import { inboxQuery } from "../functions/inbox-query.js";

const pool = new Pool({connectionString:process.env.FINDER_DATABASE_URL});
const prefix = `WITH finder_watches AS (
  SELECT * FROM jsonb_to_recordset($7::jsonb) AS x(id text,config jsonb,revision integer)
), listings AS (
  SELECT * FROM jsonb_to_recordset($8::jsonb) AS x(marketplace text,marketplace_item_id text,data jsonb)
), finder_inbox AS (
  SELECT * FROM jsonb_to_recordset($9::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,data jsonb,first_seen_at text,last_seen_at text,dismissed boolean)
), finder_verdicts AS (
  SELECT * FROM jsonb_to_recordset($10::jsonb) AS x(watch_id text,marketplace text,marketplace_item_id text,verdict text)
) `;
const config = {maximum_subtotal:"20.00",currency:"USD",country:"US",postal_code:"00000"};
const listing = {current_price:"15.00",shipping_cost:"5.00",currency:"USD",shipping_currency:"USD",price_kind:"fixed_price",details_observed_at:"2026-01-02T12:00:00.000Z",source_metadata:{delivery_country:"US",delivery_postal_code:"00000"}};
const stamp = "2026-01-02T12:00:00.000Z";
async function query(items:any[], watch:any=config, scope="watch", cursor:any[]= [null,null,null], filter="possible_pressing", judged:string[]=[]) {
  const listings=items.map(x=>({marketplace:"ebay",marketplace_item_id:x.id,data:{...listing,...x.listing}}));
  const inbox=items.map(x=>({watch_id:"synthetic",marketplace:"ebay",marketplace_item_id:x.id,data:{status:"possible_pressing",watch_revision:1,...x.review},first_seen_at:stamp,last_seen_at:stamp,dismissed:false}));
  const verdicts=judged.map(id=>({watch_id:"synthetic",marketplace:"ebay",marketplace_item_id:id,verdict:"other"}));
  return (await pool.query(prefix+inboxQuery,[filter,...cursor,scope,"2026-01-02T06:00:00.000Z",JSON.stringify([{id:"synthetic",config:watch,revision:1}]),JSON.stringify(listings),JSON.stringify(inbox),JSON.stringify(verdicts)])).rows;
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
    {id:"bad-number",listing:{current_price:"NaN"}},
  ];
  assert.deepEqual((await query(cases)).map(x=>x.marketplace_item_id).sort(),["auction","boundary","under"]);
  assert.equal((await query(cases,config,"all")).length,cases.length);
  assert.equal((await query(cases,{...config,maximum_subtotal:null})).length,cases.length);
  assert.equal((await query([{id:"edited"}],{...config,maximum_subtotal:"19.99"})).length,0);
  assert.equal((await query([{id:"edited"}],{...config,maximum_subtotal:"20.01"})).length,1);
  const reviewed=[{id:"new"},{id:"judged"}];
  assert.deepEqual((await query(reviewed,config,"watch",[null,null,null],"review",["judged"])).map(x=>x.marketplace_item_id),["new"]);
  assert.deepEqual((await query(reviewed,config,"watch",[null,null,null],"judged",["judged"])).map(x=>x.marketplace_item_id),["judged"]);
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
