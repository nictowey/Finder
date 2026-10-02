// Apply each watch's current ceiling before keyset pagination. Auctions compare the
// current bid. Discovery and notification eligibility remain independent of this view.
import { pendingEvidenceChange } from './pending-evidence.mjs';
const inboxSelection = `SELECT i.watch_id,i.marketplace,i.marketplace_item_id,i.data,i.first_seen_at,i.last_seen_at,i.dismissed,l.data AS listing,w.revision,evidence.pending AS pending_evidence_change
  FROM finder_inbox i JOIN listings l USING(marketplace,marketplace_item_id)
  JOIN finder_watches w ON w.id=i.watch_id
  LEFT JOIN finder_decisions v ON v.watch_id=i.watch_id AND v.marketplace=i.marketplace AND v.marketplace_item_id=i.marketplace_item_id
  CROSS JOIN LATERAL (SELECT ${pendingEvidenceChange} AS pending) evidence
  WHERE ($1='dismissed' AND i.dismissed OR $1='judged' AND (v.verdict IS NOT NULL OR v.purchased)
    OR $1='unavailable' AND NOT i.dismissed AND i.data->>'availability'='unavailable_on_recheck'
    OR $1 NOT IN ('dismissed','unavailable') AND NOT i.dismissed AND i.data->>'availability' IS DISTINCT FROM 'unavailable_on_recheck'
      AND ($1='review' AND v.verdict IS NULL AND NOT COALESCE(v.purchased,false) AND i.data->>'status' IN ('possible_pressing','family_review') OR i.data->>'status'=$1))
  AND ($1<>'judged' OR (($7='all' OR v.verdict=$7) AND ($8='all' OR v.purchased)))
  AND ($1='judged' OR $5='all' OR w.config->>'maximum_subtotal' IS NULL OR CASE WHEN
    w.config->>'maximum_subtotal' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'current_price' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'shipping_cost' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'currency'=w.config->>'currency'
    AND l.data->>'shipping_currency'=w.config->>'currency'
    AND l.data->>'price_kind' IN ('fixed_price','current_bid')
    AND l.data->>'details_observed_at' >= $6
    AND i.data->>'evidence_invalidated_at' IS NULL
    AND NOT evidence.pending
    AND i.data->>'watch_revision'=w.revision::text
    AND l.data->'source_metadata'->>'delivery_country'=w.config->>'country'
    AND l.data->'source_metadata'->>'delivery_postal_code'=w.config->>'postal_code'
    AND NOT COALESCE((l.data->'quality_flags')::jsonb ?| ARRAY['details_unavailable','item_specifics_stale','details_not_requested'],false)
    THEN (l.data->>'current_price')::numeric+(l.data->>'shipping_cost')::numeric <= (w.config->>'maximum_subtotal')::numeric
    ELSE false END)
`;

export const inboxQuery = `${inboxSelection}
  AND ($2::text IS NULL OR (i.first_seen_at,i.watch_id,i.marketplace_item_id)<($2,$3,$4))
  ORDER BY i.first_seen_at DESC,i.watch_id DESC,i.marketplace_item_id DESC LIMIT 51`;

// These are persisted review meanings, not a second seller-text matcher. Owner
// sign prefixes come from Clue.describe(); unknown/legacy shapes do not qualify.
const colorSupported = `jsonb_typeof(data->'verify')='array'
  AND NOT (data->'verify' ?| ARRAY['color_pair_incomplete','color_claim_ambiguous',
    'color_conflict','required_sign_conflict','color_not_comparable'])`;
// Normalized persisted timestamps are UTC ISO strings. Guard casts with that
// bounded shape and real calendar dates; malformed/unsupported values stay null.
// This works on older PostgreSQL versions without pg_input_is_valid (PG16+).
const timestamp = (field: string, withinHour=true) => {
  const value=`(${field})`, shape=`${value} ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{1,6})?(Z|[+]00:00)$'`;
  if (withinHour) return `CASE WHEN ${shape} AND left(${value},10) IN (left($9,10),left($10,10)) THEN ${value}::timestamptz END`;
  return `CASE WHEN ${shape} AND left(${value},4)<>'0000' THEN CASE WHEN substring(${value},9,2)::int <=
    EXTRACT(day FROM make_date(substring(${value},1,4)::int,substring(${value},6,2)::int,1)+interval '1 month - 1 day')
    THEN ${value}::timestamptz END END`;
};

