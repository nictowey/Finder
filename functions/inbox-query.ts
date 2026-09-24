// Apply each watch's current ceiling before keyset pagination. Auctions compare the
// current bid. Discovery and notification eligibility remain independent of this view.
export const inboxQuery = `SELECT i.watch_id,i.marketplace,i.marketplace_item_id,i.data,i.first_seen_at,i.last_seen_at,i.dismissed,l.data AS listing,w.revision
  FROM finder_inbox i JOIN listings l USING(marketplace,marketplace_item_id)
  JOIN finder_watches w ON w.id=i.watch_id
  WHERE ($1='dismissed' AND i.dismissed OR $1='unavailable' AND NOT i.dismissed AND i.data->>'availability'='unavailable_on_recheck'
    OR $1 NOT IN ('dismissed','unavailable') AND NOT i.dismissed AND i.data->>'availability' IS DISTINCT FROM 'unavailable_on_recheck' AND i.data->>'status'=$1)
  AND ($2::text IS NULL OR (i.first_seen_at,i.watch_id,i.marketplace_item_id)<($2,$3,$4))
  AND ($5='all' OR w.config->>'maximum_subtotal' IS NULL OR CASE WHEN
    w.config->>'maximum_subtotal' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'current_price' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'shipping_cost' ~ '^[0-9]+([.][0-9]+)?$'
    AND l.data->>'currency'=w.config->>'currency'
    AND l.data->>'shipping_currency'=w.config->>'currency'
    AND l.data->>'price_kind' IN ('fixed_price','current_bid')
    AND l.data->>'details_observed_at' >= $6
    AND i.data->>'watch_revision'=w.revision::text
    AND l.data->'source_metadata'->>'delivery_country'=w.config->>'country'
    AND l.data->'source_metadata'->>'delivery_postal_code'=w.config->>'postal_code'
    AND NOT COALESCE((l.data->'quality_flags')::jsonb ?| ARRAY['details_unavailable','item_specifics_stale','details_not_requested'],false)
    THEN (l.data->>'current_price')::numeric+(l.data->>'shipping_cost')::numeric <= (w.config->>'maximum_subtotal')::numeric
    ELSE false END)
  ORDER BY i.first_seen_at DESC,i.watch_id DESC,i.marketplace_item_id DESC LIMIT 51`;
