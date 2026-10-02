/// <reference types="emscripten" />
import test from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { checkOperationsFixture } from './operations-fixture.js';

test('actual bounded SQL matches the original JS summary on synthetic PostgreSQL data', async t => {
  const db = new PGlite();
  try { t.diagnostic(JSON.stringify(await checkOperationsFixture(db))); }
  finally { await db.close(); }
});
