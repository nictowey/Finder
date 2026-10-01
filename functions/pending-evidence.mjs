// Durable known-change work can predate the explicit invalidation marker. The
// predicate is shared by dashboard reads and alert quarantine; i is the review,
// l the shared listing. A duplicate observation reuses its established boundary.
export const pendingEvidenceChange = `EXISTS (
  SELECT 1 FROM finder_discovery_work d
  WHERE COALESCE(d.marketplace,'ebay')=i.marketplace AND d.item_id=i.marketplace_item_id
    AND d.status='pending' AND d.kind='existing_listing_updated' AND d.reason IS NULL
    AND CASE WHEN d.fingerprint=l.data->'source_metadata'->>'finder_summary_fingerprint'
      THEN COALESCE(l.data->'source_metadata'->>'finder_details_invalidated_at',d.last_search_at)::timestamptz
      ELSE GREATEST(d.last_search_at::timestamptz,COALESCE((l.data->'source_metadata'->>'finder_details_invalidated_at')::timestamptz,'-infinity'::timestamptz)) END
      > COALESCE((i.data->>'details_observed_at')::timestamptz,'-infinity'::timestamptz)
)`;
