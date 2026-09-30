// Invoked only by check_watch_postgres.py with its disposable synthetic schema URL.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { Pool } from 'pg';
import { saveDecisionQuery,decisionInput } from '../functions/decisions.js';
const db=new Pool({connectionString:process.env.FINDER_DATABASE_URL,max:3});
try {
 const key=(await db.query('SELECT watch_id,marketplace,marketplace_item_id FROM finder_inbox LIMIT 1')).rows[0];
 assert.ok(key);
 const save=(value:object,purchase=false)=>db.query(saveDecisionQuery,decisionInput({...key,...value},purchase)!);
 await save({purchased:true},true);await save({verdict:'mine'});
 const first=(await db.query('SELECT * FROM finder_decisions')).rows[0];
 await save({verdict:'other'});await save({verdict:null});await save({purchased:false},true);
 assert.equal((await db.query('SELECT * FROM finder_verdicts')).rows.length,0);
 await save({verdict:'unsure'});
 const after=(await db.query('SELECT * FROM finder_decisions')).rows[0];
 assert.equal(after.decided_at,first.decided_at);assert.deepEqual(after.prediction,first.prediction);
 // A worker holds this watch fence before reading suppression and enqueuing.
 // Feedback must wait, then remove its newly queued pending event atomically.
 await save({verdict:null});
 const worker=await db.connect(),feedback=await db.connect();
 let saving:Promise<unknown>|undefined;
 try {
  const workerPID=(await worker.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
  const feedbackPID=(await feedback.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
  await worker.query('BEGIN');await worker.query('SELECT id FROM finder_watches WHERE id=$1 FOR UPDATE',[key.watch_id]);
  saving=feedback.query(saveDecisionQuery,decisionInput({...key,verdict:'mine'},false)!);
  void saving.catch(()=>{}); // Preserve the rejection for await while observing the lock.
  const deadline=Date.now()+5000;let blocked=false;
  while(Date.now()<deadline){
   const pids=(await db.query('SELECT pg_blocking_pids($1) AS pids',[feedbackPID])).rows[0].pids;
   if(pids.includes(workerPID)){blocked=true;break;}
   await new Promise(resolve=>setTimeout(resolve,25));
  }
  assert.ok(blocked,'Feedback must demonstrably wait on the worker watch lock');
  await worker.query("INSERT INTO finder_outbox(id,watch_id,marketplace,marketplace_item_id,created_at,status,attempts) VALUES($1,$2,$3,$4,$5,'pending',0)",[randomUUID(),key.watch_id,key.marketplace,key.marketplace_item_id,new Date().toISOString()]);
  await worker.query('COMMIT');await saving;
  assert.equal((await db.query("SELECT * FROM finder_outbox WHERE status='pending'")).rows.length,0);
 }finally{await worker.query('ROLLBACK');if(saving)await saving.catch(()=>{});worker.release();feedback.release();}
 console.log('{"feedback_postgres_bridge_provenance_locking":"passed"}');
}finally{await db.end();}
