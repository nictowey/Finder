// Aggregate the same bounded history on PostgreSQL. Only the recent view and
// exceptional scalar values need to cross the database connection.
const integer = (value: string) => `(${value})::text ~ '^-?[0-9]{1,15}$'`;
const truth = (value: string) => `CASE
  WHEN ${value} IS NULL OR json_typeof(${value}) = 'null' THEN false
  WHEN json_typeof(${value}) = 'boolean' THEN (${value})::text = 'true'
  WHEN json_typeof(${value}) = 'string' THEN (${value})::text <> '""'
  WHEN json_typeof(${value}) IN ('array','object') THEN true
  WHEN ${integer(value)} THEN (${value})::text::bigint <> 0
  ELSE NULL END`;

// Date.parse is retained for noncanonical text. Calendar arithmetic deliberately
// normalizes day 29..31 like JS, truncates fractions to milliseconds, and avoids
// casting unchecked text to a timestamp (including on pre-PG16 servers).
const millis = (column: string) => `CASE WHEN ${column} ~
  '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\\.[0-9]+)?(Z|\\+00:00)$'
  AND left(${column},4) <> '0000' THEN
  extract(epoch FROM (make_timestamp(substring(${column},1,4)::int,
    substring(${column},6,2)::int,1,substring(${column},12,2)::int,
    substring(${column},15,2)::int,substring(${column},18,2)::int)
    + (substring(${column},9,2)::int - 1) * interval '1 day')) * 1000
  + coalesce(nullif(rpad(left(substring(${column} FROM '\\.([0-9]+)'),3),3,'0'),''),'0')::int
  ELSE NULL END`;

// PostgreSQL JSON extraction rejects escaped NUL and unpaired surrogates, even
// in ignored properties. Keep those rare documents as text for the JS decoder.
const unicodeEscape = String.raw`'\\u(0000|[dD][89a-fA-F])'`;
const fields = ['browse_requests', 'browse_retries', 'capped_pages', 'partial_details'];
const metrics = fields.map(field => `(
  SELECT json_build_object('sum',coalesce(sum(n),0),'fallback',
    CASE WHEN count(*) FILTER (WHERE n IS NULL) > 0 OR sum(abs(n)) > 9007199254740991
      OR EXISTS (SELECT 1 FROM attempts WHERE exceptional_metrics IS NOT NULL)
      THEN json_agg(value ORDER BY seq) ELSE NULL END)
  FROM (SELECT seq, value, CASE
    WHEN value IS NULL OR json_typeof(value) = 'null' OR value::text IN ('false','""') THEN 0
    WHEN value::text = 'true' THEN 1
    WHEN ${integer('value')} THEN value::text::bigint ELSE NULL END AS n
    FROM (SELECT seq,metrics->'${field}' AS value FROM attempts) projected) numbers
) AS ${field}`).join(',\n');

export const operationsHistoryQuery = `WITH
bounded_attempts AS (
  SELECT watch_id,started_at,due_at,lease_until,finished_at,status,source,run_id,dispatch_id,
    CASE WHEN metrics::text ~ ${unicodeEscape} THEN '{}'::json ELSE metrics END AS metrics,
    CASE WHEN metrics::text ~ ${unicodeEscape} THEN metrics::text ELSE NULL END AS exceptional_metrics
  FROM finder_scan_attempts WHERE started_at >= $1 ORDER BY started_at DESC LIMIT 10001
),
attempts AS (
  SELECT *, row_number() OVER (ORDER BY started_at DESC) AS seq,
    ${millis('started_at')} AS started_ms, ${millis('due_at')} AS due_ms,
    ${millis('lease_until')} AS lease_ms,
    ${truth("metrics->'catalog_check_failed'")} AS catalog_failed,
    ${truth("metrics->'catalog_search_incomplete'")} AS catalog_incomplete
  FROM bounded_attempts
),
dispatches AS (
  SELECT id,status,started_at,http_status FROM finder_dispatch_attempts
  WHERE started_at >= $1 ORDER BY started_at DESC LIMIT 10001
),
linked AS (
  SELECT DISTINCT dispatch_id FROM attempts WHERE dispatch_id <> ''
),
correlated_dispatches AS (
  SELECT d.*, l.dispatch_id AS linked_id FROM dispatches d LEFT JOIN linked l ON l.dispatch_id=d.id
),
statuses AS (
  SELECT CASE WHEN status = 'started' AND lease_ms <= $2 THEN 'abandoned' ELSE status END AS status
  FROM attempts
)
SELECT
  (SELECT count(*) FROM attempts) AS attempt_count,
  (SELECT json_agg(json_build_object('seq',seq,'metrics',exceptional_metrics))
    FROM attempts WHERE exceptional_metrics IS NOT NULL) AS metrics_fallback,
  (SELECT json_object_agg(status,n) FROM (SELECT status,count(*) AS n FROM statuses
    WHERE status IN ('completed','failed','quota_paused','abandoned','superseded','started')
    GROUP BY status) counted) AS counts,
  (SELECT max(started_ms-due_ms) FROM attempts) AS delay_ms,
  (SELECT json_agg(json_build_array(started_at,due_at)) FROM attempts
    WHERE started_ms IS NULL OR due_ms IS NULL) AS delay_fallback,
  (SELECT json_agg(lease_until) FROM attempts WHERE status='started' AND lease_ms IS NULL) AS lease_fallback,
  ${metrics},
  (SELECT count(*) FROM attempts WHERE catalog_failed OR catalog_incomplete) AS catalog_incomplete_scans,
  (SELECT json_agg(json_build_array(metrics->'catalog_check_failed',metrics->'catalog_search_incomplete'))
    FROM attempts WHERE (catalog_failed OR catalog_incomplete) IS NULL) AS catalog_fallback,
  (SELECT json_agg(json_build_object('seq',seq,'value',metrics->'quota_remaining') ORDER BY seq) FROM attempts
    WHERE json_typeof(metrics->'quota_remaining')='number'
    AND seq <= coalesce((SELECT min(seq) FROM attempts
      WHERE ${integer("(metrics->'quota_remaining')")}),10001)) AS quota_candidates,
  (SELECT json_agg(json_build_object('seq',seq,'watch_id',watch_id,'started_at',started_at,'due_at',due_at,
    'lease_until',lease_until,'finished_at',finished_at,'status',status,'source',source,'run_id',run_id,
    'metrics',json_build_object('browse_requests',metrics->'browse_requests')) ORDER BY seq)
    FROM attempts WHERE seq <= 12) AS recent,
  (SELECT json_build_object('total',count(*),'accepted',count(*) FILTER (WHERE status='accepted'),
    'rejected',count(*) FILTER (WHERE status='rejected'),
    'uncertain',count(*) FILTER (WHERE status IN ('reserved','unknown')),
    'correlated',count(*) FILTER (WHERE linked_id IS NOT NULL),
    'last',(SELECT json_build_object('status',status,'at',started_at,'http_status',http_status)
      FROM dispatches ORDER BY started_at DESC LIMIT 1)) FROM correlated_dispatches) AS dispatches`;
