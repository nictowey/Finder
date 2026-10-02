import { createHash, randomUUID } from "node:crypto";
import { Pool } from "pg";
import { decisionInput, saveDecisionQuery } from "./decisions.js";
import { decisionSummaryQuery } from "./decision-summary.js";
import { inboxQuery, pressingCluesInboxQuery } from "./inbox-query.js";
import { readOperations, readOperationsHealth } from "./operations.js";
import webpush from "web-push";
import { html, javascript, serviceWorker, stylesheet } from "./watchlist-ui.js";

type DB = { query: (sql: string, values?: unknown[]) => Promise<{ rows: any[] }> };
type Deps = { db: DB; authURL: string; origin: string; fetch: typeof fetch; sendPush?: typeof webpush.sendNotification };
let pool: Pool | undefined;
class InputError extends Error {}
// Keep in sync with MAX_WATCHES in src/finder/watch_store.py.
export const MAX_WATCHES = 20;
const CLUE_KINDS = ["keyword", "color", "catalog_number", "barcode", "label", "country", "numbered"];

function reply(data: unknown, status = 200, type = "application/json"): Response {
  return new Response(type === "application/json" ? JSON.stringify(data) : String(data), {
    status, headers: { "Content-Type": type, "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://i.ebayimg.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" },
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

function money(value: any, name: string): string | null {
  const text = value === "" || value == null ? null : String(value);
  if (text !== null && (!/^\d{1,7}(?:\.\d{1,2})?$/.test(text) || Number(text) <= 0)) throw new InputError(name);
  return text;
}

function clues(value: any) {
  const rows = value ?? [];
  if (!Array.isArray(rows) || rows.length > 12) throw new InputError("signs");
  return rows.map((row: any) => {
    const text = typeof row?.value === "string" ? row.value.trim() : "";
    if (!row || typeof row !== "object" || !CLUE_KINDS.includes(row.kind) || !text || text.length > 80 ||
      (row.required !== undefined && typeof row.required !== "boolean")) throw new InputError("signs");
    return { kind: row.kind, value: text, required: row.required ?? false };
  });
}

export function validImage(value: unknown): value is string {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" && url.hostname === "i.ebayimg.com" && !url.username && !url.port && url.href.length < 1024;
  } catch { return false; }
}

export function validateWatch(value: any) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new InputError("watch");
  const release = String(value.release_id ?? "").trim();
  const match = release.match(/^(?:https:\/\/(?:www\.)?discogs\.com\/(?:[a-z]{2}\/)?release\/)?([1-9]\d*)(?:-[^?#]*)?(?:[?#].*)?$/);
  if (!match || !Number.isSafeInteger(Number(match[1]))) throw new InputError("release");
  const ceiling = money(value.maximum_subtotal, "ceiling");
  const gamble = money(value.gamble_max, "gamble");
  const auction = value.auction_alert_minutes ?? 120;
  if (!Number.isInteger(auction) || auction < 0 || auction > 1440) throw new InputError("auction");
  const extra = value.extra_queries ?? [];
  if (!Array.isArray(extra) || extra.length > 2 || extra.some((q: any) => typeof q !== "string" || !q.trim() || q.trim().length > 100)) throw new InputError("searches");
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
    currency: value.currency ?? "USD", condition_ids: conditions, country, postal_code: postal, enabled: value.enabled ?? true, alert_mode: value.alert_mode ?? "review_leads",
    gamble_max: gamble, auction_alert_minutes: auction, tells: clues(value.tells), anti_tells: clues(value.anti_tells),
    extra_queries: extra.map((q: string) => q.trim()) };
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
      if (path === "/api/operations" && request.method === "GET") {
        const watches = (await deps.db.query("SELECT id,config->'enabled' AS enabled,status,last_success_at,next_scan_at,lease_until FROM finder_watches ORDER BY id")).rows;
        return reply(await readOperations(deps.db, watches.map(w => ({ ...w, config: { enabled: w.enabled } }))));
      }
      if (path === "/api/dashboard" && request.method === "GET") {
        const watches = (await deps.db.query("SELECT id,config,revision,status,last_started_at,last_success_at,next_scan_at,lease_until,summary,catalog,catalog_observed_at FROM finder_watches ORDER BY id")).rows;
        const tierCounts = (await deps.db.query("SELECT watch_id,data->>'status' AS tier,count(*)::int AS n FROM finder_inbox WHERE NOT dismissed GROUP BY watch_id,data->>'status'")).rows;
        const watchVerdicts = (await deps.db.query("SELECT watch_id,tier,verdict,count(*)::int AS n FROM finder_decisions WHERE verdict IS NOT NULL GROUP BY watch_id,tier,verdict")).rows;
        const profiles = (await deps.db.query("SELECT watch_id,observed_at,data->'proposals' AS proposals,data->'vinyl_versions' AS vinyl_versions,data->'partial' AS partial FROM finder_watch_profiles")).rows;
        for (const watch of watches) {
          const profile = profiles.find(p => p.watch_id === watch.id);
          watch.profile = profile ? { observed_at: profile.observed_at, proposals: profile.proposals, vinyl_versions: profile.vinyl_versions, partial: profile.partial } : null;
          watch.inbox_counts = Object.fromEntries(tierCounts.filter(row => row.watch_id === watch.id).map(row => [row.tier, Number(row.n)]));
          watch.verdict_counts = watchVerdicts.filter(row => row.watch_id === watch.id).map(({ tier, verdict, n }) => ({ tier, verdict, n: Number(n) }));
        }
        const now = Date.now(), cutoff = now - 6 * 3600_000;
        for (const watch of watches) if (Date.parse(watch.catalog_observed_at ?? "") < cutoff || !watch.catalog_observed_at) watch.catalog = null;
        const filter = url.searchParams.get("filter") || "review";
        if (!["review","judged","possible_pressing","family_review","conflicting","unrelated","unavailable","dismissed"].includes(filter)) return reply({error:"Invalid inbox filter"},400);
        const order = url.searchParams.get("order") || "newest";
        if (!["newest","pressing_clues"].includes(order)) return reply({error:"Invalid inbox order"},400);
        if (order==='pressing_clues' && !['review','judged'].includes(filter)) return reply({error:"Pressing clues order requires To review or Judged"},400);
        const priceScope = url.searchParams.get("prices") || "watch";
        if (!["watch", "all"].includes(priceScope)) return reply({error:"Invalid price filter"},400);
        const judgment = url.searchParams.get("judgment") || "all";
        const purchased = url.searchParams.get("purchased") || "all";
        if (!["all","mine","other","unsure"].includes(judgment) || !["all","yes"].includes(purchased)) return reply({error:"Invalid judgment filter"},400);
        if (filter!=="judged" && (judgment!=="all" || purchased!=="all")) return reply({error:"Judgment filters require the Judged view"},400);
        let cursor: string[] | null = null, orderCursor: string[] | null = null;
        if (url.searchParams.has("cursor")) {
          let value: unknown;
          try { value = JSON.parse(url.searchParams.get("cursor")!); } catch { return reply({error:"Invalid cursor"},400); }
          if (!Array.isArray(value) || value.some(v=>typeof v!=="string" || v.length>255)) return reply({error:"Invalid cursor"},400);
          if (order==='newest') {
            if (value.length!==3) return reply({error:"Cursor does not match this inbox order"},400);
            cursor=value;
          } else {
            if (value.length!==11 || ![order,filter,priceScope,judgment,purchased].every((v,i)=>value[i]===v)
              || !['0','1'].includes(value[5]) || !/^[a-f0-9]{32}$/.test(value[10])) return reply({error:"Cursor does not match this inbox order or filters"},400);
            orderCursor=value;cursor=[value[6],value[7],value[9]];
          }
        }
        const args: unknown[] = [filter,...(cursor || [null,null,null]),priceScope,new Date(cutoff).toISOString(),judgment,purchased];
        if (order==='pressing_clues') args.push(new Date(now-3600_000).toISOString(),new Date(now).toISOString(),orderCursor?.[5]??null,orderCursor?.[8]??null);
        let leads = (await deps.db.query(order==='pressing_clues'?pressingCluesInboxQuery:inboxQuery,args)).rows;
        const generation=leads[0]?.order_generation;
        if (orderCursor && orderCursor[10]!==generation) return reply({error:"The review order changed. Start again from the first page.",code:"inbox_order_changed"},409);
        if (order==='pressing_clues') leads=leads.filter(row=>row.watch_id!==null);
        const more = leads.length > 50;
        if (more) leads.pop();
        const tail = leads.at(-1);
        const nextCursor = more && tail ? JSON.stringify(order==='pressing_clues'
          ? [order,filter,priceScope,judgment,purchased,String(tail.pressing_rank),tail.first_seen_at,tail.watch_id,tail.marketplace,tail.marketplace_item_id,generation]
          : [tail.first_seen_at,tail.watch_id,tail.marketplace_item_id]) : null;
        // Keep old references discoverable; never extend the six-hour provider content
        // display window or imply that a fresh summary freshened detail evidence.
        for (const row of leads) {
          const l = row.listing;
          row.evidence_invalidated_reason = row.pending_evidence_change ? 'seller_changed' : row.data.evidence_invalidated_at ? (row.data.evidence_invalidated_reason==='seller_changed'?'seller_changed':'refresh_failed') : null;
          row.evidence_stale = !!row.evidence_invalidated_reason || !l.details_observed_at || Date.parse(l.details_observed_at)<cutoff || row.data.watch_revision!==row.revision;
          row.listing = row.evidence_stale ? {title:row.evidence_invalidated_reason?"Listing needs a fresh check · awaiting details":"Previously discovered listing · awaiting fresh details",listing_url:l.listing_url,item_specifics:{}} :
            { title:l.title,listing_url:l.listing_url,condition:l.condition,condition_id:l.condition_id,
              images:[l.primary_image,...(Array.isArray(l.additional_images)?l.additional_images:[])].filter(validImage).slice(0,12),
              current_price:l.current_price,shipping_cost:l.shipping_cost,currency:l.currency,
              price_kind:l.price_kind,listing_ends_at:l.listing_ends_at,item_specifics:l.item_specifics,
              details_observed_at:l.details_observed_at,last_observed_at:l.last_observed_at };
          if(row.evidence_stale) { row.pressing_clues=[];row.data={status:row.data.status,availability:row.data.availability,clues:[],verify:[row.evidence_invalidated_reason?(row.evidence_invalidated_reason==='seller_changed'?'seller_details_changed':'detail_refresh_failed'):"reference_only_current_availability_unverified"],budget:"needs_refresh",notify:false}; }
          delete row.revision;
          delete row.pressing_rank;delete row.order_generation;
        }
        const ids = leads.map(row => row.marketplace_item_id);
        const decided = ids.length ? (await deps.db.query("SELECT watch_id,marketplace,marketplace_item_id,verdict,purchased,tier,decided_at,prediction FROM finder_decisions WHERE marketplace_item_id = ANY($1)", [ids])).rows : [];
        for (const row of leads) {
          const decision=decided.find(v => v.watch_id===row.watch_id && v.marketplace===row.marketplace && v.marketplace_item_id===row.marketplace_item_id);
          row.verdict=decision?.verdict??null; row.purchased=decision?.purchased??false;
          row.verdict_tier=decision?.tier??null; row.verdict_decided_at=decision?.decided_at??null;
          row.verdict_provenance=decision?.prediction?.source??null;
        }
        const accuracy = (await deps.db.query("SELECT tier,verdict,count(*)::int AS n FROM finder_decisions WHERE verdict IS NOT NULL GROUP BY tier,verdict")).rows;
        const summary = (await deps.db.query(decisionSummaryQuery,[judgment,purchased])).rows[0] || {};
        const counts = (keys:string[]) => Object.fromEntries(keys.map(key=>[key,Number(summary[key]||0)]));
        const judged_counts = counts(["all","mine","other","unsure","purchased"]);
        const decision_totals = counts(["judged","mine","other","unsure","purchased","purchase_only","dismissed_judged"]);
        const push = (await deps.db.query("SELECT data FROM finder_private_settings WHERE key='vapid'")).rows[0]?.data;
        const operations = await readOperationsHealth(deps.db, watches);
        return reply({ watches, leads, next_cursor: nextCursor, operations, accuracy, judged_counts, decision_totals, filtered_total: filter==='judged'?Number(summary.filtered_total||0):null, max_watches: MAX_WATCHES, push_key: push?.publicKey ?? null, email: owner, now: new Date().toISOString() });
      }
      if (["/api/verdict","/api/purchase"].includes(path) && request.method === "POST") {
        const value=await body(request), args=decisionInput(value,path==="/api/purchase");
        if(!args)return reply({error:"Invalid feedback"},400);
        const saved=await deps.db.query(saveDecisionQuery,args);
        if(saved.rows.length)return reply({ok:true,...saved.rows[0]});
        const found=await deps.db.query("SELECT 1 FROM finder_inbox WHERE watch_id=$1 AND marketplace=$2 AND marketplace_item_id=$3",args.slice(0,3));
        return found.rows.length?reply({error:"This listing changed. Refresh it before saving your judgment."},409):reply({error:"Listing not found"},404);
      }
      if (path === "/api/refresh-lead" && request.method === "POST") {
        const value=await body(request);
        if(typeof value.watch_id!=="string" || typeof value.item_id!=="string" || value.item_id.length>255) return reply({error:"Invalid reference"},400);
        // Serialize with the worker's watch fence before touching its work row. The
        // durable token survives old workers that still overwrite legacy queue fields.
        const result=await deps.db.query(`WITH locked AS MATERIALIZED (
          SELECT id,enabled FROM finder_watches WHERE id=$1 FOR NO KEY UPDATE
        ), queued AS (
          UPDATE finder_discovery_work d SET refresh_token=$4,
            refresh_after=GREATEST($3,CASE WHEN d.refresh_token IS NOT NULL THEN d.refresh_after
              WHEN d.status='error' THEN d.next_check_at ELSE $3 END),
            status='pending',next_check_at=GREATEST($3,CASE WHEN d.refresh_token IS NOT NULL THEN d.refresh_after
              WHEN d.status='error' THEN d.next_check_at ELSE $3 END)
          FROM locked WHERE d.watch_id=locked.id AND d.item_id=$2 AND locked.enabled RETURNING d.watch_id
        ), scheduled AS (
          UPDATE finder_watches SET next_scan_at=$3 WHERE id IN (SELECT watch_id FROM queued) RETURNING id
        ) SELECT CASE WHEN EXISTS(SELECT 1 FROM scheduled) THEN 'queued'
          WHEN EXISTS(SELECT 1 FROM locked WHERE NOT enabled)
            AND EXISTS(SELECT 1 FROM finder_discovery_work WHERE watch_id=$1 AND item_id=$2)
            THEN 'paused' ELSE 'missing' END AS result`,[value.watch_id,value.item_id,new Date().toISOString(),randomUUID()]);
        if(result.rows[0]?.result==='queued')return reply({ok:true});
        if(result.rows[0]?.result==='paused')return reply({error:"This watch is paused. Resume it before requesting a fresh check."},409);
        return reply({error:"Listing not found"},404);
      }
      if (path === "/api/watches" && request.method === "POST") {
        const watch = validateWatch(await body(request));
        // Serialize limit checks, including concurrent requests.
        const id = randomUUID();
        const result = await deps.db.query(`INSERT INTO finder_watches(id,slot,config,revision,enabled,next_scan_at,status)
          SELECT $1,n,$2,1,$3,$4,'pending' FROM generate_series(1,$5::int) n
          WHERE n NOT IN (SELECT slot FROM finder_watches) ORDER BY n LIMIT 1
          ON CONFLICT DO NOTHING RETURNING id`, [id, watch, watch.enabled, new Date().toISOString(), MAX_WATCHES]);
        return result.rows.length ? reply({ id }, 201) : reply({ error: `Finder supports ${MAX_WATCHES} watches to stay within the daily eBay request budget.` }, 409);
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
