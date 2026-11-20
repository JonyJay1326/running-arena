import { createInterface } from 'node:readline';
import fs from 'node:fs';
import { bugAfter } from './bugsim.mjs';
import { P, decodeTerrain, solidAt, objBox, objActive, flight, surfaceY, groundAngleAt, muzzleAt } from './phys.mjs';

const log = (...a) => process.stderr.write(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' ') + '\n');
const MEM = new URL('./memory/', import.meta.url);
function readMem(name, def) { try { return JSON.parse(fs.readFileSync(new URL(name, MEM), 'utf8')); } catch { return def; } }

// ---- 虫（Bug）被炸推走模型（可由 memory/params.json 与实验标定覆盖）----
const BUG = Object.assign({ Lmax: 160, R: 45, minL: 0 }, readMem('params.json', {}).bug || {});
const KINDS = {
  single: { dmg: 120, r: 32, n: 1 },
  burst: { dmg: 65, r: 26, n: 3 },
  spread: { dmg: 55, r: 24, n: 3 },
  dig: { dmg: 30, r: 60, n: 1 },
};

let S = null; // 当前洞状态
let GA_SIGN = 1;
let TURN_T0 = 0;
let HOLE = { prev: null };

function setState(r) {
  S = r;
  S.T = decodeTerrain(r.terrain);
  S.wind = r.hole.wind ?? 0;
  try {
    const mine = groundAngleAt(S.T, r.actor.x, r.actor.y + P.foot);
    if (Math.abs(r.actor.groundAngle) > 3 && Math.abs(mine) > 3) { const sg = Math.sign(mine) === Math.sign(r.actor.groundAngle) ? 1 : -1; if (sg !== GA_SIGN) log('GA_SIGN', sg, mine, r.actor.groundAngle); GA_SIGN = sg; }
  } catch {}
}

// ---- 站位 ----
function standPositions() {
  const { hole, actor } = S;
  const out = [];
  const footNow = actor.y + P.foot;
  if (!hole.teeActive) {
    out.push({ x: actor.x, foot: footNow, ga: actor.groundAngle, keep: true });
    return out;
  }
  const x0 = hole.tee.x0, x1 = hole.tee.x1;
  // 从当前位置向两侧扩展（高度变化受限）
  const reach = new Map();
  const probe = (x, nearY) => {
    const sy = surfaceY(S.T, x, nearY, 20, 20);
    if (sy == null) return null;
    // 身体空间检查：脚上方 8..60 的中心线不能是实心
    for (let h = 8; h <= 56; h += 8) if (solidAt(S.T, x, sy - h)) return null;
    return sy;
  };
  reach.set(Math.round(actor.x), footNow);
  for (const sgn of [-1, 1]) {
    let x = Math.round(actor.x), fy = footNow;
    for (let k = 0; k < 400; k++) {
      x += sgn * 4;
      if (x < x0 || x > x1) break;
      const sy = probe(x, fy);
      if (sy == null || Math.abs(sy - fy) > 8) break;
      fy = sy; reach.set(x, sy);
    }
  }
  const xs = [...reach.keys()].sort((a, b) => a - b);
  const mf = hole.tee.minFeet;
  const pick = [];
  const want = new Set([Math.round(actor.x), xs[0], xs[xs.length - 1]]);
  const n = 6;
  for (let i = 1; i < n; i++) want.add(xs[Math.floor(i * (xs.length - 1) / n)]);
  for (const x of want) {
    const fy = reach.get(x);
    if (fy == null) continue;
    if (mf != null && fy < mf) continue;
    const isCur = x === Math.round(actor.x);
    // 第二轮：换站位只在局部平地（坡面上我估计的 groundAngle/脚位与引擎不一致，第一轮02号洞落点偏58px）
    if (!isCur) { const l = surfaceY(S.T, x - 16, fy, 12, 12), r2 = surfaceY(S.T, x + 16, fy, 12, 12); if (l == null || r2 == null || Math.abs(l - fy) > 2 || Math.abs(r2 - fy) > 2) continue; }
    pick.push({ x, foot: fy, ga: isCur ? actor.groundAngle : 0, keep: isCur });
  }
  if (!pick.length) pick.push({ x: actor.x, foot: footNow, ga: actor.groundAngle, keep: true });
  return pick;
}

