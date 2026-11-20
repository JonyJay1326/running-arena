// Physics and aiming. Flat-ground probes (RMSE ~0.2px): 
// v = 6.93 * power, g = 400 px/s^2, ax = wind * wk(y) + windAdd
// wk(y) = 15 + 0.064 * max(0, 390 - y). Below y=390 wk is constant.
// Spatial scale of g is conventional; paths match the probe landings.

export const G = 400;
export const V_SCALE = 6.93;
export const CRATER = { single: 32, burst: 26, spread: 24, dig: 60 };
export const DMG = { single: 120, burst: 65, spread: 55, dig: 30 };
const DMG_R = 30;

// Probes never went above y=262. Keep wk at that measured peak; do not
// extrapolate the linear term into the top of the map.
const WIND_Y_MEASURED = 262;
export function windK(y) {
  const yUse = Math.max(y, WIND_Y_MEASURED);
  return 15 + 0.064 * Math.max(0, 390 - yUse);
}

export function predictFlat(s) {
  const rad = ((s.angle + s.dir * (s.groundAngle || 0)) * Math.PI) / 180;
  const v = V_SCALE * s.power;
  const vx = s.dir * v * Math.cos(rad);
  const vy0 = -v * Math.sin(rad);
  const y0 = s.y0;
  const ground = s.ground;
  const disc = vy0 * vy0 - 2 * G * (y0 - ground);
  if (disc < 0) return { x: s.x, y: y0, t: 0 };
  const t = (-vy0 + Math.sqrt(disc)) / G;
  const N = 500;
  const dt = t / N;
  let integ = 0;
  let xWind = 0;
  let prev = windK(y0);
  for (let i = 1; i <= N; i++) {
    const tt = dt * i;
    const y = y0 + vy0 * tt + 0.5 * G * tt * tt;
    const wk = windK(y);
    const mid = 0.5 * (prev + wk);
    xWind += (integ + 0.5 * mid * dt) * dt;
    integ += mid * dt;
    prev = wk;
  }
  const x = s.x + vx * t + s.wind * xWind + 0.5 * (s.windAdd || 0) * t * t;
  return { x, y: ground, t };
}

export function makeGrid(terrain) {
  const w = terrain.width;
  const h = terrain.height;
  const cell = terrain.cell || 4;
  const g = new Uint8Array(w * h);
  let i = 0;
  for (const pair of terrain.rle || []) {
    const v = pair[0];
    const n = pair[1];
    if (v) g.fill(1, i, Math.min(g.length, i + n));
    i += n;
  }
  return { g, w, h, cell, W: w * cell, H: h * cell };
}

function solid(world, x, y) {
  const cx = Math.floor(x / world.cell);
  const cy = Math.floor(y / world.cell);
  if (cx < 0 || cy < 0 || cx >= world.w || cy >= world.h) return false;
  return world.g[cy * world.w + cx] === 1;
}

export function shapeOf(o) {
  if (o.type === 'bug') {
    const r = o.radius || 14;
    return { kind: 'circle', x: o.x, y: o.y - r, r, obj: o };
  }
  const defaults = { token: [28, 28], bigToken: [44, 56], pushToken: [30, 30], chicken: [26, 30] };
  const d = defaults[o.type] || [28, 28];
  const w = o.width || d[0];
  const h = o.height || d[1];
  return { kind: 'rect', l: o.x - w / 2, r: o.x + w / 2, t: o.y - h, b: o.y, cx: o.x, cy: o.y - h / 2, obj: o };
}

function segCircle(x0, y0, x1, y1, cx, cy, r) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const fx = x0 - cx;
  const fy = y0 - cy;
  const a = dx * dx + dy * dy;
  if (a < 1e-8) return null;
  const b = 2 * (fx * dx + fy * dy);
  const c = fx * fx + fy * fy - r * r;
  const disc = b * b - 4 * a * c;
  if (disc < 0) return null;
  const t = (-b - Math.sqrt(disc)) / (2 * a);
  if (t >= 0 && t <= 1) return t;
  return null;
}

