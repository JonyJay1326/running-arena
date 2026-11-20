// Golf bot. Physics in phys.mjs; strategy: grid search of (stand x, dir, angle, power, kind) with
// full trajectory simulation against terrain + objects, scoring per goal.
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { WIND_K, windHeightFactor, launch, flatLanding, FOOT } from './phys.mjs';

const log = (...a) => process.stderr.write(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' ') + '\n');
const MEMDIR = 'bot/memory';
const MEMFILE = MEMDIR + '/mem.json';
function loadMem() { try { return JSON.parse(readFileSync(MEMFILE, 'utf8')); } catch { return {}; } }
function saveMem(m) { try { if (!existsSync(MEMDIR)) mkdirSync(MEMDIR, { recursive: true }); writeFileSync(MEMFILE, JSON.stringify(m)); } catch { } }

let config = null, mem = loadMem();
let state = { windScale: 1, prev: null, bugShots: [], bugCorr: 1 };

// ---------- terrain ----------
function decodeTerrain(t) {
  const g = new Uint8Array(t.width * t.height);
  let i = 0;
  for (const [v, n] of t.rle) { if (v) g.fill(1, i, i + n); i += n; }
  return { w: t.width, h: t.height, c: t.cell, g };
}
function solid(T, x, y) {
  if (x < 0 || x >= 1600 || y < 0 || y >= 900) return false;
  return T.g[((y / 4) | 0) * T.w + ((x / 4) | 0)] === 1;
}
// surfaces (y of top of solid run) at column x
function surfaces(T, x) {
  const cx = (x / 4) | 0, out = [];
  if (cx < 0 || cx >= T.w) return out;
  for (let cy = 0; cy < T.h; cy++) {
    if (T.g[cy * T.w + cx] && (cy === 0 || !T.g[(cy - 1) * T.w + cx])) out.push(cy * 4);
  }
  return out;
}
function surfaceNear(T, x, yRef, maxUp = 40, maxDown = 60) {
  let best = null;
  for (const s of surfaces(T, x)) {
    const d = s - yRef;
    if (d >= -maxUp && d <= maxDown && (best == null || Math.abs(d) < Math.abs(best - yRef))) best = s;
  }
  return best;
}
// highest-first surface under a point (first solid going down from y)
function groundBelow(T, x, y) {
  for (let yy = Math.max(0, y); yy < 900; yy += 4) if (solid(T, x, yy)) return Math.floor(yy / 4) * 4;
  return null;
}

// ---------- objects ----------
function objBox(o) {
  if (o.type === 'bug') { const r = o.radius ?? 14; return { x0: o.x - r, x1: o.x + r, y0: o.y - 2 * r, y1: o.y }; }
  const def = config?.OBJECTS?.[o.type] || {};
  const w = o.width ?? def.width ?? 28, h = o.height ?? def.height ?? 28;
  return { x0: o.x - w / 2, x1: o.x + w / 2, y0: o.y - h, y1: o.y };
}
function boxDist(b, x, y) {
  const dx = Math.max(b.x0 - x, 0, x - b.x1), dy = Math.max(b.y0 - y, 0, y - b.y1);
  return Math.hypot(dx, dy);
}
const alive = o => !o.removed && !o.broken && !o.inRecycle;
const isTok = o => o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken';

// ---------- trajectory ----------
// returns {x,y,type:'ground'|'obj'|'out', obj, minChicken}
function fly(T, objs, L, wind, windScale, isPlane = false) {
  let { x, y, vx, vy } = L;
  const boxes = isPlane ? [] : objs.map(o => ({ o, b: objBox(o) }));
  let minCh = 1e9, minY = 1e9, minX = 1e9, maxX = -1e9;
  for (const { b } of boxes) { minY = Math.min(minY, b.y0); minX = Math.min(minX, b.x0); maxX = Math.max(maxX, b.x1); }
  const wk = WIND_K * wind * windScale;
  let apex = y;
  for (let i = 0; i < 6000; i++) {
    const sp = Math.hypot(vx, vy) + 1e-9;
    const dt = Math.min(0.25, 2.5 / sp);
    const ax = wk * windHeightFactor(y);
    const nvx = vx + ax * dt, nvy = vy + dt;
    x += (vx + nvx) / 2 * dt; y += (vy + nvy) / 2 * dt; vx = nvx; vy = nvy;
    if (y < apex) apex = y;
    if (x < -50 || x > 1650 || y > 900) return { x, y, type: 'out', minCh, apex };
    if (y >= minY && x >= minX && x <= maxX) for (const { o, b } of boxes) {
      if (o.type === 'bug') {
        const cy = o.y - (o.radius ?? 14);
        if (Math.hypot(x - o.x, y - cy) <= (o.radius ?? 14)) return { x, y, type: 'obj', obj: o, minCh, apex };
      } else if (x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1) return { x, y, type: 'obj', obj: o, minCh, apex };
    }
    if (solid(T, x, y)) return { x, y, type: 'ground', minCh, apex };
  }
  return { x, y, type: 'out', minCh };
}

