import { createHash, createVerify } from "node:crypto";
import { Pool } from "pg";

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,80}$/;
const KEY_ID_PATTERN = /^[A-Za-z0-9-]{1,128}$/;
const MAX_BODY_BYTES = 65_536;
const KEY_TTL_MS = 3_600_000;
const keys = new Map<string, { pem: string; expiresAt: number }>();
let appToken: { value: string; expiresAt: number } | undefined;
let pool: Pool | undefined;

type Queryable = {
  query: (sql: string, values?: unknown[]) => Promise<{ rows: Record<string, unknown>[] }>;
  connect: () => Promise<{
    query: Queryable["query"];
    release: () => void;
  }>;
};

type Deps = {
  env: Record<string, string | undefined>;
  db: Queryable;
  getPublicKey: (keyId: string) => Promise<string>;
};

function response(status: number, text = ""): Response {
  return new Response(status === 204 ? null : text, { status });
}

async function readBoundedBody(request: Request): Promise<Buffer | null> {
  if (!request.body) return Buffer.alloc(0);
  const reader = request.body.getReader();
  const chunks: Buffer[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) return Buffer.concat(chunks, size);
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(Buffer.from(value));
  }
}

function decodeBase64(value: unknown): Buffer {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    throw new Error("Invalid Base64");
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value) throw new Error("Invalid Base64");
  return bytes;
}

function publicKeyPem(value: string): string {
  const payload = value
    .replace("-----BEGIN PUBLIC KEY-----", "")
    .replace("-----END PUBLIC KEY-----", "")
    .replace(/\s/g, "");
  decodeBase64(payload);
  return `-----BEGIN PUBLIC KEY-----\n${payload.match(/.{1,64}/g)?.join("\n")}\n-----END PUBLIC KEY-----\n`;
}

async function ebayToken(env: Deps["env"]): Promise<string> {
  if (appToken && appToken.expiresAt > Date.now()) return appToken.value;
  const clientId = env.EBAY_PRODUCTION_CLIENT_ID;
  const clientSecret = env.EBAY_PRODUCTION_CLIENT_SECRET;
  if (!clientId || !clientSecret) throw new Error("Missing eBay credentials");
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const result = await fetch("https://api.ebay.com/identity/v1/oauth2/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "client_credentials",
      scope: "https://api.ebay.com/oauth/api_scope",
    }),
    signal: AbortSignal.timeout(5_000),
  });
  if (!result.ok) throw new Error("eBay token unavailable");
  const data = await result.json();
  if (typeof data.access_token !== "string" || !Number.isFinite(data.expires_in)) {
    throw new Error("Invalid eBay token response");
  }
  appToken = {
    value: data.access_token,
    expiresAt: Date.now() + Math.max(0, data.expires_in - 60) * 1_000,
  };
  return appToken.value;
}

async function ebayPublicKey(keyId: string, env: Deps["env"]): Promise<string> {
  const cached = keys.get(keyId);
  if (cached && cached.expiresAt > Date.now()) return cached.pem;
  const token = await ebayToken(env);
  const result = await fetch(
    `https://api.ebay.com/commerce/notification/v1/public_key/${keyId}`,
    { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5_000) },
  );
  if (!result.ok) throw new Error("eBay public key unavailable");
  const data = await result.json();
  if (typeof data.key !== "string") throw new Error("Invalid eBay public key response");
  const pem = publicKeyPem(data.key);
  keys.set(keyId, { pem, expiresAt: Date.now() + KEY_TTL_MS });
  return pem;
}