function segRect(x0, y0, x1, y1, L, T, R, B) {
  if (x0 >= L && x0 <= R && y0 >= T && y0 <= B) return 0;
  let t0 = 0;
  let t1 = 1;
  const dx = x1 - x0;
  const dy = y1 - y0;
  const p = [-dx, dx, -dy, dy];
  const q = [x0 - L, R - x0, y0 - T, B - y0];
  for (let i = 0; i < 4; i++) {
    if (Math.abs(p[i]) < 1e-12) {
      if (q[i] < 0) return null;
    } else {
      const t = q[i] / p[i];
      if (p[i] < 0) {
        if (t > t1) return null;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return null;
        if (t < t1) t1 = t;
      }
    }
  }
  return t0 <= t1 ? t0 : null;
}

function firstHit(x0, y0, x1, y1, world, shapes) {
  let bestT = 2;
  let hit = null;
  for (const s of shapes) {
    let t = null;
    if (s.kind === 'circle') t = segCircle(x0, y0, x1, y1, s.x, s.y, s.r);
    else t = segRect(x0, y0, x1, y1, s.l, s.t, s.r, s.b);
    if (t != null && t < bestT) {
      bestT = t;
      hit = { type: 'object', object: s.obj, t };
    }
  }
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const n = Math.max(2, Math.ceil(dist));
  let seen = -1;
  for (let i = 1; i <= n; i++) {
    const t = i / n;
    if (solid(world, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)) {
      seen = t;
      break;
    }
  }
  if (seen >= 0 && seen < bestT) {
    let lo = seen - 1 / n;
    let hi = seen;
    if (lo < 0) lo = 0;
    for (let k = 0; k < 8; k++) {
      const mid = (lo + hi) / 2;
      if (solid(world, x0 + (x1 - x0) * mid, y0 + (y1 - y0) * mid)) hi = mid;
      else lo = mid;
    }
    if (hi < bestT) {
      bestT = hi;
      hit = { type: 'ground', object: null, t: hi };
    }
  }
  if (!hit) return null;
  return {
    type: hit.type,
    object: hit.object,
    t: bestT,
    x: x0 + (x1 - x0) * bestT,
    y: y0 + (y1 - y0) * bestT,
  };
}

export function simulate(o) {
  const elev = ((o.angle + o.dir * (o.groundAngle || 0)) * Math.PI) / 180;
  const v = V_SCALE * o.power;
  let vx = o.dir * v * Math.cos(elev);
  let vy = -v * Math.sin(elev);
  let x = o.x;
  let y = o.y;
  let t = 0;
  const wind = o.wind || 0;
  const windAdd = o.windAdd || 0;
  const shapes = o.ignoreObjects ? [] : o.shapes || [];
  const world = o.world;
  if (solid(world, x, y)) y -= 2;
  for (let step = 0; step < 900 && t < 8; step++) {
    const sp = Math.hypot(vx, vy);
    const dt = Math.min(0.025, 2 / Math.max(sp, 30));
    const ax = wind * (o.windMode === 'flat' ? 15 : windK(y)) + windAdd;
    const nx = x + vx * dt + 0.5 * ax * dt * dt;
    const ny = y + vy * dt + 0.5 * G * dt * dt;
    const nvx = vx + ax * dt;
    const nvy = vy + G * dt;
    if (nx < -40 || nx > world.W + 40 || ny > world.H + 30 || ny < -600) {
      return { type: 'oob', x: nx, y: ny, t, object: null };
    }
    const hit = firstHit(x, y, nx, ny, world, shapes);
    if (hit) return { ...hit, t: t + dt * hit.t, vx, vy };
    x = nx;
    y = ny;
    vx = nvx;
    vy = nvy;
    t += dt;
  }
  return { type: 'miss', x, y, t, object: null };
}

function surfaces(world, x) {
  const cx = Math.floor(x / world.cell);
  if (cx < 0 || cx >= world.w) return [];
  const out = [];
  for (let cy = 0; cy < world.h; cy++) {
    const solidHere = world.g[cy * world.w + cx] === 1;
    const above = cy > 0 && world.g[(cy - 1) * world.w + cx] === 1;
    if (solidHere && !above) out.push(cy * world.cell);
  }
  return out;
}

function surfaceNear(world, x, footY) {
  const list = surfaces(world, x);
  if (!list.length) return null;
  let best = list[0];
  let bd = Math.abs(best - footY);
  for (const y of list) {
    const d = Math.abs(y - footY);
    if (d < bd) {
      bd = d;
      best = y;
    }
  }
  return bd <= 28 ? best : null;
}