// Opt-in only: retain current freshness on every request. The scope generation
// detects rank/membership changes before accepting a keyset cursor, including
// evidence aging between pages. Both values come from one statement snapshot.
// The LEFT JOIN preserves the generation even when the requested page is empty.
export const pressingCluesInboxQuery = `WITH eligible AS (${inboxSelection}),
  recorded AS (SELECT eligible.*, eligible.data::jsonb AS review_data,
    ${timestamp("listing->>'details_observed_at'")} AS listing_details_at,
    ${timestamp("data->>'details_observed_at'")} AS review_details_at FROM eligible),
  supported AS (SELECT recorded.*, CASE WHEN
    listing_details_at BETWEEN $9::timestamptz AND $10::timestamptz
    AND review_details_at=listing_details_at
    AND review_data->'watch_revision'=to_jsonb(revision)
    AND review_data->>'status' IN ('possible_pressing','family_review')
    AND review_data->>'availability' IS DISTINCT FROM 'unavailable_on_recheck'
    AND (listing->>'listing_ends_at' IS NULL OR (${timestamp("listing->>'listing_ends_at'",false)})>$10::timestamptz)
    AND review_data->>'evidence_invalidated_at' IS NULL AND NOT pending_evidence_change
    AND jsonb_typeof((listing->'quality_flags')::jsonb)='array'
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof((listing->'quality_flags')::jsonb)='array'
      THEN (listing->'quality_flags')::jsonb ELSE '[]'::jsonb END) flag WHERE jsonb_typeof(flag)<>'string')
    AND NOT ((listing->'quality_flags')::jsonb ?| ARRAY['details_unavailable','item_specifics_stale','details_not_requested'])
    THEN ARRAY(
      SELECT reason FROM (
        SELECT 1 AS position,'Matching identifier claim' AS reason
          WHERE review_data->'clues' @> '["seller_identifier_claim"]'::jsonb
        UNION ALL SELECT 2,'Seller claims a numbered copy'
          WHERE review_data->'clues' @> '["seller_numbered_claim"]'::jsonb
        UNION ALL SELECT 3,'Comparable vinyl color claim'
          WHERE review_data->'clues' @> '["seller_color_claim"]'::jsonb
            AND ${colorSupported.replaceAll('data->', 'review_data->')}
        UNION ALL SELECT 4,'Saved sign: ' || (sign.value #>> '{}')
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(review_data->'signs')='array'
            THEN review_data->'signs' ELSE '[]'::jsonb END) sign(value)
          WHERE jsonb_typeof(sign.value)='string' AND (
            sign.value #>> '{}' = 'numbered'
            OR sign.value #>> '{}' ~ '^(keyword|catalog_number|barcode|label|country): [^[:space:]]'
            OR sign.value #>> '{}' ~ '^color: [^[:space:]]'
              AND ${colorSupported.replaceAll('data->', 'review_data->')})
      ) reasons ORDER BY position,reason
    ) ELSE ARRAY[]::text[] END AS pressing_clues FROM recorded),
  ranked AS (SELECT supported.*, CASE WHEN cardinality(pressing_clues)>0 THEN 1 ELSE 0 END AS pressing_rank FROM supported),
  generation AS (SELECT md5(COALESCE(string_agg(
    jsonb_build_array(pressing_rank,first_seen_at,watch_id,marketplace,marketplace_item_id)::text,
    ',' ORDER BY pressing_rank DESC,first_seen_at DESC,watch_id DESC,marketplace DESC,marketplace_item_id DESC),'')) AS order_generation FROM ranked)
  SELECT page.watch_id,page.marketplace,page.marketplace_item_id,page.data,page.first_seen_at,page.last_seen_at,
    page.dismissed,page.listing,page.revision,page.pending_evidence_change,page.pressing_clues,page.pressing_rank,generation.order_generation
  FROM generation LEFT JOIN LATERAL (
    SELECT * FROM ranked WHERE $2::text IS NULL OR
      (pressing_rank,first_seen_at,watch_id,marketplace,marketplace_item_id)<($11::int,$2,$3,$12,$4)
    ORDER BY pressing_rank DESC,first_seen_at DESC,watch_id DESC,marketplace DESC,marketplace_item_id DESC LIMIT 51
  ) page ON true`;