// line of sight (no terrain) between explosion and object box center-ish
function los(T, x0, y0, x1, y1) {
  const n = Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 3);
  for (let i = 1; i < n; i++) { const t = i / n; if (solid(T, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)) return false; }
  return true;
}
function splashDamage(T, kind, ex, ey, o) {
  const a = config.ATTACKS[kind]; const b = objBox(o);
  const d = boxDist(b, ex, ey);
  if (d > a.damageRadius) return 0;
  const cx = Math.min(Math.max(ex, b.x0), b.x1), cy = Math.min(Math.max(ey, b.y0), b.y1);
  // back off from explosion slightly (explosion is at terrain surface)
  if (d > 0 && !los(T, ex - (cx - ex) * 0.0, ey - 2, cx, cy) && !los(T, ex, ey - 6, cx, cy)) return 0;
  return a.damage * (1 - d / a.damageRadius);
}

function carve(T, x, y, r) {
  const c0 = Math.max(0, ((x - r) / 4) | 0), c1 = Math.min(T.w - 1, ((x + r) / 4) | 0);
  const r0 = Math.max(0, ((y - r) / 4) | 0), r1 = Math.min(T.h - 1, ((y + r) / 4) | 0);
  for (let cy = r0; cy <= r1; cy++) for (let cx = c0; cx <= c1; cx++) {
    const px = cx * 4 + 2, py = cy * 4 + 2;
    if ((px - x) ** 2 + (py - y) ** 2 <= r * r) T.g[cy * T.w + cx] = 0;
  }
}
// sequential burst simulation: each ground impact carves a crater before the next shell
function burstSeq(T, objs, L, wind, target) {
  const Tc = { ...T, g: T.g.slice() };
  let dmg = 0; const rs = [];
  for (let k = 0; k < 3; k++) {
    const r = fly(Tc, objs, L, wind, state.windScale);
    rs.push(r);
    if (r.type === 'out') break;
    if (chickenPenalty(Tc, r, objs) >= 1e9) return { ok: false, unsafe: true, rs };
    if (r.type === 'obj' && r.obj.id === target.id) dmg += 65;
    else dmg += splashDamage(Tc, 'burst', r.x, r.y, target);
    if (r.type === 'ground') carve(Tc, r.x, r.y, 26);
    if (r.type === 'obj' && r.obj.type !== 'bug' && r.obj.id !== target.id) break;
  }
  return { ok: dmg >= Math.min(target.hp ?? 1, 65) - 1e-6 && dmg > 0, dmg, rs };
}

// multi-shell kinds: how many tokens die (spread = 3 simultaneous at -6/0/+6 deg; burst = sequential)
function multiSim(T, objs, c, kind, wind, bug) {
  const A = config.ATTACKS[kind];
  const hp = new Map(objs.filter(isTok).map(o => [o.id, o.hp ?? 1]));
  let live = objs.slice(); let kills = 0, near = 0;
  const Tc = kind === 'burst' ? { ...T, g: T.g.slice() } : T;
  const shells = kind === 'spread' ? A.offsets.map(off => ({ off })) : [{ off: 0 }, { off: 0 }, { off: 0 }];
  const applyHit = (r) => {
    if (r.type === 'out') return;
    if (chickenPenalty(Tc, r, live) >= 1e9) { kills = -99; return; }
    if (bug && Math.hypot(r.x - bug.x, r.y - bug.y + 14) < 55) near++;
    for (const o of live) if (isTok(o) && hp.has(o.id)) {
      const dmg = (r.type === 'obj' && r.obj.id === o.id) ? A.damage : splashDamage(Tc, kind, r.x, r.y, o);
      if (dmg > 0) { const h = hp.get(o.id) - dmg; hp.set(o.id, h); if (h <= 0) { kills++; hp.delete(o.id); } }
    }
  };
  if (kind === 'spread') {
    const rs = shells.map(sh => fly(Tc, live, launch(c.st.x, c.st.footY, c.st.gA, c.ang + sh.off, c.pw, c.dir), wind, state.windScale));
    for (const r of rs) { applyHit(r); if (kills < 0) return { kills: -1 }; }
  } else {
    for (const sh of shells) {
      const r = fly(Tc, live, launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir), wind, state.windScale);
      applyHit(r); if (kills < 0) return { kills: -1 };
      live = live.filter(o => !isTok(o) || hp.has(o.id));
      if (r.type === 'ground') carve(Tc, r.x, r.y, A.radius);
      if (r.type === 'out') break;
    }
  }
  return { kills, near };
}

