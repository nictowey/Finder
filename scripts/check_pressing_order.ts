// The same synthetic actual-API scenarios run in local PostgreSQL-engine tests
// and against the isolated CI PostgreSQL service. This never reads real rows.
import { Pool } from 'pg';
import { verifyPressingOrder } from '../functions/pressing-order-fixture.js';

const pool=new Pool({connectionString:process.env.FINDER_DATABASE_URL});
try{
 await verifyPressingOrder((sql,args)=>pool.query(sql,args));
 console.log('{"pressing_clues_order":"passed"}');
}catch{
 console.error('{"pressing_clues_order":"failed"}');process.exitCode=1;
}finally{await pool.end();}