export function groundAngleAt(world, x, footY) {
  const yL = surfaceNear(world, x - 8, footY);
  const yR = surfaceNear(world, x + 8, footY);
  if (yL == null || yR == null) return 0;
  const deg = (Math.atan2(-(yR - yL), 16) * 180) / Math.PI;
  return Math.max(-45, Math.min(45, deg));
}

function muzzle(x, footY, gaDeg, muzzleH) {
  const ga = (gaDeg * Math.PI) / 180;
  return { x: x - Math.sin(ga) * muzzleH, y: footY - Math.cos(ga) * muzzleH };
}

function canStand(world, x, footY) {
  if (surfaceNear(world, x, footY) == null) return false;
  if (surfaceNear(world, x - 12, footY) == null) return false;
  if (surfaceNear(world, x + 12, footY) == null) return false;
  const ga = groundAngleAt(world, x, footY);
  if (Math.abs(ga) > 36) return false;
  for (const dx of [-8, 0, 8]) {
    for (let k = 1; k <= 8; k++) {
      if (solid(world, x + dx, footY - k * 4)) return false;
    }
  }
  return true;
}

function lineBlocked(world, x0, y0, x1, y1) {
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const n = Math.max(2, Math.ceil(dist / 3));
  let air = false;
  let solidCount = 0;
  for (let i = 1; i < n; i++) {
    const t = i / n;
    const x = x0 + (x1 - x0) * t;
    const y = y0 + (y1 - y0) * t;
    if (solid(world, x, y)) {
      solidCount++;
      if (air) return true;
    } else air = true;
  }
  if (!air && solidCount > 2) return true;
  return false;
}

function closestOn(shape, x, y) {
  if (shape.kind === 'circle') {
    const dx = x - shape.x;
    const dy = y - shape.y;
    const d = Math.hypot(dx, dy) || 1e-6;
    const cl = Math.max(0, d - shape.r);
    return { x: shape.x + (dx / d) * shape.r, y: shape.y + (dy / d) * shape.r, dist: cl };
  }
  const cx = Math.max(shape.l, Math.min(shape.r, x));
  const cy = Math.max(shape.t, Math.min(shape.b, y));
  return { x: cx, y: cy, dist: Math.hypot(x - cx, y - cy) };
}

function splashDamage(impact, shape, world, pelletDmg) {
  if (impact.object && impact.object.id === shape.obj.id) return pelletDmg;
  const cp = closestOn(shape, impact.x, impact.y);
  if (cp.dist >= DMG_R) return 0;
  if (lineBlocked(world, impact.x, impact.y, shape.kind === 'circle' ? shape.x : shape.cx, shape.kind === 'circle' ? shape.y : shape.cy)) return 0;
  return pelletDmg * (1 - cp.dist / DMG_R);
}

function undermines(impact, obj, craterR) {
  const sy = obj.y + 1;
  return (obj.x - impact.x) ** 2 + (sy - impact.y) ** 2 < craterR * craterR;
}

const holeMem = { windAdd: 0, last: null, ban: null, fails: 0, bad: [] };

export function resetHole() {
  holeMem.windAdd = 0;
  holeMem.last = null;
  holeMem.ban = null;
  holeMem.fails = 0;
  holeMem.bad = [];
}

function adapt(obs) {
  const impacts = obs.lastResult?.impacts;
  if (!impacts?.length || !holeMem.last) return;
  const actual = impacts[0];
  const err = actual.x - holeMem.last.predX;
  // A miss of tens of pixels is a wrong trajectory, not a small wind bias.
  // Correcting it used to pin windAdd at ±8 for the rest of the hole.
  if (Math.abs(err) > 8 && Math.abs(err) <= 40) {
    const t = Math.max(0.8, holeMem.last.t || 1.2);
    const gain = err / (0.5 * t * t);
    holeMem.windAdd = Math.max(-8, Math.min(8, holeMem.windAdd + gain * 0.25));
  }
  const wanted = holeMem.last.objectId;
  const got = actual.objectId ?? null;
  const yErr = (actual.y ?? 0) - (holeMem.last.predY ?? actual.y ?? 0);
  if (wanted != null && got !== wanted && Math.hypot(err, yErr) > 18) {
    holeMem.bad.push({ x: holeMem.last.predX, y: holeMem.last.predY ?? actual.y ?? 0 });
    if (holeMem.bad.length > 6) holeMem.bad.shift();
  }
  const changes = obs.lastResult.objectChanges || [];
  const useful = changes.some((c) => (c.damage || 0) > 0 || (c.before && c.after && (Math.abs(c.before.x - c.after.x) > 2 || Math.abs((c.before.y || 0) - (c.after.y || 0)) > 2)));
  if (!useful) {
    holeMem.fails += 1;
    holeMem.ban = holeMem.last.key;
  } else holeMem.fails = 0;
}