// ---- 评估 ----
function losBlocked(T, bx, by, tx, ty, r) {
  const d = Math.hypot(tx - bx, ty - by);
  const n = Math.max(1, Math.ceil(d / 3));
  for (let i = 1; i < n; i++) {
    const px = bx + (tx - bx) * i / n, py = by + (ty - by) * i / n;
    if (Math.hypot(px - bx, py - by) <= r) continue;
    if (solidAt(T, px, py)) return true;
  }
  return false;
}
function distToBox(b, x, y) {
  const dx = Math.max(b.x0 - x, 0, x - b.x1), dy = Math.max(b.y0 - y, 0, y - b.y1);
  return Math.hypot(dx, dy);
}
function outsideMask() {
  if (S._out) return S._out;
  const { w, h, g } = S.T;
  const m = new Uint8Array(w * h);
  const q = new Int32Array(w * h); let qh = 0, qt = 0;
  for (let x = 0; x < w; x++) if (!g[x]) { m[x] = 1; q[qt++] = x; }
  for (let y = 0; y < h; y++) { for (const x of [0, w - 1]) { const i = y * w + x; if (!g[i] && !m[i]) { m[i] = 1; q[qt++] = i; } } }
  while (qh < qt) {
    const i = q[qh++]; const x = i % w, y = (i / w) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
      const j = ny * w + nx;
      if (!g[j] && !m[j]) { m[j] = 1; q[qt++] = j; }
    }
  }
  S._out = m; return m;
}
// 被土层盖住的程度：从物体中心发射线，到达“外部空气”前穿过的实心长度的最小值（忽略炸坑内的格）
function coverOf(T, o, ex, ey, er, wantRay) {
  const mask = outsideMask();
  const b = objBox(o), cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
  let best = 1e9, bestRay = null;
  for (let a = 0; a < 360; a += 15) {
    const ca = Math.cos(a * Math.PI / 180), sa = Math.sin(a * Math.PI / 180);
    let run = 0, reached = false, entry = null, lastSolid = 0;
    for (let d = 10; d <= 300; d += 4) {
      const px = cx + ca * d, py = cy + sa * d;
      if (px < 0 || px >= 1600 || py < 0) { reached = true; break; }
      const inCrater = er > 0 && Math.hypot(px - ex, py - ey) <= er;
      const cxl = Math.floor(px / 4), cyl = Math.floor(py / 4);
      const solid = !inCrater && solidAt(T, px, py);
      if (solid) { run += 4; lastSolid = d; entry = entry || { x: px, y: py }; }
      else if (cyl < T.h && mask[cyl * T.w + cxl]) { reached = true; break; }
      else if (inCrater && er > 0) { /* 炸坑里的空气：继续 */ }
    }
    if (!reached) run = 300;
    if (run < best) { best = run; bestRay = { a, d: run, x: cx + ca * 0, y: cy }; bestRay.exit = { x: cx + ca * (lastSolid + 2), y: cy + sa * (lastSolid + 2) }; }
  }
  return wantRay ? { c: best, ray: bestRay } : best;
}

