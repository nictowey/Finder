-- Volatile PL/pgSQL runs its mutation statement with a fresh READ COMMITTED
-- snapshot after acquiring the worker's watch lock, including events queued while waiting.
CREATE OR REPLACE FUNCTION finder_save_decision(text,text,text,text,text,boolean,text,text,text)
RETURNS SETOF finder_decisions LANGUAGE plpgsql VOLATILE AS $$
BEGIN
  PERFORM id FROM finder_watches WHERE id=$1 FOR NO KEY UPDATE;
  RETURN QUERY
WITH target AS MATERIALIZED (
  SELECT i.* FROM finder_inbox i LEFT JOIN listings l USING(marketplace,marketplace_item_id)
  WHERE i.watch_id=$1 AND i.marketplace=$2 AND i.marketplace_item_id=$3
    AND ($4='purchase' OR $5::text IS NULL OR i.data->>'evidence_invalidated_at' IS NOT NULL OR NOT EXISTS (
      SELECT 1 FROM finder_discovery_work d
      WHERE COALESCE(d.marketplace,'ebay')=i.marketplace AND d.item_id=i.marketplace_item_id
        AND d.status='pending' AND d.kind='existing_listing_updated' AND d.reason IS NULL
        AND CASE WHEN d.fingerprint=l.data->'source_metadata'->>'finder_summary_fingerprint'
          THEN COALESCE(l.data->'source_metadata'->>'finder_details_invalidated_at',d.last_search_at)::timestamptz
          ELSE GREATEST(d.last_search_at::timestamptz,COALESCE((l.data->'source_metadata'->>'finder_details_invalidated_at')::timestamptz,'-infinity'::timestamptz)) END
          > COALESCE((i.data->>'details_observed_at')::timestamptz,'-infinity'::timestamptz)
    ))
  FOR UPDATE OF i
), saved AS (
  INSERT INTO finder_decisions(watch_id,marketplace,marketplace_item_id,verdict,purchased,tier,decided_at,updated_at,prediction)
  SELECT watch_id,marketplace,marketplace_item_id,
    CASE WHEN $4='identity' THEN $5::text ELSE NULL END,
    CASE WHEN $4='purchase' THEN $6::boolean ELSE false END,
    CASE WHEN $4='identity' AND $5::text IS NOT NULL THEN COALESCE(data->>'status','unknown') END,
    CASE WHEN $4='identity' AND $5::text IS NOT NULL THEN $7::text END,$7,
    CASE WHEN $4='identity' AND $5::text IS NOT NULL THEN json_build_object(
      'source','recorded_prediction','status',data->>'status','policy',data->>'policy','watch_revision',data->'watch_revision','evaluated_at',last_seen_at) END
  FROM target WHERE $4='purchase' OR (
    ($8::text IS NULL OR data->>'status'=$8) AND ($9::text IS NULL OR last_seen_at=$9))
  ON CONFLICT(watch_id,marketplace,marketplace_item_id) DO UPDATE SET
    verdict=CASE WHEN $4='identity' THEN EXCLUDED.verdict ELSE finder_decisions.verdict END,
    purchased=CASE WHEN $4='purchase' THEN EXCLUDED.purchased ELSE finder_decisions.purchased END,
    tier=COALESCE(finder_decisions.tier,EXCLUDED.tier),
    decided_at=COALESCE(finder_decisions.decided_at,EXCLUDED.decided_at),
    prediction=COALESCE(finder_decisions.prediction,EXCLUDED.prediction),updated_at=EXCLUDED.updated_at
  RETURNING *
), mirror AS (
  INSERT INTO finder_verdicts(watch_id,marketplace,marketplace_item_id,verdict,tier,decided_at)
  SELECT watch_id,marketplace,marketplace_item_id,COALESCE(verdict,'bought'),COALESCE(tier,'unknown'),COALESCE(decided_at,updated_at)
  FROM saved WHERE verdict IS NOT NULL OR purchased
  ON CONFLICT(watch_id,marketplace,marketplace_item_id) DO UPDATE SET
    verdict=EXCLUDED.verdict,tier=EXCLUDED.tier,decided_at=EXCLUDED.decided_at
), cleared AS (
  DELETE FROM finder_verdicts v USING saved s WHERE v.watch_id=s.watch_id AND v.marketplace=s.marketplace
    AND v.marketplace_item_id=s.marketplace_item_id AND s.verdict IS NULL AND NOT s.purchased
), suppressed AS (
  DELETE FROM finder_outbox o USING saved s WHERE o.watch_id=s.watch_id AND o.marketplace=s.marketplace
    AND o.marketplace_item_id=s.marketplace_item_id AND o.status='pending' AND (s.verdict IS NOT NULL OR s.purchased)
)
SELECT * FROM saved;
END $$;
