// Whole saved-set totals are independent of the inbox page and its filters.
// Dismissed decisions remain history and are browseable in Judged.
export const decisionSummaryQuery = `SELECT
  count(*) FILTER (WHERE v.verdict IS NOT NULL OR v.purchased)::int AS all,
  count(*) FILTER (WHERE v.verdict IS NOT NULL)::int AS judged,
  count(*) FILTER (WHERE v.verdict='mine')::int AS mine,
  count(*) FILTER (WHERE v.verdict='other')::int AS other,
  count(*) FILTER (WHERE v.verdict='unsure')::int AS unsure,
  count(*) FILTER (WHERE v.purchased)::int AS purchased,
  count(*) FILTER (WHERE v.purchased AND v.verdict IS NULL)::int AS purchase_only,
  count(*) FILTER (WHERE i.dismissed AND v.verdict IS NOT NULL)::int AS dismissed_judged,
  count(*) FILTER (WHERE (v.verdict IS NOT NULL OR v.purchased)
    AND ($1='all' OR v.verdict=$1) AND ($2='all' OR v.purchased))::int AS filtered_total
  FROM finder_decisions v JOIN finder_inbox i USING(watch_id,marketplace,marketplace_item_id)`;
