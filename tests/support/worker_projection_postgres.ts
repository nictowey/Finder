// Run the production SQLAlchemy JSON projections in local PostgreSQL/WASM only.
import { readFileSync } from 'node:fs';
import { PGlite } from '@electric-sql/pglite';
const input = JSON.parse(readFileSync(0, 'utf8'));
const db = new PGlite();
try {
  const results = [];
  for (const sql of input.queries) results.push((await db.query(sql)).rows);
  process.stdout.write(JSON.stringify(results));
} finally {
  await db.close();
}
