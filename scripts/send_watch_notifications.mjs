import { Pool } from 'pg';
import webpush from 'web-push';

export async function deliver(db, send=webpush.sendNotification.bind(webpush)) {
  const report = async value => {
    await db.query("INSERT INTO finder_private_settings(key,data) VALUES('notification_health',$1) ON CONFLICT(key) DO UPDATE SET data=EXCLUDED.data",
      [{...value,at:new Date().toISOString()}]);
    return value;
  };
  const key=(await db.query("SELECT data FROM finder_private_settings WHERE key='vapid'")).rows[0]?.data;
  const subscriptions=(await db.query('SELECT id,data FROM finder_push_subscriptions')).rows;
  // Expiration and final-attempt crash recovery also run when no device is enrolled.
  await db.query(`UPDATE finder_outbox o SET status='expired' WHERE status IN ('pending','sending') AND NOT EXISTS (
    SELECT 1 FROM finder_inbox i JOIN finder_watches w ON w.id=i.watch_id
    JOIN listings l ON l.marketplace=i.marketplace AND l.marketplace_item_id=i.marketplace_item_id
    WHERE i.watch_id=o.watch_id AND i.marketplace=o.marketplace AND i.marketplace_item_id=o.marketplace_item_id
    AND l.data->>'details_observed_at' >= $1
    AND (l.data->>'listing_ends_at' IS NULL OR l.data->>'listing_ends_at' > $3)
    AND w.enabled AND NOT i.dismissed AND i.last_seen_at >= $1 AND (i.data->>'notify')='true'
    AND NOT EXISTS (SELECT 1 FROM finder_verdicts v WHERE v.watch_id=i.watch_id AND v.marketplace=i.marketplace AND v.marketplace_item_id=i.marketplace_item_id)
    AND (i.data->>'policy')=$2
    AND (i.data->>'watch_revision')=w.revision::text)`,[new Date(Date.now()-3600000).toISOString(),'private-target-review-v6',new Date().toISOString()]);
  await db.query("UPDATE finder_outbox SET status='failed' WHERE attempts>=3 AND status IN ('pending','sending')");
  if(!key || !subscriptions.length) return report({status:!key?'not_configured':'no_devices',accepted_events:0,devices:subscriptions.length});
  const pending=(await db.query("UPDATE finder_outbox SET status='sending',attempts=attempts+1 WHERE status IN ('pending','sending') AND attempts<3 RETURNING id")).rows;
  if(!pending.length) return report({status:'no_eligible_alerts',accepted_events:0,devices:subscriptions.length});
  let accepted=0,failed=0,invalid=0;
  for(const subscription of subscriptions){
    try {
      await send(subscription.data,JSON.stringify({kind:'review-inbox'}),{TTL:1800,topic:'finder-inbox',timeout:10000,
        vapidDetails:{subject:process.env.FINDER_APP_ORIGIN,publicKey:key.publicKey,privateKey:key.privateKey}});
      accepted++;
    } catch(error) {
      if(error.statusCode===404||error.statusCode===410){
        await db.query('DELETE FROM finder_push_subscriptions WHERE id=$1',[subscription.id]);invalid++;
      } else failed++;
    }
  }
  // Never call a batch sent if every device rejected it. Retry while fresh and bounded.
  await db.query("UPDATE finder_outbox SET status=$2 WHERE id=ANY($1)",[pending.map(row=>row.id),failed||!accepted?'pending':'sent']);
  await db.query("UPDATE finder_outbox SET status='failed' WHERE attempts>=3 AND status='pending'");
  return report({status:failed||!accepted?'delivery_failed':'accepted_by_push_service',accepted_events:accepted?pending.length:0,
    devices:subscriptions.length,accepted_devices:accepted,failed_devices:failed,invalid_devices:invalid});
}

if(process.argv[1] && import.meta.url.endsWith(process.argv[1].replaceAll('\\','/'))){
  const db=new Pool({connectionString:process.env.FINDER_DATABASE_URL,max:1});
  try{const result=await deliver(db);console.log(JSON.stringify(result));if(result.status==='delivery_failed')process.exitCode=1;}
  catch{console.log('{"notifications":"failed"}');process.exitCode=1;}finally{await db.end();}
}
