/// <reference types="emscripten" />
import assert from "node:assert/strict";
import test from "node:test";
import { PGlite } from "@electric-sql/pglite";
import { summarizeVerdictReview,verdictReviewHtml,verdictReviewQuery } from "./verdict-review.js";

const cell=(current_tier:string,verdict:string,evidence:string,n:number,judged_tier="family_review")=>({judged_tier,current_tier,verdict,evidence,n});
// Aggregate-only synthetic distribution. No provider content or identities are fixtures.
const retrospective=[
  cell("family_review","mine","stale",3),cell("conflicting","mine","stale",1),
  cell("unrelated","mine","stale",3),cell("family_review","mine","unavailable",1),
  cell("family_review","other","fresh",27),cell("family_review","other","stale",30),
  cell("conflicting","other","fresh",4),cell("conflicting","other","stale",24),
  cell("unrelated","other","stale",5),cell("family_review","other","unavailable",2),
];

test("retrospective review yield is separated from current tiers and freshness",()=>{
  const report=summarizeVerdictReview(retrospective);
  assert.equal(report.scope,"retrospective_owner_verdicts");
  assert.deepEqual(report.total,{total:100,yours:8,other:92,unsure:0,unknown:0,decided:100,review_yield:.08});
  assert.equal(report.original.family_review.total,100);
  assert.equal(report.original.possible_pressing,undefined);
  assert.deepEqual(report.evidence,{fresh:31,stale:66,unavailable:3,missing:0});
  assert.deepEqual(report.confirmed,{total:8,fresh_reviewable:0,fresh_outside_review:0,stale:7,unavailable:1,missing:0});
  assert.equal(report.current.conflicting.yours,1);
  assert.equal(report.current.unrelated.yours,3);
  assert.equal(report.matrix.reduce((n,row)=>n+row.n,0),100);
  assert.ok(!("precision" in report));
});

test("fresh classification concerns exclude stale, unavailable and missing positives",()=>{
  const report=summarizeVerdictReview([
    cell("family_review","mine","fresh",2),cell("conflicting","bought","fresh",1),
    cell("unrelated","mine","fresh",1),cell("unrelated","mine","stale",4),
    cell("conflicting","mine","unavailable",3),cell("unknown","mine","missing",1),
  ]);
  assert.deepEqual(report.confirmed,{total:12,fresh_reviewable:2,fresh_outside_review:2,stale:4,unavailable:3,missing:1});
});

test("uncertain and unknown verdicts never enter a decided denominator",()=>{
  const report=summarizeVerdictReview([cell("family_review","unsure","fresh",3),cell("conflicting","unexpected","fresh",2)]);
  assert.equal(report.total.total,5);
  assert.equal(report.total.decided,0);
  assert.equal(report.total.review_yield,null);
  assert.equal(report.confirmed.total,0);
  assert.equal(summarizeVerdictReview([]).total.review_yield,null);
  assert.equal(summarizeVerdictReview([cell("family_review","mine","fresh",-2)]).total.total,0);
});

test("verdict query is aggregate-only and uses the six-hour and revision freshness gates",()=>{
  assert.ok(verdictReviewQuery.includes("FROM finder_verdicts v"));
  assert.ok(verdictReviewQuery.includes("LEFT JOIN finder_inbox"));
  assert.ok(verdictReviewQuery.includes("details_observed_at')::timestamptz >= $1::timestamptz"));
  assert.ok(verdictReviewQuery.includes("watch_revision'=w.revision::text"));
  assert.ok(verdictReviewQuery.includes("unavailable_on_recheck"));
  assert.ok(!/INSERT|UPDATE|DELETE/i.test(verdictReviewQuery));
  assert.ok(!/listing_url|->>'title'|->'item_specifics'|seller_id/.test(verdictReviewQuery));
});

