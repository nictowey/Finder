// Execute the real owner endpoint against PostgreSQL semantics, with synthetic data only.
import { readFileSync } from 'node:fs';
import { mock } from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { createHandler } from '../../functions/watchlist.js';
const seed=JSON.parse(readFileSync(0,'utf8'));
// The worker snapshot follows the request by one second, without wall-clock races.
mock.timers.enable({apis:['Date'],now:new Date(Date.parse(seed.now)-1000)});
const db=new PGlite();
try {
 await db.exec(`CREATE TABLE finder_private_settings(key text PRIMARY KEY,data json);
 CREATE TABLE finder_watches(id text PRIMARY KEY,lease_token text,lease_until text,next_scan_at text,enabled boolean,status text);
 CREATE TABLE finder_discovery_work(watch_id text,item_id text,reason text,status text,next_check_at text,failures int,refresh_token text,refresh_after text,PRIMARY KEY(watch_id,item_id));
 INSERT INTO finder_private_settings VALUES('owner_email','{"email":"owner@example.com"}');`);
 await db.query('INSERT INTO finder_watches VALUES($1,$2,$3,$4,$5,\'pending\')',[seed.watch_id,seed.lease_token,seed.lease_until,seed.now,seed.enabled??true]);
 if(seed.item)await db.query('INSERT INTO finder_discovery_work VALUES($1,$2,$3,$4,$5,$6,$7,$8)',[seed.watch_id,seed.item.item_id,seed.item.reason,seed.item.status,seed.item.next_check_at,seed.item.failures,seed.item.refresh_token??null,seed.item.refresh_after??null]);
 const origin='https://finder.example';
 const handler=createHandler({db,origin,authURL:'https://auth.example',fetch:async()=>new Response(JSON.stringify({user:{email:'owner@example.com',emailVerified:true},session:{expiresAt:new Date(Date.now()+60000).toISOString()}}))});
 const response=await handler(new Request(origin+'/api/refresh-lead',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({watch_id:seed.watch_id,item_id:seed.item?.item_id??'missing'})}));
 console.log(JSON.stringify({status:response.status,body:await response.json(),watch:(await db.query('SELECT * FROM finder_watches')).rows[0],item:(await db.query('SELECT * FROM finder_discovery_work')).rows[0]??null}));
}finally{await db.close();mock.timers.reset();}
