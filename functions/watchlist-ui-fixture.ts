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
    data: { status: tier, clues: ['artist_and_album','seller_color_claim'], signs: tier==='possible_pressing'?['Moss green vinyl','Catalog number matches']:[], missing_signs: [] as string[], common_signs: [] as string[], verify: tier==='possible_pressing'?['verify_photos_condition_and_checkout_total']:['numbered_copy_unconfirmed','pressing_identifier_absent'], alternatives_checked:4, alternatives_not_ruled_out:tier==='possible_pressing'?0:2, subtotal:price, currency:'USD', budget:price?'within_ceiling':'needs_refresh', discovery_kind:'new_to_finder' },
    listing: { title, listing_url: 'https://www.ebay.com/itm/000000000000', details_observed_at: now, price_kind: 'fixed_price', condition:'Used · check media and sleeve', images:['https://i.ebayimg.com/finder-synthetic/'+id+'-1.svg','https://i.ebayimg.com/finder-synthetic/'+id+'-2.svg'], item_specifics:{'Record label':['Example Records'],'Format':['Vinyl, LP']} }
  });
  const stale=lead('sample-stale','sample-b','Previously discovered listing · awaiting fresh details','family_review',null);
  stale.evidence_stale=true;stale.listing.images=[];stale.listing.details_observed_at=old;stale.data.clues=[];stale.data.verify=['reference_only_current_availability_unverified'];
  return {
    watches:[watch('sample-a','Milo West · After Hours',10001),watch('sample-b','The Pines · Still Life',10002)],
    leads:[lead('sample-a-1','sample-a','Milo West — After Hours / Moss Green Vinyl LP','possible_pressing','46.00'),lead('sample-b-1','sample-b','The Pines — Still Life, limited edition LP','family_review','28.50'),stale],
    accuracy:[{tier:'possible_pressing',verdict:'mine',n:8},{tier:'possible_pressing',verdict:'other',n:2},{tier:'family_review',verdict:'mine',n:3},{tier:'family_review',verdict:'other',n:9},{tier:'family_review',verdict:'unsure',n:2}],
    max_watches:20,next_cursor:null,push_key:null,
    operations:{status:'collecting',counts:{completed:240,failed:0,quota_paused:1,abandoned:0},measured_since:old,elapsed_days:7,max_start_delay_minutes:18,browse_requests:1200,trigger_last_seen_at:now,trigger_stale:false,dispatches:{correlated:80},recent:[],notifications:{registered_devices:1,last_delivery:{at:now,status:'no_eligible_alerts'}}}
  };
}
