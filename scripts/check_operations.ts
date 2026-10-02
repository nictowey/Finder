// Opt-in synthetic gate: only the existing disposable loopback CI fixture is allowed.
// Never invoke this with a production database URL.
import { Pool } from 'pg';
import { checkOperationsFixture } from '../functions/operations-fixture.js';
import { validateOperationsFixtureURL } from './operations-fixture-target.js';
const connectionString = process.env.FINDER_OPERATIONS_FIXTURE_URL ?? '';
validateOperationsFixtureURL(connectionString);
const db = new Pool({connectionString,max:1,connectionTimeoutMillis:5000,statement_timeout:30000});
try {
  const client = await db.connect();
  try {
    await client.query('BEGIN');
    const measurement = await checkOperationsFixture(client);
    await client.query('ROLLBACK');
    console.log(JSON.stringify({operations_postgres_synthetic:'passed',...measurement}));
  } finally { client.release(); }
} catch {
  // PostgreSQL errors may contain query parameters. Keep public CI output generic.
  console.error('Synthetic operations PostgreSQL gate failed');
  process.exitCode = 1;
} finally { await db.end(); }
