import { randomUUID } from "node:crypto";
import { Pool } from "pg";

type DB = { query: (sql: string, values?: unknown[]) => Promise<{ rows: unknown[] }> };
type Deps = { db: DB; token: string; fetch: typeof fetch; now: () => Date };
let pool: Pool | undefined;

const workflow = "https://api.github.com/repos/nictowey/Finder/actions/workflows/watchlist.yml/dispatches";

function scheduledNow(value: unknown, now: Date): boolean {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:00(?:\.\d+)?(?:Z|\+00:00)$/.test(value)) return false;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) && date.getUTCMinutes() % 10 === 5 &&
    Math.abs(now.getTime() - date.getTime()) <= 180_000;
}

export function createHandler({ db, token, fetch: send, now }: Deps) {
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST" || new URL(request.url).pathname !== "/") return new Response(null, { status: 404 });
    // This header is not authentication. Timestamp validation, due checks and the
    // atomic cooldown bound forged invocations; no data or credentials are returned.
    if (!request.headers.get("x-neon-trigger-invocation-id")) return new Response(null, { status: 403 });
    let data: any;
    try { data = await request.json(); } catch { return new Response(null, { status: 400 }); }
    if (data?.trigger?.type !== "schedule" || data?.trigger?.name !== "scan-catchup" ||
      !scheduledNow(data?.data?.scheduled_at, now())) return new Response(null, { status: 403 });
    if (!token) return Response.json({ status: "dispatch_not_configured" }, { status: 503 });
    let reservedId: string | undefined;
    try {
      await db.query(`INSERT INTO finder_private_settings(key,data) VALUES('scan_trigger_health',$1)
        ON CONFLICT(key) DO UPDATE SET data=EXCLUDED.data`,
        [{ at: now().toISOString(), status: "checking" }]);
      const id = randomUUID();
      // Reservation and history insert are atomic. A crash remains 'reserved', not accepted.
      const reserved = await db.query(`WITH reservation AS (
        INSERT INTO finder_private_settings(key,data)
        SELECT 'scan_dispatch', json_build_object('dispatched_at', NOW())
        WHERE EXISTS (SELECT 1 FROM finder_watches WHERE enabled
          AND next_scan_at::timestamptz <= NOW()
          AND (lease_until IS NULL OR lease_until::timestamptz < NOW()))
        ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data
        WHERE (finder_private_settings.data->>'dispatched_at')::timestamptz < NOW() - INTERVAL '8 minutes'
        RETURNING key)
        INSERT INTO finder_dispatch_attempts(id,scheduled_at,started_at,status)
        SELECT $1,$2,$3,'reserved' FROM reservation RETURNING id`,
        [id, new Date(data.data.scheduled_at).toISOString(), now().toISOString()]);
      if (!reserved.rows.length) return Response.json({ status: "no_scan_due_or_cooldown" });
      reservedId = id;
      const result = await send(workflow, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
          "User-Agent": "Finder-scan-trigger",
        },
        body: JSON.stringify({ ref: "main", inputs: { dispatch_id: id } }),
        signal: AbortSignal.timeout(10_000), redirect: "error",
      });
      const accepted = result.status === 204;
      await db.query(`UPDATE finder_dispatch_attempts SET status=$2,http_status=$3,finished_at=$4 WHERE id=$1`,
        [id, accepted ? "accepted" : "rejected", result.status, now().toISOString()]);
      return Response.json({ status: accepted ? "dispatched" : "dispatch_unavailable" },
        { status: accepted ? 200 : 503 });
    } catch {
      if (reservedId) {
        try { await db.query("UPDATE finder_dispatch_attempts SET status='unknown',finished_at=$2 WHERE id=$1 AND status='reserved'",
          [reservedId, now().toISOString()]); } catch { /* An unfinalized reservation remains visible. */ }
      }
      return Response.json({ status: "dispatch_unavailable" }, { status: 503 });
    }
  };
}

export default async function handler(request: Request): Promise<Response> {
  try {
    pool ??= new Pool({ connectionString: process.env.DATABASE_URL, max: 2, connectionTimeoutMillis: 5000 });
    return await createHandler({ db: pool, token: process.env.FINDER_GITHUB_DISPATCH_TOKEN ?? "", fetch, now: () => new Date() })(request);
  } catch { return Response.json({ status: "dispatch_unavailable" }, { status: 503 }); }
}
