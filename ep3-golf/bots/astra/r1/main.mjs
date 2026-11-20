import { createInterface } from 'node:readline';
import { mkdirSync,appendFileSync } from 'node:fs';
import { predictFlat } from './physics.mjs';
import { choose } from './planner.mjs';
let config;
let stagnant=0;
let logging=true;
try {mkdirSync('bot/memory',{recursive:true});}
catch(error){logging=false;process.stderr.write(`Memory directory unavailable: ${error.message}\n`);}
for await(const line of createInterface({input:process.stdin})) {
  try {
    const r=JSON.parse(line);
    if(r.config)config=r.config;
    let out;
    if(r.type==='init'){stagnant=0;out={ok:true};}
    else if(r.type==='exam')out={predictions:r.questions.map(q=>({id:q.id,...predictFlat(q,r.ground,r.config.BODY.muzzleHeight)}))};
    else {
      r.objects=r.objects.map(o=>({...(config.OBJECTS[o.type]??(o.type==='pushToken'?{width:30,height:30}:{})),...o}));
      const changed=r.lastResult?.objectChanges?.some(c=>c.damage>0||c.after.inRecycle||Math.hypot(c.after.x-c.before.x,c.after.y-c.before.y)>12);
      stagnant=changed?0:stagnant+(r.lastResult?1:0);
      const result=choose(r,config,{stagnant});out=result.action;
      if(logging)try {appendFileSync('bot/memory/turns.jsonl',JSON.stringify({observation:r,debug:result.debug,action:out})+'\n');}
      catch(error){logging=false;process.stderr.write(`Memory log unavailable: ${error.message}\n`);}
    }
    process.stdout.write(JSON.stringify({reqId:r.reqId,...out})+'\n');
  } catch(e) {process.stderr.write(String(e.stack)+'\n');}
}
