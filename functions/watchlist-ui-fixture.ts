// Synthetic UI fixtures only. No seller identities, real photos, or production data.
export function dashboardFixture() {
  const now = new Date().toISOString();
  const old = new Date(Date.now() - 8 * 3600_000).toISOString();
  const watch = (id: string, label: string, release: number) => ({
    id, config: { label, release_id: release, enabled: true, currency: 'USD', maximum_subtotal: '80.00', gamble_max: '30.00', condition_ids: [], auction_alert_minutes: 120, alert_mode: 'review_leads', tells: [{kind:'color',value:'Moss green',required:true}], anti_tells: [], extra_queries: [] },
    status: 'completed', last_success_at: now, next_scan_at: new Date(Date.now() + 600000).toISOString(),
    profile: { vinyl_versions: 5, partial: false, proposals: {tells:[],anti_tells:[]} }, inbox_counts:{possible_pressing:1,family_review:2}, verdict_counts: [], summary: {}
  });
  const lead = (id: string, watchId: string, title: string, tier: string, price: string | null) => ({
    watch_id: watchId, marketplace: 'ebay', marketplace_item_id: id, first_seen_at: now, last_seen_at: now,
    dismissed: false, verdict: null as string | null, purchased: false, verdict_tier: null as string | null, verdict_decided_at: null as string | null, verdict_provenance: null as string | null,
    evidence_stale: false,
    data: { status: tier, clues: ['artist_and_album','seller_color_claim'], signs: tier==='possible_pressing'?['Moss green vinyl','Catalog number matches']:[], missing_signs: [] as string[], common_signs: [] as string[], verify: tier==='possible_pressing'?['verify_photos_condition_and_checkout_total']:['numbered_copy_unconfirmed','pressing_identifier_absent'], alternatives_checked:4, alternatives_not_ruled_out:tier==='possible_pressing'?0:2, availability:undefined as string|undefined, subtotal:price, currency:'USD', budget:price?'within_ceiling':'needs_refresh', discovery_kind:'new_to_finder' },
    listing: { title, listing_url: 'https://www.ebay.com/itm/000000000000', details_observed_at: now, price_kind: 'fixed_price', condition:'Used · check media and sleeve', images:['https://i.ebayimg.com/finder-synthetic/'+id+'-1.svg','https://i.ebayimg.com/finder-synthetic/'+id+'-2.svg'], item_specifics:{'Record label':['Example Records'],'Format':['Vinyl, LP']} }
  });
  const stale=lead('sample-stale','sample-b','Previously discovered listing · awaiting fresh details','family_review',null);
  stale.evidence_stale=true;stale.listing.images=[];stale.listing.details_observed_at=old;stale.data.clues=[];stale.data.verify=['reference_only_current_availability_unverified'];
  return {
    watches:[watch('sample-a','Milo West · After Hours',10001),watch('sample-b','The Pines · Still Life',10002)],
    leads:[lead('sample-a-1','sample-a','Milo West — After Hours / Moss Green Vinyl LP','possible_pressing','46.00'),lead('sample-b-1','sample-b','The Pines — Still Life, limited edition LP','family_review','28.50'),stale],
    accuracy:[] as {tier:string|null,verdict:string,n:number}[],now,
    max_watches:20,next_cursor:null,push_key:null,
    operations:{status:'collecting',counts:{completed:240,failed:0,quota_paused:1,abandoned:0},measured_since:old,elapsed_days:7,max_start_delay_minutes:18,browse_requests:1200,trigger_last_seen_at:now,trigger_stale:false,dispatches:{correlated:80},recent:[],notifications:{registered_devices:1,last_delivery:{at:now,status:'no_eligible_alerts'}}}
  };
}

// Synthetic API projection: filter before keyset pagination, and compute report
// totals from the entire source dataset. Shared by browser preview and DOM tests.
export function fixtureDashboard(data: ReturnType<typeof dashboardFixture>, path: string) {
  const result=structuredClone(data),params=new URL(path,'https://finder.example').searchParams;
  const filter=params.get('filter')||'review',judgment=params.get('judgment')||'all',purchase=params.get('purchased')||'all';
  const saved=data.leads.filter(r=>r.verdict||r.purchased);
  const count=(verdict:string)=>saved.filter(r=>r.verdict===verdict).length;
  const decision_totals={judged:saved.filter(r=>r.verdict).length,mine:count('mine'),other:count('other'),unsure:count('unsure'),purchased:saved.filter(r=>r.purchased).length,purchase_only:saved.filter(r=>r.purchased&&!r.verdict).length,dismissed_judged:saved.filter(r=>r.dismissed&&r.verdict).length};
  const judged_counts={all:saved.length,mine:decision_totals.mine,other:decision_totals.other,unsure:decision_totals.unsure,purchased:decision_totals.purchased};
  result.accuracy=[];
  for(const watch of result.watches)watch.verdict_counts=[];
  for(const row of saved)if(row.verdict){
    const tier=row.verdict_tier||'unknown';
    for(const list of [result.accuracy,result.watches.find(w=>w.id===row.watch_id)!.verdict_counts as {tier:string,verdict:string,n:number}[]]){
      let bucket=list.find(x=>x.tier===tier&&x.verdict===row.verdict);if(!bucket){bucket={tier,verdict:row.verdict,n:0};list.push(bucket);}bucket.n++;
    }
  }
  let rows=data.leads.filter(r=>filter==='judged'?(r.verdict||r.purchased)&&(judgment==='all'||r.verdict===judgment)&&(purchase==='all'||r.purchased):filter==='dismissed'?r.dismissed:filter==='unavailable'?!r.dismissed&&r.data.availability==='unavailable_on_recheck':!r.dismissed&&r.data.availability!=='unavailable_on_recheck'&&(filter==='review'?!r.verdict&&!r.purchased&&['possible_pressing','family_review'].includes(r.data.status):r.data.status===filter));
  const filtered_total=filter==='judged'?rows.length:null;
  const key=(r:typeof rows[number])=>[r.first_seen_at,r.watch_id,r.marketplace_item_id].join('|');
  rows.sort((a,b)=>key(b).localeCompare(key(a)));
  const cursor=params.get('cursor');if(cursor){const [at,watch,id]=JSON.parse(cursor);rows=rows.filter(r=>key(r)<[at,watch,id].join('|'));}
  const page=rows.slice(0,50),tail=page.at(-1);
  return {...result,leads:structuredClone(page),decision_totals,judged_counts,filtered_total,next_cursor:rows.length>50&&tail?JSON.stringify([tail.first_seen_at,tail.watch_id,tail.marketplace_item_id]):null,now:new Date().toISOString()};
}
