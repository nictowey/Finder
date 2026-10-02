// Only subprocess lifecycle tests load this hook; no database or network is used.
import { registerHooks } from 'node:module';
const source = `
import { appendFileSync } from 'node:fs';
const trace = event => {
 appendFileSync(process.env.FINDER_SHUTDOWN_TRACE, JSON.stringify(event)+'\\n');
 if(process.env.FINDER_SHUTDOWN_FAIL_AT===event)throw new Error('synthetic cleanup failure');
};
export class Pool {
 async connect() {
  return {
   async query(sql) {
    if(sql.includes('current_schema()')){
     if(process.env.FINDER_SHUTDOWN_DELAY_SETUP==='1')await new Promise(resolve=>setTimeout(resolve,50));
     return {rows:[{name:'finder_check_'+'0'.repeat(32)}]};
    }
    if(sql.includes('pg_backend_pid()'))return {rows:[{pid:123}]};
    if(sql==='ROLLBACK'){
     trace('rollback');
    }
    return {rows:sql.startsWith('WITH locked')?[{result:'queued'}]:[]};
   },
   release(){trace('release');}
  };
 }
 async end(){trace('pool_end');}
}
`;
registerHooks({
 resolve(specifier, context, nextResolve) {
  if(specifier==='pg')return {url:'finder-test:pg',shortCircuit:true};
  return nextResolve(specifier,context);
 },
 load(url, context, nextLoad) {
  if(url==='finder-test:pg')return {format:'module',source,shortCircuit:true};
  return nextLoad(url,context);
 }
});
