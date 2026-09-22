import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import test from "node:test";

import { createHandler } from "./ebay-deletion.js";

const endpoint = "https://example.neon.app/ebay-deletion";
const token = "test-token-with-more-than-thirty-two-characters";
const env = {
  EBAY_DELETION_ENDPOINT_URL: endpoint,
  EBAY_DELETION_VERIFICATION_TOKEN: token,
};
const keys = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const pem = keys.publicKey.export({ format: "pem", type: "spki" }).toString();

function database(itemIds: string[] = ["item-1"]) {
  const calls: Array<{ sql: string; values?: unknown[] }> = [];
  const client = {
    async query(sql: string, values?: unknown[]) {
      calls.push({ sql, values });
      return {
        rows: sql.startsWith("SELECT marketplace_item_id")
          ? itemIds.map((marketplace_item_id) => ({ marketplace_item_id }))
          : [],
      };
    },
    release() {},
  };
  return { calls, db: { query: client.query, async connect() { return client; } } };
}

function signedRequest(body: unknown, tamper = false): Request {
  const payload = JSON.stringify(body);
  const signature = sign("sha1", Buffer.from(payload), keys.privateKey).toString("base64");
  const header = Buffer.from(
    JSON.stringify({ alg: "ecdsa", digest: "SHA1", kid: "key-1", signature }),
  ).toString("base64");
  return new Request(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json", "x-ebay-signature": header },
    body: tamper ? payload.replace("seller-1", "seller-2") : payload,
  });
}

const payload = {
  metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
  notification: { data: { userId: "seller-1" } },
};

test("challenge hashes exact registered endpoint and token", async () => {
  const { db } = database();
  const handle = createHandler({ env, db, getPublicKey: async () => pem });
  const result = await handle(new Request(`${endpoint}?challenge_code=abc`));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {
    challengeResponse: createHash("sha256").update(`abc${token}${endpoint}`).digest("hex"),
  });
});

test("verified notice tombstones seller and deletes all linked eBay data", async () => {
  const { db, calls } = database();
  const handle = createHandler({ env, db, getPublicKey: async () => pem });
  assert.equal((await handle(signedRequest(payload))).status, 204);
  assert.deepEqual(calls.map(({ sql }) => sql.split(" ")[0]), [
    "BEGIN", "SELECT", "INSERT", "SELECT", "DELETE", "DELETE", "DELETE", "COMMIT",
  ]);
  assert.ok(calls.some(({ sql, values }) => sql.startsWith("INSERT INTO ebay_deleted_users") && values?.[0] === "seller-1"));
  assert.ok(calls.filter(({ sql }) => sql.startsWith("DELETE FROM")).every(({ values }) =>
    JSON.stringify(values) === JSON.stringify([["item-1"]]),
  ));
});

test("bad signature cannot delete or tombstone", async () => {
  const { db, calls } = database();
  const handle = createHandler({ env, db, getPublicKey: async () => pem });
  assert.equal((await handle(signedRequest(payload, true))).status, 412);
  assert.equal(calls.length, 0);
});

test("missing eBay key returns retryable error", async () => {
  const { db, calls } = database();
  const handle = createHandler({ env, db, getPublicKey: async () => { throw new Error("offline"); } });
  assert.equal((await handle(signedRequest(payload))).status, 503);
  assert.equal(calls.length, 0);
});

test("oversized body is rejected before signature or database work", async () => {
  const { db, calls } = database();
  const handle = createHandler({ env, db, getPublicKey: async () => { throw new Error("called"); } });
  const result = await handle(new Request(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "x".repeat(65_537),
  }));
  assert.equal(result.status, 413);
  assert.equal(calls.length, 0);
});