// ---------- standing positions ----------
function standOptions(T, obs) {
  const a = obs.actor;
  const footY = a.y + FOOT;
  const opts = [{ x: a.x, footY, gA: a.groundAngle, move: false }];
  if (!obs.hole.teeActive) return opts;
  const { x0, x1 } = obs.hole.tee;
  // walk along connected surface from current x in both directions
  for (const dirn of [-1, 1]) {
    let px = a.x, py = footY; const pts = [];
    for (let x = a.x + dirn * 4; dirn < 0 ? x >= x0 + 2 : x <= x1 - 2; x += dirn * 4) {
      const s = surfaceNear(T, x, py, 10, 10);
      if (s == null) break;
      // check headroom
      if (solid(T, x, s - 20) || solid(T, x, s - 40) || solid(T, x, s - 60) || solid(T, x - 12, s - 30) || solid(T, x + 12, s - 30)) break;
      py = s; px = x; pts.push({ x, footY: s });
    }
    // sample a few points along path
    const n = pts.length;
    for (const f of [0.25, 0.5, 0.75, 1]) {
      const k = Math.floor(f * n) - 1; if (k < 2) continue;
      const p = pts[k];
      // flatness check
      const l = surfaceNear(T, p.x - 8, p.footY, 8, 8), r = surfaceNear(T, p.x + 8, p.footY, 8, 8);
      if (l == null || r == null) continue;
      // positive groundAngle <=> surface rising to the right (consistent with angle + dir*gA)
      const gA = Math.atan2(l - r, 16) * 180 / Math.PI;
      if (Math.abs(gA) > 20) continue;
      if (obs.hole.tee.minFeet != null && p.footY < obs.hole.tee.minFeet - 1 && footY >= obs.hole.tee.minFeet - 1) continue;
      opts.push({ x: Math.round(p.x), footY: p.footY, gA, move: true });
    }
  }
  return opts;
}

// ---------- search ----------
function search(T, obs, scoreFn0, kinds, deadline, refineMs = 160, opt = {}) {
  // hedge: wind-height model only fitted from 3 flat-field shots -> mildly prefer low apex when windy
  const wAbs = Math.abs(obs.hole.wind || 0);
  const scoreFn = wAbs ? (r, c) => scoreFn0(r, c) + 0.005 * wAbs * Math.max(0, 440 - (r.apex ?? 1e9)) : scoreFn0;
  const objs = obs.objects.filter(alive);
  const wind = obs.hole.wind;
  const stands = opt.stands || standOptions(T, obs);
  const aS = opt.angStep || 3, pS = opt.pwStep || 2.5;
  const cands = [];
  const coarse = [];
  for (const st of stands) for (const dir of [1, -1]) for (let ang = 4; ang <= 85; ang += aS) for (let pw = 12; pw <= 100; pw += pS) {
    coarse.push({ st, dir, ang, pw });
  }
  // deterministic shuffle so a time cut-off still samples all stands/directions
  let seed = 12345;
  for (let k = coarse.length - 1; k > 0; k--) { seed = (seed * 1103515245 + 12345) & 0x7fffffff; const j = seed % (k + 1); const tmp = coarse[k]; coarse[k] = coarse[j]; coarse[j] = tmp; }
  let best = [];
  const tEnd = deadline;
  let i = 0;
  for (; i < coarse.length; i++) {
    if ((i & 63) === 0 && Date.now() > tEnd) break;
    const c = coarse[i];
    const L = launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir);
    const r = fly(T, objs, L, wind, state.windScale);
    const s = scoreFn(r, c);
    if (s < 1e8) { best.push({ s, c, r }); }
  }
  best.sort((a, b) => a.s - b.s);
  best = best.slice(0, 12);
  // refine
  const refineEnd = deadline + refineMs;
  const out = [];
  for (const b of best) {
    let cur = b;
    for (const step of [1.5, 0.75, 0.3]) for (let it = 0; it < 5; it++) {
      const before = cur.s;
      for (const [da, dp] of [[step, 0], [-step, 0], [0, step], [0, -step], [step, step], [-step, -step], [step, -step], [-step, step]]) {
        if (Date.now() > refineEnd) break;
        const ang = cur.c.ang + da, pw = cur.c.pw + dp;
        if (ang < 0 || ang > 85 || pw < 1 || pw > 100) continue;
        const c = { ...cur.c, ang, pw };
        const L = launch(c.st.x, c.st.footY, c.st.gA, ang, pw, c.dir);
        const r = fly(T, objs, L, wind, state.windScale);
        const s = scoreFn(r, c);
        if (s < cur.s) cur = { s, c, r };
      }
      if (cur.s >= before) break;
    }
    // robustness: check neighbors
    let rob = 0;
    if (Date.now() > refineEnd + 60) { out.push({ ...cur, total: cur.s + 60 }); continue; }
    for (const [da, dp] of [[0.6, 0], [-0.6, 0], [0, 0.6], [0, -0.6]]) {
      const c = { ...cur.c, ang: cur.c.ang + da, pw: Math.min(100, cur.c.pw + dp) };
      const L = launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir);
      const r = fly(T, objs, L, wind, state.windScale);
      rob += Math.min(scoreFn(r, c), 400);
    }
    out.push({ ...cur, total: cur.s + rob * 0.15 });
  }
  out.sort((a, b) => a.total - b.total);
  if (!opt.stands) state.lastOut = out;
  log('search evaluated', i, 'of', coarse.length, 'best', out[0] && { s: out[0].s, total: out[0].total, c: { x: out[0].c.st.x, d: out[0].c.dir, a: out[0].c.ang, p: out[0].c.pw }, r: { x: out[0].r.x, y: out[0].r.y, t: out[0].r.type, id: out[0].r.obj?.id } });
  return out[0];
}

