// 弹弹高尔夫 Bot：物理模型 + 逐杆贪心搜索。
import { createInterface } from 'node:readline';
import fs from 'node:fs';

// ---------- 物理常数（试射拟合）----------
const G = 1200, VK = 12, WK = 45, DT = 1 / 60;
let WY = 394, WM = 1.36;
const GW = 400, GH = 225, W = 1600, H = 900;
let FOOT = 12, MUZ = 35;
let ATT = {
  single: { damage: 120, radius: 32, offsets: [0], n: 1 },
  burst: { damage: 65, radius: 26, offsets: [0], n: 3 },
  spread: { damage: 55, radius: 24, offsets: [-6, 0, 6], n: 1 },
  dig: { damage: 30, radius: 60, offsets: [0], n: 1 },
};
// Bug / 滑板 token 推动模型（由练习与实验标定）
const PUSH = { R: 76, V: 1000, F: 600, land: 0.8, down: 0.3 };
let grid = new Uint8Array(GW * GH);
const log = s => process.stderr.write(s + '\n');

function decode(t) { const g = new Uint8Array(GW * GH); let i = 0; for (const [v, n] of t.rle) { if (v) g.fill(1, i, i + n); i += n; } return g; }
function solid(x, y) { if (x < 0 || x >= W || y < 0 || y >= H) return false; return grid[((y | 0) >> 2) * GW + ((x | 0) >> 2)] === 1; }

// ---------- 物体 ----------
function prep(o) {
  const p = { id: o.id, type: o.type, x: o.x, y: o.y, hp: o.hp ?? 1, maxHp: o.maxHp ?? o.hp ?? 1, inRecycle: !!o.inRecycle, stunned: !!o.stunned };
  if (o.type === 'bug') { p.r = o.radius ?? 14; p.cx = o.x; p.cy = o.y - p.r; }
  else { const w = o.width ?? 28, h = o.height ?? 28; p.x0 = o.x - w / 2; p.x1 = o.x + w / 2; p.y0 = o.y - h; p.y1 = o.y; p.cx = o.x; p.cy = o.y - h / 2; }
  return p;
}
function inside(o, x, y) {
  if (o.type === 'bug') { const dx = x - o.cx, dy = y - o.cy; return dx * dx + dy * dy <= o.r * o.r; }
  return x >= o.x0 && x <= o.x1 && y >= o.y0 && y <= o.y1;
}
function objDist(o, x, y) {
  if (o.type === 'bug') return Math.max(0, Math.hypot(x - o.cx, y - o.cy) - o.r);
  const dx = Math.max(o.x0 - x, 0, x - o.x1), dy = Math.max(o.y0 - y, 0, y - o.y1); return Math.hypot(dx, dy);
}

// ---------- 弹道 ----------
function fly(mx, my, vx, vy, wind, objs) {
  let x = mx, y = my, top = false;
  for (let i = 0; i < 1500; i++) {
    if (y < -4) top = true;
    const ax = WK * wind * (y < WY ? WM : 1);
    const dx = vx * DT + 0.5 * ax * DT * DT, dy = vy * DT + 0.5 * G * DT * DT;
    const n = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy)) / 2));
    for (let k = 1; k <= n; k++) {
      const px = x + dx * k / n, py = y + dy * k / n;
      if (px < 0 || px >= W || py >= H) return { x: px, y: py, hit: 'out', obj: null, vx, vy };
      if (py >= 0 && grid[((py | 0) >> 2) * GW + ((px | 0) >> 2)]) return { x: px, y: py, hit: 'ground', obj: null, vx, vy, top };
      for (let j = 0; j < objs.length; j++) if (inside(objs[j], px, py)) return { x: px, y: py, hit: 'object', obj: objs[j], vx, vy, top };
    }
    x += dx; y += dy; vx += ax * DT; vy += G * DT;
    if (y > H + 50) break;
  }
  return { x, y, hit: 'out', obj: null, vx, vy };
}
// 不考虑碰撞，轨迹经过 tx 时的 y
function yAt(mx, my, vx, vy, wind, tx, dir) {
  let x = mx, y = my;
  for (let i = 0; i < 1500; i++) {
    const ax = WK * wind * (y < WY ? WM : 1);
    const nx = x + vx * DT + 0.5 * ax * DT * DT, ny = y + vy * DT + 0.5 * G * DT * DT;
    if ((nx - tx) * dir >= 0) { const f = (tx - x) / (nx - x || 1e-9); return y + (ny - y) * f; }
    x = nx; y = ny; vx += ax * DT; vy += G * DT;
    if (y > 1400 || (vx * dir <= 0 && ax * dir <= 0)) return 1e9;
  }
  return 1e9;
}
function solvePower(mx, my, angDeg, dir, wind, tx, ty) {
  const th = angDeg * Math.PI / 180, c = dir * Math.cos(th) * VK, s = -Math.sin(th) * VK;
  if (c * dir <= 1e-6) return null;
  const f = p => yAt(mx, my, c * p, s * p, wind, tx, dir) - ty;
  if (f(100) > 0) return null;
  let lo = 1, hi = 100;
  if (f(lo) <= 0) return null;
  for (let i = 0; i < 13; i++) { const m = (lo + hi) / 2; if (f(m) > 0) lo = m; else hi = m; }
  return (lo + hi) / 2;
}
function muzzle(st) { const a = st.ga * Math.PI / 180; return { x: st.x - Math.sin(a) * MUZ, y: st.fy - Math.cos(a) * MUZ }; }