function recycleSolid() {
  if (S._rz) return S._rz;
  const r = S.hole.recycle; let total = 0, area = 0;
  for (let y = r.y0; y < r.y1; y += 4) for (let x = r.x0; x < r.x1; x += 4) { area++; if (solidAt(S.T, x + 2, y + 2)) total++; }
  return (S._rz = { total, area });
}
function solidInRegionAndCircle(cx, cy, rad) {
  const r = S.hole.recycle; let n = 0;
  const x0 = Math.max(r.x0, cx - rad), x1 = Math.min(r.x1, cx + rad), y0 = Math.max(r.y0, cy - rad), y1 = Math.min(r.y1, cy + rad);
  for (let y = Math.floor(y0 / 4) * 4; y < y1; y += 4) for (let x = Math.floor(x0 / 4) * 4; x < x1; x += 4) if (Math.hypot(x + 2 - cx, y + 2 - cy) <= rad && solidAt(S.T, x + 2, y + 2)) n++;
  return n;
}
function goalObjs() {
  const g = S.hole.goal;
  const objs = S.objects.filter(objActive);
  return {
    tokens: (g === 'tokens' || g === 'both') ? objs.filter(o => o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken') : [],
    bug: (g === 'bug' || g === 'both') ? objs.find(o => o.type === 'bug' && !o.inRecycle) : null,
    chickens: objs.filter(o => o.type === 'chicken'),
    all: objs,
  };
}

function recycleDist(x, y) {
  // x,y: bug 底部；以 bug 圆心(y-14) 到收缩后的回收区的距离
  const r = S.hole.recycle;
  if (!r) return 0;
  const mx = Math.min(40, (r.x1 - r.x0) * 0.25), my = Math.min(20, (r.y1 - r.y0) * 0.2);
  const cy = y - 14;
  const dx = Math.max(r.x0 + mx - x, 0, x - (r.x1 - mx));
  const dy = Math.max(r.y0 + my - cy, 0, cy - (r.y1 - my));
  return Math.hypot(dx, dy);
}
function inRecycle(x, y) {
  const r = S.hole.recycle;
  return r && x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
}

// blasts: [{x,y,kind}]（同一发的各颗），返回价值
function valueOf(blasts, G, info) {
  let v = 0;
  let bi = -1;
  const dmgTok = new Map();
  let chickHit = new Set();
  let bugPos = null, bugOut = false;
  for (const bl of blasts) {
    bi++;
    const K = KINDS[bl.kind];
    for (const o of G.tokens) {
      if (bl.kind === 'burst' && bi > 0 && o.type === 'pushToken') continue; // 后两发时它已被推走
      const b = objBox(o);
      const d = distToBox(b, bl.x, bl.y) + (distToBox(b, bl.x, bl.y) > 0.5 ? 5 : 0); // 实测：近距离非直击有一次未伤，保守加 5px
      if (d >= 30) continue;
      const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
      // 第二轮：08号洞#4 爆点在斜坡侧面、token 在坡顶，距盒13px 实测 0 伤害——引擎的遮挡判定几乎不豁免爆点周围的地形，改为只豁免5px
      if (losBlocked(S.T, bl.x, bl.y, cx, cy, 5)) continue;
      const dm = K.dmg * (1 - d / 30);
      dmgTok.set(o.id, (dmgTok.get(o.id) || 0) + dm);
    }
    for (const o of G.chickens) {
      const b = objBox(o);
      const d = distToBox(b, bl.x, bl.y);
      if (d >= 40) continue; // 保守：人质多留 10px 余量
      const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
      if (losBlocked(S.T, bl.x, bl.y, cx, cy, K.r)) continue;
      chickHit.add(o.id);
    }
    if (G.bug && bl.kind !== 'dig') {
      const cur = bugPos || { x: G.bug.x, y: G.bug.y, radius: 14 };
      if (!(HOLE.bugFail || []).some(f => Math.hypot(f.x - bl.x, f.y - bl.y) < 22) && !losBlocked(S.T, bl.x, bl.y, cur.x, cur.y - 14, 5)) { // 第二轮：09号洞#1/#2 爆点在 Bug 旁 48–56px 但隔着山体，Bug 完全没动→遮挡豁免只留5px
        const r = bugAfter(S.T, cur, bl.x, bl.y, HOLE.scale || 1);
        { const dd = Math.hypot(cur.x - bl.x, cur.y - 14 - bl.y); if (dd < 44) v -= 0.03 * (44 - dd) / 20; } // 近距离推力对瞄准误差敏感，略惩罚
        if (r.moved) { if (r.out) bugOut = true; else bugPos = { x: r.x, y: r.y, radius: 14 }; }
      }
    }
  }
  for (const o of G.tokens) {
    const dm = dmgTok.get(o.id) || 0;
    const hp = o.hp ?? 1;
    v += Math.min(1, dm / hp);
    if (o.type === 'pushToken' && dm > 0 && dm < hp) v += 0; // 被推走，不计
  }
  v -= chickHit.size * 1.2;
  if (info) info.bugPos = bugPos;
  if (G.bug && bugOut) v -= 1.2;
  else if (G.bug && bugPos) {
    const b0 = recycleDist(G.bug.x, G.bug.y), b1 = recycleDist(bugPos.x, bugPos.y);
    v += (b0 - b1) / 150;
    if (b1 === 0) v += 1.0;
  }
  if (G.bug && S.hole.recycle) {
    const rz = recycleSolid();
    if (rz.total > 0 && rz.total / rz.area > 0.15) {
      let cnt = 0;
      for (const bl of blasts) { const K = KINDS[bl.kind]; cnt += solidInRegionAndCircle(bl.x, bl.y, K.r); }
      v += 0.6 * Math.min(1, cnt / rz.total);
    }
  }
  // 遮挡：炸开土层对被盖住物体有用
  const last = blasts[blasts.length - 1];
  if (last) {
    const K = KINDS[last.kind];
    const hidden = [...G.tokens, ...(G.bug ? [G.bug] : [])];
    for (const o of hidden) {
      const b = objBox(o), cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
      S._c0 = S._c0 || new Map();
      let c0 = S._c0.get(o.id);
      if (c0 === undefined) { c0 = coverOf(S.T, o, 0, 0, 0); S._c0.set(o.id, c0); }
      if (c0 < 12) continue;
      // 第二轮：被土盖住且还很远时，离目标越近越好（逐发啃过去；第一轮06号洞每发都落在外壁上、没有向内推进的偏好）
      if (o.type !== 'bug') v += 0.25 * (1 - Math.min(1600, Math.hypot(cx - last.x, cy - last.y)) / 1600);
      if (Math.hypot(cx - last.x, cy - last.y) > K.r + 150) continue;
      const c1 = coverOf(S.T, o, last.x, last.y, K.r);
      v += 0.5 * (c0 - c1) / c0;
      // 连续掘进：上一发刚挖过的位置附近，目标仍被盖住时继续挖更划算（通用，只依赖本洞上一发）
      if (HOLE.lastDig && c1 > 0 && Math.hypot(last.x - HOLE.lastDig.x, last.y - HOLE.lastDig.y) < 30 && c1 < c0) v += 0.08;
    }
    // 兜底：让零价值的炮弹倾向靠近目标
    let md = 1e9;
    for (const o of [...G.tokens, ...(G.bug ? [G.bug] : [])]) { const b = objBox(o); md = Math.min(md, Math.hypot((b.x0 + b.x1) / 2 - last.x, (b.y0 + b.y1) / 2 - last.y)); }
    if (md < 1e9) v -= Math.min(md, 1600) / 40000;
  }
  return v;
}

function candidatesFor(pos, dir, G, deadline, out, grid) {
  const m = muzzleAt(pos.x, pos.foot, pos.ga);
  const objs = G.all;
  for (let ang = 0; ang <= 85; ang += 4) {
    const th = ang + dir * pos.ga;
    const row = [];
    for (let pw = 12; pw <= 100; pw += 4) {
      if (performance.now() > deadline) return false;
      const r = flight(S.T, objs, S.wind, m.x, m.y, th, dir, pw, 0.1);
      row.push({ pw, r });
      if (r.out) continue;
      out.push({ pos, dir, ang, pw, imp: r });
    }
    grid.push({ pos, dir, ang, row });
  }
  return true;
}

// 在固定角度下，对力度二分，让落点x命中 tx；返回候选或 null
function bisectPower(g, G, tx, ty) {
  const { pos, dir, ang, row } = g;
  const m = muzzleAt(pos.x, pos.foot, pos.ga);
  const th = ang + dir * pos.ga;
  const sgn = dir; // 落点 x 随力度单调（同方向）
  let best = null;
  for (let i = 1; i < row.length; i++) {
    const a = row[i - 1], b = row[i];
    if (a.r.out || b.r.out) continue;
    if ((a.r.x - tx) * (b.r.x - tx) > 0) continue;
    // 要求两端都不是被障碍提前拦截的异常：直接二分
    let lo = a.pw, hi = b.pw;
    let fl = a.r.x - tx;
    let res = null;
    for (let k = 0; k < 14; k++) {
      const mid = (lo + hi) / 2;
      const r = flight(S.T, G.all, S.wind, m.x, m.y, th, dir, mid, 0.03);
      res = { pw: mid, r };
      const f = r.x - tx;
      if (r.out) break;
      if ((f > 0) === (fl > 0)) { lo = mid; fl = f; } else hi = mid;
    }
    if (res && !res.r.out) {
      const c = { pos, dir, ang, pw: res.pw, imp: res.r };
      const dd = Math.hypot(res.r.x - tx, res.r.y - ty);
      if (!best || dd < best.dd) { best = c; best.dd = dd; }
    }
  }
  return best;
}

function targetsFor(G) {
  const T = []; // {x,y}
  if (G.bug) {
    const b = G.bug, pts = [];
    for (let x = b.x - 58; x <= b.x + 58; x += 3) {
      let y = Math.max(0, b.y - 60);
      while (y < 900 && !solidAt(S.T, x, y)) y += 2;
      if (y >= 900) continue;
      if (Math.hypot(x - b.x, y - (b.y - 14)) < 16) continue;
      pts.push({ x, y: y - 1, tag: 'bug' });
    }
    // 贴脸（落在Bug身上）
    for (const a of [0, 45, 90, 135, 180, 225, 270, 315]) pts.push({ x: b.x + 15 * Math.cos(a * Math.PI / 180), y: b.y - 14 + 15 * Math.sin(a * Math.PI / 180), tag: 'bug' });
    const scored = pts.map(p => ({ p, v: valueOf([{ x: p.x, y: p.y, kind: 'single' }], G) })).filter(q => q.v > 0.05).sort((a, b2) => b2.v - a.v);
    const pickd = [];
    for (const q of scored) {
      if (pickd.every(p2 => Math.hypot(p2.x - q.p.x, p2.y - q.p.y) > 5)) pickd.push(q.p);
      if (pickd.length >= 6) break;
    }
    T.push(...pickd);
  }
  for (const o of G.tokens) {
    const cv = coverOf(S.T, o, 0, 0, 0, true);
    if (cv.c >= 12 && cv.c < 300 && cv.ray) {
      const e = cv.ray.exit; // 射线出口外侧点：沿射线向外找实心表面
      T.push({ x: e.x, y: e.y, tag: 'dig' });
      const bx2 = objBox(o);
      T.push({ x: (bx2.x0 + bx2.x1) / 2 + (e.x - (bx2.x0 + bx2.x1) / 2) * 0.6, y: (bx2.y0 + bx2.y1) / 2 + (e.y - (bx2.y0 + bx2.y1) / 2) * 0.6, tag: 'dig' });
    }
    const bx = objBox(o);
    T.push({ x: (bx.x0 + bx.x1) / 2, y: (bx.y0 + bx.y1) / 2, tag: 'tok' });
    T.push({ x: (bx.x0 + bx.x1) / 2, y: bx.y0 - 2, tag: 'tok' });
  }
  return T;
}

function refine(c, G) {
  const m = muzzleAt(c.pos.x, c.pos.foot, c.pos.ga);
  let best = null;
  for (let da = -3; da <= 3; da += 1) {
    const ang = c.ang + da;
    if (ang < 0 || ang > 85) continue;
    for (let dp = -4; dp <= 4; dp += 1) {
      const pw = c.pw + dp;
      if (pw < 1 || pw > 100) continue;
      const r = flight(S.T, G.all, S.wind, m.x, m.y, ang + c.dir * c.pos.ga, c.dir, pw, 0.03);
      if (r.out) continue;
      const cand = { pos: c.pos, dir: c.dir, ang, pw, imp: r };
      cand.sv = valueOf([{ x: r.x, y: r.y, kind: 'single' }], G);
      if (!best || cand.sv > best.sv) best = cand;
    }
  }
  return best;
}

function carveCircle(T, cx, cy, rad) {
  for (let cyl = Math.max(0, Math.floor((cy - rad) / 4)); cyl <= Math.min(T.h - 1, Math.floor((cy + rad) / 4)); cyl++)
    for (let cxl = Math.max(0, Math.floor((cx - rad) / 4)); cxl <= Math.min(T.w - 1, Math.floor((cx + rad) / 4)); cxl++)
      if (Math.hypot(cxl * 4 + 2 - cx, cyl * 4 + 2 - cy) <= rad) T.g[cyl * T.w + cxl] = 0;
}

function evalKinds(rc, G, best) {
  for (const kind of ['single', 'burst', 'spread', 'dig']) {
    if (G.bug && !G.tokens.length && kind !== 'single') continue;
    let blasts;
    if (kind === 'single' || kind === 'dig') blasts = [{ x: rc.imp.x, y: rc.imp.y, kind }];
    else if (kind === 'burst') {
      // 三发依次炸：每发先把上一发的坑挖掉再飞（同一角度力度）
      const m = muzzleAt(rc.pos.x, rc.pos.foot, rc.pos.ga);
      const T2 = { w: S.T.w, h: S.T.h, g: S.T.g.slice() };
      blasts = [];
      for (let k = 0; k < 3; k++) {
        const r = flight(T2, G.all, S.wind, m.x, m.y, rc.ang + rc.dir * rc.pos.ga, rc.dir, rc.pw, 0.03);
        if (r.out) break;
        blasts.push({ x: r.x, y: r.y, kind });
        carveCircle(T2, r.x, r.y, KINDS.burst.r);
      }
    }
    else {
      const m = muzzleAt(rc.pos.x, rc.pos.foot, rc.pos.ga);
      blasts = [];
      for (const off of [-6, 0, 6]) {
        const r = flight(S.T, G.all, S.wind, m.x, m.y, rc.ang + rc.dir * rc.pos.ga + off, rc.dir, rc.pw, 0.03);
        if (!r.out) blasts.push({ x: r.x, y: r.y, kind });
      }
    }
    if (G.bug && kind !== 'single' && blasts.some(bl => Math.hypot(bl.x - G.bug.x, bl.y - (G.bug.y - 14)) < 75)) continue;
    const info = {};
    let v = valueOf(blasts, G, info);
    if (kind === 'dig') v -= 0.02; if (kind === 'burst') v -= 0.01; if (kind === 'spread') v -= 0.01;
    if (!best.b || v > best.b.v) best.b = { v, kind, c: rc, blasts, bugPos: info.bugPos };
  }
}

function planShot() {
  const t0 = performance.now();
  const deadline = t0 + 160;
  const G = goalObjs();
  const positions = standPositions();
  positions.sort((a, b) => (b.keep ? 1 : 0) - (a.keep ? 1 : 0));
  const cands = [], grid = [];
  for (const pos of positions) {
    for (const dir of [1, -1]) {
      if (!candidatesFor(pos, dir, G, deadline, cands, grid)) break;
    }
    if (performance.now() > deadline) break;
  }
  for (const c of cands) c.sv = valueOf([{ x: c.imp.x, y: c.imp.y, kind: 'single' }], G);
  cands.sort((a, b) => b.sv - a.sv);
  const top = [];
  const seen = new Set();
  for (const c of cands) {
    const key = `${c.pos.x}|${c.dir}|${Math.round(c.imp.x / 20)}|${Math.round(c.imp.y / 20)}`;
    if (seen.has(key)) continue; seen.add(key); top.push(c);
    if (top.length >= 8) break;
  }
  const best = { b: null };
  for (const c of top) { if (performance.now() - t0 > 290) break; evalKinds(refine(c, G) || c, G, best); }
  // 目标点精确瞄准（二分力度）
  const t1 = t0 + 330;
  const targets = targetsFor(G);
  let nt = 0;
  outer: for (const tg of targets) {
    for (const g of grid) {
      if (performance.now() > t1) break outer;
      // 方向要和目标一致才有意义
      if ((tg.x - g.pos.x) * g.dir < 0) continue;
      const c = bisectPower(g, G, tg.x, tg.y);
      if (!c || c.dd > 6) continue;
      nt++;
      evalKinds(c, G, best);
    }
  }
  const b = best.b;
  log('plan', { n: cands.length, nt, ms: Math.round(performance.now() - t0), best: b && { v: +b.v.toFixed(3), kind: b.kind, x: b.c.pos.x, ang: b.c.ang, pw: +b.c.pw.toFixed(2), dir: b.c.dir, imp: [b.c.imp.x | 0, b.c.imp.y | 0] } });
  return b;
}

// ---- 瞄准指定落点（实验脚本用）----
function aimAt(tx, ty, pos, dir) {
  const m = muzzleAt(pos.x, pos.foot, pos.ga);
  const G = goalObjs();
  let best = null;
  for (let ang = 5; ang <= 85; ang += 1) for (let pw = 5; pw <= 100; pw += 1) {
    const r = flight(S.T, G.all, S.wind, m.x, m.y, ang + dir * pos.ga, dir, pw, 0.05);
    if (r.out) continue;
    const d = Math.hypot(r.x - tx, r.y - ty);
    if (!best || d < best.d) best = { d, ang, pw, r };
  }
  // 精调
  if (best) {
    const b0 = best;
    for (let a = b0.ang - 1; a <= b0.ang + 1; a += 0.25) for (let p = b0.pw - 1; p <= b0.pw + 1; p += 0.25) {
      if (a < 0 || a > 85 || p < 0 || p > 100) continue;
      const r = flight(S.T, G.all, S.wind, m.x, m.y, a + dir * pos.ga, dir, p, 0.02);
      if (r.out) continue;
      const d = Math.hypot(r.x - tx, r.y - ty);
      if (d < best.d) best = { d, ang: a, pw: p, r };
    }
  }
  return best;
}

function scriptTurn() {
  const sc = readMem('script.json', null);
  if (!sc) return null;
  const idx = S.shotsTaken + S.penalties; // 简化：用 strokes 作索引
  const step = sc.shots[S.strokes];
  if (!step) return null;
  const pos = standPositions().find(p => p.keep) || standPositions()[0];
  const bug = S.objects.find(o => o.type === 'bug' && objActive(o));
  let tx = step.x, ty = step.y;
  if (step.bugOff != null && bug) { tx = bug.x + step.bugOff; ty = step.y ?? bug.y; }
  const obj = step.obj != null ? S.objects.find(o => o.id === step.obj) : null;
  if (obj) { tx = obj.x + (step.dx || 0); ty = step.y ?? obj.y - 10; }
  let moveX;
  if (step.moveX != null && S.hole.teeActive) { moveX = step.moveX; const sy = surfaceY(S.T, moveX, S.actor.y + P.foot, 20, 20); pos.x = moveX; pos.foot = sy ?? pos.foot; pos.ga = groundAngleAt(S.T, moveX, pos.foot); }
  const dir = step.dir ?? (tx > pos.x ? 1 : -1);
  const a = aimAt(tx, ty, pos, dir);
  log('script', { step, tx, ty, aim: a && { ang: a.ang, pw: a.pw, d: a.d, r: [a.r.x, a.r.y] } });
  const out = { attack: { kind: step.kind || 'single', angle: +a.ang.toFixed(2), power: +a.pw.toFixed(2), dir } };
  if (moveX != null) out.moveX = moveX;
  return out;
}

function standableAt(x, nearY) {
  const sy = surfaceY(S.T, x, nearY, 900, 900);
  if (sy == null) return null;
  for (let h = 8; h <= 56; h += 8) if (solidAt(S.T, x, sy - h)) return null;
  return sy;
}
function coarseValue(pos, G) {
  const m = muzzleAt(pos.x, pos.foot, pos.ga);
  let best = -1e9;
  for (const dir of [1, -1]) for (let ang = 6; ang <= 84; ang += 6) for (let pw = 14; pw <= 100; pw += 6) {
    const r = flight(S.T, G.all, S.wind, m.x, m.y, ang + dir * pos.ga, dir, pw, 0.15);
    if (r.out) continue;
    const v = valueOf([{ x: r.x, y: r.y, kind: 'single' }], G);
    if (v > best) best = v;
  }
  return best;
}
function planFly(G) {
  const t0 = performance.now();
  const list = [];
  const ys = S.actor.y + P.foot;
  for (let x = 60; x <= 1540; x += 40) {
    if (performance.now() - t0 > 120) break;
    const fy = standableAt(x, ys);
    if (fy == null) continue;
    if (Math.abs(x - S.actor.x) < 80) continue;
    const pos = { x, foot: fy, ga: GA_SIGN * groundAngleAt(S.T, x, fy) };
    if (Math.abs(pos.ga) > 12) continue;
    const v = coarseValue(pos, G);
    if (v >= 0.2) list.push({ v, pos });
  }
  list.sort((p, q) => q.v - p.v);
  const mz = muzzleAt(S.actor.x, S.actor.y + P.foot, S.actor.groundAngle);
  for (const bp of list.slice(0, 40)) {
    if (performance.now() - t0 > 200) break;
    const dir = bp.pos.x > S.actor.x ? 1 : -1;
    let best = null;
    for (let ang = 20; ang <= 70; ang += 2) {
      let lo = 5, hi = 100;
      const f = p => flight(S.T, [], S.wind, mz.x, mz.y, ang, dir, p, 0.05);
      const flo = f(lo), fhi = f(hi);
      if (flo.out || fhi.out) continue;
      const s0 = Math.sign(flo.x - bp.pos.x);
      if (s0 === Math.sign(fhi.x - bp.pos.x)) continue;
      for (let k = 0; k < 14; k++) { const mid = (lo + hi) / 2; const r = f(mid); if (r.out) break; if (Math.sign(r.x - bp.pos.x) === s0) lo = mid; else hi = mid; }
      const r = f((lo + hi) / 2);
      if (r.out) continue;
      const err = Math.abs(r.x - bp.pos.x);
      const score = err + Math.abs(ang - 40) * 0.3;
      if (!best || score < best.score) best = { score, ang, pw: (lo + hi) / 2, err, r };
    }
    if (best && best.err <= 12) {
      log('fly', { target: bp, best: { ang: best.ang, pw: best.pw, imp: [best.r.x, best.r.y] } });
      return { fly: { angle: +best.ang.toFixed(2), power: +best.pw.toFixed(2), dir } };
    }
  }
  return null;
}

function decide() {
  TURN_T0 = performance.now();
  const sc = scriptTurn();
  if (sc) return sc;
  const best = planShot();
  if ((!best || best.v < 0.05) && S.fly > 0 && performance.now() - TURN_T0 < 500) { const f = planFly(goalObjs()); if (f) return f; }
  if (best && best.v < 0.05) {
    const G0 = goalObjs();
    if (G0.bug && HOLE.bug0 && HOLE.unplay < 2 && Math.hypot(G0.bug.x - HOLE.bug0.x, G0.bug.y - HOLE.bug0.y) > 80 && recycleDist(HOLE.bug0.x, HOLE.bug0.y) + 50 < recycleDist(G0.bug.x, G0.bug.y) && !G0.tokens.length) {
      HOLE.unplay++; log('unplayable: bug stuck, reset');
      return { unplayable: true };
    }
  }
  if (!best) return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } };
  const c = best.c;
  { const lb = best.blasts && best.blasts[best.blasts.length - 1]; HOLE.lastDig = lb ? { x: lb.x, y: lb.y } : null; }
  HOLE.pred = { bug: best.bugPos, bugFrom: S.objects.find(o => o.type === 'bug') && { x: S.objects.find(o => o.type === 'bug').x, y: S.objects.find(o => o.type === 'bug').y } };
  const out = { attack: { kind: best.kind, angle: +c.ang.toFixed(2), power: +c.pw.toFixed(2), dir: c.dir } };
  if (S.hole.teeActive && !c.pos.keep) out.moveX = c.pos.x;
  return out;
}