function aimAnalytic(m, ga, wind, windAdd, tx, ty) {
  let best = null;
  for (const dir of [1, -1]) {
    for (let angle = 2; angle <= 85; angle += 2) {
      for (let power = 12; power <= 100; power += 8) {
        const elev = ((angle + dir * ga) * Math.PI) / 180;
        const v = V_SCALE * power;
        let x = m.x;
        let y = m.y;
        let vx = dir * v * Math.cos(elev);
        let vy = -v * Math.sin(elev);
        let miss = 1e9;
        for (let i = 0; i < 70; i++) {
          const sp = Math.hypot(vx, vy) + 1e-3;
          const dt = Math.min(0.04, 8 / sp);
          const ax = wind * windK(y) + windAdd;
          x += vx * dt + 0.5 * ax * dt * dt;
          y += vy * dt + 0.5 * G * dt * dt;
          vx += ax * dt;
          vy += G * dt;
          const d = Math.hypot(x - tx, y - ty);
          if (d < miss) miss = d;
          if (y > ty + 80 && vy > 0 && d > miss + 40) break;
          if (x < -80 || x > 1700 || y > 960 || y < -500) break;
        }
        if (!best || miss < best.miss) best = { miss, angle, power, dir };
      }
    }
  }
  return best;
}

function blockersBetween(world, x0, y0, x1, y1) {
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const n = Math.max(2, Math.ceil(dist / 4));
  const pts = [];
  let air = false;
  for (let i = 1; i < n; i++) {
    const t = i / n;
    const x = x0 + (x1 - x0) * t;
    const y = y0 + (y1 - y0) * t;
    if (solid(world, x, y)) {
      if (air) pts.push({ x, y });
    } else air = true;
    if (pts.length > 24) break;
  }
  return pts;
}

function digProgress(impact, ctx, kind) {
  if (ctx.aimX == null || !ctx.stance) return 0;
  const sx = ctx.stance.muzzle.x;
  const tx = ctx.aimX;
  const dir = Math.sign(tx - sx);
  if (!dir) return 0;
  const along = (impact.x - sx) * dir;
  const span = Math.abs(tx - sx) || 1;
  if (along < 48 || along > span + 16) return 0;
  const u = Math.min(1, along / span);
  const yLine = ctx.stance.muzzle.y + (ctx.aimY - ctx.stance.muzzle.y) * u;
  if (Math.abs(impact.y - yLine) > 180 && Math.abs(impact.y - ctx.aimY) > 180) return 0;
  return u * (kind === 'dig' ? 260 : 36);
}

function openCount(pts, impact, craterR) {
  let n = 0;
  const r2 = craterR * craterR;
  for (const p of pts) if ((p.x - impact.x) ** 2 + (p.y - impact.y) ** 2 <= r2) n++;
  return n;
}