function chickenPenalty(T, r, objs) {
  const HARD = state.softChicken ? 90 : 1e9;
  if (r.type === 'obj' && r.obj.type === 'chicken') return HARD;
  let p = 0;
  for (const o of objs) if (o.type === 'chicken' && alive(o)) {
    const d = boxDist(objBox(o), r.x, r.y);
    if (d < 42) p += HARD; else if (d < 70) p += (70 - d) * 4;
  }
  return p;
}

// ---------- strategies ----------
function tokenScoreFn(T, obs, target, kind) {
  const objs = obs.objects.filter(alive);
  const b = objBox(target);
  const tx = (b.x0 + b.x1) / 2, ty = (b.y0 + b.y1) / 2;
  const bug = objs.find(o => o.type === 'bug');
  const needBug = obs.hole.goal !== 'tokens';
  return (r, c) => {
    if (r.type === 'out') return 1e9;
    let s = chickenPenalty(T, r, objs);
    if (s >= 1e9) return 1e9;
    if (r.type === 'obj' && r.obj.id === target.id && kind === 'burst' && c) {
      // later shells may fly on if the target breaks: make sure the continuation is chicken-safe
      const L = launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir);
      const r2 = fly(T, objs.filter(o => o.id !== target.id), L, obs.hole.wind, state.windScale);
      if (r2.type !== 'out' && chickenPenalty(T, r2, objs) >= 1e9) return 1e9;
    }
    if (r.type === 'obj' && r.obj.id === target.id) {
      s += Math.hypot(r.x - tx, r.y - ty) * 0.2;
      // pushToken surviving the hit gets shoved along the shell direction: avoid shoving it into a pit
      if (target.type === 'pushToken' && c && (target.hp ?? 150) > config.ATTACKS[kind].damage) {
        const px = target.x + 45 * c.dir;
        const gb = groundBelow(T, px, target.y - 4);
        if (gb == null) s += 0; // falls off the map -> counts as broken: fine
        else if (gb - target.y > 40) s += 25;
      }
    }
    else if (r.type === 'obj' && isTok(r.obj)) s += 15 + Math.hypot(r.x - tx, r.y - ty) * 0.5;
    else {
      const d = boxDist(b, r.x, r.y);
      const dmg = splashDamage(T, kind, r.x, r.y, target);
      s += 20 + d * 1.0 + (dmg >= (target.hp ?? 1) ? -10 : 30);
    }
    if (needBug && bug) {
      const db = Math.hypot(r.x - bug.x, r.y - (bug.y - 14));
      if (db < 70) {
        const rc = obs.hole.recycle;
        const rcx = rc ? (rc.x0 + rc.x1) / 2 : bug.x;
        const toward = Math.sign(bug.x - r.x) === Math.sign(rcx - bug.x);
        s += toward ? (70 - db) * 0.3 : (70 - db) * 1.2;
      }
    }
    return s;
  };
}

function chooseKind(o) {
  const hp = o.hp ?? 1;
  if (o.type === 'token' || hp <= 55) return 'single';
  if (o.type === 'bigToken') return hp <= 120 ? 'single' : (hp <= 190 || hp > 240 ? 'burst' : 'single');
  if (o.type === 'pushToken') return 'single';
  return 'single';
}

// bug push model (lab1, flat ground, explosion on ground beside bug): moved = K*(1-d/R)^2
// data: d=15.8 -> 640, d=29.4 -> 349, d=44.4 -> 141; direct hit from above -> ~0 (pushed into ground)
// refit with Euclidean distance explosion->bug centre: e=21.1->640, 32.6->349, 46.5->141  => K=1330, R=69
// lab3: explosion inside slope below bug (no line of sight) -> bug did not move at all.
function pushParams() { const m = mem.push3 || {}; return { K: m.K ?? 1330, R: m.R ?? 69 }; }
function pushModel(d) { const { K, R } = pushParams(); return d >= R ? 0 : K * (1 - d / R) ** 2; }
function chooseBugOffset(need) {
  const { K, R } = pushParams();
  const dmin = 18;
  const d = R * (1 - Math.sqrt(Math.max(need, 1) / K));
  return Math.max(dmin, Math.min(R - 6, d));
}

function recycleTarget(T, obs, bug) {
  const rc = obs.hole.recycle;
  const cands = [];
  for (let x = rc.x0 + 16; x <= rc.x1 - 16; x += 4) {
    const s = groundBelow(T, x, Math.max(0, rc.y0 - 40));
    if (s == null) continue;
    const cy = s - 14;
    if (cy >= rc.y0 + 2 && cy <= rc.y1 - 2) cands.push(x);
  }
  if (!cands.length) return null;
  // middle of the valid range; if the far side is near the map edge (overshoot = falls out), aim short
  let k = 0.5;
  if (bug.x < cands[0] && rc.x1 > 1480) k = 0.35;
  if (bug.x > cands[cands.length - 1] && rc.x0 < 120) k = 0.65;
  return cands[Math.min(cands.length - 1, Math.floor(cands.length * k))];
}

