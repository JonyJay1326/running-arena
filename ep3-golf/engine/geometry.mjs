import { CONFIG as C } from './config.mjs';
const EPS = 1e-7;
const radians = angle => angle * Math.PI / 180;
function frame(bot) { const angle = bot.groundAngle ?? 0; if (![bot.x, bot.y, angle].every(Number.isFinite) || Math.abs(angle) > C.BODY.maxTilt + EPS) throw new Error('角色姿态坐标无效或倾角超过45度'); const a = radians(angle); return { c: Math.cos(a), s: Math.sin(a), x: bot.x, y: bot.y + C.BODY.footOffset }; }
function toWorld(bot, u, v) { const f = frame(bot); return [f.x + u * f.c + v * f.s, f.y - u * f.s + v * f.c]; }
function toLocal(bot, x, y) { const f = frame(bot), dx = x - f.x, dy = y - f.y; return [dx * f.c - dy * f.s, dx * f.s + dy * f.c]; }
export function bodyCorners(bot) { return [[-C.BODY.halfWidth, -C.BODY.height], [C.BODY.halfWidth, -C.BODY.height], [C.BODY.halfWidth, 0], [-C.BODY.halfWidth, 0]].map(([u,v]) => toWorld(bot,u,v)); }
export function bodyBounds(bot) { const p = bodyCorners(bot); return { left: Math.min(...p.map(p=>p[0])), right: Math.max(...p.map(p=>p[0])), top: Math.min(...p.map(p=>p[1])), bottom: Math.max(...p.map(p=>p[1])) }; }
export function bodyDistance(bot, x, y) { const [u,v] = toLocal(bot,x,y); return Math.hypot(Math.max(-C.BODY.halfWidth-u,0,u-C.BODY.halfWidth),Math.max(-C.BODY.height-v,0,v)); }
export function muzzlePosition(bot) { const [x,y] = toWorld(bot,0,-C.BODY.muzzleHeight); return {x,y}; }
function polygonsOverlap(a,b) {
  for (const p of [a,b]) for (let i=0;i<p.length;i++) {
    const q=p[(i+1)%p.length], n=[-(q[1]-p[i][1]),q[0]-p[i][0]], length=Math.hypot(...n); n[0]/=length;n[1]/=length;
    const aa=a.map(v=>v[0]*n[0]+v[1]*n[1]), bb=b.map(v=>v[0]*n[0]+v[1]*n[1]);
    if (Math.min(...aa)>=Math.max(...bb)-EPS || Math.min(...bb)>=Math.max(...aa)-EPS) return false;
  }
  return true;
}
export function overlaps(a,b) { return polygonsOverlap(bodyCorners(a),bodyCorners(b)); }
export function bodyClear(grid,bot,others=[]) {
  const b=bodyBounds(bot),p=bodyCorners(bot);
  if (b.left < -EPS || b.right > C.W+EPS || b.bottom > C.H+EPS) return false;
  for(let gy=Math.max(0,Math.floor(b.top/C.CELL));gy<=Math.min(C.GH-1,Math.floor((b.bottom-EPS)/C.CELL));gy++) for(let gx=Math.max(0,Math.floor(b.left/C.CELL));gx<=Math.min(C.GW-1,Math.floor((b.right-EPS)/C.CELL));gx++) {
    if (!grid[gy*C.GW+gx]) continue;
    const x=gx*C.CELL,y=gy*C.CELL;
    if (polygonsOverlap(p,[[x,y],[x+C.CELL,y],[x+C.CELL,y+C.CELL],[x,y+C.CELL]])) return false;
  }
  return !others.some(other=>other!==bot&&other.alive!==false&&overlaps(bot,other));
}