function scoreImpact(impact, ctx, kind) {
  if (!impact || impact.type === 'oob' || impact.type === 'miss') return -50;
  const dmg = DMG[kind];
  const crater = CRATER[kind];
  let score = 0;
  let killed = 0;
  let damaged = false;
  for (const s of ctx.chickens) {
    if (splashDamage(impact, s, ctx.world, dmg) > 0.2) return -1e6;
    if (undermines(impact, s.obj, crater)) return -1e6;
  }
  if (ctx.needTokens) {
    for (const s of ctx.tokens) {
      let dealt = splashDamage(impact, s, ctx.world, kind === 'burst' ? 65 : dmg);
      if (kind === 'burst' && impact.object && impact.object.id === s.obj.id && s.obj.type !== 'pushToken') {
        const hp0 = s.obj.hp || 240;
        // Two pellets leave  hp-130. The third stays on the target only if that remainder is still positive.
        dealt = hp0 > 130 ? Math.min(hp0, 195) : Math.min(hp0, 65);
      }
      if (kind === 'spread') dealt = 0;
      const hp = s.obj.hp || (s.obj.type === 'token' ? 1 : 100);
      if (dealt > 0.2) {
        damaged = true;
        if (impact.object && impact.object.id === s.obj.id) score += 50;
        if (dealt >= hp - 0.5) {
          score += 1300;
          killed++;
        } else if (dealt >= 20 || (impact.object && impact.object.id === s.obj.id)) {
          // Stay above the dig-opening cap so a real hit is not thrown away.
          score += Math.min(1200, 400 + 1000 * (dealt / hp));
        } else score += 1000 * (dealt / hp);
      }
    }
  }
  if (ctx.needBug && ctx.bug) {
    score += bugPushScore(impact, ctx);
    const below = recycleBelow(ctx.bug, ctx.recycle);
    if (undermines(impact, ctx.bug, crater)) score += below ? 450 : -80;
  } else if (ctx.bug && undermines(impact, ctx.bug, crater)) score -= 200;
  if (!damaged && score < 400) {
    // Opening bonus is only for shots that missed every token. A direct hit
    // must be ranked by damage, or a wide dig crater outscores a 195 burst.
    let extra = 0;
    if (ctx.blockers.length) {
      const n = openCount(ctx.blockers, impact, crater);
      extra += n * (kind === 'dig' ? 28 : 6);
    }
    extra += digProgress(impact, ctx, kind);
    if (extra > 0) score += Math.min(380, extra);
  }
  score -= impact.t * 0.4;
  return score;
}

function recycleBelow(bug, recycle) {
  if (!recycle) return false;
  const cy = bug.y - (bug.radius || 14);
  return bug.x > recycle.x0 + 4 && bug.x < recycle.x1 - 4 && recycle.y1 > cy + 10 && recycle.y0 > bug.y - 30;
}

// Flat-ground labs, explosion on the ground beside the bug, final y unchanged, no penalty.
// off = horizontal distance from bug foot to explosion. push = resulting +x travel away from the blast.
const BUG_PUSH = [
  { off: 58, push: 40 },
  { off: 52, push: 70 },
  { off: 41, push: 173 },
  { off: 36, push: 239 },
  { off: 24, push: 444 },
];

function pushFromOff(off) {
  const t = BUG_PUSH;
  if (off >= t[0].off) return t[0].push * (t[0].off / off);
  if (off <= t[t.length - 1].off) return t[t.length - 1].push;
  for (let i = 0; i < t.length - 1; i++) {
    if (off <= t[i].off && off >= t[i + 1].off) {
      const u = (t[i].off - off) / (t[i].off - t[i + 1].off);
      return t[i].push + u * (t[i + 1].push - t[i].push);
    }
  }
  return 100;
}

function offForPush(push) {
  const t = BUG_PUSH;
  if (push <= t[0].push) return t[0].off;
  if (push >= t[t.length - 1].push) return t[t.length - 1].off;
  for (let i = 0; i < t.length - 1; i++) {
    if (push >= t[i].push && push <= t[i + 1].push) {
      const u = (push - t[i].push) / (t[i + 1].push - t[i].push);
      return t[i].off + u * (t[i + 1].off - t[i].off);
    }
  }
  return 40;
}

export function planBugShot(bug, rec, worldW) {
  const cx = bug.x;
  const cy = bug.y - (bug.radius || 14);
  // Already above the recycle column: drop the floor instead of shoving it sideways.
  const xNear = cx > rec.x0 - 24 && cx < rec.x1 + 24;
  const recycleUnder = rec.y0 > bug.y - 10 && rec.y1 > cy + 8;
  if (xNear && recycleUnder) {
    return { x: cx, y: bug.y + 8, off: 0, push: 0, dir: 0, drop: true };
  }
  const dir = (rec.x0 + rec.x1) / 2 >= cx ? 1 : -1;
  const width = Math.max(8, rec.x1 - rec.x0);
  const inset = Math.min(48, Math.max(16, width * 0.3));
  const entry = dir > 0 ? rec.x0 + inset : rec.x1 - inset;
  const stop = dir > 0 ? rec.x1 - 10 : rec.x0 + 10;
  let need = dir > 0 ? entry - cx : cx - entry;
  if (need < 20) need = 20;
  const room = dir > 0 ? stop - cx : cx - stop;
  const mapRoom = dir > 0 ? worldW - 48 - cx : cx - 48;
  const want = Math.max(20, Math.min(need, room > 24 ? room : need, Math.max(24, mapRoom)));
  const off = offForPush(want);
  return { x: cx - dir * off, y: bug.y - 1, off, push: pushFromOff(off), dir };
}

