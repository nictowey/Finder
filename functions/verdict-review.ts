// Read-only, aggregate comparison of the owner's judgments with stored classifications.
// No listing content or identities leave this query, and no verdict is changed.
export const verdictReviewQuery = `SELECT v.tier AS judged_tier,v.verdict,
  COALESCE(i.data->>'status','unknown') AS current_tier,
  CASE WHEN i.watch_id IS NULL OR l.marketplace_item_id IS NULL OR w.id IS NULL THEN 'missing'
    WHEN i.data->>'availability'='unavailable_on_recheck' THEN 'unavailable'
    WHEN (l.data->>'details_observed_at')::timestamptz >= $1::timestamptz
      AND i.data->>'watch_revision'=w.revision::text
      AND NOT COALESCE((l.data->'quality_flags')::jsonb ?| ARRAY['details_unavailable','item_specifics_stale','details_not_requested'],false)
      THEN 'fresh' ELSE 'stale' END AS evidence,
  count(*)::int AS n
  FROM finder_verdicts v
  LEFT JOIN finder_inbox i USING(watch_id,marketplace,marketplace_item_id)
  LEFT JOIN listings l USING(marketplace,marketplace_item_id)
  LEFT JOIN finder_watches w ON w.id=v.watch_id
  GROUP BY v.tier,v.verdict,current_tier,evidence`;

type Counts = { total:number; yours:number; other:number; unsure:number; unknown:number; decided:number; review_yield:number|null };
type Evidence = "fresh"|"stale"|"unavailable"|"missing";
type Cell = { judged_tier:string; current_tier:string; verdict:string; evidence:Evidence; n:number };

export function summarizeVerdictReview(rows:any[]) {
  const empty=():Counts=>({total:0,yours:0,other:0,unsure:0,unknown:0,decided:0,review_yield:null});
  const original:Record<string,Counts>={},current:Record<string,Counts>={};
  const evidence={fresh:0,stale:0,unavailable:0,missing:0};
  const confirmed={total:0,fresh_reviewable:0,fresh_outside_review:0,stale:0,unavailable:0,missing:0};
  const total=empty(),matrix:Cell[]=[];
  const tiers=["possible_pressing","family_review","conflicting","unrelated","unavailable"];
  const tier=(value:unknown)=>typeof value==="string"&&tiers.includes(value)?value:"unknown";
  const add=(counts:Counts,verdict:string,n:number)=>{
    counts.total+=n;
    if(verdict==="mine"||verdict==="bought")counts.yours+=n;
    else if(verdict==="other")counts.other+=n;
    else if(verdict==="unsure")counts.unsure+=n;
    else counts.unknown+=n;
    counts.decided=counts.yours+counts.other;
    counts.review_yield=counts.decided?counts.yours/counts.decided:null;
  };
  for(const row of rows){
    const n=Number(row.n);
    if(!Number.isSafeInteger(n)||n<=0)continue;
    const freshness:Evidence=["fresh","stale","unavailable","missing"].includes(row.evidence)?row.evidence:"missing";
    const atJudgment=tier(row.judged_tier);
    const stored=freshness==="unavailable"?"unavailable":tier(row.current_tier);
    const verdict=["mine","bought","other","unsure"].includes(row.verdict)?row.verdict:"unknown";
    add(total,verdict,n);
    add(original[atJudgment]??=empty(),verdict,n);
    add(current[stored]??=empty(),verdict,n);
    evidence[freshness]+=n;
    matrix.push({judged_tier:atJudgment,current_tier:stored,verdict,evidence:freshness,n});
    if(verdict==="mine"||verdict==="bought"){
      confirmed.total+=n;
      if(freshness!=="fresh")confirmed[freshness]+=n;
      else if(["possible_pressing","family_review"].includes(stored))confirmed.fresh_reviewable+=n;
      else confirmed.fresh_outside_review+=n;
    }
  }
  return {scope:"retrospective_owner_verdicts",total,original,current,evidence,confirmed,matrix};
}

// Kept self-contained so this exact tested renderer can be embedded in the existing UI.
export function verdictReviewHtml(report:ReturnType<typeof summarizeVerdictReview>|undefined):string {
  if(!report||!report.total.total)return "";
  const e=report.evidence,c=report.confirmed;
  const labels:Record<string,string>={possible_pressing:"Likely yours",family_review:"Unclear",conflicting:"Likely another version",unrelated:"Other search result",unavailable:"Unavailable on recheck",unknown:"No stored classification"};
  const rows=Object.entries(labels).filter(([tier])=>report.current[tier]?.total).map(([tier,label])=>{
    const row=report.current[tier];
    return '<p>'+label+': '+row.total+' judged · '+row.yours+' yours · '+row.other+' other · '+row.unsure+' uncertain'+(row.unknown?' · '+row.unknown+' unrecognized verdicts':'')+'</p>';
  }).join("");
  return '<details><summary>Current stored classifications and evidence</summary><p>Evidence coverage: '+e.fresh+' fresh · '+e.stale+' stale · '+e.unavailable+' unavailable on recheck · '+e.missing+' missing, across '+report.total.total+' judged listings.</p>'+rows+
    (c.total?'<p>Of '+c.total+' you marked yours: '+c.fresh_reviewable+' have fresh evidence in reviewable tiers; '+c.fresh_outside_review+' have fresh evidence outside those tiers and need review; '+c.stale+' need fresh details; '+c.unavailable+' are unavailable on recheck; '+c.missing+' have missing records.</p>':'<p>No listings have been confirmed as yours yet.</p>')+
    '<p class="small">Retrospective stored classifications, not a held-out accuracy or precision estimate. Fresh details do not prove the latest matching policy was applied. Stale and unavailable records are separate from fresh classification concerns. No judgments have been changed.</p></details>';
}