// Actual exposed cell tops, not a decorative height-map or an inferred visual slope.
function supportSegments(grid,bot,others,range) {
  const feet=bot.y+C.BODY.footOffset,x=bot.x,w=C.BODY.halfWidth,result=[];
  // Terrain tops need horizontal overlap with the window; exclude a cell
  // touching only either endpoint, rather than including only the right one.
  for(let gx=Math.max(0,Math.floor((x-w+EPS)/C.CELL));gx<=Math.min(C.GW-1,Math.ceil((x+w-EPS)/C.CELL)-1);gx++) for(let gy=Math.max(0,Math.floor((feet-range)/C.CELL));gy<=Math.min(C.GH-1,Math.ceil((feet+range)/C.CELL));gy++) {
    if(grid[gy*C.GW+gx]&&(gy===0||!grid[(gy-1)*C.GW+gx])) result.push([[gx*C.CELL,gy*C.CELL],[(gx+1)*C.CELL,gy*C.CELL]]);
  }
  for(const other of others) if(other!==bot&&other.alive!==false){const p=bodyCorners(other);if(Math.max(p[0][0],p[1][0])>=x-w&&Math.min(p[0][0],p[1][0])<=x+w&&Math.min(p[0][1],p[1][1])<=feet+range&&Math.max(p[0][1],p[1][1])>=feet-range)result.push([p[0],p[1]]);}
  return result;
}
function clipPoints(segments,left,right) {
  const points=[];
  for(let [a,b]of segments){if(a[0]>b[0])[a,b]=[b,a];const l=Math.max(a[0],left),r=Math.min(b[0],right);if(r<l-EPS)continue;const dy=(b[1]-a[1])/(b[0]-a[0]);points.push([l,a[1]+(l-a[0])*dy],[r,a[1]+(r-a[0])*dy]);}
  return points;
}
function footContact(points,bot) {
  const beta=radians(bot.groundAngle??0),feet=bot.y+C.BODY.footOffset;
  const contact=points.filter(([x,y])=>Math.abs(feet-(x-bot.x)*Math.tan(beta)-y)<1e-6).map(p=>p[0]);
  return contact.length > 0;
}
export function supported(grid,bot,others=[]) {
  if(Math.abs(bot.groundAngle??0)>C.BODY.maxTilt+EPS)return false;
  const half=C.BODY.halfWidth*Math.cos(radians(bot.groundAngle??0));
  return footContact(clipPoints(supportSegments(grid,bot,others,C.BODY.halfWidth+C.CELL),bot.x-half,bot.x+half),bot)&&bodyClear(grid,bot,others);
}