function bugPushScore(impact, ctx) {
  const bug = ctx.bug;
  const rec = ctx.recycle;
  if (!rec) return 0;
  const cx = bug.x;
  const cy = bug.y - (bug.radius || 14);
  if (cx >= rec.x0 && cx <= rec.x1 && cy >= rec.y0 && cy <= rec.y1) return 60;
  const plan = planBugShot(bug, rec, ctx.world.W);
  if (impact.object && impact.object.type === 'bug') return -300;
  if (plan.drop) {
    const miss = Math.hypot(impact.x - plan.x, impact.y - plan.y);
    let s = 860 - miss * 10;
    if (miss < 22) s += 420;
    return s;
  }
  const miss = Math.hypot(impact.x - plan.x, impact.y - plan.y);
  const dx = cx - impact.x;
  const dy = cy - impact.y;
  const dist = Math.hypot(dx, dy) || 1;
  const align = (dx * plan.dir) / dist;
  if (align < 0.15) return -80 - miss * 0.05;
  let s = 980 - miss * 14;
  if (miss < 8 && align > 0.5) s += 280;
  const predX = cx + plan.dir * plan.push;
  const cy2 = cy;
  if (predX >= rec.x0 && predX <= rec.x1 && cy2 >= rec.y0 && cy2 <= rec.y1) s += 700;
  if (cx >= rec.x0 && cx <= rec.x1 && (predX < rec.x0 || predX > rec.x1)) s -= 500;
  if (predX < 30 || predX > ctx.world.W - 30) s -= 400;
  return s;
}

function live(obs) {
  return (obs.objects || []).filter((o) => !o.removed && !o.broken);
}

function buildCtx(obs, world, stance) {
  const objs = live(obs);
  const goal = obs.hole.goal;
  const needTokens = goal === 'tokens' || goal === 'both';
  const needBug = goal === 'bug' || goal === 'both';
  const shapes = objs.map(shapeOf);
  const tokens = shapes.filter((s) => s.obj.type === 'token' || s.obj.type === 'bigToken' || s.obj.type === 'pushToken');
  const chickens = shapes.filter((s) => s.obj.type === 'chicken');
  const bugS = shapes.find((s) => s.obj.type === 'bug' && !s.obj.inRecycle);
  const primary = (needTokens && tokens[0]) || bugS;
  const tx = primary ? (primary.kind === 'circle' ? primary.x : primary.cx) : stance.muzzle.x + 200;
  const ty = primary ? (primary.kind === 'circle' ? primary.y : primary.cy) : stance.footY;
  const blockers = blockersBetween(world, stance.muzzle.x, stance.muzzle.y, tx, ty);
  return {
    aimX: tx,
    aimY: ty,
    world,
    needTokens,
    needBug,
    tokens: needTokens ? tokens : [],
    chickens,
    bug: needBug ? bugS?.obj || null : objs.find((o) => o.type === 'bug') || null,
    bugShape: bugS || null,
    recycle: obs.hole.recycle,
    shapes,
    blockers,
    pushR: 42,
    pushPx: 110,
    stance,
  };
}

function aimPoints(ctx) {
  const pts = [];
  for (const s of ctx.tokens) {
    if (s.kind === 'rect') {
      pts.push([s.cx, s.cy], [s.l + 2, s.cy], [s.r - 2, s.cy], [s.cx, s.t + 2], [s.cx, s.b - 2]);
    }
  }
  if (ctx.needBug && ctx.bug && ctx.recycle) {
    const plan = planBugShot(ctx.bug, ctx.recycle, ctx.world.W);
    const sign = -plan.dir;
    pts.push([plan.x, plan.y], [plan.x + sign * 8, plan.y], [ctx.bug.x + sign * 24, ctx.bug.y - 1]);
  }
  if (ctx.blockers.length) {
    const b0 = ctx.blockers[Math.min(2, ctx.blockers.length - 1)];
    pts.push([b0.x, b0.y]);
  }
  return pts;
}

