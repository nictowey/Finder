// Operational metadata only. Provider identities and evidence never enter this ledger.
type DB = { query(sql: string, values?: unknown[]): Promise<{ rows: any[] }> };
const ms = (value: unknown) => typeof value === 'string' ? Date.parse(value) : NaN;
const minutes = (value: number) => Number.isFinite(value) ? Math.max(0, Math.round(value / 6000) / 10) : null;

export function summarizeOperations(watches: any[], attempts: any[], dispatches: any[], since: string | null,
  heartbeat: string | null, devices: number, delivery: any, now = Date.now()) {
  const counts: Record<string, number> = { completed: 0, failed: 0, quota_paused: 0, abandoned: 0, superseded: 0, started: 0 };
  let delay = 0, requests = 0, retries = 0, capped = 0, incomplete = 0, partial = 0;
  for (const row of attempts) {
    const status = row.status === 'started' && ms(row.lease_until) <= now ? 'abandoned' : row.status;
    if (Object.hasOwn(counts, status)) counts[status]++;
    delay = Math.max(delay, ms(row.started_at) - ms(row.due_at));
    requests += row.metrics?.browse_requests || 0;
    retries += row.metrics?.browse_retries || 0;
    capped += row.metrics?.capped_pages || 0;
    partial += row.metrics?.partial_details || 0;
    incomplete += row.metrics?.catalog_check_failed || row.metrics?.catalog_search_incomplete ? 1 : 0;
  }
  const current = watches.filter(w => w.config.enabled).map(w => ({
    id: w.id,
    age_minutes: minutes(now - ms(w.last_success_at)),
    overdue_minutes: minutes(now - ms(w.next_scan_at)),
    lease_expired: w.status === 'scanning' && ms(w.lease_until) <= now,
    last_success_at: w.last_success_at,
    status: w.status,
  }));
  const needsAttention = current.some(w => w.lease_expired || (w.age_minutes ?? Infinity) > 60 ||
    (w.overdue_minutes ?? 0) > 15 || ['failed', 'quota_paused'].includes(w.status));
  const linked = new Set(attempts.map(a => a.dispatch_id).filter(Boolean));
  return {
    measured_since: since, window_days: 14,
    elapsed_days: since ? Math.min(14, Math.max(0, (now - ms(since)) / 86400000)) : 0,
    status: !since ? 'not_measured' : needsAttention ? 'attention_needed' : 'collecting_evidence',
    current, counts, max_start_delay_minutes: minutes(delay),
    latest_quota_remaining: attempts.find(a => Number.isInteger(a.metrics?.quota_remaining))?.metrics.quota_remaining ?? null,
    browse_requests: requests, browse_retries: retries, capped_pages: capped, partial_details: partial,
    catalog_incomplete_scans: incomplete,
    trigger_last_seen_at: heartbeat,
    trigger_stale: current.length > 0 && (!heartbeat || now - ms(heartbeat) > 20 * 60000),
    dispatches: {
      total: dispatches.length,
      accepted: dispatches.filter(d => d.status === 'accepted').length,
      rejected: dispatches.filter(d => d.status === 'rejected').length,
      uncertain: dispatches.filter(d => ['reserved', 'unknown'].includes(d.status)).length,
      correlated: dispatches.filter(d => linked.has(d.id)).length,
      last: dispatches[0] ? { status: dispatches[0].status, at: dispatches[0].started_at, http_status: dispatches[0].http_status } : null,
    },
    notifications: { registered_devices: devices, last_delivery: delivery ?? null },
    recent: attempts.slice(0, 12).map(a => ({ watch_id: a.watch_id, started_at: a.started_at,
      finished_at: a.finished_at, status: a.status === 'started' && ms(a.lease_until) <= now ? 'abandoned' : a.status,
      source: a.source, run_id: a.run_id, start_delay_minutes: minutes(ms(a.started_at) - ms(a.due_at)),
      browse_requests: a.metrics?.browse_requests ?? null })),
  };
}

export async function readOperations(db: DB, watches: any[]) {
  const now = Date.now();
  const cutoff = new Date(now - 14 * 86400000).toISOString();
  const attempts = (await db.query(`SELECT * FROM finder_scan_attempts WHERE started_at >= $1 ORDER BY started_at DESC LIMIT 10001`, [cutoff])).rows;
  const dispatches = (await db.query(`SELECT * FROM finder_dispatch_attempts WHERE started_at >= $1 ORDER BY started_at DESC LIMIT 10001`, [cutoff])).rows;
  const config = (await db.query(`SELECT key,data FROM finder_private_settings WHERE key IN ('operations_since','scan_trigger_health','notification_health')`)).rows;
  const settings = Object.fromEntries(config.map(r => [r.key, r.data]));
  const devices = Number((await db.query('SELECT COUNT(*) AS count FROM finder_push_subscriptions')).rows[0]?.count ?? 0);
  return { ...summarizeOperations(watches, attempts, dispatches, settings.operations_since?.at ?? null,
    settings.scan_trigger_health?.at ?? null, devices, settings.notification_health, now),
    history_truncated: attempts.length > 10000 || dispatches.length > 10000 };
}
