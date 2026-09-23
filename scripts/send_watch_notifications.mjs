import { Pool } from 'pg';
import webpush from 'web-push';

export async function deliver(db, send=webpush.sendNotification.bind(webpush)) {
  const key=(await db.query("SELECT data FROM finder_private_settings WHERE key='vapid'")).rows[0]?.data;
  const subscriptions=(await db.query('SELECT id,data FROM finder_push_subscriptions')).rows;
  if(!key || !subscriptions.length) return {delivered:0,devices:subscriptions.length};
  // Expire old or invalidated notifications instead of announcing stale evidence.
  await db.query(`UPDATE finder_outbox o SET status='expired' WHERE status IN ('pending','sending') AND NOT EXISTS (
    SELECT 1 FROM finder_inbox i JOIN finder_watches w ON w.id=i.watch_id
    WHERE i.watch_id=o.watch_id AND i.marketplace=o.marketplace AND i.marketplace_item_id=o.marketplace_item_id
    AND w.enabled AND NOT i.dismissed AND i.last_seen_at >= $1 AND (i.data->>'notify')='true')`,[new Date(Date.now()-3600000).toISOString()]);
  const pending=(await db.query("UPDATE finder_outbox SET status='sending',attempts=attempts+1 WHERE status IN ('pending','sending') AND attempts<3 RETURNING id")).rows;
  if(!pending.length) return {delivered:0,devices:subscriptions.length};
  let sent=0,failed=0;
  for(const subscription of subscriptions){
    try {
      await send(subscription.data,JSON.stringify({kind:'review-inbox'}),{TTL:1800,topic:'finder-inbox',timeout:10000,
        vapidDetails:{subject:process.env.FINDER_APP_ORIGIN,publicKey:key.publicKey,privateKey:key.privateKey}});
      sent++;
    } catch(error) {
      if(error.statusCode===404||error.statusCode===410) await db.query('DELETE FROM finder_push_subscriptions WHERE id=$1',[subscription.id]);
      else failed++;
    }
  }
  await db.query("UPDATE finder_outbox SET status=$2 WHERE id=ANY($1)",[pending.map(row=>row.id),failed?'pending':'sent']);
  await db.query("UPDATE finder_outbox SET status='failed' WHERE attempts>=3 AND status='pending'");
  return {delivered:sent?pending.length:0,devices:subscriptions.length,failed_devices:failed};
}

if(process.argv[1] && import.meta.url.endsWith(process.argv[1].replaceAll('\\','/'))){
  const db=new Pool({connectionString:process.env.FINDER_DATABASE_URL,max:1});
  try{console.log(JSON.stringify(await deliver(db)));}catch{console.log('{"notifications":"failed"}');process.exitCode=1;}finally{await db.end();}
}