function consider(best, ctx, world, stance, angle, power, dir, t0) {
  if (performance.now() - t0 > 620) return best;
  const key = `${stance.x | 0}:${dir}:${angle}:${power}`;
  const base = {
    x: stance.muzzle.x,
    y: stance.muzzle.y,
    angle,
    power,
    dir,
    groundAngle: stance.groundAngle,
    wind: ctx.wind,
    windAdd: ctx.windAdd,
    world,
    shapes: ctx.shapes,
  };
  let impact = simulate(base);
  // Above the highest probe, wind strength is unmeasured. Count an object hit
  // only when a flat wind model hits the same object; otherwise trust the flat path.
  if (impact.type === 'object' && (base.y < WIND_Y_MEASURED || impact.y < WIND_Y_MEASURED)) {
    const alt = simulate({ ...base, windMode: 'flat' });
    const same = alt.type === 'object' && alt.object && impact.object && alt.object.id === impact.object.id;
    if (!same) impact = alt;
  }
  let kind = 'single';
  let score = scoreImpact(impact, ctx, 'single');
  const burstScore = scoreImpact(impact, ctx, 'burst');
  if (burstScore > score) {
    score = burstScore;
    kind = 'burst';
  }
  const digScore = scoreImpact(impact, ctx, 'dig');
  if (digScore > score) {
    score = digScore;
    kind = 'dig';
  }
  if (holeMem.bad.some((z) => Math.hypot(impact.x - z.x, impact.y - z.y) < 70)) score -= 800;
  if (holeMem.ban === key) score -= 80;
  if (holeMem.fails >= 2 && holeMem.ban && holeMem.ban.endsWith(`:${angle}:${power}`)) score -= 40;
  if (!best || score > best.score) {
    return { score, kind, angle, power, dir, stance, impact, key };
  }
  return best;
}

function stancesOf(obs, world) {
  const body = obs.config?.BODY || {};
  const footOffset = body.footOffset ?? 12;
  const muzzleH = body.muzzleHeight ?? 35;
  const footY = obs.actor.y + footOffset;
  const ga = obs.actor.groundAngle || 0;
  const cur = {
    x: obs.actor.x,
    footY,
    groundAngle: ga,
    muzzle: muzzle(obs.actor.x, footY, ga, muzzleH),
    current: true,
  };
  const list = [cur];
  const tee = obs.hole.tee;
  if (!obs.hole.teeActive || !tee) return list;
  const minFeet = tee.minFeet;
  const span = tee.x1 - tee.x0;
  const step = Math.max(12, Math.min(36, span / 7));
  for (let x = tee.x0 + 4; x <= tee.x1 - 4; x += step) {
    if (Math.abs(x - cur.x) < 8) continue;
    const surf = surfaces(world, x).filter((y) => (minFeet == null ? true : y >= minFeet - 1));
    if (!surf.length) continue;
    let fy = surf[0];
    let bd = Math.abs(fy - footY);
    for (const y of surf) {
      const d = Math.abs(y - footY);
      if (d < bd) {
        bd = d;
        fy = y;
      }
    }
    if (bd > 16) continue;
    if (!canStand(world, x, fy)) continue;
    const nga = groundAngleAt(world, x, fy);
    list.push({ x, footY: fy, groundAngle: nga, muzzle: muzzle(x, fy, nga, muzzleH), current: false });
    if (list.length >= 6) break;
  }
  return list;
}