async function verifySignature(
  body: Buffer,
  header: string | null,
  getPublicKey: Deps["getPublicKey"],
): Promise<boolean> {
  if (!header || header.length > 4_096) return false;
  let fields: Record<string, unknown>;
  let signature: Buffer;
  let payload: unknown;
  try {
    fields = JSON.parse(decodeBase64(header).toString("utf8"));
    if (
      !fields ||
      typeof fields !== "object" ||
      typeof fields.kid !== "string" ||
      !KEY_ID_PATTERN.test(fields.kid) ||
      String(fields.alg).toLowerCase() !== "ecdsa" ||
      String(fields.digest).toUpperCase() !== "SHA1"
    ) return false;
    signature = decodeBase64(fields.signature);
    payload = JSON.parse(body.toString("utf8"));
  } catch {
    return false;
  }
  const pem = await getPublicKey(fields.kid as string);
  // eBay's Node SDK signs JSON.stringify(parsedMessage), not raw HTTP spacing.
  const verifier = createVerify("sha1");
  verifier.update(JSON.stringify(payload));
  verifier.end();
  try {
    return verifier.verify(pem, signature);
  } catch {
    return false;
  }
}

async function deleteSeller(db: Queryable, sellerId: string): Promise<void> {
  const connection = await db.connect();
  try {
    await connection.query("BEGIN");
    // The Python writer takes this same transaction lock before each upsert.
    await connection.query("SELECT pg_advisory_xact_lock(hashtext($1))", [sellerId]);
    await connection.query(
      "INSERT INTO ebay_deleted_users (seller_id) VALUES ($1) ON CONFLICT DO NOTHING",
      [sellerId],
    );
    const result = await connection.query(
      "SELECT marketplace_item_id FROM listings WHERE marketplace = 'ebay' AND data->>'seller_id' = $1 FOR UPDATE",
      [sellerId],
    );
    const itemIds = result.rows.map((row) => row.marketplace_item_id);
    if (itemIds.length) {
      for (const table of ["listing_variant_candidates", "listing_observations", "listings"]) {
        await connection.query(
          `DELETE FROM ${table} WHERE marketplace = 'ebay' AND marketplace_item_id = ANY($1::text[])`,
          [itemIds],
        );
      }
    }
    await connection.query("COMMIT");
  } catch (error) {
    await connection.query("ROLLBACK");
    throw error;
  } finally {
    connection.release();
  }
}

export function createHandler({ env, db, getPublicKey }: Deps) {
  return async (request: Request): Promise<Response> => {
    const endpoint = env.EBAY_DELETION_ENDPOINT_URL;
    const token = env.EBAY_DELETION_VERIFICATION_TOKEN;
    if (!endpoint || !endpoint.startsWith("https://") || !token || !TOKEN_PATTERN.test(token)) {
      return response(503);
    }
    if (request.method === "GET") {
      const challenge = new URL(request.url).searchParams.get("challenge_code");
      if (!challenge || challenge.length > 256) return response(400);
      const digest = createHash("sha256").update(challenge + token + endpoint).digest("hex");
      return Response.json({ challengeResponse: digest });
    }
    if (request.method !== "POST") return response(405);
    if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
      return response(415);
    }
    const declaredSize = request.headers.get("content-length");
    if (declaredSize && (Number.isNaN(Number(declaredSize)) || Number(declaredSize) > MAX_BODY_BYTES)) {
      return response(413);
    }
    const body = await readBoundedBody(request);
    if (!body) return response(413);
    try {
      if (!(await verifySignature(body, request.headers.get("x-ebay-signature"), getPublicKey))) {
        return response(412);
      }
    } catch {
      // An eBay key service outage must be retried, never acknowledged as deletion.
      return response(503);
    }
    let userId: unknown;
    try {
      const payload = JSON.parse(body.toString("utf8"));
      if (payload.metadata?.topic !== "MARKETPLACE_ACCOUNT_DELETION") return response(400);
      userId = payload.notification?.data?.userId;
    } catch {
      return response(400);
    }
    if (typeof userId !== "string" || !userId.trim() || userId.length > 255) return response(400);
    try {
      await deleteSeller(db, userId);
      return response(204);
    } catch {
      // Keep identities and database details out of public logs and responses.
      return response(503);
    }
  };
}

export default function handle(request: Request): Promise<Response> {
  if (!pool) pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 2 });
  return createHandler({
    env: process.env,
    db: pool as Queryable,
    getPublicKey: (keyId) => ebayPublicKey(keyId, process.env),
  })(request);
}