function learn(r) {
  try {
    const p = HOLE.pred; if (!p || !p.bug || !p.bugFrom) return;
    const ch = r.lastResult.objectChanges.find(c => c.before && c.before.type === 'bug');
    if (!ch) return;
    const pd = p.bug.x - p.bugFrom.x, ad = ch.after.x - ch.before.x;
    { // 第二轮：预测明显推动、实际没动（09号洞#1/#2）→ 记下这个爆点，本洞内不再信它的推力预测
      const ay = ch.after.y - ch.before.y, im = r.lastResult.impacts && r.lastResult.impacts[0];
      if (Math.abs(pd) >= 40 && Math.abs(ad) < 5 && Math.abs(ay) < 5 && im) { (HOLE.bugFail = HOLE.bugFail || []).push({ x: im.x, y: im.y }); log('bugFail', im.x, im.y); }
    }
    if (Math.abs(pd) < 40 || Math.abs(ad) < 5 || Math.sign(pd) !== Math.sign(ad)) return;
    const ratio = Math.min(2, Math.max(0.5, ad / pd));
    HOLE.scale = Math.min(2, Math.max(0.5, (HOLE.scale || 1) * Math.sqrt(ratio)));
    log('scale', HOLE.scale, { pd, ad });
  } catch (e) { log('learn err', String(e)); }
}