function bugPlan(T, obs, deadline, refineMs = 160) {
  const objs = obs.objects.filter(alive);
  const bug = obs.objects.find(o => o.type === 'bug' && !o.removed && !o.inRecycle);
  if (!bug) return null;
  const rc = obs.hole.recycle;
  let tx = recycleTarget(T, obs, bug);
  if (tx == null) {
    // recycle covered: dig it open
    const cx = (rc.x0 + rc.x1) / 2, cy = Math.max(rc.y0, Math.min(rc.y1, (rc.y0 + rc.y1) / 2));
    const best = search(T, obs, r => r.type === 'out' ? 1e9 : chickenPenalty(T, r, objs) + Math.hypot(r.x - cx, r.y - cy) + (r.type === 'obj' && r.obj.type === 'bug' ? 200 : 0), ['dig'], deadline);
    return best && { best, kind: 'dig' };
  }
  const need = tx - bug.x;
  const side = need > 0 ? -1 : 1; // explosion on opposite side
  const gT = groundBelow(T, tx, Math.max(0, rc.y0 - 40)) ?? bug.y;
  const dropF = 1 + 0.0025 * Math.max(0, Math.min(200, gT - bug.y));
  const corr = state.bugCorr * dropF;
  let d = chooseBugOffset(Math.abs(need) / corr);
  // dev-only calibration schedule (file removed before competition)
  try {
    const cf = MEMDIR + '/calib.json';
    if (existsSync(cf)) { const cal = JSON.parse(readFileSync(cf, 'utf8')); if (cal.offsets?.length) { d = cal.offsets.shift(); writeFileSync(cf, JSON.stringify(cal)); log('CALIB d', d); } }
  } catch { }
  const bcx = bug.x, bcy = bug.y - (bug.radius ?? 14);
  state.bugAim = { bx: bcx, by: bcy, d, need, side, corr };
  const fn = r => {
    if (r.type === 'out') return 1e9;
    let s = chickenPenalty(T, r, objs);
    if (r.type === 'obj' && r.obj.type === 'bug') return s + 300;
    if (r.type === 'obj' && r.obj.type !== 'bug') s += 60;
    const e = Math.hypot(r.x - bcx, r.y - bcy);
    const rel = (r.x - bcx) * side;           // >0 means on the correct (pushing) side
    if (rel < 0.4 * e) s += 120 + (0.4 * e - rel) * 3;
    else if (rel < 0.7 * e) s += (0.7 * e - rel) * 1.0;
    // line of sight from explosion (backed up a bit out of the terrain) to bug centre
    const ux = (bcx - r.x) / (e + 1e-9), uy = (bcy - r.y) / (e + 1e-9);
    if (!los(T, r.x + ux * 5, r.y + uy * 5 - 2, bcx, bcy)) s += 150;
    return s + Math.abs(e - d) * 1.6;
  };
  const best = search(T, obs, fn, ['single'], deadline, refineMs);
  return best && { best, kind: 'single', fn };
}

// ---------- paper plane ----------
function planeLandings(T, obs) {
  const a = obs.actor, fx = a.x, fy = a.y + FOOT;
  const res = [];
  const seen = new Set();
  for (const dir of [1, -1]) for (let ang = 10; ang <= 85; ang += 5) for (let pw = 15; pw <= 100; pw += 2.5) {
    const L = launch(fx, fy, a.groundAngle, ang, pw, dir, true);
    const r = fly(T, [], L, obs.hole.wind, state.windScale, true);
    if (r.type !== 'ground' || r.x < 30 || r.x > 1570) continue;
    const sy = surfaceNear(T, r.x, r.y, 24, 24);
    if (sy == null) continue;
    if (solid(T, r.x, sy - 20) || solid(T, r.x, sy - 40) || solid(T, r.x, sy - 60)) continue;
    const l = surfaceNear(T, r.x - 8, sy, 8, 8), rr = surfaceNear(T, r.x + 8, sy, 8, 8);
    if (l == null || rr == null) continue;
    const gA = Math.atan2(l - rr, 16) * 180 / Math.PI;
    if (Math.abs(gA) > 15) continue;
    // landing robustness: neighbours in power should land nearby
    const key = Math.round(r.x / 20) + ':' + Math.round(sy / 20);
    if (seen.has(key)) continue; seen.add(key);
    res.push({ x: r.x, footY: sy, gA, ang, pw, dir });
  }
  return res;
}
function planePlan(T, obs, fn, focus, t0) {
  const lands = planeLandings(T, obs);
  if (!lands.length) return null;
  // prefer landing 150..650 px from the focus point
  for (const l of lands) { const d = Math.abs(l.x - focus.x); l.h = Math.abs(d - 350) + Math.max(0, l.footY - focus.y - 60) * 2; }
  lands.sort((a, b) => a.h - b.h);
  let best = null;
  for (const l of lands.slice(0, 6)) {
    if (Date.now() - t0 > 640) break;
    const st = { x: l.x, footY: l.footY, gA: l.gA, move: false };
    const b = search(T, obs, fn, null, Math.min(Date.now() + 60, t0 + 600), 30, { stands: [st], angStep: 4, pwStep: 3 });
    if (b && (!best || b.total < best.b.total)) best = { b, l };
  }
  return best;
}

