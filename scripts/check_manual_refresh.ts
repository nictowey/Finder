// The Python gate drives the real endpoint in its disposable PostgreSQL schema.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { Pool } from 'pg';
import { createHandler } from '../functions/watchlist.js';

const pool = new Pool({ connectionString: process.env.FINDER_DATABASE_URL, max: 1 });
const client = await pool.connect();
const origin = 'https://finder.example';
const emit = (value: object) => process.stdout.write(JSON.stringify(value) + '\n');
let held = false;
try {
  const schema = (await client.query('SELECT current_schema() AS name')).rows[0].name;
  assert.match(schema, /^finder_check_[a-f0-9]{32}$/);
  const pid = (await client.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
  const handler = createHandler({
    db: {
      query: async (sql, values) => sql.includes("key='owner_email'")
        ? { rows: [{ data: { email: 'owner@example.com' } }] }
        : client.query(sql, values),
    },
    origin,
    authURL: 'https://auth.example',
    fetch: async input => {
      assert.equal(String(input), 'https://auth.example/get-session');
      return new Response(JSON.stringify({
        user: { email: 'owner@example.com', emailVerified: true },
        session: { expiresAt: '2099-01-01T00:00:00Z' },
      }));
    },
  });
  for await (const line of createInterface({ input: process.stdin })) {
    const command = JSON.parse(line);
    if (command.action === 'close') break;
    if (command.action === 'commit' || command.action === 'rollback') {
      assert.ok(held);
      await client.query(command.action === 'commit' ? 'COMMIT' : 'ROLLBACK');
      held = false;
      emit({ phase: 'released' });
      continue;
    }
    assert.ok(command.action === 'request' || command.action === 'set_enabled');
    assert.equal(held, false);
    if (command.hold) {
      await client.query('BEGIN');
      held = true;
    }
    // Report the real backend before the endpoint can block on the worker fence.
    emit({ phase: 'started', pid });
    let path = '/api/refresh-lead', method = 'POST';
    let body: object = { watch_id: command.watch_id, item_id: command.item_id };
    if (command.action === 'set_enabled') {
      assert.equal(typeof command.enabled, 'boolean');
      const current = await client.query('SELECT config FROM finder_watches WHERE id=$1', [command.watch_id]);
      assert.equal(current.rows.length, 1);
      path = '/api/watches/' + command.watch_id;
      method = 'PUT';
      body = { ...current.rows[0].config, enabled: command.enabled };
    }
    const response = await handler(new Request(origin + path, {
      method,
      headers: { Origin: origin, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
    emit({ phase: 'result', status: response.status, body: await response.json(), held });
  }
} catch (error) {
  emit({ phase: 'failed', error_type: error instanceof Error ? error.name : 'UnknownError' });
  process.exitCode = 1;
} finally {
  if (held) await client.query('ROLLBACK');
  client.release();
  await pool.end();
}