export function standingPose(grid,bot,others=[],{maxRise=C.STEP_MAX,maxDrop=C.STEP_MAX}={}) {
  const feet=bot.y+C.BODY.footOffset,range=Math.max(maxRise,maxDrop)+C.BODY.halfWidth+C.CELL;
  const segments=supportSegments(grid,bot,others,range),points=clipPoints(segments,bot.x-C.BODY.halfWidth,bot.x+C.BODY.halfWidth);
  if(!points.length)return null;
  const at=x=>{
    // Average the nearest tops touching either side of an edge, so reflection
    // preserves the fitted slope. Actual contact and body clearance are checked below.
    const choices=segments.filter(([a,b])=>x>=Math.min(a[0],b[0])-EPS&&x<=Math.max(a[0],b[0])+EPS).map(([a,b])=>({left:Math.min(a[0],b[0]),right:Math.max(a[0],b[0]),y:a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0])}));
    const nearest=side=>choices.filter(side).map(p=>p.y).sort((a,b)=>Math.abs(a-feet)-Math.abs(b-feet)||a-b)[0];
    const before=nearest(p=>p.left<x-EPS),after=nearest(p=>p.right>x+EPS);
    return before===undefined?after:after===undefined?before:(before+after)/2;
  };
  const left=at(bot.x-C.BODY.supportProbe),right=at(bot.x+C.BODY.supportProbe);
  const preferred=left===undefined||right===undefined?0:Math.atan2(left-right,2*C.BODY.supportProbe)*180/Math.PI;
  if(Math.abs(preferred)>C.BODY.maxTilt+EPS)return null;
  const angles=new Set([preferred,0]);
  for(let i=0;i<points.length;i++)for(let j=i+1;j<points.length;j++){
    const dx=points[j][0]-points[i][0];if(Math.abs(dx)<C.BODY.slopeFitSpan-EPS)continue;
    const angle=Math.atan((points[i][1]-points[j][1])/dx)*180/Math.PI;
    if(Math.abs(angle)<=C.BODY.maxTilt+EPS)angles.add(angle);
  }
  const rank=angle=>[Math.abs(angle-preferred),Math.abs(angle),Math.abs(angle-(bot.groundAngle??0))];
  const compareRank=(a,b)=>{
    const aa=rank(a),bb=rank(b);
    for(let i=0;i<aa.length;i++)if(Math.abs(aa[i]-bb[i])>EPS)return aa[i]-bb[i];
    return 0;
  };
  let chosen=null,ambiguous=false;
  for(const angle of [...angles].sort(compareRank)){
    if(chosen&&compareRank(angle,chosen.groundAngle)>0)break;
    const half=C.BODY.halfWidth*Math.cos(radians(angle)),samples=clipPoints(segments,bot.x-half,bot.x+half);
    if(!samples.length)continue;
    const anchor=Math.min(...samples.map(([x,y])=>y+(x-bot.x)*Math.tan(radians(angle))));
    if(anchor<feet-maxRise-EPS||anchor>feet+maxDrop+EPS)continue;
    const pose={...bot,y:anchor-C.BODY.footOffset,groundAngle:Math.abs(angle)<1e-10?0:angle,grounded:true};
    if(!footContact(samples,pose)||!bodyClear(grid,pose,others))continue;
    if(!chosen){chosen=pose;continue;}
    // Equivalent slopes prefer continuity and then the smallest vertical shift;
    // never use the sign of the angle as a horizontal tie-breaker.
    const previousAnchor=chosen.y+C.BODY.footOffset;
    const shift=Math.abs(anchor-feet)-Math.abs(previousAnchor-feet);
    if(shift < -EPS||(Math.abs(shift)<=EPS&&anchor<previousAnchor-EPS)){
      chosen=pose;ambiguous=false;
    }else if(Math.abs(shift)<=EPS&&Math.abs(anchor-previousAnchor)<=EPS&&Math.abs(angle-chosen.groundAngle)>EPS){
      ambiguous=true;
    }
  }
  return ambiguous?null:chosen;
}
export function solidAt(grid, x, y) {
  return x >= 0 && x < C.W && y >= 0 && y < C.H && grid[Math.floor(y / C.CELL) * C.GW + Math.floor(x / C.CELL)] === 1;
}
export function carveGrid(grid, x, y, crater) {
  const { shape, rx, ry } = typeof crater === 'number' ? { shape: 'circle', rx: crater, ry: crater } : crater;
  for (let gy = Math.max(0, Math.floor((y - ry) / C.CELL)); gy <= Math.min(C.GH - 1, Math.floor((y + ry) / C.CELL)); gy++) {
    for (let gx = Math.max(0, Math.floor((x - rx) / C.CELL)); gx <= Math.min(C.GW - 1, Math.floor((x + rx) / C.CELL)); gx++) {
      const dx = (gx * C.CELL + C.CELL / 2 - x) / rx, dy = (gy * C.CELL + C.CELL / 2 - y) / ry;
      if (shape === 'rect' ? Math.abs(dx) <= 1 && Math.abs(dy) <= 1 : dx * dx + dy * dy <= 1) grid[gy * C.GW + gx] = 0;
    }
  }
}
export function visible(grid, x, y, tx, ty) {
  if (solidAt(grid, x, y)) {
    const left = Math.floor(x / C.CELL) * C.CELL, top = Math.floor(y / C.CELL) * C.CELL;
    const exits = [[left - .001, y], [left + C.CELL + .001, y], [x, top - .001], [x, top + C.CELL + .001]]
      .filter(([px, py]) => !solidAt(grid, px, py))
      .sort((a, b) => Math.hypot(a[0] - x, a[1] - y) - Math.hypot(b[0] - x, b[1] - y));
    if (!exits.length) return false;
    // 碰撞末子步可能深入接触格：从最近自由表面发出射线，不能豁免穿墙方向。
    [x, y] = exits[0];
  }
  const d = Math.hypot(tx - x, ty - y), n = Math.max(1, Math.ceil(d));
  for (let i = 1; i < n; i++) {
    const px = x + (tx - x) * i / n, py = y + (ty - y) * i / n;
    if (solidAt(grid, px, py)) return false;
  }
  return true;
}
export function visibleBodyDistance(grid,bot,x,y,radius) {
  const [u,v]=toLocal(bot,x,y), points=[toWorld(bot,Math.max(-C.BODY.halfWidth,Math.min(C.BODY.halfWidth,u)),Math.max(-C.BODY.height,Math.min(0,v)))];
  const corners=bodyCorners(bot);
  for(let i=0;i<4;i++){const a=corners[i],b=corners[(i+1)%4],n=Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/2);for(let j=0;j<=n;j++)points.push([a[0]+(b[0]-a[0])*j/n,a[1]+(b[1]-a[1])*j/n]);}
  let best=Infinity;
  for(const [px,py]of points){const d=Math.hypot(px-x,py-y);if(d<radius&&d<best&&visible(grid,x,y,px,py))best=d;}
  return best;
}
