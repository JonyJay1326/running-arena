import { createInterface } from 'node:readline';

const RAD = Math.PI / 180;
let config = null;
let previousSolidCount = null;
let stalledImpacts = [];
let stalledTurns = 0;
let initialBug = null;
let usedReset = false;
let bugScale = 1;
let tunnelPoint = null;
let tunnelDetours = 0;
let tunnelPlan = null;
function unpack(t) {
  const a = new Uint8Array(t.width * t.height); let pos = 0;
  for (const [v,n] of t.rle) { if (v) a.fill(1,pos,pos+n); pos += n; }
  return {a,w:t.width,h:t.height,c:t.cell};
}
function solid(g,x,y) {
  if (x < 0 || x >= g.w*g.c || y >= g.h*g.c) return false;
  if (y < 0) return false;
  return !!g.a[(y/g.c|0)*g.w+(x/g.c|0)];
}
function ground(g,x,start=0) {
  const xx=Math.max(0,Math.min(g.w-1,x/g.c|0));
  for(let y=Math.max(0,start/g.c|0);y<g.h;y++) if(g.a[y*g.w+xx] && (y===0 || !g.a[(y-1)*g.w+xx])) return y*g.c;
  return null;
}
function support(g,x,min) {
  let values=[];
  for(let dx=-12;dx<=12;dx+=4) {
    let y=ground(g,x+dx,min);
    if(y===null)return null;
    values.push(y);
  }
  let left=ground(g,x-24,min),right=ground(g,x+24,min);
  if(left===null||right===null||Math.abs(left-right)>48) {left=values[0];right=values[6];return {foot:Math.min(...values),angle:-Math.atan2(right-left,24)/RAD};}
  return {foot:Math.min(...values),angle:-Math.atan2(right-left,48)/RAD};
}
function positions(r,g) {
  const p=[{x:r.actor.x, foot:r.actor.y+config.BODY.footOffset, angle:r.actor.groundAngle}];
  if(!r.hole.teeActive || !r.hole.tee) return p;
  const tee=r.hole.tee, lo=tee.x0+1, hi=tee.x1-1;
  const min=tee.minFeet ?? 0;
  let reference=support(g,r.actor.x,min);
  if(!reference)return p;
  let footCorrection=r.actor.y+config.BODY.footOffset-reference.foot;
  let angleCorrection=r.actor.groundAngle-reference.angle;
  if(Math.abs(footCorrection)>20||Math.abs(angleCorrection)>30)return p;
  for(let j=0;j<=8;j++) {
    let x=Math.round(lo+(hi-lo)*j/8); if(Math.abs(x-r.actor.x)<6) continue;
    let y=ground(g,x,min), l=ground(g,x-12,min), rr=ground(g,x+12,min);
    if(y==null||l==null||rr==null||Math.abs(l-rr)>22 || Math.abs(y-l)>13||Math.abs(y-rr)>13) continue;
    if(solid(g,x,y-40)||solid(g,x-10,y-10)||solid(g,x+10,y-10)) continue;
    let candidate=support(g,x,min);
    if(!candidate)continue;
    let angle=candidate.angle+angleCorrection;
    if(Math.abs(angle)>config.BODY.maxTilt)continue;
    p.push({x,foot:candidate.foot+footCorrection,angle});
  }
  return p;
}
function live(o) {return !o.removed && !o.broken && !o.stunned && !o.inRecycle;}
function objHit(o,x,y) {
  if(o.type==='bug') {let dx=x-o.x,dy=y-(o.y-(o.radius||14)); return dx*dx+dy*dy <= (o.radius||14)**2;}
  let w=o.width||({token:28,bigToken:44,pushToken:30,chicken:26}[o.type]||28), h=o.height||({token:28,bigToken:56,pushToken:30,chicken:30}[o.type]||28);
  return x>=o.x-w/2&&x<=o.x+w/2&&y>=o.y-h&&y<=o.y;
}
function center(o) {let h=o.type==='bug'?(o.radius||14)*2:(o.height||({token:28,bigToken:56,pushToken:30,chicken:30}[o.type]||28)); return [o.x,o.y-h/2];}
function nearest(o,x,y) {
  let w=o.width||({token:28,bigToken:44,pushToken:30,chicken:26}[o.type]||28);
  let h=o.height||({token:28,bigToken:56,pushToken:30,chicken:30}[o.type]||28);
  return [Math.max(o.x-w/2,Math.min(o.x+w/2,x)),Math.max(o.y-h,Math.min(o.y,y))];
}
function bugTravel(dx,dy,kind) {
  let d=Math.hypot(dx,dy);
  let travel=400*Math.pow(Math.min(1.5,Math.abs(dx)/14),1.64)*Math.exp(.128*(dy+.6))*Math.sign(-dx);
  if(d>15) travel*=Math.max(0,(30-d)/15);
  if(kind==='dig') travel*=1.2;
  return Math.max(-1000,Math.min(1000,travel));
}
function trace(g,objects,start,angle,power,dir,wind,step=0.06,plane=false) {
  let a=(angle+(plane?0:dir*start.angle))*RAD, u=.3465*power;
  let vx=dir*u*Math.cos(a), vy=-u*Math.sin(a);
  let theta=start.angle*RAD, x=start.x-Math.sin(theta)*config.BODY.muzzleHeight, y=start.foot-Math.cos(theta)*config.BODY.muzzleHeight;
  let prevX=x,prevY=y;
  for(let t=0;t<85;t+=step) {
    prevX=x;prevY=y;
    let ax=.0375*wind*(y<400?1.35:1);
    x+=vx*step+ax*step*step/2; y+=vy*step+step*step/2; vx+=ax*step; vy+=step;
    if(x<0||x>=1600||y>=900) return {x,y,hit:'out'};
    if(y < 0) continue;
    for(const o of objects) if(objHit(o,x,y)) return {x,y,hit:'object',object:o};
    if(solid(g,x,y)) return {x,y,hit:'ground',prevX,prevY};
  }
  return {x,y,hit:'out'};
}
function visibility(g,x,y,tx,ty) {
  let d=Math.hypot(tx-x,ty-y), n=Math.ceil(d/3);
  for(let i=1;i<n;i++) if(solid(g,x+(tx-x)*i/n,y+(ty-y)*i/n))return false;
  return true;
}
function scoreImpact(r,g,objects,impact,kind) {
  if(impact.hit==='out') return -1000;
  let score=0, bestProgress=0;
  for(const o of objects) {
    let [cx,cy]=center(o), dx=impact.x-cx,dy=impact.y-cy, d=Math.hypot(dx,dy);
    if(o.type==='chicken') {
      let [nx,ny]=nearest(o,impact.x,impact.y), edge=Math.hypot(impact.x-nx,impact.y-ny);
      if(edge<35) {
        let cost=r.strokes>=r.hole.cap-2?150:300;
        score-=cost*Math.min(1,(35-edge)/7);
      }
      continue;
    }
    const bug=o.type==='bug';
    if(bug && (r.hole.goal==='tokens'||!r.hole.recycle)) continue;
    if(!bug && !['token','bigToken','pushToken'].includes(o.type)) continue;
    let visible=d<80 && visibility(g,impact.x,impact.y,cx,cy);
    if(bug) {
      let bin=r.hole.recycle, target=(bin.x0+bin.x1)/2, direction=Math.sign(target-o.x)||1;
      let toward= -direction*dx;
      let margin=Math.min(8,(bin.x1-bin.x0)/4);
      let verticalGoal=cx>=bin.x0+margin && cx<=bin.x1-margin && cy<bin.y0-10;
      if(d<30 && visible) {
        // Empirical flat-ground estimate; impact height strongly affects the slide.
        let travel=bugTravel(dx,dy,kind)*bugScale;
        let projected=o.x+travel;
        let before=Math.abs(target-o.x), quality=0;
        let landingGround=ground(g,projected,Math.max(0,o.y-4));
        let factors=landingGround!==null && landingGround>o.y+40 ? [.6,1,1.6] : [.7,1,1.3];
        for(const factor of factors) {
          let landing=o.x+travel*factor;
          quality+=Math.max(-160,Math.min(150,(before-Math.abs(target-landing))*0.6));
          if(landing>=bin.x0+5 && landing<=bin.x1-5) quality+=110;
        }
        score+=quality/3*(verticalGoal ? 0.2 : 1);
        if(projected<30 || projected>1570) score-=300;
        if(toward<=0) score-=30;
      }
      if(verticalGoal && kind==='dig' && d<95) {
        let cells=0;
        for(let xx=o.x-16;xx<=o.x+16;xx+=8) for(let yy=o.y;yy<=o.y+64;yy+=8) {
          if((xx-impact.x)**2+(yy-impact.y)**2<58**2 && solid(g,xx,yy)) cells++;
        }
        score+=Math.min(240,cells*7);
        if(cells) {
          // A dig blast can push the Bug even when its centre is outside the damage radius.
          let push=d<75 ? -dx/Math.max(1,d)*260*Math.max(0,1-d/75) : 0;
          let predictedX=o.x+push;
          for(const factor of [.3,1,2.5]) {
            let x=o.x+push*factor;
            if(x<bin.x0 || x>bin.x1)score-=30;
          }
          let floor=null;
          for(let yy=o.y;yy<=Math.min(899,bin.y1+(o.radius||14));yy+=4) {
            if([-12,0,12].some(off=>solid(g,predictedX+off,yy) && (predictedX+off-impact.x)**2+(yy-impact.y)**2>=60**2)) {floor=yy;break;}
          }
          if(floor===null)score-=130;
          else if(floor-(o.radius||14)>=bin.y0 && floor-(o.radius||14)<=bin.y1)score+=80;
        }
      }
      let desiredX=o.x-direction*18, desiredY=cy;
      bestProgress=Math.max(bestProgress, 12/(1+Math.hypot(impact.x-desiredX,impact.y-desiredY)/50));
    } else {
      let hp=o.hp??1, weight=o.type==='token'?150:110;
      let [nx,ny]=nearest(o,impact.x,impact.y), edgeDistance=Math.hypot(impact.x-nx,impact.y-ny);
      let canDamage=edgeDistance<30 && visibility(g,impact.x,impact.y,nx,ny);
      if(canDamage) {
        let burstDamage=195;
        if(kind==='burst' && o.type==='pushToken') {
          let w=o.width||30, h=o.height||30;
          let topHit=Math.abs(impact.x-o.x)<w*.32 && impact.y<o.y-h+6;
          if(!topHit) burstDamage=65;
          else if(hp>120) score+=25;
        }
        let raw=(kind==='burst'?burstDamage:kind==='spread'?165:kind==='dig'?30:120)*Math.max(0,1-edgeDistance/30);
        score+=weight*Math.min(1,raw/Math.max(1,hp)) + (raw>=hp?55:0);
      }
      let progressDistance=edgeDistance;
      if(kind==='dig' && impact.hit==='ground' && impact.y>o.y+25) {
        progressDistance=Math.hypot(impact.x-nx,2.5*(impact.y-ny));
      }
      bestProgress=Math.max(bestProgress, 14/(1+progressDistance/50));
    }
  }
  if(impact.hit==='ground') score+=bestProgress*(kind==='dig'?1.7:0.7);
  else score+=bestProgress;
  if(kind==='dig' && impact.hit==='ground' && tunnelPoint && r.hole.goal==='tokens') {
    let targets=objects.filter(o=>['token','bigToken','pushToken'].includes(o.type));
    let target=targets.reduce((a,b)=>!a||Math.hypot(b.x-tunnelPoint.x,b.y-tunnelPoint.y)<Math.hypot(a.x-tunnelPoint.x,a.y-tunnelPoint.y)?b:a,null);
    if(target) {
      let vx=target.x-tunnelPoint.x,vy=(target.y-(target.height||30)/2)-tunnelPoint.y, len=Math.hypot(vx,vy)||1;
      let dx=impact.x-tunnelPoint.x,dy=impact.y-tunnelPoint.y;
      let along=(dx*vx+dy*vy)/len, lateral=Math.abs(dx*vy-dy*vx)/len;
      if(along>3 && along<150 && lateral<65) score+=65*Math.min(1,along/35)*(1-lateral/65);
    }
  }
  for(const p of stalledImpacts) {
    const distance=Math.hypot(impact.x-p.x,impact.y-p.y);
    if(distance<35) score-=40*(1-distance/35);
  }
  return score;
}
function burstRisk(g,objects,impact,pos,angle,power,dir,wind) {
  if(impact.hit!=='object'||!impact.object||!['token','bigToken','pushToken'].includes(impact.object.type))return 0;
  if((impact.object.hp??1)>130)return 0;
  let chickens=objects.filter(o=>o.type==='chicken'&&Math.hypot(o.x-impact.x,o.y-impact.y)<160);
  if(!chickens.length)return 0;
  let next=trace(g,objects.filter(o=>o!==impact.object),pos,angle,power,dir,wind);
  for(const chicken of chickens) {
    let [x,y]=nearest(chicken,next.x,next.y), d=Math.hypot(x-next.x,y-next.y);
    if(d<55)return 400*(55-d)/55+160;
  }
  return 0;
}
function route(g,objects,target,pos,angle,power,dir,wind) {
  const a=(angle+dir*pos.angle)*RAD, step=.16, u=.3465*power;
  let vx=dir*u*Math.cos(a), vy=-u*Math.sin(a), theta=pos.angle*RAD;
  let x=pos.x-Math.sin(theta)*config.BODY.muzzleHeight, y=pos.foot-Math.cos(theta)*config.BODY.muzzleHeight;
  let first=null, thickness=0, segments=0, wasSolid=false;
  for(let t=0;t<85;t+=step) {
    let ax=.0375*wind*(y<400?1.35:1), oldX=x,oldY=y;
    x+=vx*step+ax*step*step/2;y+=vy*step+step*step/2;vx+=ax*step;vy+=step;
    if(x<0||x>=1600||y>=900)return null;
    if(y<0)continue;
    let material=solid(g,x,y);
    if(material) {
      if(!first)first={x,y,hit:'ground'};
      if(!wasSolid)segments++;
      thickness+=Math.hypot(x-oldX,y-oldY);
    }
    wasSolid=material;
    for(const o of objects) if(objHit(o,x,y)) {
      if(o.id!==target.id)return null;
      return {first,thickness,segments};
    }
  }
  return null;
}
function planTunnel(r,g,objects,positions,wind,deadline) {
  const targets=objects.filter(o=>['token','bigToken','pushToken'].includes(o.type));
  if(!targets.length)return null;
  function test(plan) {
    let target=targets.find(o=>o.id===plan.targetId);
    if(!target)return null;
    let candidate=positions.find(p=>Math.abs(p.x-plan.pos.x)<1);
    if(!candidate)return null;
    let info=route(g,objects,target,candidate,plan.angle,plan.power,plan.dir,wind);
    if(!info?.first)return null;
    let safe=scoreImpact(r,g,objects,info.first,'dig');
    if(safe<0)return null;
    return {...plan,pos:candidate,info};
  }
  if(tunnelPlan) {
    let saved=test(tunnelPlan);
    if(saved)return saved;
    tunnelPlan=null;
  }
  let choices=positions.length<=3?positions:[positions[0],positions[1],positions[positions.length-1]],best=null;
  searchRoute: for(const pos of choices) for(const target of targets) for(const dir of [-1,1]) {
    if((target.x-pos.x)*dir < -100)continue;
    for(let angle=0;angle<=85;angle+=2.5) {
      if(performance.now()>deadline-45)break searchRoute;
      for(let power=20;power<=100;power+=2) {
      let info=route(g,objects,target,pos,angle,power,dir,wind);
      if(!info?.first)continue;
      if(info.thickness>Math.max(160,(r.hole.cap-r.strokes)*100))continue;
      let safe=scoreImpact(r,g,objects,info.first,'dig');
      if(safe<0)continue;
      let cost=info.thickness+info.segments*30+Math.hypot(info.first.x-target.x,info.first.y-target.y)*.01;
      if(!best||cost<best.cost)best={pos,angle,power,dir,targetId:target.id,info,cost};
      }
    }
  }
  tunnelPlan=best;
  return best;
}
function choose(r,nested=false,deadline=performance.now()+800) {
  const solidCount=r.terrain.rle.reduce((n,[v,count])=>n+(v?count:0),0);
  if(!nested && previousSolidCount!==null && r.lastResult) {
    for(const c of r.lastResult.objectChanges||[]) {
      if(c.before?.type!=='bug' || !c.after || Math.abs(c.before.y-c.after.y)>8) continue;
      let p=(r.lastResult.impacts||[]).find(p=>p.objectId===c.before.id && p.kind==='single');
      if(!p)continue;
      let expected=bugTravel(p.x-c.before.x,p.y-(c.before.y-(c.before.radius||14)),p.kind);
      let actual=c.after.x-c.before.x;
      if(Math.abs(expected)>35 && actual*expected>0) {
        let ratio=Math.max(.7,Math.min(1.4,actual/expected));
        bugScale=.5*bugScale+.5*ratio;
      }
    }
    const changed=(r.lastResult.objectChanges||[]).some(({before,after})=>before && after &&
      ((before.hp??0)>(after.hp??0) || Math.hypot(before.x-after.x,before.y-after.y)>3 || (!before.inRecycle&&after.inRecycle)));
    const opened=previousSolidCount-solidCount>8;
    if(changed){tunnelPoint=null;tunnelDetours=0;tunnelPlan=null;}
    else if(opened && (r.lastResult.impacts||[]).some(p=>p.kind==='dig')) {
      const p=(r.lastResult.impacts||[]).find(p=>p.kind==='dig' && Number.isFinite(p.x)&&Number.isFinite(p.y));
      if(p) {
        let targets=r.objects.filter(o=>live(o)&&['token','bigToken','pushToken'].includes(o.type));
        let distance=q=>Math.min(...targets.map(o=>Math.hypot(o.x-q.x,o.y-(o.height||30)/2-q.y)));
        if(!tunnelPoint||distance(p)<distance(tunnelPoint)-5||tunnelDetours>=2) {
          tunnelPoint={x:p.x,y:p.y};tunnelDetours=0;
        } else tunnelDetours++;
      }
    }
    if(!changed && !opened) {
      stalledTurns++;
      for(const p of r.lastResult.impacts||[]) if(Number.isFinite(p.x)&&Number.isFinite(p.y)) stalledImpacts.push({x:p.x,y:p.y});
    }
    if(changed || opened) {stalledImpacts=[];stalledTurns=0;}
    stalledImpacts=stalledImpacts.slice(-5);
  }
  if(!nested) previousSolidCount=solidCount;
  let bug=r.objects.find(o=>o.type==='bug' && live(o)), bin=r.hole.recycle;
  let otherTargets=r.objects.some(o=>live(o)&&['token','bigToken','pushToken'].includes(o.type));
  if(!nested && stalledTurns>=2 && !usedReset && initialBug && bug && bin && !otherTargets && r.strokes+2<r.hole.cap) {
    let target=(bin.x0+bin.x1)/2;
    if(Math.abs(bug.x-target)>Math.abs(initialBug.x-target)+80) {
      usedReset=true;stalledTurns=0;stalledImpacts=[];
      return {unplayable:true};
    }
  }
  const g=unpack(r.terrain), objs=r.objects.filter(live), ps=positions(r,g), wind=r.hole.wind||0;
  let best=null, candidates=[];
  function offer(candidate) {
    if(candidates.length<20 || candidate.score>candidates[candidates.length-1].score) {
      candidates.push(candidate);
      candidates.sort((a,b)=>b.score-a.score);
      if(candidates.length>20)candidates.pop();
    }
  }
  // Search a fixed grid; simulated collision and blast scoring account for walls and bystanders.
  searchShots: for(const pos of ps) for(const dir of [-1,1]) for(let angle=0;angle<=85;angle+=2.5) {
    if(performance.now()>deadline-120)break searchShots;
    for(let power=20;power<=100;power+=2) {
    let impact=trace(g,objs,pos,angle,power,dir,wind);
    if(impact.hit==='out') continue;
    for(const kind of ['single','burst','dig']) {
      let s=scoreImpact(r,g,objs,impact,kind);
      if(kind==='burst')s-=burstRisk(g,objs,impact,pos,angle,power,dir,wind);
      if(kind==='dig' && impact.hit==='object') s-=20;
      if(kind==='burst' && impact.hit==='ground') s-=1;
      offer({pos,dir,angle,power,kind,score:s});
    }
    }
  }
  for(const seed of candidates.slice()) {
    for(const da of [-2,-1,0,1,2]) for(const dp of [-2,0,2]) {
      if(!da&&!dp) continue;
      let angle=seed.angle+da, power=seed.power+dp;
      if(angle<0||angle>85||power<0||power>100)continue;
      let impact=trace(g,objs,seed.pos,angle,power,seed.dir,wind);
      let score=scoreImpact(r,g,objs,impact,seed.kind);
      if(seed.kind==='burst')score-=burstRisk(g,objs,impact,seed.pos,angle,power,seed.dir,wind);
      if(seed.kind==='dig' && impact.hit==='object') score-=20;
      if(seed.kind==='burst' && impact.hit==='ground') score-=1;
      offer({...seed,angle,power,score});
    }
  }
  let bestScore=-Infinity;
  for(const candidate of candidates) {
    let {pos,dir,angle,power,kind}=candidate;
    let scores=[candidate.score];
    for(const [da,dp] of [[-.35,0],[.35,0],[0,-.5],[0,.5]]) {
      let impact=trace(g,objs,pos,angle+da,Math.max(0,Math.min(100,power+dp)),dir,wind);
      let score=scoreImpact(r,g,objs,impact,kind);
      if(kind==='burst')score-=burstRisk(g,objs,impact,pos,angle+da,Math.max(0,Math.min(100,power+dp)),dir,wind);
      scores.push(score);
    }
    if(Math.abs(pos.x-r.actor.x)>2 && Math.abs(r.actor.groundAngle)>3) {
      for(const tiltError of [-2,2]) {
        let altered={...pos,angle:pos.angle+tiltError};
        let impact=trace(g,objs,altered,angle,power,dir,wind);
        scores.push(scoreImpact(r,g,objs,impact,kind));
      }
    }
    let mean=scores.reduce((a,b)=>a+b,0)/scores.length;
    let s=mean*.75+Math.min(...scores)*.25;
    if(s>bestScore){bestScore=s;best=candidate;}
  }
  if(!best) {
    let fallback={attack:{kind:'single',angle:45,power:60,dir:1}};
    return nested?{action:fallback,score:-1000}:fallback;
  }
  let action={attack:{kind:best.kind,angle:best.angle,power:best.power,dir:best.dir}};
  if(r.hole.teeActive && Math.abs(best.pos.x-r.actor.x)>2) action.moveX=Math.round(best.pos.x);
  if(nested) return {action,score:bestScore};
  if(r.fly>0 && bestScore<25 && r.strokes+2<r.hole.cap && performance.now()<deadline-180) {
    const start={x:r.actor.x,foot:r.actor.y+config.BODY.footOffset,angle:r.actor.groundAngle};
    const targets=objs.filter(o=>['token','bigToken','pushToken','bug'].includes(o.type));
    let flights=[];
    for(const dir of [-1,1]) for(let angle=10;angle<=80;angle+=10) for(let power=30;power<=100;power+=10) {
      let hit=trace(g,[],start,angle,power,dir,wind,.12,true);
      if(hit.hit!=='ground'||hit.x<30||hit.x>1570)continue;
      let minFeet=r.hole.tee?.minFeet??0;
      let y=ground(g,hit.x,minFeet), l=ground(g,hit.x-12,minFeet), rr=ground(g,hit.x+12,minFeet);
      if(y==null||l==null||rr==null||Math.abs(l-rr)>18||solid(g,hit.x,y-45))continue;
      y=Math.min(y,l,rr);
      if(Math.abs(hit.x-r.actor.x)<100)continue;
      let distance=Math.min(...targets.map(o=>Math.hypot(o.x-hit.x,(o.y-15)-(y-35))));
      flights.push({dir,angle,power,x:hit.x,y,tilt:-Math.atan2(rr-l,24)/RAD,distance});
    }
    flights.sort((a,b)=>a.distance-b.distance);
    let tried=[];
    for(const f of flights) {
      if(performance.now()>deadline-130)break;
      if(tried.some(x=>Math.abs(x-f.x)<45))continue;
      tried.push(f.x);
      let stable=true;
      for(const da of [-.5,.5]) {
        let hit=trace(g,[],start,f.angle+da,f.power,f.dir,wind,.12,true);
        let y=ground(g,hit.x,r.hole.tee?.minFeet??0);
        if(hit.hit!=='ground'||y==null||Math.abs(hit.x-f.x)>45||Math.abs(y-f.y)>20)stable=false;
      }
      if(!stable)continue;
      let next={...r,actor:{...r.actor,x:f.x,y:f.y-config.BODY.footOffset,groundAngle:f.tilt},hole:{...r.hole,teeActive:false},fly:0};
      let result=choose(next,true,deadline);
      if(result.score>55 && result.score>bestScore+45) return {fly:{angle:f.angle,power:f.power,dir:f.dir}};
      if(tried.length>=6)break;
    }
  }
  if(r.hole.goal==='tokens' && best.kind==='dig' && performance.now()<deadline-100) {
    let impact=trace(g,objs,best.pos,best.angle,best.power,best.dir,wind);
    let near=objs.some(o=>['token','bigToken','pushToken'].includes(o.type) && (()=>{let [x,y]=nearest(o,impact.x,impact.y);return Math.hypot(x-impact.x,y-impact.y)<30;})());
    if(impact.hit==='ground' && !near) {
      let plan=planTunnel(r,g,objs,ps,wind,deadline);
      if(plan) {
        let result={attack:{kind:'dig',angle:plan.angle,power:plan.power,dir:plan.dir}};
        if(r.hole.teeActive && Math.abs(plan.pos.x-r.actor.x)>2)result.moveX=Math.round(plan.pos.x);
        return result;
      }
    }
  }
  return action;
}
function exam(r) {
  return r.questions.map(q=>{
    let a=q.angle*RAD,u=.3465*q.power,vy=-u*Math.sin(a),vx=q.dir*u*Math.cos(a),x=q.x,y=r.ground-(r.config.BODY?.muzzleHeight||35),dt=.02;
    for(let t=0;t<100;t+=dt) {let ax=.0375*q.wind*(y<400?1.35:1);x+=vx*dt+ax*dt*dt/2;y+=vy*dt+dt*dt/2;vx+=ax*dt;vy+=dt;if(y>=r.ground)break;}
    return {id:q.id,x,y:r.ground};
  });
}

for await(const line of createInterface({input:process.stdin})) {
  try {
    const r=JSON.parse(line); let out;
    if(r.type==='init'){config=r.config;initialBug=r.objects.find(o=>o.type==='bug')||null;out={ok:true};}
    else if(r.type==='exam')out={predictions:exam(r)};
    else if(r.type==='turn')out=choose(r);
    else throw Error('unknown request');
    process.stdout.write(JSON.stringify({reqId:r.reqId,...out})+'\n');
  } catch(e) {process.stderr.write(String(e.stack||e)+'\n');}
}