// ---------- 站位 ----------
function standAt(x, refFy, minFeet) {
  const cx = Math.floor(x / 4); if (cx < 9 || cx >= GW - 9) return null;
  let best = null;
  for (let cy = 14; cy < GH; cy++) {
    if (!grid[cy * GW + cx] || grid[(cy - 1) * GW + cx]) continue;
    const fy = cy * 4; if (minFeet != null && fy < minFeet - 2) continue;
    let ok = true; for (let k = 1; k <= 13 && ok; k++) if (grid[(cy - k) * GW + cx]) ok = false;
    if (!ok) continue;
    let flat = true; for (let c = cx - 9; c <= cx + 9 && flat; c++) if (!grid[cy * GW + c] || grid[(cy - 1) * GW + c]) flat = false;
    if (!flat) continue;
    if (!best || Math.abs(fy - refFy) < Math.abs(best.fy - refFy)) best = { x, fy, ga: 0, cur: false };
  }
  return best;
}
// 从当前站位沿地表走到 x（每列台阶 ≤16px），返回到达处脚底 y；走不通返回 null
function walkTo(cur, x) {
  let y = Math.round(cur.fy / 4) * 4; const c0 = Math.floor(cur.x / 4), c1 = Math.floor(x / 4), step = c1 >= c0 ? 1 : -1;
  for (let c = c0; c !== c1 + step; c += step) {
    if (c < 0 || c >= GW) return null; let found = null;
    for (let dy = 0; dy <= 16 && found == null; dy += 4) for (const yy of [y - dy, y + dy]) { const r = yy >> 2; if (r > 0 && r < GH && grid[r * GW + c] && !grid[(r - 1) * GW + c]) { found = r * 4; break; } }
    if (found == null) return null; y = found;
  }
  return y;
}
function surfaceNear(x, y) { // 离 y 最近的地表 y
  const cx = Math.floor(x / 4); if (cx < 0 || cx >= GW) return y;
  let best = y, bd = 1e9;
  for (let cy = 1; cy < GH; cy++) if (grid[cy * GW + cx] && !grid[(cy - 1) * GW + cx]) { const d = Math.abs(cy * 4 - y); if (d < bd) { bd = d; best = cy * 4; } }
  return best;
}

// ---------- 推动模型 ----------
function circHit(x, y, r) {
  let nx = 0, ny = 0, n = 0;
  for (let i = 0; i < 16; i++) { const a = i * Math.PI / 8, c = Math.cos(a), sn = Math.sin(a); if (solid(x + r * c, y + r * sn)) { nx -= c; ny -= sn; n++; } }
  if (!n) return null; const l = Math.hypot(nx, ny) || 1; return { nx: nx / l, ny: ny / l, n };
}
// 推动模型：Bug 当作半径 r 的圆。空中弹道飞行；首次碰到“地板”转入滑行，碰到墙则水平速度归零，无法再下落就停住。
function simPush(o, ex, ey, kind, K) {
  const r = o.r ?? 14; let x = o.cx, y = (o.type === 'bug') ? o.cy : o.y - r;
  const d = Math.hypot(x - ex, y - ey); if (d >= K.R) return null;
  const sp = K.V * (1 - d / K.R);
  let vx = d > 1 ? sp * (x - ex) / d : 0, vy = d > 1 ? sp * (y - ey) / d : -sp;
  let air = true; const rc = r - 0.6;
  const supported = solid(x, y + r + 1) || solid(x - 8, y + r) || solid(x + 8, y + r);
  if (vy >= 0 && supported) { air = false; vx *= (vy > 0.25 * sp ? K.down ?? 0.3 : 1); vy = 0; }
  for (let i = 0; i < 1200; i++) {
    if (air) {
      vy += G * DT; const nx = x + vx * DT, ny = y + vy * DT;
      if (nx < -r || nx > W + r || ny > H) return { x: nx, y: ny + r, out: true };
      const c = circHit(nx, ny, rc);
      if (!c) { x = nx; y = ny; continue; }
      if (c.ny < -0.55 && vy >= 0) { // 落地
        x = nx; y = ny; let k = 0; while (k < 24 && solid(x, y + r - 0.5)) { y -= 1; k++; }
        vy = 0; air = false; vx *= K.land ?? 1; continue;
      }
      if (vy < 0 && c.ny > 0.55) { vy = 0; if (!circHit(nx, y, rc)) x = nx; else vx = 0; continue; }
      // 撞墙：水平速度归零，尝试只做竖直运动
      vx = 0;
      if (!circHit(x, ny, rc)) { y = ny; continue; }
      if (vy < 0) { vy = 0; continue; }
      return { x, y: y + r, out: false };
    } else {
      const sg = Math.sign(vx); const dec = K.F * DT;
      if (Math.abs(vx) <= dec) return { x, y: y + r, out: false };
      vx -= sg * dec; const nx = x + vx * DT;
      if (nx < -r || nx > W + r) return { x: nx, y: y + r, out: true };
      if (solid(nx + sg * r, y - 2) || solid(nx + sg * r * 0.8, y - r * 0.6)) return { x, y: y + r, out: false };
      if (solid(nx, y + r - 1)) { let k = 1; while (k <= 8 && solid(nx, y + r - 1 - k)) k++; if (k > 8) return { x, y: y + r, out: false }; const v2 = vx * vx - 2 * G * k; if (v2 <= 0) return { x, y: y + r, out: false }; vx = sg * Math.sqrt(v2); y -= k; }
      else if (!solid(nx, y + r + 1)) { let k = 1; while (k <= 6 && !solid(nx, y + r + 1 + k)) k++; if (k > 6) { air = true; vy = 0; } else { y += k; vx = sg * Math.sqrt(vx * vx + 2 * G * k); } }
      x = nx;
    }
  }
  return { x, y: y + r, out: false };
}
function restCols(z) {
  let n = 0;
  for (let c = Math.ceil((z.x0 + 6) / 4); c <= Math.floor((z.x1 - 6) / 4) && c < GW; c++) {
    for (let r = Math.max(8, Math.floor(z.y0 / 4)); r < GH && r * 4 - 14 <= z.y1; r++) {
      if (!grid[r * GW + c] || grid[(r - 1) * GW + c]) continue;
      if (r * 4 - 14 < z.y0 + 6) continue;
      let ok = true; for (let k = 1; k <= 7; k++) if (grid[(r - k) * GW + c]) { ok = false; break; }
      if (ok) { n++; break; }
    }
  }
  return n;
}
function coverCells(z) { // 回收区上方及上部 40px 内的实心格数
  let n = 0; const r1 = Math.min(GH, Math.floor((z.y0 + 40) / 4)), r0 = Math.max(0, Math.floor((z.y0 - 240) / 4));
  for (let c = Math.max(0, Math.ceil(z.x0 / 4)); c <= Math.floor(z.x1 / 4) && c < GW; c++) for (let r = r0; r < r1; r++) n += grid[r * GW + c];
  return n;
}
function zoneDist(z, x, y) { const dx = Math.max(z.x0 - x, 0, x - z.x1), dy = Math.max(z.y0 - y, 0, y - z.y1); return Math.hypot(dx, dy); }

