import { trajectory } from './physics.mjs';
import { alive,center,decode,solid,surface,blocked,objectDistance,impact,carve,settleStatic,stances,actorPose } from './world.mjs';

const TYPES={single:{damage:120,radius:32},burst:{damage:65,radius:26},spread:{damage:55,radius:24},dig:{damage:30,radius:60}};
export const tuning={bugKick:28.5,bugLift:9.75,bugDrag:0.175,pushKick:18,pushDrag:0.09};
function regionDistance(p,r) {return Math.hypot(Math.max(0,r.x0-p.x,p.x-r.x1),Math.max(0,r.y0-p.y,p.y-r.y1));}
function desiredPoint(o,r,cells) {
  const c=center(o),rec=r.hole.recycle;
  if(!rec)return c;
  const candidates=[];
  for(let x=rec.x0+10;x<rec.x1;x+=16) {
    const feet=surface(cells,x,Math.max(0,rec.y0-14));
    const y=feet-14;
    if(y>=rec.y0&&y<=rec.y1)candidates.push({x,y,cost:Math.hypot(x-c.x,y-c.y)});
  }
  if(candidates.length)return candidates.sort((a,b)=>a.cost-b.cost)[0];
  return {x:Math.max(rec.x0+15,Math.min(rec.x1-15,c.x)),y:Math.max(rec.y0+8,Math.min(rec.y1-8,c.y))};
}
export function moveBody(cells,o,blast,kind) {
  const c=center(o),dx=c.x-blast.x,dy=c.y-blast.y,dist=Math.hypot(dx,dy);
  if(dist>=60)return {...o};
  const kick=(o.type==='bug'?tuning.bugKick:tuning.pushKick)*Math.max(0,1-dist/60);
  let vx=dx/Math.max(1,dist)*kick,vy=dy/Math.max(1,dist)*kick-(o.type==='bug'?tuning.bugLift:0);
  let x=c.x,y=c.y,r=o.radius??15,grounded=false;
  const dt=.22,drag=o.type==='bug'?tuning.bugDrag:tuning.pushDrag;
  for(let t=0;t<100;t+=dt) {
    vy+=dt;
    let nx=x+vx*dt,ny=y+vy*dt;
    if(nx< -r||nx>1600+r||ny>930)return {...o,x:nx,y:ny+r,removed:true};
    if(solid(cells,nx,ny+r)||solid(cells,nx-r*.65,ny+r*.75)||solid(cells,nx+r*.65,ny+r*.75)) {
      const floor=surface(cells,nx,Math.max(0,y-r));
      if(floor<y+r+12&&floor>=y-r) {ny=floor-r;vy=0;grounded=true;vx*=Math.exp(-drag*dt*8);}
      else {vx*=-.15;nx=x;}
    } else grounded=false;
    if(solid(cells,nx+Math.sign(vx)*r,ny)){vx*=-.1;nx=x;}
    x=nx;y=ny;
    if(grounded&&Math.abs(vx)<.12)break;
  }
  return {...o,x,y:y+r};
}
function estimateDamage(o,hit,damage,cells) {
  if(hit.objectId===o.id)return damage;
  const c=center(o),d=objectDistance(o,hit.x,hit.y);
  if(d>=30||blocked(cells,hit.x,hit.y,c.x,c.y,2))return 0;
  return damage*(1-d/30);
}
function evaluate(r,cells,objects,hit,kind,{mutate=false}={}) {
  if(hit.hit==='out')return -100;
  const spec=TYPES[kind];
  let score=0;
  for(const o of objects) {
    if(!alive(o))continue;
    const c=center(o),distance=Math.hypot(c.x-hit.x,c.y-hit.y);
    const damage=estimateDamage(o,hit,spec.damage,cells);
    if(o.type==='chicken') {if(damage>0)score-=140;continue;}
    if(o.type==='bug') {
      if(r.hole.goal==='tokens')continue;
      const want=desiredPoint(o,r,cells),targetDistance=regionDistance(c,r.hole.recycle);
      if(distance<60&&!blocked(cells,hit.x,hit.y,c.x,c.y,2)) {
        const next=moveBody(cells,o,hit,kind),nc=center(next);
        const nextDistance=regionDistance(nc,r.hole.recycle);
        if(next.removed)score-=120;
        else if(nextDistance===0) {
          const rec=r.hole.recycle,margin=Math.min(nc.x-rec.x0,rec.x1-nc.x,nc.y-rec.y0,rec.y1-nc.y);
          const uncertainty=12+Math.abs(nc.x-c.x)*.07;
          score+=165+Math.min(40,margin)*.45-Math.max(0,uncertainty-margin)*1.2;
        }
        else score+=Math.max(-65,Math.min(75,(targetDistance-nextDistance)*.45));
        const dot=((c.x-hit.x)*(want.x-c.x)+(c.y-hit.y)*(want.y-c.y))/Math.max(1,distance*Math.hypot(want.x-c.x,want.y-c.y));
        score+=dot*12*Math.max(0,1-distance/60);
        if(mutate)Object.assign(o,next,{inRecycle:nextDistance===0&&!next.removed});
      }
      if(hit.hit==='ground'&&distance<spec.radius+28) {
        if(hit.y>=c.y-4&&want.y>c.y+15)score+=25*(1-Math.min(1,distance/(spec.radius+30)));
      }
      continue;
    }
    if(r.hole.goal==='bug')continue;
    const hp=o.hp??1;
    if(damage>0) {
      const fraction=Math.min(1,damage/hp);
      score+=fraction*70+(damage>=hp?70:0);
      if(mutate){
        o.hp=Math.max(0,hp-damage);
        if(o.hp<=0){o.broken=true;o.removed=true;}
        else if(o.type==='pushToken')Object.assign(o,moveBody(cells,o,hit,kind));
      }
    }
    if(hit.hit==='ground'&&hit.y>o.y-10&&Math.abs(hit.x-o.x)<spec.radius+(o.width??30)/2)score+=kind==='dig'?17:5;
  }
  if(hit.hit==='ground') {
    let nearest=Infinity;
    for(const o of objects)if(alive(o)&&o.type!=='chicken'&&(r.hole.goal!=='bug'||o.type==='bug'))nearest=Math.min(nearest,Math.hypot(hit.x-o.x,hit.y-o.y+15));
    score+=Math.max(0,22-nearest*.08)*(kind==='dig'?1.6:0.7);
  }
  return score;
}
function goals(r,cells) {
  const points=[];
  for(const o of r.objects) {
    if(!alive(o)||o.type==='chicken'||(r.hole.goal==='bug'&&o.type!=='bug')||(r.hole.goal==='tokens'&&o.type==='bug'))continue;
    const c=center(o);
    if(o.type==='bug') {
      const want=desiredPoint(o,r,cells),dx=want.x-c.x,dy=want.y-c.y,len=Math.max(1,Math.hypot(dx,dy));
      for(const d of [16,24,32,42])for(const vertical of [-9,0,10])points.push({x:c.x-dx/len*d,y:c.y-dy/len*d+vertical});
      points.push({x:c.x,y:o.y+28});
      if(r.hole.recycle)points.push({x:want.x,y:want.y+16});
    } else {
      points.push(c,{x:o.x,y:o.y-4},{x:o.x,y:o.y-(o.height??30)},{x:o.x,y:o.y-(o.height??30)+4});
      points.push({x:o.x-(o.width??30)/2+3,y:c.y},{x:o.x+(o.width??30)/2-3,y:c.y});
      if(o.hp>180||o.type==='pushToken')points.push({x:o.x,y:o.y+30});
    }
  }
  return points;
}
export function roots(pose,point,angle,dir,wind) {
  const output=[];
  for(const rising of [false,true]) {
    function residual(power) {
      const path=trajectory(pose.mx,pose.my,angle+dir*pose.groundAngle,power,dir,wind);
      const disc=path.up*path.up+2*(point.y-pose.my);
      if(disc<0)return NaN;
      const t=path.up+(rising?-1:1)*Math.sqrt(disc);
      if(t<0)return NaN;
      return path.at(t).x-point.x;
    }
    let previous=NaN,last=0;
    for(let power=2;power<=100;power+=7) {
      const p=Math.min(power,100),value=residual(p);
      if(Number.isFinite(value)&&Number.isFinite(previous)&&value*previous<=0) {
        let lo=last,hi=p,flo=previous;
        for(let j=0;j<14;j++){const mid=(lo+hi)/2,v=residual(mid);if(!Number.isFinite(v)){lo=mid;continue;}if(v*flo<=0)hi=mid;else{lo=mid;flo=v;}}
        output.push((lo+hi)/2);
      }
      previous=value;last=p;
    }
  }
  return output;
}
export function simulateAction(r,pose,shot,originalCells=decode(r.terrain),reachable=null) {
  const ground=originalCells.slice(),objects=r.objects.map(o=>({...o})),hits=[];
  const spec=TYPES[shot.kind];
  let penalties=0,excavated=0;
  let projectiles=shot.kind==='spread'?[-6,0,6].map(offset=>({...shot,angle:shot.angle+offset})):Array.from({length:shot.kind==='burst'?3:1},()=>shot);
  if(shot.kind==='spread')projectiles.sort((a,b)=>impact(ground,objects,pose,a,r.hole.wind).time-impact(ground,objects,pose,b,r.hole.wind).time);
  let earliest=0;
  for(const fire of projectiles) {
    const hit=impact(ground,objects,pose,fire,r.hole.wind,{minTime:shot.kind==='spread'?earliest:0});
    hits.push(hit);earliest=hit.time;
    if(hit.hit==='out')continue;
    const forces=[];
    for(const o of objects)if(alive(o)) {
      const damage=estimateDamage(o,hit,spec.damage,ground),c=center(o);
      if(o.type==='chicken'&&damage>0) {o.stunned=true;o.removed=true;penalties++;continue;}
      if(o.type!=='bug'&&damage>0) {o.hp=Math.max(0,(o.hp??1)-damage);if(o.hp===0){o.broken=true;o.removed=true;continue;}}
      if((o.type==='bug'||o.type==='pushToken')&&Math.hypot(c.x-hit.x,c.y-hit.y)<60&&(hit.objectId===o.id||!blocked(ground,hit.x,hit.y,c.x,c.y,2)))forces.push(o);
    }
    excavated+=carve(ground,hit.x,hit.y,spec.radius);
    for(const o of forces) {
      Object.assign(o,moveBody(ground,o,hit,shot.kind));
      if(o.removed&&o.type==='bug'){Object.assign(o,r.objects.find(value=>value.id===o.id));penalties++;}
    }
    for(const o of objects)if(alive(o)) {
      const oldY=o.y;
      if(o.type==='bug') {
        const floor=surface(ground,o.x,o.y-4);
        if(floor>900){const original=r.objects.find(v=>v.id===o.id);Object.assign(o,original);penalties++;}
        else if(floor>o.y)o.y=floor;
        o.inRecycle=!!r.hole.recycle&&regionDistance(center(o),r.hole.recycle)===0;
      } else {
        settleStatic(ground,o);
        if(o.type==='chicken'&&o.removed&&oldY<900)penalties++;
      }
    }
  }
  let score=-160*penalties,progress=0;
  for(const before of r.objects) {
    if(!alive(before)||before.type==='chicken')continue;
    const after=objects.find(o=>o.id===before.id);
    if(before.type==='bug') {
      if(r.hole.goal==='tokens')continue;
      const c=center(before),nc=center(after),rec=r.hole.recycle;
      const oldDistance=regionDistance(c,rec),newDistance=regionDistance(nc,rec);
      if(after.inRecycle) {
        const margin=Math.min(nc.x-rec.x0,rec.x1-nc.x,nc.y-rec.y0,rec.y1-nc.y);
        const uncertainty=12+Math.abs(nc.x-c.x)*.07;
        score+=165+Math.min(40,margin)*.45-Math.max(0,uncertainty-margin)*1.2;
        progress++;
      } else score+=Math.max(-85,Math.min(85,(oldDistance-newDistance)*.5));
    } else if(r.hole.goal!=='bug') {
      const hp=before.hp??1;
      const damage=after.removed||after.broken?hp:Math.max(0,hp-(after.hp??1));
      score+=70*damage/hp+(after.removed||after.broken?70:0);
      if(alive(after)&&after.y>before.y)score+=Math.min(75,100*(after.y-before.y)/Math.max(24,900-before.y));
      progress+=damage/hp;
    }
  }
  let digging=0;
  if(excavated>0)for(const hit of hits)if(hit.hit!=='out') {
    for(const o of objects)if(alive(o)&&o.type!=='chicken'&&(r.hole.goal!=='bug'||o.type==='bug')) {
      const distance=objectDistance(o,hit.x,hit.y);
      const original=r.objects.find(value=>value.id===o.id),target=center(original);
      if((!reachable||!reachable.has(o.id))&&blocked(originalCells,hit.x,hit.y,target.x,target.y,2))digging=Math.max(digging,(8+Math.max(0,24-distance*.08))*(spec.radius/32));
    }
  }
  score+=Math.min(32,digging);
  const tokensDone=objects.every(o=>!['token','bigToken','pushToken'].includes(o.type)||!alive(o));
  const bugDone=objects.every(o=>o.type!=='bug'||o.inRecycle);
  const completed=(r.hole.goal==='bug'||tokensDone)&&(r.hole.goal==='tokens'||bugDone);
  if(completed)score+=800;
  else if(r.strokes+1+penalties>=r.hole.cap)score-=200;
  if(shot.kind==='burst'||shot.kind==='spread') {
    let movementRisk=0;
    for(const before of r.objects)if(alive(before)&&before.type==='pushToken') {
      const after=objects.find(o=>o.id===before.id);
      movementRisk+=Math.abs(after.x-before.x)*.16+Math.abs(after.y-before.y)*.08;
    }
    score-=Math.min(35,movementRisk);
  }
  // Prefer simpler shots when the modeled outcome is equivalent.
  score-=(shot.kind==='spread'?.5:shot.kind==='burst'?.25:0);
  return {score,hits,objects,ground,penalties,excavated,progress,completed};
}
function reachableCount(r,cells,pose) {
  let count=0;
  for(const o of r.objects) {
    if(!alive(o)||o.type==='chicken'||(r.hole.goal==='bug'&&o.type!=='bug'))continue;
    const point=center(o),dir=point.x>=pose.mx?1:-1;
    let found=false;
    for(const angle of [15,30,45,60,75,85]) {
      for(const power of roots(pose,point,angle,dir,r.hole.wind)) {
        const hit=impact(cells,r.objects,pose,{angle,power,dir},r.hole.wind);
        if(hit.objectId===o.id||objectDistance(o,hit.x,hit.y)<12){found=true;break;}
      }
      if(found)break;
    }
    if(found)count++;
  }
  return count;
}
function flightAlternative(r,config,cells,currentPose,begin) {
  const currentReach=reachableCount(r,cells,currentPose),positions=[];
  for(const o of r.objects)if(alive(o)&&o.type!=='chicken')for(const dx of [-250,-140,140,250])positions.push(o.x+dx);
  for(const dx of [-600,-400,-250,250,400,600])positions.push(currentPose.x+dx);
  for(let x=80;x<1550;x+=80)if(surface(cells,x)<currentPose.feet-40)positions.push(x);
  const seen=new Set();let best=null;
  const flightPose={...currentPose,groundAngle:0};
  for(const x of positions) {
    if(x<35||x>1565||Math.abs(x-currentPose.x)<150||seen.has(Math.round(x/20)))continue;
    seen.add(Math.round(x/20));
    const y=surface(cells,x),left=surface(cells,x-14),right=surface(cells,x+14);
    if(y>896||Math.abs(left-y)>8||Math.abs(right-y)>8)continue;
    if(solid(cells,x,y-40)||solid(cells,x-13,y-50)||solid(cells,x+13,y-50))continue;
    const landing=actorPose({x,y:y-config.BODY.footOffset,groundAngle:Math.atan2(left-right,28)*180/Math.PI,grounded:true},config.BODY);
    const reach=reachableCount(r,cells,landing);
    if(reach<=currentReach)continue;
    const dir=x>=flightPose.mx?1:-1;
    for(const angle of [25,35,45,55,60,65,70,75,80,85])for(const power of roots(flightPose,{x,y},angle,dir,r.hole.wind)) {
      const hit=impact(cells,r.objects,flightPose,{angle,power,dir},r.hole.wind,{ignoreObjects:true});
      if(hit.hit!=='ground'||Math.abs(hit.x-x)>8||Math.abs(hit.y-y)>5)continue;
      const score=(reach-currentReach)*90-Math.abs(x-currentPose.x)*.01;
      if(!best||score>best.score)best={action:{fly:{angle,power,dir}},score,hit,landing,currentReach,reach};
    }
    if(performance.now()-begin>820)break;
  }
  return best;
}
export function choose(r,config,context={}) {
  const begin=performance.now(),cells=decode(r.terrain),poses=stances(r,cells,config.BODY),points=goals(r,cells);
  const candidates=[],seen=new Set();
  function add(pose,shot) {
    if(shot.power<0||shot.power>100||shot.angle<0||shot.angle>85)return;
    const key=[Math.round(pose.x),shot.dir,shot.angle.toFixed(2),shot.power.toFixed(2)].join(':');
    if(seen.has(key))return;seen.add(key);
    const hit=impact(cells,r.objects,pose,shot,r.hole.wind);
    for(const kind of ['single','burst','spread','dig']) {
      const score=evaluate(r,cells,r.objects,hit,kind);
      candidates.push({pose,shot:{...shot,kind},hit,rough:(kind==='burst'||kind==='spread')?score*2.7:score});
    }
  }
  const orderedPoses=poses.sort((a,b)=>Math.abs(a.x-r.actor.x)-Math.abs(b.x-r.actor.x));
  const edgePoses=[orderedPoses[0],poses.reduce((a,b)=>a.x<b.x?a:b),poses.reduce((a,b)=>a.x>b.x?a:b)];
  for(const pose of [...new Set(edgePoses)])for(const dir of [-1,1])for(let angle=0;angle<=85;angle+=5) {
    for(const power of [40,70,100])add(pose,{angle,power,dir});
  }
  outer:for(const pose of orderedPoses)for(const point of points) {
    const direction=point.x>=pose.mx?1:-1;
    for(let angle=0;angle<=85;angle+=5) {
      for(const power of roots(pose,point,angle,direction,r.hole.wind))add(pose,{angle,power,dir:direction});
      if(performance.now()-begin>410)break outer;
    }
  }
  if(!candidates.length)add(poses[0],{angle:45,power:70,dir:1});
  candidates.sort((a,b)=>b.rough-a.rough);
  const reachable=new Set(candidates.filter(candidate=>candidate.hit.objectId!==undefined).map(candidate=>candidate.hit.objectId));
  const finalists=[];
  function full(candidate) {
    const {pose,shot}=candidate;
    const result=simulateAction(r,pose,shot,cells,reachable);
    return {...candidate,hits:result.hits,score:result.score,outcome:result.objects,progress:result.progress};
  }
  const shortlist=[];
  for(const kind of ['single','burst','spread','dig']) {
    const selected=new Set(),groups=new Set(),pool=candidates.filter(c=>c.shot.kind===kind);
    for(const candidate of pool) {
      const {hit,pose,shot}=candidate;
      const group=[hit.objectId??'terrain',Math.floor(hit.x/40),Math.floor(hit.y/20),Math.floor(shot.angle/15),Math.floor(pose.x/64)].join(':');
      if(groups.has(group))continue;
      groups.add(group);selected.add(candidate);
      if(selected.size>=42)break;
    }
    for(const o of r.objects)if(alive(o)) {
      const directGroups=new Set();
      for(const candidate of pool)if(candidate.hit.objectId===o.id) {
        const group=`${Math.floor(candidate.hit.y/8)}:${Math.floor(candidate.shot.angle/15)}`;
        if(directGroups.has(group))continue;
        directGroups.add(group);selected.add(candidate);
        if(directGroups.size>=16)break;
      }
    }
    shortlist.push(...selected);
  }
  for(const candidate of shortlist) {finalists.push(full(candidate));if(performance.now()-begin>650)break;}
  finalists.sort((a,b)=>b.score-a.score);
  for(const base of finalists.slice(0,8)) {
    for(const da of [-1,0,1])for(const dp of [-.7,0,.7]) {
      const shot={...base.shot,angle:Math.max(0,Math.min(85,base.shot.angle+da)),power:Math.max(0,Math.min(100,base.shot.power+dp))};
      const hit=impact(cells,r.objects,base.pose,shot,r.hole.wind);
      finalists.push(full({pose:base.pose,shot,hit}));
    }
    if(performance.now()-begin>780)break;
  }
  finalists.sort((a,b)=>b.score-a.score);
  for(const base of finalists.slice(0,3)) {
    if(performance.now()-begin>730)break;
    for(const da of [-.2,0,.2])for(const dp of [-.12,0,.12]) {
      const shot={...base.shot,angle:Math.max(0,Math.min(85,base.shot.angle+da)),power:Math.max(0,Math.min(100,base.shot.power+dp))};
      const hit=impact(cells,r.objects,base.pose,shot,r.hole.wind);
      finalists.push(full({pose:base.pose,shot,hit}));
    }
  }
  finalists.sort((a,b)=>b.score-a.score);
  const pool=finalists.slice(0,14),poolGroups=new Set(pool.map(c=>`${c.shot.kind}:${c.hit.objectId??Math.floor(c.hit.x/100)}`));
  for(const candidate of finalists) {
    const group=`${candidate.shot.kind}:${candidate.hit.objectId??Math.floor(candidate.hit.x/100)}`;
    if(!poolGroups.has(group)){pool.push(candidate);poolGroups.add(group);}
    if(pool.length>=26)break;
  }
  const hasMotion=r.objects.some(o=>alive(o)&&(o.type==='pushToken'||o.type==='bug'&&r.hole.goal!=='tokens'));
  if(hasMotion)for(const candidate of pool) {
    if(performance.now()-begin>805)break;
    const saved={...tuning},scores=[candidate.score];
    try {
      for(const direction of [-1,1]) {
        Object.assign(tuning,{bugKick:saved.bugKick*(1+direction*.065),bugLift:saved.bugLift+direction*.4,pushKick:saved.pushKick*(1+direction*.15),pushDrag:saved.pushDrag-direction*.01});
        scores.push(simulateAction(r,candidate.pose,candidate.shot,cells,reachable).score);
      }
    } finally {Object.assign(tuning,saved);}
    candidate.nominalScore=candidate.score;
    candidate.score=Math.min(...scores)*.65+scores.reduce((sum,value)=>sum+value,0)/scores.length*.35;
    candidate.range=[Math.min(...scores),Math.max(...scores)];
  }
  pool.sort((a,b)=>b.score-a.score);
  const best=pool[0],action={attack:best.shot};
  if(r.fly>0&&(best.score<35||context.stagnant>=2)&&performance.now()-begin<650) {
    const alternate=flightAlternative(r,config,cells,actorPose(r.actor,config.BODY),begin);
    if(alternate)return {action:alternate.action,debug:{version:3,reason:'flight improves target access',...alternate,ms:performance.now()-begin}};
  }
  if(r.hole.teeActive&&Math.abs(best.pose.x-r.actor.x)>.1)action.moveX=best.pose.x;
  return {action,debug:{version:5,score:best.score,nominalScore:best.nominalScore,scoreRange:best.range,predicted:best.hits??[best.hit],outcome:best.outcome,pose:best.pose,ms:performance.now()-begin,candidates:candidates.length,tuning:{...tuning}}};
}