function search(obs, world, t0) {
  // Stay on the spawned stand. A bad moveX forfeits the hole.
  const stances = stancesOf(obs, world).slice(0, 1);
  let best = null;
  for (const stance of stances) {
    const ctx = buildCtx(obs, world, stance);
    ctx.wind = obs.hole.wind || 0;
    ctx.windAdd = holeMem.windAdd;
    const pts = aimPoints(ctx);
    const seeds = [];
    for (const [tx, ty] of pts) {
      if (performance.now() - t0 > 420) break;
      const a = aimAnalytic(stance.muzzle, stance.groundAngle, ctx.wind, ctx.windAdd, tx, ty);
      if (a && a.miss < 70) seeds.push(a);
    }
    for (const a of seeds) {
      for (let da = -2; da <= 2; da += 2) {
        for (let dp = -8; dp <= 8; dp += 8) {
          const angle = Math.max(0, Math.min(85, a.angle + da));
          const power = Math.max(8, Math.min(100, a.power + dp));
          best = consider(best, ctx, world, stance, angle, power, a.dir, t0);
        }
      }
    }
    if (performance.now() - t0 < 520) {
      for (const dir of [-1, 1]) {
        for (let angle = 4; angle <= 84; angle += 2) {
          for (let power = 30; power <= 100; power += 5) {
            best = consider(best, ctx, world, stance, angle, power, dir, t0);
            if (performance.now() - t0 > 560) break;
          }
        }
      }
    }
  }
  if (!best) return null;
  const stance = best.stance;
  const ctx = buildCtx(obs, world, stance);
  ctx.wind = obs.hole.wind || 0;
  ctx.windAdd = holeMem.windAdd;
  for (let angle = best.angle - 3; angle <= best.angle + 3; angle++) {
    if (angle < 0 || angle > 85) continue;
    for (let power = best.power - 6; power <= best.power + 6; power += 2) {
      if (power < 5 || power > 100) continue;
      best = consider(best, ctx, world, stance, angle, power, best.dir, t0);
    }
  }
  // Spread bracket around the chosen angle if a single shot is not a clean kill.
  if (best.score < 1200 && performance.now() - t0 < 640) {
    const pellets = [-6, 0, 6].map((off) => simulate({
      x: stance.muzzle.x,
      y: stance.muzzle.y,
      angle: Math.max(0, Math.min(85, best.angle + off)),
      power: best.power,
      dir: best.dir,
      groundAngle: stance.groundAngle,
      wind: ctx.wind,
      windAdd: ctx.windAdd,
      world,
      shapes: ctx.shapes,
    }));
    let sc = 0;
    let bad = false;
    for (const p of pellets) {
      const s = scoreImpact(p, ctx, 'spread');
      if (s <= -1e5) bad = true;
      sc += Math.max(0, s);
    }
    // scoreImpact for spread zeroes token damage; recompute per pellet as spread damage.
    if (!bad) {
      sc = 0;
      for (const p of pellets) {
        for (const c of ctx.chickens) if (splashDamage(p, c, world, 55) > 0.2) bad = true;
        if (ctx.needTokens) {
          for (const s of ctx.tokens) {
            const dealt = splashDamage(p, s, world, 55);
            const hp = s.obj.hp || 1;
            if (dealt >= hp - 0.5) sc += 1300;
            else if (dealt > 0.2) sc += 1000 * (dealt / hp);
          }
        }
      }
      if (!bad && sc > best.score && sc >= 800) best = { ...best, score: sc, kind: 'spread' };
    }
  }
  return best;
}

function fallback(obs) {
  const objs = live(obs);
  const chicken = objs.find((o) => o.type === 'chicken');
  let dir = obs.actor.x < 800 ? 1 : -1;
  if (chicken && Math.sign(chicken.x - obs.actor.x) === dir) dir = -dir || 1;
  return { attack: { kind: 'single', angle: 75, power: 28, dir } };
}

export function decide(obs) {
  const t0 = performance.now();
  adapt(obs);
  const world = makeGrid(obs.terrain);
  const best = search(obs, world, t0);
  if (!best || best.score < -1e5) {
    process.stderr.write('fallback\n');
    return fallback(obs);
  }
  const action = { attack: { kind: best.kind, angle: best.angle | 0, power: best.power | 0, dir: best.dir > 0 ? 1 : -1 } };
  holeMem.last = {
    predX: best.impact.x,
    predY: best.impact.y,
    t: best.impact.t,
    key: best.key,
    objectId: best.impact.object?.id ?? null,
  };
  process.stderr.write(
    `${best.kind} a${action.attack.angle} p${action.attack.power} d${action.attack.dir} mv${action.moveX ?? '-'} sc${best.score.toFixed(0)} pred${best.impact.x.toFixed(0)},${best.impact.y.toFixed(0)} ${best.impact.type} ${(performance.now() - t0).toFixed(0)}ms windAdd${holeMem.windAdd.toFixed(1)}\n`
  );
  return action;
}

export function answerExam(msg) {
  const ground = msg.ground ?? 780;
  const mh = msg.config?.BODY?.muzzleHeight ?? 35;
  const predictions = (msg.questions || []).map((q) => {
    const r = predictFlat({
      x: q.x,
      angle: q.angle,
      power: q.power,
      dir: q.dir,
      wind: q.wind,
      y0: ground - mh,
      ground,
      groundAngle: 0,
    });
    return { id: q.id, x: Math.round(r.x * 100) / 100, y: ground };
  });
  return { predictions };
}
