// Runs only in the secret-backed deploy workflow. Never print response bodies.
import { Pool } from 'pg';
import webpush from 'web-push';
import { appendFileSync } from 'node:fs';

const branch = 'https://console.neon.tech/api/v2/projects/billowing-queen-56301477/branches/br-soft-forest-b4fo15q2';
async function neon(path, method='GET', data) {
  const response = await fetch(branch+path, {method, headers:{Authorization:'Bearer '+process.env.NEON_API_KEY,'Content-Type':'application/json'}, body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(20000)});
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('Auth configuration request failed: '+response.status);
  return response.status===204?{}:response.json();
}
const db=new Pool({connectionString:process.env.FINDER_DATABASE_URL,max:1});
try {
  let auth=await neon('/auth');
  if (!auth) auth=await neon('/auth','POST',{auth_provider:'better_auth'});
  if (!auth?.base_url || !auth.base_url.startsWith('https://')) throw new Error('Auth URL unavailable');
  await neon('/auth/email_and_password','PATCH',{enabled:true,require_email_verification:true});
  await neon('/auth/config','PATCH',{name:'Finder private watchlist'});
  const origin=process.env.FINDER_APP_ORIGIN;
  const domains=await neon('/auth/domains');
  if (!JSON.stringify(domains).includes(new URL(origin).hostname)) await neon('/auth/domains','POST',{domain:origin,auth_provider:'better_auth'});
  const email=process.env.FINDER_OWNER_EMAIL?.trim().toLowerCase();
  if (email) {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('Invalid owner email');
    await db.query("INSERT INTO finder_private_settings(key,data) VALUES('owner_email',$1) ON CONFLICT(key) DO UPDATE SET data=excluded.data",[{email}]);
  }
  const existing=await db.query("SELECT data FROM finder_private_settings WHERE key='vapid'");
  if(!existing.rows.length) await db.query("INSERT INTO finder_private_settings(key,data) VALUES('vapid',$1) ON CONFLICT(key) DO NOTHING",[webpush.generateVAPIDKeys()]);
  appendFileSync(process.env.GITHUB_ENV,'FINDER_AUTH_URL='+auth.base_url+'\n');
  console.log('Private authentication and notification configuration ready.');
} catch {console.log('Private app configuration failed; no response payloads were logged.');process.exitCode=1;}
finally {await db.end();}
