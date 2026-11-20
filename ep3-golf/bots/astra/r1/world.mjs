import { trajectory } from './physics.mjs';

export const alive = o => !o.removed && !o.broken && !o.stunned && !o.inRecycle;
export const center = o => ({x:o.x,y:o.y-(o.radius??(o.height??30)/2)});
export function decode(terrain) {
  const cells = new Uint8Array(terrain.width * terrain.height);
  let offset = 0;
  for (const [value,count] of terrain.rle) { if(value)cells.fill(value,offset,offset+count); offset+=count; }
  return cells;
}
export function solid(cells,x,y) {
  if(x<0||x>=1600||y<0||y>=900)return false;
  return cells[(y>>2)*400+(x>>2)]===1;
}
export function surface(cells,x,minY=0) {
  const col = Math.max(0,Math.min(399,x>>2));
  for(let row=Math.max(0,Math.ceil(minY/4));row<225;row++) {
    if(cells[row*400+col] && (row===0||!cells[(row-1)*400+col]))return row*4;
  }
  return 904;
}
export function blocked(cells,x0,y0,x1,y1,skip=2) {
  const length=Math.hypot(x1-x0,y1-y0),n=Math.ceil(length/2);
  for(let i=skip;i<n;i++) {const f=i/n;if(solid(cells,x0+(x1-x0)*f,y0+(y1-y0)*f))return true;}
  return false;
}
export function contains(o,x,y,pad=0) {
  if(o.type==='bug') {const r=o.radius??14;return (x-o.x)**2+(y-(o.y-r))**2<=(r+pad)**2;}
  return Math.abs(x-o.x)<=(o.width??30)/2+pad&&y>=o.y-(o.height??30)-pad&&y<=o.y+pad;
}
export function objectDistance(o,x,y) {
  if(o.type==='bug')return Math.max(0,Math.hypot(x-o.x,y-o.y+(o.radius??14))-(o.radius??14));
  return Math.hypot(Math.max(0,Math.abs(x-o.x)-(o.width??30)/2),Math.max(0,o.y-(o.height??30)-y,y-o.y));
}
export function impact(cells,objects,pose,shot,wind,{ignoreObjects=false,step=2,minTime=0}={}) {
  const angle=shot.angle+shot.dir*pose.groundAngle;
  const path=trajectory(pose.mx,pose.my,angle,shot.power,shot.dir,wind);
  const dt=step/Math.max(10,path.speed+Math.abs(wind)*1.5);
  let previous=path.at(minTime),t0=minTime;
  for(let t=minTime+dt;t<160;t+=dt) {
    const p=path.at(t);
    if(p.x<0||p.x>=1600||p.y>=900||p.y< -500)return {...p,hit:'out',time:t};
    let object=null;
    if(!ignoreObjects)for(const o of objects)if(alive(o)&&contains(o,p.x,p.y)){object=o;break;}
    const ground=solid(cells,p.x,p.y);
    if(ground||object) {
      let lo=t0,hi=t;
      for(let j=0;j<5;j++) {const mid=(lo+hi)/2,v=path.at(mid);if(solid(cells,v.x,v.y)||(object&&contains(object,v.x,v.y)))hi=mid;else lo=mid;}
      const q=path.at(hi);
      return {...q,hit:object?'object':'ground',objectId:object?.id,time:hi};
    }
    previous=p;t0=t;
  }
  return {...previous,hit:'out',time:160};
}
export function carve(cells,x,y,r) {
  const x0=Math.max(0,Math.floor((x-r)/4)),x1=Math.min(399,Math.ceil((x+r)/4));
  const y0=Math.max(0,Math.floor((y-r)/4)),y1=Math.min(224,Math.ceil((y+r)/4));
  let count=0;
  for(let row=y0;row<=y1;row++)for(let col=x0;col<=x1;col++)if((col*4+2-x)**2+(row*4+2-y)**2<=r*r){const i=row*400+col;count+=cells[i];cells[i]=0;}
  return count;
}
export function settleStatic(cells,o) {
  const half=(o.width??28)/2;
  let y=904;
  for(const dx of [-half+3,0,half-3])y=Math.min(y,surface(cells,o.x+dx,o.y-4));
  if(y>900) {o.removed=true;o.broken=o.type!=='chicken';o.stunned=o.type==='chicken';return;}
  o.y=y;
}
export function actorPose(actor,body) {
  const feet=actor.y+body.footOffset,a=actor.groundAngle*Math.PI/180;
  return {...actor,feet,mx:actor.x-Math.sin(a)*body.muzzleHeight,my:feet-Math.cos(a)*body.muzzleHeight};
}
export function stances(r,cells,body) {
  const result=[actorPose(r.actor,body)];
  if(!r.hole.teeActive)return result;
  const {x0,x1,minFeet=0}=r.hole.tee;
  const candidates=[x0+2,x1-2,(x0+x1)/2];
  for(let x=x0+12;x<x1;x+=32)candidates.push(x);
  for(const x of candidates) {
    if(x<14||x>1586||Math.abs(x-r.actor.x)<3)continue;
    const y=surface(cells,x,minFeet),left=surface(cells,x-8,minFeet),right=surface(cells,x+8,minFeet);
    if(y>=900||Math.max(Math.abs(left-y),Math.abs(right-y))>12)continue;
    const groundAngle=Math.max(-45,Math.min(45,Math.atan2(left-right,16)*180/Math.PI));
    const pose=actorPose({x,y:y-body.footOffset,groundAngle,grounded:true},body);
    if(solid(cells,pose.mx,pose.my)||solid(cells,x,y-50))continue;
    result.push(pose);
  }
  return result;
}