test("browser report labels stale coverage without presenting it as fresh failure",()=>{
  const report=summarizeVerdictReview(retrospective);
  const browserRenderer=new Function('return ('+verdictReviewHtml.toString()+')')();
  const html=browserRenderer(report);
  assert.ok(html.includes('31 fresh · 66 stale · 3 unavailable'));
  assert.ok(html.includes('0 have fresh evidence outside those tiers'));
  assert.ok(html.includes('7 need fresh details'));
  assert.ok(html.includes('not a held-out accuracy or precision estimate'));
  assert.ok(!html.includes('8% precision'));
  assert.equal(verdictReviewHtml(summarizeVerdictReview([])),"");
});

test("production verdict SQL runs against isolated PostgreSQL with no provider data",async()=>{
  const db=new PGlite();
  try{
    await db.exec(`CREATE TABLE finder_verdicts(watch_id text,marketplace text,marketplace_item_id text,tier text,verdict text);
      CREATE TABLE finder_inbox(watch_id text,marketplace text,marketplace_item_id text,data jsonb);
      CREATE TABLE listings(marketplace text,marketplace_item_id text,data jsonb);
      CREATE TABLE finder_watches(id text,revision integer);
      INSERT INTO finder_watches VALUES('synthetic-watch',1);`);
    const cases=[
      {id:"fresh",status:"family_review",verdict:"mine"},
      {id:"stale",status:"conflicting",verdict:"mine",old:true},
      {id:"unavailable",status:"unrelated",verdict:"mine",unavailable:true},
      {id:"missing",status:"unrelated",verdict:"mine",missing:true},
      {id:"revision",status:"unrelated",verdict:"mine",revision:0},
      {id:"failed-details",status:"conflicting",verdict:"mine",flags:["details_unavailable"]},
      {id:"negative",status:"conflicting",verdict:"other"},
    ];
    for(const item of cases){
      await db.query('INSERT INTO finder_verdicts VALUES($1,$2,$3,$4,$5)',["synthetic-watch","ebay",item.id,"family_review",item.verdict]);
      await db.query('INSERT INTO finder_inbox VALUES($1,$2,$3,$4)',["synthetic-watch","ebay",item.id,JSON.stringify({status:item.status,watch_revision:item.revision??1,...(item.unavailable?{availability:"unavailable_on_recheck"}:{})})]);
      if(!item.missing)await db.query('INSERT INTO listings VALUES($1,$2,$3)',["ebay",item.id,JSON.stringify({details_observed_at:item.old?"2026-01-01T00:00:00.000Z":"2026-01-02T12:00:00.000Z",quality_flags:item.flags??[]})]);
    }
    const result=await db.query<Record<string,unknown>>(verdictReviewQuery,["2026-01-02T06:00:00.000Z"]);
    const report=summarizeVerdictReview(result.rows);
    assert.equal(report.total.total,7);
    assert.equal(report.original.family_review.total,7);
    assert.deepEqual(report.evidence,{fresh:2,stale:3,unavailable:1,missing:1});
    assert.deepEqual(report.confirmed,{total:6,fresh_reviewable:1,fresh_outside_review:0,stale:3,unavailable:1,missing:1});
    assert.equal((await db.query<{n:number}>('SELECT count(*)::int AS n FROM finder_verdicts')).rows[0].n,7);
    assert.ok(result.rows.every(row=>!('marketplace_item_id' in row)));
    // Python and browser timestamps can spell the same instant differently.
    for(const [stamp,expected] of [
      ["2026-01-02T06:00:00+00:00","fresh"],
      ["2026-01-02T01:00:00-05:00","fresh"],
      ["2026-01-02T07:00:00+02:00","stale"],
      ["2026-01-02T05:59:59.999Z","stale"],
    ]){
      await db.query("UPDATE listings SET data=jsonb_set(data,'{details_observed_at}',to_jsonb($1::text)) WHERE marketplace_item_id='fresh'",[stamp]);
      const rows=(await db.query<Record<string,unknown>>(verdictReviewQuery,["2026-01-02T06:00:00.000Z"])).rows;
      const freshCase=rows.find(row=>row.current_tier==="family_review"&&row.verdict==="mine"&&row.evidence!=="unavailable");
      assert.equal(freshCase?.evidence,expected);
    }
  }finally{await db.close();}
});