function respond(r) {
  switch (r.type) {
    case 'init':
      setState(r);
      { const b0 = r.objects.find(o => o.type === 'bug'); HOLE.bug0 = b0 ? { x: b0.x, y: b0.y } : null; HOLE.unplay = 0; HOLE.bugFail = []; HOLE.lastDig = null; }
      log('init', r.hole, r.actor, r.objects.map(o => [o.id, o.type, o.x, o.y, o.hp]));
      return { ok: true };
    case 'turn': {
      setState(r);
      if (r.lastResult) { log('last', r.lastResult.impacts, r.lastResult.objectChanges); learn(r); }
      return decide();
    }
    case 'exam': {
      const preds = [];
      const T = { w: 400, h: 225, g: new Uint8Array(400 * 225) };
      T.g.fill(1, 195 * 400);
      for (const q of r.questions) {
        const m = { x: q.x, y: (r.ground ?? 780) - P.muzzleH };
        const f = flight(T, [], q.wind, m.x, m.y, q.angle, q.dir, q.power, 0.01);
        preds.push({ id: q.id, x: +f.x.toFixed(2), y: +f.y.toFixed(2) });
      }
      return { predictions: preds };
    }
    default:
      throw new Error('unknown ' + r.type);
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  const r = JSON.parse(line);
  let reply;
  try { reply = respond(r); } catch (e) {
    log('ERR', String(e && e.stack || e));
    reply = r.type === 'turn' ? { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } } : r.type === 'exam' ? { predictions: [] } : { ok: true };
  }
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
