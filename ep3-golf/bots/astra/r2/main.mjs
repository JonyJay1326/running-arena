import { createInterface } from 'node:readline';
import { predictFlat } from './physics.mjs';
import { choose } from './planner.mjs';
let config;
let stagnant=0;
let previousOutcome=null;
let motionUncertainty=12;
for await(const line of createInterface({input:process.stdin})) {
  try {
    const r=JSON.parse(line);
    if(r.config)config=r.config;
    let out;
    if(r.type==='init'){stagnant=0;previousOutcome=null;motionUncertainty=12;out={ok:true};}
    else if(r.type==='exam')out={predictions:r.questions.map(q=>({id:q.id,...predictFlat(q,r.ground,r.config.BODY.muzzleHeight)}))};
    else {
      r.objects=r.objects.map(o=>({...(config.OBJECTS[o.type]??(o.type==='pushToken'?{width:30,height:30}:{})),...o}));
      const changed=r.lastResult?.objectChanges?.some(c=>c.damage>0||c.after.inRecycle||Math.hypot(c.after.x-c.before.x,c.after.y-c.before.y)>12);
      stagnant=changed?0:stagnant+(r.lastResult?1:0);
      if(previousOutcome&&r.lastResult)for(const actual of r.objects)if(actual.type==='bug'&&!actual.inRecycle) {
        const predicted=previousOutcome.find(o=>o.id===actual.id);
        if(predicted){const error=Math.hypot(predicted.x-actual.x,predicted.y-actual.y);motionUncertainty=Math.min(45,Math.max(12,motionUncertainty*.7+error*.25));}
      }
      const result=choose(r,config,{stagnant,motionUncertainty});out=result.action;previousOutcome=result.debug.outcome??null;
    }
    process.stdout.write(JSON.stringify({reqId:r.reqId,...out})+'\n');
  } catch(e) {process.stderr.write(String(e.stack)+'\n');}
}
