import { Pool } from "pg";

type DB = { query: (sql: string) => Promise<{ rows: unknown[] }> };
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
    // The header identifies a Neon trigger, but is not an authentication secret.
    // A fresh cron timestamp plus an atomic database cooldown bounds forged requests.
    if (!request.headers.get("x-neon-trigger-invocation-id")) return new Response(null, { status: 403 });
    let data: any;
    try { data = await request.json(); } catch { return new Response(null, { status: 400 }); }
    if (data?.trigger?.type !== "schedule" || data?.trigger?.name !== "scan-catchup" ||
      !scheduledNow(data?.data?.scheduled_at, now())) return new Response(null, { status: 403 });
    if (!token) return Response.json({ status: "dispatch_not_configured" }, { status: 503 });
    try {
      // A reservation is made only when an enabled watch is due and has no live lease.
      // The existing Python worker remains the sole collector and owns watch leases.
      const reserved = await db.query(`INSERT INTO finder_private_settings(key,data)
        SELECT 'scan_dispatch', json_build_object('dispatched_at', NOW())
        WHERE EXISTS (SELECT 1 FROM finder_watches WHERE enabled
          AND next_scan_at::timestamptz <= NOW()
          AND (lease_until IS NULL OR lease_until::timestamptz < NOW()))
        ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data
        WHERE (finder_private_settings.data->>'dispatched_at')::timestamptz < NOW() - INTERVAL '8 minutes'
        RETURNING key`);
      if (!reserved.rows.length) return Response.json({ status: "no_scan_due" });
      const result = await send(workflow, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "Finder-scan-trigger",
        },
        body: JSON.stringify({ ref: "main" }),
        signal: AbortSignal.timeout(10_000),
        redirect: "error",
      });
      return Response.json({ status: result.status === 204 ? "dispatched" : "dispatch_unavailable" },
        { status: result.status === 204 ? 200 : 503 });
    } catch {
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