// ---------- 评估一杆 ----------
function carve(ex, ey, R, undo) {
  const c0 = Math.max(0, Math.floor((ex - R) / 4)), c1 = Math.min(GW - 1, Math.floor((ex + R) / 4));
  const r0 = Math.max(0, Math.floor((ey - R) / 4)), r1 = Math.min(GH - 1, Math.floor((ey + R) / 4));
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
    const i = r * GW + c; if (!grid[i]) continue;
    const dx = c * 4 + 2 - ex, dy = r * 4 + 2 - ey; if (dx * dx + dy * dy <= R * R) { grid[i] = 0; undo.push(i); }
  }
}
function los(ex, ey, vx, vy, o) { // 爆点退后一点到物体最近点之间无地形
  const sp = Math.hypot(vx, vy) || 1; const sx = ex - vx / sp * 5, sy = ey - vy / sp * 5;
  const tx = Math.min(Math.max(sx, o.x0 + 2), o.x1 - 2), ty = Math.min(Math.max(sy, o.y0 + 2), o.y1 - 2);
  const n = Math.ceil(Math.hypot(tx - sx, ty - sy) / 3) + 1;
  for (let k = 0; k <= n; k++) if (solid(sx + (tx - sx) * k / n, sy + (ty - sy) * k / n)) return false;
  return true;
}
// 爆点到 Bug 圆心之间是否无地形遮挡（练习 1 第 6、8 杆：隔着地形时 Bug 基本不动）
function pushLos(ex, ey, vx, vy, o) {
  const sp = Math.hypot(vx, vy) || 1; const sx = ex - vx / sp * 5, sy = ey - vy / sp * 5;
  const L = Math.hypot(o.cx - sx, o.cy - sy) - (o.r ?? 14) + 2; if (L <= 3) return true;
  const ux = (o.cx - sx) / (L + (o.r ?? 14) - 2), uy = (o.cy - sy) / (L + (o.r ?? 14) - 2); let blocked = 0;
  for (let t = 0; t <= L; t += 3) if (solid(sx + ux * t, sy + uy * t)) blocked++;
  return blocked < 2;
}
function evalShot(st, ang, power, dir, kind, S, K = PUSH) {
  const A = ATT[kind]; const m = muzzle(st); const act = ang + dir * st.ga;
  let alive = S.objs.slice(); const dmg = new Map(); const chick = new Set(); let bugRes = null, bugHit = false, bugD = 99, bugUp = false, ptPushed = false, carvedAll = false; const shifted = new Map(); const impacts = []; const undo = [];
  let prog = 0;
  const shells = [];
  if (kind === 'burst') shells.push(0, 0, 0); else for (const o of A.offsets) shells.push(o);
  for (const off of shells) {
    const th = (act + off) * Math.PI / 180;
    const r = fly(m.x, m.y, dir * Math.cos(th) * VK * power, -Math.sin(th) * VK * power, S.wind, alive);
    impacts.push(r); if (r.hit === 'out') continue;
    const dead = [];
    for (const o of alive) {
      if (o.type === 'bug') {
        if (!bugHit && (r.obj === o || pushLos(r.x, r.y, r.vx, r.vy, o))) { const p = simPush(o, r.x, r.y, kind, K); if (p) { bugRes = p; bugHit = true; bugD = Math.hypot(o.cx - r.x, o.cy - r.y); bugUp = r.y < o.cy - 3; } }
        continue;
      }
      const d = r.obj === o ? 0 : objDist(o, r.x, r.y);
      if (o.type === 'rock') continue;
      if (o.type === 'chicken') { if (d < 37) chick.add(o.id); continue; }
      if (o.type === 'pushToken' && !ptPushed && d < 30) { ptPushed = true; const p = simPush({ type: 'bug', r: 15, cx: o.cx, cy: o.cy, y: o.y }, r.x, r.y, kind, K); if (p && p.out) { dmg.set(o.id, o.hp); dead.push(o); continue; } }
      if (d < 30 && (d === 0 || los(r.x, r.y, r.vx, r.vy, o))) {
        const t = (dmg.get(o.id) ?? 0) + A.damage * (1 - d / 30); dmg.set(o.id, t);
        if (t >= o.hp) dead.push(o);
        else if (kind === 'burst' && o.type === 'pushToken') { // 滑板 token 挨炸后会被推走：按练习记录估计后两发时的位置
          if (!shifted.has(o)) shifted.set(o, { x0: o.x0, x1: o.x1, cx: o.cx, n: 0, sg: Math.abs(r.x - o.cx) > 4 ? Math.sign(o.cx - r.x) : (Math.sign(r.vx) || dir) });
          const h = shifted.get(o); h.n++; const dx = h.sg * (h.n === 1 ? 23 : 90); o.x0 = h.x0 + dx; o.x1 = h.x1 + dx; o.cx = h.cx + dx;
        }
      } else if (r.hit === 'ground') {
        const dc = Math.hypot(o.cx - r.x, o.cy - r.y); if (dc < A.radius + 40) prog = Math.max(prog, (kind === 'dig' ? 30 : 10) * (1 - dc / (A.radius + 40)));
      }
    }
    if (dead.length) alive = alive.filter(o => !dead.includes(o));
    if (kind === 'burst') carve(r.x, r.y, A.radius, undo);
  }
  if (kind === 'burst') carvedAll = true;
  for (const [o, h] of shifted) { o.x0 = h.x0; o.x1 = h.x1; o.cx = h.cx; }
  const ensureCarve = () => { if (carvedAll) return; carvedAll = true; if (kind !== 'burst') for (const r of impacts) if (r.hit !== 'out') carve(r.x, r.y, A.radius, undo); };
  // 支撑被炸空：物体下落，若正下方再无地形则掉出地图
  for (const o of alive) {
    if (o.type === 'bug' || o.type === 'rock') continue;
    let near = false; for (const r of impacts) if (r.hit !== 'out' && Math.abs(r.x - o.cx) < A.radius + (o.x1 - o.x0) / 2 + 2 && Math.abs(r.y - o.y) < A.radius + 6) near = true;
    if (!near) continue; ensureCarve();
    let sup = false; for (let x = o.x0 + 1; x <= o.x1 - 1 && !sup; x += 3) if (solid(x, o.y + 1) || solid(x, o.y + 5)) sup = true;
    if (sup) continue;
    let lands = false; for (let y = o.y + 6; y < H && !lands; y += 4) for (let x = o.x0 + 1; x <= o.x1 - 1; x += 3) if (solid(x, y)) { lands = true; break; }
    if (lands) continue;
    if (o.type === 'chicken') chick.add(o.id); else dmg.set(o.id, o.hp);
  }
  let digGain = 0;
  if (S.digOK && impacts.some(r => r.hit === 'ground' && zoneDist(S.zone, r.x, r.y) < 90)) {
    ensureCarve();
    const rc = restCols(S.zone), cc = coverCells(S.zone);
    digGain = (S.covered ? 1 : 0.45) * (50 * Math.min(1, Math.max(0, rc - S.rest0) / 14) + 30 * Math.min(1, Math.max(0, S.cover0 - cc) / 300));
  }
  for (const i of undo) grid[i] = 1;
  let score = digGain, gain = digGain;
  if (impacts.some(r => r.top)) score -= 15; /* 飞出地图上沿后的行为未知，尽量回避 */ const needTok = S.goal !== 'bug', needBug = S.goal !== 'tokens';
  score -= 130 * chick.size;
  if (S.doneBug && impacts.some(r => r.hit !== 'out' && Math.hypot(r.x - S.doneBug[0], r.y - S.doneBug[1]) < 80)) score -= 12; // 已回收的 Bug 是否还会被推走未知：能避则避
  let anyDmg = false;
  for (const [id, t] of dmg) {
    const o = S.byId.get(id); if (!needTok) continue; anyDmg = true;
    const g = t >= o.hp ? 100 : 70 * t / o.hp; score += g; gain += g;
  }
  if (needTok && !anyDmg) score += prog;
  if (bugRes) {
    const b = S.bug;
    if (bugRes.out) score -= 200; // 出界 = 罚 1 杆 + 白打 1 杆
    else if (needBug && S.zone) {
      const d0 = zoneDist(S.zone, b.cx, b.cy), d1 = zoneDist(S.zone, bugRes.x, bugRes.y - 14);
      const inX = bugRes.x > S.zone.x0 + 10 && bugRes.x < S.zone.x1 - 10 && bugRes.y - 14 > S.zone.y0 + 6 && bugRes.y - 14 < S.zone.y1 - 3;
      let v;
      if (d1 === 0 && inX) v = b.inRecycle ? 0 : 120 - 15 * Math.abs(bugRes.x - (S.zone.x0 + S.zone.x1) / 2) / Math.max(20, (S.zone.x1 - S.zone.x0) / 2);
      else if (b.inRecycle) v = -120;
      else v = Math.max(-60, Math.min(60, 60 * (d0 - d1) / Math.max(d0, 60)));
      if (kind === 'burst' || kind === 'spread') v -= 15;
      if (bugD < 24) v -= 15;
      if (bugUp) v -= 20;
      score += v; if (v > 0) gain += v;
    }
  }
  return { score, gain, impacts, bugRes, dmg, chick };
}

