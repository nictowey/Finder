import assert from "node:assert/strict";
import test from "node:test";
import { createHandler } from "./scan-trigger.js";

const timestamp = "2026-09-23T15:35:00Z";
const event = { trigger: { type: "schedule", name: "scan-catchup" }, data: { scheduled_at: timestamp } };

function setup(rows: unknown[] = [{ key: "scan_dispatch" }]) {
  const queries: string[] = [];
  const sent: { url: string; options: RequestInit }[] = [];
  const handler = createHandler({
    db: { query: async (sql) => { queries.push(sql); return { rows }; } },
    token: "synthetic-token",
    fetch: async (url, options = {}) => {
      sent.push({ url: String(url), options });
      return new Response(null, { status: 204 });
    },
    now: () => new Date("2026-09-23T15:35:10Z"),
  });
  const request = (value: unknown = event, headers: Record<string, string> = { "x-neon-trigger-invocation-id": "test" }) =>
    new Request("https://example.test/", { method: "POST", headers, body: JSON.stringify(value) });
  return { handler, request, queries, sent };
}

test("rejects direct or stale calls without reading watch state", async () => {
  const { handler, request, queries, sent } = setup();
  assert.equal((await handler(request(event, {}))).status, 403);
  assert.equal((await handler(request({ ...event, data: { scheduled_at: "2026-09-23T15:15:00Z" } }))).status, 403);
  assert.equal((await handler(request({ ...event, trigger: { type: "schedule", name: "unrelated" } }))).status, 403);
  assert.equal(queries.length, 0);
  assert.equal(sent.length, 0);
});

test("a due watch dispatches only the fixed workflow and no watch identity", async () => {
  const { handler, request, queries, sent } = setup();
  assert.equal((await handler(request())).status, 200);
  assert.match(queries[1], /ON CONFLICT \(key\) DO UPDATE/);
  assert.match(queries[1], /lease_until/);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "https://api.github.com/repos/nictowey/Finder/actions/workflows/watchlist.yml/dispatches");
  const payload=JSON.parse(String(sent[0].options.body));
  assert.equal(payload.ref,'main');assert.match(payload.inputs.dispatch_id,/^[a-f0-9-]{36}$/);
  assert.ok(queries.some(q=>q.includes('http_status=$3')));
});

test("no due watch or missing credential cannot dispatch", async () => {
  const { handler, request, sent } = setup([]);
  assert.equal((await handler(request())).status, 200);
  assert.equal(sent.length, 0);
  const disabled = createHandler({ db: { query: async () => { throw new Error("should not query"); } },
    token: "", fetch: async () => { throw new Error("should not dispatch"); }, now: () => new Date("2026-09-23T15:35:10Z") });
  assert.equal((await disabled(request())).status, 503);
});

for(const mode of ['rejected','timeout']) test('dispatch '+mode+' cannot report acceptance',async()=>{
 const values:unknown[][]=[];
 const handler=createHandler({db:{query:async(sql,v=[])=>{values.push(v);return {rows:sql.includes('RETURNING id')?[{id:'reserved'}]:[]};}},token:'synthetic',now:()=>new Date('2026-09-23T15:35:10Z'),fetch:async()=>{if(mode==='timeout')throw new Error('private response');return new Response(null,{status:401});}});
 const response=await handler(new Request('https://example.test/',{method:'POST',headers:{'x-neon-trigger-invocation-id':'test'},body:JSON.stringify(event)}));
 assert.equal(response.status,503);assert.ok(!(await response.text()).includes('private'));
 assert.ok(values.some(v=>mode==='rejected'?v[1]==='rejected'&&v[2]===401:v.length===2));
});