// ---------- learning ----------
function learnFromLast(T, obs) {
  const lr = obs.lastResult; const prev = state.prev;
  if (!lr || !prev || !lr.impacts?.length) return;
  const imp = lr.impacts[0];
  // wind/model correction: re-sim previous command with windScale range, compare to first impact
  if (prev.wind !== 0 && prev.kind !== 'spread' && prev.T && imp && prev.r && prev.r.type !== 'out' && imp.x != null) {
    let bestS = state.windScale, bestE = 1e9;
    for (let ws = 0.6; ws <= 1.4001; ws += 0.02) {
      const r = fly(prev.T, prev.objs, prev.L, prev.wind, ws);
      const e = Math.hypot(r.x - imp.x, r.y - imp.y);
      if (e < bestE) { bestE = e; bestS = ws; }
    }
    const err = Math.hypot(prev.r.x - imp.x, prev.r.y - imp.y);
    if (err > 12 && bestE < err * 0.5) { state.windScale = state.windScale * 0.5 + bestS * 0.5; state.windLearned = true; recordLearned(); }
    log('impact err', err.toFixed(1), 'windScale ->', state.windScale.toFixed(3));
  }
  // bug push learning
  if (prev.bugAim) {
    const ch = (lr.objectChanges || []).find(c => c.before && c.after && obs.objects.find(o => o.id === c.id && o.type === 'bug'));
    const d = Math.hypot(imp.x - prev.bugAim.bx, imp.y - prev.bugAim.by);
    const moved = ch ? Math.abs(ch.after.x - ch.before.x) : 0;
    const bugIds = new Set(obs.objects.filter(o => o.type === 'bug').map(o => o.id));
    const penal = (lr.penalties || []).some(p => bugIds.has(p.objectId) || (p.reason && /bug/i.test(p.reason)));
    const flatish = ch && Math.abs(ch.after.y - ch.before.y) < 24 && !penal;
    if (d < 60) {
      mem.pushObs = (mem.pushObs || []).concat([{ d, moved, dy: ch ? ch.after.y - ch.before.y : 0 }]).slice(-30);
      if (flatish && moved > 3 && d > 6) {
        const pr = pushParams();
        const pred = Math.max(1, pushModel(d));
        const ratio = Math.max(0.5, Math.min(2, moved / pred));
        mem.push3 = { K: pr.K * Math.sqrt(ratio), R: pr.R };
      }
      if (penal) state.bugCorr = Math.min(3, state.bugCorr * 1.5);
      else if (ch && moved > 3 && d > 6) {
        const pred = Math.max(5, pushModel(d) * prev.bugAim.corr);
        const ratio = Math.max(0.4, Math.min(2.5, moved / pred));
        state.bugCorr = Math.max(0.3, Math.min(3, state.bugCorr * Math.pow(ratio, 0.6)));
      }
      state.bugLearned = true; recordLearned();
      log('bugCorr', state.bugCorr);
      saveMem(mem);
      log('bug push obs d', d.toFixed(1), 'moved', moved.toFixed(1), 'model', mem.push);
    }
  }
}

// cross-hole priors: half-strength (in log space) geometric mean of what earlier holes learned
function initPriors(holeId) {
  state.holeId = holeId;
  const prior = (obj) => {
    const v = Object.entries(obj || {}).filter(([k]) => k !== String(holeId)).map(([, x]) => Math.log(x)).filter(Number.isFinite);
    if (!v.length) return 1;
    const m = v.reduce((a, b) => a + b, 0) / v.length;
    return Math.exp(Math.max(-0.4, Math.min(0.4, m * 0.5)));
  };
  state.bugCorr = prior(mem.bugCorrByHole);
  state.windScale = prior(mem.windScaleByHole);
  log('priors', { bugCorr: state.bugCorr, windScale: state.windScale });
}
function recordLearned() {
  if (state.holeId == null) return;
  mem.bugCorrByHole = mem.bugCorrByHole || {}; mem.windScaleByHole = mem.windScaleByHole || {};
  if (state.bugLearned) mem.bugCorrByHole[state.holeId] = state.bugCorr;
  if (state.windLearned) mem.windScaleByHole[state.holeId] = state.windScale;
  saveMem(mem);
}

