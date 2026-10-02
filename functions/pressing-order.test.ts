/// <reference types="emscripten" />
import test from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { verifyPressingOrder } from './pressing-order-fixture.js';

test('opt-in pressing evidence order preserves every eligible row, guards freshness, and rejects moved generations',async()=>{
 const db=new PGlite();
 try{await verifyPressingOrder((sql,args)=>db.query(sql,args));}finally{await db.close();}
});
