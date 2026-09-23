import { createHash, randomUUID } from "node:crypto";
import { Pool } from "pg";
import { readOperations } from "./operations.js";
import webpush from "web-push";
import { html, javascript, serviceWorker, stylesheet } from "./watchlist-ui.js";

type DB = { query: (sql: string, values?: unknown[]) => Promise<{ rows: any[] }> };
type Deps = { db: DB; authURL: string; origin: string; fetch: typeof fetch; sendPush?: typeof webpush.sendNotification };
let pool: Pool | undefined;
class InputError extends Error {}

function reply(data: unknown, status = 200, type = "application/json"): Response {
  return new Response(type === "application/json" ? JSON.stringify(data) : String(data), {
    status, headers: { "Content-Type": type, "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" },
  });
}

async function body(request: Request): Promise<any> {
  const reader = request.body?.getReader();
  if (!reader) throw new InputError("body");
  let size = 0;
  const chunks: Uint8Array[] = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 16_384) { await reader.cancel(); throw new InputError("size"); }
    chunks.push(value);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString()); } catch { throw new InputError("json"); }
}

export function validateWatch(value: any) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new InputError("watch");
  const release = String(value.release_id ?? "").trim();
  const match = release.match(/^(?:https:\/\/(?:www\.)?discogs\.com\/(?:[a-z]{2}\/)?release\/)?([1-9]\d*)(?:-[^?#]*)?(?:[?#].*)?$/);
  if (!match || !Number.isSafeInteger(Number(match[1]))) throw new InputError("release");
  const ceiling = value.maximum_subtotal === "" || value.maximum_subtotal == null ? null : String(value.maximum_subtotal);
  if (ceiling !== null && (!/^\d{1,7}(?:\.\d{1,2})?$/.test(ceiling) || Number(ceiling) <= 0)) throw new InputError("ceiling");
  if (!/^[A-Z]{3}$/.test(value.currency ?? "USD")) throw new InputError("currency");
  const country = value.country || null, postal = value.postal_code || null;
  if (Boolean(country) !== Boolean(postal) || (country && !/^[A-Z]{2}$/.test(country)) ||
    (postal && !/^[A-Za-z0-9 -]{1,16}$/.test(postal))) throw new InputError("destination");
  const conditions = value.condition_ids ?? [];
  if (!Array.isArray(conditions) || conditions.length > 20 || conditions.some((v: any) => typeof v !== "string" || !/^\d{1,8}$/.test(v))) throw new InputError("conditions");
  if (value.alert_mode !== undefined && !['review_leads','strict'].includes(value.alert_mode)) throw new InputError("alert mode");
  if (value.enabled !== undefined && typeof value.enabled !== "boolean") throw new InputError("enabled");
  if (typeof (value.label ?? "") !== "string" || (value.label ?? "").length > 120) throw new InputError("label");
  return { release_id: Number(match[1]), label: (value.label ?? "").trim(), maximum_subtotal: ceiling,
    currency: value.currency ?? "USD", condition_ids: conditions, country, postal_code: postal, enabled: value.enabled ?? true, alert_mode: value.alert_mode ?? "review_leads" };
}

export function validPush(value: any): boolean {
  try {
    const url = new URL(value.endpoint);
    // Prevent a subscription endpoint from becoming a server-side request forgery.
    return url.protocol === "https:" && !url.username && !url.password && !url.port &&
      ["fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"].includes(url.hostname) &&
      url.href.length < 4096 && /^[A-Za-z0-9_-]{80,100}$/.test(value.keys.p256dh) &&
      /^[A-Za-z0-9_-]{20,30}$/.test(value.keys.auth);
  } catch { return false; }
}

export function createHandler(deps: Deps) {
  return async (request: Request): Promise<Response> => {
    try {
      const url = new URL(request.url), path = url.pathname;
      if (request.method === "GET") {
        if (path === "/") return reply(html, 200, "text/html; charset=utf-8");
        if (path === "/app.js") return reply(javascript, 200, "text/javascript");
        if (path === "/app.css") return reply(stylesheet, 200, "text/css");
        if (path === "/sw.js") return reply(serviceWorker, 200, "text/javascript");
        if (path === "/manifest.webmanifest") return reply({ name: "Finder", short_name: "Finder", id: "/", start_url: "/", scope: "/", display: "standalone", background_color: "#f6f5f0", theme_color: "#173f35", icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }] }, 200);
        if (path === "/icon.svg") return reply('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#173f35"/><circle cx="96" cy="96" r="62" fill="#f6f5f0"/><circle cx="96" cy="96" r="45" fill="#173f35"/><circle cx="96" cy="96" r="15" fill="#d4b771"/></svg>',200,"image/svg+xml");
        if (path === "/health") return reply({ status: "ok" });
      }
      if (!["GET", "POST", "PUT", "DELETE"].includes(request.method)) return reply({ error: "Method not allowed" }, 405);
      if (request.method !== "GET" && (request.headers.get("Origin") !== deps.origin ||
          !request.headers.get("Content-Type")?.startsWith("application/json"))) return reply({ error: "Invalid request origin" }, 403);
      const owner = (await deps.db.query("SELECT data FROM finder_private_settings WHERE key='owner_email'")).rows[0]?.data?.email;
      if (!owner || !deps.authURL) return reply({ error: "Private access is awaiting owner setup." }, 503);
      const authHeaders = { "Content-Type": "application/json", Origin: deps.origin,
        Cookie: request.headers.get("Cookie") ?? "" };
      if (request.method === "POST" && (path === "/auth/send-code" || path === "/auth/verify-code")) {
        const value = await body(request);
        if (typeof value.email !== "string" || value.email.trim().toLowerCase() !== owner.toLowerCase())
          return reply({ error: "Use the email approved for this private dashboard." }, 403);
        const verify = path === "/auth/verify-code";
        if (verify && (typeof value.otp !== "string" || !/^\d{6,8}$/.test(value.otp))) return reply({ error: "Enter the verification code." }, 400);
        const result = await deps.fetch(deps.authURL + (verify ? "/sign-in/email-otp" : "/email-otp/send-verification-otp"), {
          method: "POST", headers: authHeaders, body: JSON.stringify(verify ? { email: owner, otp: value.otp } : { email: owner, type: "sign-in" }),
          signal: AbortSignal.timeout(10_000), redirect: "error",
        });
        if (!result.ok) return reply({ error: "Sign-in failed. Check the code or request a new one." }, result.status === 429 ? 429 : 400);
        const response = reply({ ok: true });
        for (const cookie of result.headers.getSetCookie()) {
          response.headers.append("Set-Cookie", cookie.replace(/;\s*Domain=[^;]+/gi, "").replace(/;\s*SameSite=[^;]+/gi, "; SameSite=Strict"));
        }
        return response;
      }
      const sessionResponse = await deps.fetch(deps.authURL + "/get-session", {
        headers: authHeaders, signal: AbortSignal.timeout(10_000), redirect: "error",
      });
      const session = sessionResponse.ok ? await sessionResponse.json() : null;
      if (!session?.session || !session?.user?.emailVerified || session.user.email.toLowerCase() !== owner.toLowerCase() ||
          !(Date.parse(session.session.expiresAt) > Date.now())) return reply({ error: "Sign in to view your watchlist." }, 401);
      if (path === "/auth/sign-out" && request.method === "POST") {
        const result = await deps.fetch(deps.authURL + "/sign-out", { method: "POST", headers: authHeaders, body: "{}", signal: AbortSignal.timeout(10_000) });
        const response = reply({ ok: result.ok });
        for (const cookie of result.headers.getSetCookie()) response.headers.append("Set-Cookie", cookie.replace(/;\s*Domain=[^;]+/gi, ""));
        return response;
      }
      if (path === "/api/dashboard" && request.method === "GET") {
        const watches = (await deps.db.query("SELECT id,config,revision,status,last_started_at,last_success_at,next_scan_at,lease_until,summary,catalog,catalog_observed_at FROM finder_watches ORDER BY id")).rows;
        const cutoff = Date.now() - 6 * 3600_000;
        for (const watch of watches) if (Date.parse(watch.catalog_observed_at ?? "") < cutoff || !watch.catalog_observed_at) watch.catalog = null;
        const leads = (await deps.db.query(`SELECT i.watch_id,i.marketplace,i.marketplace_item_id,i.data,i.first_seen_at,i.last_seen_at,i.dismissed,l.data AS listing
          FROM finder_inbox i JOIN listings l USING(marketplace,marketplace_item_id)
          JOIN finder_watches w ON w.id=i.watch_id
          WHERE i.last_seen_at >= $1 AND (i.data->>'watch_revision')::integer=w.revision ORDER BY i.last_seen_at DESC LIMIT 300`, [new Date(cutoff).toISOString()])).rows;
        // Only expose the fields the UI uses; no seller IDs, buyer postal data, opaque source payloads or auth information.
        for (const row of leads) {
          const l = row.listing;
          row.listing = { title: l.title, listing_url: l.listing_url, condition: l.condition, condition_id: l.condition_id,
            current_price: l.current_price, shipping_cost: l.shipping_cost, currency: l.currency,
            price_kind: l.price_kind, listing_ends_at: l.listing_ends_at, item_specifics: l.item_specifics };
        }
        const push = (await deps.db.query("SELECT data FROM finder_private_settings WHERE key='vapid'")).rows[0]?.data;
        const operations = await readOperations(deps.db, watches);
        return reply({ watches, leads, operations, push_key: push?.publicKey ?? null, email: owner, now: new Date().toISOString() });
      }
      if (path === "/api/watches" && request.method === "POST") {
        const watch = validateWatch(await body(request));
        // Serialize limit checks, including concurrent requests.
        const id = randomUUID();
        const result = await deps.db.query(`INSERT INTO finder_watches(id,slot,config,revision,enabled,next_scan_at,status)
          SELECT $1,n,$2,1,$3,$4,'pending' FROM generate_series(1,3) n
          WHERE n NOT IN (SELECT slot FROM finder_watches) ORDER BY n LIMIT 1
          ON CONFLICT DO NOTHING RETURNING id`, [id, watch, watch.enabled, new Date().toISOString()]);
        return result.rows.length ? reply({ id }, 201) : reply({ error: "This pilot supports three watches to stay within the scan budget." }, 409);
      }
      const watchPath = path.match(/^\/api\/watches\/([a-zA-Z0-9-]{1,36})$/);
      if (watchPath && request.method === "PUT") {
        const watch = validateWatch(await body(request));
        const original = (await deps.db.query("SELECT config FROM finder_watches WHERE id=$1", [watchPath[1]])).rows[0];
        if (!original) return reply({ error: "Watch not found" }, 404);
        if (original.config.release_id !== watch.release_id) return reply({ error: "Create a new watch to change the target pressing." }, 400);
        const result = await deps.db.query(`UPDATE finder_watches SET config=$2,enabled=$3,revision=revision+1,
          next_scan_at=$4,lease_token=NULL,lease_until=NULL,status='pending' WHERE id=$1 RETURNING id`,
          [watchPath[1], watch, watch.enabled, new Date().toISOString()]);
        // Old evidence is invalid after edits. Worker will re-evaluate at the next scan.
        await deps.db.query("DELETE FROM finder_outbox WHERE watch_id=$1 AND status='pending'", [watchPath[1]]);
        await deps.db.query("UPDATE finder_inbox SET data=jsonb_set(data::jsonb,'{notify}','false')::json WHERE watch_id=$1", [watchPath[1]]);
        return reply({ ok: result.rows.length > 0 });
      }
      if (watchPath && request.method === "DELETE") {
        await deps.db.query("DELETE FROM finder_watches WHERE id=$1", [watchPath[1]]);
        return reply({ ok: true });
      }
      if (path === "/api/dismiss" && request.method === "POST") {
        const value = await body(request);
        if (typeof value.dismissed !== "boolean") return reply({ error: "Invalid review state" }, 400);
        await deps.db.query("UPDATE finder_inbox SET dismissed=$4 WHERE watch_id=$1 AND marketplace=$2 AND marketplace_item_id=$3",
          [value.watch_id, value.marketplace, value.marketplace_item_id, value.dismissed]);
        return reply({ ok: true });
      }
      if (path === "/api/push/test" && request.method === "POST") {
        const value = await body(request);
        if (typeof value.endpoint !== "string" || value.endpoint.length > 4096) return reply({ error: "Enable notifications on this device first." }, 400);
        const id = createHash("sha256").update(value.endpoint).digest("hex");
        const subscription = (await deps.db.query("SELECT data FROM finder_push_subscriptions WHERE id=$1", [id])).rows[0]?.data;
        const key = (await deps.db.query("SELECT data FROM finder_private_settings WHERE key='vapid'")).rows[0]?.data;
        if (!subscription || !key || !validPush(subscription)) return reply({ error: "Enable notifications on this device first." }, 409);
        const reserved = await deps.db.query(`INSERT INTO finder_private_settings(key,data) VALUES($1,json_build_object('at',NOW()))
          ON CONFLICT(key) DO UPDATE SET data=EXCLUDED.data
          WHERE (finder_private_settings.data->>'at')::timestamptz < NOW()-INTERVAL '1 minute' RETURNING key`, ['push_test_' + id.slice(0,48)]);
        if (!reserved.rows.length) return reply({ error: "Wait one minute before another test." }, 429);
        try {
          await (deps.sendPush ?? webpush.sendNotification.bind(webpush))(subscription, JSON.stringify({ kind: "test" }), {
            TTL: 300, timeout: 10000, topic: "finder-test", vapidDetails: { subject: deps.origin, publicKey: key.publicKey, privateKey: key.privateKey },
          });
          return reply({ accepted: true, message: "The push service accepted the test. Check this device for the Finder test notification; acceptance alone does not confirm display." });
        } catch (error: any) {
          if ([404,410].includes(error.statusCode)) await deps.db.query("DELETE FROM finder_push_subscriptions WHERE id=$1", [id]);
          return reply({ error: "Test delivery failed. Enable notifications again and retry." }, 503);
        }
      }
      if (path === "/api/push" && request.method === "POST") {
        const value = await body(request);
        if (!validPush(value)) return reply({ error: "Unsupported push subscription" }, 400);
        const id = createHash("sha256").update(value.endpoint).digest("hex");
        await deps.db.query("INSERT INTO finder_push_subscriptions(id,data) VALUES($1,$2) ON CONFLICT(id) DO UPDATE SET data=excluded.data", [id, value]);
        return reply({ ok: true });
      }
      return reply({ error: "Not found" }, 404);
    } catch (error) { return error instanceof InputError ? reply({ error: "Check the fields and try again." }, 400) : reply({ error: "Finder is temporarily unavailable. Please retry." }, 503); }
  };
}

export default async function handler(request: Request): Promise<Response> {
  try {
    pool ??= new Pool({ connectionString: process.env.DATABASE_URL, max: 2, connectionTimeoutMillis: 5000 });
    return await createHandler({ db: pool, authURL: process.env.FINDER_AUTH_URL ?? "",
      origin: process.env.FINDER_APP_ORIGIN ?? "", fetch })(request);
  } catch { return reply({ error: "Finder is temporarily unavailable." }, 503); }
}