// ---------- turn ----------
function decide(obs) {
  const t0 = Date.now();
  const T = decodeTerrain(obs.terrain);
  if (!state.bugInit) { const b = obs.objects.find(o => o.type === 'bug' && !o.removed && !o.inRecycle); if (b && obs.strokes === 0) state.bugInit = { x: b.x, y: b.y }; }
  learnFromLast(T, obs);
  if (state.planeExpect) {
    log('plane landed', { x: obs.actor.x, footY: obs.actor.y + FOOT, expect: state.planeExpect, teeActive: obs.hole.teeActive });
    mem.planeObs = (mem.planeObs || []).concat([{ ax: obs.actor.x, ay: obs.actor.y + FOOT, ex: state.planeExpect.x, ey: state.planeExpect.y }]).slice(-10);
    saveMem(mem); state.planeExpect = null;
  }
  state.bugAim = null;
  const objs = obs.objects.filter(alive);
  const goal = obs.hole.goal;
  const toks = objs.filter(isTok);
  let plan = null;
  const deadline = t0 + 380;
  const bugAlive = obs.objects.some(o => o.type === 'bug' && !o.removed && !o.inRecycle);
  const wantTok = (goal === 'tokens' || goal === 'both') && toks.length;
  const wantBug = (goal === 'bug' || goal === 'both') && bugAlive;
  if (wantTok) {
    // pick target: evaluate all tokens in one sweep with combined score (min over targets)
    const fns = toks.map(o => ({ o, kind: chooseKind(o), fn: tokenScoreFn(T, obs, o, chooseKind(o)) }));
    let bestTarget = null;
    const fn = (r, c) => {
      let m = 1e9, mt = null;
      for (const f of fns) { const s = f.fn(r, c); if (s < m) { m = s; mt = f; } }
      return m;
    };
    const nWeak = toks.filter(o => (o.hp ?? 1) <= 65).length;
    const best = search(T, obs, fn, null, nWeak >= 2 ? t0 + 300 : deadline);
    if (best) {
      for (const f of fns) if (f.fn(best.r, best.c) <= best.s + 1e-9) { bestTarget = f; break; }
      let kind = bestTarget ? bestTarget.kind : 'single';
      // blocked by terrain far from target -> dig
      if (best.r.type === 'ground' && bestTarget) {
        const d = boxDist(objBox(bestTarget.o), best.r.x, best.r.y);
        if (d > 28) kind = 'dig';
      }
      plan = { best, kind, target: bestTarget?.o, fn, focus: bestTarget ? { x: bestTarget.o.x, y: bestTarget.o.y } : null };
      // no safe progress (e.g. token glued to a chicken): accept a 1-stroke chicken penalty if that breaks a token
      const chickens = objs.filter(o => o.type === 'chicken');
      if (best.s > 45 && chickens.length && Date.now() - t0 < 380 && obs.strokes + 2 <= obs.hole.cap) {
        state.softChicken = true;
        const sfn = (r, c) => (r.type === 'obj' && isTok(r.obj)) ? fn(r, c) : 1e9;
        const sb = search(T, obs, sfn, null, Date.now() + 120, 60, { stands: standOptions(T, obs) });
        state.softChicken = false;
        const safeProgress = best.r.type === 'obj' && isTok(best.r.obj);
        if (sb && sb.s < 300 && !safeProgress) {
          log('accepting chicken risk', sb.s, 'vs', best.s);
          plan = { ...plan, best: sb, kind: chooseKind(sb.r.obj), target: sb.r.obj, tunnel: true };
        }
      }
      // occluded target: try burst tunnelling (shell 1 digs, later shells pass through)
      if (best.r.type === 'ground' && bestTarget && (state.lastOut || []).length) {
        const tgt = bestTarget.o;
        let pick = null;
        for (const cand of state.lastOut) {
          if (Date.now() - t0 > 560) break;
          const c = cand.c;
          const L = launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir);
          const bs = burstSeq(T, objs, L, obs.hole.wind, tgt);
          if (bs.ok && (!pick || bs.dmg > pick.bs.dmg)) pick = { cand, bs };
        }
        if (pick) { log('burst tunnel', pick.bs.dmg); plan = { ...plan, best: pick.cand, kind: 'burst', target: tgt, tunnel: true }; }
      }
      // multi-kill: spread / burst killing >= 2 tokens in one stroke
      const weak = toks.filter(o => (o.hp ?? 1) <= 65);
      if (weak.length >= 2 && Date.now() - t0 < 400) {
        const bugO = wantBug ? objs.find(o => o.type === 'bug') : null;
        const evalC = (c) => {
          let bestK = null;
          for (const kind of ['spread', 'burst']) {
            const m = multiSim(T, objs, c, kind, obs.hole.wind, bugO);
            if (m.kills >= 2 && (!bestK || m.kills > bestK.kills || (m.kills === bestK.kills && m.near < bestK.near))) bestK = { kind, ...m };
          }
          return bestK;
        };
        const mfn = (r, c) => {
          if (r.type === 'out') return 1e9;
          // quick prefilter: centre shell must land near some weak token
          let dm = 1e9; for (const o of weak) dm = Math.min(dm, boxDist(objBox(o), r.x, r.y));
          if (dm > 90) return 1e9;
          const k = evalC(c);
          return k ? 300 - 100 * k.kills + 20 * k.near + dm * 0.05 : 1e9;
        };
        const stands = [best.c.st];
        const mb = search(T, obs, mfn, null, t0 + 500, 40, { stands, angStep: 2, pwStep: 1.5 });
        if (mb && mb.s < 150) {
          const k = evalC(mb.c);
          if (k) { log('multi-kill', k.kind, k.kills); plan = { ...plan, best: mb, kind: k.kind, tunnel: true }; }
        }
      }
    }
  }
  if (!plan && wantBug) {
    plan = bugPlan(T, obs, t0 + 330);
    const bug = obs.objects.find(o => o.type === 'bug' && !o.removed && !o.inRecycle);
    const bi = state.bugInit;
    // stuck bug: if no decent push exists from here but the initial spot offers one, declare unplayable
    if (bug && bi && (!plan || plan.best.total > 110) && Math.hypot(bug.x - bi.x, bug.y - bi.y) > 40
        && obs.strokes + 3 <= obs.hole.cap && Date.now() - t0 < 430 && !state.usedUnplayable) {
      const saveAim = state.bugAim;
      const obs2 = { ...obs, objects: obs.objects.map(o => o.id === bug.id ? { ...o, x: bi.x, y: bi.y } : o) };
      const alt = bugPlan(T, obs2, Date.now() + 140, 80);
      state.bugAim = saveAim;
      log('stuck check: cur', plan?.best.total, 'alt', alt?.best.total);
      if (alt && alt.best.total < 40) { state.usedUnplayable = true; state.prev = null; return { unplayable: true }; }
    }
  }
  if (!plan) {
    log('no plan; safe fallback');
    const tg = objs.filter(o => o.type !== 'chicken');
    const best = search(T, obs, r => {
      if (r.type === 'out') return 5000;
      const p = chickenPenalty(T, r, objs); if (p >= 1e9) return 1e9;
      let m = 3000; for (const o of tg) m = Math.min(m, boxDist(objBox(o), r.x, r.y));
      return p + m;
    }, null, t0 + 300);
    if (best) plan = { best, kind: 'dig' };
    else return { attack: { kind: 'single', angle: 85, power: 5, dir: 1 } };
  }
  // paper plane when the tee offers nothing decent
  const forcePlane = (() => { try { return existsSync(MEMDIR + '/forceplane.flag'); } catch { return false; } })();
  if (obs.fly > 0 && obs.hole.teeActive && plan.fn && !plan.tunnel && obs.strokes + 2 <= obs.hole.cap && (plan.best.total > 60 || forcePlane) && Date.now() - t0 < 450) {
    let focus = plan.focus;
    if (!focus) { const b = obs.objects.find(o => o.type === 'bug' && alive(o)); if (b) focus = { x: b.x, y: b.y }; }
    if (focus) {
      const pp = planePlan(T, obs, plan.fn, focus, t0);
      log('plane option', pp && { total: pp.b.total, land: { x: pp.l.x | 0, y: pp.l.footY }, cur: plan.best.total });
      if (pp && (forcePlane ? pp.b.total < 40 : (pp.b.total < 25 && pp.b.total + 35 < plan.best.total))) {
        state.prev = null;
        state.planeExpect = { x: pp.l.x, y: pp.l.footY };
        return { fly: { angle: pp.l.ang, power: pp.l.pw, dir: pp.l.dir } };
      }
    }
  }
  const c = plan.best.c;
  const act = { attack: { kind: plan.kind, angle: +c.ang.toFixed(2), power: +c.pw.toFixed(2), dir: c.dir } };
  if (c.st.move && obs.hole.teeActive) act.moveX = c.st.x;
  const L = launch(c.st.x, c.st.footY, c.st.gA, c.ang, c.pw, c.dir);
  state.prev = { T, objs, L, wind: obs.hole.wind, r: plan.best.r, bugAim: state.bugAim, kind: plan.kind };
  log('turn', obs.strokes, 'plan', plan.kind, 'target', plan.target?.id, 'pred', { x: plan.best.r.x | 0, y: plan.best.r.y | 0, t: plan.best.r.type }, 'ms', Date.now() - t0);
  return act;
}

function respond(r) {
  switch (r.type) {
    case 'init':
      config = r.config;
      try { initPriors(r.hole?.id); } catch { }
      return { ok: true };
    case 'turn': {
      if (!config) config = r.config;
      try { return decide(r); }
      catch (e) { log('ERR', e.stack); return { attack: { kind: 'single', angle: 45, power: 50, dir: 1 } }; }
    }
    case 'exam': {
      config = r.config;
      const g = r.ground ?? 780;
      return { predictions: r.questions.map(q => { const p = flatLanding(q.wind, q.x, q.angle, q.power, q.dir, g); return { id: q.id, x: +p.x.toFixed(2), y: g }; }) };
    }
    default:
      return {};
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let r;
  try { r = JSON.parse(line); } catch { continue; }
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
