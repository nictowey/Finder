// Canonical identity feedback is separate from purchasing. The legacy table remains
// an alert-suppression mirror for existing workers; it is never an accuracy source.
export const saveDecisionQuery = `SELECT verdict,purchased,tier AS verdict_tier,decided_at AS verdict_decided_at,
 prediction->>'source' AS verdict_provenance FROM finder_save_decision($1,$2,$3,$4,$5,$6,$7,$8,$9)`;

export function decisionInput(value:any, purchase:boolean) {
  if(!value || typeof value.watch_id!=="string" || !value.watch_id || value.marketplace!=="ebay" ||
    typeof value.marketplace_item_id!=="string" || !value.marketplace_item_id || value.marketplace_item_id.length>255) return null;
  // Old clients still send bought through /api/verdict. It records only a purchase.
  const legacyPurchase=!purchase && value.verdict==="bought";
  if(purchase && typeof value.purchased!=="boolean")return null;
  if(!purchase && !legacyPurchase && value.verdict!==null && !["mine","other","unsure"].includes(value.verdict))return null;
  if(value.observed_tier!==undefined && (typeof value.observed_tier!=="string" || value.observed_tier.length>32))return null;
  if(value.observed_evaluated_at!==undefined && (typeof value.observed_evaluated_at!=="string" || value.observed_evaluated_at.length>40 || !Number.isFinite(Date.parse(value.observed_evaluated_at))))return null;
  return [value.watch_id,value.marketplace,value.marketplace_item_id,purchase||legacyPurchase?"purchase":"identity",
    purchase||legacyPurchase?null:value.verdict,legacyPurchase?true:purchase?value.purchased:false,
    new Date().toISOString(),value.observed_tier??null,value.observed_evaluated_at??null];
}
