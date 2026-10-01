/// <reference types="emscripten" />
import test from 'node:test';
import { PGlite } from '@electric-sql/pglite';
import { checkNotificationPolicy } from '../scripts/check_notification_policy.mjs';

test('real SQL accepts current worker policy and preserves all notification safety gates',async()=>{
 const db=new PGlite();
 try{await checkNotificationPolicy(db);}finally{await db.close();}
});