// ---------- 瞄准点 ----------
function aimPoints(S) {
  const pts = [];
  const needTok = S.goal !== 'bug', needBug = S.goal !== 'tokens';
  if (needTok) for (const o of S.objs) {
    if (o.type === 'bug' || o.type === 'chicken' || o.type === 'rock') continue;
    pts.push([o.cx, o.cy], [o.cx, o.y0 + 3], [o.x0 + 3, o.cy], [o.x1 - 3, o.cy], [o.cx, o.y0 - 12]);
    if (o.hp > 195) pts.push([o.x0 - 12, o.y + 2], [o.x1 + 12, o.y + 2], [o.x0 - 34, o.y + 2], [o.x1 + 34, o.y + 2]);
  }
  if (needTok) { const tk = S.objs.filter(o => o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken'); for (let i = 0; i < tk.length; i++) for (let j = i + 1; j < tk.length; j++) { const a = tk[i], b = tk[j]; if (Math.abs(a.cx - b.cx) < 90 && Math.abs(a.cy - b.cy) < 90 && pts.length < 120) pts.push([(a.cx + b.cx) / 2, Math.min(a.y0, b.y0) + 4], [(a.cx + b.cx) / 2, (a.cy + b.cy) / 2]); } }
  if (needBug && S.bug && S.zone && !S.bug.inRecycle) {
    const b = S.bug; const zc = (S.zone.x0 + S.zone.x1) / 2; const sg = zc >= b.cx ? 1 : -1;
    for (let s = 6; s <= 78; s += 4) { const x = b.cx - sg * s; pts.push([x, surfaceNear(x, b.y)]); }
    if (S.digOK) for (let x = S.zone.x0 + 16; x <= S.zone.x1 - 16; x += 24) { const sy = surfaceNear(x, S.zone.y0 - 20); pts.push([x, sy + 1]); }
    for (const ph of [-20, 10, 40, 70]) { const a = ph * Math.PI / 180; pts.push([b.cx - sg * 13 * Math.cos(a), b.cy - 13 * Math.sin(a)]); }
  }
  return pts;
}
let MEM = { moveErr: [] };
try { MEM = { moveErr: [], ...JSON.parse(fs.readFileSync('bot/memory/stats.json', 'utf8')) }; } catch { }
function saveMem() { if (process.env.GOLF_LIB || process.env.NODEV) return; /* 本地测试不写记忆 */ try { fs.mkdirSync('bot/memory', { recursive: true }); fs.writeFileSync('bot/memory/stats.json', JSON.stringify(MEM)); } catch { } }
function moveDistrust() { const e = (Array.isArray(MEM.moveErr) ? MEM.moveErr : []).filter(v => typeof v === 'number').slice(-5).sort((a, b) => a - b); return e.length >= 2 && e[(e.length - 1) >> 1] > 12; } // 最近 5 次的（下）中位数 >12px 才不再换站位，单次离群不触发
function stands(obs) {
  const a = obs.actor, tee = obs.hole.tee; const cur = { x: a.x, fy: a.y + FOOT, ga: a.groundAngle || 0, cur: true };
  const out = [cur];
  if (moveDistrust()) return out;
  if (obs.hole.teeActive && tee) {
    const N = 6;
    for (let i = 0; i <= N; i++) {
      const x = Math.round(tee.x0 + 4 + (tee.x1 - tee.x0 - 8) * i / N);
      if (Math.abs(x - a.x) < 12) continue;
      const wy = walkTo(cur, x); if (wy == null) continue;
      const s = standAt(x, wy, tee.minFeet); if (s && Math.abs(s.fy - wy) <= 4) out.push(s);
    }
  }
  return out;
}

function buildState(obs) {
  grid = decode(obs.terrain);
  const objs = obs.objects.filter(o => !o.removed && !o.broken && !(o.type === 'bug' && o.inRecycle)).map(prep);
  for (const o of objs) if (o.type === 'chicken' && o.stunned) o.type = 'rock';
  const S = { objs, wind: obs.hole.wind || 0, goal: obs.hole.goal, zone: obs.hole.recycle, byId: new Map(objs.map(o => [o.id, o])) };
  S.bug = objs.find(o => o.type === 'bug') ?? null;
  const db = obs.objects.find(o => o.type === 'bug' && o.inRecycle && !o.removed); S.doneBug = db ? [db.x, db.y - (db.radius ?? 14)] : null;
  S.multiTok = S.goal !== 'bug' && objs.filter(o => o.type !== 'bug' && o.type !== 'chicken' && o.type !== 'rock').length >= 2;
  S.covered = false; S.digOK = false;
  if (S.zone && S.bug && S.goal !== 'tokens' && !S.bug.inRecycle) { S.rest0 = restCols(S.zone); S.cover0 = coverCells(S.zone); S.covered = S.rest0 < 8; S.digOK = S.rest0 < 40; }
  return S;
}

function search(obs, S, pts, kinds, t0, budget, stsIn, allowPen, extra = 90) {
  const sts = stsIn ?? stands(obs); let best = null, pen = null; const cands = [];
  outer: for (const st of sts) {
    const m = muzzle(st);
    for (const [tx, ty] of pts) {
      const dir = tx >= m.x ? 1 : -1;
      for (let ang = 2; ang <= 85; ang += (st.cur ? 1 : 3)) {
        const act = ang + dir * st.ga; if (act < 0 || act > 89) continue;
        const p = solvePower(m.x, m.y, act, dir, S.wind, tx, ty); if (p == null) continue;
        const pw = Math.round(p * 100) / 100;
        let skip = false, skipAll = false;
        for (const kind of kinds) {
          if (skipAll || (skip && kind !== 'spread')) continue;
          const e = evalShot(st, ang, pw, dir, kind, S);
          if (kind === 'single') { const r0 = e.impacts[0]; skipAll = !!e.bugRes && e.dmg.size === 0 && e.chick.size === 0 && !S.digOK; /* 纯推 Bug：其他弹种推力相同且更不确定 */ skip = r0.hit === 'out' || !(S.objs.some(o => Math.abs(o.cx - r0.x) < 130 && Math.abs(o.cy - r0.y) < 130) || (S.digOK && zoneDist(S.zone, r0.x, r0.y) < 90)); }
          let sc = e.score + (st.cur ? 1 : 0) - (st.cur && Math.abs(st.ga) > 3 ? 6 : 0) - (kind === 'single' ? 0 : 0.5);
          if (sc > 5) cands.push({ sc, st, ang, pw, dir, kind, e, tx, ty });
          else if (e.gain >= 95 && sc > -180 && (!pen || sc > pen.sc)) pen = { sc, rs: sc, st, ang, pw, dir, kind, e, tx, ty };
        }
        if (S.multiTok && kinds.includes('spread')) for (const off of [-6, 6]) { // 让散射的边弹命中瞄准点
          const a2 = act + off; if (a2 < 1 || a2 > 89) continue;
          const p2 = solvePower(m.x, m.y, a2, dir, S.wind, tx, ty); if (p2 == null) continue;
          const pw2 = Math.round(p2 * 100) / 100; const e = evalShot(st, ang, pw2, dir, 'spread', S);
          const sc = e.score + (st.cur ? 1 : 0) - (st.cur && Math.abs(st.ga) > 3 ? 6 : 0) - 0.5;
          if (sc > 105) cands.push({ sc, st, ang, pw: pw2, dir, kind: 'spread', e, tx, ty });
        }
        if (Date.now() - t0 > budget) break outer;
      }
    }
  }
  cands.sort((a, b) => b.sc - a.sc);
  // 稳健性复核：力度 ±0.8 扰动
  const top = cands.slice(0, 150);
  for (const c of top) {
    if (Date.now() - t0 > budget + extra) { c.rs = c.sc * 0.6; continue; }
    const a = evalShot(c.st, c.ang, c.pw + 0.8, c.dir, c.kind, S).score, b = evalShot(c.st, c.ang, c.pw - 0.8, c.dir, c.kind, S).score;
    const a2 = evalShot(c.st, c.ang + 0.8, c.pw, c.dir, c.kind, S).score;
    let k1 = c.e.score, k2 = c.e.score;
    if (c.e.bugRes) { k1 = evalShot(c.st, c.ang, c.pw, c.dir, c.kind, S, { ...PUSH, V: PUSH.V * 0.88 }).score; k2 = evalShot(c.st, c.ang, c.pw, c.dir, c.kind, S, { ...PUSH, V: PUSH.V * 1.12 }).score; }
    c.rs = 0.3 * c.e.score + 0.14 * (a + b + a2 + k1 + k2) + (c.sc - c.e.score);
  }
  top.sort((a, b) => b.rs - a.rs);
  if (allowPen && pen && (!top[0] || top[0].rs < (stall >= 1 ? 35 : 12))) return pen; // 上一杆白打过则更愿意接受“带罚杆的击杀”
  return top[0] ?? null;
}

let lastPred = null, stall = 0, prevBd = null, prevNeedBug = false, prevCover = null, bugInit = null;
function flyOptions(obs, S) {
  const a = obs.actor; const st = { x: a.x, fy: a.y + FOOT, ga: a.groundAngle || 0 }; const m = muzzle(st); const out = []; const seen = new Set();
  for (const dir of [1, -1]) for (const ang of [30, 45, 60, 75, 15]) for (let pw = 20; pw <= 100; pw += 8) {
    const th = ang * Math.PI / 180; const r = fly(m.x, m.y, dir * Math.cos(th) * VK * pw, -Math.sin(th) * VK * pw, S.wind, []);
    if (r.hit !== 'ground') continue; if (Math.abs(r.x - a.x) < 80) continue;
    const key = Math.round(r.x / 48); if (seen.has(key)) continue;
    const s2 = standAt(r.x, r.y, null); if (!s2 || Math.abs(s2.fy - r.y) > 12) continue;
    seen.add(key); out.push({ angle: ang, power: pw, dir, st: { x: r.x, fy: s2.fy, ga: 0, cur: true } });
  }
  // 离目标近的落脚点优先
  const tg = S.objs.filter(o => o.type !== 'chicken'); const dmin = f => Math.min(...tg.map(o => Math.abs(Math.abs(o.cx - f.st.x) - 300)), 1e9);
  out.sort((p, q) => dmin(p) - dmin(q));
  return out;
}
function decide(obs) {
  const t0 = Date.now();
  const S = buildState(obs);
  if (obs.lastResult && lastPred) {
    try { log('上杆预测 ' + JSON.stringify(lastPred) + ' 实际 ' + JSON.stringify((obs.lastResult.impacts || []).map(i => [Math.round(i.x), Math.round(i.y), i.hit, i.objectId])) + ' 站位 ' + obs.actor.x + ',' + (obs.actor.y + FOOT) + ' ga ' + obs.actor.groundAngle); } catch { }
  }
  // 跨洞记忆：记录“换站位后”的落点预测误差；误差大则后续洞不再换站位
  if (obs.lastResult && lastPred && lastPred.moved && lastPred.imp?.[0] && obs.lastResult.impacts?.[0]) {
    const pi = lastPred.imp[0], ai = obs.lastResult.impacts[0]; const er = Math.hypot(pi[0] - ai.x, pi[1] - ai.y);
    if (pi[2] === 'ground' && isFinite(er)) { MEM.moveErr.push(Math.round(er)); MEM.moveErr = MEM.moveErr.slice(-8); saveMem(); }
  }
  let calib = null;
  // （开发期的标定脚本入口已停用：正式版不读取 bot/ 以外的文件）
  let pick = null;
  if (calib && S.bug && calib.shots[obs.shotsTaken]) {
    const c = calib.shots[obs.shotsTaken]; const b = S.bug; const sg = (S.zone ? (S.zone.x0 + S.zone.x1) / 2 >= b.cx : true) ? 1 : -1;
    const x = b.cx - sg * c.off; const pt = c.hit != null ? [b.cx - sg * 13 * Math.cos(c.hit * Math.PI / 180), b.cy - 13 * Math.sin(c.hit * Math.PI / 180)] : [x, surfaceNear(x, b.y) + 1];
    const st = stands(obs)[0], m = muzzle(st); const dir = pt[0] >= m.x ? 1 : -1; let bb = null;
    for (let ang = c.a0 ?? 30; ang <= (c.a1 ?? 60); ang += 1) {
      const p = solvePower(m.x, m.y, ang + dir * st.ga, dir, S.wind, pt[0], pt[1]); if (p == null) continue;
      const e = evalShot(st, ang, p, dir, c.kind, S); const im = e.impacts[e.impacts.length >> 1];
      const d = Math.hypot(im.x - pt[0], im.y - pt[1]); if (!bb || d < bb.d) bb = { d, st, ang, pw: Math.round(p * 100) / 100, dir, kind: c.kind, e };
    }
    pick = bb;
  }
  const KINDS = ['single', 'burst', 'spread', 'dig'];
  // 停滞计数：上一杆没有任何物体变化
  const needBugNow = S.goal !== 'tokens' && S.bug && S.zone && !S.bug.inRecycle;
  const bd = needBugNow ? zoneDist(S.zone, S.bug.cx, S.bug.cy) : 0;
  if (bugInit == null && S.bug) bugInit = [S.bug.cx, S.bug.cy];
  if (obs.lastResult) {
    const ch = (obs.lastResult.objectChanges || []).some(c => c.type !== 'bug' && c.before?.type !== 'bug' && (c.damage > 0 || c.after?.broken || c.after?.removed));
    const bugProg = prevBd != null && needBugNow && bd < prevBd - 15;
    const digProg = S.covered && prevCover != null && S.cover0 < prevCover - 40;
    stall = (ch || bugProg || digProg || (prevNeedBug && !needBugNow)) ? 0 : stall + 1;
  }
  prevBd = bd; prevNeedBug = needBugNow; prevCover = S.covered ? S.cover0 : null;
  const pts = aimPoints(S);
  if (!pick) pick = search(obs, S, pts, KINDS, t0, 300, null, true, 80);
  // 纸飞机：常规搜索找不到有效杆（或停滞）时，评估飞到别处再打
  if ((obs.fly | 0) > 0 && (!pick || pick.rs < 12) && !calib) {
    const fl = flyOptions(obs, S); let bestF = null; log('飞机候选 ' + fl.length);
    for (const f of fl) {
      if (Date.now() - t0 > 520) break;
      const r = search(obs, S, pts, ['single', 'burst'], Date.now(), 40, [f.st], false, 20);
      if (r && (!bestF || r.rs > bestF.r.rs)) bestF = { f, r };
    }
    if (bestF && bestF.r.rs > (pick ? pick.rs : 0) + 25) {
      lastPred = { fly: [bestF.f.angle, bestF.f.power, bestF.f.dir], land: [Math.round(bestF.f.st.x), bestF.f.st.fy], then: bestF.r.rs };
      log(`决策(飞机) ${Date.now() - t0}ms ` + JSON.stringify(lastPred));
      return { fly: { angle: bestF.f.angle, power: bestF.f.power, dir: bestF.f.dir } };
    }
  }
  if (!calib && (!pick || pick.rs < 12) && stall >= 2 && needBugNow && bugInit && Math.hypot(S.bug.cx - bugInit[0], S.bug.cy - bugInit[1]) > 40 && obs.strokes + 3 <= obs.hole.cap) {
    log('决策：不可打，重置 Bug'); lastPred = null; stall = 0; prevBd = null; return { unplayable: true };
  }
  if (!pick) {
    // 兜底：朝最近目标打一发
    const tg = S.objs.find(o => o.type !== 'chicken' && !(o.type === 'bug' && S.goal === 'tokens')) ?? { cx: obs.actor.x + 300, cy: obs.actor.y };
    const st = stands(obs)[0], m = muzzle(st); const dir = tg.cx >= m.x ? 1 : -1;
    let p = null, ang = 45; for (const a of [45, 60, 30, 70, 20, 80]) { p = solvePower(m.x, m.y, a + dir * st.ga, dir, S.wind, tg.cx, tg.cy); if (p != null) { ang = a; break; } }
    pick = { st, ang, pw: p ?? 60, dir, kind: 'dig', e: null };
  }
  lastPred = pick.e ? { kind: pick.kind, sc: pick.sc, rs: pick.rs, imp: pick.e.impacts.map(i => [Math.round(i.x), Math.round(i.y), i.hit, i.obj?.id]), bug: pick.e.bugRes && [Math.round(pick.e.bugRes.x), Math.round(pick.e.bugRes.y)], st: [pick.st.x, pick.st.fy], moved: !pick.st.cur } : null;
  log(`决策 ${Date.now() - t0}ms ` + JSON.stringify(lastPred));
  const out = { attack: { kind: pick.kind, angle: Math.max(0, Math.min(85, Math.round(pick.ang * 100) / 100)), power: Math.max(0, Math.min(100, pick.pw)), dir: pick.dir } };
  if (!pick.st.cur && obs.hole.teeActive) out.moveX = pick.st.x;
  return out;
}

function exam(r) {
  const gy = r.ground ?? 780; grid = new Uint8Array(GW * GH); grid.fill(1, Math.floor(gy / 4) * GW);
  if (r.config?.BODY) { FOOT = r.config.BODY.footOffset ?? FOOT; MUZ = r.config.BODY.muzzleHeight ?? MUZ; }
  return {
    predictions: r.questions.map(q => {
      const th = q.angle * Math.PI / 180; const d = q.dir ?? 1;
      const f = fly(q.x, gy - MUZ, d * Math.cos(th) * VK * q.power, -Math.sin(th) * VK * q.power, q.wind || 0, []);
      return { id: q.id, x: Math.round(f.x * 10) / 10, y: Math.round(f.y * 10) / 10 };
    })
  };
}

function respond(r) {
  switch (r.type) {
    case 'init':
      if (r.config?.BODY) { FOOT = r.config.BODY.footOffset ?? FOOT; MUZ = r.config.BODY.muzzleHeight ?? MUZ; }
      if (r.config?.ATTACKS) for (const k of Object.keys(ATT)) { const a = r.config.ATTACKS[k]; if (a) { ATT[k].damage = a.damage ?? ATT[k].damage; ATT[k].radius = a.radius ?? ATT[k].radius; } }
      return { ok: true };
    case 'turn':
      try { return decide(r); } catch (e) { log('ERR ' + (e?.stack || e)); return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } }; }
    case 'exam':
      try { return exam(r); } catch (e) { log('ERR ' + (e?.stack || e)); return { predictions: [] }; }
    default:
      return {};
  }
}

export const T = { pushLos, setWind: (y, m) => { WY = y; WM = m; }, buildState, simPush, PUSH, fly, evalShot, decode, setGrid: g => { grid = g; }, respond, solvePower, carve };
if (!process.env.GOLF_LIB) for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let r; try { r = JSON.parse(line); } catch { continue; }
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
